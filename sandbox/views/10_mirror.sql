-- 10_mirror.sql: mirrored Power BI model tables for franchise {ENT}, created in schema {SCHEMA}.
-- Applied by `python sandbox/sandbox.py apply-views`, which substitutes the franchise code and schema.
-- One view per table that the model "Salesforce BI {ENT}" imports, named exactly like the Power BI table,
-- with the Power BI column names, column order and Power Query steps (entity filter, column selection,
-- renames, datetime-to-date casts, active-only designers, distinct lookups, the Lead Milestones native SQL)
-- and the DAX calculated columns and calculated tables. No measures.
-- Column lists were generated from the TMDL and reviewed by hand. See SCHEMA.md and PARITY_NOTES.md.
-- Objects that exist in one franchise model only live in NN_name.only_ENT.sql files.
-- No semicolons or apostrophes inside comments: the mysql client parses them.

-- Salesforce_Data: one row per opportunity (project).
-- M: icall_gold.fact_opportunities (passthrough view over fact_sales), entity filter, Table.SelectColumns
-- keeps 24 Gold columns. The 4 Sold* columns are the DAX calculated columns.
CREATE OR REPLACE SQL SECURITY DEFINER VIEW `{SCHEMA}`.`Salesforce_Data` AS
SELECT
  `Project Name`,
  `Stage`,
  `Original Project Sold Date`,
  `Amount`,
  `Created Date`,
  `Last Modified Date`,
  `Lead Source`,
  `Project Notes`,
  `Project ID`,
  `Consumer Name: Full Name`,
  `Start of Week`,
  `Created Month`,
  YEAR(`Original Project Sold Date`) AS `Sold Year`,
  MONTH(`Original Project Sold Date`) AS `Sold Month Number`,
  ELT(MONTH(`Original Project Sold Date`), 'Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec') AS `Sold Month`,
  DATE_SUB(`Original Project Sold Date`, INTERVAL DAYOFWEEK(`Original Project Sold Date`) - 1 DAY) AS `Sold Start of Week`,
  `Lead Source Group`,
  `Designer`,
  `Revised Stage`,
  `Sale Cycle`,
  `Sale to Complete Days`,
  `Lead Time`,
  `opportunity_sk`,
  `entity`,
  `Data Ingested At`,
  `Final Completion Date`,
  `Target Ready to Order Date`,
  `Owner First Appointments Last 12 Months`
FROM icall_gold.fact_opportunities
WHERE `entity` = '{ENT}';

-- Leads: one row per Salesforce lead.
-- M: icall_gold.fact_leads, `Lead Created Date` cast to date, entity filter. All Gold columns kept.
CREATE OR REPLACE SQL SECURITY DEFINER VIEW `{SCHEMA}`.`Leads` AS
SELECT
  `Index`,
  CAST(`Lead Created Date` AS DATE) AS `Lead Created Date`,
  `Lead Source Channel`,
  `Lead Status`,
  `Lead Capture Form Type`,
  `Lead Creator Name`,
  `Customer First Name`,
  `Customer Last Name`,
  `Customer Email`,
  `Customer Phone`,
  `Showroom Location`,
  `Customer Street Address`,
  `Customer City`,
  `Customer State`,
  `Customer ZIP Code`,
  `Preferred Call Time`,
  `Preferred Call Day`,
  `Contact Time Preference`,
  `Is Referral Lead`,
  `Consultation Requested`,
  `Is Converted Lead`,
  `Customer Account Number`,
  `Converted Customer Name`,
  `Lead Conversion Date`,
  `Converted Project Name`,
  `Interested Home Area`,
  `Lead Source from URL`,
  `Lead ID`,
  `Lead Creation Timestamp`,
  `Lead Type`,
  `Lead Quality Rating`,
  `Lead Description`,
  `Initial Comments`,
  `Project Notes`,
  `Lead Disqualification Reason`,
  `Lead Nurturing Reason`,
  `Has Showroom Visit`,
  `Additional Source Information`,
  `Preferred Communication Method`,
  `Estimated Home Value`,
  `Lead Status Category`,
  `Lead Age in Days`,
  `Lead Age Category`,
  `Lead Creation Year-Month`,
  `Customer Full Name`,
  `Contact Attempt Stage`,
  `DataType`,
  `Customer Full Address`,
  `lead_sk`,
  `entity`,
  `is_likely_duplicate`,
  `duplicate_of_lead_id`,
  `Data Ingested At`,
  `Customer State Code`,
  `customer_sk`,
  `geography_sk`,
  `lead_source_sk`,
  `lead_status_sk`,
  `form_type_sk`,
  `project_sk`,
  `entity_sk`
