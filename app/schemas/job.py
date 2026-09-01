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
    scheduled_region: str | None = None
    scheduled_start: datetime | None = None
    created_at: datetime

    class Config:
        from_attributes = True
