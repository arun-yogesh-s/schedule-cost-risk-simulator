-- 02_seed.sql
-- Sample project: a fictional substation upgrade. All names and figures are invented.
-- Durations are in calendar days, costs in USD.

INSERT INTO projects VALUES
    (1, 'Substation Upgrade (sample)', DATE '2027-01-04', DATE '2027-07-23', 1700000);

INSERT INTO scenarios VALUES
    (1, 'baseline',  'Risks as currently assessed, no extra mitigation'),
    (2, 'mitigated', 'Mitigation actions funded: lower probabilities, plus mitigation cost');

-- activity_id, project_id, code, name, phase, optimistic, likely, pessimistic, daily cost
INSERT INTO activities VALUES
    (1,  1, 'A01', 'Project kickoff',                 'Initiation',    2,  3,  5, 1500),
    (2,  1, 'A02', 'Site survey',                     'Engineering',   5,  7, 12, 2500),
    (3,  1, 'A03', 'Basic design',                    'Engineering',  10, 15, 25, 6000),
    (4,  1, 'A04', 'Permit application',              'Initiation',   15, 25, 45, 1200),
    (5,  1, 'A05', 'Detailed design',                 'Engineering',  15, 20, 35, 7000),
    (6,  1, 'A06', 'Design review',                   'Engineering',   3,  5, 10, 3000),
    (7,  1, 'A07', 'Procure long-lead equipment',     'Procurement',  30, 45, 80, 2000),
    (8,  1, 'A08', 'Procure cables and fittings',     'Procurement',  15, 20, 35, 1500),
    (9,  1, 'A09', 'Procure structural steel',        'Procurement',  20, 30, 50, 1800),
    (10, 1, 'A10', 'Site mobilisation',               'Construction',  4,  6, 10, 4000),
    (11, 1, 'A11', 'Civil foundations',               'Construction', 20, 28, 45, 9000),
    (12, 1, 'A12', 'Steel erection',                  'Construction', 12, 18, 30, 8500),
    (13, 1, 'A13', 'Factory acceptance test',         'Testing',       3,  5,  9, 3500),
    (14, 1, 'A14', 'Equipment installation',          'Construction', 15, 22, 40, 9500),
    (15, 1, 'A15', 'Cable laying',                    'Construction', 10, 14, 25, 6500),
    (16, 1, 'A16', 'Electrical termination',          'Construction',  8, 12, 20, 5500),
    (17, 1, 'A17', 'Control system integration',      'Testing',      10, 15, 28, 6000),
    (18, 1, 'A18', 'Site acceptance test',            'Testing',       4,  6, 12, 4000),
    (19, 1, 'A19', 'Safety audit',                    'Commissioning', 2,  3,  6, 2500),
    (20, 1, 'A20', 'Commissioning',                   'Commissioning', 8, 12, 22, 7000),
    (21, 1, 'A21', 'Performance trial',               'Commissioning', 5,  7, 14, 4500),
    (22, 1, 'A22', 'Punch list closure',              'Handover',      5,  8, 15, 3000),
    (23, 1, 'A23', 'Handover documentation',          'Handover',      5,  7, 12, 1500),
    (24, 1, 'A24', 'Project close-out',               'Handover',      2,  3,  5, 1500);

-- predecessor_id, successor_id (finish-to-start)
INSERT INTO dependencies VALUES
    (1, 2), (1, 3), (1, 4),
    (2, 5), (3, 5),
    (5, 6),
    (6, 7), (6, 8), (6, 9), (6, 11),
    (4, 10),
    (10, 11),
    (9, 12), (11, 12),
    (7, 13),
    (13, 14), (12, 14),
    (8, 15), (11, 15),
    (14, 16), (15, 16),
    (16, 17),
    (17, 18),
    (18, 19), (18, 20), (19, 20),
    (20, 21),
    (21, 22),
    (22, 23),
    (23, 24);

-- risk_id, project_id, activity_id, name, probability, mitigated_probability,
-- delay min, delay max, cost impact, mitigation cost
INSERT INTO risks VALUES
    (1, 1,  4, 'Permit approval delayed',          0.35, 0.15, 10, 30,      0, 12000),
    (2, 1,  7, 'Long-lead equipment arrives late', 0.30, 0.15, 15, 45,  40000, 30000),
    (3, 1,  6, 'Design rework after review',       0.25, 0.10,  5, 15,  25000,  8000),
    (4, 1, 11, 'Adverse ground conditions',        0.20, 0.12,  7, 21,  90000, 20000),
    (5, 1, 12, 'Weather delay on steel erection',  0.40, 0.30,  3, 10,  15000,  4000),
    (6, 1,  8, 'Cable price increase',             0.30, 0.10,  0,  5,  60000,  5000),
    (7, 1, 17, 'Control integration defects',      0.35, 0.20,  5, 20,  30000, 10000),
    (8, 1, 18, 'Site acceptance test failure',     0.20, 0.10,  4, 12,  20000,  6000);