FROM icall_gold.fact_leads
WHERE `entity` = '{ENT}';

-- Appointments: one row per calendar event or task (Salesforce Event/Task).
-- M: icall_gold.fact_appointments, 3 datetime columns cast to date (time of day dropped), entity filter.
CREATE OR REPLACE SQL SECURITY DEFINER VIEW `{SCHEMA}`.`Appointments` AS
SELECT
  CAST(`Activity Date and Time` AS DATE) AS `Activity Date and Time`,
  `Activity Type`,
  `Is First Appointment`,
  `Lead Name`,
  `Lead Source Channel`,
  `Related Entity`,
  `Appointment Subject`,
  `Assigned Staff Member`,
  `Created By User`,
  CAST(`Appointment Start Time` AS DATE) AS `Appointment Start Time`,
  `Project Stage`,
  `Appointment Location`,
  `Account Manager`,
  `Project Name`,
  `Customer Contact`,
  `Appointment Notes`,
  `Attached Resources`,
  `Meeting Attendees`,
  `Project Status Category`,
  `Activity Year-Month`,
  `Is Future Appointment`,
  `Is Real Appointment`,
  `City`,
  `State`,
  `Zip Code`,
  CAST(`Start Of WeekA` AS DATE) AS `Start Of WeekA`,
  `appointment_sk`,
  `entity`,
  `appointment_key`,
  `Data Ingested At`,
  `designer_sk`,
  `geography_sk`,
  `lead_source_sk`,
  `activity_type_sk`,
  `project_sk`,
  `entity_sk`
FROM icall_gold.fact_appointments
WHERE `entity` = '{ENT}';

-- Leads_Journey: one row per lead, status-history summary.
-- M: icall_gold.fact_lead_journey, entity filter.
CREATE OR REPLACE SQL SECURITY DEFINER VIEW `{SCHEMA}`.`Leads_Journey` AS
SELECT
  `Lead ID`,
  `Current Status`,
  `Lead Journey`,
  `Total Status Changes`,
  `First Status Date`,
  `Latest Status Date`,
  `Latest File Date`,
  `Last Updated`,
  `journey_sk`,
  `entity`,
  `Data Ingested At`,
  `lead_status_sk`,
  `entity_sk`
FROM icall_gold.fact_lead_journey
WHERE `entity` = '{ENT}';

-- Opportunities to Appointments: one row per converted lead with its first appointment (funnel bridge).
-- M: icall_gold.fact_opportunities_to_appointments, 2 datetime columns cast to date, entity filter.
CREATE OR REPLACE SQL SECURITY DEFINER VIEW `{SCHEMA}`.`Opportunities to Appointments` AS
SELECT
  `Index`,
  CAST(`Created Date` AS DATE) AS `Created Date`,
  `Inspired Closets Lead Source`,
  `Lead Status`,
  `Converted Contact: Full Name`,
  `Converted Date`,
  `Converted Project: Project Name`,
  CAST(`Activity Date and Time` AS DATE) AS `Activity Date and Time`,
  `Activity Type`,
  `Inspired Closets Lead Source.1`,
  `Assigned`,
  `Project Stage`,
  `Project`,
  `entity`,
  `Lead ID`,
  `Data Ingested At`
