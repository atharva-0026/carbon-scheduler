from datetime import datetime, timedelta, timezone

import pytest

from app.services.scoring import (
    DEFAULT_MAX_SLACK_MINUTES,
    ScheduleCandidate,
    ScoringWeights,
    compute_urgency,
    normalize,
    pick_best_candidate,
    score_candidates,
)


def test_normalize_spreads_values_to_unit_range():
    assert normalize([100.0, 200.0, 300.0]) == [0.0, 0.5, 1.0]


def test_normalize_empty_list():
    assert normalize([]) == []


def test_normalize_single_value():
    assert normalize([42.0]) == [0.0]


def test_normalize_all_equal_values_returns_zeros():
    assert normalize([50.0, 50.0, 50.0]) == [0.0, 0.0, 0.0]


def test_compute_urgency_no_deadline_is_zero():
    assert compute_urgency(None) == 0.0


def test_compute_urgency_past_deadline_is_max():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    deadline = now - timedelta(hours=1)
    assert compute_urgency(deadline, now=now, estimated_duration_minutes=30) == 1.0


def test_compute_urgency_no_slack_is_max():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    deadline = now + timedelta(minutes=30)
    # exactly enough time to run, no slack left over
    assert compute_urgency(deadline, now=now, estimated_duration_minutes=30) == 1.0


def test_compute_urgency_far_future_deadline_is_zero():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    deadline = now + timedelta(minutes=DEFAULT_MAX_SLACK_MINUTES * 10)
    assert compute_urgency(deadline, now=now, estimated_duration_minutes=0) == 0.0


def test_compute_urgency_scales_linearly_with_slack():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    half_slack = DEFAULT_MAX_SLACK_MINUTES / 2
    deadline = now + timedelta(minutes=half_slack)
    urgency = compute_urgency(deadline, now=now, estimated_duration_minutes=0)
    assert urgency == pytest.approx(0.5)


def test_compute_urgency_handles_naive_datetimes():
    now = datetime(2026, 1, 1)  # naive
    deadline = now - timedelta(hours=1)  # naive
    assert compute_urgency(deadline, now=now) == 1.0


def test_scoring_weights_reject_negative():
    with pytest.raises(ValueError):
        ScoringWeights(carbon=-0.1)


def test_score_candidates_empty_returns_empty():
    assert score_candidates([]) == []


def test_score_candidates_picks_cleanest_region_when_not_urgent():
    candidates = [
        ScheduleCandidate(region="us-east", delay_minutes=0, carbon_intensity=500, latency_ms=0),
        ScheduleCandidate(region="eu-north", delay_minutes=60, carbon_intensity=50, latency_ms=40),
    ]
    # No deadline -> zero urgency -> delay shouldn't matter, carbon should dominate.
    best = pick_best_candidate(candidates)
    assert best.candidate.region == "eu-north"
    assert best.urgency == 0.0


def test_score_candidates_prefers_immediate_slot_when_urgent():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    deadline = now + timedelta(minutes=5)  # exactly enough time, zero slack -> urgency 1.0
    candidates = [
        ScheduleCandidate(region="us-east", delay_minutes=0, carbon_intensity=500, latency_ms=0),
        ScheduleCandidate(region="eu-north", delay_minutes=120, carbon_intensity=50, latency_ms=40),
    ]
    # Weight urgency heavily so a tight deadline clearly overrides the carbon advantage.
    weights = ScoringWeights(carbon=0.3, latency=0.2, urgency=0.5)
    best = pick_best_candidate(
        candidates,
        deadline=deadline,
        estimated_duration_minutes=5,
        now=now,
        weights=weights,
    )
    assert best.candidate.region == "us-east"
    assert best.urgency == 1.0


def test_latency_penalty_breaks_ties_between_equal_carbon_candidates():
    candidates = [
        ScheduleCandidate(region="home", delay_minutes=0, carbon_intensity=200, latency_ms=0),
        ScheduleCandidate(region="far", delay_minutes=0, carbon_intensity=200, latency_ms=150),
    ]
    best = pick_best_candidate(candidates)
    assert best.candidate.region == "home"
    assert best.normalized_latency == 0.0


def test_custom_weights_change_the_outcome():
    candidates = [
        ScheduleCandidate(region="clean-but-far", delay_minutes=0, carbon_intensity=10, latency_ms=200),
        ScheduleCandidate(region="dirty-but-close", delay_minutes=0, carbon_intensity=490, latency_ms=0),
    ]
    carbon_first = pick_best_candidate(candidates, weights=ScoringWeights(carbon=1.0, latency=0.0, urgency=0.0))
    assert carbon_first.candidate.region == "clean-but-far"

    latency_first = pick_best_candidate(candidates, weights=ScoringWeights(carbon=0.0, latency=1.0, urgency=0.0))
    assert latency_first.candidate.region == "dirty-but-close"


def test_pick_best_candidate_none_for_empty_list():
    assert pick_best_candidate([]) is None


def test_score_candidates_returns_one_scored_result_per_candidate():
    candidates = [
        ScheduleCandidate(region="a", delay_minutes=0, carbon_intensity=100),
        ScheduleCandidate(region="b", delay_minutes=30, carbon_intensity=200),
        ScheduleCandidate(region="c", delay_minutes=60, carbon_intensity=300),
    ]
    scored = score_candidates(candidates)
    assert len(scored) == 3
    assert all(0.0 <= s.score <= max(w.carbon, w.latency, w.urgency) + 1 for s, w in zip(scored, [ScoringWeights()] * 3))
    # Cleanest, no-delay candidate should score lowest with default weights and no urgency.
    assert scored[0].score < scored[2].score
