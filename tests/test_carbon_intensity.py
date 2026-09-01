import json

import httpx
import pytest

from app.services.carbon_intensity import CarbonIntensityClient, CarbonIntensityError


class FakeRedis:
    """Minimal in-memory stand-in for redis.asyncio.Redis, async get/set/aclose only."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value

    async def aclose(self) -> None:
        pass


@pytest.fixture
def client(monkeypatch) -> CarbonIntensityClient:
    from app.core.config import get_settings

    monkeypatch.setenv("ELECTRICITYMAPS_API_KEY", "test-key")
    get_settings.cache_clear()
    c = CarbonIntensityClient()
    c._redis = FakeRedis()  # bypass real redis.from_url
    yield c
    get_settings.cache_clear()


def _mock_transport(json_body: dict, status_code: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=json_body, request=request)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_get_intensity_live_call_and_cache(client, monkeypatch):
    body = {
        "zone": "IN-WE",
        "carbonIntensity": 412.5,
        "datetime": "2026-09-01T12:00:00Z",
        "updatedAt": "2026-09-01T12:00:00Z",
        "isEstimated": False,
    }

    async def fake_get(self, url, headers=None, params=None):
        request = httpx.Request("GET", url, params=params)
        return httpx.Response(200, json=body, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    result = await client.get_intensity("in-we")
    assert result["zone"] == "IN-WE"
    assert result["carbon_intensity"] == 412.5
    assert result["cached"] is False

    # Second call should be served from the fake cache, not hit the API again.
    cache_key = client._cache_key("in-we")
    assert cache_key in client._redis.store

    cached_result = await client.get_intensity("in-we")
    assert cached_result["cached"] is True
    assert cached_result["carbon_intensity"] == 412.5


@pytest.mark.asyncio
async def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("ELECTRICITYMAPS_API_KEY", raising=False)
    from app.core.config import get_settings

    get_settings.cache_clear()
    c = CarbonIntensityClient()
    c._redis = FakeRedis()

    with pytest.raises(CarbonIntensityError, match="not configured"):
        await c.get_intensity("DE")

    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_unauthorized_raises_carbon_intensity_error(client, monkeypatch):
    async def fake_get(self, url, headers=None, params=None):
        request = httpx.Request("GET", url, params=params)
        return httpx.Response(401, json={"message": "invalid key"}, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    with pytest.raises(CarbonIntensityError, match="401"):
        await client.get_intensity("DE")


@pytest.mark.asyncio
async def test_unknown_zone_raises_carbon_intensity_error(client, monkeypatch):
    async def fake_get(self, url, headers=None, params=None):
        request = httpx.Request("GET", url, params=params)
        return httpx.Response(404, json={"message": "unknown zone"}, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    with pytest.raises(CarbonIntensityError, match="Unknown ElectricityMaps zone"):
        await client.get_intensity("ZZ-NOPE")


@pytest.mark.asyncio
async def test_network_error_raises_carbon_intensity_error(client, monkeypatch):
    async def fake_get(self, url, headers=None, params=None):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    with pytest.raises(CarbonIntensityError, match="Network error"):
        await client.get_intensity("DE")


@pytest.mark.asyncio
async def test_redis_read_failure_falls_back_to_live_call(client, monkeypatch):
    async def broken_get(key):
        raise ConnectionError("redis down")

    client._redis.get = broken_get

    body = {"zone": "DE", "carbonIntensity": 300.0, "datetime": None, "updatedAt": None}

    async def fake_get(self, url, headers=None, params=None):
        request = httpx.Request("GET", url, params=params)
        return httpx.Response(200, json=body, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    result = await client.get_intensity("DE")
    assert result["carbon_intensity"] == 300.0
    assert result["cached"] is False
