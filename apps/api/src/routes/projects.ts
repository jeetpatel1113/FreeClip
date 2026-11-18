import { randomUUID } from "node:crypto";
import type { FastifyInstance } from "fastify";
import { z } from "zod";
import { getOwner, prisma } from "../lib/db.js";
import { enqueueProject } from "../lib/queues.js";
import { serializeProject } from "../lib/serialize.js";
import { deletePrefix, presignPut } from "../lib/storage.js";

const CaptionStyle = z.enum(["HORMOZI", "MINIMALIST", "NEON"]);

const ALLOWED_HOSTS: Record<string, string> = {
  "youtube.com": "youtube",
  "youtu.be": "youtube",
  "twitch.tv": "twitch",
  "kick.com": "kick",
};

function detectPlatform(url: string): string | null {
  try {
    const host = new URL(url).hostname.replace(/^(www\.|m\.|clips\.)/, "");
    for (const [h, name] of Object.entries(ALLOWED_HOSTS)) {
      if (host === h || host.endsWith(`.${h}`)) return name;
    }
  } catch {
    return null;
  }
  return null;
}

const createBody = z
  .object({
    title: z.string().max(200).optional(),
    sourceUrl: z.string().url().optional(),
    sourceKey: z.string().startsWith("uploads/").optional(),
    captionStyle: CaptionStyle.optional(),
    brollEnabled: z.boolean().default(false),
    clipCountMin: z.number().int().min(1).max(20).default(5),
    clipCountMax: z.number().int().min(1).max(20).default(10),
  })
  .refine((b) => Boolean(b.sourceUrl) !== Boolean(b.sourceKey), {
    message: "Provide exactly one of sourceUrl or sourceKey",
  })
  .refine((b) => b.clipCountMin <= b.clipCountMax, { message: "clipCountMin must be <= clipCountMax" });

export async function projectRoutes(app: FastifyInstance) {
  app.post("/uploads/presign", async (req) => {
    const body = z
      .object({
        filename: z.string().min(1).max(300),
        contentType: z.string().regex(/^video\//, "Only video uploads are supported"),
      })
      .parse(req.body);
    const safe = body.filename.replace(/[^a-zA-Z0-9._-]/g, "_").slice(-120);
    const key = `uploads/${randomUUID()}/${safe}`;
    return { key, url: await presignPut(key, body.contentType) };
  });

  app.post("/projects", async (req, reply) => {
    const body = createBody.parse(req.body);
    const owner = await getOwner();
    let sourcePlatform: string | null = "upload";
    if (body.sourceUrl) {
      sourcePlatform = detectPlatform(body.sourceUrl);
      if (!sourcePlatform) {
        return reply.code(400).send({ error: "Only YouTube, Twitch and Kick URLs are supported." });
      }
    }
    const project = await prisma.project.create({
      data: {
        userId: owner.id,
        title: body.title || (body.sourceUrl ?? body.sourceKey!.split("/").pop() ?? "Untitled"),
        sourceType: body.sourceUrl ? "URL" : "UPLOAD",
        sourceUrl: body.sourceUrl,
        sourceKey: body.sourceKey,
        sourcePlatform,
        captionStyle: body.captionStyle ?? owner.defaultCaptionStyle,
        brollEnabled: body.brollEnabled,
        clipCountMin: body.clipCountMin,
        clipCountMax: body.clipCountMax,
        statusMessage: "Waiting for a worker",
      },
    });
    await enqueueProject({
      projectId: project.id,
      sourceType: project.sourceType,
      sourceUrl: project.sourceUrl,
      sourceKey: project.sourceKey,
      captionStyle: project.captionStyle,
      brollEnabled: project.brollEnabled,
      clipCountMin: project.clipCountMin,
      clipCountMax: project.clipCountMax,
    });
    return reply.code(201).send(await serializeProject(project));
  });

  app.get("/projects", async () => {
    const owner = await getOwner();
    const projects = await prisma.project.findMany({
      where: { userId: owner.id },
      orderBy: { createdAt: "desc" },
      include: { _count: { select: { clips: true } } },
      take: 100,
    });
    return Promise.all(projects.map(serializeProject));
  });

  app.get("/projects/:id", async (req, reply) => {
    const { id } = z.object({ id: z.string() }).parse(req.params);
    const project = await prisma.project.findUnique({
      where: { id },
      include: { clips: { orderBy: [{ viralityScore: "desc" }, { index: "asc" }] } },
    });
    if (!project) return reply.code(404).send({ error: "Project not found" });
    return serializeProject(project);
  });

  app.post("/projects/:id/retry", async (req, reply) => {
    const { id } = z.object({ id: z.string() }).parse(req.params);
    const project = await prisma.project.findUnique({ where: { id } });
    if (!project) return reply.code(404).send({ error: "Project not found" });
    if (project.status !== "FAILED") return reply.code(409).send({ error: "Only failed projects can be retried" });
    const updated = await prisma.project.update({
      where: { id },
      data: { status: "QUEUED", progress: 0, error: null, statusMessage: "Retrying" },
    });
    await enqueueProject({
      projectId: id,
      sourceType: project.sourceType,
      sourceUrl: project.sourceUrl,
      sourceKey: project.sourceKey,
      captionStyle: project.captionStyle,
      brollEnabled: project.brollEnabled,
      clipCountMin: project.clipCountMin,
      clipCountMax: project.clipCountMax,
    });
    return serializeProject(updated);
  });

  app.delete("/projects/:id", async (req, reply) => {
    const { id } = z.object({ id: z.string() }).parse(req.params);
    const project = await prisma.project.findUnique({ where: { id } });
    if (!project) return reply.code(404).send({ error: "Project not found" });
    await prisma.project.delete({ where: { id } });
    await deletePrefix(`projects/${id}/`).catch((err) => req.log.warn({ err }, "storage cleanup failed"));
    return reply.code(204).send();
  });
}
