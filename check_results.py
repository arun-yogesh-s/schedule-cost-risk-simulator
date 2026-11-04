"""Run 05_results.sql and check the views.

Two kinds of checks:
  1. Exact consistency checks (the views agree with the underlying tables and with each other).
  2. Values compared with a separate plain-Python simulation of the same model (approximate).

Run from the repo root (after check_simulation.py has passed):  python check_results.py
"""
import duckdb
from pathlib import Path

SQL_DIR = Path(__file__).parent / "sql"

con = duckdb.connect("risk_simulator.duckdb")
con.execute((SQL_DIR / "05_results.sql").read_text())
print("ran 05_results.sql\n")

ok = True

def check(label, passed, detail=""):
    global ok
    ok = ok and passed
    print(f"{'ok      ' if passed else 'FAILED  '}{label} {detail}")

def one(sql):
    return con.execute(sql).fetchone()[0]

# --- Row counts ---------------------------------------------------------------------------------
for view, expected in [("scenario_summary", 2), ("contingency_p80", 2), ("activity_sensitivity", 48),
                       ("risk_impact", 16), ("scenario_comparison", 1)]:
    n = one(f"SELECT count(*) FROM {view}")
    check(f"{view} rows", n == expected, f"(got {n}, expected {expected})")

# --- Exact consistency --------------------------------------------------------------------------
direct = one("SELECT quantile_cont(finish_day, 0.8) FROM simulation_runs WHERE scenario_id = 1")
view = one("SELECT p80_days FROM scenario_summary WHERE scenario = 'baseline'")
check("scenario_summary P80 matches a direct query", abs(direct - view) < 1e-9)

plan_days = one("SELECT max(plan_days) FROM contingency_p80")
plan_cost = one("SELECT max(plan_cost_usd) FROM contingency_p80")
check("plan duration is 188 days", int(plan_days) == 188, f"(got {plan_days})")
check("plan cost is USD 1,559,500", abs(plan_cost - 1559500) < 1e-6, f"(got {plan_cost})")
gap = one("SELECT abs(days_over_plan - (p80_days - plan_days)) FROM contingency_p80 WHERE scenario = 'baseline'")
check("days_over_plan = P80 - plan", gap < 1e-9)

for sc in ("baseline", "mitigated"):
    total = one(f"SELECT sum(pct_of_runs) FROM finish_distribution WHERE scenario = '{sc}'")
    last = one(f"SELECT max(cumulative_pct) FROM finish_distribution WHERE scenario = '{sc}'")
    check(f"{sc} histogram shares sum to 1", abs(total - 1) < 1e-9)
    check(f"{sc} S-curve ends at 1", abs(last - 1) < 1e-9)

spend = one("SELECT mitigation_spend_usd FROM scenario_comparison")
check("mitigation spend is USD 95,000", abs(spend - 95000) < 1e-6, f"(got {spend})")

# --- Compared with the plain-Python simulation (tolerances allow for random variation) -----------
crit = dict(con.execute(
    "SELECT code, criticality_pct FROM activity_sensitivity WHERE scenario = 'baseline'").fetchall())
check("A01 and A24 critical in 100% of runs", crit["A01"] == 100 and crit["A24"] == 100)
for code, exp in [("A05", 85.6), ("A07", 53.6), ("A12", 46.4), ("A11", 29.2)]:
    check(f"criticality {code} about {exp}%", abs(crit[code] - exp) <= 6, f"(got {crit[code]:.1f})")
for code, limit in [("A02", 2), ("A08", 1), ("A15", 1)]:
    check(f"criticality {code} below {limit}%", crit[code] < limit, f"(got {crit[code]:.1f})")

impact = dict(con.execute(
    "SELECT risk, finish_impact_days FROM risk_impact WHERE scenario = 'baseline'").fetchall())
top = max(impact, key=impact.get)
check("biggest finish-date risk is the long-lead equipment", top == "Long-lead equipment arrives late", f"(got {top})")
check("long-lead equipment impact about 21 days", abs(impact["Long-lead equipment arrives late"] - 21.5) <= 4,
      f"(got {impact['Long-lead equipment arrives late']:.1f})")
check("control defects impact about 12 days", abs(impact["Control integration defects"] - 11.8) <= 4,
      f"(got {impact['Control integration defects']:.1f})")
check("cable price risk barely moves the date", abs(impact["Cable price increase"]) < 1.5,
      f"(got {impact['Cable price increase']:.1f})")

saved80, saved50 = con.execute("SELECT p80_days_saved, p50_days_saved FROM scenario_comparison").fetchone()
check("mitigation saves about 11 days at P80", abs(saved80 - 11.2) <= 4, f"(got {saved80:.1f})")
check("mitigation saves about 9 days at P50", abs(saved50 - 9.0) <= 4, f"(got {saved50:.1f})")

# --- Headline numbers for the README -------------------------------------------------------------
print("\nHeadline numbers")
for row in con.execute("""
    SELECT scenario, round(p50_days, 1), round(p80_days, 1), round(p80_cost_usd),
           round(100 * prob_on_time, 1), round(100 * prob_within_budget, 1)
    FROM scenario_summary ORDER BY scenario_id
""").fetchall():
    print(f"  {row[0]:10} P50 {row[1]} d | P80 {row[2]} d | P80 cost {row[3]:,.0f} | on time {row[4]}% | within budget {row[5]}%")

print("\nALL OK" if ok else "\nCHECK ABOVE")
