import pandas as pd
import pytest

from app.core.carbon_math import AVG_JOB_POWER_KW, REGION_COST_PER_HOUR
from app.core.regions import HOME_REGION
from dashboard.data import summary_metrics, with_derived_columns


def _row(**overrides):
    base = {
        "id": 1,
        "name": "job-1",
        "job_type": "batch",
        "status": "dispatched",
        "estimated_duration_minutes": 60,
        "deadline": pd.NaT,
        "allowed_regions": None,
        "scheduled_region": "eu-north-1",
        "scheduled_start": pd.Timestamp("2026-01-01T01:00:00Z"),
        "carbon_intensity_actual": 100.0,
        "carbon_intensity_baseline": 400.0,
        "created_at": pd.Timestamp("2026-01-01T00:00:00Z"),
    }
    base.update(overrides)
    return base


def test_with_derived_columns_computes_expected_co2_and_cost():
    df = pd.DataFrame([_row()])
    out = with_derived_columns(df)

    energy_kwh = (60 / 60.0) * AVG_JOB_POWER_KW  # 1 hour -> AVG_JOB_POWER_KW kWh
    assert out.loc[0, "actual_co2_g"] == pytest.approx(100.0 * energy_kwh)
    assert out.loc[0, "baseline_co2_g"] == pytest.approx(400.0 * energy_kwh)
    assert out.loc[0, "carbon_saved_g"] == pytest.approx(300.0 * energy_kwh)

    expected_actual_cost = REGION_COST_PER_HOUR["eu-north-1"] * 1.0
    expected_baseline_cost = REGION_COST_PER_HOUR[HOME_REGION] * 1.0
    assert out.loc[0, "actual_cost_usd"] == pytest.approx(expected_actual_cost)
    assert out.loc[0, "baseline_cost_usd"] == pytest.approx(expected_baseline_cost)
    assert out.loc[0, "cost_delta_usd"] == pytest.approx(expected_actual_cost - expected_baseline_cost)


def test_with_derived_columns_handles_missing_carbon_data():
    # A "failed" job: no scheduled_region, no carbon intensity readings.
    df = pd.DataFrame(
        [_row(status="failed", scheduled_region=None, carbon_intensity_actual=None, carbon_intensity_baseline=None)]
    )
    out = with_derived_columns(df)

    assert pd.isna(out.loc[0, "actual_co2_g"])
    assert pd.isna(out.loc[0, "baseline_co2_g"])
    assert pd.isna(out.loc[0, "carbon_saved_g"])
    assert pd.isna(out.loc[0, "actual_cost_usd"])  # region unknown -> cost unknown


def test_summary_metrics_aggregates_only_analyzed_jobs():
    df = pd.DataFrame(
        [
            _row(id=1, carbon_intensity_actual=100.0, carbon_intensity_baseline=400.0),
            _row(id=2, carbon_intensity_actual=200.0, carbon_intensity_baseline=300.0),
            _row(id=3, status="failed", scheduled_region=None, carbon_intensity_actual=None, carbon_intensity_baseline=None),
        ]
    )
    out = with_derived_columns(df)
    metrics = summary_metrics(out)

    assert metrics["jobs_total"] == 3
    assert metrics["jobs_analyzed"] == 2  # the failed job is excluded

    energy_kwh = AVG_JOB_POWER_KW  # 60-minute jobs
    expected_saved_g = (400.0 - 100.0) * energy_kwh + (300.0 - 200.0) * energy_kwh
    assert metrics["total_carbon_saved_kg"] == pytest.approx(expected_saved_g / 1000.0)
    assert metrics["pct_carbon_reduction"] > 0


def test_summary_metrics_handles_empty_dataframe():
    df = pd.DataFrame(columns=list(_row().keys()))
    out = with_derived_columns(df)
    metrics = summary_metrics(out)

    assert metrics == {
        "jobs_analyzed": 0,
        "jobs_total": 0,
        "total_carbon_saved_kg": 0.0,
        "pct_carbon_reduction": 0.0,
        "total_cost_delta_usd": 0.0,
    }
