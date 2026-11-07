-- 30_metric_appointments_reported.only_ICLA.sql: ICLA appointments as its report counts them (arm B+).
-- Applied by `python sandbox/sandbox.py apply-plus` to ICLA only. {ICLA_EXCLUDED_STAFF_SQL} is filled at
-- apply time from daxvssql/private/rules_names.md (gitignored) and is never written to a committed file.
-- The three .only_<ENT> files share one column list and differ only in the WHERE clause and counting_rule:
-- keep them in step (apply-plus checks that the columns match).
-- Rule (business_rules.md section 8, sql_bindings.md section 8): first design appointments only.
-- Activity type neither Installation nor Unspecified, subject contains 1st and not Cancelled (case-insensitive
-- substring tests), assigned staff not on the excluded office-staff list, start date on or after 2025-06-01.
-- The COALESCE wrappers keep entries with a blank field, as the report does.
-- Project match by name without fan-out: Salesforce_Data is aggregated per project name first, so each
-- appointment stays one row. matched_project_sold is the bindings EXISTS test (a Sold project has this name).
-- No semicolons or apostrophes inside comments: the mysql client parses them.
CREATE OR REPLACE SQL SECURITY DEFINER VIEW `{SCHEMA}`.`metric_appointments_reported` AS
SELECT
  a.`appointment_key` AS appointment_key,
  a.`Appointment Start Time` AS appointment_date,
  a.`Activity Type` AS activity_type,
  a.`Appointment Subject` AS subject,
  a.`Assigned Staff Member` AS assigned_staff,
  COALESCE(a.`Lead Source Channel`, 'No source recorded') AS appointment_source,
  a.`Project Name` AS project_name,
  a.`Project Stage` AS project_stage,
  CAST(COALESCE(a.`Project Stage` = 'Sold', 0) AS UNSIGNED) AS is_stage_sold,
  a.`Is First Appointment` AS is_first_appointment,
  d.`Designer` AS designer,
  CAST(COALESCE(p.n_projects, 0) AS UNSIGNED) AS matched_projects,
  CASE WHEN p.n_projects = 1 THEN p.any_project_id END AS matched_project_id,
  CAST(COALESCE(p.any_sold, 0) AS UNSIGNED) AS matched_project_sold,
  'first design appointments' AS counting_rule
FROM `{SCHEMA}`.`Appointments` AS a
LEFT JOIN `{SCHEMA}`.`dim_designers` AS d ON d.`designer_sk` = a.`designer_sk`
LEFT JOIN (
  SELECT s.`Project Name` AS project_name,
         COUNT(*) AS n_projects,
         MIN(s.`Project ID`) AS any_project_id,
         MAX(s.`Revised Stage` = 'Sold') AS any_sold
  FROM `{SCHEMA}`.`Salesforce_Data` AS s
  WHERE s.`Project Name` IS NOT NULL
  GROUP BY s.`Project Name`
) AS p ON p.project_name = a.`Project Name`
WHERE COALESCE(a.`Activity Type`, '') NOT IN ('Installation', 'Unspecified')
  AND a.`Appointment Subject` LIKE '%1st%'
  AND a.`Appointment Subject` NOT LIKE '%Cancelled%'
  AND COALESCE(a.`Assigned Staff Member`, '') NOT IN ({ICLA_EXCLUDED_STAFF_SQL})
  AND a.`Appointment Start Time` >= DATE '2025-06-01';
