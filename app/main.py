from fastapi import FastAPI

from app.api import health, jobs

app = FastAPI(title="Carbon-Aware Compute Scheduler", version="0.1.0")

app.include_router(health.router)
app.include_router(jobs.router)


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "carbon-aware-compute-scheduler"}
