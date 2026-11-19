"""FreeClip MCP server: drive FreeClip from Claude Code and track Content Rewards submissions.

Tools
  run_freeclip_pipeline      upload/ingest a video, wait for clips, publish the best ones, return live URLs
  get_latest_published_urls  most recent published posts with age (Content Rewards' 30-minute window)
  log_campaign_submission    append to submitted_clips.csv; refuses duplicate URL+campaign pairs

FreeClip's Postgres database is the source of truth for every publish run (ScheduledPost.externalUrl,
publishedAt, status). This server mirrors it to published_clips.json in the project root after each
run and whenever URLs are read, so the file always reflects the latest state, including posts
published from the web UI or by Auto-Pilot.

Talks to the FreeClip API at FREECLIP_API_URL (default http://localhost:${API_PORT:-4000}); reads
API_AUTH_TOKEN and other flags from the environment or the project's .env file.
Run: python freeclip_mcp.py   (stdio; registered in .mcp.json)
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import mimetypes
import os
import re
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit, urlunsplit

import httpx
from mcp.server.fastmcp import Context, FastMCP

ROOT = Path(__file__).resolve().parent
PUBLISHED_FILE = ROOT / "published_clips.json"
SUBMITTED_FILE = ROOT / "submitted_clips.csv"
SUBMISSION_WINDOW_MIN = 30

# stdout is the MCP transport; log to stderr only.
logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("freeclip-mcp")


def _dotenv() -> dict[str, str]:
    values: dict[str, str] = {}
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            values[key.strip()] = val.strip().strip("'\"")
    return values


_ENV = _dotenv()


def _cfg(key: str, default: str = "") -> str:
    return os.environ.get(key) or _ENV.get(key) or default


API_URL = _cfg("FREECLIP_API_URL", f"http://localhost:{_cfg('API_PORT', '4000')}").rstrip("/")
API_TOKEN = _cfg("API_AUTH_TOKEN")

PLATFORM_ALIASES = {
    "youtube": "YOUTUBE", "yt": "YOUTUBE", "youtube shorts": "YOUTUBE", "shorts": "YOUTUBE",
    "youtube_shorts": "YOUTUBE",
    "tiktok": "TIKTOK", "tik tok": "TIKTOK", "tt": "TIKTOK",
    "instagram": "INSTAGRAM", "ig": "INSTAGRAM", "instagram reels": "INSTAGRAM", "reels": "INSTAGRAM",
    "instagram_reels": "INSTAGRAM",
    "x": "X", "twitter": "X", "x (twitter)": "X",
    "linkedin": "LINKEDIN",
}
URL_KEYS = {"YOUTUBE": "youtube_url", "TIKTOK": "tiktok_url", "INSTAGRAM": "instagram_url", "X": "x_url",
            "LINKEDIN": "linkedin_url"}
PUBLIC_URL_PATTERNS = {
    "YOUTUBE": re.compile(r"^https://(www\.)?(youtube\.com/(shorts/|watch\?v=)|youtu\.be/)[\w-]{6,}"),
    "TIKTOK": re.compile(r"^https://(www\.)?tiktok\.com/@[\w.-]+/video/\d+"),
    "INSTAGRAM": re.compile(r"^https://(www\.)?instagram\.com/(reel|reels|p)/[\w-]+"),
    "X": re.compile(r"^https://(www\.)?(x|twitter)\.com/\w+/status/\d+"),
    "LINKEDIN": re.compile(r"^https://(www\.)?linkedin\.com/feed/update/urn:li:"),
}
TERMINAL_POST = {"PUBLISHED", "FAILED", "CANCELLED"}

mcp = FastMCP(
    "freeclip",
    instructions=(
        "FreeClip turns long videos into vertical clips and publishes them. Use run_freeclip_pipeline to "
        "generate and publish, get_latest_published_urls to confirm live URLs (with age for the 30-minute "
        "Content Rewards window), and log_campaign_submission (check_only=True before submitting, then "
        "again after) to record Whop submissions and avoid duplicates."
    ),
)


# --------------------------------------------------------------------------- API helpers


def _client(timeout: float = 30.0) -> httpx.AsyncClient:
    headers = {"Authorization": f"Bearer {API_TOKEN}"} if API_TOKEN else {}
    return httpx.AsyncClient(base_url=API_URL, headers=headers, timeout=timeout)


async def _api(method: str, path: str, **kw: Any) -> Any:
    async with _client() as c:
        try:
            r = await c.request(method, path, **kw)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"FreeClip API unreachable at {API_URL} ({exc}). Is `docker compose up` running?") from exc
    if r.status_code >= 400:
        try:
            msg = r.json().get("error") or r.text
        except ValueError:
            msg = r.text
        raise RuntimeError(f"FreeClip API {method} {path} -> {r.status_code}: {msg}")
    return r.json() if r.content else None


def _normalize_platforms(platforms: list[str]) -> list[str]:
    out: list[str] = []
    for p in platforms:
        key = PLATFORM_ALIASES.get(p.strip().lower()) or (p.strip().upper() if p.strip().upper() in URL_KEYS else None)
        if not key:
            raise ValueError(f"Unknown platform {p!r}. Use youtube, tiktok, instagram, x or linkedin.")
        if key not in out:
            out.append(key)
    if not out:
        raise ValueError("No platforms given.")
    return out


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _age_minutes(value: str | None) -> float | None:
    dt = _parse_dt(value)
    return round((datetime.now(UTC) - dt).total_seconds() / 60, 1) if dt else None


def _publish_detail(post: dict[str, Any]) -> str:
    """The uploader's detail string (e.g. 'privacy=SELF_ONLY') from the agent log, if any."""
    for step in reversed((post.get("agentLog") or {}).get("steps") or []):
        if step.get("type") == "tool" and not step.get("is_error"):
            try:
                return str(json.loads(step.get("result") or "{}").get("detail") or "")
            except (ValueError, AttributeError):
                return ""
    return ""


