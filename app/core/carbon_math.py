"""Shared math for turning a scheduling decision into demo-friendly numbers:
grams of CO2 and dollars, for the dashboard's "carbon saved" and "cost
tradeoff" views.

These are simplifying assumptions for a portfolio demo, not measured power
draw or real cloud pricing:
- every job is assumed to draw AVG_JOB_POWER_KW continuously for its
  estimated duration (no per-job-type profiling)
- REGION_COST_PER_HOUR is a made-up flat hourly compute rate per region,
  not a real cloud price list
"""

from __future__ import annotations

AVG_JOB_POWER_KW = 0.5  # ~500W, a rough single-node compute draw

REGION_COST_PER_HOUR: dict[str, float] = {
    "us-east-1": 0.10,
    "us-west-2": 0.11,
    "eu-west-1": 0.13,
    "eu-north-1": 0.12,
    "ap-south-1": 0.09,
}


def energy_kwh(estimated_duration_minutes: float) -> float:
    """Energy drawn by a job of this duration, under the flat-power assumption."""
    return (estimated_duration_minutes / 60.0) * AVG_JOB_POWER_KW


def co2_grams(carbon_intensity_g_per_kwh: float, estimated_duration_minutes: float) -> float:
    """Grams of CO2 emitted running a job of this duration at this carbon intensity."""
    return carbon_intensity_g_per_kwh * energy_kwh(estimated_duration_minutes)


def cost_usd(region: str, estimated_duration_minutes: float) -> float:
    """Compute cost (USD) for running a job of this duration in `region`.
    Unknown regions cost $0 (rather than raising) so demo/legacy data never
    breaks the dashboard's cost math."""
    rate = REGION_COST_PER_HOUR.get(region, 0.0)
    return rate * (estimated_duration_minutes / 60.0)
