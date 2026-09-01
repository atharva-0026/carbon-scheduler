from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Integer, String
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
    # gCO2eq/kWh at the chosen region, and at the home region at submission
    # time (the "naive: always run immediately at home" baseline) -- both
    # are live external readings at decision time, so they're persisted
    # rather than recomputed later. Cost is a deterministic function of
    # (region, duration) and is computed on read instead (see
    # app.core.carbon_math.cost_usd).
    carbon_intensity_actual: Mapped[float | None] = mapped_column(Float, nullable=True)
    carbon_intensity_baseline: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
