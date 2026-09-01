import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.core.regions import HOME_REGION, available_regions  # noqa: E402
from dashboard.data import load_jobs, summary_metrics, with_derived_columns  # noqa: E402

st.set_page_config(page_title="Carbon-Aware Compute Scheduler", layout="wide")

# Fixed categorical color order -- one hue per region, never reassigned by
# filtering or sort order (see the dataviz skill's categorical-color rule).
REGION_ORDER = available_regions()
REGION_COLORS = dict(
    zip(REGION_ORDER, ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"])
)
SERIES_COLORS = {
    "Carbon-aware (actual)": "#2a78d6",
    "Naive baseline": "#eb6834",
}

st.title("Carbon-Aware Compute Scheduler")
st.caption("Carbon and cost impact of carbon-aware scheduling vs. always running immediately at home")

df, source = load_jobs()
df = with_derived_columns(df)

with st.sidebar:
    st.subheader("Filters")
    job_types = sorted(df["job_type"].dropna().unique().tolist())
    statuses = sorted(df["status"].dropna().unique().tolist())
    selected_types = st.multiselect("Job type", job_types, default=job_types)
    selected_statuses = st.multiselect("Status", statuses, default=statuses)

    st.divider()
    st.caption(f"Data source: **{source}**")
    if source == "sample data":
        st.caption(
            "No live Postgres connection found (or the `jobs` table is empty) -- "
            "showing the bundled demo dataset from `dashboard/sample_data.csv` "
            "(generate real history with `python scripts/seed_data.py`, or run "
            "the full stack via `docker compose up`)."
        )
    st.caption(
        "Carbon/cost figures are estimates: every job is assumed to draw "
        "~0.5 kW for its duration, and region compute cost is a flat made-up "
        "hourly rate (see `app/core/carbon_math.py`) -- not real cloud pricing."
    )

mask = df["job_type"].isin(selected_types) & df["status"].isin(selected_statuses)
view = df[mask].copy()

analyzed = view[view["carbon_saved_g"].notna()].copy()
metrics = summary_metrics(view)

st.divider()

col1, col2, col3, col4 = st.columns(4)
col1.metric("Jobs analyzed", f"{metrics['jobs_analyzed']} / {metrics['jobs_total']}")
col2.metric("Carbon saved", f"{metrics['total_carbon_saved_kg']:.2f} kg CO2")
col3.metric("Carbon reduction", f"{metrics['pct_carbon_reduction']:.1f}%")
cost_label = "extra cost" if metrics["total_cost_delta_usd"] >= 0 else "cost saved"
col4.metric(f"Net {cost_label} vs. naive", f"${abs(metrics['total_cost_delta_usd']):.2f}")

st.divider()

st.subheader("Carbon saved vs. naive baseline")
st.caption(
    "Naive baseline = always run immediately in the home region "
    f"({HOME_REGION}), ignoring carbon. Grouped by the day the job was submitted."
)

if analyzed.empty:
    st.info("No scheduled jobs with carbon data to show yet.")
else:
    daily = (
        analyzed.assign(day=analyzed["created_at"].dt.floor("D"))
        .groupby("day", as_index=False)[["actual_co2_g", "baseline_co2_g"]]
        .sum()
    )
    daily_long = daily.melt(
        id_vars="day",
        value_vars=["actual_co2_g", "baseline_co2_g"],
        var_name="series",
        value_name="co2_g",
    )
    daily_long["series"] = daily_long["series"].map(
        {"actual_co2_g": "Carbon-aware (actual)", "baseline_co2_g": "Naive baseline"}
    )
    daily_long["CO2 (kg)"] = daily_long["co2_g"] / 1000.0

    fig_daily = px.bar(
        daily_long,
        x="day",
        y="CO2 (kg)",
        color="series",
        barmode="group",
        color_discrete_map=SERIES_COLORS,
        category_orders={"series": ["Carbon-aware (actual)", "Naive baseline"]},
        labels={"day": "Date", "series": ""},
    )
    fig_daily.update_layout(legend_title_text="", hovermode="x unified", margin=dict(t=10))
    st.plotly_chart(fig_daily, use_container_width=True)

st.divider()

st.subheader("Cost vs. carbon tradeoff")
st.caption("One point per job: how much carbon it saved vs. the naive baseline, and what that cost (or saved).")

if analyzed.empty:
    st.info("No scheduled jobs with carbon data to show yet.")
else:
    scatter_df = analyzed.copy()
    scatter_df["Carbon saved (kg)"] = scatter_df["carbon_saved_g"] / 1000.0
    fig_tradeoff = px.scatter(
        scatter_df,
        x="Carbon saved (kg)",
        y="cost_delta_usd",
        color="scheduled_region",
        color_discrete_map=REGION_COLORS,
        category_orders={"scheduled_region": REGION_ORDER},
        hover_data={"name": True, "job_type": True, "scheduled_region": True},
        labels={"cost_delta_usd": "Extra cost vs. baseline (USD)", "scheduled_region": "Region"},
    )
    fig_tradeoff.add_hline(y=0, line_dash="dot", line_color="#898781")
    fig_tradeoff.add_vline(x=0, line_dash="dot", line_color="#898781")
    fig_tradeoff.update_traces(marker=dict(size=10, line=dict(width=1, color="#fcfcfb")))
    fig_tradeoff.update_layout(margin=dict(t=10))
    st.plotly_chart(fig_tradeoff, use_container_width=True)
    st.caption(
        "Top-left = saved carbon *and* money. Bottom-right = cost carbon *and* money "
        "(usually a tightly-constrained or urgent job)."
    )

st.divider()

st.subheader("Per-job: chosen region/time vs. the naive alternative")
st.caption(
    "\"Chosen\" is what the scoring engine actually picked -- by construction the "
    "optimal choice it could score at decision time (see app/services/scoring.py). "
    "\"Naive\" is what would have run without carbon-aware scheduling: immediately, "
    f"in the home region ({HOME_REGION})."
)

table_df = view.copy()
table_df["naive_region"] = HOME_REGION
table_df["naive_time"] = table_df["created_at"]

display_cols = {
    "name": "Job",
    "job_type": "Type",
    "status": "Status",
    "scheduled_region": "Chosen region",
    "scheduled_start": "Chosen start",
    "naive_region": "Naive region",
    "naive_time": "Naive start (immediate)",
    "carbon_intensity_actual": "Carbon @ chosen (gCO2/kWh)",
    "carbon_intensity_baseline": "Carbon @ naive (gCO2/kWh)",
    "carbon_saved_g": "Carbon saved (g)",
    "cost_delta_usd": "Cost delta (USD)",
    "deadline": "Deadline",
}
table_view = table_df[list(display_cols.keys())].rename(columns=display_cols)
st.dataframe(table_view.sort_values("Naive start (immediate)", ascending=False), use_container_width=True)

with st.expander("Raw job records"):
    st.dataframe(view, use_container_width=True)
