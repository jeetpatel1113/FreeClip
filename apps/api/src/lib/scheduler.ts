import { QueueEvents, Worker } from "bullmq";
import type { FastifyBaseLogger } from "fastify";
import { env } from "../env.js";
import { prisma } from "./db.js";
import {
  QUEUE_PIPELINE,
  QUEUE_SCHEDULER,
  enqueuePublish,
  pipelineQueue,
  publishQueue,
  redisConnection,
  schedulerQueue,
} from "./queues.js";

/**
 * Cron sweep. Delayed BullMQ jobs are the primary scheduling mechanism; this sweep is the
 * safety net that re-enqueues due posts whose job was lost (Redis flush, restore, etc.) and
 * recovers posts stuck in PUBLISHING after a worker crash.
 */
export async function sweepDuePosts(log: FastifyBaseLogger): Promise<void> {
  const due = await prisma.scheduledPost.findMany({
    where: { status: { in: ["SCHEDULED", "QUEUED"] }, scheduledFor: { lte: new Date(Date.now() + 60_000) } },
    take: 200,
  });
  for (const post of due) {
    const job = await publishQueue.getJob(`publish-${post.id}`);
    const state = job ? await job.getState() : null;
    if (!job || state === "failed" || state === "completed" || state === "unknown") {
      log.info({ postId: post.id }, "re-enqueueing due post");
      await enqueuePublish(post.id, 0);
    }
  }

  const stuck = await prisma.scheduledPost.updateMany({
    where: { status: "PUBLISHING", updatedAt: { lt: new Date(Date.now() - 60 * 60_000) } },
    data: { status: "FAILED", error: "Publishing timed out (worker did not report back within 60 minutes)." },
  });
  if (stuck.count) log.warn({ count: stuck.count }, "marked stuck posts as failed");

  // Last-resort net for projects whose worker vanished without any failure event reaching us.
  const stale = await prisma.project.updateMany({
    where: {
      status: { in: ["DOWNLOADING", "TRANSCRIBING", "CURATING", "REFRAMING", "CAPTIONING"] },
      updatedAt: { lt: new Date(Date.now() - 2 * 60 * 60_000) },
    },
    data: { status: "FAILED", error: "No progress from the worker for 2 hours. Retry the project." },
  });
  if (stale.count) log.warn({ count: stale.count }, "marked stalled projects as failed");
}

/**
 * BullMQ can fail a job without the worker ever reporting back, e.g. "job stalled more than
 * allowable limit" after the worker crashes or is restarted mid-job. Mirror those failures into
 * Postgres so the UI shows the error and the project can be retried instead of spinning forever.
 */
export function watchPipelineFailures(log: FastifyBaseLogger): QueueEvents {
  const events = new QueueEvents(QUEUE_PIPELINE, { connection: redisConnection });
  events.on("failed", async ({ jobId, failedReason }) => {
    try {
      const job = await pipelineQueue.getJob(jobId);
      const data = (job?.data ?? {}) as { projectId?: string; clipId?: string };
      const error = `Pipeline job failed: ${failedReason}`;
      if (job?.name === "process-project" && data.projectId) {
        const res = await prisma.project.updateMany({
          where: { id: data.projectId, status: { notIn: ["READY", "FAILED"] } },
          data: { status: "FAILED", error, statusMessage: "Failed" },
        });
        if (res.count) log.warn({ projectId: data.projectId, failedReason }, "pipeline job failed in queue");
      } else if (job?.name === "render-clip" && data.clipId) {
        await prisma.clip.updateMany({
          where: { id: data.clipId, status: { in: ["PENDING", "RENDERING"] } },
          data: { status: "FAILED", error },
        });
      }
    } catch (err) {
      log.error({ err, jobId }, "could not record pipeline job failure");
    }
  });
  events.on("error", (err) => log.error({ err }, "pipeline queue events error"));
  return events;
}

export async function startScheduler(log: FastifyBaseLogger): Promise<{ close: () => Promise<void> }> {
  await schedulerQueue.upsertJobScheduler("sweep-due-posts", { pattern: env.SCHEDULER_SWEEP_CRON }, { name: "sweep" });
  const worker = new Worker(
    QUEUE_SCHEDULER,
    async () => {
      await sweepDuePosts(log);
    },
    { connection: redisConnection, concurrency: 1 },
  );
  worker.on("failed", (_job, err) => log.error({ err }, "scheduler sweep failed"));
  const failures = watchPipelineFailures(log);
  return {
    close: async () => {
      await Promise.all([worker.close(), failures.close()]);
    },
  };
}
