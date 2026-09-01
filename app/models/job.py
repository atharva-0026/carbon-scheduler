from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    job_type: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, default="pending")
    estimated_duration_minutes: Mapped[int] = mapped_column(Integer)
    deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # JSON-encoded list[str] of allowed region keys, or NULL for "any region"
    allowed_regions: Mapped[str | None] = mapped_column(String, nullable=True)
    scheduled_region: Mapped[str | None] = mapped_column(String, nullable=True)
    scheduled_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
