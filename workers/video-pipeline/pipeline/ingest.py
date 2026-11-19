"""Ingest: download with yt-dlp, extract 16 kHz mono audio, word-level transcription.

Transcription uses faster-whisper (CTranslate2, CUDA when available) with word timestamps.
If USE_WHISPERX=1 and whisperx is installed, words are re-aligned with a wav2vec2 model for
tighter millisecond boundaries.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import storage
from .config import settings
from .ffmpeg_utils import extract_wav, probe

log = logging.getLogger(__name__)

ProgressFn = Callable[[float, str], None]


@dataclass
class Source:
    video: Path
    title: str | None
    duration_ms: int
    width: int
    height: int
    fps: float


# --------------------------------------------------------------------------- download


def duration_filter(max_seconds: int, rejected: list[str] | None = None) -> Callable[..., str | None]:
    """yt-dlp match_filter callable: return None to download, or a reason string to skip.

    Replaces a filter-string expression, whose parser compared int durations against str values
    and crashed with TypeError. Unknown durations (None or non-numeric) are allowed through and
    checked after download with ffprobe.
    """

    def _filter(info: dict[str, Any], *, incomplete: bool = False) -> str | None:
        if info.get("is_live") or info.get("live_status") in {"is_live", "is_upcoming"}:
            reason = "Live streams can't be clipped until they end; use the VOD link instead."
        else:
            try:
                duration = float(info.get("duration") or 0)
            except (TypeError, ValueError):
                duration = 0.0
            if not duration or duration <= max_seconds:
                return None
            reason = (f"Video is {duration / 60:.0f} min, longer than MAX_SOURCE_MINUTES "
                      f"({max_seconds // 60} min).")
        if rejected is not None:
            rejected.append(reason)
        return reason

    return _filter


def download_url(url: str, workdir: Path, on_progress: ProgressFn | None = None) -> tuple[Path, str | None]:
    import yt_dlp

    workdir.mkdir(parents=True, exist_ok=True)
    rejected: list[str] = []

    def hook(d: dict[str, Any]) -> None:
        if on_progress and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if total:
                on_progress(d.get("downloaded_bytes", 0) / total, f"Downloading {d.get('_percent_str', '').strip()}")

    opts: dict[str, Any] = {
        # Best MP4 video + M4A audio, falling back to best muxed stream; capped at 1080p (enough for 1080x1920 crops).
        "format": "bv*[ext=mp4][height<=1080]+ba[ext=m4a]/bv*[height<=1080]+ba/b[ext=mp4]/b",
        "merge_output_format": "mp4",
        "outtmpl": str(workdir / "source.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        # The console progress bar floods container logs; progress reaches the UI via the hook instead.
        "noprogress": True,
        "progress_hooks": [hook],
        "retries": 5,
        "fragment_retries": 10,
        "concurrent_fragment_downloads": 4,
        "match_filter": duration_filter(settings.max_source_minutes * 60, rejected),
    }
    if settings.ytdlp_cookies_file:
        opts["cookiefile"] = settings.ytdlp_cookies_file

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if info is None:
            raise RuntimeError("yt-dlp returned no info (video may be private, live, or too long)")
        if "entries" in info:
            info = next(iter(info["entries"]))
    if rejected:
        raise RuntimeError(rejected[0])
    candidates = sorted(workdir.glob("source.*"), key=lambda p: p.stat().st_size, reverse=True)
    video = next((p for p in candidates if p.suffix in {".mp4", ".mkv", ".webm", ".mov"}), None)
    if video is None:
        raise RuntimeError("Download finished but no video file was produced")
    return video, info.get("title")


def fetch_source(job: dict[str, Any], workdir: Path, on_progress: ProgressFn | None = None) -> Source:
    title = None
    if job.get("sourceType") == "URL":
        video, title = download_url(job["sourceUrl"], workdir, on_progress)
    else:
        key = job["sourceKey"]
        video = storage.download(key, workdir / ("source" + (Path(key).suffix.lower() or ".mp4")))
    info = probe(video)
    if info.duration_ms > settings.max_source_minutes * 60_000:
        raise RuntimeError(f"Source is longer than MAX_SOURCE_MINUTES ({settings.max_source_minutes} min)")
    return Source(video, title, info.duration_ms, info.width, info.height, info.fps)


# --------------------------------------------------------------------------- transcription

_model_lock = threading.Lock()
_model: Any = None


def _device() -> tuple[str, str]:
    device = settings.whisper_device
    if device == "auto":
        try:
            import ctranslate2

            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:  # noqa: BLE001
            device = "cpu"
    compute = settings.whisper_compute_type
    if compute == "auto":
        compute = "float16" if device == "cuda" else "int8"
    return device, compute


def _get_model() -> Any:
    global _model
    with _model_lock:
        if _model is None:
            from faster_whisper import WhisperModel

            device, compute = _device()
            log.info("loading faster-whisper %s on %s (%s)", settings.whisper_model, device, compute)
            _model = WhisperModel(settings.whisper_model, device=device, compute_type=compute)
        return _model


def transcribe(wav: Path, duration_ms: int, on_progress: ProgressFn | None = None) -> dict[str, Any]:
    """Returns {"language", "words": [{word,start,end,prob}], "segments": [{start,end,text,w0,w1}]} in ms."""
    model = _get_model()
    segments_iter, info = model.transcribe(
        str(wav),
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        beam_size=5,
        condition_on_previous_text=False,
    )
    words: list[dict[str, Any]] = []
    segments: list[dict[str, Any]] = []
    for seg in segments_iter:
        w0 = len(words)
        for w in seg.words or []:
            text = w.word.strip()
            if not text:
                continue
            words.append({"word": text, "start": int(w.start * 1000), "end": int(w.end * 1000),
                          "prob": round(float(w.probability), 3)})
        segments.append({"start": int(seg.start * 1000), "end": int(seg.end * 1000), "text": seg.text.strip(),
                         "w0": w0, "w1": len(words)})
        if on_progress and duration_ms:
            on_progress(min(1.0, seg.end * 1000 / duration_ms), "Transcribing")

    result = {"language": info.language, "words": words, "segments": segments}
    if settings.use_whisperx:
        result = _align_whisperx(wav, result)
    _fix_monotonic(result["words"])
    return result


def _align_whisperx(wav: Path, result: dict[str, Any]) -> dict[str, Any]:
    try:
        import whisperx
    except ImportError:
        log.warning("USE_WHISPERX=1 but whisperx is not installed; keeping faster-whisper timestamps")
        return result
    device, _ = _device()
    try:
        audio = whisperx.load_audio(str(wav))
        model_a, metadata = whisperx.load_align_model(language_code=result["language"], device=device)
        segs = [{"start": s["start"] / 1000, "end": s["end"] / 1000, "text": s["text"]} for s in result["segments"]]
        aligned = whisperx.align(segs, model_a, metadata, audio, device, return_char_alignments=False)
    except Exception as exc:  # noqa: BLE001
        log.warning("whisperx alignment failed (%s); keeping faster-whisper timestamps", exc)
        return result

    words: list[dict[str, Any]] = []
    segments: list[dict[str, Any]] = []
    for seg in aligned["segments"]:
        w0 = len(words)
        last_end = int(seg["start"] * 1000)
        for w in seg.get("words", []):
            text = str(w.get("word", "")).strip()
            if not text:
                continue
            # whisperx leaves numerals/symbols unaligned; interpolate from neighbours.
            start = int(w["start"] * 1000) if "start" in w else last_end
            end = int(w["end"] * 1000) if "end" in w else start + 200
            words.append({"word": text, "start": start, "end": end, "prob": round(float(w.get("score", 1.0)), 3)})
            last_end = end
        segments.append({"start": int(seg["start"] * 1000), "end": int(seg["end"] * 1000),
                         "text": seg["text"].strip(), "w0": w0, "w1": len(words)})
    return {"language": result["language"], "words": words, "segments": segments}


def _fix_monotonic(words: list[dict[str, Any]]) -> None:
    prev_end = 0
    for w in words:
        w["start"] = max(w["start"], prev_end)
        w["end"] = max(w["end"], w["start"] + 40)
        prev_end = w["start"]


def ingest(job: dict[str, Any], workdir: Path, on_download: ProgressFn, on_transcribe: ProgressFn
           ) -> tuple[Source, Path, dict[str, Any]]:
    source = fetch_source(job, workdir, on_download)
    wav = extract_wav(source.video, workdir / "audio16k.wav")
    transcript = transcribe(wav, source.duration_ms, on_transcribe)
    if not transcript["words"]:
        raise RuntimeError("No speech detected in this video; nothing to clip.")
    return source, wav, transcript
