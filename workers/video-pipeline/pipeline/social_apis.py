"""Direct uploads to Instagram Graph, TikTok Content Posting, YouTube Data v3, X v2 and LinkedIn.

Every uploader returns a PublishResult. Errors raise PublishError with the platform's message so
the publisher agent can see (and report) exactly what went wrong.
"""

from __future__ import annotations

import logging
import math
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from . import api_client
from .config import settings

log = logging.getLogger(__name__)
TIMEOUT = 60


class PublishError(RuntimeError):
    pass


@dataclass
class PublishResult:
    external_id: str
    url: str | None
    detail: str = ""


@dataclass
class Account:
    id: str
    platform: str
    access_token: str
    refresh_token: str | None
    client_id: str | None
    client_secret: str | None
    external_id: str | None
    expires_at: datetime | None
    meta: dict[str, Any]

    @classmethod
    def from_api(cls, a: dict[str, Any]) -> Account:
        exp = a.get("expiresAt")
        return cls(
            id=a["id"], platform=a["platform"], access_token=a.get("accessToken") or "",
            refresh_token=a.get("refreshToken"), client_id=a.get("clientId"), client_secret=a.get("clientSecret"),
            external_id=a.get("externalId"),
            expires_at=datetime.fromisoformat(exp.replace("Z", "+00:00")) if exp else None,
            meta=a.get("meta") or {},
        )

    def expiring(self) -> bool:
        return bool(self.expires_at and self.expires_at - datetime.now(UTC) < timedelta(minutes=5))

    def save_tokens(self, access: str, refresh: str | None, expires_in: int | None) -> None:
        self.access_token = access
        if refresh:
            self.refresh_token = refresh
        self.expires_at = datetime.now(UTC) + timedelta(seconds=expires_in) if expires_in else None
        api_client.update_account_tokens(
            self.id, access, refresh, self.expires_at.isoformat() if self.expires_at else None
        )


def _check(resp: requests.Response, what: str) -> dict[str, Any]:
    try:
        body = resp.json() if resp.content else {}
    except ValueError:
        body = {"raw": resp.text[:500]}
    if resp.status_code >= 400:
        raise PublishError(f"{what} failed ({resp.status_code}): {str(body)[:800]}")
    return body


def _dry(platform: str) -> PublishResult:
    fake = f"dryrun-{platform.lower()}-{uuid.uuid4().hex[:10]}"
    return PublishResult(fake, None, "PUBLISH_DRY_RUN=1: no request was sent")


# --------------------------------------------------------------------------- Instagram (Graph API)


def post_instagram_reel(acct: Account, video_url: str, caption: str, share_to_feed: bool = True) -> PublishResult:
    """Reels via a public video URL (the worker's S3_PUBLIC_ENDPOINT must be reachable by Meta)."""
    if settings.publish_dry_run:
        return _dry("INSTAGRAM")
    if not acct.external_id:
        raise PublishError("Instagram account is missing its Instagram Business/Creator user ID")
    base = f"https://graph.facebook.com/{settings.meta_graph_version}"
    body = _check(requests.post(f"{base}/{acct.external_id}/media", data={
        "media_type": "REELS", "video_url": video_url, "caption": caption[:2200],
        "share_to_feed": "true" if share_to_feed else "false", "access_token": acct.access_token,
    }, timeout=TIMEOUT), "Instagram container create")
    container = body["id"]
    deadline = time.time() + 15 * 60
    while True:
        st = _check(requests.get(f"{base}/{container}", params={
            "fields": "status_code,status", "access_token": acct.access_token}, timeout=TIMEOUT),
            "Instagram container status")
        code = st.get("status_code")
        if code == "FINISHED":
            break
        if code in {"ERROR", "EXPIRED"}:
            raise PublishError(f"Instagram processing failed: {st.get('status')}")
        if time.time() > deadline:
            raise PublishError("Instagram processing timed out after 15 minutes")
        time.sleep(6)
    pub = _check(requests.post(f"{base}/{acct.external_id}/media_publish", data={
        "creation_id": container, "access_token": acct.access_token}, timeout=TIMEOUT), "Instagram publish")
    media_id = pub["id"]
    link = requests.get(f"{base}/{media_id}", params={"fields": "permalink", "access_token": acct.access_token},
                        timeout=TIMEOUT)
    permalink = link.json().get("permalink") if link.ok else None
    return PublishResult(media_id, permalink)