def _post_warning(post: dict[str, Any]) -> str | None:
    platform, url, status = post["platform"], post.get("externalUrl"), post["status"]
    if status != "PUBLISHED":
        return post.get("error") or f"Post is {status.lower()}"
    detail = _publish_detail(post)
    if "DRY_RUN" in detail:
        return "PUBLISH_DRY_RUN=1: nothing was actually posted."
    if platform == "TIKTOK":
        if not url:
            return ("TikTok accepted the upload but returned no public video URL: it is still processing or "
                    "landed in the creator's Inbox/Drafts. Not submittable until it is posted publicly.")
        if "privacy=" in detail and "PUBLIC_TO_EVERYONE" not in detail:
            return (f"TikTok post is not public ({detail}). Unaudited TikTok apps can only post SELF_ONLY; "
                    "make it public in the TikTok app before submitting.")
    if not url:
        return f"{platform} returned no post URL."
    pattern = PUBLIC_URL_PATTERNS.get(platform)
    if pattern and not pattern.match(url):
        return f"URL does not look like a public {platform} post URL."
    return None


def _post_view(post: dict[str, Any]) -> dict[str, Any]:
    age = _age_minutes(post.get("publishedAt"))
    warning = _post_warning(post)
    return {
        "post_id": post["id"],
        "platform": post["platform"].lower(),
        "status": post["status"],
        "url": post.get("externalUrl"),
        "published_at": post.get("publishedAt"),
        "age_minutes": age,
        "within_submission_window": age is not None and age <= SUBMISSION_WINDOW_MIN,
        "minutes_left_in_window": max(0.0, round(SUBMISSION_WINDOW_MIN - age, 1)) if age is not None else None,
        "submittable": post["status"] == "PUBLISHED" and warning is None,
        "warning": warning,
        "error": post.get("error"),
        "clip_id": (post.get("clip") or {}).get("id") or post.get("clipId"),
        "clip_title": (post.get("clip") or {}).get("title"),
        "project_id": (post.get("clip") or {}).get("projectId"),
        "caption": post.get("caption"),
    }