FROM icall_gold.fact_opportunities_to_appointments
WHERE `entity` = '{ENT}';

-- DateTable: calendar 2020-01-01 to 2030-12-31, one row per day.
-- M: icall_gold.dim_date, no entity filter (tenant-agnostic), no other steps.
CREATE OR REPLACE SQL SECURITY DEFINER VIEW `{SCHEMA}`.`DateTable` AS
SELECT
  `Date`,
  `Year`,
  `Month`,
  `MonthNumber`,
  `Quarter`,
  `Year-Month`,
  `MonthYear`,
  `Weekday`,
  `WeekdayNumber`,
  `YearQuarter`,
  `Start Of Week`,
  `Data Ingested At`
FROM icall_gold.dim_date;

-- SoldDateTable: DAX calculated table SoldDateTable = DateTable (role-playing calendar for the sold date).
CREATE OR REPLACE SQL SECURITY DEFINER VIEW `{SCHEMA}`.`SoldDateTable` AS
SELECT
  `Date`,
  `Year`,
  `Month`,
  `MonthNumber`,
  `Quarter`,
  `Year-Month`,
  `MonthYear`,
  `Weekday`,
  `WeekdayNumber`,
  `YearQuarter`,
  `Start Of Week`,
  `Data Ingested At`
FROM `{SCHEMA}`.`DateTable`;

-- dim_designers: designer roster, ACTIVE designers only.
-- M: icall_gold.dim_designers (view over dim_designer), entity filter, active_flag = 1, two renames.
CREATE OR REPLACE SQL SECURITY DEFINER VIEW `{SCHEMA}`.`dim_designers` AS
SELECT
  `Designer`,
  `Sort`,
  `entity`,
  `active_flag`,
  `total_projects` AS `Total Projects`,
  `total_amount` AS `Total Amount`,
  `designer_sk`,
  `first_seen_date`,
  `last_seen_date`,
  `Data Ingested At`
FROM icall_gold.dim_designers
WHERE `entity` = '{ENT}'
  AND `active_flag` = 1;

-- dim_projects: distinct project names, the hub between Salesforce_Data, Leads, Appointments
-- and Opportunities to Appointments. M: icall_gold.dim_projects, entity filter, keep Project Name, Table.Distinct.
CREATE OR REPLACE SQL SECURITY DEFINER VIEW `{SCHEMA}`.`dim_projects` AS
SELECT DISTINCT
  `Project Name`
FROM icall_gold.dim_projects
WHERE `entity` = '{ENT}';

-- Lead Source Channels: distinct lead-source names (tenant-agnostic lookup).
-- M: icall_gold.dim_lead_sources, keep the name, Table.Distinct. No entity filter.
CREATE OR REPLACE SQL SECURITY DEFINER VIEW `{SCHEMA}`.`Lead Source Channels` AS
SELECT DISTINCT
  `Inspired Closets Lead Source`
FROM icall_gold.dim_lead_sources;

-- Source_Sort: lead source with its display sort (tenant-agnostic lookup).
-- M: icall_gold.dim_lead_sources, rename Inspired Closets Lead Source to Lead Source and Source Sort to Sort,
-- Table.Distinct on Lead Source. The name is the primary key of dim_lead_source, so DISTINCT is equivalent.
CREATE OR REPLACE SQL SECURITY DEFINER VIEW `{SCHEMA}`.`Source_Sort` AS
SELECT DISTINCT
  `Source Sort` AS `Sort`,
  `Inspired Closets Lead Source` AS `Lead Source`
FROM icall_gold.dim_lead_sources;

