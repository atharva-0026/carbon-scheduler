"""In-process fallback job pipeline, for environments with no Celery worker.

Render's free tier only runs web services -- it has no `type: worker`
background process, so there's nothing to consume the Celery queue that
`POST /submit-job` normally enqueues to. When `settings.use_inprocess_worker`
is true, the API instead runs the same scheduling decision and the same stub
dispatch itself, as a FastAPI `BackgroundTasks` callback after the response
is sent, rather than a Celery task a separate worker process picks up.

This intentionally mirrors app/tasks.py's `schedule_job` + `execute_job`
(same shared building blocks: `choose_schedule`, `dispatch_job`, etc. --
only the "how this gets run" glue differs) rather than sharing code with it,
so the real Celery path in app/tasks.py is untouched and still fully covers
what a production deployment (Docker Compose, a paid Render plan with a
worker service, ...) actually runs.

One real behavioral difference from the Celery path: this can't honor a
delayed start time. Celery's `apply_async(countdown=...)` lets a separate
worker process wake up later; a request-serving web process has no
equivalent without blocking a thread for however long the delay is (which
could be hours), which isn't reasonable on a single free-tier instance. So
this dispatches immediately after scheduling, whatever slot the scoring
engine picked -- good enough to demo the scoring/dashboard pipeline
end-to-end, not a substitute for real deadline-aware deferral.
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta

from app.core.db import SessionLocal
from app.models.job import Job
from app.services.execution_adapter import dispatch_job
from app.services.job_scheduler import NoAvailableRegionsError, choose_schedule, fetch_baseline_intensity

logger = logging.getLogger(__name__)


def run_job_pipeline_sync(job_id: int) -> dict:
    """Schedule `job_id`, then immediately dispatch it. No Celery involved."""
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if job is None:
            logger.error("run_job_pipeline_sync: job %s not found", job_id)
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
            logger.error("run_job_pipeline_sync: job %s could not be scheduled: %s", job_id, exc)
            return {"job_id": job_id, "status": "failed", "reason": str(exc)}

        job.status = "scheduled"
        job.scheduled_region = best.candidate.region
        job.scheduled_start = now + timedelta(minutes=best.candidate.delay_minutes)
        job.carbon_intensity_actual = best.candidate.carbon_intensity
        job.carbon_intensity_baseline = fetch_baseline_intensity()
        db.commit()

        result = dispatch_job(job_id, job.scheduled_region)
        job.status = "dispatched"
        db.commit()

        return {
            "job_id": job_id,
            "status": result.status,
            "region": job.scheduled_region,
            "message": result.message,
        }
    finally:
        db.close()
