-- 03_critical_path.sql
-- Critical path method (CPM) in SQL, using the most-likely duration of each activity.
--
-- Approach, in two steps:
--   1. A recursive CTE walks the dependency network from the start activity and builds every
--      start-to-finish path. Result: table network_paths (path_id, activity_id, position).
--   2. Window functions add up durations along each path, forwards and backwards. Grouping by
--      activity then gives earliest/latest start and finish, total float and the critical flag.
--
-- Why enumerate paths? A recursive CTE in DuckDB (as in PostgreSQL) cannot use MAX() or other
-- aggregates inside its recursive part, so a normal "earliest start = max of predecessors' finish"
-- pass cannot be written directly. Path enumeration is cheap for networks of this size
-- (24 activities, 24 paths), and the same table is reused by the Monte Carlo simulation.
--
-- Run after 01_schema.sql and 02_seed.sql. Rerun this file after reloading the data.

DROP VIEW IF EXISTS activity_schedule;
DROP TABLE IF EXISTS network_paths;

-- Step 1: every path from a start activity (no predecessors) to an end activity (no successors).
CREATE TABLE network_paths AS
WITH RECURSIVE walk (activity_id, path) AS (
    -- Anchor: activities with no predecessor
    SELECT a.activity_id, [a.activity_id]
    FROM activities a
    WHERE NOT EXISTS (
        SELECT 1 FROM dependencies d WHERE d.successor_id = a.activity_id
    )
    UNION ALL
    -- Recursive step: extend each path by one successor
    SELECT d.successor_id, list_append(w.path, d.successor_id)
    FROM walk w
    JOIN dependencies d ON d.predecessor_id = w.activity_id
),
full_paths AS (
    -- Keep only paths that end at an activity with no successor
    SELECT row_number() OVER (ORDER BY path) AS path_id, path
    FROM walk w
    WHERE NOT EXISTS (
        SELECT 1 FROM dependencies d WHERE d.predecessor_id = w.activity_id
    )
)
-- One row per activity per path, with its position in the path (1 = first)
SELECT path_id, activity_id, list_position(path, activity_id) AS position
FROM (
    SELECT path_id, path, unnest(path) AS activity_id
    FROM full_paths
);

-- Step 2: forward and backward pass with window functions.
CREATE VIEW activity_schedule AS
WITH timed AS (
    SELECT
        a.project_id,
        np.path_id,
        np.activity_id,
        np.position,
        a.likely_days,
        -- Days from project start to the finish of this activity along this path
        sum(a.likely_days) OVER (
            PARTITION BY np.path_id ORDER BY np.position
        ) AS days_through_here,
        -- Days from the start of this activity to the end of this path
        sum(a.likely_days) OVER (
            PARTITION BY np.path_id ORDER BY np.position
            ROWS BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING
        ) AS days_from_here
    FROM network_paths np
    JOIN activities a ON a.activity_id = np.activity_id
),
per_activity AS (
    -- The longest path through the activity decides its dates
    SELECT
        project_id,
        activity_id,
        any_value(likely_days)   AS duration_days,
        max(days_through_here)   AS earliest_finish,
        max(days_from_here)      AS longest_tail
    FROM timed
    GROUP BY project_id, activity_id
),
project_length AS (
    SELECT project_id, max(earliest_finish) AS project_days
    FROM per_activity
    GROUP BY project_id
)
SELECT
    pa.project_id,
    pa.activity_id,
    a.code,
    a.name,
    pa.duration_days,
    pa.earliest_finish - pa.duration_days                          AS earliest_start,
    pa.earliest_finish                                             AS earliest_finish,
    pl.project_days - pa.longest_tail                              AS latest_start,
    pl.project_days - pa.longest_tail + pa.duration_days           AS latest_finish,
    (pl.project_days - pa.longest_tail)
        - (pa.earliest_finish - pa.duration_days)                  AS total_float,
    ((pl.project_days - pa.longest_tail)
        - (pa.earliest_finish - pa.duration_days)) = 0             AS is_critical
FROM per_activity pa
JOIN project_length pl ON pl.project_id = pa.project_id
JOIN activities a      ON a.activity_id = pa.activity_id;

-- Try these once the file has run:
--
--   SELECT * FROM activity_schedule ORDER BY earliest_start, activity_id;
--
--   SELECT string_agg(code, ' > ' ORDER BY earliest_start) AS critical_path
--   FROM activity_schedule WHERE is_critical;
--
--   SELECT max(earliest_finish) AS project_days FROM activity_schedule;
