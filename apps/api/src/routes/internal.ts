import { timingSafeEqual } from "node:crypto";
import type { FastifyInstance } from "fastify";
import { z } from "zod";
import { planAutopilot } from "../lib/autopilot.js";
import { decryptOpt, encryptOpt } from "../lib/crypto.js";
import { prisma } from "../lib/db.js";
import { env } from "../env.js";
import { presignGet } from "../lib/storage.js";

const ProjectStatus = z.enum([
  "QUEUED",
  "DOWNLOADING",
  "TRANSCRIBING",
  "CURATING",
  "REFRAMING",
  "CAPTIONING",
  "READY",
  "FAILED",
]);

const Word = z.object({ word: z.string(), start: z.number(), end: z.number() }).passthrough();

const clipInput = z.object({
  index: z.number().int().nonnegative(),
  title: z.string(),
  startMs: z.number().int().nonnegative(),
  endMs: z.number().int().positive(),
  viralityScore: z.number().int().min(0).max(100),
  hook: z.string(),
  hookBreakdown: z.record(z.string(), z.unknown()),
  reasoning: z.string(),
  words: z.array(Word),
  captions: z.record(z.string(), z.string()),
  hashtags: z.array(z.string()),
  brollKeywords: z.array(z.string()).default([]),
  captionStyle: z.enum(["HORMOZI", "MINIMALIST", "NEON"]).optional(),
});

function tokenOk(header: unknown): boolean {
  if (typeof header !== "string") return false;
  const a = Buffer.from(header);
  const b = Buffer.from(env.INTERNAL_API_TOKEN);
  return a.length === b.length && timingSafeEqual(a, b);
}

