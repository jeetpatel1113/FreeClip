export type ProjectStatus =
  | "QUEUED"
  | "DOWNLOADING"
  | "TRANSCRIBING"
  | "CURATING"
  | "REFRAMING"
  | "CAPTIONING"
  | "READY"
  | "FAILED";
export type ClipStatus = "PENDING" | "RENDERING" | "READY" | "FAILED";
export type CaptionStyle = "HORMOZI" | "MINIMALIST" | "NEON";
export type Platform = "INSTAGRAM" | "TIKTOK" | "YOUTUBE" | "X" | "LINKEDIN";
export type PostStatus = "SCHEDULED" | "QUEUED" | "PUBLISHING" | "PUBLISHED" | "FAILED" | "CANCELLED";

export interface Word {
  word: string;
  start: number;
  end: number;
}

export interface HookBreakdown {
  hook_type: string;
  first_3_seconds: string;
  emotional_trigger: string;
  payoff: string;
  retention_notes: string;
  scores: { hook: number; flow: number; value: number; trend: number };
}

export interface Clip {
  id: string;
  projectId: string;
  index: number;
  title: string;
  startMs: number;
  endMs: number;
  durationMs: number;
  viralityScore: number;
  hook: string;
  hookBreakdown: HookBreakdown;
  reasoning: string;
  words: Word[];
  captions: Record<string, string>;
  hashtags: string[];
  captionStyle: CaptionStyle;
  layout: string | null;
  status: ClipStatus;
  error: string | null;
  videoUrl: string | null;
  thumbnailUrl: string | null;
  posts?: Post[];
}

export interface Project {
  id: string;
  title: string;
  sourceType: "URL" | "UPLOAD";
  sourceUrl: string | null;
  sourcePlatform: string | null;
  status: ProjectStatus;
  progress: number;
  statusMessage: string | null;
  error: string | null;
  durationMs: number | null;
  thumbnailUrl: string | null;
  captionStyle: CaptionStyle;
  clipCount: number;
  clips?: Clip[];
  createdAt: string;
}

export interface Post {
  id: string;
  clipId: string;
  platform: Platform;
  scheduledFor: string;
  status: PostStatus;
  source: "MANUAL" | "AUTOPILOT";
  caption: string | null;
  externalUrl: string | null;
  error: string | null;
  agentLog: { steps?: { type: string; name?: string; text?: string; result?: string }[] } | null;
  publishedAt: string | null;
  clip?: { id: string; title: string; projectId: string; viralityScore: number };
}

export interface SocialAccount {
  id: string;
  platform: Platform;
  accountName: string | null;
  externalId: string | null;
  clientId: string | null;
  expiresAt: string | null;
  meta: Record<string, unknown>;
  enabled: boolean;
  hasAccessToken: boolean;
  hasRefreshToken: boolean;
  hasClientSecret: boolean;
  accessTokenPreview: string | null;
}

export interface UserSettings {
  id: string;
  email: string;
  name: string | null;
  timezone: string;
  defaultCaptionStyle: CaptionStyle;
  autopilotEnabled: boolean;
  autopilotPlatforms: Platform[];
  autopilotMinScore: number;
  autopilotMaxPostsPerDay: number;
  autopilotPostingHours: number[];
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

/** All calls go through the Next.js server-side proxy at /api, which adds the API token. */
export async function api<T>(path: string, init?: RequestInit & { json?: unknown }): Promise<T> {
  const { json, ...rest } = init ?? {};
  const res = await fetch(`/api${path}`, {
    ...rest,
    headers: { ...(json !== undefined ? { "Content-Type": "application/json" } : {}), ...rest.headers },
    body: json !== undefined ? JSON.stringify(json) : rest.body,
    cache: "no-store",
  });
  if (res.status === 204) return undefined as T;
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, body.error ?? `Request failed (${res.status})`);
  return body as T;
}

export const fetcher = <T,>(path: string) => api<T>(path);

export const PLATFORMS: { id: Platform; label: string }[] = [
  { id: "TIKTOK", label: "TikTok" },
  { id: "INSTAGRAM", label: "Instagram Reels" },
  { id: "YOUTUBE", label: "YouTube Shorts" },
  { id: "X", label: "X / Twitter" },
  { id: "LINKEDIN", label: "LinkedIn" },
];

export const CAPTION_STYLES: { id: CaptionStyle; label: string; description: string }[] = [
  { id: "HORMOZI", label: "Hormozi Bold", description: "Heavy uppercase, yellow/green word pop" },
  { id: "MINIMALIST", label: "Minimalist", description: "Clean sentence case, subtle highlight" },
  { id: "NEON", label: "Neon", description: "Glowing cyan outline, magenta active word" },
];
