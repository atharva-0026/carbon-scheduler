#!/usr/bin/env bash
set -euo pipefail

# Run this from inside your carbon-scheduler repo root.

# Clean up delivery scripts from previous rounds that got swept into the
# repo by 'git add -A' -- harmless, just noise.
rm -f push_docker_fix.sh push_upstash_render.sh push_runtime_txt.sh push_render_python_version.sh

mkdir -p "$(dirname ".env.example")"
cat > .env.example << 'PYEOF__ENV_EXAMPLE'
APP_ENV=development
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/carbon_scheduler

# Set to true only on a deployment with no Celery worker running (e.g.
# Render's free tier, which doesn't support background worker services) --
# /submit-job then schedules+dispatches jobs in-process via FastAPI
# BackgroundTasks instead of enqueuing a Celery task. Leave false anywhere
# a real worker is running (Docker Compose, a paid Render plan, ...); see
# the README's "Production architecture vs. free-tier demo" section.
USE_INPROCESS_WORKER=false

# Local dev / docker compose: a plain local Redis, one DB per use.
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/1

# Production (Render, etc.): point all three at the same Upstash Redis
# database instead -- Upstash is TLS-only (rediss://) and only gives you
# database 0 (no /1, /2 selectors), which is fine here since the cache
# keys ("carbon_intensity:*"), Celery broker queue, and Celery result keys
# ("celery-task-meta-*") don't collide. Get the connection string from the
# Upstash console (Redis tab -> "Connect" -> the rediss:// URL, which
# already embeds the password).
# REDIS_URL=rediss://default:<password>@<endpoint>.upstash.io:6379
# CELERY_BROKER_URL=rediss://default:<password>@<endpoint>.upstash.io:6379
# CELERY_RESULT_BACKEND=rediss://default:<password>@<endpoint>.upstash.io:6379

# ElectricityMaps (https://www.electricitymaps.com/) — get a key at
# https://api-portal.electricitymaps.com/
ELECTRICITYMAPS_API_KEY=
ELECTRICITYMAPS_BASE_URL=https://api.electricitymap.org/v3
# How long to cache carbon intensity lookups in Redis, in seconds
CARBON_CACHE_TTL_SECONDS=900

WATTTIME_USERNAME=
WATTTIME_PASSWORD=
PYEOF__ENV_EXAMPLE

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

