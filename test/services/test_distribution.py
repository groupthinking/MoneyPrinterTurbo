"""Unit tests for the omni-distribution service."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.config import config
from app.services.distribution import (
    ADAPTER_REGISTRY,
    DEFAULT_CHANNELS,
    DistributionAdapter,
    DistributionPayload,
    DistributionResult,
    UploadPostAdapter,
    YouTubeAdapter,
    add_channel,
    distribute,
    validate_all,
    validate_channel,
)


class _FakeAdapter(DistributionAdapter):
    name = "fake"
    platforms = ["fake_platform"]

    def is_configured(self) -> bool:
        return True

    def validate(self) -> dict:
        return {"configured": True}

    def upload(self, payload: DistributionPayload) -> DistributionResult:
        return DistributionResult(channel=self.name, platform="fake_platform", success=True)


class TestDistributionModels(unittest.TestCase):
    def test_payload_defaults(self):
        p = DistributionPayload(video_path="/tmp/x.mp4", title="t")
        self.assertEqual(p.description, "")
        self.assertEqual(p.tags, [])
        self.assertTrue(p.include_disclosure)

    def test_result_defaults(self):
        r = DistributionResult(channel="c", success=True)
        self.assertIsNone(r.platform)
        self.assertEqual(r.details, {})


class TestYouTubeAdapter(unittest.TestCase):
    def test_validate_reports_missing_credentials(self):
        with patch.dict(config.app, {"youtube_client_id": "", "youtube_client_secret": ""}):
            adapter = YouTubeAdapter({})
            result = adapter.validate()
            self.assertFalse(result["configured"])
            self.assertIn("youtube_client_id", result["error"])

    def test_is_configured_false_without_token(self):
        with patch("app.services.youtube.is_authorised", return_value=False):
            adapter = YouTubeAdapter({})
            self.assertFalse(adapter.is_configured())


class TestUploadPostAdapter(unittest.TestCase):
    def test_validate_disabled(self):
        adapter = UploadPostAdapter({"enabled": False})
        result = adapter.validate()
        self.assertFalse(result["configured"])
        self.assertIn("disabled", result["error"])

    def test_validate_missing_credentials(self):
        with patch.dict(config.app, {"upload_post_api_key": "", "upload_post_username": ""}):
            adapter = UploadPostAdapter({"enabled": True})
            result = adapter.validate()
            self.assertFalse(result["configured"])
            self.assertIn("api_key", result["error"].lower())


class TestRegistryAndValidation(unittest.TestCase):
    def test_default_channels_list(self):
        self.assertIn("youtube", DEFAULT_CHANNELS)
        self.assertIn("upload_post", DEFAULT_CHANNELS)

    def test_validate_all_returns_every_channel(self):
        with patch("app.services.youtube.is_authorised", return_value=False):
            results = validate_all()
        for name in ADAPTER_REGISTRY:
            self.assertIn(name, results)
        self.assertFalse(results["youtube"]["validation"]["configured"])

    def test_validate_unknown_channel(self):
        result = validate_channel("not_a_channel")
        self.assertFalse(result["configured"])
        self.assertIn("Unknown", result["error"])

    def test_add_channel_registers_adapter(self):
        add_channel("fake_for_test", _FakeAdapter)
        self.assertIn("fake_for_test", ADAPTER_REGISTRY)


class TestDistribute(unittest.TestCase):
    def setUp(self):
        self.original_cfg = config._cfg.get("distribution", {})

    def tearDown(self):
        config._cfg["distribution"] = self.original_cfg

    def test_distribute_respects_enabled_channels(self):
        add_channel("fake_for_test", _FakeAdapter)
        config._cfg["distribution"] = {
            "enabled": True,
            "enabled_channels": ["fake_for_test"],
            "channels": {"fake_for_test": {}},
        }
        payload = DistributionPayload(video_path="/tmp/x.mp4", title="t")
        with patch.object(_FakeAdapter, "upload", return_value=DistributionResult(
            channel="fake_for_test", platform="fake_platform", success=True
        )):
            results = distribute(payload)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].success)

    def test_distribute_disabled_globally(self):
        add_channel("fake_for_test", _FakeAdapter)
        config._cfg["distribution"] = {"enabled": False, "enabled_channels": ["fake_for_test"]}
        payload = DistributionPayload(video_path="/tmp/x.mp4", title="t")
        results = distribute(payload)
        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
