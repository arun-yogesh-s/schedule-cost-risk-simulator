-- 01_schema.sql
-- Schema for the schedule and cost risk simulator (DuckDB).
-- Durations are in calendar days. Costs are in USD.
-- DuckDB notes for PostgreSQL users:
--   * Runs in-process, so there is no server, user or database to create.
--   * Constraints (PRIMARY KEY, FOREIGN KEY, CHECK) work as in PostgreSQL.
--   * Run the whole file again to rebuild: the DROP statements below make it repeatable.

DROP TABLE IF EXISTS simulation_activity_results;
DROP TABLE IF EXISTS simulation_runs;
DROP TABLE IF EXISTS risks;
DROP TABLE IF EXISTS dependencies;
DROP TABLE IF EXISTS activities;
DROP TABLE IF EXISTS scenarios;
DROP TABLE IF EXISTS projects;

CREATE TABLE projects (
    project_id      INTEGER PRIMARY KEY,
    name            VARCHAR NOT NULL,
    planned_start   DATE NOT NULL,
    target_finish   DATE NOT NULL,
    budget_usd      DOUBLE NOT NULL CHECK (budget_usd > 0),
    CHECK (target_finish > planned_start)
);

-- One row per activity. Three-point estimate: optimistic <= most likely <= pessimistic.
-- daily_cost_usd is the burn rate while the activity is running.
CREATE TABLE activities (
    activity_id     INTEGER PRIMARY KEY,
    project_id      INTEGER NOT NULL REFERENCES projects (project_id),
    code            VARCHAR NOT NULL,
    name            VARCHAR NOT NULL,
    phase           VARCHAR NOT NULL,
    optimistic_days INTEGER NOT NULL CHECK (optimistic_days > 0),
    likely_days     INTEGER NOT NULL,
    pessimistic_days INTEGER NOT NULL,
    daily_cost_usd  DOUBLE NOT NULL CHECK (daily_cost_usd >= 0),
    CHECK (likely_days >= optimistic_days),
    CHECK (pessimistic_days >= likely_days),
    UNIQUE (project_id, code)
);

-- Finish-to-start links with no lag: the successor starts when the predecessor finishes.
CREATE TABLE dependencies (
    predecessor_id  INTEGER NOT NULL REFERENCES activities (activity_id),
    successor_id    INTEGER NOT NULL REFERENCES activities (activity_id),
    PRIMARY KEY (predecessor_id, successor_id),
    CHECK (predecessor_id <> successor_id)
);

-- Two scenarios share the same risks:
--   baseline  uses probability
--   mitigated uses mitigated_probability and adds mitigation_cost_usd
CREATE TABLE scenarios (
    scenario_id     INTEGER PRIMARY KEY,
    name            VARCHAR NOT NULL UNIQUE,
    description     VARCHAR
);

-- A risk event hits one activity. If it happens, it adds a delay (uniform between the
-- min and max) to that activity and a fixed cost impact.
CREATE TABLE risks (
    risk_id                 INTEGER PRIMARY KEY,
    project_id              INTEGER NOT NULL REFERENCES projects (project_id),
    activity_id             INTEGER NOT NULL REFERENCES activities (activity_id),
    name                    VARCHAR NOT NULL,
    probability             DOUBLE NOT NULL CHECK (probability BETWEEN 0 AND 1),
    mitigated_probability   DOUBLE NOT NULL CHECK (mitigated_probability BETWEEN 0 AND 1),
    delay_days_min          INTEGER NOT NULL CHECK (delay_days_min >= 0),
    delay_days_max          INTEGER NOT NULL,
    cost_impact_usd         DOUBLE NOT NULL CHECK (cost_impact_usd >= 0),
    mitigation_cost_usd     DOUBLE NOT NULL CHECK (mitigation_cost_usd >= 0),
    CHECK (delay_days_max >= delay_days_min),
    CHECK (mitigated_probability <= probability)
);

-- Results tables, filled by 03_simulate.sql (next step).
CREATE TABLE simulation_runs (
    scenario_id     INTEGER NOT NULL REFERENCES scenarios (scenario_id),
    run_id          INTEGER NOT NULL,
    finish_day      DOUBLE NOT NULL,
    total_cost_usd  DOUBLE NOT NULL,
    PRIMARY KEY (scenario_id, run_id)
);

CREATE TABLE simulation_activity_results (
    scenario_id     INTEGER NOT NULL,
    run_id          INTEGER NOT NULL,
    activity_id     INTEGER NOT NULL REFERENCES activities (activity_id),
    duration_days   DOUBLE NOT NULL,
    start_day       DOUBLE NOT NULL,
    finish_day      DOUBLE NOT NULL,
    on_critical_path BOOLEAN NOT NULL,
    PRIMARY KEY (scenario_id, run_id, activity_id)
);