FastAPI · Celery · Redis (broker + result backend + carbon-intensity cache;
[Upstash](https://upstash.com/) in production) · Postgres (Neon or Render
Postgres in production) · SQLAlchemy · Streamlit + Plotly · Docker Compose ·
deployed on [Render](https://render.com/) (`render.yaml`).

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

## Production architecture vs. free-tier demo

The architecture above — FastAPI enqueues, a separate Celery worker
schedules and dispatches — is what this project actually runs everywhere
that has a worker process: Docker Compose, or a paid Render plan. That's
the design worth judging the project on: the API stays fast (it just
writes a row and enqueues), scheduling work is retried/queued/scaled
independently of request traffic, and a slot 3 hours from now is a real
`apply_async(countdown=...)`, not something the request process has to
sit around waiting for.

Render's **free tier**, though, only runs web services — there's no
`type: worker` background process available without upgrading. Rather than
have the hosted demo silently do nothing when a job is submitted,
`render.yaml`'s single web service sets `USE_INPROCESS_WORKER=true`, which
makes `POST /submit-job` (`app/api/scheduling.py`) run the same scoring
decision and the same stub dispatch itself — via FastAPI's `BackgroundTasks`,
after the response is sent — instead of enqueuing a Celery task
(`app/services/inprocess_worker.py`). No separate worker process, no Redis
broker/backend involved in that path at all.

This fallback is honestly worse in one specific way: it can't honor a
delayed start time. Celery's `countdown` lets a separate process wake up
later; a web request process has no equivalent without blocking a thread
for however long the delay is, which isn't reasonable on a single
free-tier instance. So on the hosted demo, jobs schedule then dispatch
immediately, whatever region the scoring engine picked — real carbon-aware
*region* selection, just not real deadline-aware *deferral*. Locally, in
Docker Compose, or on a paid Render plan with a worker service, leave
`USE_INPROCESS_WORKER` unset (default `false`) and you get the full
Celery pipeline, countdown included.

## Deploying to Render

`render.yaml` (a [Render Blueprint](https://render.com/docs/blueprint-spec))
defines one service: `carbon-scheduler-api`, a free-tier web service
running the FastAPI app with `USE_INPROCESS_WORKER=true` (see above — no
worker service is defined, since the free tier can't run one).

1. Push this repo to GitHub, then in the Render dashboard: **New +** ->
   **Blueprint**, point it at the repo. It reads `render.yaml` and creates
   the service.
2. Set `DATABASE_URL` (Render only lets you fill in `sync: false` vars
   per-service): a [Neon](https://neon.tech/) or
   [Render Postgres](https://render.com/docs/postgresql) connection
   string. Either works — this project only needs a plain `DATABASE_URL`,
   not a Render-managed database resource, so Neon's free tier is a fine
   default.
3. Set `REDIS_URL` to an [Upstash Redis](https://upstash.com/) `rediss://`
   connection string (see `.env.example`) — still used for the
   ElectricityMaps cache even though Celery isn't in the picture here.
   `CELERY_BROKER_URL`/`CELERY_RESULT_BACKEND` can be left blank on the
   free tier; they're unused while `USE_INPROCESS_WORKER=true`.
4. Set `ELECTRICITYMAPS_API_KEY` if you want live scheduling to work in
   production (see Limitations).

Tables are created automatically on first boot (same `init_db()` as
Docker Compose), so there's no separate migration step.

**On a paid Render plan**, `render.yaml` has a commented-out `worker`
service block ready to uncomment — add it back, set
`USE_INPROCESS_WORKER=false` (or remove it) on the web service, and fill
in `CELERY_BROKER_URL`/`CELERY_RESULT_BACKEND` with the same Upstash URL as
`REDIS_URL`, to get the real Celery pipeline in production.

The Streamlit dashboard isn't part of `render.yaml` either — it's meant to
be run locally against a copy of production data, or added as its own
Render web service (`streamlit run dashboard/app.py --server.port $PORT --server.address 0.0.0.0`)
the same way as the API, if you want it hosted too.

## Redis: Upstash in production, plain Redis locally

`REDIS_URL` (the ElectricityMaps cache), `CELERY_BROKER_URL`, and
`CELERY_RESULT_BACKEND` are three separate settings but don't have to point
at three separate databases. Locally / in Docker Compose they're the same
container on different logical DBs (`/0`, `/1`); in production they're
[Upstash Redis](https://upstash.com/), which only offers DB 0, so all three
point at the identical `rediss://` URL — safe, because the three use
non-overlapping key prefixes (`carbon_intensity:*`, Celery's queue keys,
and `celery-task-meta-*`). `app/celery_app.py` detects the `rediss://`
scheme and sets the `ssl_cert_reqs` option Celery/Kombu requires for it
(without this, Celery refuses to start against a TLS Redis URL); the
carbon-intensity client's `redis.asyncio.from_url()` already defaults to
TLS-verified for `rediss://` and needed no change.

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
- **The free-tier demo doesn't honor delayed scheduling.** With
  `USE_INPROCESS_WORKER=true` (only set on the hosted Render free-tier
  deploy), `app/services/inprocess_worker.py` dispatches immediately after
  scheduling instead of waiting for the chosen start time — see
  "Production architecture vs. free-tier demo" above for why, and what
  still works despite it (region selection) vs. what doesn't (deferral).
PYEOF_README_MD

mkdir -p "$(dirname "app/api/scheduling.py")"
cat > app/api/scheduling.py << 'PYEOF_APP_API_SCHEDULING_PY'
import json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.regions import available_regions
from app.models.job import Job
from app.schemas.job import JobSubmitRequest, JobSubmitResponse
from app.services.inprocess_worker import run_job_pipeline_sync
from app.tasks import schedule_job

router = APIRouter(tags=["scheduling"])


@router.post("/submit-job", response_model=JobSubmitResponse)
def submit_job(
    payload: JobSubmitRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> JobSubmitResponse:
    """Accept a deferrable job and hand it to the scheduler.

    Persists the job as "pending", then either enqueues the `schedule_job`
    Celery task (picks a region/time slot via the scoring engine and
    dispatches it -- the normal path, when a Celery worker is running) or,
    if `settings.use_inprocess_worker` is set (no worker available, e.g.
    Render's free tier), runs the same decision + dispatch itself via
    FastAPI `BackgroundTasks` after the response is sent. See
    app/services/inprocess_worker.py for what that fallback doesn't do
    (no delayed/countdown dispatch).
    """
    if payload.allowed_regions:
        unknown = [r for r in payload.allowed_regions if r not in available_regions()]
        if unknown:
            raise HTTPException(status_code=422, detail=f"Unknown region(s): {unknown}")

    job = Job(
        name=payload.name or payload.job_type,
        job_type=payload.job_type,
        estimated_duration_minutes=payload.estimated_duration_minutes,
        deadline=payload.deadline,
        allowed_regions=json.dumps(payload.allowed_regions) if payload.allowed_regions else None,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    if get_settings().use_inprocess_worker:
        background_tasks.add_task(run_job_pipeline_sync, job.id)
        task_id = f"inprocess-{job.id}"
    else:
        task_id = schedule_job.delay(job.id).id

    return JobSubmitResponse(
        **{
            "id": job.id,
            "name": job.name,
            "job_type": job.job_type,
            "status": job.status,
            "estimated_duration_minutes": job.estimated_duration_minutes,
            "deadline": job.deadline,
            "scheduled_region": job.scheduled_region,
            "scheduled_start": job.scheduled_start,
            "created_at": job.created_at,
        },
        task_id=task_id,
    )
PYEOF_APP_API_SCHEDULING_PY

mkdir -p "$(dirname "app/core/config.py")"
cat > app/core/config.py << 'PYEOF_APP_CORE_CONFIG_PY'
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    database_url: str = "postgresql://postgres:postgres@localhost:5432/carbon_scheduler"
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    electricitymaps_api_key: str | None = None
    electricitymaps_base_url: str = "https://api.electricitymap.org/v3"
    carbon_cache_ttl_seconds: int = 900  # 15 minutes

    watttime_username: str | None = None
    watttime_password: str | None = None

    # True on deployments with no Celery worker (e.g. Render's free tier,
    # which doesn't support background worker services): /submit-job runs
    # the scheduling+dispatch pipeline in-process via FastAPI BackgroundTasks
    # instead of enqueuing a Celery task. See app/services/inprocess_worker.py
    # and the README's "Production architecture vs. free-tier demo" section.
    use_inprocess_worker: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
PYEOF_APP_CORE_CONFIG_PY

mkdir -p "$(dirname "app/services/inprocess_worker.py")"
cat > app/services/inprocess_worker.py << 'PYEOF_APP_SERVICES_INPROCESS_WORKER_PY'
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
PYEOF_APP_SERVICES_INPROCESS_WORKER_PY

mkdir -p "$(dirname "render.yaml")"
cat > render.yaml << 'PYEOF_RENDER_YAML'
services:
  - type: web
    name: carbon-scheduler-api
    runtime: python
    plan: free
    buildCommand: pip install -r requirements.txt
    startCommand: uvicorn app.main:app --host 0.0.0.0 --port $PORT
    healthCheckPath: /health
    envVars:
      - key: PYTHON_VERSION
        value: 3.11.9
      - key: APP_ENV
        value: production
      # Render's free tier only runs web services -- no `type: worker`
      # background process, so there's no Celery worker to consume the
      # queue /submit-job normally enqueues to. This makes the API run
      # the scheduling+dispatch pipeline in-process instead (see
      # app/services/inprocess_worker.py and the README). On a paid plan
      # with a real worker service, set this back to "false" (or unset
      # it -- false is the default).
      - key: USE_INPROCESS_WORKER
        value: "true"
      # Neon or Render Postgres -- paste either provider's connection
      # string here (Render Postgres: the "External Database URL" from its
      # dashboard; Neon: the connection string from its dashboard, keep
      # "?sslmode=require" if Neon includes it).
      - key: DATABASE_URL
        sync: false
      # Upstash Redis (rediss://...) -- still used for the ElectricityMaps
      # cache even in in-process mode. CELERY_BROKER_URL/RESULT_BACKEND
      # aren't read anywhere while USE_INPROCESS_WORKER=true, but are left
      # here so flipping back to a real Celery worker (see README) is a
      # one-line change, not a re-add of every var.
      - key: REDIS_URL
        sync: false
      - key: CELERY_BROKER_URL
        sync: false
      - key: CELERY_RESULT_BACKEND
        sync: false
      - key: ELECTRICITYMAPS_API_KEY
        sync: false
      - key: ELECTRICITYMAPS_BASE_URL
        value: https://api.electricitymap.org/v3
      - key: CARBON_CACHE_TTL_SECONDS
        value: "900"
      - key: WATTTIME_USERNAME
        sync: false
      - key: WATTTIME_PASSWORD
        sync: false

# No worker service: Render's free tier doesn't support `type: worker`
# background services (that needs a paid plan). If you upgrade, add one
# back here --
#
#   - type: worker
#     name: carbon-scheduler-worker
#     runtime: python
#     plan: starter  # or another paid plan; free doesn't support workers
#     buildCommand: pip install -r requirements.txt
#     startCommand: celery -A app.celery_app.celery_app worker --loglevel=info
#     envVars: [same keys as carbon-scheduler-api above, minus USE_INPROCESS_WORKER]
#
# and set USE_INPROCESS_WORKER to "false" (or remove it) on the web
# service so /submit-job goes back to enqueuing Celery tasks instead of
# running the pipeline in-process.
PYEOF_RENDER_YAML

mkdir -p "$(dirname "tests/test_inprocess_worker.py")"
cat > tests/test_inprocess_worker.py << 'PYEOF_TESTS_TEST_INPROCESS_WORKER_PY'
from datetime import datetime, timezone

import app.services.inprocess_worker as inprocess_module
from app.models.job import Job
from app.services.execution_adapter import ExecutionResult
from app.services.job_scheduler import NoAvailableRegionsError
from app.services.scoring import ScheduleCandidate, ScoredCandidate


def _make_job(session_factory, **overrides) -> int:
    session = session_factory()
    job = Job(
        name=overrides.get("name", "test-job"),
        job_type=overrides.get("job_type", "batch"),
        estimated_duration_minutes=overrides.get("estimated_duration_minutes", 30),
        deadline=overrides.get("deadline"),
        allowed_regions=overrides.get("allowed_regions"),
    )
    session.add(job)
    session.commit()
    job_id = job.id
    session.close()
    return job_id


def _get_job(session_factory, job_id: int) -> Job:
    session = session_factory()
    job = session.query(Job).filter(Job.id == job_id).first()
    session.expunge(job)
    session.close()
    return job


def test_run_job_pipeline_sync_not_found(monkeypatch, test_db):
    monkeypatch.setattr(inprocess_module, "SessionLocal", test_db)
    result = inprocess_module.run_job_pipeline_sync(999)
    assert result == {"job_id": 999, "status": "not_found"}


def test_run_job_pipeline_sync_schedules_and_dispatches_immediately(monkeypatch, test_db):
    monkeypatch.setattr(inprocess_module, "SessionLocal", test_db)
    job_id = _make_job(test_db)

    fake_best = ScoredCandidate(
        candidate=ScheduleCandidate(
            region="eu-north-1", delay_minutes=120.0, carbon_intensity=50.0, latency_ms=100.0
        ),
        score=0.1,
        normalized_carbon=0.0,
        normalized_latency=0.0,
        normalized_delay=0.0,
        urgency=0.0,
    )
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(inprocess_module, "choose_schedule", lambda **kwargs: (fake_best, now))
    monkeypatch.setattr(inprocess_module, "fetch_baseline_intensity", lambda: 420.0)

    calls = {}

    def fake_dispatch(jid, region):
        calls["args"] = (jid, region)
        return ExecutionResult(job_id=jid, region=region, status="dispatched", message="ok")

    monkeypatch.setattr(inprocess_module, "dispatch_job", fake_dispatch)

    result = inprocess_module.run_job_pipeline_sync(job_id)

    # Dispatches right away even though delay_minutes=120 -- no countdown
    # mechanism without Celery, documented in the module docstring.
    assert calls["args"] == (job_id, "eu-north-1")
    assert result["status"] == "dispatched"

    job = _get_job(test_db, job_id)
    assert job.status == "dispatched"
    assert job.scheduled_region == "eu-north-1"
    assert job.carbon_intensity_actual == 50.0
    assert job.carbon_intensity_baseline == 420.0


def test_run_job_pipeline_sync_marks_failed_when_no_regions_available(monkeypatch, test_db):
    monkeypatch.setattr(inprocess_module, "SessionLocal", test_db)
    job_id = _make_job(test_db)

    def raise_no_regions(**kwargs):
        raise NoAvailableRegionsError("all lookups failed")

    monkeypatch.setattr(inprocess_module, "choose_schedule", raise_no_regions)

    result = inprocess_module.run_job_pipeline_sync(job_id)

    assert result["status"] == "failed"
    job = _get_job(test_db, job_id)
    assert job.status == "failed"
PYEOF_TESTS_TEST_INPROCESS_WORKER_PY

mkdir -p "$(dirname "tests/test_submit_job_api.py")"
cat > tests/test_submit_job_api.py << 'PYEOF_TESTS_TEST_SUBMIT_JOB_API_PY'
from fastapi.testclient import TestClient

import app.api.scheduling as scheduling_module
from app.core.config import get_settings
from app.core.db import get_db
from app.main import app


class FakeAsyncResult:
    def __init__(self, task_id: str):
        self.id = task_id


def _override_get_db(session_factory):
    def _override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    return _override


def test_submit_job_creates_job_and_enqueues_scheduling_task(monkeypatch, test_db):
    app.dependency_overrides[get_db] = _override_get_db(test_db)
    monkeypatch.setattr(
        scheduling_module.schedule_job, "delay", lambda job_id: FakeAsyncResult("fake-task-123")
    )

    try:
        client = TestClient(app)
        response = client.post(
            "/submit-job",
            json={
                "job_type": "ml-training",
                "estimated_duration_minutes": 45,
                "allowed_regions": ["us-east-1", "eu-west-1"],
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["job_type"] == "ml-training"
        assert body["status"] == "pending"
        assert body["task_id"] == "fake-task-123"
        assert body["id"] is not None
        assert body["scheduled_region"] is None
    finally:
        app.dependency_overrides.clear()


def test_submit_job_defaults_name_to_job_type(monkeypatch, test_db):
    app.dependency_overrides[get_db] = _override_get_db(test_db)
    monkeypatch.setattr(
        scheduling_module.schedule_job, "delay", lambda job_id: FakeAsyncResult("fake-task-456")
    )

    try:
        client = TestClient(app)
        response = client.post(
            "/submit-job",
            json={"job_type": "ci-run", "estimated_duration_minutes": 10},
        )
        assert response.status_code == 200
        assert response.json()["name"] == "ci-run"
    finally:
        app.dependency_overrides.clear()


def test_submit_job_uses_inprocess_worker_when_configured(monkeypatch, test_db):
    # Simulates a deployment with no Celery worker (e.g. Render's free
    # tier): USE_INPROCESS_WORKER=true should run the pipeline via
    # BackgroundTasks instead of enqueuing a Celery task.
    monkeypatch.setenv("USE_INPROCESS_WORKER", "true")
    get_settings.cache_clear()

    app.dependency_overrides[get_db] = _override_get_db(test_db)

    calls = {}
    monkeypatch.setattr(
        scheduling_module, "run_job_pipeline_sync", lambda job_id: calls.setdefault("job_id", job_id)
    )
    # Should NOT be reached in this mode -- fail loudly if it is.
    monkeypatch.setattr(
        scheduling_module.schedule_job,
        "delay",
        lambda job_id: (_ for _ in ()).throw(AssertionError("Celery path used instead of in-process")),
    )

    try:
        client = TestClient(app)
        response = client.post(
            "/submit-job",
            json={"job_type": "batch", "estimated_duration_minutes": 10},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["task_id"] == f"inprocess-{body['id']}"
        assert calls["job_id"] == body["id"]
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


def test_submit_job_rejects_unknown_region(test_db):
    app.dependency_overrides[get_db] = _override_get_db(test_db)

    try:
        client = TestClient(app)
        response = client.post(
            "/submit-job",
            json={
                "job_type": "batch",
                "estimated_duration_minutes": 10,
                "allowed_regions": ["mars-1"],
            },
        )
        assert response.status_code == 422
        assert "mars-1" in response.json()["detail"]
    finally:
        app.dependency_overrides.clear()
PYEOF_TESTS_TEST_SUBMIT_JOB_API_PY

git add -A
git commit -F - << 'PYEOF_COMMIT_MSG'
Remove Render worker service, add in-process BackgroundTasks fallback

Render's free tier only runs web services -- no background worker
process type -- so the worker service added earlier can't actually run
there; drop it from render.yaml (kept as a commented-out block for
when/if upgrading to a paid plan).

In its place: when USE_INPROCESS_WORKER=true (set on the free-tier web
service in render.yaml), POST /submit-job runs the scheduling decision
and stub dispatch itself via FastAPI BackgroundTasks after the response
is sent, instead of enqueuing a Celery task -- app/services/
inprocess_worker.py, mirroring app/tasks.py's glue without touching it.
One real gap vs. the Celery path: no delayed/countdown dispatch (jobs
dispatch immediately after scheduling), documented in the README and in
the module's docstring.

The real Celery+Redis pipeline (Docker Compose, or Render on a paid plan)
is unchanged and still the reference architecture -- README now has a
'Production architecture vs. free-tier demo' section explaining the
tradeoff plainly instead of pretending the fallback is equivalent.

Verified: full pytest suite (66 passed, 4 new), plus booting the API with
USE_INPROCESS_WORKER=true against real Postgres/Redis and zero Celery
worker processes running, and confirming POST /submit-job actually
schedules+dispatches synchronously (task_id comes back as 'inprocess-N').

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PNVLXn3HXkxXNZ8SDteenK
PYEOF_COMMIT_MSG
git push

echo "Done."