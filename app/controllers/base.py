from uuid import uuid4

from fastapi import Request

from app.config import config
from app.models.exception import HttpException


def get_task_id(request: Request):
    task_id = request.headers.get("x-task-id")
    if not task_id:
        task_id = uuid4()
    return str(task_id)


def get_api_key(request: Request):
    api_key = request.headers.get("x-api-key")
    return api_key


def verify_token(request: Request):
    token = get_api_key(request)

    # Billing DB keys are always valid regardless of other config
    if token:
        try:
            from app.services.billing import get_key_info
            if get_key_info(token):
                return
        except Exception:
            pass

    configured_key = config.app.get("api_key", "")
    if not configured_key:
        # auth disabled — open access (default, backward-compatible)
        return
    if token == configured_key:
        return
    # also accept per-key quotas table
    quotas = config._cfg.get("api_key_quotas", {})
    if quotas and token in quotas:
        return

    request_id = get_task_id(request)
    raise HttpException(
        task_id=request_id,
        status_code=401,
        message="invalid or missing API key",
    )
