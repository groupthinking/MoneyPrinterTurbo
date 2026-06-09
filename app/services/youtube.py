"""YouTube Data API v3 upload service.

First-time setup:
  1. Set youtube_client_id and youtube_client_secret in config.toml
  2. Run:  uv run python authorize_youtube.py
     This opens a browser, you approve, and token is saved to
     storage/youtube_token.json automatically.
  3. Subsequent uploads use the saved token (auto-refreshed).
"""
import os
import random
import tempfile
import threading
import time
from pathlib import Path

from loguru import logger

from app.config import config

_TOKEN_PATH = Path("storage/youtube_token.json")
_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
_token_lock = threading.Lock()

# Privacy: "public" | "unlisted" | "private"
DEFAULT_PRIVACY = "public"

_LOOPBACK_REDIRECT = "http://localhost"


def _client_config() -> dict:
    """Build and return the OAuth2 installed-app client config dict."""
    client_id = config.app.get("youtube_client_id", "")
    client_secret = config.app.get("youtube_client_secret", "")
    if not client_id or not client_secret:
        raise RuntimeError(
            "youtube_client_id and youtube_client_secret must be set in config.toml"
        )
    return {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [_LOOPBACK_REDIRECT],
        }
    }


def _write_token_secure(json_str: str) -> None:
    """Write token JSON to disk atomically and restrict permissions to owner-only."""
    _TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=_TOKEN_PATH.parent,
        prefix=f"{_TOKEN_PATH.stem}.",
        suffix=".tmp",
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json_str)
        os.replace(str(tmp_path), str(_TOKEN_PATH))
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    try:
        os.chmod(str(_TOKEN_PATH), 0o600)
    except OSError:
        pass  # Windows does not support POSIX chmod


def _load_credentials():
    """Load saved OAuth token, refreshing if expired."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    if not _TOKEN_PATH.exists():
        raise RuntimeError(
            "YouTube not authorised yet. Run: uv run python authorize_youtube.py"
        )
    with _token_lock:
        creds = Credentials.from_authorized_user_file(str(_TOKEN_PATH), _SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            _write_token_secure(creds.to_json())
            logger.info("YouTube token refreshed")
        return creds


def _build_service():
    """Load credentials and return an authenticated YouTube Data API v3 service client."""
    from googleapiclient.discovery import build

    creds = _load_credentials()
    return build("youtube", "v3", credentials=creds)


def is_authorised() -> bool:
    """Return True if a valid, non-expired (or refreshable) token file exists."""
    if not _TOKEN_PATH.exists():
        return False
    try:
        from google.oauth2.credentials import Credentials
        creds = Credentials.from_authorized_user_file(str(_TOKEN_PATH), _SCOPES)
        return creds.valid or bool(creds.refresh_token)
    except Exception as exc:
        logger.debug(f"is_authorised check failed for {_TOKEN_PATH}: {exc}")
        return False


def get_auth_url() -> str:
    """Return the OAuth authorisation URL (for web-based auth flow)."""
    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_config(_client_config(), scopes=_SCOPES)
    flow.redirect_uri = _LOOPBACK_REDIRECT
    auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")
    return auth_url


def exchange_code(code: str) -> None:
    """Exchange an auth code for a token and save it to disk."""
    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_config(_client_config(), scopes=_SCOPES)
    flow.redirect_uri = _LOOPBACK_REDIRECT
    flow.fetch_token(code=code)
    with _token_lock:
        _write_token_secure(flow.credentials.to_json())
    logger.info(f"YouTube token saved to {_TOKEN_PATH}")


def _truncate_tags(tags: list[str], max_chars: int = 500) -> list[str]:
    """Return tags truncated so total character length stays within max_chars.

    YouTube counts comma separators between tags toward the 500-character limit.
    """
    result: list[str] = []
    total = 0
    for tag in tags:
        separator_len = 1 if result else 0  # comma between tags counts toward limit
        if total + separator_len + len(tag) > max_chars:
            break
        result.append(tag)
        total += separator_len + len(tag)
    return result


def upload_video(
    video_path: str,
    title: str,
    description: str = "",
    tags: list[str] | None = None,
    privacy_status: str | None = None,
    category_id: str = "22",  # 22 = People & Blogs
) -> dict:
    """Upload a video to YouTube. Returns {video_id, url}."""
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path}")

    privacy = privacy_status or config.app.get("youtube_default_privacy", DEFAULT_PRIVACY)
    service = _build_service()

    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": _truncate_tags(tags or []),
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(video_path, mimetype="video/*", resumable=True, chunksize=5 * 1024 * 1024)
    request = service.videos().insert(part="snippet,status", body=body, media_body=media)

    _RETRYABLE = (500, 502, 503, 504)
    _MAX_RETRIES = 5
    retry_count = 0
    response = None
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                logger.info(f"YouTube upload {int(status.progress() * 100)}%")
            retry_count = 0
        except HttpError as exc:
            if exc.resp.status in _RETRYABLE and retry_count < _MAX_RETRIES:
                retry_count += 1
                wait = (2 ** retry_count) + random.random()
                logger.warning(
                    f"YouTube upload transient HTTP {exc.resp.status}, "
                    f"retry {retry_count}/{_MAX_RETRIES} in {wait:.1f}s"
                )
                time.sleep(wait)
            else:
                raise

    video_id = response["id"]
    url = f"https://www.youtube.com/watch?v={video_id}"
    logger.success(f"YouTube upload complete: {url}")
    return {"video_id": video_id, "url": url, "privacy": privacy}