-- LS Group Sort: lead-source group with its display sort (tenant-agnostic lookup, 3 groups).
-- M: icall_gold.dim_lead_sources, keep group and Group Sort renamed to Sort, Table.Distinct on the group.
-- Every group carries a single Group Sort value, so MIN() only makes the one-row-per-group rule explicit.
CREATE OR REPLACE SQL SECURITY DEFINER VIEW `{SCHEMA}`.`LS Group Sort` AS
SELECT
  `Lead Source Group`,
  MIN(`Group Sort`) AS `Sort`
FROM icall_gold.dim_lead_sources
GROUP BY `Lead Source Group`;

-- dim_entity: franchise master list. M: icall_gold.dim_entity, no steps; columns renamed to Title Case.
-- DELIBERATE DEVIATION (row-level security, 2026-11-07): the model imports all 9 rows, but every view a
-- franchise account can read must hold only that franchise's rows, so this view is filtered to {ENT}.
-- See PARITY_NOTES.md and sandbox/rls_test.py.
CREATE OR REPLACE SQL SECURITY DEFINER VIEW `{SCHEMA}`.`dim_entity` AS
SELECT
  `entity`,
  `display_name` AS `Display Name`,
  `region` AS `Region`,
  `is_active` AS `Is Active`,
  `entity_sk`,
  `Data Ingested At`
FROM icall_gold.dim_entity
WHERE `entity` = '{ENT}';

-- Lead Milestones: one row per lead, journey milestones from the lead status history.
-- M: native SQL query (MySQL.Database ... [Query = ...]) reused verbatim with the entity literal,
-- wrapped so the columns come out in the model order. Power Query then only types the columns.
CREATE OR REPLACE SQL SECURITY DEFINER VIEW `{SCHEMA}`.`Lead Milestones` AS
SELECT
  m.`Lead ID`,
  m.`Contact Attempts`,
  m.`Contact Path`,
  m.`Reached Studio`,
  m.`Journey Outcome`,
  m.`Sold Amount`,
  m.`Days to Scheduled`,
  m.`Days to Studio`,
  m.`Days to Sold`,
  m.`Exclude From Journey`,
  m.`Has Status History`,
  m.`Contact Path Order`,
  m.`Journey Outcome Order`,
  m.`Studio Step`,
  m.`Booked Appointment`
