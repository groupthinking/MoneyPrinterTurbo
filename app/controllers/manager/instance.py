"""Shared task manager singleton — import this instead of re-creating in each module."""
from app.config import config
from app.controllers.manager.memory_manager import InMemoryTaskManager
from app.controllers.manager.redis_manager import RedisTaskManager

_enable_redis = config.app.get("enable_redis", False)
_max_concurrent = config.app.get("max_concurrent_tasks", 5)
_max_queued = config.app.get("max_queued_tasks", 100)

if _enable_redis:
    _redis_host = config.app.get("redis_host", "localhost")
    _redis_port = config.app.get("redis_port", 6379)
    _redis_db = config.app.get("redis_db", 0)
    _redis_password = config.app.get("redis_password", None)
    if _redis_password:
        _redis_url = f"redis://:{_redis_password}@{_redis_host}:{_redis_port}/{_redis_db}"
    else:
        _redis_url = f"redis://{_redis_host}:{_redis_port}/{_redis_db}"
    task_manager = RedisTaskManager(
        max_concurrent_tasks=_max_concurrent,
        redis_url=_redis_url,
        max_queued_tasks=_max_queued,
    )
else:
    task_manager = InMemoryTaskManager(
        max_concurrent_tasks=_max_concurrent,
        max_queued_tasks=_max_queued,
    )
