/**
 * SQL bindings of the Power BI measures (FIRST DRAFT, phase 2 reviews them against the views and the gold set).
 *
 * Route A's prompt carries each non-trivial measure's DAX; Route B gets the same measure written as a SQL
 * expression over the mirrored view, in the same detail. Names and descriptions come from the vendored
 * semantic-knowledge.ts at runtime; only the SQL lives here. Measures with no entry are listed by name and
 * description only (as production does for the placeholder DAX bodies).
 *
 * Conventions:
 *   - `view`: the view the expression aggregates over (alias-free: write it inside SELECT ... FROM <view>).
 *   - DAX DIVIDE(a, b, 0) -> COALESCE(a / NULLIF(b, 0), 0); DIVIDE(a, b) -> a / NULLIF(b, 0) (NULL = blank).
 *   - {start} / {end}: the period window of DateTable-driven measures. With no period the window is the whole
 *     calendar table (Power BI's DateTable spans 2020-2030): {start} = (SELECT MIN(`Date`) FROM DateTable),
 *     {end} = (SELECT MAX(`Date`) FROM DateTable). Cohort measures use SoldDateTable the same way.
 */
export interface SqlMetric {
  view: string;
  sql: string;
  note?: string;
  /** Replaces the vendored description when that one is about DAX mechanics (filter context, DateTable). */
  desc?: string;
}

const sold = "`Revised Stage` = 'Sold'";
const sumIf = (cond: string) => `SUM(CASE WHEN ${cond} THEN \`Amount\` END)`;
const countIf = (cond: string) => `COUNT(CASE WHEN ${cond} THEN 1 END)`;
const div0 = (a: string, b: string) => `COALESCE(${a} / NULLIF(${b}, 0), 0)`;

