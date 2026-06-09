"""YouTube auth and upload endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.controllers import base
from app.services import youtube as yt_svc

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
    code: str


@router.post("/auth/code")
def yt_auth_code(req: CodeRequest):
    """Exchange an OAuth authorisation code for a persistent token."""
    try:
        yt_svc.exchange_code(req.code)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "authorised"}


class UploadRequest(BaseModel):
    video_path: str
    title: str
    description: str = ""
    tags: list[str] = []
    privacy_status: str = "public"


@router.post("/upload")
def yt_upload(req: UploadRequest):
    """Upload a local video file to YouTube. Returns {video_id, url}."""
    if not yt_svc.is_authorised():
        raise HTTPException(
            status_code=503,
            detail="YouTube not authorised. Call GET /api/v1/youtube/auth then POST /api/v1/youtube/auth/code",
        )
    try:
        result = yt_svc.upload_video(
            video_path=req.video_path,
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
