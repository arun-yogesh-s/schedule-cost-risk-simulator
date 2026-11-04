"""Run 03_critical_path.sql and compare the result with values worked out independently in Python.

Run from the repo root (after check_setup.py has passed):  python check_critical_path.py
"""
import duckdb
from pathlib import Path

SQL_DIR = Path(__file__).parent / "sql"

con = duckdb.connect("risk_simulator.duckdb")
con.execute((SQL_DIR / "03_critical_path.sql").read_text())
print("ran 03_critical_path.sql")

# activity_id: (earliest_start, earliest_finish, latest_start, latest_finish, total_float)
expected = {
    1: (0, 3, 0, 3, 0),        2: (3, 10, 11, 18, 8),     3: (3, 18, 3, 18, 0),
    4: (3, 28, 16, 41, 13),    5: (18, 38, 18, 38, 0),    6: (38, 43, 38, 43, 0),
    7: (43, 88, 43, 88, 0),    8: (43, 63, 81, 101, 38),  9: (43, 73, 45, 75, 2),
    10: (28, 34, 41, 47, 13),  11: (43, 71, 47, 75, 4),   12: (73, 91, 75, 93, 2),
    13: (88, 93, 88, 93, 0),   14: (93, 115, 93, 115, 0), 15: (71, 85, 101, 115, 30),
    16: (115, 127, 115, 127, 0), 17: (127, 142, 127, 142, 0), 18: (142, 148, 142, 148, 0),
    19: (148, 151, 148, 151, 0), 20: (151, 163, 151, 163, 0), 21: (163, 170, 163, 170, 0),
    22: (170, 178, 170, 178, 0), 23: (178, 185, 178, 185, 0), 24: (185, 188, 185, 188, 0),
}

n_paths = con.execute("SELECT count(DISTINCT path_id) FROM network_paths").fetchone()[0]
print("start-to-finish paths:", n_paths, "(expected 24)")

rows = con.execute("""
    SELECT activity_id, earliest_start, earliest_finish, latest_start, latest_finish, total_float
    FROM activity_schedule ORDER BY activity_id
""").fetchall()

bad = 0
for r in rows:
    got = tuple(int(x) for x in r[1:])
    if got != expected[r[0]]:
        bad += 1
        print(f"MISMATCH activity {r[0]}: got {got}, expected {expected[r[0]]}")

project_days = con.execute("SELECT max(earliest_finish) FROM activity_schedule").fetchone()[0]
critical = con.execute("SELECT string_agg(code, ' > ' ORDER BY earliest_start) FROM activity_schedule WHERE is_critical").fetchone()[0]
print("project duration:", int(project_days), "days (expected 188)")
print("critical path:", critical)

all_ok = bad == 0 and len(rows) == 24 and n_paths == 24 and int(project_days) == 188
print("ALL OK" if all_ok else "CHECK ABOVE")