# --------------------------------------------------------------------------- published_clips.json


def _write_json_atomic(path: Path, data: Any) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def _load_published_file() -> dict[str, Any]:
    try:
        return json.loads(PUBLISHED_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"clips": []}


async def sync_published_file() -> list[dict[str, Any]]:
    """Mirror FreeClip's posts into published_clips.json (one entry per clip) and return the post views."""
    posts = await _api("GET", "/posts")
    views = [_post_view(p) for p in posts if p["status"] != "CANCELLED"]
    existing = {c["clip_id"]: c for c in _load_published_file().get("clips", []) if c.get("clip_id")}
    by_clip: dict[str, dict[str, Any]] = {}
    for v in sorted(views, key=lambda v: v["published_at"] or ""):
        entry = by_clip.setdefault(v["clip_id"], {
            "clip_id": v["clip_id"], "project_id": v["project_id"], "title": v["clip_title"],
            **{k: None for k in URL_KEYS.values()}, "platforms": {},
        })
        key = v["platform"]
        prev = entry["platforms"].get(key)
        # A successful post wins over an earlier/later failed retry for the same platform.
        if prev and prev["status"] == "PUBLISHED" and v["status"] != "PUBLISHED":
            continue
        entry["platforms"][key] = {k: v[k] for k in ("post_id", "status", "url", "published_at", "warning", "error")}
        if v["status"] == "PUBLISHED" and v["url"]:
            entry[URL_KEYS[v["platform"].upper()]] = v["url"]
    for cid, entry in by_clip.items():
        times = [p["published_at"] for p in entry["platforms"].values() if p["published_at"]]
        entry["last_published_at"] = max(times) if times else None
        existing[cid] = entry
    clips = sorted(existing.values(), key=lambda c: c.get("last_published_at") or "", reverse=True)
    _write_json_atomic(PUBLISHED_FILE, {"updated_at": datetime.now(UTC).isoformat(), "source": API_URL,
                                        "clips": clips})
    return views


# --------------------------------------------------------------------------- pipeline


def _upload_file(path: Path, url: str, content_type: str) -> None:
    with path.open("rb") as fh, httpx.Client(timeout=httpx.Timeout(60.0, write=None)) as c:
        r = c.put(url, content=fh, headers={"Content-Type": content_type,
                                            "Content-Length": str(path.stat().st_size)})
    if r.status_code >= 300:
        raise RuntimeError(f"Upload to storage failed ({r.status_code}): {r.text[:300]}")


async def _progress(ctx: Context | None, pct: float, msg: str) -> None:
    log.info("%3.0f%% %s", pct, msg)
    if ctx is not None:
        try:
            await ctx.report_progress(pct, 100, msg)
        except Exception:  # noqa: BLE001 - progress is best effort
            pass


async def _preflight(platforms: list[str]) -> list[str]:
    settings = await _api("GET", "/settings")
    connected = {a["platform"] for a in settings["accounts"] if a["enabled"] and a["hasAccessToken"]}
    missing = [p for p in platforms if p not in connected]
    if missing:
        raise RuntimeError(f"Connect these accounts in FreeClip Settings first: {', '.join(missing)}")
    warnings = []
    if _cfg("PUBLISH_DRY_RUN") == "1":
        warnings.append("PUBLISH_DRY_RUN=1 in .env: posts will not actually be uploaded.")
    if "INSTAGRAM" in platforms and re.match(r"https?://(localhost|127\.|minio|s3)", _cfg("S3_PUBLIC_ENDPOINT")):
        warnings.append("S3_PUBLIC_ENDPOINT is not publicly reachable; Instagram fetches the video by URL and "
                        "will fail. Point it at a public https URL.")
    if "TIKTOK" in platforms and _cfg("TIKTOK_PRIVACY_LEVEL", "SELF_ONLY") != "PUBLIC_TO_EVERYONE":
        warnings.append("TIKTOK_PRIVACY_LEVEL is not PUBLIC_TO_EVERYONE: TikTok posts will be private "
                        "(SELF_ONLY) and not eligible for Content Rewards until made public.")
    return warnings


