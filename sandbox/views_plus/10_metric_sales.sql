-- 10_metric_sales.sql: governed sales view for franchise {ENT}, created in schema {SCHEMA} (arm B+).
-- Applied by `python sandbox/sandbox.py apply-plus`, which substitutes {ENT}, {SCHEMA}, {SALES_DATE}
-- (the franchise sales-date column) and {SALES_BASIS} (its label). Reads only the pass-through views of
-- the same schema, which read the Route B mirror poc_<ent> of the same franchise.
-- Rules encoded (rules/business_rules.md sections 3, 4, 6; rules/sql_bindings.md sections 3, 4, 6):
--   * one row per project (Salesforce_Data row; project_id is unique, project_name is not);
--   * sales_date = the franchise default period date: for a Sold project the franchise sales-date basis
--     (ICLA created date, ICCH and ICAU original project sold date); for Lost, Dead Lead and Proposed
--     always the created date. A Sold project with no sold date has a NULL sales_date on the sold basis;
--   * stage flags from Revised Stage (exactly Sold, Proposed, Lost, Dead Lead);
--   * is_active_designer = the designer is on the active roster (dim_designers).
-- No semicolons or apostrophes inside comments: the mysql client parses them.
CREATE OR REPLACE SQL SECURITY DEFINER VIEW `{SCHEMA}`.`metric_sales` AS
SELECT
  s.`Project ID` AS project_id,
  s.`Project Name` AS project_name,
  s.`Revised Stage` AS stage,
  s.`Stage` AS raw_stage,
  s.`Amount` AS amount,
  CASE WHEN s.`Revised Stage` = 'Sold' THEN s.`Amount` END AS sold_amount,
  CAST(s.`Revised Stage` = 'Sold' AS UNSIGNED) AS is_sold,
  CAST(s.`Revised Stage` = 'Proposed' AS UNSIGNED) AS is_proposed,
  CAST(s.`Revised Stage` = 'Lost' AS UNSIGNED) AS is_lost,
  CAST(s.`Revised Stage` = 'Dead Lead' AS UNSIGNED) AS is_dead_lead,
  s.`Created Date` AS created_date,
  s.`Original Project Sold Date` AS sold_date,
  CASE WHEN s.`Revised Stage` = 'Sold' THEN s.{SALES_DATE} ELSE s.`Created Date` END AS sales_date,
  CASE WHEN s.`Revised Stage` = 'Sold' THEN '{SALES_BASIS}' ELSE 'created date' END AS sales_date_basis,
  s.`Designer` AS designer,
  CAST(EXISTS (SELECT 1 FROM `{SCHEMA}`.`dim_designers` AS d WHERE d.`Designer` = s.`Designer`) AS UNSIGNED) AS is_active_designer,
  s.`Lead Source` AS lead_source,
  s.`Lead Source Group` AS lead_source_group,
  s.`Sale Cycle` AS sale_cycle_days
FROM `{SCHEMA}`.`Salesforce_Data` AS s;
