import type { FastifyInstance } from "fastify";
import { z } from "zod";
import { prisma } from "../lib/db.js";
import { enqueuePublish, enqueueRender } from "../lib/queues.js";
import { serializeClip, serializePost } from "../lib/serialize.js";

const Platform = z.enum(["INSTAGRAM", "TIKTOK", "YOUTUBE", "X", "LINKEDIN"]);

const Word = z.object({
  word: z.string(),
  start: z.number().int().nonnegative(),
  end: z.number().int().nonnegative(),
});

const patchBody = z.object({
  title: z.string().min(1).max(200).optional(),
  hook: z.string().max(200).optional(),
  words: z.array(Word).max(5000).optional(),
  captionStyle: z.enum(["HORMOZI", "MINIMALIST", "NEON"]).optional(),
  captions: z.record(z.string(), z.string().max(5000)).optional(),
  hashtags: z.array(z.string().max(100)).max(30).optional(),
  rerender: z.boolean().optional(),
});

export async function clipRoutes(app: FastifyInstance) {
  app.get("/clips/:id", async (req, reply) => {
    const { id } = z.object({ id: z.string() }).parse(req.params);
    const clip = await prisma.clip.findUnique({
      where: { id },
      include: { scheduledPosts: { orderBy: { scheduledFor: "desc" } } },
    });
    if (!clip) return reply.code(404).send({ error: "Clip not found" });
    const { scheduledPosts, ...rest } = clip;
    return { ...(await serializeClip(rest)), posts: scheduledPosts.map(serializePost) };
  });

  app.patch("/clips/:id", async (req, reply) => {
    const { id } = z.object({ id: z.string() }).parse(req.params);
    const body = patchBody.parse(req.body);
    const clip = await prisma.clip.findUnique({ where: { id } });
    if (!clip) return reply.code(404).send({ error: "Clip not found" });

    const visualChange =
      body.words !== undefined ||
      (body.captionStyle !== undefined && body.captionStyle !== clip.captionStyle) ||
      (body.hook !== undefined && body.hook !== clip.hook);
    const rerender = body.rerender ?? visualChange;

    const updated = await prisma.clip.update({
      where: { id },
      data: {
        title: body.title,
        hook: body.hook,
        words: body.words,
        captionStyle: body.captionStyle,
        captions: body.captions ? { ...(clip.captions as Record<string, string>), ...body.captions } : undefined,
        hashtags: body.hashtags,
        ...(rerender ? { status: "RENDERING", error: null } : {}),
      },
    });
    if (rerender) await enqueueRender({ clipId: id, projectId: clip.projectId });
    return serializeClip(updated);
  });

  app.post("/clips/:id/render", async (req, reply) => {
    const { id } = z.object({ id: z.string() }).parse(req.params);
    const clip = await prisma.clip.findUnique({ where: { id } });
    if (!clip) return reply.code(404).send({ error: "Clip not found" });
    const updated = await prisma.clip.update({ where: { id }, data: { status: "RENDERING", error: null } });
    await enqueueRender({ clipId: id, projectId: clip.projectId });
    return serializeClip(updated);
  });

  app.post("/clips/:id/publish", async (req, reply) => {
    const { id } = z.object({ id: z.string() }).parse(req.params);
    const body = z
      .object({
        platforms: z.array(Platform).min(1),
        scheduledFor: z.string().datetime({ offset: true }).optional(),
        captions: z.record(z.string(), z.string().max(5000)).optional(),
      })
      .parse(req.body);

    const clip = await prisma.clip.findUnique({ where: { id }, include: { project: true } });
    if (!clip) return reply.code(404).send({ error: "Clip not found" });
    if (clip.status !== "READY" || !clip.videoKey) {
      return reply.code(409).send({ error: "Clip must finish rendering before it can be published" });
    }

    const accounts = await prisma.socialAccount.findMany({
      where: { userId: clip.project.userId, platform: { in: body.platforms }, enabled: true },
    });
    const missing = body.platforms.filter((p) => !accounts.some((a) => a.platform === p && a.accessTokenEnc));
    if (missing.length) {
      return reply.code(400).send({ error: `Connect these accounts in Settings first: ${missing.join(", ")}` });
    }

    const when = body.scheduledFor ? new Date(body.scheduledFor) : new Date();
    const defaults = (clip.captions ?? {}) as Record<string, string>;
    const posts = [];
    for (const platform of body.platforms) {
      const account = accounts.find((a) => a.platform === platform)!;
      const post = await prisma.scheduledPost.create({
        data: {
          clipId: id,
          socialAccountId: account.id,
          platform,
          scheduledFor: when,
          status: when.getTime() <= Date.now() + 30_000 ? "QUEUED" : "SCHEDULED",
          caption: body.captions?.[platform.toLowerCase()] ?? defaults[platform.toLowerCase()] ?? null,
        },
      });
      await enqueuePublish(post.id, when.getTime() - Date.now());
      posts.push(serializePost(post));
    }
    return reply.code(201).send(posts);
  });
}
