-- 05_results.sql
-- Results layer: views over the simulation tables, so the app and any ad-hoc query read from one place.
--
--   scenario_summary      P10/P50/P80/P90 finish days and cost, chance of hitting target and budget
--   contingency_p80       what to add to the plan, target and budget to be 80% confident
--   activity_sensitivity  criticality index and duration-to-finish correlation per activity
--   risk_impact           how much each risk moves the finish date and cost when it happens
--   finish_distribution   histogram and cumulative (S-curve) of finish day, 5-day bins
--   scenario_comparison   baseline vs mitigated in one row
--
-- Run after 04_simulate.sql.
-- To rebuild everything from scratch, delete risk_simulator.duckdb and run files 01 to 05 in order.
--
-- DuckDB notes for PostgreSQL users:
--   * quantile_cont(x, 0.8) is the same as PostgreSQL's percentile_cont(0.8) WITHIN GROUP (ORDER BY x).
--   * FILTER (WHERE ...) on aggregates and window functions over aggregates work as in PostgreSQL.
--   * Subtracting two DATE values gives a whole number of days in both.

DROP VIEW IF EXISTS scenario_comparison;
DROP VIEW IF EXISTS finish_distribution;
DROP VIEW IF EXISTS risk_impact;
DROP VIEW IF EXISTS activity_sensitivity;
DROP VIEW IF EXISTS contingency_p80;
DROP VIEW IF EXISTS scenario_summary;

-- 1. Headline numbers per scenario
CREATE VIEW scenario_summary AS
SELECT
    s.scenario_id,
    s.name                                   AS scenario,
    count(*)                                 AS runs,
    avg(r.finish_day)                        AS mean_days,
    quantile_cont(r.finish_day, 0.10)        AS p10_days,
    quantile_cont(r.finish_day, 0.50)        AS p50_days,
    quantile_cont(r.finish_day, 0.80)        AS p80_days,
    quantile_cont(r.finish_day, 0.90)        AS p90_days,
    avg(r.total_cost_usd)                    AS mean_cost_usd,
    quantile_cont(r.total_cost_usd, 0.10)    AS p10_cost_usd,
    quantile_cont(r.total_cost_usd, 0.50)    AS p50_cost_usd,
    quantile_cont(r.total_cost_usd, 0.80)    AS p80_cost_usd,
    quantile_cont(r.total_cost_usd, 0.90)    AS p90_cost_usd,
    p.target_days,
    p.budget_usd,
    -- Share of runs that meet the target finish date and the budget
    avg(CASE WHEN r.finish_day     <= p.target_days THEN 1.0 ELSE 0.0 END) AS prob_on_time,
    avg(CASE WHEN r.total_cost_usd <= p.budget_usd  THEN 1.0 ELSE 0.0 END) AS prob_within_budget
FROM simulation_runs r
JOIN scenarios s ON s.scenario_id = r.scenario_id
CROSS JOIN (
    SELECT budget_usd, (target_finish - planned_start) AS target_days
    FROM projects
    WHERE project_id = 1
) p
GROUP BY s.scenario_id, s.name, p.target_days, p.budget_usd;

-- 2. Contingency: the gap between the plan (most-likely durations, no risk events) and P80
CREATE VIEW contingency_p80 AS
WITH plan AS (
    SELECT
        (SELECT max(earliest_finish) FROM activity_schedule)                 AS plan_days,
        (SELECT sum(likely_days * daily_cost_usd) FROM activities
          WHERE project_id = 1)                                              AS plan_cost_usd
)
SELECT
    ss.scenario,
    pl.plan_days,
    ss.target_days,
    ss.p80_days,
    ss.p80_days - pl.plan_days        AS days_over_plan,
    ss.p80_days - ss.target_days      AS days_over_target,
    pl.plan_cost_usd,
    ss.budget_usd,
    ss.p80_cost_usd,
    ss.p80_cost_usd - pl.plan_cost_usd AS cost_over_plan_usd,
    ss.p80_cost_usd - ss.budget_usd    AS cost_over_budget_usd
FROM scenario_summary ss
CROSS JOIN plan pl;

-- 3. Which activities drive the finish date
--    criticality_pct: share of runs in which the activity is on the critical path
--    finish_correlation: correlation between the activity's duration and the project finish day
CREATE VIEW activity_sensitivity AS
SELECT
    s.scenario_id,
    s.name                                   AS scenario,
    a.activity_id,
    a.code,
    a.name                                   AS activity,
    a.likely_days,
    avg(sar.duration_days)                   AS mean_duration_days,
    avg(sar.duration_days) - a.likely_days   AS mean_slip_days,
    100.0 * avg(CASE WHEN sar.on_critical_path THEN 1.0 ELSE 0.0 END) AS criticality_pct,
    corr(sar.duration_days, r.finish_day)    AS finish_correlation
