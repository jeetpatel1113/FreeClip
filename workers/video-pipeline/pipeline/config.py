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

    anthropic_model: str = field(default_factory=lambda: os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5"))
    curator_effort: str = field(default_factory=lambda: os.getenv("CURATOR_EFFORT", "high"))
    publisher_effort: str = field(default_factory=lambda: os.getenv("PUBLISHER_EFFORT", "medium"))

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
