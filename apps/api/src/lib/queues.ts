import { Queue } from "bullmq";
import { env } from "../env.js";

export const QUEUE_PIPELINE = "video-pipeline";
export const QUEUE_PUBLISH = "publish";
export const QUEUE_SCHEDULER = "scheduler";

function connection() {
  const url = new URL(env.REDIS_URL);
  return {
    host: url.hostname,
    port: Number(url.port || 6379),
    username: url.username || undefined,
    password: url.password ? decodeURIComponent(url.password) : undefined,
    db: url.pathname.length > 1 ? Number(url.pathname.slice(1)) : 0,
    tls: url.protocol === "rediss:" ? {} : undefined,
    maxRetriesPerRequest: null,
  };
}

export const redisConnection = connection();

const defaultJobOptions = {
  removeOnComplete: { age: 7 * 24 * 3600, count: 1000 },
  removeOnFail: { age: 30 * 24 * 3600 },
};

export const pipelineQueue = new Queue(QUEUE_PIPELINE, { connection: redisConnection, defaultJobOptions });
export const publishQueue = new Queue(QUEUE_PUBLISH, {
  connection: redisConnection,
  defaultJobOptions: { ...defaultJobOptions, attempts: 3, backoff: { type: "exponential", delay: 60_000 } },
});
export const schedulerQueue = new Queue(QUEUE_SCHEDULER, { connection: redisConnection, defaultJobOptions });

export interface ProcessProjectJob {
  projectId: string;
  sourceType: "URL" | "UPLOAD";
  sourceUrl?: string | null;
  sourceKey?: string | null;
  captionStyle: string;
  brollEnabled: boolean;
  clipCountMin: number;
  clipCountMax: number;
}

export interface RenderClipJob {
  clipId: string;
  projectId: string;
}

export interface PublishPostJob {
  postId: string;
}

export function enqueueProject(data: ProcessProjectJob) {
  return pipelineQueue.add("process-project", data, { jobId: `project-${data.projectId}-${Date.now()}` });
}

export function enqueueRender(data: RenderClipJob) {
  return pipelineQueue.add("render-clip", data, { jobId: `render-${data.clipId}-${Date.now()}`, priority: 1 });
}

export async function enqueuePublish(postId: string, delayMs = 0) {
  // Remove any previous (e.g. rescheduled) job for this post so the jobId can be reused.
  const existing = await publishQueue.getJob(`publish-${postId}`);
  if (existing) {
    const state = await existing.getState();
    if (state === "active") return existing;
    await existing.remove();
  }
  return publishQueue.add("publish-post", { postId } satisfies PublishPostJob, {
    jobId: `publish-${postId}`,
    delay: Math.max(0, delayMs),
  });
}

export async function cancelPublish(postId: string) {
  const job = await publishQueue.getJob(`publish-${postId}`);
  if (job && (await job.getState()) !== "active") await job.remove();
}
