import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.regions import available_regions
from app.models.job import Job
from app.schemas.job import JobSubmitRequest, JobSubmitResponse
from app.tasks import schedule_job

router = APIRouter(tags=["scheduling"])


@router.post("/submit-job", response_model=JobSubmitResponse)
def submit_job(payload: JobSubmitRequest, db: Session = Depends(get_db)) -> JobSubmitResponse:
    """Accept a deferrable job and hand it to the scheduler.

    Persists the job as "pending", enqueues the `schedule_job` Celery task
    (which picks a region/time slot via the scoring engine and dispatches
    it), and returns the created job plus the Celery task id.
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

    async_result = schedule_job.delay(job.id)

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
        task_id=async_result.id,
    )
