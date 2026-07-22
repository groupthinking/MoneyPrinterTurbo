"""
Omni-distribution orchestration layer.

This module provides a phased, gateable way to publish generated videos across
multiple distribution channels. The design goals are:

1. Each channel is self-contained (config, validation, upload).
2. New channels can be added by registering an adapter — if a native API/MCP is
   not available, the adapter can fall back to browser automation
   (Browserbase, Playwright, Chrome DevTools MCP, etc.).
3. Channels run in a small pilot mode by default; expansion is controlled by
   config flags so we can validate one channel before turning others on.
4. Failures are isolated: one channel failing does not abort the others.

Out-of-the-box adapters:
  - youtube        (YouTube Data API v3)
  - upload_post    (TikTok/Instagram via Upload-Post API)
  - composio       (Composio MCP stub — YouTube/Google/TikTok/Meta actions)
  - shopify        (Shopify API / MailerLite email stub)

Fallback adapters (pilot stubs):
  - browserbase    (Browserbase MCP stub)
  - playwright     (Playwright MCP stub)
  - chrome_devtools (Chrome DevTools Protocol MCP stub)
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from app.config import config


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class DistributionResult:
    """Result of a single distribution attempt."""

    channel: str
    success: bool
    platform: str | None = None
    request_id: str | None = None
    url: str | None = None
    error: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class DistributionPayload:
    """Normalized payload handed to every channel adapter."""

    video_path: str
    title: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    affiliate_url: str = ""
    include_disclosure: bool = True
    privacy_status: str | None = None


# ---------------------------------------------------------------------------
# Base adapter
# ---------------------------------------------------------------------------

class DistributionAdapter(ABC):
    """Abstract base for a distribution channel."""

    name: str = ""
    platforms: list[str] = []

    def __init__(self, channel_cfg: dict[str, Any]):
        self.cfg = channel_cfg

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True when the channel has enough config to attempt an upload."""
        raise NotImplementedError

    @abstractmethod
    def validate(self) -> dict[str, Any]:
        """Lightweight health/validation check before upload."""
        raise NotImplementedError

    @abstractmethod
    def upload(self, payload: DistributionPayload) -> DistributionResult:
        """Upload a video and return a DistributionResult."""
        raise NotImplementedError

    # Subclasses may override per-platform limits
    max_caption_length = 2200
    max_title_length = 100

    def _build_caption(self, payload: DistributionPayload) -> str:
        """Build a cross-post caption with affiliate link and disclosure."""
        parts = [payload.title]
        if payload.include_disclosure:
            parts.append("#ad")
        if payload.affiliate_url:
            parts.append(payload.affiliate_url)
        parts += ["#shorts", "#viral"]
        return " ".join(parts)[: self.max_caption_length]


# ---------------------------------------------------------------------------
# Built-in adapters
# ---------------------------------------------------------------------------

class YouTubeAdapter(DistributionAdapter):
    name = "youtube"
    platforms = ["youtube"]

    def is_configured(self) -> bool:
        from app.services import youtube as yt_svc

        return yt_svc.is_authorised()

    def validate(self) -> dict[str, Any]:
        from app.services import youtube as yt_svc

        if not config.app.get("youtube_client_id") or not config.app.get("youtube_client_secret"):
            return {"configured": False, "error": "youtube_client_id and youtube_client_secret not set"}
        if not yt_svc.is_authorised():
            return {"configured": False, "error": "YouTube not authorised — run authorize_youtube.py"}
        return {"configured": True}

    def upload(self, payload: DistributionPayload) -> DistributionResult:
        from app.services import youtube as yt_svc

        from googleapiclient.errors import HttpError

        try:
            result = yt_svc.upload_video(
                video_path=payload.video_path,
                title=payload.title[: self.max_title_length] or "New Video",
                description=payload.description,
                tags=payload.tags or ["shorts", "viral"],
                privacy_status=payload.privacy_status,
            )
            return DistributionResult(
                channel=self.name,
                platform="youtube",
                success=True,
                url=result.get("url"),
                details=result,
            )
        except (HttpError, FileNotFoundError, RuntimeError) as exc:
            logger.warning(f"YouTube distribution failed: {exc}")
            return DistributionResult(
                channel=self.name,
                platform="youtube",
                success=False,
                error=str(exc),
            )


class UploadPostAdapter(DistributionAdapter):
    name = "upload_post"
    platforms = ["tiktok", "instagram"]

    def is_configured(self) -> bool:
        from app.services import upload_post as up_svc

        return up_svc.upload_post_service.is_configured()

    def validate(self) -> dict[str, Any]:
        api_key = self.cfg.get("api_key") or config.app.get("upload_post_api_key", "")
        username = self.cfg.get("username") or config.app.get("upload_post_username", "")
        enabled = self.cfg.get("enabled", config.app.get("upload_post_enabled", False))
        if not enabled:
            return {"configured": False, "error": "Upload-Post is disabled"}
        if not api_key or not username:
            return {"configured": False, "error": "Upload-Post requires api_key and username"}
        return {"configured": True}

    def upload(self, payload: DistributionPayload) -> DistributionResult:
        from app.services import upload_post as up_svc

        platforms = self.cfg.get("platforms") or config.app.get("upload_post_platforms", ["tiktok", "instagram"])
        result = up_svc.upload_post_service.upload_video(
            video_path=payload.video_path,
            title=self._build_caption(payload),
            platforms=platforms,
            affiliate_url="",  # already folded into caption
            include_disclosure=False,
        )
        if result.get("success"):
            return DistributionResult(
                channel=self.name,
                platform=", ".join(platforms),
                success=True,
                request_id=result.get("request_id"),
                details=result,
            )
        return DistributionResult(
            channel=self.name,
            platform=", ".join(platforms),
            success=False,
            error=result.get("error", "Unknown Upload-Post error"),
            details=result,
        )


