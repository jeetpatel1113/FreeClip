"""Job handlers: process-project, render-clip, publish-post."""

from __future__ import annotations

import logging
import shutil
import time
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import api_client, broll, captions, curator, ingest, publisher, reframer, storage
from .config import settings
from .ffmpeg_utils import cut_segment, thumbnail

log = logging.getLogger(__name__)


@contextmanager
def workspace(name: str) -> Iterator[Path]:
    path = settings.work_dir / f"{name}-{int(time.time() * 1000)}"
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


class Throttle:
    """Rate-limit progress callbacks so long downloads/transcriptions don't flood the API."""

    def __init__(self, interval: float = 2.0) -> None:
        self.interval = interval
        self.last = 0.0

    def ready(self) -> bool:
        now = time.monotonic()
        if now - self.last >= self.interval:
            self.last = now
            return True
        return False


def _source_key(project_id: str) -> str:
    return f"projects/{project_id}/source.mp4"


def _reframed_key(project_id: str, index: int) -> str:
    return f"projects/{project_id}/clips/{index}/reframed.mp4"


def _render_final(clip: dict[str, Any], reframed: Path, layout: str, broll_enabled: bool, workdir: Path,
                  version: int) -> tuple[str, str]:
    duration_ms = clip["endMs"] - clip["startMs"]
    cutaways = broll.fetch_broll(clip.get("brollKeywords") or [], duration_ms, workdir / "broll") \
        if broll_enabled else []
    final = workdir / f"clip{clip['index']}_final.mp4"
    captions.render(reframed, clip["words"], clip.get("captionStyle") or "HORMOZI", clip.get("hook"), duration_ms,
                    final, layout=layout, broll=cutaways)
    thumb = thumbnail(final, workdir / f"clip{clip['index']}.jpg", at_s=min(1.5, duration_ms / 2000))
    base = f"projects/{clip['projectId']}/clips/{clip['index']}"
    video_key = storage.upload(final, f"{base}/final-v{version}.mp4")
    thumb_key = storage.upload(thumb, f"{base}/thumb-v{version}.jpg", "image/jpeg")
    return video_key, thumb_key


# --------------------------------------------------------------------------- process-project


def process_project(data: dict[str, Any]) -> None:
    pid = data["projectId"]
    try:
        with workspace(f"project-{pid}") as wd:
            _process_project(pid, data, wd)
    except Exception as exc:  # noqa: BLE001
        log.exception("project %s failed", pid)
        api_client.project_progress(pid, "FAILED", 100, "Failed", error=f"{exc}\n\n{traceback.format_exc()[-1500:]}")


def _process_project(pid: str, data: dict[str, Any], wd: Path) -> None:
    throttle = Throttle()

    def on_download(frac: float, msg: str) -> None:
        if throttle.ready():
            api_client.project_progress(pid, "DOWNLOADING", 2 + 13 * frac, msg)

    def on_transcribe(frac: float, msg: str) -> None:
        if throttle.ready():
            api_client.project_progress(pid, "TRANSCRIBING", 15 + 25 * frac, f"{msg} {frac * 100:.0f}%")

    api_client.project_progress(pid, "DOWNLOADING", 1, "Fetching source video")
    source = ingest.fetch_source(data, wd, on_download)
    # Keep a copy in object storage so clips can be re-rendered later without re-downloading.
    if data.get("sourceType") == "URL":
        storage.upload(source.video, _source_key(pid))
    thumb = thumbnail(source.video, wd / "project.jpg", at_s=min(5.0, source.duration_ms / 2000))
    api_client.project_progress(pid, "TRANSCRIBING", 15, "Extracting audio", durationMs=source.duration_ms,
                                title=source.title,
                                thumbnailKey=storage.upload(thumb, f"projects/{pid}/thumb.jpg", "image/jpeg"))

    wav = ingest.extract_wav(source.video, wd / "audio16k.wav")
    transcript = ingest.transcribe(wav, source.duration_ms, on_transcribe)
    if not transcript["words"]:
        raise RuntimeError("No speech detected in this video; nothing to clip.")
    api_client.project_progress(pid, "CURATING", 40, f"Claude is reviewing {len(transcript['words'])} words",
                                transcript={"language": transcript["language"], "segments": transcript["segments"],
                                            "words": [[w["word"], w["start"], w["end"]] for w in transcript["words"]]})

    clips = curator.curate(transcript, source.duration_ms, source.title,
                           int(data.get("clipCountMin", 5)), int(data.get("clipCountMax", 10)))
    for c in clips:
        c["captionStyle"] = data.get("captionStyle", "HORMOZI")
    saved = {c["index"]: c["id"] for c in api_client.save_clips(pid, clips)}
    api_client.project_progress(pid, "REFRAMING", 50, f"Found {len(clips)} clips; tracking active speakers")

    layouts: dict[int, tuple[Path, str]] = {}
    for n, clip in enumerate(clips):
        cid = saved[clip["index"]]
        try:
            api_client.update_clip(cid, "RENDERING")
            segment = cut_segment(source.video, clip["startMs"], clip["endMs"], wd / f"clip{clip['index']}_raw.mp4")
            result = reframer.reframe(segment, wd / f"clip{clip['index']}_9x16.mp4", wd)
            storage.upload(result.path, _reframed_key(pid, clip["index"]))
            layouts[clip["index"]] = (result.path, result.layout)
            api_client.update_clip(cid, "RENDERING", layout=result.layout)
        except Exception as exc:  # noqa: BLE001
            log.exception("reframe failed for clip %s", cid)
            api_client.update_clip(cid, "FAILED", error=f"Reframing failed: {exc}")
        api_client.project_progress(pid, "REFRAMING", 50 + 25 * (n + 1) / len(clips),
                                    f"Reframed {n + 1}/{len(clips)} clips")

    ready = 0
    for n, clip in enumerate(clips):
        if clip["index"] not in layouts:
            continue
        cid = saved[clip["index"]]
        path, layout = layouts[clip["index"]]
        try:
            video_key, thumb_key = _render_final({**clip, "projectId": pid}, path, layout,
                                                 bool(data.get("brollEnabled")), wd, version=1)
            api_client.update_clip(cid, "READY", videoKey=video_key, thumbnailKey=thumb_key, error=None)
            ready += 1
        except Exception as exc:  # noqa: BLE001
            log.exception("caption render failed for clip %s", cid)
            api_client.update_clip(cid, "FAILED", error=f"Caption render failed: {exc}")
        api_client.project_progress(pid, "CAPTIONING", 75 + 24 * (n + 1) / len(clips),
                                    f"Rendered captions {n + 1}/{len(clips)}")

    if ready == 0:
        raise RuntimeError("Every clip failed to render; see clip errors for details.")
    api_client.project_progress(pid, "READY", 100, f"{ready} clips ready")


