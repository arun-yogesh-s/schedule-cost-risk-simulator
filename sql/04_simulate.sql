-- 04_simulate.sql
-- Monte Carlo simulation of project finish date and cost, written in SQL.
-- 10,000 runs for each scenario (baseline and mitigated). Change the 10000 below to use fewer or more.
--
-- Run after 01_schema.sql, 02_seed.sql and 03_critical_path.sql. Rerunning replaces the old results.
--
-- Design choices worth knowing:
--   * All random numbers are drawn once, up front, into two tables (sim_u_activity, sim_u_risk).
--     Everything after that is a deterministic function of those tables, so a run can be audited
--     and a seed makes the whole file reproducible.
--   * Common random numbers: both scenarios use the same draws. A risk happens in a run when
--     u_occur < probability, so every risk that happens in the mitigated scenario also happens in
--     the baseline. The difference between scenarios is then the effect of mitigation, not noise.
--   * Triangular durations use the inverse CDF method, so only random() is needed.
--   * Each run's critical path comes from network_paths (step 03): the finish date is the
--     longest path in that run, and an activity is critical if the longest path goes through it.
--   * Assumes a single project (project_id = 1). A second project would need project_id carried
--     into the results tables.
--
-- DuckDB notes for PostgreSQL users:
--   * generate_series(1, N) AS r(run_id) works as a table, as in PostgreSQL.
--   * setseed() takes a value between -1 and 1 in both. DuckDB only repeats the same random numbers
--     when a single thread generates them, hence SET threads = 1 around the draws.

-- 0. Clear previous results
DELETE FROM simulation_activity_results;
DELETE FROM simulation_runs;
DROP TABLE IF EXISTS sim_u_activity;
DROP TABLE IF EXISTS sim_u_risk;

-- 1. Random draws (uniform 0-1), stored so that later steps are deterministic
SELECT setseed(0.42);
SET threads = 1;

CREATE TABLE sim_u_activity AS
SELECT r.run_id, a.activity_id, random() AS u
FROM generate_series(1, 10000) AS r(run_id)
CROSS JOIN activities a;

CREATE TABLE sim_u_risk AS
SELECT r.run_id, k.risk_id, random() AS u_occur, random() AS u_delay
FROM generate_series(1, 10000) AS r(run_id)
CROSS JOIN risks k;

RESET threads;

-- 2. Durations and critical path for every run in every scenario
INSERT INTO simulation_activity_results
    (scenario_id, run_id, activity_id, duration_days, start_day, finish_day, on_critical_path)
WITH base AS (
    -- Triangular(optimistic, likely, pessimistic) by inverse CDF
    SELECT
        ua.run_id,
        ua.activity_id,
        CASE
            WHEN a.pessimistic_days = a.optimistic_days THEN a.optimistic_days::DOUBLE
            WHEN ua.u < (a.likely_days - a.optimistic_days)::DOUBLE
                        / (a.pessimistic_days - a.optimistic_days)
                THEN a.optimistic_days
                     + sqrt(ua.u * (a.pessimistic_days - a.optimistic_days)
                                 * (a.likely_days - a.optimistic_days))
            ELSE a.pessimistic_days
                 - sqrt((1 - ua.u) * (a.pessimistic_days - a.optimistic_days)
                                   * (a.pessimistic_days - a.likely_days))
        END AS base_days
    FROM sim_u_activity ua
    JOIN activities a ON a.activity_id = ua.activity_id
),
delays AS (
    -- Risk events that happen in each run and scenario add a uniform delay to their activity
    SELECT
        s.scenario_id,
        ur.run_id,
        r.activity_id,
        sum(r.delay_days_min + ur.u_delay * (r.delay_days_max - r.delay_days_min)) AS delay_days
    FROM scenarios s
    CROSS JOIN sim_u_risk ur
    JOIN risks r ON r.risk_id = ur.risk_id
    WHERE ur.u_occur < CASE WHEN s.name = 'mitigated' THEN r.mitigated_probability
                            ELSE r.probability END
    GROUP BY s.scenario_id, ur.run_id, r.activity_id
),
dur AS (
    SELECT
        s.scenario_id,
        b.run_id,
        b.activity_id,
        b.base_days + coalesce(d.delay_days, 0) AS duration_days
    FROM scenarios s
    CROSS JOIN base b
    LEFT JOIN delays d
           ON d.scenario_id = s.scenario_id
          AND d.run_id      = b.run_id
          AND d.activity_id = b.activity_id
),
timed AS (
    -- Same window-function pass as 03_critical_path.sql, now once per run and scenario
    SELECT
        d.scenario_id,
        d.run_id,
        np.path_id,
        np.activity_id,
        d.duration_days,
        sum(d.duration_days) OVER (
            PARTITION BY d.scenario_id, d.run_id, np.path_id ORDER BY np.position
        ) AS days_through_here,
        sum(d.duration_days) OVER (
            PARTITION BY d.scenario_id, d.run_id, np.path_id ORDER BY np.position
            ROWS BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING
        ) AS days_from_here
    FROM dur d
    JOIN network_paths np ON np.activity_id = d.activity_id
),
per_activity AS (
    SELECT
        scenario_id,
        run_id,
        activity_id,
        any_value(duration_days) AS duration_days,
        max(days_through_here)   AS earliest_finish,
        max(days_from_here)      AS tail_days
    FROM timed
    GROUP BY scenario_id, run_id, activity_id
),
run_length AS (
    SELECT scenario_id, run_id, max(earliest_finish) AS finish_day
    FROM per_activity
    GROUP BY scenario_id, run_id
)
SELECT
    pa.scenario_id,
    pa.run_id,
    pa.activity_id,
    pa.duration_days,
    pa.earliest_finish - pa.duration_days AS start_day,
    pa.earliest_finish                    AS finish_day,
    -- Critical when the longest path through the activity is the longest path in the run
    (rl.finish_day - pa.tail_days) - (pa.earliest_finish - pa.duration_days) < 1e-9
        AS on_critical_path