# ---------------------------------------------------------------------------
# Pilot / MCP / fallback adapter stubs
# ---------------------------------------------------------------------------

class _StubAdapter(DistributionAdapter):
    """Base for adapters that are not implemented yet but appear in config."""

    def is_configured(self) -> bool:
        return self.cfg.get("enabled") and (self.cfg.get("mcp_url") or self.cfg.get("api_key"))

    def validate(self) -> dict[str, Any]:
        if not self.cfg.get("enabled"):
            return {"configured": False, "error": f"{self.name} is disabled"}
        if not (self.cfg.get("mcp_url") or self.cfg.get("api_key")):
            return {"configured": False, "error": f"{self.name} requires mcp_url or api_key"}
        return {"configured": True, "note": "pilot stub — no real upload performed"}

    def upload(self, payload: DistributionPayload) -> DistributionResult:
        logger.info(f"[{self.name}] pilot stub invoked for {payload.video_path}")
        return DistributionResult(
            channel=self.name,
            platform=self.platforms[0] if self.platforms else self.name,
            success=False,
            error=f"{self.name} adapter is a pilot stub and not yet implemented",
        )


class ComposioAdapter(_StubAdapter):
    name = "composio"
    platforms = ["youtube", "google", "tiktok", "meta"]


class ShopifyAdapter(_StubAdapter):
    name = "shopify"
    platforms = ["shopify", "mailerlite"]


class BrowserbaseAdapter(_StubAdapter):
    name = "browserbase"
    platforms = ["browser"]


class PlaywrightAdapter(_StubAdapter):
    name = "playwright"
    platforms = ["browser"]


class ChromeDevToolsAdapter(_StubAdapter):
    name = "chrome_devtools"
    platforms = ["browser"]


# ---------------------------------------------------------------------------
# Registry and manager
# ---------------------------------------------------------------------------

ADAPTER_REGISTRY: dict[str, type[DistributionAdapter]] = {
    "youtube": YouTubeAdapter,
    "upload_post": UploadPostAdapter,
    "composio": ComposioAdapter,
    "shopify": ShopifyAdapter,
    "browserbase": BrowserbaseAdapter,
    "playwright": PlaywrightAdapter,
    "chrome_devtools": ChromeDevToolsAdapter,
}

DEFAULT_CHANNELS = ["youtube", "upload_post"]


def _get_channel_config(channel_name: str) -> dict[str, Any]:
    """Read per-channel config from config.toml under [distribution.channels]."""
    dist_cfg = config._cfg.get("distribution", {})
    channels_cfg = dist_cfg.get("channels", {})
    return channels_cfg.get(channel_name, {})


def _get_enabled_channels() -> list[str]:
    """Return the ordered list of channels that should run."""
    dist_cfg = config._cfg.get("distribution", {})
    enabled = dist_cfg.get("enabled_channels")
    if enabled is not None:
        return [c for c in enabled if c in ADAPTER_REGISTRY]
    return DEFAULT_CHANNELS


def _get_fallback_channels() -> list[str]:
    """Return fallback channel names used when a primary channel lacks an MCP."""
    dist_cfg = config._cfg.get("distribution", {})
    fallback = dist_cfg.get("fallback_channels", ["browserbase", "playwright", "chrome_devtools"])
    return [c for c in fallback if c in ADAPTER_REGISTRY]


def validate_channel(channel_name: str) -> dict[str, Any]:
    """Validate a single channel by name."""
    adapter_cls = ADAPTER_REGISTRY.get(channel_name)
    if not adapter_cls:
        return {"configured": False, "error": f"Unknown distribution channel '{channel_name}'"}
    cfg = _get_channel_config(channel_name)
    adapter = adapter_cls(cfg)
    return {
        "channel": channel_name,
        "configured": adapter.is_configured(),
        "validation": adapter.validate(),
    }


def validate_all() -> dict[str, dict[str, Any]]:
    """Validate every registered channel and return a map of results."""
    return {name: validate_channel(name) for name in ADAPTER_REGISTRY}


def distribute(payload: DistributionPayload, *, force_enabled: bool | None = None) -> list[DistributionResult]:
    """Distribute a video to all enabled channels, isolating failures."""
    dist_cfg = config._cfg.get("distribution", {})
    if force_enabled is False or (force_enabled is None and not dist_cfg.get("enabled", True)):
        logger.info("Omni-distribution is disabled; skipping distribution")
        return []
    results: list[DistributionResult] = []
    for channel_name in _get_enabled_channels():
        adapter_cls = ADAPTER_REGISTRY.get(channel_name)
        if not adapter_cls:
            logger.warning(f"Unknown distribution channel '{channel_name}', skipping")
            continue
        cfg = _get_channel_config(channel_name)
        adapter = adapter_cls(cfg)
        validation = adapter.validate()
        if not validation.get("configured"):
            logger.info(f"[{channel_name}] not configured: {validation.get('error')}")
            results.append(
                DistributionResult(
                    channel=channel_name,
                    platform=adapter.platforms[0] if adapter.platforms else channel_name,
                    success=False,
                    error=validation.get("error", "Not configured"),
                )
            )
            continue
        logger.info(f"[{channel_name}] distributing {payload.video_path}")
        result = adapter.upload(payload)
        results.append(result)
    return results


def add_channel(name: str, adapter_cls: type[DistributionAdapter]) -> None:
    """Register a custom distribution adapter at runtime."""
    ADAPTER_REGISTRY[name] = adapter_cls
