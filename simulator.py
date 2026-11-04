"""Engine for the schedule and cost risk simulator.

Two jobs:
  1. clean_inputs():  check the project, activities, dependencies and risks before anything runs.
  2. run_pipeline():  build a fresh in-memory DuckDB database, run the SQL files in sql/, and return
                      the result views as DataFrames.

All of the maths lives in the SQL files. This module only loads data, swaps in the number of runs
and the seed, and reads the results back.
"""
import time
from collections import deque
from pathlib import Path

import duckdb
import pandas as pd

SQL_DIR = Path(__file__).parent / "sql"

MAX_ACTIVITIES = 200
MAX_PATHS = 5_000          # start-to-finish paths in the network
MAX_PREFIX_ROWS = 20_000   # rows produced by the recursive path query
MAX_ROWS = 25_000_000      # activity-path rows the simulation may process (keeps memory in check)
MIN_RUNS = 100

ACTIVITY_COLUMNS = ["code", "name", "optimistic_days", "likely_days", "pessimistic_days", "daily_cost_usd"]
DEPENDENCY_COLUMNS = ["predecessor", "successor"]
RISK_COLUMNS = ["risk", "activity", "probability", "mitigated_probability",
                "delay_days_min", "delay_days_max", "cost_impact_usd", "mitigation_cost_usd"]


def read_sql(name):
    return (SQL_DIR / name).read_text()


# --------------------------------------------------------------------------------------------------
# Input checks
# --------------------------------------------------------------------------------------------------
def prepare_sheet(df, required, sheet, errors):
    """Lower-case the column names, check the required ones exist and drop empty rows."""
    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
    missing = [c for c in required if c not in df.columns]
    if missing:
        errors.append(f"Sheet '{sheet}' is missing columns: {', '.join(missing)}.")
        return None
    return df.dropna(how="all").reset_index(drop=True)


def _numeric(df, cols, sheet, errors):
    for c in cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")
        if df[c].isna().any():
            errors.append(f"'{sheet}': every row needs a number in '{c}'.")


def network_size(codes, edges):
    """Size of the dependency network, worked out without a database.

    Returns (path_count, prefix_rows, path_rows), or None if the dependencies contain a loop.
      path_count   number of start-to-finish paths
      prefix_rows  rows the recursive path query will produce
      path_rows    rows in network_paths (activities summed over all paths)
    """
    preds = {c: [] for c in codes}
    succs = {c: [] for c in codes}
    indegree = {c: 0 for c in codes}
    for p, s in edges:
        preds[s].append(p)
        succs[p].append(s)
        indegree[s] += 1

    order = []
    queue = deque(c for c in codes if indegree[c] == 0)
    while queue:
        c = queue.popleft()
        order.append(c)
        for s in succs[c]:
            indegree[s] -= 1
            if indegree[s] == 0:
                queue.append(s)
    if len(order) < len(codes):
        return None  # a loop: some activities never reach in-degree 0

    count, length_sum = {}, {}
    for c in order:
        if not preds[c]:
            count[c], length_sum[c] = 1, 1
        else:
            count[c] = sum(count[p] for p in preds[c])
            length_sum[c] = sum(length_sum[p] + count[p] for p in preds[c])
    ends = [c for c in codes if not succs[c]]
    return (sum(count[e] for e in ends), sum(count.values()), sum(length_sum[e] for e in ends))


