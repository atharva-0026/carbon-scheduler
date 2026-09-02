import ssl
from urllib.parse import urlparse

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


def _ssl_options(redis_url: str) -> dict | None:
    """SSL options for a `rediss://` URL (e.g. Upstash Redis), else None.

    Kombu/redis-py require ssl_cert_reqs to be set explicitly for a
    rediss:// connection -- without it, Celery raises
    "A rediss:// URL must have parameter ssl_cert_reqs" at startup.
    """
    if urlparse(redis_url).scheme != "rediss":
        return None
    return {"ssl_cert_reqs": ssl.CERT_REQUIRED}


celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    broker_use_ssl=_ssl_options(settings.celery_broker_url),
    redis_backend_use_ssl=_ssl_options(settings.celery_result_backend),
)


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