# --------------------------------------------------------------------------- TikTok (Content Posting API)

TIKTOK = "https://open.tiktokapis.com"


def _tiktok_refresh(acct: Account) -> None:
    if not (acct.refresh_token and acct.client_id and acct.client_secret):
        return
    body = _check(requests.post(f"{TIKTOK}/v2/oauth/token/", data={
        "client_key": acct.client_id, "client_secret": acct.client_secret,
        "grant_type": "refresh_token", "refresh_token": acct.refresh_token,
    }, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=TIMEOUT), "TikTok token refresh")
    acct.save_tokens(body["access_token"], body.get("refresh_token"), body.get("expires_in"))


def post_tiktok_video(acct: Account, video: Path, title: str, privacy_level: str | None = None) -> PublishResult:
    if settings.publish_dry_run:
        return _dry("TIKTOK")
    if acct.expiring():
        _tiktok_refresh(acct)
    headers = {"Authorization": f"Bearer {acct.access_token}", "Content-Type": "application/json; charset=UTF-8"}

    info = _check(requests.post(f"{TIKTOK}/v2/post/publish/creator_info/query/", headers=headers, timeout=TIMEOUT),
                  "TikTok creator info")["data"]
    options = info.get("privacy_level_options") or ["SELF_ONLY"]
    privacy = privacy_level or settings.tiktok_privacy_level
    if privacy not in options:
        privacy = "SELF_ONLY" if "SELF_ONLY" in options else options[0]

    size = video.stat().st_size
    # Single chunk up to 64 MB; otherwise 10 MB chunks with the remainder merged into the last chunk.
    chunk = size if size <= 64 * 1024 * 1024 else 10 * 1024 * 1024
    total_chunks = max(1, size // chunk)
    init = _check(requests.post(f"{TIKTOK}/v2/post/publish/video/init/", headers=headers, json={
        "post_info": {
            "title": title[:2200], "privacy_level": privacy, "disable_duet": False, "disable_comment": False,
            "disable_stitch": False, "video_cover_timestamp_ms": 1000,
        },
        "source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk,
                        "total_chunk_count": total_chunks},
    }, timeout=TIMEOUT), "TikTok init")["data"]
    publish_id, upload_url = init["publish_id"], init["upload_url"]

    with video.open("rb") as fh:
        for i in range(total_chunks):
            start = i * chunk
            end = size - 1 if i == total_chunks - 1 else start + chunk - 1
            fh.seek(start)
            data = fh.read(end - start + 1)
            _check(requests.put(upload_url, data=data, headers={
                "Content-Type": "video/mp4", "Content-Length": str(len(data)),
                "Content-Range": f"bytes {start}-{end}/{size}"}, timeout=300), f"TikTok upload chunk {i + 1}")

    deadline = time.time() + 15 * 60
    while time.time() < deadline:
        st = _check(requests.post(f"{TIKTOK}/v2/post/publish/status/fetch/", headers=headers,
                                  json={"publish_id": publish_id}, timeout=TIMEOUT), "TikTok status")["data"]
        status = st.get("status")
        if status == "PUBLISH_COMPLETE":
            ids = st.get("publicaly_available_post_id") or []
            post_id = str(ids[0]) if ids else publish_id
            return PublishResult(post_id, f"https://www.tiktok.com/video/{ids[0]}" if ids else None,
                                 f"privacy={privacy}")
        if status == "FAILED":
            raise PublishError(f"TikTok publish failed: {st.get('fail_reason')}")
        time.sleep(5)
    # Uploaded but still processing (common for SELF_ONLY / inbox flows).
    return PublishResult(publish_id, None, f"Uploaded; TikTok still processing (privacy={privacy})")