export const SQL_METRICS: Record<string, SqlMetric> = {
  "Total Revenue": { view: "Salesforce_Data", sql: "SUM(`Amount`)" },
  "Revenue Sold": { view: "Salesforce_Data", sql: sumIf(sold) },
  "Revenue Proposed": { view: "Salesforce_Data", sql: sumIf("`Revised Stage` = 'Proposed'") },
  "Revenue Lost": { view: "Salesforce_Data", sql: sumIf("`Revised Stage` = 'Lost'"), note: "Dead Lead is NOT included." },
  "Count Total Projects": { view: "Salesforce_Data", sql: "COUNT(*)" },
  "Count Sold": { view: "Salesforce_Data", sql: countIf(sold) },
  "Count Proposed": { view: "Salesforce_Data", sql: countIf("`Revised Stage` = 'Proposed'") },
  "Count Lost": { view: "Salesforce_Data", sql: countIf("`Revised Stage` = 'Lost'"), note: "Dead Lead is NOT included; count Dead Lead inline." },
  "Sold Percentage": { view: "Salesforce_Data", sql: div0(sumIf(sold), "SUM(`Amount`)"), note: "A REVENUE share, not a closing rate." },
  "Proposed Percentage": { view: "Salesforce_Data", sql: div0(sumIf("`Revised Stage` = 'Proposed'"), "SUM(`Amount`)") },
  "Lost Percentage": { view: "Salesforce_Data", sql: div0(sumIf("`Revised Stage` = 'Lost'"), "SUM(`Amount`)") },
  "Average Project Value Sold": { view: "Salesforce_Data", sql: div0(sumIf(sold), countIf(sold)) },
  "Average Project Value Proposed": { view: "Salesforce_Data", sql: div0(sumIf("`Revised Stage` = 'Proposed'"), countIf("`Revised Stage` = 'Proposed'")) },
  "APV Sold by Lead Source": { view: "Salesforce_Data", sql: div0(sumIf(sold), countIf(sold)) },
  "APV Proposed by Lead Source": { view: "Salesforce_Data", sql: div0(sumIf("`Revised Stage` = 'Proposed'"), countIf("`Revised Stage` = 'Proposed'")) },
  "APV Lost by Lead Source": { view: "Salesforce_Data", sql: div0(sumIf("`Revised Stage` = 'Lost'"), countIf("`Revised Stage` = 'Lost'")) },
  "APV": { view: "Salesforce_Data", sql: div0(sumIf("`Amount` > 1000"), countIf("`Amount` > 1000")), note: "Excludes deals of $1,000 or less." },
  "Total Revenue (>1000)": { view: "Salesforce_Data", sql: sumIf("`Amount` > 1000") },
  "Total Records (>1000)": { view: "Salesforce_Data", sql: countIf("`Amount` > 1000") },
  "Av Cycle": { view: "Salesforce_Data", sql: "AVG(`Sale Cycle`)" },
  "Revenue Previous Year": {
    view: "Salesforce_Data",
    sql: "SUM(CASE WHEN `Created Date` BETWEEN DATE_SUB({start}, INTERVAL 1 YEAR) AND DATE_SUB({end}, INTERVAL 1 YEAR) THEN `Amount` END)",
    note: "Same window one year earlier, on Created Date. For \"last year's revenue\" filter YEAR(`Created Date`) = {PREV_YEAR} instead.",
  },
  "Revenue YoY Growth": { view: "Salesforce_Data", sql: "[Total Revenue] - [Revenue Previous Year] (compute both in one query)" },
  "Revenue YoY Growth %": { view: "Salesforce_Data", sql: "([Total Revenue] - [Revenue Previous Year]) / NULLIF([Revenue Previous Year], 0), 0 when blank" },
  "Revenue Previous Month": {
    view: "Salesforce_Data",
    sql: "SUM(CASE WHEN `Created Date` BETWEEN DATE_SUB({start}, INTERVAL 1 MONTH) AND LAST_DAY(DATE_SUB({start}, INTERVAL 1 MONTH)) THEN `Amount` END)",
  },
  "total records": { view: "Salesforce_Data", sql: "COUNT(*)" },
  "Closing Rate": {
    view: "Salesforce_Data",
    desc: "SF-community-matching closing / conversion %. = COUNT of opportunities SOLD (by Original Project Sold Date) in the window / COUNT CREATED (by Created Date) in the same window, capped at 100%. This is the figure the Salesforce Partner Community 'Closing % - Last 4 Months' report shows, and it backs the 'Closing % by designer' table on every franchise's Pipeline Report page. DIFFERENT from the count-based Sold/(Sold+Lost+Dead Lead) closing rate (which is created-date and not capped). Per designer: GROUP BY `Designer` with the three window counts.",
    sql: `LEAST(1, ${countIf(`${sold} AND \`Original Project Sold Date\` BETWEEN {start} AND {end}`)} / NULLIF(${countIf("`Created Date` BETWEEN {start} AND {end}")}, 0))`,
    note: "Window ONLY inside the expression; never put a date filter on Salesforce_Data in WHERE with this metric (WHERE may filter designer, lead source and other non-date columns). NULL when nothing was created in the window.",
  },
  "Projects Created (Period)": { view: "Salesforce_Data", desc: "COUNT of opportunities created (Created Date) in the window. Denominator of [Closing Rate].", sql: countIf("`Created Date` BETWEEN {start} AND {end}") },
  "Projects Sold (Period)": { view: "Salesforce_Data", desc: "COUNT of opportunities sold (Revised Stage = 'Sold') by Original Project Sold Date in the window. Numerator of [Closing Rate]. Differs from [Count Sold], which has no window of its own.", sql: countIf(`${sold} AND \`Original Project Sold Date\` BETWEEN {start} AND {end}`) },
  "Projects Created (Cohort)": {
    view: "Salesforce_Data",
    desc: "Opportunities CREATED in the sold-date window, Dead Lead left out. Denominator of [Closing Rate (Cohort)]; matches the Salesforce Community 'Designer Report | Closing & Amount'.",
    sql: countIf("`Revised Stage` <> 'Dead Lead' AND `Created Date` BETWEEN {start} AND {end}"),
    note: "Window from SoldDateTable (default: its MIN/MAX `Date`).",
  },
  "Projects Sold (Cohort)": {
    view: "Salesforce_Data",
    sql: countIf(`${sold} AND \`Original Project Sold Date\` BETWEEN {start} AND {end}`),
    note: "Window from SoldDateTable (default: its MIN/MAX `Date`).",
  },
  "Closing Rate (Cohort)": { view: "Salesforce_Data", desc: "Salesforce Community closing rate on the SOLD-date window: [Projects Sold (Cohort)] / [Projects Created (Cohort)]. Use for sold-date franchises when the user wants the Community 'Closing & Amount' figure.", sql: "[Projects Sold (Cohort)] / NULLIF([Projects Created (Cohort)], 0) (not capped)" },

  // Lead journey: `Lead Milestones` joined to Leads (1:1 on `Lead ID`); period on Leads.`Lead Created Date`.
  "JN Leads": { view: "Lead Milestones", sql: "COUNT(CASE WHEN m.`Exclude From Journey` = 0 THEN 1 END)", note: "FROM `Lead Milestones` m JOIN Leads l ON l.`Lead ID` = m.`Lead ID`; period on l.`Lead Created Date`." },
  "JN Booked": { view: "Lead Milestones", sql: "COUNT(CASE WHEN m.`Exclude From Journey` = 0 AND m.`Booked Appointment` = 1 THEN 1 END)" },
  "JN Booked %": { view: "Lead Milestones", sql: "[JN Booked] / NULLIF([JN Leads], 0)" },
  "JN Studio": { view: "Lead Milestones", sql: "COUNT(CASE WHEN m.`Exclude From Journey` = 0 AND m.`Reached Studio` = 1 THEN 1 END)" },
  "JN Studio %": { view: "Lead Milestones", sql: "[JN Studio] / NULLIF([JN Leads], 0)" },
  "JN Sold": { view: "Lead Milestones", sql: "COUNT(CASE WHEN m.`Exclude From Journey` = 0 AND m.`Journey Outcome` = 'Sold' THEN 1 END)" },
  "JN Win %": { view: "Lead Milestones", sql: "[JN Sold] / NULLIF([JN Leads], 0)" },
  "JN Sold Value": { view: "Lead Milestones", sql: "COALESCE(SUM(CASE WHEN m.`Exclude From Journey` = 0 THEN m.`Sold Amount` END), 0)" },
  "JN Sold per Lead": { view: "Lead Milestones", sql: "[JN Sold Value] / NULLIF([JN Leads], 0)" },
  "JN Days to Booked": { view: "Lead Milestones", sql: "MEDIAN of m.`Days to Scheduled` over rows with `Exclude From Journey` = 0 AND `Days to Scheduled` >= 0 (use the MEDIAN pattern)" },
  "JN Days to Studio": { view: "Lead Milestones", sql: "MEDIAN of m.`Days to Studio` over rows with `Exclude From Journey` = 0 AND `Days to Studio` >= 0 (MEDIAN pattern)" },
  "JN Days to Sold": { view: "Lead Milestones", sql: "MEDIAN of m.`Days to Sold` over rows with `Exclude From Journey` = 0 AND `Days to Sold` >= 0 AND `Journey Outcome` = 'Sold' (MEDIAN pattern)" },

  "Total Lead Count": { view: "Leads", sql: "COUNT(*)" },
  "Converted Lead Count": { view: "Leads", sql: "COUNT(CASE WHEN `Lead Status` = 'Moved to Studio' THEN 1 END)" },
  "Lost Lead Count": { view: "Leads", sql: "COUNT(CASE WHEN `Lead Status` IN ('Junk', 'Duplicate', 'Cancelled') THEN 1 END)" },
  "No Response Lead Count": { view: "Leads", sql: "COUNT(CASE WHEN `Lead Status` LIKE '%No Response%' THEN 1 END)" },
  "Leads in Nurturing Stage": { view: "Leads", sql: "COUNT(CASE WHEN `Lead Status` = 'Lead Nurturing' THEN 1 END)" },
  "Lead Conversion Rate": { view: "Leads", sql: div0("COUNT(CASE WHEN `Lead Status` = 'Moved to Studio' THEN 1 END)", "COUNT(*)") },
  "Lead Loss Rate": { view: "Leads", sql: div0("COUNT(CASE WHEN `Lead Status` IN ('Junk', 'Duplicate', 'Cancelled') THEN 1 END)", "COUNT(*)") },
  "Lead Response Rate": { view: "Leads", sql: "1 - COALESCE(COUNT(CASE WHEN `Lead Status` LIKE '%No Response%' THEN 1 END) / NULLIF(COUNT(*) - COUNT(CASE WHEN `Lead Status` = 'New Lead' THEN 1 END), 0), 0)" },
  "Junk & Duplicate Count": { view: "Leads", sql: "COUNT(CASE WHEN `Lead Status` IN ('Junk', 'Duplicate') THEN 1 END)" },
  "Junk & Duplicate %": { view: "Leads", sql: "COUNT(CASE WHEN `Lead Status` IN ('Junk', 'Duplicate') THEN 1 END) / NULLIF(COUNT(*), 0)" },
  "Brochure Count": { view: "Leads", sql: "COUNT(CASE WHEN `Lead Capture Form Type` = 'Brochure download' THEN 1 END)" },
  "Consultation Count": { view: "Leads", sql: "COUNT(CASE WHEN `Lead Capture Form Type` = 'Consultation request' THEN 1 END)" },
  "Manual Count": { view: "Leads", sql: "COUNT(CASE WHEN `Lead Capture Form Type` = 'Manual Entry' THEN 1 END)" },
  "Current Month Lead Count": { view: "Leads", sql: "COUNT(CASE WHEN `Lead Created Date` BETWEEN DATE_FORMAT(CURDATE(), '%Y-%m-01') AND CURDATE() THEN 1 END)" },
  "Days to Convert2": { view: "Leads", sql: "DATEDIFF(`Lead Conversion Date`, `Lead Created Date`) (one lead)" },

  "Total Activity Count": { view: "Appointments", sql: "COUNT(*)" },
  "Design Appointment Count": { view: "Appointments", sql: "COUNT(CASE WHEN `Activity Type` = 'Design' THEN 1 END)" },
  "Installation Appointment Count": { view: "Appointments", sql: "COUNT(CASE WHEN `Activity Type` = 'Installation' THEN 1 END)" },
  "Design to Installation Ratio": { view: "Appointments", sql: div0("COUNT(CASE WHEN `Activity Type` = 'Design' THEN 1 END)", "COUNT(CASE WHEN `Activity Type` = 'Installation' THEN 1 END)") },
  "Sold Project Count": { view: "Appointments", sql: "COUNT(CASE WHEN `Project Stage` = 'Sold' THEN 1 END)" },
  "Lost Project Count": { view: "Appointments", sql: "COUNT(CASE WHEN `Project Stage` = 'Lost' THEN 1 END)" },
  "Proposed Project Count": { view: "Appointments", sql: "COUNT(CASE WHEN `Project Stage` = 'Propose' THEN 1 END)" },
  "Project Success Rate": { view: "Appointments", sql: div0("COUNT(CASE WHEN `Project Stage` = 'Sold' THEN 1 END)", "COUNT(*)") },
  "Future Appointment Count": { view: "Appointments", sql: "COUNT(CASE WHEN `Appointment Start Time` > CURDATE() THEN 1 END)" },
  "Average Activities per Staff Member": { view: "Appointments", sql: div0("COUNT(*)", "COUNT(DISTINCT `Assigned Staff Member`)") },
  "Current Month Activity Count": { view: "Appointments", sql: "COUNT(CASE WHEN YEAR(`Activity Date and Time`) = {YEAR} AND MONTH(`Activity Date and Time`) = {MONTH} THEN 1 END)" },
  "Opportunities to Appointments %": {
    view: "Opportunities to Appointments",
    sql: "(SELECT COUNT(*) FROM `Opportunities to Appointments` WHERE `Project Stage` IN ('Design', 'Incomplete', 'Propose', 'Sold') AND <period on `Activity Date and Time`>) / NULLIF((SELECT COUNT(*) FROM Leads WHERE <period on `Lead Created Date`>), 0)",
  },
  "Opportunities to Sold %": {
    view: "Opportunities to Appointments",
    sql: "(SELECT COUNT(*) FROM `Opportunities to Appointments` WHERE `Project Stage` = 'Sold' AND <period on `Activity Date and Time`>) / NULLIF((SELECT COUNT(*) FROM Leads WHERE <period on `Lead Created Date`>), 0)",
  },
  "Opportunities to Propose %": {
    view: "Opportunities to Appointments",
    sql: "(SELECT COUNT(*) FROM `Opportunities to Appointments` WHERE `Project Stage` = 'Propose' AND <period on `Activity Date and Time`>) / NULLIF((SELECT COUNT(*) FROM Leads WHERE <period on `Lead Created Date`>), 0)",
  },
  "Opportunities to Lost %": {
    view: "Opportunities to Appointments",
    sql: "(SELECT COUNT(*) FROM `Opportunities to Appointments` WHERE `Project Stage` = 'Lost' AND <period on `Activity Date and Time`>) / NULLIF((SELECT COUNT(*) FROM Leads WHERE <period on `Lead Created Date`>), 0)",
  },
  "Opportunities to Sold Count": { view: "Opportunities to Appointments", sql: "COUNT(CASE WHEN `Project Stage` = 'Sold' THEN 1 END) (APPOINTMENT GRAIN: never a count of sold projects)" },
};

/** The MEDIAN pattern (MySQL has no MEDIAN; DAX MEDIANX averages the two middle values for an even count). */
export const SQL_MEDIAN_PATTERN = [
  "MEDIAN pattern (MySQL has no MEDIAN function; this matches DAX MEDIANX, which averages the two middle values):",
  "  WITH x AS (SELECT <value> AS v, ROW_NUMBER() OVER (ORDER BY <value>) AS rn, COUNT(*) OVER () AS c FROM ... WHERE <filters>)",
  "  SELECT AVG(v) AS `Median` FROM x WHERE rn IN (FLOOR((c + 1) / 2), CEIL((c + 1) / 2))",
  "  Per group: add PARTITION BY <group> to both window functions and GROUP BY <group> in the outer SELECT.",
].join("\n");