async def _start_project(video_path: str, max_clips: int, ctx: Context | None) -> dict[str, Any]:
    counts = {"clipCountMin": max(1, min(max_clips, 20)), "clipCountMax": max(3, min(max_clips + 2, 20))}
    if re.match(r"^https?://", video_path.strip()):
        return await _api("POST", "/projects", json={"sourceUrl": video_path.strip(), **counts})
    path = Path(video_path).expanduser()
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Video not found: {path}")
    ctype = mimetypes.guess_type(path.name)[0] or "video/mp4"
    if not ctype.startswith("video/"):
        ctype = "video/mp4"
    presign = await _api("POST", "/uploads/presign", json={"filename": path.name, "contentType": ctype})
    await _progress(ctx, 1, f"Uploading {path.name} ({path.stat().st_size / 1e6:.0f} MB)")
    await asyncio.to_thread(_upload_file, path, presign["url"], ctype)
    return await _api("POST", "/projects", json={"sourceKey": presign["key"], "title": path.stem[:200], **counts})


async def _wait_for_clips(project_id: str, deadline: float, ctx: Context | None) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    last = None
    while True:
        project = await _api("GET", f"/projects/{project_id}")
        status = project["status"]
        msg = f"{status}: {project.get('statusMessage') or ''}".strip()
        if msg != last:
            await _progress(ctx, 5 + 0.8 * (project.get("progress") or 0), msg)
            last = msg
        clips = project.get("clips") or []
        if status == "FAILED":
            raise RuntimeError(f"FreeClip project failed: {project.get('error') or project.get('statusMessage')}")
        if status == "READY" and not any(c["status"] in ("PENDING", "RENDERING") for c in clips):
            return project
        if loop.time() > deadline:
            return project
        await asyncio.sleep(10)


async def _wait_for_posts(post_ids: list[str], deadline: float, ctx: Context | None) -> dict[str, dict[str, Any]]:
    loop = asyncio.get_running_loop()
    while True:
        posts = {p["id"]: p for p in await _api("GET", "/posts") if p["id"] in post_ids}
        done = [i for i in post_ids if posts.get(i, {}).get("status") in TERMINAL_POST]
        await _progress(ctx, 90 + 9 * len(done) / len(post_ids), f"Published {len(done)}/{len(post_ids)} posts")
        if len(done) == len(post_ids) or loop.time() > deadline:
            return posts
        await asyncio.sleep(5)


