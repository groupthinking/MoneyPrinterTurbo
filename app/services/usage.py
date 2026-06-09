"""Per-API-key usage tracking and daily quota enforcement.

Counters are persisted to the billing SQLite DB so they survive restarts
and memory doesn't grow unboundedly. For true multi-process deployments
(multiple Uvicorn workers) use Redis — single-worker is the common case.
"""
import contextlib
import sqlite3
import threading
from datetime import date
from pathlib import Path

from loguru import logger

from app.config import config

_DB_PATH = Path("storage/billing.db")
_lock = threading.Lock()


@contextlib.contextmanager
def _db():
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _ensure_table():
    with _db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS usage_counters (
                api_key   TEXT NOT NULL,
                day       TEXT NOT NULL,
                count     INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (api_key, day)
            )
        """)
        conn.commit()


_ensure_table()


class UsageTracker:
    def _quotas(self) -> dict:
        # [api_key_quotas] is a top-level TOML section, not nested under [app]
        return config._cfg.get("api_key_quotas", {}) or {}

    def _get_quota(self, api_key: str) -> int:
        """Return daily video quota for a key. -1 = unlimited, 0 = blocked/unknown."""
        try:
            from app.services.billing import get_key_info
            info = get_key_info(api_key)
            if info is not None:
                return info["daily_quota"]
        except Exception:
            pass
        quotas = self._quotas()
        if not quotas:
            return -1
        return int(quotas.get(api_key, 0))

    def check_and_increment(self, api_key: str) -> tuple[bool, str]:
        """Check quota and increment counter atomically."""
        quota = self._get_quota(api_key)
        if quota == 0:
            return False, "API key not authorised or quota not configured"
        if quota == -1:
            self._persist_increment(api_key)
            return True, ""

        today = str(date.today())
        with _lock, _db() as conn:
            row = conn.execute(
                "SELECT count FROM usage_counters WHERE api_key = ? AND day = ?",
                (api_key, today),
            ).fetchone()
            current = row["count"] if row else 0
            if current >= quota:
                logger.warning(f"quota exceeded for key …{api_key[-6:]}: {current}/{quota}")
                return False, f"daily quota of {quota} videos exceeded"
            conn.execute(
                """INSERT INTO usage_counters (api_key, day, count) VALUES (?, ?, 1)
                   ON CONFLICT(api_key, day) DO UPDATE SET count = count + 1""",
                (api_key, today),
            )
            conn.commit()
        return True, ""

    def _persist_increment(self, api_key: str):
        today = str(date.today())
        with _lock, _db() as conn:
            conn.execute(
                """INSERT INTO usage_counters (api_key, day, count) VALUES (?, ?, 1)
                   ON CONFLICT(api_key, day) DO UPDATE SET count = count + 1""",
                (api_key, today),
            )
            conn.commit()

    def get_usage(self, api_key: str) -> int:
        today = str(date.today())
        with _db() as conn:
            row = conn.execute(
                "SELECT count FROM usage_counters WHERE api_key = ? AND day = ?",
                (api_key, today),
            ).fetchone()
        return row["count"] if row else 0

    def get_all_usage(self) -> dict[str, int]:
        today = str(date.today())
        with _db() as conn:
            rows = conn.execute(
                "SELECT api_key, count FROM usage_counters WHERE day = ?", (today,)
            ).fetchall()
        return {r["api_key"]: r["count"] for r in rows}


usage_tracker = UsageTracker()
