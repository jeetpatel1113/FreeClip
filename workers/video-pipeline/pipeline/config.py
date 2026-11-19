from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    api_url: str = field(default_factory=lambda: os.getenv("API_URL", "http://localhost:4000").rstrip("/"))
    internal_token: str = field(default_factory=lambda: os.getenv("INTERNAL_API_TOKEN", ""))
    redis_url: str = field(default_factory=lambda: os.getenv("REDIS_URL", "redis://localhost:6379"))

    s3_endpoint: str = field(default_factory=lambda: os.getenv("S3_ENDPOINT", "http://localhost:9000"))
    s3_region: str = field(default_factory=lambda: os.getenv("S3_REGION", "us-east-1"))
    s3_bucket: str = field(default_factory=lambda: os.getenv("S3_BUCKET", "freeclip"))
    s3_access_key: str = field(default_factory=lambda: os.getenv("S3_ACCESS_KEY", "minioadmin"))
    s3_secret_key: str = field(default_factory=lambda: os.getenv("S3_SECRET_KEY", "minioadmin"))

    gemini_model: str = field(default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-3.8-flash"))
    gemini_timeout_seconds: int = field(default_factory=lambda: int(os.getenv("GEMINI_TIMEOUT_SECONDS", "600")))
    # Tried in order when GEMINI_MODEL is overloaded (503/429) or unavailable to the key (404).
    gemini_fallback_models: tuple[str, ...] = field(default_factory=lambda: tuple(
        m.strip() for m in os.getenv("GEMINI_FALLBACK_MODELS", "gemini-3.7-flash,gemini-3.6-flash,gemini-3.5-flash")
        .split(",") if m.strip()
    ))
    # BLOCK_NONE | BLOCK_ONLY_HIGH | BLOCK_MEDIUM_AND_ABOVE | OFF
    gemini_safety_threshold: str = field(default_factory=lambda: os.getenv("GEMINI_SAFETY_THRESHOLD", "BLOCK_NONE"))
    # -1 = dynamic thinking, 0 = off (Flash only), >0 = token budget. Empty = model default.
    gemini_thinking_budget: int | None = field(
        default_factory=lambda: int(v) if (v := os.getenv("GEMINI_THINKING_BUDGET", "-1").strip()) else None
    )

    whisper_model: str = field(default_factory=lambda: os.getenv("WHISPER_MODEL", "large-v3"))
    whisper_device: str = field(default_factory=lambda: os.getenv("WHISPER_DEVICE", "auto"))
    whisper_compute_type: str = field(default_factory=lambda: os.getenv("WHISPER_COMPUTE_TYPE", "auto"))
    use_whisperx: bool = field(default_factory=lambda: _bool("USE_WHISPERX"))

    ffmpeg_encoder: str = field(default_factory=lambda: os.getenv("FFMPEG_ENCODER", "auto"))  # auto|nvenc|cpu
    pexels_api_key: str = field(default_factory=lambda: os.getenv("PEXELS_API_KEY", ""))
    work_dir: Path = field(default_factory=lambda: Path(os.getenv("WORK_DIR", "/tmp/freeclip")))
    ytdlp_cookies_file: str = field(default_factory=lambda: os.getenv("YTDLP_COOKIES_FILE", ""))
    max_source_minutes: int = field(default_factory=lambda: int(os.getenv("MAX_SOURCE_MINUTES", "240")))

    pipeline_concurrency: int = field(default_factory=lambda: int(os.getenv("PIPELINE_CONCURRENCY", "1")))
    publish_concurrency: int = field(default_factory=lambda: int(os.getenv("PUBLISH_CONCURRENCY", "2")))
    publish_dry_run: bool = field(default_factory=lambda: _bool("PUBLISH_DRY_RUN"))
    tiktok_privacy_level: str = field(default_factory=lambda: os.getenv("TIKTOK_PRIVACY_LEVEL", "SELF_ONLY"))
    youtube_privacy_status: str = field(default_factory=lambda: os.getenv("YOUTUBE_PRIVACY_STATUS", "public"))
    meta_graph_version: str = field(default_factory=lambda: os.getenv("META_GRAPH_VERSION", "v21.0"))
    linkedin_version: str = field(default_factory=lambda: os.getenv("LINKEDIN_API_VERSION", "202505"))


settings = Settings()
