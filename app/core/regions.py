"""Static region catalog and cross-region latency table.

This is a small hardcoded demo catalog mapping deployable "regions" to
ElectricityMaps zone keys, plus a one-way latency estimate from the
scheduler's home region. Swap this for a real service-discovery /
infra-config source (and real measured latencies) when wiring this up to
actual compute.
"""

from __future__ import annotations

HOME_REGION = "us-east-1"

# region -> ElectricityMaps zone key (https://api-portal.electricitymaps.com/)
REGION_ZONES: dict[str, str] = {
    "us-east-1": "US-MIDA-PJM",
    "us-west-2": "US-NW-PACW",
    "eu-west-1": "IE",
    "eu-north-1": "SE",
    "ap-south-1": "IN-WE",
}

# One-way latency in ms from HOME_REGION to each region (0 for home itself).
# Rough real-world WAN estimates for a demo — not measured.
REGION_LATENCY_MS: dict[str, float] = {
    "us-east-1": 0.0,
    "us-west-2": 60.0,
    "eu-west-1": 90.0,
    "eu-north-1": 100.0,
    "ap-south-1": 180.0,
}


def available_regions() -> list[str]:
    """All region keys the scheduler is allowed to place jobs in."""
    return list(REGION_ZONES.keys())


def zone_for_region(region: str) -> str:
    """ElectricityMaps zone key for a region. Raises ValueError if unknown."""
    try:
        return REGION_ZONES[region]
    except KeyError as exc:
        raise ValueError(f"Unknown region: {region!r}") from exc


def latency_ms_from_home(region: str) -> float:
    """One-way latency (ms) from HOME_REGION to `region`. Unknown regions get 0."""
    return REGION_LATENCY_MS.get(region, 0.0)