@mcp.tool()
async def run_freeclip_pipeline(
    video_path: str,
    caption_suffix: str,
    platforms: list[str],
    max_clips: int = 1,
    min_clip_seconds: float = 0,
    project_id: str | None = None,
    wait_minutes: int = 60,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Generate clips from a video with FreeClip and publish the best ones, returning the live post URLs.

    Args:
        video_path: Local video file (absolute, or relative to the FreeClip folder) or a YouTube/Twitch/Kick URL.
        caption_suffix: Text appended verbatim to every caption/title (campaign #hashtags, @mentions,
            disclosures like "#ad"). Guaranteed present even though the publishing agent writes the caption.
        platforms: Any of youtube, tiktok, instagram, x, linkedin.
        max_clips: How many of the highest-scoring clips to publish (each goes to every platform).
        min_clip_seconds: Skip clips shorter than this (e.g. the campaign's minimum length).
        project_id: Resume an existing FreeClip project instead of uploading again (e.g. after a timeout).
        wait_minutes: How long to wait for processing before returning the project id to resume with.
    """
    targets = _normalize_platforms(platforms)
    warnings = await _preflight(targets)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait_minutes * 60

    if project_id:
        project = await _api("GET", f"/projects/{project_id}")
        if project["status"] == "FAILED":
            await _api("POST", f"/projects/{project_id}/retry")
    else:
        project = await _start_project(video_path, max_clips, ctx)
    pid = project["id"]
    await _progress(ctx, 3, f"FreeClip project {pid} queued")

    project = await _wait_for_clips(pid, deadline, ctx)
    clips = project.get("clips") or []
    if project["status"] != "READY" or any(c["status"] in ("PENDING", "RENDERING") for c in clips):
        return {"status": "still_processing", "project_id": pid, "project_status": project["status"],
                "progress": project.get("progress"), "message": project.get("statusMessage"),
                "next_step": f"Call run_freeclip_pipeline again with project_id='{pid}' (same other arguments).",
                "warnings": warnings}

    # Don't republish a clip that already went out to these platforms (e.g. when resuming).
    already = {(p["clipId"], p["platform"]) for p in await _api("GET", "/posts")
               if p["status"] in ("QUEUED", "PUBLISHING", "PUBLISHED")}
    eligible = [c for c in clips if c["status"] == "READY" and c["durationMs"] >= min_clip_seconds * 1000]
    eligible.sort(key=lambda c: c["viralityScore"], reverse=True)
    chosen = eligible[:max_clips]
    if not chosen:
        lengths = ", ".join(f"{c['durationMs'] / 1000:.0f}s" for c in clips) or "none"
        raise RuntimeError(f"No rendered clips meet the {min_clip_seconds:.0f}s minimum (clip lengths: {lengths}).")

    post_ids: list[str] = []
    for clip in chosen:
        todo = [p for p in targets if (clip["id"], p) not in already]
        if not todo:
            continue
        created = await _api("POST", f"/clips/{clip['id']}/publish",
                             json={"platforms": todo, "captionSuffix": caption_suffix.strip() or None})
        post_ids += [p["id"] for p in created]
    await _progress(ctx, 90, f"Publishing {len(post_ids)} posts")

    if post_ids:
        await _wait_for_posts(post_ids, loop.time() + max(15 * 60, deadline - loop.time()), ctx)
    views = await sync_published_file()
    chosen_ids = {c["id"] for c in chosen}
    results = []
    for clip in chosen:
        mine = [v for v in views if v["clip_id"] == clip["id"] and v["platform"].upper() in targets]
        latest: dict[str, dict[str, Any]] = {}
        for v in sorted(mine, key=lambda v: (v["status"] == "PUBLISHED", v["published_at"] or "")):
            latest[v["platform"]] = v
        results.append({
            "clip_id": clip["id"], "title": clip["title"], "virality_score": clip["viralityScore"],
            "duration_seconds": round(clip["durationMs"] / 1000, 1),
            **{URL_KEYS[p]: (latest.get(p.lower()) or {}).get("url") for p in targets},
            "posts": list(latest.values()),
        })
    pending = [v for v in views if v["post_id"] in post_ids and v["status"] not in TERMINAL_POST]
    warnings += sorted({f"{v['platform']}: {v['warning']}" for r in results for v in r["posts"] if v["warning"]})
    return {
        "status": "published" if not pending else "publishing",
        "project_id": pid,
        "clips": results,
        "unpublished_ready_clips": [c["id"] for c in eligible if c["id"] not in chosen_ids],
        "warnings": warnings,
        "published_clips_file": str(PUBLISHED_FILE),
    }


@mcp.tool()
async def get_latest_published_urls(limit: int = 5) -> dict[str, Any]:
    """Most recent published FreeClip posts, newest first, with age in minutes and whether each is still
    inside Content Rewards' 30-minute submission window and looks like a valid public URL."""
    source = "api"
    try:
        views = await sync_published_file()
    except RuntimeError as exc:
        # API down: fall back to the last mirror on disk.
        source = f"published_clips.json (API unavailable: {exc})"
        views = []
        for clip in _load_published_file().get("clips", []):
            for platform, p in clip.get("platforms", {}).items():
                age = _age_minutes(p.get("published_at"))
                views.append({**p, "platform": platform, "clip_id": clip["clip_id"], "clip_title": clip.get("title"),
                              "age_minutes": age,
                              "within_submission_window": age is not None and age <= SUBMISSION_WINDOW_MIN,
                              "submittable": p.get("status") == "PUBLISHED" and not p.get("warning")})
    published = [v for v in views if v["status"] == "PUBLISHED"]
    published.sort(key=lambda v: v.get("published_at") or "", reverse=True)
    return {"source": source, "now_utc": datetime.now(UTC).isoformat(timespec="seconds"),
            "window_minutes": SUBMISSION_WINDOW_MIN, "posts": published[: max(1, limit)]}


# --------------------------------------------------------------------------- submissions log


def _normalize_url(url: str) -> str:
    """Canonical form for duplicate checks: https, no www/m., no query/fragment/trailing slash."""
    raw = url.strip()
    if not re.match(r"^[a-z]+://", raw, re.I):
        raw = "https://" + raw
    parts = urlsplit(raw)
    host = re.sub(r"^(www\.|m\.|mobile\.)", "", parts.netloc.lower())
    path = parts.path.rstrip("/")
    if host == "youtu.be":
        host, path = "youtube.com", f"/shorts{path}"
    elif host == "youtube.com" and path == "/watch":
        vid = parse_qs(parts.query).get("v", [""])[0]
        path = f"/shorts/{vid}"
    elif host == "twitter.com":
        host = "x.com"
    return urlunsplit(("https", host, path, "", ""))


FIELDS = ["submitted_at_utc", "campaign_url", "platform", "post_url", "published_at", "age_minutes_at_submit"]


def _read_submissions() -> list[dict[str, str]]:
    if not SUBMITTED_FILE.exists():
        return []
    with SUBMITTED_FILE.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


@mcp.tool()
async def log_campaign_submission(campaign_url: str, platform: str, post_url: str,
                                  check_only: bool = False) -> dict[str, Any]:
    """Record a Content Rewards submission in submitted_clips.csv, refusing duplicates.

    The same post URL can only be logged once per campaign (URLs are compared in canonical form, so
    youtu.be/..., ?si=... and trailing slashes don't slip through). Call with check_only=True before
    submitting in Whop to confirm the URL is new, then without it after the submission succeeds."""
    plat = _normalize_platforms([platform])[0].lower()
    campaign, post = _normalize_url(campaign_url), _normalize_url(post_url)
    rows = _read_submissions()
    dupe = next((r for r in rows if _normalize_url(r["campaign_url"]) == campaign
                 and _normalize_url(r["post_url"]) == post), None)
    other_campaigns = sorted({r["campaign_url"] for r in rows
                              if _normalize_url(r["post_url"]) == post and _normalize_url(r["campaign_url"]) != campaign})
    if dupe:
        return {"logged": False, "duplicate": True, "previous_submission": dupe,
                "message": f"Already submitted this URL to this campaign at {dupe['submitted_at_utc']}; do not submit it again."}

    published_at = None
    for clip in _load_published_file().get("clips", []):
        for p in clip.get("platforms", {}).values():
            if p.get("url") and _normalize_url(p["url"]) == post:
                published_at = p.get("published_at")
    age = _age_minutes(published_at)
    result: dict[str, Any] = {"duplicate": False, "published_at": published_at, "age_minutes": age,
                              "also_submitted_to": other_campaigns}
    if age is not None and age > SUBMISSION_WINDOW_MIN:
        result["warning"] = f"Post is {age:.0f} min old, past the {SUBMISSION_WINDOW_MIN}-minute submission window."
    if check_only:
        return {"logged": False, **result, "message": "Not yet submitted to this campaign; OK to submit."}

    new = not SUBMITTED_FILE.exists()
    with SUBMITTED_FILE.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        if new:
            writer.writeheader()
        writer.writerow({"submitted_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
                         "campaign_url": campaign_url.strip(), "platform": plat, "post_url": post_url.strip(),
                         "published_at": published_at or "", "age_minutes_at_submit": "" if age is None else age})
    return {"logged": True, **result, "file": str(SUBMITTED_FILE)}


if __name__ == "__main__":
    mcp.run()
