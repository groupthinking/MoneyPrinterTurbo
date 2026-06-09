"""Stripe billing — API key issuance, SQLite key store, tier definitions."""
import contextlib
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

_DB_PATH = Path("storage/billing.db")
_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

TIERS: dict[str, int] = {
    "free": 5,       # 5 videos/day, no payment
    "starter": 30,   # 30 videos/day
    "pro": -1,       # unlimited
}

_lock = threading.Lock()


@contextlib.contextmanager
def _db():
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _init_db():
    with _db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS api_keys (
                key                   TEXT PRIMARY KEY,
                tier                  TEXT NOT NULL,
                daily_quota           INTEGER NOT NULL,
                stripe_customer_id    TEXT,
                stripe_subscription_id TEXT,
                customer_email        TEXT,
                created_at            TEXT NOT NULL,
                active                INTEGER NOT NULL DEFAULT 1
            )
        """)
        conn.commit()


_init_db()


def issue_key(
    tier: str,
    stripe_customer_id: str = "",
    stripe_subscription_id: str = "",
    customer_email: str = "",
) -> str:
    if tier not in TIERS:
        raise ValueError(f"Unknown tier: {tier}")
    key = "mpt_" + secrets.token_urlsafe(32)
    quota = TIERS[tier]
    with _lock, _db() as conn:
        conn.execute(
            """INSERT INTO api_keys
               (key, tier, daily_quota, stripe_customer_id, stripe_subscription_id,
                customer_email, created_at, active)
               VALUES (?, ?, ?, ?, ?, ?, ?, 1)""",
            (key, tier, quota, stripe_customer_id, stripe_subscription_id,
             customer_email, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    logger.info(f"Issued {tier} key …{key[-8:]} for {customer_email or stripe_customer_id or 'anon'}")
    return key


def get_key_info(key: str) -> dict | None:
    """Return key row as dict, or None if not found / inactive."""
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM api_keys WHERE key = ? AND active = 1", (key,)
        ).fetchone()
    return dict(row) if row else None


def deactivate_by_subscription(stripe_subscription_id: str):
    with _lock, _db() as conn:
        conn.execute(
            "UPDATE api_keys SET active = 0 WHERE stripe_subscription_id = ?",
            (stripe_subscription_id,),
        )
        conn.commit()
    logger.info(f"Deactivated keys for subscription {stripe_subscription_id}")
