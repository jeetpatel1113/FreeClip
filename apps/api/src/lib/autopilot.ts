import type { Platform } from "@prisma/client";
import { prisma } from "./db.js";
import { enqueuePublish } from "./queues.js";

/** Offset (ms) of `timeZone` from UTC at the given instant. */
function tzOffsetMs(date: Date, timeZone: string): number {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).formatToParts(date);
  const get = (t: string) => Number(parts.find((p) => p.type === t)?.value ?? 0);
  const asUtc = Date.UTC(get("year"), get("month") - 1, get("day"), get("hour"), get("minute"), get("second"));
  return asUtc - date.getTime();
}

/** UTC instant for local `hour`:00 on the local calendar day `dayOffset` days from `from`. */
function localSlot(from: Date, dayOffset: number, hour: number, timeZone: string): Date {
  const local = new Date(from.getTime() + tzOffsetMs(from, timeZone));
  const guess = Date.UTC(local.getUTCFullYear(), local.getUTCMonth(), local.getUTCDate() + dayOffset, hour, 0, 0);
  return new Date(guess - tzOffsetMs(new Date(guess), timeZone));
}

/**
 * Auto-Pilot: when a project finishes, schedule its best clips onto each enabled platform,
 * filling the user's posting slots while respecting the per-platform daily cap. The publisher
 * agent then reviews each post at publish time (topic fatigue, hook optimisation).
 */
export async function planAutopilot(projectId: string): Promise<number> {
  const project = await prisma.project.findUnique({
    where: { id: projectId },
    include: { user: { include: { socialAccounts: true } }, clips: true },
  });
  if (!project || !project.user.autopilotEnabled) return 0;
  const user = project.user;

  const clips = project.clips
    .filter((c) => c.status === "READY" && c.viralityScore >= user.autopilotMinScore)
    .sort((a, b) => b.viralityScore - a.viralityScore);
  if (!clips.length) return 0;

  const hours = [...new Set(user.autopilotPostingHours)].filter((h) => h >= 0 && h <= 23).sort((a, b) => a - b);
  if (!hours.length) hours.push(12);
  const now = new Date();
  let created = 0;

  for (const platform of user.autopilotPlatforms as Platform[]) {
    const account = user.socialAccounts.find((a) => a.platform === platform && a.enabled && a.accessTokenEnc);
    if (!account) continue;

    const existing = await prisma.scheduledPost.findMany({
      where: {
        platform,
        status: { in: ["SCHEDULED", "QUEUED", "PUBLISHING", "PUBLISHED"] },
        scheduledFor: { gte: new Date(now.getTime() - 24 * 3600_000) },
        clip: { project: { userId: user.id } },
      },
      select: { scheduledFor: true, clipId: true },
    });
    const taken = new Set(existing.map((p) => p.scheduledFor.toISOString()));
    const alreadyPosted = new Set(existing.map((p) => p.clipId));
    const perDay = new Map<string, number>();
    for (const p of existing) {
      const day = p.scheduledFor.toISOString().slice(0, 10);
      perDay.set(day, (perDay.get(day) ?? 0) + 1);
    }

    const queue = clips.filter((c) => !alreadyPosted.has(c.id));
    for (let day = 0; day < 60 && queue.length; day++) {
      for (const hour of hours) {
        if (!queue.length) break;
        const slot = localSlot(now, day, hour, user.timezone);
        if (slot.getTime() < now.getTime() + 5 * 60_000) continue;
        const dayKey = slot.toISOString().slice(0, 10);
        if ((perDay.get(dayKey) ?? 0) >= user.autopilotMaxPostsPerDay) break;
        if (taken.has(slot.toISOString())) continue;

        const clip = queue.shift()!;
        const captions = (clip.captions ?? {}) as Record<string, string>;
        const post = await prisma.scheduledPost.create({
          data: {
            clipId: clip.id,
            socialAccountId: account.id,
            platform,
            scheduledFor: slot,
            source: "AUTOPILOT",
            caption: captions[platform.toLowerCase()] ?? null,
          },
        });
        await enqueuePublish(post.id, slot.getTime() - Date.now());
        taken.add(slot.toISOString());
        perDay.set(dayKey, (perDay.get(dayKey) ?? 0) + 1);
        created++;
      }
    }
  }
  return created;
}
