"""Schedule and cost risk simulator: Streamlit app.

The maths runs in SQL (DuckDB, see the sql/ folder). This file is only the interface.
Run locally with:  streamlit run app.py
"""
import io

import altair as alt
import duckdb
import pandas as pd
import streamlit as st

from simulator import (ACTIVITY_COLUMNS, DEPENDENCY_COLUMNS, RISK_COLUMNS, clean_inputs,
                       prepare_sheet, read_sql, run_pipeline)

st.set_page_config(page_title="Schedule and Cost Risk Simulator", layout="wide")

X_TITLE = "Project duration (days)"
Y_TITLE = "Chance of finishing by this day"
SCENARIO_COLORS = alt.Scale(domain=["baseline", "mitigated"], range=["#c0392b", "#2e86c1"])


# --------------------------------------------------------------------------------------------------
# Sample data and template
# --------------------------------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def load_sample():
    """Build the sample project by running 01_schema.sql and 02_seed.sql in a throwaway database."""
    con = duckdb.connect(":memory:")
    con.execute(read_sql("01_schema.sql"))
    con.execute(read_sql("02_seed.sql"))
    activities = con.execute("""
        SELECT code, name, phase, optimistic_days, likely_days, pessimistic_days, daily_cost_usd
        FROM activities ORDER BY activity_id""").df()
    deps = con.execute("""
        SELECT p.code AS predecessor, s.code AS successor
        FROM dependencies d
        JOIN activities p ON p.activity_id = d.predecessor_id
        JOIN activities s ON s.activity_id = d.successor_id
        ORDER BY p.activity_id, s.activity_id""").df()
    risks = con.execute("""
        SELECT k.name AS risk, a.code AS activity, k.probability, k.mitigated_probability,
               k.delay_days_min, k.delay_days_max, k.cost_impact_usd, k.mitigation_cost_usd
        FROM risks k JOIN activities a ON a.activity_id = k.activity_id
        ORDER BY k.risk_id""").df()
    row = con.execute("SELECT name, planned_start, target_finish, budget_usd FROM projects WHERE project_id = 1").fetchone()
    con.close()
    project = {"name": row[0], "planned_start": row[1], "target_finish": row[2], "budget_usd": float(row[3])}
    return activities, deps, risks, project


@st.cache_data(show_spinner=False)
def template_bytes():
    activities, deps, risks, _ = load_sample()
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as xl:
        activities.to_excel(xl, sheet_name="activities", index=False)
        deps.to_excel(xl, sheet_name="dependencies", index=False)
        risks.to_excel(xl, sheet_name="risks", index=False)
    return buffer.getvalue()


def money(x):
    return f"USD {x / 1e6:.2f}M"


# --------------------------------------------------------------------------------------------------
# Charts
# --------------------------------------------------------------------------------------------------
def s_curve(dist, plan_days, target_days):
    curve = alt.Chart(dist).mark_line(interpolate="step-after").encode(
        x=alt.X("bin_end_days:Q", title=X_TITLE),
        y=alt.Y("cumulative_pct:Q", title=Y_TITLE, axis=alt.Axis(format="%")),
        color=alt.Color("scenario:N", scale=SCENARIO_COLORS, title=None, legend=alt.Legend(orient="top")),
        tooltip=[alt.Tooltip("scenario:N"), alt.Tooltip("bin_end_days:Q", title="By day"),
                 alt.Tooltip("cumulative_pct:Q", title="Chance", format=".0%")],
    )
    marks = pd.DataFrame({"day": [plan_days, target_days], "label": ["Plan", "Target"]})
    rules = alt.Chart(marks).mark_rule(strokeDash=[4, 4], color="gray").encode(
        x=alt.X("day:Q", title=X_TITLE), tooltip=["label:N", "day:Q"])
    labels = alt.Chart(marks).mark_text(align="left", dx=4, baseline="top", color="gray").encode(
        x=alt.X("day:Q", title=X_TITLE), y=alt.value(2), text="label:N")
    p80 = alt.Chart(pd.DataFrame({"p": [0.8]})).mark_rule(strokeDash=[2, 2], color="gray").encode(
        y=alt.Y("p:Q", title=Y_TITLE))
    return (curve + rules + labels + p80).properties(height=360)


