"""Per-API-key usage tracking and daily quota enforcement."""
import threading
from datetime import date

from loguru import logger

from app.config import config


class UsageTracker:
    def __init__(self):
        self._lock = threading.Lock()
        # {api_key: {date_str: count}}
        self._counters: dict[str, dict[str, int]] = {}

    def _get_quota(self, api_key: str) -> int:
        """Return daily video quota for a key. -1 = unlimited, 0 = blocked/unknown."""
        quotas = config.app.get("api_key_quotas", {})
        if not quotas:
            return -1
        return int(quotas.get(api_key, 0))

    def check_and_increment(self, api_key: str) -> tuple[bool, str]:
        """
        Check quota and increment counter atomically.
        Returns (allowed, reason). If allowed=False, reason explains why.
        """
        quota = self._get_quota(api_key)
        if quota == 0:
            return False, "API key not authorised or quota not configured"
        if quota == -1:
            self._increment(api_key)
            return True, ""

        today = str(date.today())
        with self._lock:
            key_counters = self._counters.setdefault(api_key, {})
            # prune stale dates to keep memory bounded
            for old_date in list(key_counters.keys()):
                if old_date != today:
                    del key_counters[old_date]
            current = key_counters.get(today, 0)
            if current >= quota:
                logger.warning(f"quota exceeded for key …{api_key[-6:]}: {current}/{quota}")
                return False, f"daily quota of {quota} videos exceeded"
            key_counters[today] = current + 1
            return True, ""

    def _increment(self, api_key: str):
        today = str(date.today())
        with self._lock:
            key_counters = self._counters.setdefault(api_key, {})
            key_counters[today] = key_counters.get(today, 0) + 1

    def get_usage(self, api_key: str) -> int:
        today = str(date.today())
        with self._lock:
            return self._counters.get(api_key, {}).get(today, 0)

    def get_all_usage(self) -> dict[str, int]:
        today = str(date.today())
        with self._lock:
            return {k: v.get(today, 0) for k, v in self._counters.items()}


usage_tracker = UsageTracker()
