"""FFmpeg helpers: probing, accurate cutting, and GPU-aware encoder selection."""

from __future__ import annotations

import functools
import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import settings

log = logging.getLogger(__name__)


class FFmpegError(RuntimeError):
    pass


def run(cmd: list[str], timeout: int | None = None) -> None:
    log.debug("exec: %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise FFmpegError(f"{cmd[0]} failed ({proc.returncode}): {proc.stderr[-2000:]}")


@dataclass(frozen=True)
class MediaInfo:
    duration_ms: int
    width: int
    height: int
    fps: float
    has_audio: bool


def probe(path: Path) -> MediaInfo:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    data = json.loads(out)
    video = next((s for s in data["streams"] if s.get("codec_type") == "video"), None)
    if video is None:
        raise FFmpegError(f"No video stream in {path.name}")
    num, _, den = (video.get("avg_frame_rate") or video.get("r_frame_rate") or "30/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 30.0
    # Account for rotation metadata on phone footage.
    width, height = int(video["width"]), int(video["height"])
    rotation = 0
    for sd in video.get("side_data_list", []) or []:
        if "rotation" in sd:
            rotation = abs(int(sd["rotation"]))
    if rotation in (90, 270):
        width, height = height, width
    duration = float(data["format"].get("duration") or video.get("duration") or 0)
    return MediaInfo(
        duration_ms=int(duration * 1000),
        width=width,
        height=height,
        fps=fps if 1 <= fps <= 240 else 30.0,
        has_audio=any(s.get("codec_type") == "audio" for s in data["streams"]),
    )


@functools.lru_cache(maxsize=1)
def nvenc_available() -> bool:
    if settings.ffmpeg_encoder == "cpu":
        return False
    try:
        # A real 1-frame encode: the encoder is often compiled in even when no GPU is attached.
        run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=black:s=256x256:d=0.1",
             "-frames:v", "1", "-c:v", "h264_nvenc", "-f", "null", "-"],
            timeout=30,
        )
        log.info("NVENC hardware encoder available")
        return True
    except (FFmpegError, subprocess.TimeoutExpired, FileNotFoundError):
        if settings.ffmpeg_encoder == "nvenc":
            log.warning("FFMPEG_ENCODER=nvenc but NVENC is unavailable; falling back to libx264")
        return False


def video_encoder_args(quality: str = "final") -> list[str]:
    """Encoder flags. quality: 'final' (delivery) or 'intermediate' (near-lossless, fast)."""
    if nvenc_available():
        cq = "19" if quality == "final" else "16"
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", cq, "-b:v", "0",
                "-pix_fmt", "yuv420p"]
    crf = "20" if quality == "final" else "16"
    preset = "medium" if quality == "final" else "veryfast"
    return ["-c:v", "libx264", "-preset", preset, "-crf", crf, "-pix_fmt", "yuv420p"]


def audio_encoder_args() -> list[str]:
    return ["-c:a", "aac", "-b:a", "192k", "-ar", "48000"]


def cut_segment(src: Path, start_ms: int, end_ms: int, out: Path) -> Path:
    """Frame-accurate cut (re-encode) so analysis and audio stay perfectly aligned."""
    start = start_ms / 1000
    dur = (end_ms - start_ms) / 1000
    run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", str(src),
         "-t", f"{dur:.3f}", "-map", "0:v:0", "-map", "0:a:0?", *video_encoder_args("intermediate"),
         *audio_encoder_args(), "-movflags", "+faststart", str(out)]
    )
    return out


def extract_wav(src: Path, out: Path) -> Path:
    """16 kHz mono PCM WAV for Whisper and WebRTC VAD."""
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", str(out)])
    return out


def thumbnail(src: Path, out: Path, at_s: float = 1.0) -> Path:
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{at_s:.2f}", "-i", str(src),
         "-frames:v", "1", "-q:v", "3", "-vf", "scale=540:-2", str(out)])
    return out
