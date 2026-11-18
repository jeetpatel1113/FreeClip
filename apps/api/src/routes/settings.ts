import type { FastifyInstance } from "fastify";
import { z } from "zod";
import { encryptOpt } from "../lib/crypto.js";
import { getOwner, prisma } from "../lib/db.js";
import { serializeAccount } from "../lib/serialize.js";

const Platform = z.enum(["INSTAGRAM", "TIKTOK", "YOUTUBE", "X", "LINKEDIN"]);

const settingsBody = z.object({
  name: z.string().max(100).optional(),
  timezone: z
    .string()
    .refine((tz) => {
      try {
        new Intl.DateTimeFormat("en-US", { timeZone: tz });
        return true;
      } catch {
        return false;
      }
    }, "Unknown IANA timezone")
    .optional(),
  defaultCaptionStyle: z.enum(["HORMOZI", "MINIMALIST", "NEON"]).optional(),
  autopilotEnabled: z.boolean().optional(),
  autopilotPlatforms: z.array(Platform).optional(),
  autopilotMinScore: z.number().int().min(0).max(100).optional(),
  autopilotMaxPostsPerDay: z.number().int().min(1).max(24).optional(),
  autopilotPostingHours: z.array(z.number().int().min(0).max(23)).max(24).optional(),
});

const accountBody = z.object({
  accountName: z.string().max(200).nullish(),
  externalId: z.string().max(200).nullish(),
  accessToken: z.string().max(10_000).nullish(),
  refreshToken: z.string().max(10_000).nullish(),
  clientId: z.string().max(500).nullish(),
  clientSecret: z.string().max(2000).nullish(),
  expiresAt: z.string().datetime({ offset: true }).nullish(),
  meta: z.record(z.string(), z.unknown()).optional(),
  enabled: z.boolean().optional(),
});

export async function settingsRoutes(app: FastifyInstance) {
  app.get("/settings", async () => {
    const owner = await getOwner();
    const accounts = await prisma.socialAccount.findMany({ where: { userId: owner.id } });
    return { user: owner, accounts: accounts.map(serializeAccount) };
  });

  app.patch("/settings", async (req) => {
    const body = settingsBody.parse(req.body);
    const owner = await getOwner();
    const user = await prisma.user.update({ where: { id: owner.id }, data: body });
    return { user };
  });

  app.put("/social-accounts/:platform", async (req) => {
    const { platform } = z.object({ platform: Platform }).parse(req.params);
    const body = accountBody.parse(req.body);
    const owner = await getOwner();
    const data = {
      accountName: body.accountName,
      externalId: body.externalId,
      clientId: body.clientId,
      // Empty/omitted secret fields keep the stored value; send null explicitly to clear.
      accessTokenEnc: body.accessToken === "" ? undefined : encryptOpt(body.accessToken),
      refreshTokenEnc: body.refreshToken === "" ? undefined : encryptOpt(body.refreshToken),
      clientSecretEnc: body.clientSecret === "" ? undefined : encryptOpt(body.clientSecret),
      expiresAt: body.expiresAt === undefined ? undefined : body.expiresAt ? new Date(body.expiresAt) : null,
      meta: body.meta as object | undefined,
      enabled: body.enabled,
    };
    const account = await prisma.socialAccount.upsert({
      where: { userId_platform: { userId: owner.id, platform } },
      update: data,
      create: { ...data, userId: owner.id, platform, meta: (body.meta ?? {}) as object },
    });
    return serializeAccount(account);
  });

  app.delete("/social-accounts/:platform", async (req, reply) => {
    const { platform } = z.object({ platform: Platform }).parse(req.params);
    const owner = await getOwner();
    await prisma.socialAccount.deleteMany({ where: { userId: owner.id, platform } });
    return reply.code(204).send();
  });
}
