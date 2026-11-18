import { Worker } from "bullmq";
import type { FastifyBaseLogger } from "fastify";
import { env } from "../env.js";
import { prisma } from "./db.js";
import { QUEUE_SCHEDULER, enqueuePublish, publishQueue, redisConnection, schedulerQueue } from "./queues.js";

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
}

export async function startScheduler(log: FastifyBaseLogger): Promise<Worker> {
  await schedulerQueue.upsertJobScheduler("sweep-due-posts", { pattern: env.SCHEDULER_SWEEP_CRON }, { name: "sweep" });
  const worker = new Worker(
    QUEUE_SCHEDULER,
    async () => {
      await sweepDuePosts(log);
    },
    { connection: redisConnection, concurrency: 1 },
  );
  worker.on("failed", (_job, err) => log.error({ err }, "scheduler sweep failed"));
  return worker;
}