FROM (
    SELECT l.`Lead ID`,
      COALESCE(h.attempts, 0) AS `Contact Attempts`,
      CASE WHEN l.`Lead Created Date` < '2025-01-01' THEN 'No contact history' ELSE CASE WHEN h.sched IS NOT NULL OR h.studio IS NOT NULL OR l.project_sk IS NOT NULL THEN CASE WHEN COALESCE(h.attempts, 0) = 0 AND COALESCE(h.followup, 0) = 0 THEN 'Booked first try' ELSE 'Booked after follow-up' END ELSE 'Not booked' END END AS `Contact Path`,
      CASE WHEN h.sched IS NOT NULL OR h.studio IS NOT NULL OR l.project_sk IS NOT NULL THEN 1 ELSE 0 END AS `Booked Appointment`,
      CASE WHEN h.studio IS NOT NULL OR l.project_sk IS NOT NULL THEN 1 ELSE 0 END AS `Reached Studio`,
      CASE WHEN h.studio IS NOT NULL OR l.project_sk IS NOT NULL THEN 'Reached the studio' ELSE 'No studio visit' END AS `Studio Step`,
      CASE WHEN s.stage IS NOT NULL THEN s.stage WHEN l.`Lead Status` = 'Lead Nurturing' THEN 'Nurturing' WHEN l.`Lead Status` IN ('Cancelled', 'Duplicate', 'Junk', 'Non-Qualified Lead') THEN 'Closed' ELSE 'Still working' END AS `Journey Outcome`,
      CASE WHEN l.`Lead Created Date` < '2025-01-01' THEN 4 ELSE CASE WHEN h.sched IS NOT NULL OR h.studio IS NOT NULL OR l.project_sk IS NOT NULL THEN CASE WHEN COALESCE(h.attempts, 0) = 0 AND COALESCE(h.followup, 0) = 0 THEN 1 ELSE 2 END ELSE 3 END END AS `Contact Path Order`,
      CASE COALESCE(s.stage, CASE WHEN l.`Lead Status` = 'Lead Nurturing' THEN 'Nurturing' WHEN l.`Lead Status` IN ('Cancelled', 'Duplicate', 'Junk', 'Non-Qualified Lead') THEN 'Closed' ELSE 'Still working' END) WHEN 'Sold' THEN 1 WHEN 'Proposed' THEN 2 WHEN 'Lost' THEN 3 WHEN 'Dead Lead' THEN 4 WHEN 'Nurturing' THEN 5 WHEN 'Still working' THEN 6 ELSE 7 END AS `Journey Outcome Order`,
      s.sold_amount AS `Sold Amount`,
      ROUND(TIMESTAMPDIFF(MINUTE, l.`Lead Created Date`, h.sched) / 1440, 2) AS `Days to Scheduled`,
      ROUND(TIMESTAMPDIFF(MINUTE, l.`Lead Created Date`, COALESCE(h.studio, s.opp_created)) / 1440, 2) AS `Days to Studio`,
      DATEDIFF(s.sold_date, DATE(l.`Lead Created Date`)) AS `Days to Sold`,
      CASE WHEN l.`Lead Status` IN ('Duplicate', 'Junk') THEN 1 ELSE 0 END AS `Exclude From Journey`,
      CASE WHEN l.`Lead Created Date` >= '2025-01-01' THEN 1 ELSE 0 END AS `Has Status History`
    FROM icall_gold.fact_leads AS l
    LEFT JOIN ( SELECT entity, `Lead ID`, COUNT(DISTINCT CASE WHEN `New Status` LIKE '%Attempt%' THEN `New Status` END) AS attempts, MAX(CASE WHEN `New Status` = 'Needs Follow Up' THEN 1 ELSE 0 END) AS followup, MIN(CASE WHEN `New Status` = 'Scheduled' THEN `Status Change Date` END) AS sched, MIN(CASE WHEN `New Status` = 'Moved to Studio' THEN `Status Change Date` END) AS studio FROM icall_silver.silver_lead_status_history WHERE entity = '{ENT}' GROUP BY entity, `Lead ID` ) AS h ON h.entity = l.entity AND h.`Lead ID` = l.`Lead ID`
    LEFT JOIN ( SELECT entity, project_sk, CASE WHEN MAX(`Revised Stage` = 'Sold') = 1 THEN 'Sold' WHEN MAX(`Revised Stage` = 'Proposed') = 1 THEN 'Proposed' WHEN MAX(`Revised Stage` = 'Lost') = 1 THEN 'Lost' ELSE MAX(`Revised Stage`) END AS stage, MAX(CASE WHEN `Revised Stage` = 'Sold' THEN `Amount` END) AS sold_amount, MIN(CASE WHEN `Revised Stage` = 'Sold' THEN `Original Project Sold Date` END) AS sold_date, MIN(`Created Date`) AS opp_created FROM icall_gold.fact_sales WHERE entity = '{ENT}' AND project_sk IS NOT NULL GROUP BY entity, project_sk ) AS s ON s.entity = l.entity AND s.project_sk = l.project_sk
    WHERE l.entity = '{ENT}'
) AS m;

-- report_scorecard: weekly-report snapshot, scorecard metrics, long shape (one row per metric_key per window per report_date).
-- M: icall_gold.report_scorecard (passthrough view over ic_weekly_report.report_scorecard), entity filter.
CREATE OR REPLACE SQL SECURITY DEFINER VIEW `{SCHEMA}`.`report_scorecard` AS
SELECT
  `id`,
  `entity`,
  `report_date`,
  `metric_key`,
  `metric_label`,
  `metric_kind`,
  `window_name`,
  `value`,
  `created_at`
FROM icall_gold.report_scorecard
WHERE `entity` = '{ENT}';

