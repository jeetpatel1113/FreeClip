# FreeClip

A self-hosted AI clipping studio. Paste a YouTube, Twitch or Kick link (or upload an MP4). FreeClip transcribes it, has Gemini pick the 5-10 moments most likely to go viral, crops each one to a smooth 1080x1920 vertical video that follows the active speaker, burns in animated word-by-word captions, and can publish to TikTok, Instagram Reels, YouTube Shorts, X and LinkedIn on a schedule or fully on Auto-Pilot.

Everything runs on your own machine or GPU VPS with one command.

```
docker compose up -d
```

---

## Features

| | |
|---|---|
| **Ingest** | `yt-dlp` downloads best-quality MP4 (≤1080p) from YouTube, Twitch VODs/clips and Kick, or direct uploads go straight to RustFS by presigned URL. Audio is extracted as 16 kHz mono WAV. |
| **Transcription** | `faster-whisper` (CUDA when available) with word-level millisecond timestamps and VAD filtering. Optional `whisperx` forced alignment for tighter boundaries. |
| **AI viral curation** | Gemini (`gemini-3.8-flash` via the `google-genai` SDK, structured outputs with a Pydantic `response_schema`) reads the timestamped transcript and returns standalone 30-60 s clips with a virality score (0-100), an on-screen hook, a hook breakdown (type, first 3 s, emotional trigger, payoff, sub-scores), and platform-native captions and hashtags. Boundaries are snapped to exact word timestamps, finished on sentence ends, and de-overlapped. |
| **Active speaker reframing** | MediaPipe Face Mesh tracks faces, lip-gap variance measures who is talking, WebRTC VAD gates it to real speech, and hysteresis prevents flicker. A constant-velocity Kalman filter plus a dead-zone and a moving average gives smooth, jitter-free camera motion. Two people trading turns get an automatic stacked split-screen. |
| **Dynamic captions** | Generated `.ass` subtitles with word-by-word highlighting. Styles: **Hormozi Bold** (uppercase, yellow/green pop), **Minimalist** (dimmed inactive words), **Neon** (glowing cyan outline, magenta active word). Burned in with FFmpeg (NVENC when available). |
| **B-roll** | Optional Pexels stock cutaways, placed using keywords Gemini suggests and kept clear of the hook and the payoff. |
| **Clip studio** | 9:16 grid sorted by virality, hook breakdown, inline transcript editor (fixes re-render the captions), caption style switcher, download, and a Publish Now / Schedule modal. |
| **Autonomous publishing agent** | A Gemini function-calling agent reviews each post at publish time: it reads your 14-day posting history, rewrites the caption for the platform, defers Auto-Pilot posts that would repeat a recent topic, and calls the platform upload tool directly. Every step is logged and shown in the Schedule page. |
| **Auto-Pilot** | When a project finishes, clips above your score threshold are slotted into your posting hours (timezone-aware, with a per-platform daily cap) and published automatically. |

## Architecture

```
┌──────────────┐   /api proxy    ┌─────────────────────┐   BullMQ    ┌──────────────────────────────┐
│  apps/web    │ ──────────────▶ │  apps/api           │ ──────────▶ │ workers/video-pipeline       │
│  Next.js 15  │                 │  Fastify + Prisma   │   (Redis)   │ Python 3.11 · FFmpeg · CUDA  │
│  Tailwind +  │ ◀── presigned ─ │  BullMQ + scheduler │ ◀────────── │ ingest → curator → reframer  │
│  shadcn/ui   │     RustFS URLs  │  REST + /internal   │  callbacks  │ → captions → publisher agent │
└──────────────┘                 └─────────┬───────────┘             └───────────────┬──────────────┘
                                           │ PostgreSQL                              │ RustFS (S3)
```

* **Queues.** `video-pipeline` carries the `process-project` and `render-clip` jobs, and `publish` carries `publish-post` (delayed jobs, one per scheduled post). A `scheduler` cron (`*/5 * * * *`) re-enqueues due posts whose jobs were lost, recovers stuck posts, and picks up agent-deferred posts.
* **Exactly-once posting.** The worker atomically *claims* a post (`SCHEDULED/QUEUED → PUBLISHING`) before the agent runs, and the agent can call the upload tool for only one platform, at most once.
* **Security.** OAuth tokens are encrypted with AES-256-GCM (`ENCRYPTION_KEY`). The worker talks to `/internal/*` with `INTERNAL_API_TOKEN`. The browser never sees either, because the web app proxies the API server-side and can add `API_AUTH_TOKEN`.

