"""Optional Pexels B-roll: portrait stock cutaways placed away from the hook."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import requests

from .config import settings

log = logging.getLogger(__name__)

PEXELS_SEARCH = "https://api.pexels.com/videos/search"
CUTAWAY_MS = 2500


@dataclass
class BrollClip:
    path: Path
    at_ms: int
    duration_ms: int
    keyword: str


def _best_file(video: dict) -> str | None:
    files = [f for f in video.get("video_files", []) if f.get("file_type") == "video/mp4" and f.get("link")]
    if not files:
        return None
    # Prefer portrait files closest to 1920 tall without going far above it.
    files.sort(key=lambda f: (f.get("height", 0) < f.get("width", 0), abs((f.get("height") or 0) - 1920)))
    return files[0]["link"]


def fetch_broll(keywords: list[str], duration_ms: int, workdir: Path, hook_ms: int = 4000) -> list[BrollClip]:
    if not settings.pexels_api_key or not keywords or duration_ms < hook_ms + CUTAWAY_MS * 2:
        return []
    workdir.mkdir(parents=True, exist_ok=True)
    # Evenly space cutaways after the hook, leaving the ending clean for the payoff.
    usable = duration_ms - hook_ms - CUTAWAY_MS * 2
    slots = [hook_ms + int(usable * (i + 1) / (len(keywords) + 1)) for i in range(len(keywords))]
    out: list[BrollClip] = []
    for kw, at in zip(keywords, slots, strict=True):
        try:
            resp = requests.get(
                PEXELS_SEARCH,
                params={"query": kw, "orientation": "portrait", "size": "medium", "per_page": 5},
                headers={"Authorization": settings.pexels_api_key},
                timeout=20,
            )
            resp.raise_for_status()
            videos = [v for v in resp.json().get("videos", []) if v.get("duration", 0) * 1000 >= CUTAWAY_MS]
            link = next((link for v in videos if (link := _best_file(v))), None)
            if not link:
                continue
            path = workdir / f"broll_{len(out)}.mp4"
            with requests.get(link, stream=True, timeout=60) as dl:
                dl.raise_for_status()
                with path.open("wb") as fh:
                    for chunk in dl.iter_content(1 << 20):
                        fh.write(chunk)
            out.append(BrollClip(path, at, CUTAWAY_MS, kw))
        except requests.RequestException as exc:
            log.warning("pexels b-roll for %r failed: %s", kw, exc)
    return out