def histogram(dist):
    dist = dist.assign(zero=0.0)  # bars run from zero up to the share of runs
    return alt.Chart(dist).mark_bar(opacity=0.55).encode(
        x=alt.X("bin_start_days:Q", title=X_TITLE, scale=alt.Scale(zero=False)),
        x2="bin_end_days:Q",
        y=alt.Y("pct_of_runs:Q", title="Share of runs", stack=None, axis=alt.Axis(format="%")),
        y2="zero:Q",
        color=alt.Color("scenario:N", scale=SCENARIO_COLORS, title=None, legend=alt.Legend(orient="top")),
        tooltip=[alt.Tooltip("scenario:N"), alt.Tooltip("bin_start_days:Q", title="From day"),
                 alt.Tooltip("bin_end_days:Q", title="To day"),
                 alt.Tooltip("pct_of_runs:Q", title="Share of runs", format=".1%")],
    ).properties(height=300)


def tornado(df, value, title, number_format):
    return alt.Chart(df).mark_bar(color="#c0392b").encode(
        y=alt.Y("risk:N", sort="-x", title=None, axis=alt.Axis(labelLimit=260)),
        x=alt.X(f"{value}:Q", title=title),
        tooltip=[alt.Tooltip("risk:N"), alt.Tooltip("probability:Q", title="Probability", format=".0%"),
                 alt.Tooltip(f"{value}:Q", title=title, format=number_format)],
    ).properties(height=30 * max(len(df), 3) + 40)


def criticality_chart(df):
    return alt.Chart(df).mark_bar(color="#2e86c1").encode(
        y=alt.Y("label:N", sort="-x", title=None, axis=alt.Axis(labelLimit=260)),
        x=alt.X("criticality_pct:Q", title="Share of runs on the critical path (%)"),
        tooltip=[alt.Tooltip("label:N", title="Activity"),
                 alt.Tooltip("criticality_pct:Q", title="Critical in % of runs", format=".1f"),
                 alt.Tooltip("mean_slip_days:Q", title="Average slip (days)", format=".1f")],
    ).properties(height=22 * len(df) + 40)


def gantt(schedule):
    df = schedule.copy()
    df["label"] = df["code"] + "  " + df["name"]
    df["path"] = df["is_critical"].map({True: "Critical", False: "Has float"})
    return alt.Chart(df).mark_bar().encode(
        y=alt.Y("label:N", sort=list(df["label"]), title=None, axis=alt.Axis(labelLimit=260)),
        x=alt.X("earliest_start:Q", title="Day"),
        x2="earliest_finish:Q",
        color=alt.Color("path:N", title=None,
                        scale=alt.Scale(domain=["Critical", "Has float"], range=["#c0392b", "#7fa7c9"])),
        tooltip=[alt.Tooltip("label:N", title="Activity"),
                 alt.Tooltip("earliest_start:Q", title="Start"), alt.Tooltip("earliest_finish:Q", title="Finish"),
                 alt.Tooltip("total_float:Q", title="Float (days)")],
    ).properties(height=22 * len(df) + 40)


# --------------------------------------------------------------------------------------------------
# Page
# --------------------------------------------------------------------------------------------------
st.title("Schedule and cost risk simulator")
st.caption("Monte Carlo simulation of project finish date and cost. The maths runs in SQL on DuckDB. "
           "The sample project is fictional.")

sample_act, sample_dep, sample_risk, sample_proj = load_sample()

