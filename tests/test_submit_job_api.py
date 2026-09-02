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
