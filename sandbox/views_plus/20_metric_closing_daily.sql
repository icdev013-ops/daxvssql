-- 20_metric_closing_daily.sql: governed closing-rate building blocks for franchise {ENT}, schema {SCHEMA} (arm B+).
-- Applied by `python sandbox/sandbox.py apply-plus`. Reads only the pass-through views of the same schema.
-- Grain: one row per calendar day x designer x lead source x lead source group that has at least one
-- event, where an event is a project created that day or a Sold project whose sold date is that day.
-- Days stay inside the reporting calendar 2020-01-01 .. 2030-12-31 (rules section 2 and 5), so an all time
-- closing rate is the sum over every row. Any window (year to date, a month, the last 4 months, the
-- 120 days ending on the anchor date) is a sum over its days, then one division:
--   C1 closing rate (default)  = LEAST(1, SUM(projects_sold) / NULLIF(SUM(projects_created), 0))
--   C2 cohort closing rate     = SUM(projects_sold) / NULLIF(SUM(projects_created_excl_dead), 0)
--   C3 120-day closing rate    = C2 over the 120 days ending on the anchor date. The KPI tile counts raw
--                                stages Complete, Ready to Install, Ready to Order, Incomplete, which are
--                                exactly Revised Stage Sold, so projects_sold is its numerator too.
--   C4 count-based (weekly)    = SUM(c4_sold) / NULLIF(SUM(c4_decided), 0), both on the created date.
-- The cap of C1 applies to the window total, never per row. Rules: business_rules.md section 5,
-- sql_bindings.md section 5. No semicolons or apostrophes inside comments.
CREATE OR REPLACE SQL SECURITY DEFINER VIEW `{SCHEMA}`.`metric_closing_daily` AS
SELECT
  e.event_date,
  e.designer,
  e.is_active_designer,
  e.lead_source,
  e.lead_source_group,
  CAST(SUM(e.created) AS UNSIGNED) AS projects_created,
  CAST(SUM(e.created_excl_dead) AS UNSIGNED) AS projects_created_excl_dead,
  CAST(SUM(e.sold_on_sold_date) AS UNSIGNED) AS projects_sold,
  CAST(SUM(e.created_sold) AS UNSIGNED) AS c4_sold,
  CAST(SUM(e.created_decided) AS UNSIGNED) AS c4_decided
FROM (
  SELECT
    CAST(s.`Created Date` AS DATE) AS event_date,
    s.`Designer` AS designer,
    CAST(EXISTS (SELECT 1 FROM `{SCHEMA}`.`dim_designers` AS d WHERE d.`Designer` = s.`Designer`) AS UNSIGNED) AS is_active_designer,
    s.`Lead Source` AS lead_source,
    s.`Lead Source Group` AS lead_source_group,
    1 AS created,
    CAST(s.`Revised Stage` <> 'Dead Lead' AS UNSIGNED) AS created_excl_dead,
    0 AS sold_on_sold_date,
    CAST(s.`Revised Stage` = 'Sold' AS UNSIGNED) AS created_sold,
    CAST(s.`Revised Stage` IN ('Sold', 'Lost', 'Dead Lead') AS UNSIGNED) AS created_decided
  FROM `{SCHEMA}`.`Salesforce_Data` AS s
  WHERE s.`Created Date` >= DATE '2020-01-01' AND s.`Created Date` < DATE '2031-01-01'
  UNION ALL
  SELECT
    CAST(s.`Original Project Sold Date` AS DATE) AS event_date,
    s.`Designer` AS designer,
    CAST(EXISTS (SELECT 1 FROM `{SCHEMA}`.`dim_designers` AS d WHERE d.`Designer` = s.`Designer`) AS UNSIGNED) AS is_active_designer,
    s.`Lead Source` AS lead_source,
    s.`Lead Source Group` AS lead_source_group,
    0 AS created,
    0 AS created_excl_dead,
    1 AS sold_on_sold_date,
    0 AS created_sold,
    0 AS created_decided
  FROM `{SCHEMA}`.`Salesforce_Data` AS s
  WHERE s.`Revised Stage` = 'Sold'
    AND s.`Original Project Sold Date` >= DATE '2020-01-01' AND s.`Original Project Sold Date` < DATE '2031-01-01'
) AS e
GROUP BY e.event_date, e.designer, e.is_active_designer, e.lead_source, e.lead_source_group;
