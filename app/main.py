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
