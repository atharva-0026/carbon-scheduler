from app.services.execution_adapter import dispatch_job


def test_dispatch_job_returns_dispatched_result():
    result = dispatch_job(42, "eu-west-1")
    assert result.job_id == 42
    assert result.region == "eu-west-1"
    assert result.status == "dispatched"
    assert "42" in result.message
    assert "eu-west-1" in result.message
