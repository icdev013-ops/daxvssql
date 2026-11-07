-- 20_salesforce_data.only_ICAU.sql: the ICAU model keeps one extra Salesforce_Data column,
-- `Additional Lead Source` (Gold column `Other Lead Source`, the free-text detail shown when Lead Source = Other).
-- Applied to ICAU only, after 10_mirror.sql: it REPLACES the shared Salesforce_Data view with the same
-- definition plus that column in the ICAU model position (after Lead Source). Keep the two in step.

-- Salesforce_Data: one row per opportunity (project).
-- M: icall_gold.fact_opportunities (passthrough view over fact_sales), entity filter, Table.SelectColumns
-- keeps 25 Gold columns (the 24 shared ones plus `Other Lead Source`, exposed as `Additional Lead Source`). The 4 Sold* columns are the DAX calculated columns.
CREATE OR REPLACE SQL SECURITY DEFINER VIEW `{SCHEMA}`.`Salesforce_Data` AS
SELECT
  `Project Name`,
  `Stage`,
  `Original Project Sold Date`,
  `Amount`,
  `Created Date`,
  `Last Modified Date`,
  `Lead Source`,
  `Other Lead Source` AS `Additional Lead Source`,
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