def clean_inputs(project, activities, dependencies, risks):
    """Validate and tidy the inputs. Returns (errors, cleaned); cleaned is None if there are errors."""
    errors = []
    act = prepare_sheet(activities, ACTIVITY_COLUMNS, "activities", errors)
    dep = prepare_sheet(dependencies, DEPENDENCY_COLUMNS, "dependencies", errors)
    rsk = prepare_sheet(risks, RISK_COLUMNS, "risks", errors)

    project = dict(project)
    project["name"] = str(project.get("name") or "").strip() or "Untitled project"
    if project["target_finish"] <= project["planned_start"]:
        errors.append("The target finish must be after the planned start.")
    if project["budget_usd"] <= 0:
        errors.append("The budget must be above zero.")
    if errors:
        return errors, None

    # --- activities ---
    if act.empty:
        errors.append("Add at least one activity.")
    if len(act) > MAX_ACTIVITIES:
        errors.append(f"At most {MAX_ACTIVITIES} activities are supported.")
    act["code"] = act["code"].astype(str).str.strip()
    if act["code"].isin(["", "nan", "None"]).any():
        errors.append("'activities': every row needs a code.")
    if act["code"].duplicated().any():
        errors.append("'activities': codes must be unique.")
    day_cols = ["optimistic_days", "likely_days", "pessimistic_days"]
    _numeric(act, day_cols + ["daily_cost_usd"], "activities", errors)
    if errors:
        return errors, None
    for c in day_cols:
        if (act[c] % 1 != 0).any():
            errors.append(f"'activities': '{c}' must be whole days.")
    if (act["optimistic_days"] <= 0).any():
        errors.append("'activities': optimistic_days must be above zero.")
    if (act["likely_days"] < act["optimistic_days"]).any() or (act["pessimistic_days"] < act["likely_days"]).any():
        errors.append("'activities': each row needs optimistic_days <= likely_days <= pessimistic_days.")
    if (act["daily_cost_usd"] < 0).any():
        errors.append("'activities': daily_cost_usd cannot be negative.")
    if errors:
        return errors, None
    if "phase" not in act.columns:
        act["phase"] = "General"
    act["phase"] = act["phase"].fillna("General").astype(str)
    act["name"] = act["name"].where(act["name"].notna(), act["code"]).astype(str)
    for c in day_cols:
        act[c] = act[c].astype(int)

    # --- dependencies ---
    dep["predecessor"] = dep["predecessor"].astype(str).str.strip()
    dep["successor"] = dep["successor"].astype(str).str.strip()
    codes = set(act["code"])
    unknown = sorted((set(dep["predecessor"]) | set(dep["successor"])) - codes)
    if unknown:
        errors.append(f"'dependencies': unknown activity codes: {', '.join(unknown[:10])}.")
    if (dep["predecessor"] == dep["successor"]).any():
        errors.append("'dependencies': an activity cannot depend on itself.")
    if dep.duplicated().any():
        errors.append("'dependencies': the same link is listed twice.")
    size = None
    if not errors:
        size = network_size(list(act["code"]), list(zip(dep["predecessor"], dep["successor"])))
        if size is None:
            errors.append("'dependencies': the links form a loop, so there is no valid schedule.")
        elif size[0] > MAX_PATHS or size[1] > MAX_PREFIX_ROWS:
            errors.append(
                f"The network has {size[0]:,} start-to-finish paths, above the limit of {MAX_PATHS:,}. "
                "Simplify it, for example by merging parallel activities."
            )

    # --- risks ---
    rsk["risk"] = rsk["risk"].astype(str).str.strip()
    rsk["activity"] = rsk["activity"].astype(str).str.strip()
    if rsk.empty:
        errors.append("Add at least one risk.")
    if rsk["risk"].isin(["", "nan", "None"]).any():
        errors.append("'risks': every row needs a risk name.")
    rnum = ["probability", "mitigated_probability", "delay_days_min", "delay_days_max",
            "cost_impact_usd", "mitigation_cost_usd"]
    _numeric(rsk, rnum, "risks", errors)
    if not rsk["activity"].isin(codes).all():
        errors.append("'risks': every risk must point at an activity code from the activities sheet.")
    if not errors:
        if not rsk["probability"].between(0, 1).all() or not rsk["mitigated_probability"].between(0, 1).all():
            errors.append("'risks': probabilities must be between 0 and 1.")
        if (rsk["mitigated_probability"] > rsk["probability"]).any():
            errors.append("'risks': mitigated_probability cannot be higher than probability.")
        if (rsk[["delay_days_min", "delay_days_max"]] % 1 != 0).any().any():
            errors.append("'risks': delays must be whole days.")
        if (rsk["delay_days_min"] < 0).any() or (rsk["delay_days_max"] < rsk["delay_days_min"]).any():
            errors.append("'risks': delays need 0 <= delay_days_min <= delay_days_max.")
        if (rsk["cost_impact_usd"] < 0).any() or (rsk["mitigation_cost_usd"] < 0).any():
            errors.append("'risks': costs cannot be negative.")
    if errors:
        return errors, None
    for c in ["delay_days_min", "delay_days_max"]:
        rsk[c] = rsk[c].astype(int)

    return [], {"project": project, "activities": act, "dependencies": dep, "risks": rsk, "size": size}


# --------------------------------------------------------------------------------------------------
# SQL pipeline
# --------------------------------------------------------------------------------------------------
def _as_object(df, cols):
    """Plain Python strings, so DuckDB reads the columns the same way on every pandas version."""
    for c in cols:
        df[c] = df[c].astype(object)
    return df


