from datetime import datetime

from pydantic import BaseModel


class JobCreate(BaseModel):
    name: str
    job_type: str
    estimated_duration_minutes: int
    deadline: datetime | None = None


class JobOut(BaseModel):
    id: int
    name: str
    job_type: str
    status: str
    estimated_duration_minutes: int
    deadline: datetime | None = None
    scheduled_region: str | None = None
    scheduled_start: datetime | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class JobSubmitRequest(BaseModel):
    """Payload for POST /submit-job."""

    job_type: str
    name: str | None = None
    estimated_duration_minutes: int
    deadline: datetime | None = None
    # Restrict scheduling to these regions; omit/None to consider all available regions.
    allowed_regions: list[str] | None = None


class JobSubmitResponse(JobOut):
    """JobOut plus the id of the Celery task that will schedule this job."""

    task_id: str