with st.sidebar:
    st.header("Project")
    source = st.radio("Data source", ["Sample project", "Upload Excel file"])
    st.download_button("Download Excel template", data=template_bytes(), file_name="risk_simulator_template.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    uploaded = None
    if source == "Upload Excel file":
        uploaded = st.file_uploader("Excel file with sheets: activities, dependencies, risks", type=["xlsx"])

    st.subheader("Targets")
    project_name = st.text_input("Project name", value=sample_proj["name"], key=f"name_{source}")
    planned_start = st.date_input("Planned start", value=sample_proj["planned_start"], key=f"start_{source}")
    target_finish = st.date_input("Target finish", value=sample_proj["target_finish"], key=f"finish_{source}")
    budget = st.number_input("Budget (USD)", min_value=1.0, value=float(sample_proj["budget_usd"]),
                             step=50_000.0, key=f"budget_{source}")

    st.subheader("Simulation")
    n_runs = st.selectbox("Runs per scenario", [1000, 5000, 10000], index=2)
    seed = st.number_input("Random seed", min_value=1, max_value=99, value=42, step=1)
    run_clicked = st.button("Run simulation", type="primary")

# --- Load the chosen data -------------------------------------------------------------------------
if source == "Sample project":
    activities, deps, risks = sample_act, sample_dep, sample_risk
    data_key = "sample"
else:
    if uploaded is None:
        st.info("Upload a file (start from the template in the sidebar) or switch to the sample project.")
        st.stop()
    try:
        sheets = pd.read_excel(uploaded, sheet_name=None)
    except Exception as exc:  # unreadable or not a real workbook
        st.error(f"Could not read the file: {exc}")
        st.stop()
    sheets = {str(k).strip().lower(): v for k, v in sheets.items()}
    missing = [s for s in ("activities", "dependencies", "risks") if s not in sheets]
    if missing:
        st.error(f"The workbook needs these sheets: {', '.join(missing)}.")
        st.stop()
    activities, deps, risks = sheets["activities"], sheets["dependencies"], sheets["risks"]
    data_key = f"upload_{uploaded.name}_{uploaded.size}"

sheet_errors = []
activities = prepare_sheet(activities, ACTIVITY_COLUMNS, "activities", sheet_errors)
deps = prepare_sheet(deps, DEPENDENCY_COLUMNS, "dependencies", sheet_errors)
risks = prepare_sheet(risks, RISK_COLUMNS, "risks", sheet_errors)
if sheet_errors:
    st.error("\n\n".join(sheet_errors))
    st.stop()

tab_results, tab_inputs, tab_schedule, tab_method = st.tabs(["Results", "Inputs", "Schedule", "How it works"])

# --- Inputs ---------------------------------------------------------------------------------------
with tab_inputs:
    st.subheader("Risks (editable)")
    st.caption("Change a probability, delay or cost, then press Run simulation in the sidebar. "
               "Rows can be added or removed.")
    activity_codes = [str(c) for c in activities["code"]]
    edited_risks = st.data_editor(
        risks, num_rows="dynamic", width="stretch", key=f"risks_{data_key}",
        column_config={
            "risk": st.column_config.TextColumn("Risk", required=True),
            "activity": st.column_config.SelectboxColumn("Activity", options=activity_codes, required=True),
            "probability": st.column_config.NumberColumn("Probability", min_value=0.0, max_value=1.0, step=0.05, format="%.2f"),
            "mitigated_probability": st.column_config.NumberColumn("Probability if mitigated", min_value=0.0, max_value=1.0, step=0.05, format="%.2f"),
            "delay_days_min": st.column_config.NumberColumn("Delay min (days)", min_value=0, step=1),
            "delay_days_max": st.column_config.NumberColumn("Delay max (days)", min_value=0, step=1),
            "cost_impact_usd": st.column_config.NumberColumn("Cost impact (USD)", min_value=0, step=1000, format="%d"),
            "mitigation_cost_usd": st.column_config.NumberColumn("Mitigation cost (USD)", min_value=0, step=1000, format="%d"),
        },
    )
    st.subheader("Activities")
    st.caption("Durations are in calendar days. Each estimate is optimistic, most likely and pessimistic.")
    st.dataframe(activities, width="stretch", hide_index=True)
    st.subheader("Dependencies")
    st.caption("Finish-to-start links: the successor starts when the predecessor finishes.")
    st.dataframe(deps, width="stretch", hide_index=True)

# --- Run ------------------------------------------------------------------------------------------
if run_clicked or st.session_state.get("results_key") != data_key:
    project = {"name": project_name, "planned_start": planned_start,
               "target_finish": target_finish, "budget_usd": budget}
    errors, cleaned = clean_inputs(project, activities, deps, edited_risks)
    st.session_state["errors"] = errors
    st.session_state["results"] = None
    if not errors:
        with st.spinner("Running the SQL simulation..."):
            try:
                st.session_state["results"] = run_pipeline(cleaned, int(n_runs), int(seed))
            except Exception as exc:
                st.session_state["errors"] = [f"The simulation failed: {exc}"]
    st.session_state["results_key"] = data_key

res = st.session_state.get("results")

# --- Results --------------------------------------------------------------------------------------
with tab_results:
    if st.session_state.get("errors"):
        st.error("\n\n".join(st.session_state["errors"]))
    if res is None:
        st.info("No results yet. Fix the problems above, then press Run simulation.")
    else:
        summary = res["summary"].set_index("scenario")
        b, m = summary.loc["baseline"], summary.loc["mitigated"]
        c = res["contingency"].set_index("scenario").loc["baseline"]
        cmp_row = res["comparison"].iloc[0]

        st.caption(f"{res['runs_used']:,} runs per scenario in {res['seconds']:.1f} s. "
                   "Results are from the last time Run simulation was pressed.")
        if res["runs_used"] < res["runs_requested"]:
            st.warning(f"The network is large, so the runs were lowered to {res['runs_used']:,} per scenario.")

        st.markdown(
            f"**{100 * b.prob_on_time:.0f}%** of runs meet the target of {int(b.target_days)} days and "
            f"**{100 * b.prob_within_budget:.0f}%** stay within the {money(b.budget_usd)} budget. "
            f"For 80% confidence, plan for **{b.p80_days:.0f} days** ({c.days_over_plan:.0f} more than the "
            f"{int(c.plan_days)}-day plan) and **{money(b.p80_cost_usd)}**."
        )

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("P80 finish", f"{b.p80_days:.0f} days", f"{c.days_over_target:+.0f} d vs target", delta_color="inverse")
        k2.metric("Contingency to P80", f"{c.days_over_plan:.0f} days",
                  help="P80 finish minus the plan duration (most likely durations, no risk events).")
        k3.metric("P80 cost (USD)", f"{b.p80_cost_usd / 1e6:.2f}M", f"{c.cost_over_budget_usd / 1000:+,.0f}K vs budget", delta_color="inverse")
        k4.metric("Chance on time", f"{100 * b.prob_on_time:.0f}%", help="Share of runs that finish by the target date.")

        st.subheader("Does mitigation pay?")
        j1, j2, j3 = st.columns(3)
        j1.metric("Days saved at P80", f"{cmp_row.p80_days_saved:.1f}")
        j2.metric("P80 cost saved, net of spend", f"USD {cmp_row.p80_cost_saved_usd / 1000:,.0f}K")
        j3.metric("Mitigation spend", f"USD {cmp_row.mitigation_spend_usd / 1000:,.0f}K")
        st.caption("Both scenarios use the same random numbers, so the difference is the effect of the mitigation, "
                   "not luck. Mitigated costs already include the mitigation spend.")

        st.subheader("When will it finish?")
        dist = res["distribution"]
        st.altair_chart(s_curve(dist, int(c.plan_days), int(b.target_days)), width="stretch")
        st.caption("Dashed lines: plan, target and the 80% confidence level.")
        st.altair_chart(histogram(dist), width="stretch")

        st.subheader("Which risks matter?")
        impact = res["risk_impact"]
        impact = impact[impact["scenario"] == "baseline"]
        left, right = st.columns(2)
        with left:
            st.markdown("**Days added to the finish date when the risk happens**")
            st.altair_chart(tornado(impact, "finish_impact_days", "Days added", ".1f"), width="stretch")
        with right:
            st.markdown("**Cost added when the risk happens**")
            st.altair_chart(tornado(impact, "cost_impact_usd", "Cost added (USD)", ",.0f"), width="stretch")
        st.caption("Compares runs where the risk happens with runs where it does not. "
                   "A risk can be a budget problem without being a schedule problem.")

        st.subheader("Which activities drive the date?")
        sens = res["sensitivity"]
        sens = sens[sens["scenario"] == "baseline"].copy()
        sens["label"] = sens["code"] + "  " + sens["activity"]
        sens = sens[sens["criticality_pct"] > 0.5].sort_values("criticality_pct", ascending=False)
        st.altair_chart(criticality_chart(sens), width="stretch")
        st.caption("The criticality index: how often each activity sits on the critical path across all runs.")

# --- Schedule -------------------------------------------------------------------------------------
with tab_schedule:
    if res is None:
        st.info("Run the simulation to see the plan schedule.")
    else:
        sched = res["schedule"]
        st.subheader("Plan schedule (most likely durations)")
        st.altair_chart(gantt(sched), width="stretch")
        show = sched[["code", "name", "duration_days", "earliest_start", "earliest_finish",
                      "latest_start", "latest_finish", "total_float", "is_critical"]]
        st.dataframe(show, width="stretch", hide_index=True)

# --- Method ---------------------------------------------------------------------------------------
with tab_method:
    st.markdown(
        """
**What this does.** Each activity has three duration estimates and each risk has a probability, a delay
range and a cost. The simulator draws a random duration for every activity and decides which risks
happen, then finds the critical path and total cost. It repeats this thousands of times to show the
range of outcomes.

**All of the maths is SQL.** The files below run in order on DuckDB, which works like PostgreSQL but runs
inside this app with no server. Python only loads the data and draws the charts.

1. `01_schema.sql` creates the tables, with keys and constraints.
2. `02_seed.sql` loads the fictional sample project.
3. `03_critical_path.sql` uses a recursive query to list every start-to-finish path and window
   functions to find float and the critical path.
4. `04_simulate.sql` draws random numbers, samples triangular durations and risk events, and finds the
   critical path in every run.
5. `05_results.sql` turns the runs into percentiles, contingency, the criticality index, risk impact and
   the baseline vs mitigated comparison.
        """
    )
    for fname in ["01_schema.sql", "02_seed.sql", "03_critical_path.sql", "04_simulate.sql", "05_results.sql"]:
        with st.expander(fname):
            st.code(read_sql(fname), language="sql")
