"""Turns a submitted job into a concrete (region, start time) decision.

Fetches live carbon intensity for each candidate region, builds a set of
(region, delay) candidates via the scoring engine (app.services.scoring),
and returns the best one within the job's deadline.

NOTE: candidate time slots currently all use the *same* live carbon
intensity reading per region (ElectricityMaps' free tier only exposes
"latest", not a forecast), so today the region axis carries the real
signal and the delay axis exists mainly to exercise the urgency/latency
tradeoff end-to-end. Swap in a forecast-capable client and this slots in
without changing the scoring engine or the Celery tasks.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from app.core.regions import available_regions, latency_ms_from_home, zone_for_region
from app.services.carbon_intensity import CarbonIntensityClient, CarbonIntensityError
from app.services.scoring import ScheduleCandidate, ScoredCandidate, ScoringWeights, pick_best_candidate

logger = logging.getLogger(__name__)

SLOT_INTERVAL_MINUTES = 60.0
MAX_TIME_SLOTS = 6


class NoAvailableRegionsError(Exception):
    """Raised when no candidate region/time slot could be scored at all."""


def candidate_delays(
    deadline: datetime | None,
    estimated_duration_minutes: float,
    now: datetime,
) -> list[float]:
    """Minutes-from-now offsets to consider scheduling at.

    Always includes 0 (run immediately). With a deadline and slack to spare,
    adds up to MAX_TIME_SLOTS more offsets spaced SLOT_INTERVAL_MINUTES apart,
    never exceeding the available slack.
    """
    if deadline is None:
        return [0.0]

    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    slack_minutes = (deadline - now).total_seconds() / 60.0 - estimated_duration_minutes
    if slack_minutes <= 0:
        return [0.0]

    delays = [0.0]
    step = min(SLOT_INTERVAL_MINUTES, slack_minutes)
    next_delay = step
    while next_delay < slack_minutes and len(delays) < MAX_TIME_SLOTS:
        delays.append(next_delay)
        next_delay += step
    return delays


async def build_candidates(
    regions: list[str],
    delays: list[float],
    client: CarbonIntensityClient | None = None,
) -> list[ScheduleCandidate]:
    """Fetch carbon intensity for each region and expand into ScheduleCandidates.

    Regions whose carbon-intensity lookup fails are logged and skipped
    rather than failing the whole batch.
    """
    owns_client = client is None
    client = client or CarbonIntensityClient()
    candidates: list[ScheduleCandidate] = []
    try:
        for region in regions:
            try:
                zone = zone_for_region(region)
                intensity_data = await client.get_intensity(zone)
            except (CarbonIntensityError, ValueError) as exc:
                logger.warning("Skipping region %s: %s", region, exc)
                continue

            latency = latency_ms_from_home(region)
            for delay in delays:
                candidates.append(
                    ScheduleCandidate(
                        region=region,
                        delay_minutes=delay,
                        carbon_intensity=intensity_data["carbon_intensity"],
                        latency_ms=latency,
                    )
                )
    finally:
        if owns_client:
            await client.close()

    return candidates


def choose_schedule(
    *,
    allowed_regions: list[str] | None = None,
    deadline: datetime | None = None,
    estimated_duration_minutes: float = 0.0,
    now: datetime | None = None,
    weights: ScoringWeights | None = None,
) -> tuple[ScoredCandidate, datetime]:
    """Pick the best (region, start time) for a job.

    Returns (best_scored_candidate, `now` used for the computation).
    Raises ValueError for an unknown region in `allowed_regions`, and
    NoAvailableRegionsError if every candidate region's carbon-intensity
    lookup failed.
    """
    now = now or datetime.now(timezone.utc)
    regions = allowed_regions or available_regions()

    unknown = [r for r in regions if r not in available_regions()]
    if unknown:
        raise ValueError(f"Unknown region constraint(s): {unknown}")

    delays = candidate_delays(deadline, estimated_duration_minutes, now)
    candidates = asyncio.run(build_candidates(regions, delays))

    if not candidates:
        raise NoAvailableRegionsError(
            "No candidate region/time slot could be scored "
            "(carbon intensity lookups failed for every allowed region)"
        )

    best = pick_best_candidate(
        candidates,
        deadline=deadline,
        estimated_duration_minutes=estimated_duration_minutes,
        now=now,
        weights=weights,
    )
    assert best is not None  # candidates is non-empty here
    return best, now
