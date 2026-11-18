from __future__ import annotations

from pathlib import Path

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from .config import settings

_client = boto3.client(
    "s3",
    endpoint_url=settings.s3_endpoint,
    region_name=settings.s3_region,
    aws_access_key_id=settings.s3_access_key,
    aws_secret_access_key=settings.s3_secret_key,
    config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
)


def upload(path: Path, key: str, content_type: str = "video/mp4") -> str:
    _client.upload_file(str(path), settings.s3_bucket, key, ExtraArgs={"ContentType": content_type})
    return key


def download(key: str, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    _client.download_file(settings.s3_bucket, key, str(path))
    return path


def exists(key: str) -> bool:
    try:
        _client.head_object(Bucket=settings.s3_bucket, Key=key)
        return True
    except ClientError:
        return False
