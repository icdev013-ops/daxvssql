-- 40_metric_designers.sql: the active designer roster of franchise {ENT}, schema {SCHEMA} (arm B+).
-- Applied by `python sandbox/sandbox.py apply-plus`. Reads only the pass-through dim_designers view.
-- Rules (business_rules.md section 6, sql_bindings.md section 6): the roster is the active designers
-- (not deactivated, a project created in the 365 days before the last pipeline build), unassigned or
-- blank names excluded. List the active designers = roster members with at least one project (has_projects).
-- This view is the roster only: sales rankings group by the designer on the project (metric_sales),
-- active or not. No semicolons or apostrophes inside comments: the mysql client parses them.
CREATE OR REPLACE SQL SECURITY DEFINER VIEW `{SCHEMA}`.`metric_designers` AS
SELECT
  d.`Designer` AS designer,
  d.`Sort` AS sort_order,
  d.`Total Projects` AS total_projects,
  CAST(COALESCE(d.`Total Projects`, 0) > 0 AS UNSIGNED) AS has_projects
FROM `{SCHEMA}`.`dim_designers` AS d
WHERE d.`Designer` IS NOT NULL
  AND TRIM(d.`Designer`) <> ''
  AND d.`Designer` NOT LIKE 'unassigned%';