-- report_designer_leaderboard: weekly-report snapshot, designer leaderboard (one row per designer per report_date).
-- M: icall_gold.report_designer_leaderboard (passthrough view over ic_weekly_report.report_designer_leaderboard), entity filter.
CREATE OR REPLACE SQL SECURITY DEFINER VIEW `{SCHEMA}`.`report_designer_leaderboard` AS
SELECT
  `id`,
  `entity`,
  `report_date`,
  `designer_name`,
  `sold_dollars_mtd`,
  `sold_count_mtd`,
  `decided_count_mtd`,
  `total_opps_mtd`,
  `closing_rate_mtd`,
  `sold_dollars_ytd`,
  `rank_by_sold`,
  `created_at`
FROM icall_gold.report_designer_leaderboard
WHERE `entity` = '{ENT}';

-- report_lead_source_roi: weekly-report snapshot, lead-source group ROI (one row per source_group per report_date).
-- M: icall_gold.report_lead_source_roi (passthrough view over ic_weekly_report.report_lead_source_roi), entity filter.
CREATE OR REPLACE SQL SECURITY DEFINER VIEW `{SCHEMA}`.`report_lead_source_roi` AS
SELECT
  `id`,
  `entity`,
  `report_date`,
  `source_group`,
  `sold_dollars_mtd`,
  `opp_count_mtd`,
  `closing_rate_mtd`,
  `rank_by_sold`,
  `created_at`
FROM icall_gold.report_lead_source_roi
WHERE `entity` = '{ENT}';

-- report_designer_concentration: weekly-report snapshot, designer concentration (one row per report_date).
-- M: icall_gold.report_designer_concentration (passthrough view over ic_weekly_report.report_designer_concentration), entity filter.
CREATE OR REPLACE SQL SECURITY DEFINER VIEW `{SCHEMA}`.`report_designer_concentration` AS
SELECT
  `id`,
  `entity`,
  `report_date`,
  `top1_name`,
  `top1_dollars_ytd`,
  `top1_share`,
  `top2_name`,
  `top2_dollars_ytd`,
  `top2_combined_share`,
  `total_ytd`,
  `active_designers_ytd`,
  `warn_flag`,
  `created_at`
FROM icall_gold.report_designer_concentration
WHERE `entity` = '{ENT}';

-- report_channel_roi: weekly-report snapshot, channel ROI (one row per channel_name per report_date).
-- M: icall_gold.report_channel_roi (passthrough view over ic_weekly_report.report_channel_roi), entity filter.
CREATE OR REPLACE SQL SECURITY DEFINER VIEW `{SCHEMA}`.`report_channel_roi` AS
SELECT
  `id`,
  `entity`,
  `report_date`,
  `channel_name`,
  `new_leads_mtd`,
  `sold_opps_mtd`,
  `sold_dollars_mtd`,
  `decided_n`,
  `closing_rate`,
  `rank_by_sold`,
  `created_at`
FROM icall_gold.report_channel_roi
WHERE `entity` = '{ENT}';

-- report_appointment_funnel: weekly-report snapshot, appointment funnel (one row per activity_type per report_date).
-- M: icall_gold.report_appointment_funnel (passthrough view over ic_weekly_report.report_appointment_funnel), entity filter.
CREATE OR REPLACE SQL SECURITY DEFINER VIEW `{SCHEMA}`.`report_appointment_funnel` AS
SELECT
  `id`,
  `entity`,
  `report_date`,
  `activity_type`,
  `count_mtd`,
  `count_prior_mtd`,
  `mom_delta`,
  `share_of_mtd`,
  `tied_to_sold_pct`,
  `avg_cycle_days_mtd_sold`,
  `hygiene_flag`,
  `hygiene_pct`,
  `total_mtd_all`,
  `rank_by_count`,
  `created_at`
FROM icall_gold.report_appointment_funnel
WHERE `entity` = '{ENT}';
