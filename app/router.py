"""Application configuration - root APIRouter.

Defines all FastAPI application endpoints.

Resources:
    1. https://fastapi.tiangolo.com/tutorial/bigger-applications

"""

from fastapi import APIRouter

from app.controllers.v1 import batch, billing, llm, video

root_api_router = APIRouter()
# v1
root_api_router.include_router(video.router)
root_api_router.include_router(llm.router)
root_api_router.include_router(billing.router)
root_api_router.include_router(batch.router)
