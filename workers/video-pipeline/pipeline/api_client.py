"""Thin client for the API's /internal callback routes."""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

from .config import settings

log = logging.getLogger(__name__)

_session = requests.Session()


def _request(method: str, path: str, json: Any = None, retries: int = 4) -> Any:
    url = f"{settings.api_url}{path}"
    headers = {"x-internal-token": settings.internal_token}
    for attempt in range(retries + 1):
        try:
            resp = _session.request(method, url, json=json, headers=headers, timeout=60)
        except requests.RequestException as exc:
            if attempt == retries:
                raise
            log.warning("API %s %s failed (%s); retrying", method, path, exc)
            time.sleep(2**attempt)
            continue
        if resp.status_code >= 500 and attempt < retries:
            time.sleep(2**attempt)
            continue
        if resp.status_code >= 400:
            raise RuntimeError(f"API {method} {path} -> {resp.status_code}: {resp.text[:500]}")
        return resp.json() if resp.content else None
    raise RuntimeError("unreachable")


def project_progress(project_id: str, status: str, progress: float, message: str | None = None, **extra: Any) -> None:
    body: dict[str, Any] = {"status": status, "progress": max(0.0, min(100.0, progress))}
    if message:
        body["message"] = message[:500]
    body.update({k: v for k, v in extra.items() if v is not None})
    _request("POST", f"/internal/projects/{project_id}/progress", body)


def save_clips(project_id: str, clips: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _request("POST", f"/internal/projects/{project_id}/clips", {"clips": clips})["clips"]


def get_clip(clip_id: str) -> dict[str, Any]:
    return _request("GET", f"/internal/clips/{clip_id}")


def update_clip(clip_id: str, status: str, **fields: Any) -> None:
    body = {"status": status, **{k: v for k, v in fields.items() if v is not None}}
    if "error" in body and isinstance(body["error"], str):
        body["error"] = body["error"][:5000]
    _request("PATCH", f"/internal/clips/{clip_id}", body)


def get_post(post_id: str) -> dict[str, Any]:
    return _request("GET", f"/internal/posts/{post_id}")


def claim_post(post_id: str) -> bool:
    return bool(_request("POST", f"/internal/posts/{post_id}/claim", {})["claimed"])


def update_post(post_id: str, status: str, **fields: Any) -> None:
    body = {"status": status, **{k: v for k, v in fields.items() if v is not None}}
    _request("PATCH", f"/internal/posts/{post_id}", body)


def update_account_tokens(
    account_id: str, access_token: str | None, refresh_token: str | None, expires_at: str | None
) -> None:
    body = {"accessToken": access_token, "refreshToken": refresh_token, "expiresAt": expires_at}
    _request("PATCH", f"/internal/social-accounts/{account_id}", {k: v for k, v in body.items() if v})
