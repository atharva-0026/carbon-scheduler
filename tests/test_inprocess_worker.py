from datetime import datetime, timezone

import app.services.inprocess_worker as inprocess_module
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


def test_run_job_pipeline_sync_not_found(monkeypatch, test_db):
    monkeypatch.setattr(inprocess_module, "SessionLocal", test_db)
    result = inprocess_module.run_job_pipeline_sync(999)
    assert result == {"job_id": 999, "status": "not_found"}


def test_run_job_pipeline_sync_schedules_and_dispatches_immediately(monkeypatch, test_db):
    monkeypatch.setattr(inprocess_module, "SessionLocal", test_db)
    job_id = _make_job(test_db)

    fake_best = ScoredCandidate(
        candidate=ScheduleCandidate(
            region="eu-north-1", delay_minutes=120.0, carbon_intensity=50.0, latency_ms=100.0
        ),
        score=0.1,
        normalized_carbon=0.0,
        normalized_latency=0.0,
        normalized_delay=0.0,
        urgency=0.0,
    )
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(inprocess_module, "choose_schedule", lambda **kwargs: (fake_best, now))
    monkeypatch.setattr(inprocess_module, "fetch_baseline_intensity", lambda: 420.0)

    calls = {}

    def fake_dispatch(jid, region):
        calls["args"] = (jid, region)
        return ExecutionResult(job_id=jid, region=region, status="dispatched", message="ok")

    monkeypatch.setattr(inprocess_module, "dispatch_job", fake_dispatch)

    result = inprocess_module.run_job_pipeline_sync(job_id)

    # Dispatches right away even though delay_minutes=120 -- no countdown
    # mechanism without Celery, documented in the module docstring.
    assert calls["args"] == (job_id, "eu-north-1")
    assert result["status"] == "dispatched"

    job = _get_job(test_db, job_id)
    assert job.status == "dispatched"
    assert job.scheduled_region == "eu-north-1"
    assert job.carbon_intensity_actual == 50.0
    assert job.carbon_intensity_baseline == 420.0


def test_run_job_pipeline_sync_marks_failed_when_no_regions_available(monkeypatch, test_db):
    monkeypatch.setattr(inprocess_module, "SessionLocal", test_db)
    job_id = _make_job(test_db)

    def raise_no_regions(**kwargs):
        raise NoAvailableRegionsError("all lookups failed")

    monkeypatch.setattr(inprocess_module, "choose_schedule", raise_no_regions)

    result = inprocess_module.run_job_pipeline_sync(job_id)

    assert result["status"] == "failed"
    job = _get_job(test_db, job_id)
    assert job.status == "failed"
