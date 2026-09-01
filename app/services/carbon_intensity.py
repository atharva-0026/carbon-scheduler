"""Client for ElectricityMaps real-time grid carbon intensity data.

Fetches live carbon intensity per grid zone and caches results in Redis for
`settings.carbon_cache_ttl_seconds` (default 15 min) to respect API rate
limits and keep the scheduler fast.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
import redis.asyncio as redis

from app.core.config import get_settings

logger = logging.getLogger(__name__)

CACHE_KEY_PREFIX = "carbon_intensity"


class CarbonIntensityError(Exception):
    """Raised when carbon intensity data cannot be retrieved or is invalid."""


class CarbonIntensityClient:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._redis: redis.Redis | None = None

    async def _get_redis(self) -> redis.Redis:
        if self._redis is None:
            self._redis = redis.from_url(self.settings.redis_url, decode_responses=True)
        return self._redis

    @staticmethod
    def _cache_key(zone: str) -> str:
        return f"{CACHE_KEY_PREFIX}:{zone.strip().upper()}"

    async def get_intensity(self, zone: str) -> dict[str, Any]:
        """Return current carbon intensity data for a grid zone.

        `zone` is an ElectricityMaps zone key, e.g. "US-CAL-CISO", "DE", "IN-WE".

        Returns a dict shaped like:
            {"zone": str, "carbon_intensity": float, "datetime": str,
             "updated_at": str, "is_estimated": bool, "cached": bool}

        Raises CarbonIntensityError if the API key is missing, the zone is
        unknown, the request fails, or the response can't be parsed. Redis
        being unavailable degrades to an uncached live call rather than
        failing outright.
        """
        cache_key = self._cache_key(zone)

        cached_data = await self._read_cache(cache_key)
        if cached_data is not None:
            return {**cached_data, "cached": True}

        data = await self._fetch_from_api(zone)
        await self._write_cache(cache_key, data)
        return {**data, "cached": False}

    async def _read_cache(self, cache_key: str) -> dict[str, Any] | None:
        try:
            cache = await self._get_redis()
            raw = await cache.get(cache_key)
        except Exception as exc:  # redis down/unreachable shouldn't be fatal
            logger.warning("Redis unavailable for read, skipping cache: %s", exc)
            return None

        if not raw:
            return None

        try:
            return json.loads(raw)
        except (TypeError, ValueError) as exc:
            logger.warning("Corrupt cache entry for %s, ignoring: %s", cache_key, exc)
            return None

    async def _write_cache(self, cache_key: str, data: dict[str, Any]) -> None:
        try:
            cache = await self._get_redis()
            await cache.set(cache_key, json.dumps(data), ex=self.settings.carbon_cache_ttl_seconds)
        except Exception as exc:  # redis down/unreachable shouldn't be fatal
            logger.warning("Redis unavailable for write, skipping cache: %s", exc)

    async def _fetch_from_api(self, zone: str) -> dict[str, Any]:
        if not self.settings.electricitymaps_api_key:
            raise CarbonIntensityError(
                "ELECTRICITYMAPS_API_KEY is not configured; cannot fetch live carbon intensity."
            )

        url = f"{self.settings.electricitymaps_base_url}/carbon-intensity/latest"
        headers = {"auth-token": self.settings.electricitymaps_api_key}
        params = {"zone": zone}

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url, headers=headers, params=params)
                response.raise_for_status()
                payload = response.json()
        except httpx.TimeoutException as exc:
            raise CarbonIntensityError(
                f"Timed out fetching carbon intensity for zone {zone!r}"
            ) from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 401:
                raise CarbonIntensityError(
                    "ElectricityMaps rejected the API key (401 Unauthorized)"
                ) from exc
            if status == 404:
                raise CarbonIntensityError(f"Unknown ElectricityMaps zone: {zone!r}") from exc
            if status == 429:
                raise CarbonIntensityError("ElectricityMaps rate limit exceeded (429)") from exc
            raise CarbonIntensityError(
                f"ElectricityMaps API error {status} for zone {zone!r}: {exc.response.text}"
            ) from exc
        except httpx.RequestError as exc:
            raise CarbonIntensityError(
                f"Network error contacting ElectricityMaps for zone {zone!r}: {exc}"
            ) from exc
        except ValueError as exc:  # response.json() failed
            raise CarbonIntensityError(
                f"ElectricityMaps returned a non-JSON response for zone {zone!r}"
            ) from exc

        if payload.get("carbonIntensity") is None:
            raise CarbonIntensityError(
                f"ElectricityMaps response for zone {zone!r} had no carbonIntensity field"
            )

        return {
            "zone": payload.get("zone", zone),
            "carbon_intensity": payload["carbonIntensity"],
            "datetime": payload.get("datetime"),
            "updated_at": payload.get("updatedAt"),
            "is_estimated": payload.get("isEstimated", False),
        }

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None
