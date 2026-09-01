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

Or everything via Docker:

```bash
docker compose up --build
```

## Layout

- `app/main.py` — FastAPI entrypoint
- `app/api/` — routes
- `app/models/` — SQLAlchemy models
- `app/schemas/` — Pydantic schemas
- `app/services/` — carbon intensity client, scheduling logic
- `app/celery_app.py`, `app/tasks.py` — background job execution
- `dashboard/` — Streamlit carbon-savings dashboard
- `tests/` — pytest