# --------------------------------------------------------------------------- YouTube (Data API v3)


def _google_refresh(acct: Account) -> None:
    if not (acct.refresh_token and acct.client_id and acct.client_secret):
        return
    body = _check(requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": acct.client_id, "client_secret": acct.client_secret,
        "refresh_token": acct.refresh_token, "grant_type": "refresh_token",
    }, timeout=TIMEOUT), "Google token refresh")
    acct.save_tokens(body["access_token"], body.get("refresh_token"), body.get("expires_in"))


def post_youtube_short(acct: Account, video: Path, title: str, description: str, tags: list[str],
                       privacy_status: str | None = None) -> PublishResult:
    if settings.publish_dry_run:
        return _dry("YOUTUBE")
    # Google access tokens live 1 hour; refresh whenever we can.
    if acct.refresh_token or acct.expiring():
        _google_refresh(acct)
    if "#shorts" not in (title + " " + description).lower():
        description = f"{description}\n\n#Shorts".strip()
    meta = {
        "snippet": {"title": title[:100], "description": description[:5000],
                    "tags": [t.lstrip("#") for t in tags][:30], "categoryId": str(acct.meta.get("categoryId", "22"))},
        "status": {"privacyStatus": privacy_status or settings.youtube_privacy_status,
                   "selfDeclaredMadeForKids": False},
    }
    size = video.stat().st_size
    init = requests.post(
        "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
        headers={"Authorization": f"Bearer {acct.access_token}", "Content-Type": "application/json; charset=UTF-8",
                 "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)},
        json=meta, timeout=TIMEOUT)
    _check(init, "YouTube upload init")
    location = init.headers.get("Location")
    if not location:
        raise PublishError("YouTube did not return a resumable upload URL")
    with video.open("rb") as fh:
        resp = requests.put(location, data=fh, headers={"Content-Type": "video/mp4", "Content-Length": str(size)},
                            timeout=1800)
    body = _check(resp, "YouTube upload")
    vid = body["id"]
    return PublishResult(vid, f"https://youtube.com/shorts/{vid}")


# --------------------------------------------------------------------------- X (API v2)

X_API = "https://api.x.com/2"


def _x_refresh(acct: Account) -> None:
    if not (acct.refresh_token and acct.client_id):
        return
    auth = (acct.client_id, acct.client_secret) if acct.client_secret else None
    data = {"grant_type": "refresh_token", "refresh_token": acct.refresh_token}
    if not auth:
        data["client_id"] = acct.client_id
    body = _check(requests.post(f"{X_API}/oauth2/token", data=data, auth=auth, timeout=TIMEOUT), "X token refresh")
    acct.save_tokens(body["access_token"], body.get("refresh_token"), body.get("expires_in"))


def post_x_video(acct: Account, video: Path, text: str) -> PublishResult:
    """OAuth 2.0 user-context token with tweet.write, users.read, media.write (and offline.access)."""
    if settings.publish_dry_run:
        return _dry("X")
    if acct.expiring():
        _x_refresh(acct)
    auth = {"Authorization": f"Bearer {acct.access_token}"}
    size = video.stat().st_size
    init = _check(requests.post(f"{X_API}/media/upload/initialize", headers=auth, json={
        "media_type": "video/mp4", "total_bytes": size, "media_category": "tweet_video"}, timeout=TIMEOUT),
        "X media init")
    media_id = init["data"]["id"]
    chunk = 4 * 1024 * 1024
    with video.open("rb") as fh:
        for idx in range(math.ceil(size / chunk)):
            part = fh.read(chunk)
            _check(requests.post(f"{X_API}/media/upload/{media_id}/append", headers=auth,
                                 data={"segment_index": str(idx)}, files={"media": ("chunk", part)},
                                 timeout=300), f"X media append {idx}")
    fin = _check(requests.post(f"{X_API}/media/upload/{media_id}/finalize", headers=auth, timeout=TIMEOUT),
                 "X media finalize")
    info = (fin.get("data") or {}).get("processing_info")
    deadline = time.time() + 10 * 60
    while info and info.get("state") in {"pending", "in_progress"} and time.time() < deadline:
        time.sleep(max(2, int(info.get("check_after_secs", 5))))
        st = _check(requests.get(f"{X_API}/media/upload", headers=auth,
                                 params={"command": "STATUS", "media_id": media_id}, timeout=TIMEOUT),
                    "X media status")
        info = (st.get("data") or {}).get("processing_info")
    if info and info.get("state") == "failed":
        raise PublishError(f"X video processing failed: {info.get('error')}")
    tweet = _check(requests.post(f"{X_API}/tweets", headers=auth, json={
        "text": text[:280], "media": {"media_ids": [media_id]}}, timeout=TIMEOUT), "X create post")
    tid = tweet["data"]["id"]
    handle = acct.meta.get("username") or "i"
    return PublishResult(tid, f"https://x.com/{handle}/status/{tid}")


# --------------------------------------------------------------------------- LinkedIn (Versioned REST)


def post_linkedin_video(acct: Account, video: Path, commentary: str, title: str) -> PublishResult:
    """external_id must be the author URN (urn:li:person:... or urn:li:organization:...)."""
    if settings.publish_dry_run:
        return _dry("LINKEDIN")
    owner = acct.external_id
    if not owner or not owner.startswith("urn:li:"):
        raise PublishError("LinkedIn account needs its author URN (urn:li:person:… or urn:li:organization:…)")
    headers = {"Authorization": f"Bearer {acct.access_token}", "LinkedIn-Version": settings.linkedin_version,
               "X-Restli-Protocol-Version": "2.0.0", "Content-Type": "application/json"}
    size = video.stat().st_size
    init = _check(requests.post("https://api.linkedin.com/rest/videos?action=initializeUpload", headers=headers,
                                json={"initializeUploadRequest": {"owner": owner, "fileSizeBytes": size,
                                                                  "uploadCaptions": False, "uploadThumbnail": False}},
                                timeout=TIMEOUT), "LinkedIn video init")["value"]
    etags = []
    with video.open("rb") as fh:
        for ins in init["uploadInstructions"]:
            first, last = int(ins["firstByte"]), int(ins["lastByte"])
            fh.seek(first)
            resp = requests.put(ins["uploadUrl"], data=fh.read(last - first + 1),
                                headers={"Content-Type": "application/octet-stream"}, timeout=300)
            if resp.status_code >= 400:
                raise PublishError(f"LinkedIn part upload failed ({resp.status_code}): {resp.text[:300]}")
            etags.append(resp.headers.get("ETag", "").strip('"'))
    _check(requests.post("https://api.linkedin.com/rest/videos?action=finalizeUpload", headers=headers, json={
        "finalizeUploadRequest": {"video": init["video"], "uploadToken": init.get("uploadToken", ""),
                                  "uploadedPartIds": etags}}, timeout=TIMEOUT), "LinkedIn video finalize")
    resp = requests.post("https://api.linkedin.com/rest/posts", headers=headers, json={
        "author": owner, "commentary": commentary[:3000], "visibility": "PUBLIC",
        "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [], "thirdPartyDistributionChannels": []},
        "content": {"media": {"title": title[:200], "id": init["video"]}},
        "lifecycleState": "PUBLISHED", "isReshareDisabledByAuthor": False,
    }, timeout=TIMEOUT)
    _check(resp, "LinkedIn create post")
    urn = resp.headers.get("x-restli-id", "")
    return PublishResult(urn or init["video"], f"https://www.linkedin.com/feed/update/{urn}" if urn else None)