def run_pipeline(cleaned, n_runs, seed):
    """Build an in-memory DuckDB database, run sql/01 to 05 and return the results as DataFrames.

    n_runs: simulation runs per scenario (lowered automatically for very large networks).
    seed:   whole number from 1 to 99, used as setseed(seed / 100).
    """
    started = time.time()
    project = cleaned["project"]
    path_rows = cleaned["size"][2]

    max_runs = MAX_ROWS // (2 * path_rows)
    if max_runs < MIN_RUNS:
        raise ValueError("The network is too large to simulate here. Simplify the dependencies.")
    runs_used = n_runs if n_runs <= max_runs else (max_runs // MIN_RUNS) * MIN_RUNS

    sql04 = read_sql("04_simulate.sql")
    if sql04.count("generate_series(1, 10000)") != 2 or sql04.count("setseed(0.42)") != 1:
        raise RuntimeError("04_simulate.sql no longer has the run count and seed this app replaces.")
    sql04 = sql04.replace("generate_series(1, 10000)", f"generate_series(1, {int(runs_used)})")
    sql04 = sql04.replace("setseed(0.42)", f"setseed({seed / 100:.2f})")

    act = cleaned["activities"].copy()
    act.insert(0, "activity_id", range(1, len(act) + 1))
    act["project_id"] = 1
    _as_object(act, ["code", "name", "phase"])
    code_to_id = dict(zip(act["code"], act["activity_id"]))

    dep = pd.DataFrame({
        "predecessor_id": cleaned["dependencies"]["predecessor"].map(code_to_id).astype(int),
        "successor_id": cleaned["dependencies"]["successor"].map(code_to_id).astype(int),
    })

    rsk = cleaned["risks"].copy()
    rsk.insert(0, "risk_id", range(1, len(rsk) + 1))
    rsk["project_id"] = 1
    rsk["activity_id"] = rsk["activity"].map(code_to_id).astype(int)
    rsk["name"] = rsk["risk"]
    _as_object(rsk, ["name"])

    con = duckdb.connect(":memory:")
    try:
        con.execute(read_sql("01_schema.sql"))
        con.execute("INSERT INTO projects VALUES (1, ?, ?, ?, ?)",
                    [project["name"], project["planned_start"], project["target_finish"],
                     float(project["budget_usd"])])
        con.execute("""INSERT INTO scenarios VALUES
            (1, 'baseline',  'Risks as currently assessed, no extra mitigation'),
            (2, 'mitigated', 'Mitigation actions funded: lower probabilities, plus mitigation cost')""")
        con.register("act_df", act)
        con.execute("""INSERT INTO activities (activity_id, project_id, code, name, phase,
                           optimistic_days, likely_days, pessimistic_days, daily_cost_usd)
                       SELECT activity_id, project_id, code, name, phase,
                           optimistic_days, likely_days, pessimistic_days, daily_cost_usd
                       FROM act_df""")
        con.register("dep_df", dep)
        con.execute("INSERT INTO dependencies SELECT predecessor_id, successor_id FROM dep_df")
        con.register("risk_df", rsk)
        con.execute("""INSERT INTO risks (risk_id, project_id, activity_id, name, probability,
                           mitigated_probability, delay_days_min, delay_days_max,
                           cost_impact_usd, mitigation_cost_usd)
                       SELECT risk_id, project_id, activity_id, name, probability,
                           mitigated_probability, delay_days_min, delay_days_max,
                           cost_impact_usd, mitigation_cost_usd
                       FROM risk_df""")

        con.execute(read_sql("03_critical_path.sql"))
        con.execute(sql04)
        con.execute(read_sql("05_results.sql"))

        def table(sql):
            return con.execute(sql).df()

        results = {
            "summary": table("SELECT * FROM scenario_summary ORDER BY scenario_id"),
            "contingency": table("SELECT * FROM contingency_p80"),
            "comparison": table("SELECT * FROM scenario_comparison"),
            "sensitivity": table("SELECT * FROM activity_sensitivity ORDER BY scenario_id, code"),
            "risk_impact": table("SELECT * FROM risk_impact ORDER BY scenario_id, risk_id"),
            "distribution": table("SELECT * FROM finish_distribution ORDER BY scenario_id, bin_start_days"),
            "schedule": table("SELECT * FROM activity_schedule ORDER BY earliest_start, activity_id"),
        }
    finally:
        con.close()

    results["runs_used"] = int(runs_used)
    results["runs_requested"] = int(n_runs)
    results["seconds"] = time.time() - started
    return results
