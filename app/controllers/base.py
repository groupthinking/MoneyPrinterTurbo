from uuid import uuid4

from fastapi import Request
from loguru import logger

from app.config import config
from app.models.exception import HttpException


def get_task_id(request: Request):
    """Return the X-Task-Id header value, or a fresh UUID if absent."""
    task_id = request.headers.get("x-task-id")
    if not task_id:
        task_id = uuid4()
    return str(task_id)


def get_api_key(request: Request):
    """Return the X-Api-Key header value, or None if not present."""
    api_key = request.headers.get("x-api-key")
    return api_key


def verify_token(request: Request):
    """FastAPI dependency that enforces API-key authentication.

    Passes when: the key is a valid billing-DB key, the global api_key
    matches, the key appears in api_key_quotas, or auth is disabled
    (no api_key and no quotas configured).  Raises HTTP 401 otherwise.
    """
    token = get_api_key(request)

    # Billing DB keys are always valid regardless of other config
    if token:
        try:
            from app.services.billing import get_key_info
            if get_key_info(token):
                return
        except Exception as exc:
            logger.warning(f"billing key lookup failed: {exc}")

    configured_key = config.app.get("api_key", "")
    quotas = config._cfg.get("api_key_quotas", {})
    if not configured_key and not quotas:
        # auth disabled — open access (default, backward-compatible)
        return
    if token == configured_key:
        return
    if quotas and token in quotas:
        return

    request_id = get_task_id(request)
    raise HttpException(
        task_id=request_id,
        status_code=401,
        message="invalid or missing API key",
    )
