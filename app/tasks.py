from app.celery_app import celery_app


@celery_app.task(name="run_deferrable_job")
def run_deferrable_job(job_id: int) -> str:
    """Placeholder task: execute a deferred job once dispatched to a region."""
    return f"job {job_id} executed"