/** Callback surface for the Python worker. Protected by INTERNAL_API_TOKEN. */
export async function internalRoutes(app: FastifyInstance) {
  app.addHook("onRequest", async (req, reply) => {
    if (!tokenOk(req.headers["x-internal-token"])) {
      return reply.code(401).send({ error: "Invalid internal token" });
    }
  });

  app.post("/internal/projects/:id/progress", async (req, reply) => {
    const { id } = z.object({ id: z.string() }).parse(req.params);
    const body = z
      .object({
        status: ProjectStatus,
        progress: z.number().min(0).max(100),
        message: z.string().max(500).optional(),
        error: z.string().max(5000).nullish(),
        durationMs: z.number().int().optional(),
        title: z.string().max(300).optional(),
        thumbnailKey: z.string().optional(),
        transcript: z.unknown().optional(),
      })
      .parse(req.body);
    const project = await prisma.project.findUnique({ where: { id } });
    if (!project) return reply.code(404).send({ error: "Project not found" });

    await prisma.project.update({
      where: { id },
      data: {
        status: body.status,
        progress: Math.round(body.progress),
        statusMessage: body.message,
        error: body.error,
        durationMs: body.durationMs,
        thumbnailKey: body.thumbnailKey,
        transcript: body.transcript === undefined ? undefined : (body.transcript as object),
        // Replace a URL placeholder title with the real video title from yt-dlp.
        title: body.title && project.title === project.sourceUrl ? body.title : undefined,
      },
    });

    if (body.status === "READY" && project.status !== "READY") {
      const scheduled = await planAutopilot(id).catch((err) => {
        req.log.error({ err }, "autopilot planning failed");
        return 0;
      });
      if (scheduled) req.log.info({ projectId: id, scheduled }, "autopilot scheduled posts");
    }
    return { ok: true };
  });

  app.post("/internal/projects/:id/clips", async (req, reply) => {
    const { id } = z.object({ id: z.string() }).parse(req.params);
    const body = z.object({ clips: z.array(clipInput).max(50) }).parse(req.body);
    const project = await prisma.project.findUnique({ where: { id } });
    if (!project) return reply.code(404).send({ error: "Project not found" });

    // Re-processing a project replaces its clips.
    await prisma.clip.deleteMany({ where: { projectId: id, index: { notIn: body.clips.map((c) => c.index) } } });
    const clips = [];
    for (const c of body.clips) {
      const data = {
        title: c.title,
        startMs: c.startMs,
        endMs: c.endMs,
        viralityScore: c.viralityScore,
        hook: c.hook,
        hookBreakdown: c.hookBreakdown as object,
        reasoning: c.reasoning,
        words: c.words as object[],
        captions: c.captions,
        hashtags: c.hashtags,
        brollKeywords: c.brollKeywords,
        captionStyle: c.captionStyle ?? project.captionStyle,
        status: "PENDING" as const,
        videoKey: null,
        thumbnailKey: null,
      };
      clips.push(
        await prisma.clip.upsert({
          where: { projectId_index: { projectId: id, index: c.index } },
          update: data,
          create: { ...data, projectId: id, index: c.index },
        }),
      );
    }
    return { clips: clips.map((c) => ({ id: c.id, index: c.index })) };
  });

  app.get("/internal/clips/:id", async (req, reply) => {
    const { id } = z.object({ id: z.string() }).parse(req.params);
    const clip = await prisma.clip.findUnique({ where: { id }, include: { project: true } });
    if (!clip) return reply.code(404).send({ error: "Clip not found" });
    const { project, ...rest } = clip;
    return {
      clip: rest,
      project: {
        id: project.id,
        sourceType: project.sourceType,
        sourceUrl: project.sourceUrl,
        sourceKey: project.sourceKey,
        brollEnabled: project.brollEnabled,
      },
    };
  });

  app.patch("/internal/clips/:id", async (req) => {
    const { id } = z.object({ id: z.string() }).parse(req.params);
    const body = z
      .object({
        status: z.enum(["PENDING", "RENDERING", "READY", "FAILED"]),
        videoKey: z.string().optional(),
        thumbnailKey: z.string().optional(),
        layout: z.string().optional(),
        error: z.string().max(5000).nullish(),
      })
      .parse(req.body);
    await prisma.clip.update({
      where: { id },
      data: { ...body, renderVersion: body.status === "READY" ? { increment: 1 } : undefined },
    });
    return { ok: true };
  });

  app.get("/internal/posts/:id", async (req, reply) => {
    const { id } = z.object({ id: z.string() }).parse(req.params);
    const post = await prisma.scheduledPost.findUnique({
      where: { id },
      include: { clip: { include: { project: { include: { user: true } } } }, socialAccount: true },
    });
    if (!post) return reply.code(404).send({ error: "Post not found" });
    const { clip, socialAccount } = post;

    const since = new Date(Date.now() - 14 * 24 * 3600_000);
    const history = await prisma.scheduledPost.findMany({
      where: {
        id: { not: id },
        status: { in: ["PUBLISHED", "SCHEDULED", "QUEUED", "PUBLISHING"] },
        scheduledFor: { gte: since },
        clip: { project: { userId: clip.project.userId } },
      },
      include: { clip: { select: { title: true, hook: true, hashtags: true } } },
      orderBy: { scheduledFor: "desc" },
      take: 50,
    });

    return {
      post: {
        id: post.id,
        platform: post.platform,
        status: post.status,
        source: post.source,
        caption: post.caption,
        captionSuffix: post.captionSuffix,
        scheduledFor: post.scheduledFor,
        attempts: post.attempts,
      },
      clip: {
        id: clip.id,
        title: clip.title,
        hook: clip.hook,
        hookBreakdown: clip.hookBreakdown,
        viralityScore: clip.viralityScore,
        captions: clip.captions,
        hashtags: clip.hashtags,
        durationMs: clip.endMs - clip.startMs,
        transcript: (clip.words as { word: string }[]).map((w) => w.word).join(" "),
        videoKey: clip.videoKey,
        status: clip.status,
      },
      videoUrl: clip.videoKey ? await presignGet(clip.videoKey, 24 * 3600) : null,
      account: socialAccount
        ? {
            id: socialAccount.id,
            platform: socialAccount.platform,
            accountName: socialAccount.accountName,
            externalId: socialAccount.externalId,
            accessToken: decryptOpt(socialAccount.accessTokenEnc),
            refreshToken: decryptOpt(socialAccount.refreshTokenEnc),
            clientId: socialAccount.clientId,
            clientSecret: decryptOpt(socialAccount.clientSecretEnc),
            expiresAt: socialAccount.expiresAt,
            meta: socialAccount.meta,
            enabled: socialAccount.enabled,
          }
        : null,
      history: history.map((h) => ({
        platform: h.platform,
        status: h.status,
        scheduledFor: h.scheduledFor,
        publishedAt: h.publishedAt,
        title: h.clip.title,
        hook: h.clip.hook,
        hashtags: h.clip.hashtags,
      })),
    };
  });

  /** Atomically claim a due post for publishing so duplicate jobs can never double-post. */
  app.post("/internal/posts/:id/claim", async (req) => {
    const { id } = z.object({ id: z.string() }).parse(req.params);
    const claimed = await prisma.scheduledPost.updateMany({
      where: {
        id,
        status: { in: ["SCHEDULED", "QUEUED"] },
        scheduledFor: { lte: new Date(Date.now() + 2 * 60_000) },
      },
      data: { status: "PUBLISHING", attempts: { increment: 1 }, error: null },
    });
    return { claimed: claimed.count === 1 };
  });

  app.patch("/internal/posts/:id", async (req) => {
    const { id } = z.object({ id: z.string() }).parse(req.params);
    const body = z
      .object({
        status: z.enum(["SCHEDULED", "QUEUED", "PUBLISHING", "PUBLISHED", "FAILED", "CANCELLED"]),
        caption: z.string().optional(),
        externalPostId: z.string().nullish(),
        externalUrl: z.string().nullish(),
        error: z.string().max(10_000).nullish(),
        agentLog: z.unknown().optional(),
        scheduledFor: z.string().datetime({ offset: true }).optional(),
      })
      .parse(req.body);
    await prisma.scheduledPost.update({
      where: { id },
      data: {
        status: body.status,
        caption: body.caption,
        externalPostId: body.externalPostId,
        externalUrl: body.externalUrl,
        error: body.error,
        agentLog: body.agentLog === undefined ? undefined : (body.agentLog as object),
        scheduledFor: body.scheduledFor ? new Date(body.scheduledFor) : undefined,
        attempts: body.status === "PUBLISHING" ? { increment: 1 } : undefined,
        publishedAt: body.status === "PUBLISHED" ? new Date() : undefined,
      },
    });
    return { ok: true };
  });

  /** Persist refreshed OAuth tokens from the worker. */
  app.patch("/internal/social-accounts/:id", async (req) => {
    const { id } = z.object({ id: z.string() }).parse(req.params);
    const body = z
      .object({
        accessToken: z.string().optional(),
        refreshToken: z.string().optional(),
        expiresAt: z.string().datetime({ offset: true }).nullish(),
      })
      .parse(req.body);
    await prisma.socialAccount.update({
      where: { id },
      data: {
        accessTokenEnc: encryptOpt(body.accessToken),
        refreshTokenEnc: encryptOpt(body.refreshToken),
        expiresAt: body.expiresAt === undefined ? undefined : body.expiresAt ? new Date(body.expiresAt) : null,
      },
    });
    return { ok: true };
  });
}
