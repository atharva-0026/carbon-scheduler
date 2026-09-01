"""Scoring engine for deferrable-job scheduling.

Combines three signals into a single, lower-is-better score for each
candidate (region, delay) pair a job could run in/at:

- carbon intensity of the candidate's region/time slot (min-max normalized
  across the candidate set)
- deadline urgency of the job (0 = no rush, 1 = must run immediately) —
  scales how heavily a candidate's delay is penalized
- cross-region latency penalty for running away from the job's home region
  (min-max normalized across the candidate set)

The candidate set is whatever the caller assembled (e.g. N regions x M time
slots from the carbon intensity client); normalization is always relative
to that set, not to any global scale.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

# Beyond this much slack (deadline minus now minus job duration), urgency is
# treated as zero — the job is fully deferrable.
DEFAULT_MAX_SLACK_MINUTES = 7 * 24 * 60.0  # 7 days


@dataclass(frozen=True)
class ScoringWeights:
    """Relative weight of each scoring signal. Need not sum to 1 — scores are
    only ever compared to each other, never against an absolute scale."""

    carbon: float = 0.5
    latency: float = 0.2
    urgency: float = 0.3

    def __post_init__(self) -> None:
        for name, value in (("carbon", self.carbon), ("latency", self.latency), ("urgency", self.urgency)):
            if value < 0:
                raise ValueError(f"scoring weight {name!r} must be >= 0, got {value}")


@dataclass(frozen=True)
class ScheduleCandidate:
    """One (region, time slot) a deferrable job could be scheduled into."""

    region: str
    delay_minutes: float  # how far from now this slot starts; 0 = run immediately
    carbon_intensity: float  # gCO2eq/kWh, from the carbon intensity client
    latency_ms: float = 0.0  # extra network latency vs. the job's home region


@dataclass(frozen=True)
class ScoredCandidate:
    candidate: ScheduleCandidate
    score: float
    normalized_carbon: float
    normalized_latency: float
    normalized_delay: float
    urgency: float


def normalize(values: list[float]) -> list[float]:
    """Min-max normalize to [0, 1]. Empty input returns []; a set of equal
    values returns all zeros (that dimension carries no signal)."""
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi == lo:
        return [0.0 for _ in values]
    span = hi - lo
    return [(v - lo) / span for v in values]


def compute_urgency(
    deadline: datetime | None,
    now: datetime | None = None,
    estimated_duration_minutes: float = 0.0,
    max_slack_minutes: float = DEFAULT_MAX_SLACK_MINUTES,
) -> float:
    """How urgent it is to run the job now, in [0, 1].

    0 = no deadline pressure at all (fully deferrable); 1 = there is no slack
    left — the job must start now to make its deadline (or the deadline has
    already passed). Scales linearly with remaining slack in between.
    """
    if deadline is None:
        return 0.0

    now = now or datetime.now(timezone.utc)
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    slack_minutes = (deadline - now).total_seconds() / 60.0 - estimated_duration_minutes

    if slack_minutes <= 0:
        return 1.0
    if slack_minutes >= max_slack_minutes:
        return 0.0
    return 1.0 - (slack_minutes / max_slack_minutes)


def score_candidates(
    candidates: list[ScheduleCandidate],
    *,
    deadline: datetime | None = None,
    estimated_duration_minutes: float = 0.0,
    now: datetime | None = None,
    weights: ScoringWeights | None = None,
    max_slack_minutes: float = DEFAULT_MAX_SLACK_MINUTES,
) -> list[ScoredCandidate]:
    """Score every candidate for one job. Lower score = better choice.

    Empty `candidates` returns an empty list.
    """
    if not candidates:
        return []

    weights = weights or ScoringWeights()
    urgency = compute_urgency(
        deadline, now=now, estimated_duration_minutes=estimated_duration_minutes,
        max_slack_minutes=max_slack_minutes,
    )

    norm_carbon = normalize([c.carbon_intensity for c in candidates])
    norm_latency = normalize([c.latency_ms for c in candidates])
    norm_delay = normalize([c.delay_minutes for c in candidates])

    scored = []
    for candidate, nc, nl, nd in zip(candidates, norm_carbon, norm_latency, norm_delay):
        score = weights.carbon * nc + weights.latency * nl + weights.urgency * urgency * nd
        scored.append(
            ScoredCandidate(
                candidate=candidate,
                score=score,
                normalized_carbon=nc,
                normalized_latency=nl,
                normalized_delay=nd,
                urgency=urgency,
            )
        )
    return scored


def pick_best_candidate(
    candidates: list[ScheduleCandidate],
    *,
    deadline: datetime | None = None,
    estimated_duration_minutes: float = 0.0,
    now: datetime | None = None,
    weights: ScoringWeights | None = None,
    max_slack_minutes: float = DEFAULT_MAX_SLACK_MINUTES,
) -> ScoredCandidate | None:
    """Score every candidate and return the best (lowest-score) one, or None
    if `candidates` is empty."""
    scored = score_candidates(
        candidates,
        deadline=deadline,
        estimated_duration_minutes=estimated_duration_minutes,
        now=now,
        weights=weights,
        max_slack_minutes=max_slack_minutes,
    )
    if not scored:
        return None
    return min(scored, key=lambda s: s.score)
