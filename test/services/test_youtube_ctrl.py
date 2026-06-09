"""Unit tests for app/controllers/v1/youtube_ctrl.py"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from fastapi.testclient import TestClient


class TestYtStatus(unittest.TestCase):
    def test_not_authorised(self):
        from fastapi import FastAPI
        from app.controllers.v1 import youtube_ctrl

        app = FastAPI()

        # Remove auth dependency for testing
        from fastapi import APIRouter
        router = APIRouter(prefix="/api/v1/youtube", tags=["youtube"])

        @router.get("/status")
        def yt_status():
            return youtube_ctrl.yt_status()

        app.include_router(router)
        client = TestClient(app)

        with patch("app.services.youtube.is_authorised", return_value=False):
            resp = client.get("/api/v1/youtube/status")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["authorised"])

    def test_authorised(self):
        from fastapi import FastAPI
        from app.controllers.v1 import youtube_ctrl

        app = FastAPI()
        from fastapi import APIRouter
        router = APIRouter(prefix="/api/v1/youtube", tags=["youtube"])

        @router.get("/status")
        def yt_status():
            return youtube_ctrl.yt_status()

        app.include_router(router)
        client = TestClient(app)

        with patch("app.services.youtube.is_authorised", return_value=True):
            resp = client.get("/api/v1/youtube/status")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["authorised"])


class TestYtAuthUrl(unittest.TestCase):
    def _make_app(self):
        from fastapi import FastAPI
        from app.controllers.v1 import youtube_ctrl
        from fastapi import APIRouter

        app = FastAPI()
        router = APIRouter(prefix="/api/v1/youtube", tags=["youtube"])

        @router.get("/auth")
        def yt_auth_url():
            return youtube_ctrl.yt_auth_url()

        app.include_router(router)
        return TestClient(app)

    def test_returns_auth_url(self):
        client = self._make_app()
        with patch("app.services.youtube.get_auth_url", return_value="https://auth.example.com/"):
            resp = client.get("/api/v1/youtube/auth")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["auth_url"], "https://auth.example.com/")

    def test_service_error_returns_503(self):
        client = self._make_app()
        with patch("app.services.youtube.get_auth_url", side_effect=RuntimeError("no config")):
            resp = client.get("/api/v1/youtube/auth")
        self.assertEqual(resp.status_code, 503)
        self.assertIn("no config", resp.json()["detail"])


class TestYtAuthCode(unittest.TestCase):
    def _make_app(self):
        from fastapi import FastAPI
        from app.controllers.v1 import youtube_ctrl
        from fastapi import APIRouter

        app = FastAPI()
        router = APIRouter(prefix="/api/v1/youtube", tags=["youtube"])

        @router.post("/auth/code")
        def yt_auth_code(req: youtube_ctrl.CodeRequest):
            return youtube_ctrl.yt_auth_code(req)

        app.include_router(router)
        return TestClient(app)

    def test_valid_code_returns_authorised(self):
        client = self._make_app()
        with patch("app.services.youtube.exchange_code"):
            resp = client.post("/api/v1/youtube/auth/code", json={"code": "valid_code"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "authorised")

    def test_value_error_returns_400(self):
        client = self._make_app()
        with patch("app.services.youtube.exchange_code", side_effect=ValueError("bad code")):
            resp = client.post("/api/v1/youtube/auth/code", json={"code": "bad"})
        self.assertEqual(resp.status_code, 400)

    def test_runtime_error_returns_503(self):
        client = self._make_app()
        with patch("app.services.youtube.exchange_code", side_effect=RuntimeError("flow failed")):
            resp = client.post("/api/v1/youtube/auth/code", json={"code": "bad"})
        self.assertEqual(resp.status_code, 503)

    def test_unexpected_exception_returns_500(self):
        client = self._make_app()
        with patch("app.services.youtube.exchange_code", side_effect=MemoryError("oom")):
            resp = client.post("/api/v1/youtube/auth/code", json={"code": "bad"})
        self.assertEqual(resp.status_code, 500)


class TestYtUpload(unittest.TestCase):
    def _make_app(self):
        from fastapi import FastAPI
        from app.controllers.v1 import youtube_ctrl
        from fastapi import APIRouter

        app = FastAPI()
        router = APIRouter(prefix="/api/v1/youtube", tags=["youtube"])

        @router.post("/upload")
        def yt_upload(req: youtube_ctrl.UploadRequest):
            return youtube_ctrl.yt_upload(req)

        app.include_router(router)
        return TestClient(app)

    def _valid_payload(self, **kwargs):
        base = {
            "task_id": "task-123",
            "filename": "video.mp4",
            "title": "My Video",
            "description": "A description",
            "tags": ["tag1", "tag2"],
            "privacy_status": "public",
        }
        base.update(kwargs)
        return base

    def test_not_authorised_returns_503(self):
        client = self._make_app()
        with patch("app.services.youtube.is_authorised", return_value=False):
            resp = client.post("/api/v1/youtube/upload", json=self._valid_payload())
        self.assertEqual(resp.status_code, 503)

    def test_path_traversal_rejected(self):
        client = self._make_app()
        with patch("app.services.youtube.is_authorised", return_value=True):
            resp = client.post("/api/v1/youtube/upload",
                               json=self._valid_payload(filename="../etc/passwd"))
        self.assertEqual(resp.status_code, 400)

    def test_empty_filename_rejected(self):
        client = self._make_app()
        with patch("app.services.youtube.is_authorised", return_value=True):
            resp = client.post("/api/v1/youtube/upload",
                               json=self._valid_payload(filename=""))
        self.assertEqual(resp.status_code, 400)

    def test_subdirectory_filename_rejected(self):
        client = self._make_app()
        with patch("app.services.youtube.is_authorised", return_value=True):
            resp = client.post("/api/v1/youtube/upload",
                               json=self._valid_payload(filename="subdir/video.mp4"))
        self.assertEqual(resp.status_code, 400)

    def test_dot_filename_rejected(self):
        client = self._make_app()
        with patch("app.services.youtube.is_authorised", return_value=True):
            resp = client.post("/api/v1/youtube/upload",
                               json=self._valid_payload(filename="."))
        self.assertEqual(resp.status_code, 400)

    def test_dotdot_filename_rejected(self):
        client = self._make_app()
        with patch("app.services.youtube.is_authorised", return_value=True):
            resp = client.post("/api/v1/youtube/upload",
                               json=self._valid_payload(filename=".."))
        self.assertEqual(resp.status_code, 400)

    def test_task_id_traversal_rejected(self):
        client = self._make_app()
        with patch("app.services.youtube.is_authorised", return_value=True):
            resp = client.post("/api/v1/youtube/upload",
                               json=self._valid_payload(task_id="../storage"))
        self.assertEqual(resp.status_code, 400)

    def test_invalid_privacy_status_rejected(self):
        client = self._make_app()
        with patch("app.services.youtube.is_authorised", return_value=True):
            resp = client.post("/api/v1/youtube/upload",
                               json=self._valid_payload(privacy_status="secret"))
        self.assertEqual(resp.status_code, 422)

    def test_file_not_found_returns_404(self):
        client = self._make_app()
        with patch("app.services.youtube.is_authorised", return_value=True), \
             patch("app.services.youtube.upload_video",
                   side_effect=FileNotFoundError("no file")), \
             patch("app.utils.utils.task_dir", return_value="/fake/task"):
            resp = client.post("/api/v1/youtube/upload", json=self._valid_payload())
        self.assertEqual(resp.status_code, 404)

    def test_runtime_error_returns_503(self):
        client = self._make_app()
        with patch("app.services.youtube.is_authorised", return_value=True), \
             patch("app.services.youtube.upload_video",
                   side_effect=RuntimeError("API error")), \
             patch("app.utils.utils.task_dir", return_value="/fake/task"):
            resp = client.post("/api/v1/youtube/upload", json=self._valid_payload())
        self.assertEqual(resp.status_code, 503)

    def test_successful_upload_returns_video_info(self):
        client = self._make_app()
        mock_result = {"video_id": "abc123", "url": "https://youtube.com/watch?v=abc123", "privacy": "public"}
        with patch("app.services.youtube.is_authorised", return_value=True), \
             patch("app.services.youtube.upload_video", return_value=mock_result), \
             patch("app.utils.utils.task_dir", return_value="/fake/task"):
            resp = client.post("/api/v1/youtube/upload", json=self._valid_payload())
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["video_id"], "abc123")
        self.assertIn("youtube.com", data["url"])


if __name__ == "__main__":
    unittest.main()
