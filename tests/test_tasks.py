from datetime import datetime, timezone

import pytest

import app.tasks as tasks_module
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


@pytest.fixture(autouse=True)
def patch_session(monkeypatch, test_db):
    monkeypatch.setattr(tasks_module, "SessionLocal", test_db)


def test_schedule_job_not_found():
    result = tasks_module.schedule_job(999)
    assert result == {"job_id": 999, "status": "not_found"}


def test_schedule_job_dispatches_immediately_when_delay_is_zero(monkeypatch, test_db):
    job_id = _make_job(test_db)

    fake_best = ScoredCandidate(
        candidate=ScheduleCandidate(
            region="eu-north-1", delay_minutes=0.0, carbon_intensity=50.0, latency_ms=100.0
        ),
        score=0.1,
        normalized_carbon=0.0,
        normalized_latency=0.0,
        normalized_delay=0.0,
        urgency=0.0,
    )
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(tasks_module, "choose_schedule", lambda **kwargs: (fake_best, now))

    calls = {}
    monkeypatch.setattr(tasks_module.execute_job, "delay", lambda jid: calls.setdefault("delay", jid))
    monkeypatch.setattr(
        tasks_module.execute_job, "apply_async", lambda **kw: calls.setdefault("apply_async", kw)
    )

    result = tasks_module.schedule_job(job_id)

    assert result["status"] == "scheduled"
    assert result["region"] == "eu-north-1"
    assert calls.get("delay") == job_id
    assert "apply_async" not in calls

    job = _get_job(test_db, job_id)
    assert job.status == "scheduled"
    assert job.scheduled_region == "eu-north-1"
    # SQLite has no tz-aware DATETIME type, so it round-trips as naive UTC.
    assert job.scheduled_start == now.replace(tzinfo=None)


def test_schedule_job_schedules_countdown_when_delayed(monkeypatch, test_db):
    job_id = _make_job(test_db)

    fake_best = ScoredCandidate(
        candidate=ScheduleCandidate(
            region="us-west-2", delay_minutes=120.0, carbon_intensity=80.0, latency_ms=60.0
        ),
        score=0.2,
        normalized_carbon=0.0,
        normalized_latency=0.0,
        normalized_delay=1.0,
        urgency=0.5,
    )
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(tasks_module, "choose_schedule", lambda **kwargs: (fake_best, now))

    calls = {}
    monkeypatch.setattr(tasks_module.execute_job, "delay", lambda jid: calls.setdefault("delay", jid))
    monkeypatch.setattr(
        tasks_module.execute_job,
        "apply_async",
        lambda args, countdown: calls.setdefault("apply_async", (args, countdown)),
    )

    result = tasks_module.schedule_job(job_id)

    assert result["status"] == "scheduled"
    assert "delay" not in calls
    assert calls["apply_async"] == ([job_id], 7200)  # 120 minutes -> 7200 seconds

    job = _get_job(test_db, job_id)
    assert job.status == "scheduled"
    assert job.scheduled_region == "us-west-2"


def test_schedule_job_marks_failed_when_no_regions_available(monkeypatch, test_db):
    job_id = _make_job(test_db)

    def raise_no_regions(**kwargs):
        raise NoAvailableRegionsError("all lookups failed")

    monkeypatch.setattr(tasks_module, "choose_schedule", raise_no_regions)

    result = tasks_module.schedule_job(job_id)

    assert result["status"] == "failed"
    job = _get_job(test_db, job_id)
    assert job.status == "failed"


def test_execute_job_not_found():
    result = tasks_module.execute_job(999)
    assert result == {"job_id": 999, "status": "not_found"}


def test_execute_job_dispatches_and_updates_status(monkeypatch, test_db):
    job_id = _make_job(test_db)
    session = test_db()
    job = session.query(Job).filter(Job.id == job_id).first()
    job.scheduled_region = "ap-south-1"
    session.commit()
    session.close()

    calls = {}

    def fake_dispatch(jid, region):
        calls["args"] = (jid, region)
        return ExecutionResult(job_id=jid, region=region, status="dispatched", message="ok")

    monkeypatch.setattr(tasks_module, "dispatch_job", fake_dispatch)

    result = tasks_module.execute_job(job_id)

    assert result["status"] == "dispatched"
    assert calls["args"] == (job_id, "ap-south-1")

    job = _get_job(test_db, job_id)
    assert job.status == "dispatched"
