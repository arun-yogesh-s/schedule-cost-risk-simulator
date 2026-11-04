"""Run 04_simulate.sql and check the results.

Two kinds of checks:
  1. Rules that must hold in every run (exact).
  2. Percentiles and costs compared with a separate plain-Python simulation of the same model
     (approximate, so a tolerance is allowed).

Run from the repo root (after check_critical_path.py has passed):  python check_simulation.py
"""
import time
import duckdb
from pathlib import Path

SQL_DIR = Path(__file__).parent / "sql"

con = duckdb.connect("risk_simulator.duckdb")
t0 = time.time()
con.execute((SQL_DIR / "04_simulate.sql").read_text())
print(f"ran 04_simulate.sql in {time.time() - t0:.1f} s")

ok = True

def check(label, passed, detail=""):
    global ok
    ok = ok and passed
    print(f"{'ok      ' if passed else 'FAILED  '}{label} {detail}")

# --- 1. Rules that must always hold -------------------------------------------------------------
n_runs = con.execute("SELECT count(*) FROM simulation_runs").fetchone()[0]
n_acts = con.execute("SELECT count(*) FROM simulation_activity_results").fetchone()[0]
check("run rows", n_runs == 20000, f"(got {n_runs}, expected 20000)")
check("activity rows", n_acts == 480000, f"(got {n_acts}, expected 480000)")

worse = con.execute("""
    SELECT count(*) FROM simulation_runs b
    JOIN simulation_runs m ON m.run_id = b.run_id
    WHERE b.scenario_id = 1 AND m.scenario_id = 2
      AND m.finish_day > b.finish_day + 1e-9
""").fetchone()[0]
check("mitigated never finishes later than baseline in the same run", worse == 0, f"({worse} violations)")

short = con.execute("""
    SELECT count(*) FROM simulation_activity_results sar
    JOIN activities a ON a.activity_id = sar.activity_id
    WHERE sar.duration_days < a.optimistic_days - 1e-9
""").fetchone()[0]
check("no duration below its optimistic estimate", short == 0, f"({short} violations)")

# A01 and A24 are on every path, so they must be critical in every run
not_crit = con.execute("""
    SELECT count(*) FROM (
        SELECT scenario_id, run_id
        FROM simulation_activity_results
        WHERE activity_id IN (1, 24)
        GROUP BY scenario_id, run_id
        HAVING count(*) FILTER (WHERE on_critical_path) < 2
    )
""").fetchone()[0]
check("first and last activity critical in every run", not_crit == 0, f"({not_crit} violations)")

# --- 2. Compare with the plain-Python simulation -------------------------------------------------
rows = con.execute("""
    SELECT s.name,
           quantile_cont(r.finish_day, 0.5),
           quantile_cont(r.finish_day, 0.8),
           avg(r.total_cost_usd)
    FROM simulation_runs r JOIN scenarios s USING (scenario_id)
    GROUP BY s.name
""").fetchall()
got = {name: (p50, p80, cost) for name, p50, p80, cost in rows}

# name: (expected P50, expected P80, expected mean cost); tolerance 4 days and USD 40,000
expected = {"baseline": (239.9, 257.5, 1974000), "mitigated": (230.9, 246.3, 1984000)}
for name, (e50, e80, ecost) in expected.items():
    p50, p80, cost = got[name]
    print(f"        {name}: P50 {p50:.1f} (expect ~{e50}), P80 {p80:.1f} (expect ~{e80}), mean cost {cost:,.0f} (expect ~{ecost:,})")
    check(f"{name} P50 within 4 days", abs(p50 - e50) <= 4)
    check(f"{name} P80 within 4 days", abs(p80 - e80) <= 4)
    check(f"{name} mean cost within USD 40,000", abs(cost - ecost) <= 40000)

print("ALL OK" if ok else "CHECK ABOVE")