```
apps/
  api/            Fastify REST API, Prisma schema, BullMQ producers, cron scheduler, Auto-Pilot planner
  web/            Next.js dashboard, clip studio, schedule, accounts & automation
workers/
  video-pipeline/
    worker.py     BullMQ consumer (asyncio + worker threads)
    pipeline/
      ingest.py       yt-dlp download, 16 kHz WAV, faster-whisper / whisperx word timestamps
      curator.py      Gemini structured-output curation + word-boundary snapping
      reframer.py     Face Mesh + VAD active speaker, Kalman smoothing, split-screen, OpenCV→FFmpeg
      captions.py     .ass generation (3 styles) + burn-in, B-roll overlay graph
      broll.py        Pexels search/download
      publisher.py    Gemini function-calling publishing agent
      social_apis.py  Instagram Graph, TikTok Content Posting, YouTube Data v3, X v2, LinkedIn uploads
      jobs.py         job orchestration and progress callbacks
```

## Quick start

Requirements: Docker with Compose v2. For speed you'll want an NVIDIA GPU with the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html); CPU works but is slow with `large-v3`.

```bash
git clone https://github.com/jeetpatel1113/FreeClip && cd FreeClip
cp .env.example .env
# Generate secrets:
for k in POSTGRES_PASSWORD MINIO_ROOT_PASSWORD INTERNAL_API_TOKEN ENCRYPTION_KEY; do
  sed -i "s|^$k=.*|$k=$(openssl rand -hex 32)|" .env
done
# Then set GEMINI_API_KEY in .env

docker compose up -d                                              # CPU
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d   # NVIDIA GPU
```

Open http://localhost:3000. The RustFS console is at http://localhost:9001.

The first run downloads the Whisper model (~3 GB for `large-v3`) into the `whisper-models` volume. On CPU, set `WHISPER_MODEL=small` or `distil-large-v3`.

### Deploying on a VPS

* Put the web app (port 3000) and RustFS (port 9000) behind HTTPS with a reverse proxy such as Caddy or Nginx.
* Set `WEB_ORIGIN=https://clips.example.com` and `S3_PUBLIC_ENDPOINT=https://media.example.com`. **Instagram requires** a public HTTPS video URL, so the media host must be reachable from the internet.
* Set `API_AUTH_TOKEN` and protect the web app with your proxy's auth (for example basic auth or an SSO forward-auth). FreeClip is single-tenant: everyone who can reach the dashboard is the owner.

## Configuration

All settings live in [`.env.example`](.env.example), with step-by-step instructions for getting **Gemini, Instagram, TikTok, YouTube, X and LinkedIn** credentials. Social tokens are entered in the UI (**Accounts & Automation**), not in `.env`.

| Variable | Default | Notes |
|---|---|---|
| `GEMINI_API_KEY` | (required) | From aistudio.google.com |
| `GEMINI_MODEL` | `gemini-3.8-flash` | Used for curation and the publishing agent |
| `GEMINI_SAFETY_THRESHOLD` | `BLOCK_NONE` | Applied to harassment, hate, sexual, dangerous and civic categories so profanity in transcripts isn't blocked |
| `GEMINI_THINKING_BUDGET` | `-1` | `-1` dynamic, `0` off (Flash), `>0` token budget |
| `WHISPER_MODEL` | `large-v3` | `small`/`distil-large-v3` for CPU |
| `USE_WHISPERX` | `0` | Needs `pip install whisperx` in the worker image |
| `FFMPEG_ENCODER` | `auto` | `auto` tests NVENC once, then falls back to libx264 |
| `PEXELS_API_KEY` | (empty) | Enables B-roll |
| `PUBLISH_DRY_RUN` | `0` | Run the agent without sending uploads |
| `TIKTOK_PRIVACY_LEVEL` | `SELF_ONLY` | Required until your TikTok app is audited |

