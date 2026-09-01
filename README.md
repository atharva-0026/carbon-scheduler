# Carbon-Aware Compute Scheduler

Reschedules deferrable compute jobs (batch, ML training, CI) to run in cleaner
grid regions/time windows, using real-time carbon intensity data.

## Stack

FastAPI, Redis, Celery, Postgres, Docker, Streamlit dashboard. Deployed on Railway.

## Local dev

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

uvicorn app.main:app --reload
```

Celery worker:

```bash
celery -A app.celery_app.celery_app worker --loglevel=info
```

Dashboard:

```bash
streamlit run dashboard/app.py
```

The dashboard needs no live services to demo: if it can't reach Postgres (or
the `jobs` table is empty) it falls back to the bundled sample dataset at
`dashboard/sample_data.csv`. To seed realistic-looking job history —
Postgres, the CSV fallback, or both:

```bash
python scripts/seed_data.py             # seeds Postgres and refreshes the CSV
python scripts/seed_data.py --target csv  # just refresh dashboard/sample_data.csv
```

Or everything via Docker:

```bash
docker compose up --build
```

## Layout

- `app/main.py` — FastAPI entrypoint
- `app/api/` — routes, including `POST /submit-job`
- `app/models/` — SQLAlchemy models
- `app/schemas/` — Pydantic schemas
- `app/services/` — carbon intensity client, scoring engine, job scheduler, execution adapter
- `app/core/` — config, DB session, region catalog, carbon/cost math
- `app/celery_app.py`, `app/tasks.py` — background job scheduling/execution
- `dashboard/` — Streamlit carbon-savings dashboard + its data loading (`dashboard/data.py`)
- `scripts/seed_data.py` — generates demo job history (Postgres and/or the dashboard's CSV fallback)
- `tests/` — pytest
