import { timingSafeEqual } from "node:crypto";
import cors from "@fastify/cors";
import Fastify from "fastify";
import { ZodError } from "zod";
import { env } from "./env.js";
import { prisma } from "./lib/db.js";
import { pipelineQueue, publishQueue, schedulerQueue } from "./lib/queues.js";
import { startScheduler } from "./lib/scheduler.js";
import { ensureBucket } from "./lib/storage.js";
import { clipRoutes } from "./routes/clips.js";
import { internalRoutes } from "./routes/internal.js";
import { postRoutes } from "./routes/posts.js";
import { projectRoutes } from "./routes/projects.js";
import { settingsRoutes } from "./routes/settings.js";

const app = Fastify({
  logger: {
    level: env.NODE_ENV === "production" ? "info" : "debug",
    redact: ["req.headers.authorization", 'req.headers["x-internal-token"]'],
  },
  bodyLimit: 10 * 1024 * 1024,
  trustProxy: true,
});

await app.register(cors, { origin: env.CORS_ORIGIN.split(",").map((s) => s.trim()), credentials: true });

app.setErrorHandler((err, req, reply) => {
  if (err instanceof ZodError) {
    return reply.code(400).send({ error: "Validation failed", issues: err.issues });
  }
  const status = (err as { statusCode?: number }).statusCode ?? 500;
  if (status >= 500) req.log.error({ err }, "request failed");
  const message = err instanceof Error ? err.message : "Request failed";
  return reply.code(status).send({ error: status >= 500 ? "Internal server error" : message });
});

app.get("/health", async () => {
  await prisma.$queryRaw`SELECT 1`;
  const [pipeline, publish] = await Promise.all([pipelineQueue.getJobCounts(), publishQueue.getJobCounts()]);
  return { ok: true, queues: { pipeline, publish } };
});

// Public API (optionally bearer-protected). The web app reaches it through its server-side proxy.
await app.register(async (pub) => {
  if (env.API_AUTH_TOKEN) {
    const expected = Buffer.from(`Bearer ${env.API_AUTH_TOKEN}`);
    pub.addHook("onRequest", async (req, reply) => {
      const got = Buffer.from(req.headers.authorization ?? "");
      if (got.length !== expected.length || !timingSafeEqual(got, expected)) {
        return reply.code(401).send({ error: "Unauthorized" });
      }
    });
  }
  await pub.register(projectRoutes);
  await pub.register(clipRoutes);
  await pub.register(postRoutes);
  await pub.register(settingsRoutes);
});

await app.register(internalRoutes);

await ensureBucket().catch((err) => app.log.warn({ err }, "could not verify storage bucket (will retry on use)"));
const scheduler = await startScheduler(app.log);

const shutdown = async (signal: string) => {
  app.log.info({ signal }, "shutting down");
  await app.close();
  await scheduler.close();
  await Promise.all([pipelineQueue.close(), publishQueue.close(), schedulerQueue.close()]);
  await prisma.$disconnect();
  process.exit(0);
};
process.on("SIGTERM", () => void shutdown("SIGTERM"));
process.on("SIGINT", () => void shutdown("SIGINT"));

await app.listen({ port: env.PORT, host: env.HOST });