# --------------------------------------------------------------------------- render-clip


def render_clip(data: dict[str, Any]) -> None:
    cid = data["clipId"]
    try:
        info = api_client.get_clip(cid)
        clip, project = info["clip"], info["project"]
        with workspace(f"render-{cid}") as wd:
            reframed_key = _reframed_key(project["id"], clip["index"])
            reframed = wd / "reframed.mp4"
            layout = clip.get("layout") or "single"
            if storage.exists(reframed_key):
                storage.download(reframed_key, reframed)
            else:
                src_key = project["sourceKey"] if project["sourceType"] == "UPLOAD" else _source_key(project["id"])
                src = storage.download(src_key, wd / "source.mp4")
                segment = cut_segment(src, clip["startMs"], clip["endMs"], wd / "raw.mp4")
                result = reframer.reframe(segment, reframed, wd)
                layout = result.layout
                storage.upload(reframed, reframed_key)
            video_key, thumb_key = _render_final(clip, reframed, layout, bool(project.get("brollEnabled")), wd,
                                                 version=int(clip.get("renderVersion", 0)) + 1)
        api_client.update_clip(cid, "READY", videoKey=video_key, thumbnailKey=thumb_key, layout=layout, error=None)
    except Exception as exc:  # noqa: BLE001
        log.exception("render failed for clip %s", cid)
        api_client.update_clip(cid, "FAILED", error=f"Render failed: {exc}")


# --------------------------------------------------------------------------- publish-post


def publish_post(data: dict[str, Any]) -> None:
    post_id = data["postId"]
    if not api_client.claim_post(post_id):
        log.info("post %s not claimable (cancelled, rescheduled, or already handled); skipping", post_id)
        return
    try:
        ctx = api_client.get_post(post_id)
        with workspace(f"publish-{post_id}") as wd:
            outcome = publisher.publish(ctx, wd)
    except Exception as exc:  # noqa: BLE001
        log.exception("publish failed for post %s", post_id)
        api_client.update_post(post_id, "FAILED", error=str(exc)[:5000])
        return

    agent_log = {"steps": outcome.log, "finishedAt": datetime.now(UTC).isoformat()}
    if outcome.status == "PUBLISHED":
        api_client.update_post(post_id, "PUBLISHED", externalPostId=outcome.external_id, externalUrl=outcome.url,
                               caption=outcome.caption, agentLog=agent_log, error=None)
    elif outcome.status == "RESCHEDULED" and outcome.reschedule_to:
        # The API's cron sweep re-enqueues it when due.
        api_client.update_post(post_id, "SCHEDULED", scheduledFor=outcome.reschedule_to.isoformat(),
                               error=f"Deferred by agent: {outcome.error}", agentLog=agent_log)
    else:
        api_client.update_post(post_id, "FAILED", error=(outcome.error or "Unknown error")[:5000], agentLog=agent_log)
