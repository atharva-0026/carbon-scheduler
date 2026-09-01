from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.job import Job
from app.schemas.job import JobCreate, JobOut

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("", response_model=JobOut)
def create_job(payload: JobCreate, db: Session = Depends(get_db)) -> Job:
    job = Job(
        name=payload.name,
        job_type=payload.job_type,
        estimated_duration_minutes=payload.estimated_duration_minutes,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


@router.get("", response_model=list[JobOut])
def list_jobs(db: Session = Depends(get_db)) -> list[Job]:
    return db.query(Job).all()


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: int, db: Session = Depends(get_db)) -> Job:
    return db.query(Job).filter(Job.id == job_id).first()
