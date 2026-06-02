"""Batch and schedule endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.controllers import base
from app.services import batch as batch_svc

router = APIRouter(
    prefix="/api/v1",
    tags=["batch"],
    dependencies=[Depends(base.verify_token)],
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class BatchRequest(BaseModel):
    topics: list[str] = Field(..., min_length=1, description="List of video topics/subjects")
    params: dict = Field(default_factory=dict, description="VideoParams fields (voice, language, etc.)")
    name: str = ""


class ScheduleRequest(BaseModel):
    topics: list[str] = Field(..., min_length=1)
    params: dict = Field(default_factory=dict)
    interval_hours: float = Field(24.0, gt=0, description="How often to run (hours)")
    name: str = ""


# ---------------------------------------------------------------------------
# Batch endpoints
# ---------------------------------------------------------------------------

@router.post("/batch", summary="Submit a batch of video topics")
def submit_batch(req: BatchRequest, request: Request):
    api_key = base.get_api_key(request) or ""
    result = batch_svc.create_batch(
        topics=req.topics,
        params_dict=req.params,
        api_key=api_key,
        name=req.name,
    )
    return result


@router.get("/batch", summary="List recent batches for your API key")
def list_batches(request: Request, limit: int = 20):
    api_key = base.get_api_key(request) or ""
    return batch_svc.list_batches(api_key=api_key, limit=limit)


@router.get("/batch/{batch_id}", summary="Get batch progress and per-task status")
def get_batch(batch_id: str):
    result = batch_svc.get_batch_status(batch_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    return result


@router.delete("/batch/{batch_id}", summary="Cancel a batch (stops queuing new tasks; in-flight tasks finish)")
def cancel_batch(batch_id: str):
    # Mark all not-yet-started tasks as cancelled in state — best-effort
    from app.services import state as sm
    from app.config.config import const
    import sqlite3
    from pathlib import Path

    with sqlite3.connect("storage/batch.db") as conn:
        conn.row_factory = sqlite3.Row
        tasks = conn.execute(
            "SELECT task_id FROM batch_tasks WHERE batch_id = ?", (batch_id,)
        ).fetchall()

    cancelled = 0
    for t in tasks:
        task_state = sm.state.get_task(t["task_id"]) or {}
        if task_state.get("state", 0) == 0:
            sm.state.update_task(t["task_id"], state=const.TASK_STATE_FAILED)
            cancelled += 1

    return {"batch_id": batch_id, "cancelled_pending": cancelled}


# ---------------------------------------------------------------------------
# Schedule endpoints
# ---------------------------------------------------------------------------

@router.post("/schedule", summary="Create a recurring batch job")
def create_schedule(req: ScheduleRequest, request: Request):
    api_key = base.get_api_key(request) or ""
    job_id = batch_svc.add_scheduled_job(
        topics=req.topics,
        params_dict=req.params,
        interval_hours=req.interval_hours,
        api_key=api_key,
        name=req.name,
    )
    return {"job_id": job_id, "interval_hours": req.interval_hours, "topics": len(req.topics)}


@router.get("/schedule", summary="List your active scheduled jobs")
def list_schedules(request: Request):
    api_key = base.get_api_key(request) or ""
    return batch_svc.list_scheduled_jobs(api_key=api_key)


@router.delete("/schedule/{job_id}", summary="Deactivate a scheduled job")
def delete_schedule(job_id: str):
    removed = batch_svc.remove_scheduled_job(job_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"job_id": job_id, "status": "deactivated"}
