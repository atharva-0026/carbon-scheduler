from datetime import datetime, timedelta, timezone

import pytest

from app.services.carbon_intensity import CarbonIntensityError
from app.services.job_scheduler import (
    MAX_TIME_SLOTS,
    SLOT_INTERVAL_MINUTES,
    NoAvailableRegionsError,
    build_candidates,
    candidate_delays,
    choose_schedule,
)


class FakeCarbonClient:
    """Stand-in for CarbonIntensityClient: no network, no Redis."""

    def __init__(self, mapping: dict[str, float] | None = None, fail_zones: set[str] | None = None):
        self.mapping = mapping or {}
        self.fail_zones = fail_zones or set()
        self.closed = False

    async def get_intensity(self, zone: str) -> dict:
        if zone in self.fail_zones:
            raise CarbonIntensityError(f"no data for zone {zone!r}")
        return {"carbon_intensity": self.mapping.get(zone, 300.0), "zone": zone, "cached": False}

    async def close(self) -> None:
        self.closed = True


def test_candidate_delays_no_deadline_is_immediate_only():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert candidate_delays(None, 30, now) == [0.0]


def test_candidate_delays_no_slack_is_immediate_only():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    deadline = now + timedelta(minutes=10)  # less than the 30-minute duration
    assert candidate_delays(deadline, 30, now) == [0.0]


def test_candidate_delays_spaced_within_slack():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    deadline = now + timedelta(minutes=200)  # slack = 200 - 20 = 180
    delays = candidate_delays(deadline, 20, now)
    assert delays[0] == 0.0
    assert all(d < 180 for d in delays)
    assert len(delays) <= MAX_TIME_SLOTS
    assert delays == sorted(delays)


def test_candidate_delays_caps_at_max_slots():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    deadline = now + timedelta(minutes=SLOT_INTERVAL_MINUTES * 100)
    delays = candidate_delays(deadline, 0, now)
    assert len(delays) == MAX_TIME_SLOTS


@pytest.mark.asyncio
async def test_build_candidates_skips_failing_regions():
    client = FakeCarbonClient(mapping={"US-MIDA-PJM": 400.0}, fail_zones={"IE"})
    candidates = await build_candidates(["us-east-1", "eu-west-1"], [0.0, 30.0], client=client)
    # eu-west-1 -> IE fails and is skipped entirely; us-east-1 succeeds for both delays.
    assert len(candidates) == 2
    assert all(c.region == "us-east-1" for c in candidates)
    assert {c.delay_minutes for c in candidates} == {0.0, 30.0}


def test_choose_schedule_picks_cleanest_region(monkeypatch):
    fake_client = FakeCarbonClient(
        mapping={
            "US-MIDA-PJM": 450.0,  # us-east-1
            "US-NW-PACW": 300.0,  # us-west-2
            "IE": 60.0,  # eu-west-1  <- cleanest
            "SE": 80.0,  # eu-north-1
            "IN-WE": 500.0,  # ap-south-1
        }
    )
    monkeypatch.setattr("app.services.job_scheduler.CarbonIntensityClient", lambda: fake_client)

    best, now = choose_schedule()
    assert best.candidate.region == "eu-west-1"
    assert fake_client.closed is True


def test_choose_schedule_respects_allowed_regions(monkeypatch):
    fake_client = FakeCarbonClient(mapping={"US-MIDA-PJM": 450.0, "US-NW-PACW": 100.0})
    monkeypatch.setattr("app.services.job_scheduler.CarbonIntensityClient", lambda: fake_client)

    best, _ = choose_schedule(allowed_regions=["us-east-1", "us-west-2"])
    assert best.candidate.region == "us-west-2"


def test_choose_schedule_unknown_region_raises():
    with pytest.raises(ValueError, match="Unknown region"):
        choose_schedule(allowed_regions=["mars-1"])


def test_choose_schedule_raises_when_all_regions_fail(monkeypatch):
    fake_client = FakeCarbonClient(fail_zones={"US-MIDA-PJM", "US-NW-PACW", "IE", "SE", "IN-WE"})
    monkeypatch.setattr("app.services.job_scheduler.CarbonIntensityClient", lambda: fake_client)

    with pytest.raises(NoAvailableRegionsError):
        choose_schedule()
