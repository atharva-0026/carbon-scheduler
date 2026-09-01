import pytest

from app.core.carbon_math import AVG_JOB_POWER_KW, co2_grams, cost_usd, energy_kwh


def test_energy_kwh_scales_with_duration():
    assert energy_kwh(60) == pytest.approx(AVG_JOB_POWER_KW)
    assert energy_kwh(30) == pytest.approx(AVG_JOB_POWER_KW / 2)
    assert energy_kwh(0) == 0.0


def test_co2_grams_multiplies_intensity_by_energy():
    # 60 minutes at AVG_JOB_POWER_KW -> 1 kWh drawn
    assert co2_grams(carbon_intensity_g_per_kwh=400.0, estimated_duration_minutes=60) == pytest.approx(
        400.0 * AVG_JOB_POWER_KW
    )


def test_co2_grams_zero_duration_is_zero():
    assert co2_grams(500.0, 0) == 0.0


def test_cost_usd_known_region():
    # us-east-1 rate * 1 hour
    assert cost_usd("us-east-1", 60) == pytest.approx(0.10)
    assert cost_usd("us-east-1", 30) == pytest.approx(0.05)


def test_cost_usd_unknown_region_is_zero_not_an_error():
    assert cost_usd("mars-1", 60) == 0.0
