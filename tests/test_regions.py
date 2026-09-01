import pytest

from app.core.regions import REGION_ZONES, available_regions, latency_ms_from_home, zone_for_region


def test_available_regions_matches_catalog():
    assert set(available_regions()) == set(REGION_ZONES.keys())


def test_zone_for_region_known():
    assert zone_for_region("us-east-1") == "US-MIDA-PJM"


def test_zone_for_region_unknown_raises():
    with pytest.raises(ValueError, match="Unknown region"):
        zone_for_region("mars-1")


def test_latency_ms_from_home_known_regions():
    assert latency_ms_from_home("us-east-1") == 0.0
    assert latency_ms_from_home("ap-south-1") == 180.0


def test_latency_ms_from_home_unknown_region_defaults_to_zero():
    assert latency_ms_from_home("mars-1") == 0.0
