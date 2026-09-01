"""Generate demonstrative job-history data so the dashboard is demoable
without live API keys, a running Celery worker, or Redis.

Usage:
    python scripts/seed_data.py                 # seed Postgres AND refresh
                                                  # the bundled CSV fallback
    python scripts/seed_data.py --target csv     # only refresh dashboard/sample_data.csv
    python scripts/seed_data.py --target db      # only seed Postgres
    python scripts/seed_data.py --count 60       # generate a different number of jobs

Every number here is fabricated for demo purposes: region "typical" carbon
intensity is a rough made-up spread (see BASE_INTENSITY below), not real
ElectricityMaps data, and the DB seed clears + reinserts with fixed ids
(fine for a demo dataset, not something to run against a database with
real submitted jobs you want to keep).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.core.regions import HOME_REGION, available_regions  # noqa: E402

SAMPLE_CSV_PATH = REPO_ROOT / "dashboard" / "sample_data.csv"

JOB_TYPES = ["ml-training", "batch", "ci", "etl"]

# Rough, made-up "typical" grid carbon intensity (gCO2eq/kWh) per region --
# not real ElectricityMaps data, just enough spread to tell a believable
# story (e.g. eu-north-1 standing in for a hydro/nuclear-heavy grid,
# ap-south-1 for a coal-heavy one).
BASE_INTENSITY: dict[str, float] = {
    "us-east-1": 420.0,
    "us-west-2": 250.0,
    "eu-west-1": 300.0,
    "eu-north-1": 90.0,
    "ap-south-1": 650.0,
}


@dataclass
class SeedJob:
    id: int
    name: str
    job_type: str
    status: str
    estimated_duration_minutes: int
    deadline: str | None
    allowed_regions: str | None
    scheduled_region: str | None
    scheduled_start: str | None
    carbon_intensity_actual: float | None
    carbon_intensity_baseline: float | None
    created_at: str


def _simulate_intensity(region: str, dt: datetime, rng: random.Random) -> float:
    """A believable, made-up carbon intensity reading: a per-region base,
    a mild diurnal swing, and noise. Not a real forecast model."""
    base = BASE_INTENSITY.get(region, 400.0)
    hour = dt.hour + dt.minute / 60.0
    diurnal = 1.0 + 0.25 * math.sin((hour - 6) / 24 * 2 * math.pi)
    noise = rng.uniform(0.85, 1.15)
    return round(max(20.0, base * diurnal * noise), 1)


def generate_sample_jobs(count: int = 40, seed: int = 42) -> list[SeedJob]:
    rng = random.Random(seed)
    regions = available_regions()
    # Weight region selection inversely to typical intensity, so the
    # simulated "carbon-aware scheduler" picks skew toward cleaner grids --
    # the same intuition as the real scoring engine, without calling it.
    weights = [1.0 / BASE_INTENSITY.get(r, 400.0) for r in regions]

    now = datetime.now(timezone.utc)
    jobs: list[SeedJob] = []

    for i in range(1, count + 1):
        created_at = now - timedelta(
            days=rng.uniform(0, 14), hours=rng.uniform(0, 24), minutes=rng.uniform(0, 60)
        )
        job_type = rng.choice(JOB_TYPES)
        duration = rng.choice([15, 30, 45, 60, 90, 120, 180])

        has_deadline = rng.random() < 0.6
        deadline = (
            created_at + timedelta(minutes=duration + rng.uniform(30, 600))
            if has_deadline
            else None
        )

        restricted = rng.random() < 0.2
        allowed = rng.sample(regions, k=2) if restricted else None
        candidate_regions = allowed or regions
        candidate_weights = [weights[regions.index(r)] for r in candidate_regions]

        failed = rng.random() < 0.05  # a handful of jobs where scheduling failed
        if failed:
            jobs.append(
                SeedJob(
                    id=i,
                    name=f"{job_type}-{i:03d}",
                    job_type=job_type,
                    status="failed",
                    estimated_duration_minutes=duration,
                    deadline=deadline.isoformat() if deadline else None,
                    allowed_regions=json.dumps(allowed) if allowed else None,
                    scheduled_region=None,
                    scheduled_start=None,
                    carbon_intensity_actual=None,
                    carbon_intensity_baseline=None,
                    created_at=created_at.isoformat(),
                )
            )
            continue

        # A tight deadline sometimes forces an immediate, non-optimal (often
        # home-region) placement -- mirrors the real scheduler's urgency
        # override in app/services/scoring.py.
        urgent = has_deadline and rng.random() < 0.25
        if urgent:
            scheduled_region = (
                HOME_REGION if HOME_REGION in candidate_regions else candidate_regions[0]
            )
            delay_minutes = 0.0
        else:
            scheduled_region = rng.choices(candidate_regions, weights=candidate_weights, k=1)[0]
            max_delay = 180.0
            if deadline:
                slack = (deadline - created_at).total_seconds() / 60.0 - duration
                max_delay = max(0.0, min(max_delay, slack))
            delay_minutes = rng.uniform(0, max_delay) if max_delay > 0 else 0.0

        scheduled_start = created_at + timedelta(minutes=delay_minutes)
        baseline_intensity = _simulate_intensity(HOME_REGION, created_at, rng)
        actual_intensity = _simulate_intensity(scheduled_region, scheduled_start, rng)

        status = "dispatched" if created_at < now - timedelta(hours=2) else "scheduled"

        jobs.append(
            SeedJob(
                id=i,
                name=f"{job_type}-{i:03d}",
                job_type=job_type,
                status=status,
                estimated_duration_minutes=duration,
                deadline=deadline.isoformat() if deadline else None,
                allowed_regions=json.dumps(allowed) if allowed else None,
                scheduled_region=scheduled_region,
                scheduled_start=scheduled_start.isoformat(),
                carbon_intensity_actual=actual_intensity,
                carbon_intensity_baseline=baseline_intensity,
                created_at=created_at.isoformat(),
            )
        )

    jobs.sort(key=lambda j: j.created_at)
    return jobs


def write_csv(jobs: list[SeedJob], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(asdict(jobs[0]).keys())
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for job in jobs:
            writer.writerow(asdict(job))
    print(f"Wrote {len(jobs)} sample jobs to {path}")


def write_db(jobs: list[SeedJob]) -> None:
    # Imported lazily -- building the DB engine shouldn't be required for
    # `--target csv`, and shouldn't fail import of this whole module.
    from app.core.db import Base, SessionLocal, engine
    from app.models.job import Job as JobModel

    try:
        Base.metadata.create_all(engine)
        session = SessionLocal()
        session.execute(JobModel.__table__.select().limit(1))  # cheap connectivity check
    except Exception as exc:  # pragma: no cover -- depends on a live DB
        print(f"Could not connect to Postgres ({exc}); skipping DB seed.")
        return

    try:
        session.query(JobModel).delete()
        for job in jobs:
            session.add(
                JobModel(
                    id=job.id,
                    name=job.name,
                    job_type=job.job_type,
                    status=job.status,
                    estimated_duration_minutes=job.estimated_duration_minutes,
                    deadline=datetime.fromisoformat(job.deadline) if job.deadline else None,
                    allowed_regions=job.allowed_regions,
                    scheduled_region=job.scheduled_region,
                    scheduled_start=(
                        datetime.fromisoformat(job.scheduled_start) if job.scheduled_start else None
                    ),
                    carbon_intensity_actual=job.carbon_intensity_actual,
                    carbon_intensity_baseline=job.carbon_intensity_baseline,
                    created_at=datetime.fromisoformat(job.created_at),
                )
            )
        session.commit()
        print(f"Seeded {len(jobs)} jobs into Postgres.")
    finally:
        session.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=["both", "db", "csv"], default="both")
    parser.add_argument("--count", type=int, default=40)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    jobs = generate_sample_jobs(count=args.count, seed=args.seed)

    if args.target in ("both", "csv"):
        write_csv(jobs, SAMPLE_CSV_PATH)
    if args.target in ("both", "db"):
        write_db(jobs)


if __name__ == "__main__":
    main()
