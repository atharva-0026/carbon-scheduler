#!/usr/bin/env bash
set -euo pipefail

# Run this from inside your carbon-scheduler repo root.

mkdir -p "$(dirname "README.md")"
cat > README.md << 'PYEOF_README_MD'
# Carbon-Aware Compute Scheduler

Reschedules deferrable compute jobs (batch, ML training, CI) to run in
whichever region and time window has the cleanest electricity grid, instead
of always running them immediately wherever the request came from. Built as
a portfolio project for climate-tech / SWE roles.

## Why

Cloud compute runs on a grid whose carbon intensity swings 3-8x by region
and by hour, driven by the local mix of solar/wind/nuclear/gas/coal. A job
that isn't latency-sensitive — a nightly batch job, an ML training run, a CI
suite — doesn't have to run *now, here*. Delaying it a few hours, or routing
it to a region that happens to be sunny or windy right now, can cut its
carbon footprint substantially for free. This project ingests real-time
grid carbon intensity, scores the (region, time) options for each submitted
job, and picks the best one within its deadline.

## Architecture

```
                 POST /submit-job
                        |
                        v
              +-------------------+
              |     FastAPI       |  writes job (status=pending)
              |  (app/api/*)      |----------------------+
              +-------------------+                      |
                        |                                 v
              enqueue schedule_job                  +-----------+
                        |                            | Postgres  |
                        v                             +-----------+
              +-------------------+                        ^
              |   Celery worker   |------------------------+
              |   (app/tasks.py)  |   updates job (region, start time,
              +-------------------+   carbon intensity actual/baseline)
                   |          |
                   v          v
        +-------------------+   +----------------------+
        | scoring engine    |   | carbon intensity      |
        | (app/services/    |<--| client (ElectricityMaps|
        |  scoring.py,      |   |  API), cached in Redis |
        |  job_scheduler.py)|   +----------------------+
        +-------------------+
                   |
                   v
        execution adapter (stub) -- "runs" the job, marks it dispatched

              +-------------------+
              |    Streamlit      |  reads job history from Postgres
              |    dashboard      |  (falls back to bundled sample CSV)
              +-------------------+
```

