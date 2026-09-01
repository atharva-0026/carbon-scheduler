"""Celery tasks: schedule a submitted job, then dispatch it once its slot arrives."""

from __future__ import annotations

import json
import logging
from datetime import timedelta

from app.celery_app import celery_app
from app.core.db import SessionLocal
from app.models.job import Job
from app.services.execution_adapter import dispatch_job
from app.services.job_scheduler import NoAvailableRegionsError, choose_schedule, fetch_baseline_intensity

logger = logging.getLogger(__name__)


@celery_app.task(name="schedule_job", bind=True, max_retries=3, default_retry_delay=30)
def schedule_job(self, job_id: int) -> dict:
    """Pick the best (region, start time) for a job and either dispatch it
    immediately (delay <= 0) or schedule `execute_job` to run at that time."""
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if job is None:
            logger.error("schedule_job: job %s not found", job_id)
            return {"job_id": job_id, "status": "not_found"}

        allowed_regions = json.loads(job.allowed_regions) if job.allowed_regions else None

        try:
            best, now = choose_schedule(
                allowed_regions=allowed_regions,
                deadline=job.deadline,
                estimated_duration_minutes=job.estimated_duration_minutes,
            )
        except NoAvailableRegionsError as exc:
            job.status = "failed"
            db.commit()
            logger.error("schedule_job: job %s could not be scheduled: %s", job_id, exc)
            return {"job_id": job_id, "status": "failed", "reason": str(exc)}

        scheduled_start = now + timedelta(minutes=best.candidate.delay_minutes)
        job.status = "scheduled"
        job.scheduled_region = best.candidate.region
        job.scheduled_start = scheduled_start
        job.carbon_intensity_actual = best.candidate.carbon_intensity
        job.carbon_intensity_baseline = fetch_baseline_intensity()
        db.commit()

        delay_seconds = max(0, int(best.candidate.delay_minutes * 60))
        if delay_seconds <= 0:
            execute_job.delay(job_id)
        else:
            execute_job.apply_async(args=[job_id], countdown=delay_seconds)

        return {
            "job_id": job_id,
            "status": "scheduled",
            "region": best.candidate.region,
            "scheduled_start": scheduled_start.isoformat(),
            "score": best.score,
        }
    finally:
        db.close()


@celery_app.task(name="execute_job")
def execute_job(job_id: int) -> dict:
    """Hand a scheduled job off to the (stub) execution adapter."""
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if job is None:
            logger.error("execute_job: job %s not found", job_id)
            return {"job_id": job_id, "status": "not_found"}

        result = dispatch_job(job_id, job.scheduled_region or "unknown")
        job.status = "dispatched"
        db.commit()
        return {"job_id": job_id, "status": result.status, "message": result.message}
    finally:
        db.close()