FROM per_activity pa
JOIN run_length rl
  ON rl.scenario_id = pa.scenario_id AND rl.run_id = pa.run_id;

-- 3. Finish day and total cost for every run
INSERT INTO simulation_runs (scenario_id, run_id, finish_day, total_cost_usd)
WITH finish AS (
    SELECT scenario_id, run_id, max(finish_day) AS finish_day
    FROM simulation_activity_results
    GROUP BY scenario_id, run_id
),
activity_cost AS (
    SELECT sar.scenario_id, sar.run_id,
           sum(sar.duration_days * a.daily_cost_usd) AS activity_cost_usd
    FROM simulation_activity_results sar
    JOIN activities a ON a.activity_id = sar.activity_id
    GROUP BY sar.scenario_id, sar.run_id
),
risk_cost AS (
    SELECT s.scenario_id, ur.run_id, sum(r.cost_impact_usd) AS risk_cost_usd
    FROM scenarios s
    CROSS JOIN sim_u_risk ur
    JOIN risks r ON r.risk_id = ur.risk_id
    WHERE ur.u_occur < CASE WHEN s.name = 'mitigated' THEN r.mitigated_probability
                            ELSE r.probability END
    GROUP BY s.scenario_id, ur.run_id
),
mitigation AS (
    -- Mitigation actions are paid for in every run of the mitigated scenario
    SELECT s.scenario_id,
           CASE WHEN s.name = 'mitigated'
                THEN (SELECT sum(mitigation_cost_usd) FROM risks)
                ELSE 0 END AS mitigation_cost_usd
    FROM scenarios s
)
SELECT
    f.scenario_id,
    f.run_id,
    f.finish_day,
    ac.activity_cost_usd + coalesce(rc.risk_cost_usd, 0) + m.mitigation_cost_usd
FROM finish f
JOIN activity_cost ac ON ac.scenario_id = f.scenario_id AND ac.run_id = f.run_id
LEFT JOIN risk_cost rc ON rc.scenario_id = f.scenario_id AND rc.run_id = f.run_id
JOIN mitigation m      ON m.scenario_id = f.scenario_id;

-- Try these once the file has run:
--
--   SELECT s.name,
--          round(quantile_cont(r.finish_day, 0.5), 1)  AS p50_days,
--          round(quantile_cont(r.finish_day, 0.8), 1)  AS p80_days,
--          round(quantile_cont(r.total_cost_usd, 0.8)) AS p80_cost_usd
--   FROM simulation_runs r JOIN scenarios s USING (scenario_id)
--   GROUP BY s.name;
--
--   -- Criticality index: share of runs in which each activity is on the critical path
--   SELECT a.code, a.name,
--          round(100.0 * avg(CASE WHEN sar.on_critical_path THEN 1 ELSE 0 END), 1) AS criticality_pct
--   FROM simulation_activity_results sar JOIN activities a USING (activity_id)
--   WHERE sar.scenario_id = 1
--   GROUP BY a.code, a.name
--   ORDER BY criticality_pct DESC;
