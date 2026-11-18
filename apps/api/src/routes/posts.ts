import type { FastifyInstance } from "fastify";
import { z } from "zod";
import { getOwner, prisma } from "../lib/db.js";
import { cancelPublish, enqueuePublish } from "../lib/queues.js";
import { serializePost } from "../lib/serialize.js";

export async function postRoutes(app: FastifyInstance) {
  app.get("/posts", async (req) => {
    const q = z
      .object({ status: z.enum(["SCHEDULED", "QUEUED", "PUBLISHING", "PUBLISHED", "FAILED", "CANCELLED"]).optional() })
      .parse(req.query);
    const owner = await getOwner();
    const posts = await prisma.scheduledPost.findMany({
      where: { status: q.status, clip: { project: { userId: owner.id } } },
      include: { clip: true },
      orderBy: { scheduledFor: "desc" },
      take: 200,
    });
    return posts.map(serializePost);
  });

  app.patch("/posts/:id", async (req, reply) => {
    const { id } = z.object({ id: z.string() }).parse(req.params);
    const body = z
      .object({ scheduledFor: z.string().datetime({ offset: true }).optional(), caption: z.string().max(5000).optional() })
      .parse(req.body);
    const post = await prisma.scheduledPost.findUnique({ where: { id } });
    if (!post) return reply.code(404).send({ error: "Post not found" });
    if (!["SCHEDULED", "QUEUED", "FAILED"].includes(post.status)) {
      return reply.code(409).send({ error: `Cannot edit a post that is ${post.status.toLowerCase()}` });
    }
    const when = body.scheduledFor ? new Date(body.scheduledFor) : post.scheduledFor;
    const updated = await prisma.scheduledPost.update({
      where: { id },
      data: { scheduledFor: when, caption: body.caption, status: "SCHEDULED", error: null },
    });
    await enqueuePublish(id, when.getTime() - Date.now());
    return serializePost(updated);
  });

  app.post("/posts/:id/retry", async (req, reply) => {
    const { id } = z.object({ id: z.string() }).parse(req.params);
    const post = await prisma.scheduledPost.findUnique({ where: { id } });
    if (!post) return reply.code(404).send({ error: "Post not found" });
    if (post.status !== "FAILED") return reply.code(409).send({ error: "Only failed posts can be retried" });
    const updated = await prisma.scheduledPost.update({ where: { id }, data: { status: "QUEUED", error: null } });
    await enqueuePublish(id, 0);
    return serializePost(updated);
  });

  app.delete("/posts/:id", async (req, reply) => {
    const { id } = z.object({ id: z.string() }).parse(req.params);
    const post = await prisma.scheduledPost.findUnique({ where: { id } });
    if (!post) return reply.code(404).send({ error: "Post not found" });
    if (post.status === "PUBLISHING" || post.status === "PUBLISHED") {
      return reply.code(409).send({ error: "This post is already publishing or published" });
    }
    await cancelPublish(id);
    const updated = await prisma.scheduledPost.update({ where: { id }, data: { status: "CANCELLED" } });
    return serializePost(updated);
  });
}
