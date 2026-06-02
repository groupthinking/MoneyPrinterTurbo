"""Batch video generation and scheduled job runner.

Batch: submit a list of topics and get a batch_id. Tasks are queued
       through the existing task_manager; progress is tracked in SQLite.

Schedule: recurring jobs stored in SQLite; a background thread fires
          them at the requested interval.
"""
import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from loguru import logger

from app.utils import utils

_DB_PATH = Path("storage/batch.db")
_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

_lock = threading.Lock()


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db():
    with _db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS batches (
                id          TEXT PRIMARY KEY,
                name        TEXT NOT NULL DEFAULT '',
                api_key     TEXT NOT NULL DEFAULT '',
                total       INTEGER NOT NULL,
                created_at  TEXT NOT NULL,
                params_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS batch_tasks (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id    TEXT NOT NULL,
                task_id     TEXT NOT NULL,
                topic       TEXT NOT NULL,
                created_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS scheduled_jobs (
                id              TEXT PRIMARY KEY,
                name            TEXT NOT NULL DEFAULT '',
                topics_json     TEXT NOT NULL,
                params_json     TEXT NOT NULL DEFAULT '{}',
                api_key         TEXT NOT NULL DEFAULT '',
                interval_hours  REAL NOT NULL DEFAULT 24,
                next_run_at     TEXT NOT NULL,
                last_run_at     TEXT,
                run_count       INTEGER NOT NULL DEFAULT 0,
                active          INTEGER NOT NULL DEFAULT 1,
                created_at      TEXT NOT NULL
            );
        """)
        conn.commit()


_init_db()


# ---------------------------------------------------------------------------
# Batch
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_batch(
    topics: list[str],
    params_dict: dict,
    api_key: str = "",
    name: str = "",
) -> dict:
    """Submit N topics as a batch. Returns {batch_id, task_ids}."""
    from app.controllers.manager.instance import task_manager
    from app.controllers.manager.base_manager import TaskQueueFullError
    from app.services import state as sm
    from app.services import task as tm
    from app.models.schema import VideoParams

    batch_id = utils.get_uuid()
    task_ids = []
    created_at = _now_iso()

    with _lock, _db() as conn:
        conn.execute(
            "INSERT INTO batches (id, name, api_key, total, created_at, params_json) VALUES (?,?,?,?,?,?)",
            (batch_id, name, api_key, len(topics), created_at, json.dumps(params_dict)),
        )
        for topic in topics:
            task_id = utils.get_uuid()
            p = dict(params_dict)
            p["video_subject"] = topic
            try:
                body = VideoParams(**p)
            except Exception as exc:
                logger.warning(f"batch {batch_id}: invalid params for topic '{topic}': {exc}")
                continue
            sm.state.update_task(task_id)
            try:
                task_manager.add_task(tm.start, task_id=task_id, params=body, stop_at="video")
                task_ids.append(task_id)
                conn.execute(
                    "INSERT INTO batch_tasks (batch_id, task_id, topic, created_at) VALUES (?,?,?,?)",
                    (batch_id, task_id, topic, created_at),
                )
                logger.info(f"batch {batch_id}: queued task {task_id} for topic '{topic}'")
            except TaskQueueFullError:
                sm.state.delete_task(task_id)
                logger.warning(f"batch {batch_id}: queue full, skipped topic '{topic}'")
        conn.commit()

    return {"batch_id": batch_id, "task_ids": task_ids, "total": len(task_ids)}


def get_batch_status(batch_id: str) -> dict | None:
    from app.services import state as sm
    from app.utils.utils import get_response
    from app.config.config import const

    with _db() as conn:
        row = conn.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
        if not row:
            return None
        tasks = conn.execute(
            "SELECT task_id, topic FROM batch_tasks WHERE batch_id = ?", (batch_id,)
        ).fetchall()

    task_details = []
    completed = failed = running = 0
    for t in tasks:
        task_state = sm.state.get_task(t["task_id"]) or {}
        state_val = task_state.get("state", 0)
        if state_val == 1:
            running += 1
        elif state_val == 2:
            completed += 1
        elif state_val == -1:
            failed += 1
        task_details.append({
            "task_id": t["task_id"],
            "topic": t["topic"],
            "state": state_val,
            "progress": task_state.get("progress", 0),
        })

    total = row["total"]
    return {
        "batch_id": batch_id,
        "name": row["name"],
        "total": total,
        "completed": completed,
        "failed": failed,
        "running": running,
        "pending": total - completed - failed - running,
        "created_at": row["created_at"],
        "tasks": task_details,
    }


def list_batches(api_key: str = "", limit: int = 20) -> list[dict]:
    with _db() as conn:
        if api_key:
            rows = conn.execute(
                "SELECT * FROM batches WHERE api_key = ? ORDER BY created_at DESC LIMIT ?",
                (api_key, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM batches ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Scheduled jobs
# ---------------------------------------------------------------------------

def add_scheduled_job(
    topics: list[str],
    params_dict: dict,
    interval_hours: float = 24.0,
    api_key: str = "",
    name: str = "",
) -> str:
    job_id = utils.get_uuid()
    now = datetime.now(timezone.utc)
    next_run = (now + timedelta(hours=interval_hours)).isoformat()
    with _lock, _db() as conn:
        conn.execute(
            """INSERT INTO scheduled_jobs
               (id, name, topics_json, params_json, api_key, interval_hours,
                next_run_at, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (job_id, name, json.dumps(topics), json.dumps(params_dict),
             api_key, interval_hours, next_run, now.isoformat()),
        )
        conn.commit()
    logger.info(f"Scheduled job {job_id} '{name}' every {interval_hours}h, first run at {next_run}")
    return job_id


def remove_scheduled_job(job_id: str) -> bool:
    with _lock, _db() as conn:
        cur = conn.execute("UPDATE scheduled_jobs SET active = 0 WHERE id = ?", (job_id,))
        conn.commit()
    return cur.rowcount > 0


def list_scheduled_jobs(api_key: str = "") -> list[dict]:
    with _db() as conn:
        if api_key:
            rows = conn.execute(
                "SELECT * FROM scheduled_jobs WHERE api_key = ? AND active = 1 ORDER BY created_at DESC",
                (api_key,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM scheduled_jobs WHERE active = 1 ORDER BY created_at DESC"
            ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Background scheduler thread
# ---------------------------------------------------------------------------

def _run_due_jobs():
    now_iso = _now_iso()
    with _db() as conn:
        due = conn.execute(
            "SELECT * FROM scheduled_jobs WHERE active = 1 AND next_run_at <= ?",
            (now_iso,),
        ).fetchall()
    for job in due:
        try:
            topics = json.loads(job["topics_json"])
            params = json.loads(job["params_json"])
            name = f"{job['name']} (run {job['run_count'] + 1})"
            result = create_batch(topics, params, api_key=job["api_key"], name=name)
            logger.info(f"Scheduled job {job['id']} fired — batch {result['batch_id']}")
            next_run = (
                datetime.now(timezone.utc) + timedelta(hours=job["interval_hours"])
            ).isoformat()
            with _lock, _db() as conn:
                conn.execute(
                    "UPDATE scheduled_jobs SET next_run_at=?, last_run_at=?, run_count=run_count+1 WHERE id=?",
                    (next_run, _now_iso(), job["id"]),
                )
                conn.commit()
        except Exception as exc:
            logger.error(f"Scheduled job {job['id']} failed: {exc}")


def _scheduler_loop():
    logger.info("Batch scheduler started")
    while True:
        try:
            _run_due_jobs()
        except Exception as exc:
            logger.error(f"Scheduler loop error: {exc}")
        time.sleep(60)


_scheduler_thread = threading.Thread(target=_scheduler_loop, daemon=True, name="batch-scheduler")
_scheduler_thread.start()
