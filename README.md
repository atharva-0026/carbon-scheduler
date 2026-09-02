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