FROM simulation_activity_results sar
JOIN simulation_runs r ON r.scenario_id = sar.scenario_id AND r.run_id = sar.run_id
JOIN scenarios s       ON s.scenario_id = sar.scenario_id
JOIN activities a      ON a.activity_id = sar.activity_id
GROUP BY s.scenario_id, s.name, a.activity_id, a.code, a.name, a.likely_days;

-- 4. Which risks matter (tornado chart input)
--    Average finish day (and cost) in runs where the risk happens minus runs where it does not.
--    This is a fair comparison because each risk is drawn independently of everything else.
CREATE VIEW risk_impact AS
WITH hits AS (
    SELECT
        s.scenario_id,
        s.name AS scenario,
        k.risk_id,
        k.name AS risk,
        a.code AS activity_code,
        CASE WHEN s.name = 'mitigated' THEN k.mitigated_probability ELSE k.probability END AS probability,
        ur.u_occur < CASE WHEN s.name = 'mitigated' THEN k.mitigated_probability
                          ELSE k.probability END AS occurred,
        r.finish_day,
        r.total_cost_usd
    FROM scenarios s
    CROSS JOIN risks k
    JOIN activities a      ON a.activity_id = k.activity_id
    JOIN sim_u_risk ur     ON ur.risk_id = k.risk_id
    JOIN simulation_runs r ON r.scenario_id = s.scenario_id AND r.run_id = ur.run_id
)
SELECT
    scenario_id,
    scenario,
    risk_id,
    risk,
    activity_code,
    probability,
    count(*) FILTER (WHERE occurred) AS runs_with_risk,
    avg(finish_day)     FILTER (WHERE occurred) - avg(finish_day)     FILTER (WHERE NOT occurred)
        AS finish_impact_days,
    avg(total_cost_usd) FILTER (WHERE occurred) - avg(total_cost_usd) FILTER (WHERE NOT occurred)
        AS cost_impact_usd
FROM hits
GROUP BY scenario_id, scenario, risk_id, risk, activity_code, probability;

-- 5. Distribution of finish day in 5-day bins, with the cumulative share (the S-curve)
CREATE VIEW finish_distribution AS
WITH binned AS (
    SELECT
        scenario_id,
        (floor(finish_day / 5) * 5)::INTEGER AS bin_start_days,
        count(*)                             AS runs
    FROM simulation_runs
    GROUP BY scenario_id, (floor(finish_day / 5) * 5)::INTEGER
)
SELECT
    s.name AS scenario,
    b.scenario_id,
    b.bin_start_days,
    b.bin_start_days + 5 AS bin_end_days,
    b.runs,
    b.runs::DOUBLE / sum(b.runs) OVER (PARTITION BY b.scenario_id) AS pct_of_runs,
    sum(b.runs) OVER (PARTITION BY b.scenario_id ORDER BY b.bin_start_days)::DOUBLE
        / sum(b.runs) OVER (PARTITION BY b.scenario_id)            AS cumulative_pct
FROM binned b
JOIN scenarios s ON s.scenario_id = b.scenario_id;

-- 6. Baseline vs mitigated in one row. Positive "saved" values mean mitigation is better.
--    Mitigated costs already include the mitigation spend, so cost_saved is net of it.
CREATE VIEW scenario_comparison AS
SELECT
    b.p50_days - m.p50_days                      AS p50_days_saved,
    b.p80_days - m.p80_days                      AS p80_days_saved,
    b.mean_cost_usd - m.mean_cost_usd            AS mean_cost_saved_usd,
    b.p80_cost_usd  - m.p80_cost_usd             AS p80_cost_saved_usd,
    (SELECT sum(mitigation_cost_usd) FROM risks) AS mitigation_spend_usd,
    m.prob_on_time - b.prob_on_time              AS prob_on_time_gain,
    m.prob_within_budget - b.prob_within_budget  AS prob_within_budget_gain
FROM scenario_summary b
JOIN scenario_summary m ON b.scenario = 'baseline' AND m.scenario = 'mitigated';

-- Try these once the file has run:
--
--   SELECT * FROM scenario_summary;
--   SELECT * FROM contingency_p80;
--   SELECT * FROM scenario_comparison;
--
--   SELECT code, activity, round(criticality_pct, 1) AS criticality_pct
--   FROM activity_sensitivity WHERE scenario = 'baseline' ORDER BY criticality_pct DESC;
--
--   SELECT risk, round(finish_impact_days, 1) AS days, round(cost_impact_usd) AS usd
--   FROM risk_impact WHERE scenario = 'baseline' ORDER BY finish_impact_days DESC;
