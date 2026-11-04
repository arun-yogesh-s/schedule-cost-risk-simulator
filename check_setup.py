"""Load the schema and seed data into DuckDB and print basic checks.

Run from the repo root:  python check_setup.py
"""
import duckdb
from pathlib import Path

SQL_DIR = Path(__file__).parent / "sql"

con = duckdb.connect("risk_simulator.duckdb")
print("DuckDB version:", duckdb.__version__)

for name in ["01_schema.sql", "02_seed.sql"]:
    con.execute((SQL_DIR / name).read_text())
    print("ran", name)

checks = {
    "projects": 1,
    "scenarios": 2,
    "activities": 24,
    "dependencies": 30,
    "risks": 8,
}
ok = True
for table, expected in checks.items():
    n = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    flag = "ok" if n == expected else "MISMATCH"
    ok = ok and n == expected
    print(f"{table:13} {n:3} (expected {expected}) {flag}")

# Referential and sanity checks
orphans = con.execute("""
    SELECT count(*) FROM dependencies d
    WHERE d.predecessor_id NOT IN (SELECT activity_id FROM activities)
       OR d.successor_id   NOT IN (SELECT activity_id FROM activities)
""").fetchone()[0]
print("orphan dependencies:", orphans)

# Expected from a plain-Python check: sum of likely_days x daily cost = 1,559,500? (not all activities are
# sequential, but cost is per activity so this total is valid)
cost = con.execute("SELECT sum(likely_days * daily_cost_usd) FROM activities").fetchone()[0]
print("most-likely total cost (expect 1559500):", cost)
print("ALL OK" if ok and orphans == 0 and cost == 1559500 else "CHECK ABOVE")
