import type { Clip, Project, ScheduledPost, SocialAccount } from "@prisma/client";
import { mask, decryptOpt } from "./crypto.js";
import { presignGetOpt } from "./storage.js";

export async function serializeClip(clip: Clip) {
  return {
    ...clip,
    videoUrl: await presignGetOpt(clip.videoKey),
    thumbnailUrl: await presignGetOpt(clip.thumbnailKey),
    durationMs: clip.endMs - clip.startMs,
  };
}

export async function serializeProject(project: Project & { clips?: Clip[]; _count?: { clips: number } }) {
  const { transcript: _transcript, ...rest } = project;
  return {
    ...rest,
    thumbnailUrl: await presignGetOpt(project.thumbnailKey),
    clipCount: project._count?.clips ?? project.clips?.length ?? 0,
    clips: project.clips ? await Promise.all(project.clips.map(serializeClip)) : undefined,
  };
}

export function serializeAccount(a: SocialAccount) {
  return {
    id: a.id,
    platform: a.platform,
    accountName: a.accountName,
    externalId: a.externalId,
    clientId: a.clientId,
    expiresAt: a.expiresAt,
    meta: a.meta,
    enabled: a.enabled,
    hasAccessToken: Boolean(a.accessTokenEnc),
    hasRefreshToken: Boolean(a.refreshTokenEnc),
    hasClientSecret: Boolean(a.clientSecretEnc),
    accessTokenPreview: mask(decryptOpt(a.accessTokenEnc)),
    updatedAt: a.updatedAt,
  };
}

export function serializePost(p: ScheduledPost & { clip?: Clip | null }) {
  return {
    ...p,
    clip: p.clip
      ? { id: p.clip.id, title: p.clip.title, projectId: p.clip.projectId, viralityScore: p.clip.viralityScore }
      : undefined,
  };
}
