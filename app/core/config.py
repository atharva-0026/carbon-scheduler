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
