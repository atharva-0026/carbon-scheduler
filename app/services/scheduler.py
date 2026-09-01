"""Job scheduling logic: pick cleanest region/time window for a deferrable job."""


def pick_best_region(candidate_intensities: dict[str, float]) -> str:
    """Given {region: gCO2eq/kWh}, return the cleanest region."""
    return min(candidate_intensities, key=candidate_intensities.get)
