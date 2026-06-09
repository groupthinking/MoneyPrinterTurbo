"""Unit tests for app/services/youtube.py"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.services.youtube import (
    _LOOPBACK_REDIRECT,
    _truncate_tags,
    _write_token_secure,
)


class TestTruncateTags(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(_truncate_tags([]), [])

    def test_single_tag_fits(self):
        self.assertEqual(_truncate_tags(["hello"]), ["hello"])

    def test_single_tag_too_long(self):
        self.assertEqual(_truncate_tags(["a" * 501]), [])

    def test_single_tag_exactly_limit(self):
        tag = "a" * 500
        self.assertEqual(_truncate_tags([tag]), [tag])

    def test_comma_separator_counted(self):
        # 250 + 1 + 250 = 501 > 500 — second tag must be dropped
        tag = "a" * 250
        self.assertEqual(_truncate_tags([tag, tag]), [tag])

    def test_comma_separator_fits_exactly(self):
        # 249 + 1 + 250 = 500 — both fit
        tag1, tag2 = "a" * 249, "b" * 250
        self.assertEqual(_truncate_tags([tag1, tag2]), [tag1, tag2])

    def test_multiple_tags_truncated(self):
        tags = ["a" * 100] * 5
        # 4 tags: 100+1+100+1+100+1+100 = 403 ✓; 5th adds 101 → 504 ✗
        self.assertEqual(_truncate_tags(tags), tags[:4])

    def test_custom_max_chars(self):
        # "abc" = 3, "abc,def" = 7 ✓, "abc,def,ghi" = 11 ✗
        self.assertEqual(_truncate_tags(["abc", "def", "ghi"], max_chars=7), ["abc", "def"])

    def test_first_tag_no_separator(self):
        # max_chars=5: "hello"=5 fits, second needs +6 → dropped
        self.assertEqual(_truncate_tags(["hello", "world"], max_chars=5), ["hello"])


class TestWriteTokenSecure(unittest.TestCase):
    def test_writes_and_chmods(self):
        fake_path = MagicMock(spec=Path)
        fake_path.parent = MagicMock()
        with patch("app.services.youtube._TOKEN_PATH", fake_path), \
             patch("os.chmod") as mock_chmod:
            _write_token_secure('{"token": "x"}')
        fake_path.write_text.assert_called_once_with('{"token": "x"}')
        mock_chmod.assert_called_once()

    def test_chmod_oserror_suppressed(self):
        fake_path = MagicMock(spec=Path)
        fake_path.parent = MagicMock()
        with patch("app.services.youtube._TOKEN_PATH", fake_path), \
             patch("os.chmod", side_effect=OSError("no chmod")):
            _write_token_secure("{}")  # must not raise


def _mock_google_modules(creds_obj):
    """Return a sys.modules patch dict that stubs google auth modules."""
    mock_creds_module = MagicMock()
    mock_creds_module.Credentials.from_authorized_user_file.return_value = creds_obj
    return {
        "google": MagicMock(),
        "google.auth": MagicMock(),
        "google.auth.transport": MagicMock(),
        "google.auth.transport.requests": MagicMock(),
        "google.oauth2": MagicMock(),
        "google.oauth2.credentials": mock_creds_module,
    }


class TestIsAuthorised(unittest.TestCase):
    def test_no_token_file_returns_false(self):
        from app.services.youtube import is_authorised
        with patch("app.services.youtube._TOKEN_PATH") as mock_path:
            mock_path.exists.return_value = False
            self.assertFalse(is_authorised())

    def test_valid_creds_returns_true(self):
        from app.services.youtube import is_authorised
        mock_creds = MagicMock()
        mock_creds.valid = True
        mock_creds.refresh_token = None
        with patch("app.services.youtube._TOKEN_PATH") as mock_path, \
             patch.dict("sys.modules", _mock_google_modules(mock_creds)):
            mock_path.exists.return_value = True
            result = is_authorised()
        self.assertTrue(result)

    def test_expired_but_refreshable_returns_true(self):
        from app.services.youtube import is_authorised
        mock_creds = MagicMock()
        mock_creds.valid = False
        mock_creds.refresh_token = "refresh_tok"
        with patch("app.services.youtube._TOKEN_PATH") as mock_path, \
             patch.dict("sys.modules", _mock_google_modules(mock_creds)):
            mock_path.exists.return_value = True
            result = is_authorised()
        self.assertTrue(result)

    def test_expired_no_refresh_token_returns_false(self):
        from app.services.youtube import is_authorised
        mock_creds = MagicMock()
        mock_creds.valid = False
        mock_creds.refresh_token = None
        with patch("app.services.youtube._TOKEN_PATH") as mock_path, \
             patch.dict("sys.modules", _mock_google_modules(mock_creds)):
            mock_path.exists.return_value = True
            result = is_authorised()
        self.assertFalse(result)

    def test_exception_returns_false(self):
        from app.services.youtube import is_authorised
        mock_creds_module = MagicMock()
        mock_creds_module.Credentials.from_authorized_user_file.side_effect = ValueError("bad file")
        bad_modules = {
            "google": MagicMock(), "google.auth": MagicMock(),
            "google.auth.transport": MagicMock(), "google.auth.transport.requests": MagicMock(),
            "google.oauth2": MagicMock(), "google.oauth2.credentials": mock_creds_module,
        }
        with patch("app.services.youtube._TOKEN_PATH") as mock_path, \
             patch.dict("sys.modules", bad_modules):
            mock_path.exists.return_value = True
            result = is_authorised()
        self.assertFalse(result)


def _mock_oauthlib_modules(flow_obj=None):
    """Return sys.modules patch dict that stubs google_auth_oauthlib."""
    mock_flow_module = MagicMock()
    if flow_obj is not None:
        mock_flow_module.Flow.from_client_config.return_value = flow_obj
    return {
        "google_auth_oauthlib": MagicMock(),
        "google_auth_oauthlib.flow": mock_flow_module,
    }


class TestGetAuthUrl(unittest.TestCase):
    def _make_mock_config(self, has_creds=True):
        mock_cfg = MagicMock()
        if has_creds:
            mock_cfg.app = {"youtube_client_id": "cid", "youtube_client_secret": "csec"}
        else:
            mock_cfg.app = {}
        return mock_cfg

    def test_missing_credentials_raises(self):
        from app.services.youtube import get_auth_url
        with patch("app.services.youtube.config", self._make_mock_config(has_creds=False)), \
             patch.dict("sys.modules", _mock_oauthlib_modules()):
            with self.assertRaises(RuntimeError):
                get_auth_url()

    def test_returns_url_from_flow(self):
        from app.services.youtube import get_auth_url
        mock_flow = MagicMock()
        mock_flow.authorization_url.return_value = ("https://accounts.google.com/auth", "state")
        with patch("app.services.youtube.config", self._make_mock_config()), \
             patch.dict("sys.modules", _mock_oauthlib_modules(mock_flow)):
            url = get_auth_url()
        self.assertEqual(url, "https://accounts.google.com/auth")
        self.assertEqual(mock_flow.redirect_uri, _LOOPBACK_REDIRECT)


class TestExchangeCode(unittest.TestCase):
    def _make_mock_config(self):
        mock_cfg = MagicMock()
        mock_cfg.app = {"youtube_client_id": "cid", "youtube_client_secret": "csec"}
        return mock_cfg

    def test_saves_token_to_disk(self):
        from app.services.youtube import exchange_code
        mock_flow = MagicMock()
        mock_flow.credentials.to_json.return_value = '{"token": "abc"}'
        fake_path = MagicMock(spec=Path)
        fake_path.parent = MagicMock()
        with patch("app.services.youtube.config", self._make_mock_config()), \
             patch.dict("sys.modules", _mock_oauthlib_modules(mock_flow)), \
             patch("app.services.youtube._TOKEN_PATH", fake_path), \
             patch("os.chmod"):
            exchange_code("auth-code-123")
        mock_flow.fetch_token.assert_called_once_with(code="auth-code-123")
        fake_path.write_text.assert_called_once_with('{"token": "abc"}')

    def test_sets_loopback_redirect(self):
        from app.services.youtube import exchange_code
        mock_flow = MagicMock()
        mock_flow.credentials.to_json.return_value = "{}"
        fake_path = MagicMock(spec=Path)
        fake_path.parent = MagicMock()
        with patch("app.services.youtube.config", self._make_mock_config()), \
             patch.dict("sys.modules", _mock_oauthlib_modules(mock_flow)), \
             patch("app.services.youtube._TOKEN_PATH", fake_path), \
             patch("os.chmod"):
            exchange_code("code")
        self.assertEqual(mock_flow.redirect_uri, _LOOPBACK_REDIRECT)


class TestUploadVideo(unittest.TestCase):
    def _make_mock_service(self, video_id="abc123"):
        mock_request = MagicMock()
        mock_request.next_chunk.return_value = (None, {"id": video_id})
        mock_service = MagicMock()
        mock_service.videos.return_value.insert.return_value = mock_request
        return mock_service

    def _mock_media(self):
        mock_media_module = MagicMock()
        mock_media_module.MediaFileUpload.return_value = MagicMock()
        return mock_media_module

    def test_file_not_found_raises(self):
        from app.services.youtube import upload_video
        with self.assertRaises(FileNotFoundError):
            upload_video("/nonexistent/path/video.mp4", title="Test")

    def test_successful_upload_returns_dict(self):
        from app.services.youtube import upload_video
        mock_service = self._make_mock_service("vid999")
        mock_http_module = MagicMock()
        mock_http_module.MediaFileUpload.return_value = MagicMock()
        with patch("os.path.exists", return_value=True), \
             patch("app.services.youtube._build_service", return_value=mock_service), \
             patch("app.config.config") as mock_cfg, \
             patch.dict("sys.modules", {"googleapiclient.http": mock_http_module}):
            mock_cfg.app = {}
            result = upload_video("/fake/video.mp4", title="My Video")
        self.assertEqual(result["video_id"], "vid999")
        self.assertIn("url", result)
        self.assertIn("privacy", result)

    def test_title_truncated_to_100(self):
        from app.services.youtube import upload_video
        captured_body = {}

        def fake_insert(part, body, media_body):
            captured_body.update(body)
            mock_req = MagicMock()
            mock_req.next_chunk.return_value = (None, {"id": "x"})
            return mock_req

        mock_service = MagicMock()
        mock_service.videos.return_value.insert.side_effect = fake_insert
        mock_http_module = MagicMock()
        mock_http_module.MediaFileUpload.return_value = MagicMock()
        with patch("os.path.exists", return_value=True), \
             patch("app.services.youtube._build_service", return_value=mock_service), \
             patch("app.config.config") as mock_cfg, \
             patch.dict("sys.modules", {"googleapiclient.http": mock_http_module}):
            mock_cfg.app = {}
            upload_video("/fake/video.mp4", title="T" * 200)
        self.assertEqual(len(captured_body["snippet"]["title"]), 100)

    def test_description_truncated_to_5000(self):
        from app.services.youtube import upload_video
        captured_body = {}

        def fake_insert(part, body, media_body):
            captured_body.update(body)
            mock_req = MagicMock()
            mock_req.next_chunk.return_value = (None, {"id": "x"})
            return mock_req

        mock_service = MagicMock()
        mock_service.videos.return_value.insert.side_effect = fake_insert
        mock_http_module = MagicMock()
        mock_http_module.MediaFileUpload.return_value = MagicMock()
        with patch("os.path.exists", return_value=True), \
             patch("app.services.youtube._build_service", return_value=mock_service), \
             patch("app.config.config") as mock_cfg, \
             patch.dict("sys.modules", {"googleapiclient.http": mock_http_module}):
            mock_cfg.app = {}
            upload_video("/fake/video.mp4", title="T", description="D" * 6000)
        self.assertEqual(len(captured_body["snippet"]["description"]), 5000)

    def test_retries_on_transient_5xx(self):
        from app.services.youtube import upload_video
        from googleapiclient.errors import HttpError
        mock_resp = MagicMock()
        mock_resp.status = 503
        http_error = HttpError(resp=mock_resp, content=b"server error")

        call_count = {"n": 0}

        def flaky_next_chunk():
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise http_error
            return (None, {"id": "retried"})

        mock_request = MagicMock()
        mock_request.next_chunk.side_effect = flaky_next_chunk
        mock_service = MagicMock()
        mock_service.videos.return_value.insert.return_value = mock_request
        mock_http_module = MagicMock()
        mock_http_module.MediaFileUpload.return_value = MagicMock()
        with patch("os.path.exists", return_value=True), \
             patch("app.services.youtube._build_service", return_value=mock_service), \
             patch("app.config.config") as mock_cfg, \
             patch.dict("sys.modules", {"googleapiclient.http": mock_http_module}), \
             patch("time.sleep"):
            mock_cfg.app = {}
            result = upload_video("/fake/video.mp4", title="Test")
        self.assertEqual(result["video_id"], "retried")

    def test_raises_after_max_retries(self):
        from app.services.youtube import upload_video
        from googleapiclient.errors import HttpError
        mock_resp = MagicMock()
        mock_resp.status = 503
        http_error = HttpError(resp=mock_resp, content=b"persistent error")

        mock_request = MagicMock()
        mock_request.next_chunk.side_effect = http_error
        mock_service = MagicMock()
        mock_service.videos.return_value.insert.return_value = mock_request
        mock_http_module = MagicMock()
        mock_http_module.MediaFileUpload.return_value = MagicMock()
        with patch("os.path.exists", return_value=True), \
             patch("app.services.youtube._build_service", return_value=mock_service), \
             patch("app.config.config") as mock_cfg, \
             patch.dict("sys.modules", {"googleapiclient.http": mock_http_module}), \
             patch("time.sleep"):
            mock_cfg.app = {}
            with self.assertRaises(HttpError):
                upload_video("/fake/video.mp4", title="Test")

    def test_non_retryable_http_error_no_sleep(self):
        from app.services.youtube import upload_video
        from googleapiclient.errors import HttpError
        mock_resp = MagicMock()
        mock_resp.status = 403
        http_error = HttpError(resp=mock_resp, content=b"forbidden")

        mock_request = MagicMock()
        mock_request.next_chunk.side_effect = http_error
        mock_service = MagicMock()
        mock_service.videos.return_value.insert.return_value = mock_request
        mock_http_module = MagicMock()
        mock_http_module.MediaFileUpload.return_value = MagicMock()
        with patch("os.path.exists", return_value=True), \
             patch("app.services.youtube._build_service", return_value=mock_service), \
             patch("app.config.config") as mock_cfg, \
             patch.dict("sys.modules", {"googleapiclient.http": mock_http_module}), \
             patch("time.sleep") as mock_sleep:
            mock_cfg.app = {}
            with self.assertRaises(HttpError):
                upload_video("/fake/video.mp4", title="Test")
        mock_sleep.assert_not_called()

    def test_uses_config_default_privacy(self):
        from app.services.youtube import upload_video
        captured_body = {}

        def fake_insert(part, body, media_body):
            captured_body.update(body)
            mock_req = MagicMock()
            mock_req.next_chunk.return_value = (None, {"id": "x"})
            return mock_req

        mock_service = MagicMock()
        mock_service.videos.return_value.insert.side_effect = fake_insert
        mock_http_module = MagicMock()
        mock_http_module.MediaFileUpload.return_value = MagicMock()

        mock_config = MagicMock()
        mock_config.app.get.side_effect = lambda k, d=None: (
            "private" if k == "youtube_default_privacy" else d
        )

        with patch("os.path.exists", return_value=True), \
             patch("app.services.youtube._build_service", return_value=mock_service), \
             patch("app.services.youtube.config", mock_config), \
             patch.dict("sys.modules", {"googleapiclient.http": mock_http_module}):
            result = upload_video("/fake/video.mp4", title="T")
        self.assertEqual(result["privacy"], "private")
        self.assertEqual(captured_body["status"]["privacyStatus"], "private")


if __name__ == "__main__":
    unittest.main()
