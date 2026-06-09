"""YouTube auth and upload endpoints."""
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from loguru import logger
from pydantic import BaseModel

from app.controllers import base
from app.services import youtube as yt_svc
from app.utils import utils

router = APIRouter(
    prefix="/api/v1/youtube",
    tags=["youtube"],
    dependencies=[Depends(base.verify_token)],
)


@router.get("/status")
def yt_status():
    """Check whether YouTube is authorised (token file exists)."""
    return {"authorised": yt_svc.is_authorised()}


@router.get("/auth")
def yt_auth_url():
    """Return the OAuth URL. User visits it, approves, then calls /auth/code."""
    try:
        url = yt_svc.get_auth_url()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"auth_url": url}


class CodeRequest(BaseModel):
    """Request body for POST /youtube/auth/code."""

    code: str


@router.post("/auth/code")
def yt_auth_code(req: CodeRequest):
    """Exchange an OAuth authorisation code for a persistent token."""
    try:
        yt_svc.exchange_code(req.code)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid authorization code")
    except RuntimeError:
        raise HTTPException(status_code=503, detail="YouTube auth service unavailable")
    except Exception:
        logger.exception("Unexpected YouTube auth/code exchange failure")
        raise HTTPException(status_code=500, detail="YouTube authorization failed")
    return {"status": "authorised"}


class UploadRequest(BaseModel):
    """Request body for POST /youtube/upload."""

    task_id: str
    filename: str
    title: str
    description: str = ""
    tags: list[str] = []
    privacy_status: str | None = None


@router.post("/upload")
def yt_upload(req: UploadRequest):
    """Upload a completed task's video to YouTube. Returns {video_id, url}."""
    if not yt_svc.is_authorised():
        raise HTTPException(
            status_code=503,
            detail="YouTube not authorised. Call GET /api/v1/youtube/auth then POST /api/v1/youtube/auth/code",
        )
    # Prevent path traversal: filename must be a bare name with no directory components
    if os.path.basename(req.filename) != req.filename or not req.filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    video_path = os.path.join(utils.task_dir(req.task_id), req.filename)
    try:
        result = yt_svc.upload_video(
            video_path=video_path,
            title=req.title,
            description=req.description,
            tags=req.tags,
            privacy_status=req.privacy_status,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return result
