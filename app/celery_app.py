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
