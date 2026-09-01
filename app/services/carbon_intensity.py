"""Client for grid carbon intensity providers (ElectricityMaps / WattTime).

Stubbed for now — wire up real API calls once keys are configured.
"""

import httpx

from app.core.config import get_settings


class CarbonIntensityClient:
    def __init__(self) -> None:
        self.settings = get_settings()

    async def get_intensity(self, region: str) -> float:
        """Return current carbon intensity (gCO2eq/kWh) for a region.

        TODO: replace with a real ElectricityMaps/WattTime call.
        """
        async with httpx.AsyncClient():
            raise NotImplementedError("Wire up ElectricityMaps/WattTime here")
