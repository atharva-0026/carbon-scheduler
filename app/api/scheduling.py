import json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.regions import available_regions
from app.models.job import Job
from app.schemas.job import JobSubmitRequest, JobSubmitResponse
from app.services.inprocess_worker import run_job_pipeline_sync
from app.tasks import schedule_job

router = APIRouter(tags=["scheduling"])


@router.post("/submit-job", response_model=JobSubmitResponse)
def submit_job(
    payload: JobSubmitRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> JobSubmitResponse:
    """Accept a deferrable job and hand it to the scheduler.

    Persists the job as "pending", then either enqueues the `schedule_job`
    Celery task (picks a region/time slot via the scoring engine and
    dispatches it -- the normal path, when a Celery worker is running) or,
    if `settings.use_inprocess_worker` is set (no worker available, e.g.
    Render's free tier), runs the same decision + dispatch itself via
    FastAPI `BackgroundTasks` after the response is sent. See
    app/services/inprocess_worker.py for what that fallback doesn't do
    (no delayed/countdown dispatch).
    """
    if payload.allowed_regions:
        unknown = [r for r in payload.allowed_regions if r not in available_regions()]
        if unknown:
            raise HTTPException(status_code=422, detail=f"Unknown region(s): {unknown}")

    job = Job(
        name=payload.name or payload.job_type,
        job_type=payload.job_type,
        estimated_duration_minutes=payload.estimated_duration_minutes,
        deadline=payload.deadline,
        allowed_regions=json.dumps(payload.allowed_regions) if payload.allowed_regions else None,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    if get_settings().use_inprocess_worker:
        background_tasks.add_task(run_job_pipeline_sync, job.id)
        task_id = f"inprocess-{job.id}"
    else:
        task_id = schedule_job.delay(job.id).id

    return JobSubmitResponse(
        **{
            "id": job.id,
            "name": job.name,
            "job_type": job.job_type,
            "status": job.status,
            "estimated_duration_minutes": job.estimated_duration_minutes,
            "deadline": job.deadline,
            "scheduled_region": job.scheduled_region,
            "scheduled_start": job.scheduled_start,
            "created_at": job.created_at,
        },
        task_id=task_id,
    )
