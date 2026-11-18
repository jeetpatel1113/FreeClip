import { PrismaClient } from "@prisma/client";
import { env } from "../env.js";

export const prisma = new PrismaClient();

/** Single-tenant self-hosted mode: everything belongs to one owner account. */
export function getOwner() {
  return prisma.user.upsert({
    where: { email: env.DEFAULT_USER_EMAIL },
    update: {},
    create: { email: env.DEFAULT_USER_EMAIL, name: "Owner" },
  });
}