Safety filters are relaxed only for the configurable categories. Google's built-in filters (for example `PROHIBITED_CONTENT`) still apply, and a blocked response fails the job with the block reason.

## Local development

```bash
npm install                                   # installs apps/api + apps/web workspaces
docker compose up -d postgres redis s3        # infra only

# API
cd apps/api
export DATABASE_URL=postgresql://freeclip:<pw>@localhost:5432/freeclip INTERNAL_API_TOKEN=... ENCRYPTION_KEY=... \
       S3_ACCESS_KEY=freeclip S3_SECRET_KEY=<MINIO_ROOT_PASSWORD>
npx prisma db push && npm run dev

# Web
cd apps/web && API_INTERNAL_URL=http://localhost:4000 npm run dev

# Worker (needs ffmpeg on PATH)
cd workers/video-pipeline
python3.11 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
API_URL=http://localhost:4000 INTERNAL_API_TOKEN=... GEMINI_API_KEY=... python worker.py
```

### Checks

```bash
npm run typecheck && npm run lint && npm run build     # api + web
cd workers/video-pipeline && ruff check . && python tests/test_smoke.py
```

The smoke tests run offline. They check the structured-output schema, the Gemini function-calling loop (against a stubbed client), 30-60 s boundary snapping, `.ass` generation for every style, Kalman jitter reduction, and a real FFmpeg reframe plus caption burn on a synthetic video.

## REST API

| Method | Path | |
|---|---|---|
| `POST` | `/uploads/presign` | Presigned PUT for direct-to-RustFS uploads |
| `POST` | `/projects` | `{ sourceUrl \| sourceKey, captionStyle?, brollEnabled?, clipCountMin?, clipCountMax? }` |
| `GET` | `/projects`, `/projects/:id` | Progress, clips (sorted by virality) and presigned media URLs |
| `POST` | `/projects/:id/retry` | Re-run a failed project |
| `PATCH` | `/clips/:id` | Edit title, hook, words, caption style or captions (re-renders when visuals change) |
| `POST` | `/clips/:id/publish` | `{ platforms[], scheduledFor?, captions? }` |
| `GET` / `PATCH` / `DELETE` | `/posts[/:id]` | Schedule, reschedule, cancel; `POST /posts/:id/retry` |
| `GET` / `PATCH` | `/settings` | Auto-Pilot and defaults |
| `PUT` / `DELETE` | `/social-accounts/:platform` | Store or remove encrypted credentials |

## Claude Code automation (Whop Content Rewards)

`.mcp.json` registers two MCP servers for Claude Code in this folder:

- **playwright**: the official Playwright MCP server, with a persistent Chrome profile in `./.playwright-profile`, so your Whop login is kept between runs.
- **freeclip**: `freeclip_mcp.py`, which talks to the FreeClip API. Install its dependencies with `pip install -r requirements-mcp.txt`. Its tools:
  - `run_freeclip_pipeline`: ingest, wait for clips, then publish the top clips with a `caption_suffix` that is always added to the caption.
  - `get_latest_published_urls`: latest URLs with their age, for the 30-minute submission window.
  - `log_campaign_submission`: records submissions in `submitted_clips.csv` and blocks duplicates.

The database stores every post's URL, status and publish time. `published_clips.json` is a copy of that data, refreshed by the MCP tools, so posts published from the web UI or by Auto-Pilot show up there too.

One-time Whop login. Close the window when you're done, because the profile can't be open in two browsers at once:

```powershell
npx -y playwright@latest open --channel chrome --user-data-dir .playwright-profile https://whop.com/login
```

Then, in Claude Code: `/clip-campaign <campaign_url> <video_path> [--auto]`. Without `--auto`, it stops before every final Submit click and waits for you to confirm.

## Notes and limits

* Only download content you have the rights to. yt-dlp may need `YTDLP_COOKIES_FILE` for age-restricted or members-only videos.
* Platform APIs change often and need app review: TikTok direct post needs an audited app for public posts, YouTube uploads from unverified projects stay private, X media upload needs a paid API tier, and Instagram needs a Business/Creator account linked to a Page.
* Long sources (2-4 h) fit easily in Gemini's 1M-token context window. Curation cost scales with transcript length.

## License

MIT, see [LICENSE](LICENSE).