**Request flow.** `POST /submit-job` (`app/api/scheduling.py`) validates the
requested regions, writes a `Job` row with `status=pending`, and enqueues
the `schedule_job` Celery task. That task (`app/tasks.py`) asks
`app/services/job_scheduler.py` to build one candidate per (allowed region ×
a handful of delay slots up to the deadline), each annotated with its live
carbon intensity, cross-region latency penalty, and how much slack the job
has left. `app/services/scoring.py` normalizes those signals and combines
them into a single weighted score; the best-scoring candidate wins.
`schedule_job` writes the chosen region/start time back to Postgres, then
either dispatches immediately or schedules `execute_job` to fire (via
Celery's `countdown`) at the chosen start time. `execute_job` hands the job
to a stub execution adapter (`app/services/execution_adapter.py`) — swapping
in a real one (an actual Kubernetes job, a cloud batch API, etc.) is the
natural next step and doesn't touch the scheduling logic above it.

**Carbon data.** `app/services/carbon_intensity.py` calls the
[ElectricityMaps](https://www.electricitymaps.com/) API for a region's
current grid carbon intensity (gCO2eq/kWh) and caches each reading in Redis
for 15 minutes, so scoring a job's candidates doesn't mean N live API calls
on every submission. Without an API key configured, live scheduling can't
happen (there's no synthetic fallback in that path — see Limitations
below); the dashboard's demo mode is what makes the project explorable with
zero live credentials.

**Dashboard.** `dashboard/app.py` reads job history straight from Postgres
(`dashboard/data.py`) and shows carbon saved and cost delta vs. a naive
baseline (always run immediately, at home, ignoring carbon), a cost/carbon
tradeoff scatter plot, and a per-job table of the chosen region/time vs. the
naive alternative. If Postgres isn't reachable or has no jobs yet, it falls
back to the bundled `dashboard/sample_data.csv` so the dashboard is always
demoable — `scripts/seed_data.py` is what generates that file (and can seed
real Postgres rows too).

## Stack

FastAPI · Celery · Redis (broker + result backend + carbon-intensity cache)
· Postgres · SQLAlchemy · Streamlit + Plotly · Docker Compose · deployed on
Railway.

## Layout

- `app/main.py` — FastAPI entrypoint; creates tables on startup (see
  Limitations)
- `app/api/` — routes: `POST /jobs`, `GET /jobs`, `GET /jobs/{id}`,
  `POST /submit-job`
- `app/models/` — SQLAlchemy models (`Job`)
- `app/schemas/` — Pydantic request/response schemas
- `app/services/` — carbon intensity client, scoring engine
  (`scoring.py`), job scheduler (`job_scheduler.py`), execution adapter
  (stub)
- `app/core/` — settings, DB session/engine, region catalog, shared
  carbon/cost math
- `app/celery_app.py`, `app/tasks.py` — Celery app + the `schedule_job` /
  `execute_job` background tasks
- `dashboard/` — Streamlit carbon-savings dashboard + its data loading
  (`dashboard/data.py`)
- `scripts/seed_data.py` — generates demo job history (Postgres and/or the
  dashboard's CSV fallback)
- `tests/` — pytest (unit tests only; hermetic SQLite in-memory DB, no
  external services needed to run them)

## Run everything with Docker Compose

```bash
cp .env.example .env
docker compose up --build
```

This starts Postgres, Redis, the FastAPI API (`:8000`), a Celery worker,
and the Streamlit dashboard (`:8501`). The api/worker/dashboard containers
talk to `db`/`redis` by their Compose service names — the `DATABASE_URL` /
`REDIS_URL` / `CELERY_*` values baked into `docker-compose.yml` already
point at those, overriding whatever `.env` has (`.env`'s `localhost` values
are for running processes directly on your host — see below). All you need
in `.env` for live carbon-aware scheduling is `ELECTRICITYMAPS_API_KEY`.

Tables are created automatically on first startup (`Base.metadata.create_all`,
called from both the API and the worker — see Limitations). Once it's up:

```bash
curl -X POST localhost:8000/submit-job \
  -H "content-type: application/json" \
  -d '{"job_type":"batch","estimated_duration_minutes":30,"deadline":"2026-01-01T00:00:00Z"}'
```

and open http://localhost:8501 for the dashboard. Without an
`ELECTRICITYMAPS_API_KEY` set, that job will get scheduled to `status=failed`
(no live carbon data to score against) — the dashboard still works via the
bundled sample data (see below), it just won't show *your* submitted job.

## Run locally without Docker

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # DATABASE_URL/REDIS_URL here default to localhost

uvicorn app.main:app --reload
```

You'll need Postgres and Redis running locally and reachable at the
`localhost` URLs in `.env` (or edit `.env` to point elsewhere). Celery
worker, in another shell:

```bash
celery -A app.celery_app.celery_app worker --loglevel=info
```

Dashboard, in another shell:

```bash
streamlit run dashboard/app.py
```

The dashboard needs no live services to demo: if it can't reach Postgres
(or the `jobs` table is empty) it falls back to the bundled sample dataset
at `dashboard/sample_data.csv`. To seed realistic-looking job history —
Postgres, the CSV fallback, or both:

```bash
python scripts/seed_data.py               # seeds Postgres and refreshes the CSV
python scripts/seed_data.py --target csv  # just refresh dashboard/sample_data.csv
```

## Tests

```bash
pytest
```

All 60+ tests run against an in-memory SQLite DB and mocked HTTP/Celery
calls — no Postgres, Redis, or ElectricityMaps key required.

## Limitations

- **No database migrations.** Tables are created with
  `Base.metadata.create_all()` on startup (`app/core/db.py::init_db`,
  called from the FastAPI lifespan hook and from a Celery
  `worker_process_init` signal, so either process can be first to boot).
  It's idempotent and fine for a portfolio project's single `Job` table, but
  it won't handle schema changes to existing columns — a real project would
  use the Alembic setup that's already in `requirements.txt` but not yet
  wired up.
- **No synthetic fallback for live scheduling.** If
  `ELECTRICITYMAPS_API_KEY` isn't set (or the API call fails for every
  allowed region), `schedule_job` marks the job `failed` rather than
  guessing — carbon-aware scheduling without carbon data isn't carbon-aware
  scheduling. The dashboard's demo mode (sample CSV / seeded Postgres data)
  is intentionally the separate path for exploring the project without a
  key.
- **Execution adapter is a stub.** `dispatch_job` always "succeeds" — it's
  the seam where a real executor (Kubernetes, a cloud batch API, a CI
  runner) would plug in.
- **Cost figures are illustrative**, not real cloud pricing (see
  `app/core/carbon_math.py`) — flat made-up per-region hourly rates, used
  only for the dashboard's cost/carbon tradeoff chart.
PYEOF_README_MD

mkdir -p "$(dirname "app/celery_app.py")"
cat > app/celery_app.py << 'PYEOF_APP_CELERY_APP_PY'
from celery import Celery
from celery.signals import worker_process_init

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "carbon_scheduler",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.tasks"],
)

celery_app.conf.update(task_serializer="json", result_serializer="json", accept_content=["json"])


@worker_process_init.connect
def _create_tables(**kwargs) -> None:
    """Make sure tables exist before this worker process picks up any task.

    The worker is a separate process from the API and may start first (or
    run standalone) -- it can't rely on the API's startup hook. Only fires
    when an actual `celery worker` boots, never on a plain import of this
    module (e.g. from tests), so it can't interfere with the SQLite test DB.
    """
    from app.core.db import init_db
    from app.models.job import Job  # noqa: F401 -- register on Base.metadata

    init_db()
PYEOF_APP_CELERY_APP_PY

mkdir -p "$(dirname "app/core/db.py")"
cat > app/core/db.py << 'PYEOF_APP_CORE_DB_PY'
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create any tables that don't exist yet.

    No Alembic migrations are set up for this project -- this is the
    portfolio-project equivalent (idempotent, safe to call from both the
    API and the Celery worker on every startup). Models must already be
    imported (so they're registered on Base.metadata) before this runs.
    """
    Base.metadata.create_all(bind=engine)
PYEOF_APP_CORE_DB_PY

mkdir -p "$(dirname "app/main.py")"
cat > app/main.py << 'PYEOF_APP_MAIN_PY'
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import health, jobs, scheduling
from app.core.db import init_db


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # app.api.jobs / app.api.scheduling (imported below) already import
    # app.models.job, so Job is registered on Base.metadata by the time
    # this runs. No Alembic migrations are set up -- see init_db().
    init_db()
    yield


app = FastAPI(title="Carbon-Aware Compute Scheduler", version="0.1.0", lifespan=lifespan)

app.include_router(health.router)
app.include_router(jobs.router)
app.include_router(scheduling.router)


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "carbon-aware-compute-scheduler"}
PYEOF_APP_MAIN_PY

mkdir -p "$(dirname "docker-compose.yml")"
cat > docker-compose.yml << 'PYEOF_DOCKER-COMPOSE_YML'
services:
  api:
    build: .
    ports:
      - "8000:8000"
    env_file: .env
    environment:
      # Inside the compose network, services reach each other by service
      # name, not localhost -- these override whatever .env has (.env is
      # meant for running the API/worker/dashboard directly on the host).
      DATABASE_URL: postgresql://postgres:postgres@db:5432/carbon_scheduler
      REDIS_URL: redis://redis:6379/0
      CELERY_BROKER_URL: redis://redis:6379/0
      CELERY_RESULT_BACKEND: redis://redis:6379/1
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy

  worker:
    build: .
    command: celery -A app.celery_app.celery_app worker --loglevel=info
    env_file: .env
    environment:
      DATABASE_URL: postgresql://postgres:postgres@db:5432/carbon_scheduler
      REDIS_URL: redis://redis:6379/0
      CELERY_BROKER_URL: redis://redis:6379/0
      CELERY_RESULT_BACKEND: redis://redis:6379/1
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy

  dashboard:
    build: .
    command: streamlit run dashboard/app.py --server.port 8501 --server.address 0.0.0.0 --server.headless true
    ports:
      - "8501:8501"
    env_file: .env
    environment:
      DATABASE_URL: postgresql://postgres:postgres@db:5432/carbon_scheduler
      REDIS_URL: redis://redis:6379/0
    depends_on:
      db:
        condition: service_healthy

  db:
    image: postgres:16
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: carbon_scheduler
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 5s
      timeout: 5s
      retries: 10

  redis:
    image: redis:7
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 5s
      retries: 10

volumes:
  pgdata:
PYEOF_DOCKER-COMPOSE_YML

mkdir -p "$(dirname ".dockerignore")"
cat > .dockerignore << 'PYEOF__DOCKERIGNORE'
.git
.gitignore
__pycache__/
*.pyc
.pytest_cache/
.venv/
venv/
.env
tests/
README.md
PYEOF__DOCKERIGNORE

rm -f app/services/scheduler.py

git add -A
git commit -m "Fix Docker Compose wiring (missing tables, wrong hostnames), remove dead scheduler stub, rewrite README

- api/worker/dashboard containers pointed at DATABASE_URL/REDIS_URL=localhost
  inside docker-compose.yml, which resolves to the container itself, not the
  db/redis services -- override with service hostnames in compose instead
- nothing ever created the jobs table (no Alembic setup, no create_all call);
  add app.core.db.init_db(), called from FastAPI's lifespan hook and from a
  Celery worker_process_init signal so either process can create it first
- add healthchecks + service_healthy depends_on for db/redis so api/worker
  do not race Postgres/Redis startup
- delete app/services/scheduler.py, an unused stub superseded by
  job_scheduler.py + scoring.py
- add .dockerignore
- rewrite README: architecture/request-flow diagram, Docker Compose and
  local setup instructions, documented limitations

Verified: full pytest suite (60 passed) plus a live end-to-end run against
real Postgres + Redis + Celery."
git push

echo "Done."