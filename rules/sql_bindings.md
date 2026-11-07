# SQL bindings

How each section of the business rules maps to the franchise's MySQL views (MySQL 8.0), in the same section order. The views mirror the Power BI tables one for one (same names, columns and rows) but have no measures: every metric the rules use is written out below as a SQL expression.

Placeholders used below:

| Placeholder | Meaning |
|---|---|
| `{A}` | the anchor date from the context line, written as `DATE 'yyyy-mm-dd'` |
| `{Y}`, `{M}` | the anchor's year and month number |
| `{SALES_DATE}` | the franchise's sales-date column of `Salesforce_Data` (section 4): `` `Created Date` `` for ICLA, `` `Original Project Sold Date` `` for ICCH and ICAU |
| `{ICLA_EXCLUDED_STAFF_SQL}` | ICLA's excluded office-staff list as `'…', '…'`, supplied with the prompt |
| `<designer>`, `<source>` | a name taken from the user's question |

If a placeholder still shows braces in your prompt, substitute it yourself from the context line and the tables here.

## 0. View map

The connection sees one franchise's schema only, and every row in it belongs to that franchise: never filter on `entity`.

| View | Grain / use |
|---|---|
| `Salesforce_Data` | one row per opportunity (project): `` `Project ID` `` (unique), `` `Project Name` `` (not unique), `Stage`, `` `Revised Stage` ``, `Amount`, `` `Created Date` ``, `` `Original Project Sold Date` `` (both DATETIME at midnight), `Designer`, `` `Lead Source` ``, `` `Lead Source Group` ``, `` `Project Notes` ``, `` `Consumer Name: Full Name` ``, `` `Sale Cycle` ``, `` `Last Modified Date` ``; `` `Sold Year` ``, `` `Sold Month` ``, `` `Sold Month Number` ``, `` `Sold Start of Week` ``; `` `Start of Week` `` (created). ICAU only: `` `Additional Lead Source` `` |
| `Leads` | one row per lead: `` `Lead ID` ``, `` `Lead Created Date` `` (DATE), `` `Lead Status` ``, `` `Lead Source Channel` ``, `` `Converted Project Name` ``, `` `Lead Capture Form Type` ``, contact fields, `` `Project Notes` ``, `` `Initial Comments` ``, `` `Lead Description` `` |
| `Appointments` | one row per calendar entry or task: `` `Appointment Start Time` `` and `` `Activity Date and Time` `` (DATE), `` `Activity Type` ``, `` `Appointment Subject` ``, `` `Assigned Staff Member` ``, `` `Project Name` ``, `` `Project Stage` ``, `` `Lead Source Channel` ``, `` `Is First Appointment` ``, `designer_sk` |
| `Leads_Journey` | one row per lead: `` `Current Status` ``, `` `Lead Journey` `` (status path text), `` `Total Status Changes` `` |
| `` `Lead Milestones` `` | one row per lead: `` `Contact Path` ``, `` `Contact Attempts` ``, `` `Booked Appointment` ``, `` `Reached Studio` ``, `` `Journey Outcome` ``, `` `Sold Amount` ``, `` `Days to Scheduled` ``, `` `Days to Studio` ``, `` `Days to Sold` ``, `` `Exclude From Journey` `` |
| `` `Opportunities to Appointments` `` | one row per converted lead with its first appointment (funnel bridge) |
| `dim_designers` | **active** designers only (`Designer`, `` `Total Projects` ``, `Sort`) |
| `dim_projects` | distinct project names |
| `DateTable`, `SoldDateTable` | calendars 2020-01-01..2030-12-31: `Date`, `Year`, `Month`, `MonthNumber`, `Quarter`, `` `Year-Month` ``, `` `Start Of Week` `` (Sunday) |
| `report_scorecard`, `report_designer_leaderboard`, `report_lead_source_roi`, `report_channel_roi`, `report_designer_concentration`, `report_appointment_funnel` | weekly-report snapshots (section 9) |
| `` `Projects Part List` `` | ICLA only: parts per project |

Join keys (MySQL has no relationships; write every join yourself):

| From | To | Note |
|---|---|---|
| `Salesforce_Data`.`` `Created Date` `` | `DateTable`.`Date` | the created date |
| `Salesforce_Data`.`` `Original Project Sold Date` `` | `SoldDateTable`.`Date` | the sold date |
| `Leads`.`` `Lead Created Date` ``, `Appointments`.`` `Appointment Start Time` `` | `DateTable`.`Date` | `` `Activity Date and Time` `` is the activity date, not the period date |
| `Salesforce_Data`.`` `Project Name` ``, `Leads`.`` `Converted Project Name` ``, `Appointments`.`` `Project Name` `` | `dim_projects`.`` `Project Name` `` | **name matches; `Project Name` repeats in `Salesforce_Data` (60 / 60 / 52 rows in ICLA / ICCH / ICAU) and across appointments** |
| `` `Lead Milestones` ``.`` `Lead ID` `` | `Leads`.`` `Lead ID` `` | 1:1 |
| `Leads`.`` `Lead ID` `` | `Leads_Journey`.`` `Lead ID` `` | 1:1 (a few leads have no journey row) |
| `Salesforce_Data`.`Designer` | `dim_designers`.`Designer` | inner join = active roster; LEFT JOIN keeps the others under NULL |
| `Appointments`.`designer_sk` | `dim_designers`.`designer_sk` | |
| `report_*` | none | |

## 1. Answering-rule mechanics

- Send exactly one statement: a `SELECT`, or `WITH … SELECT`. No other statement types, no `SET`, no user variables, no optimizer hints, no `INTO`, no locking clauses.
- Rule 4 has no measures to lean on, so use these exact expressions (over `Salesforce_Data AS s` or `Leads`):
  - sold count `` SUM(s.`Revised Stage` = 'Sold') ``, sold revenue `` SUM(CASE WHEN s.`Revised Stage` = 'Sold' THEN s.Amount END) ``, all projects `COUNT(*)`, leads `COUNT(*)` over `Leads`.
  - Don't add `IS NOT NULL` date conditions the user didn't ask for.
- Lists return rows with readable aliases (backticks for aliases with spaces) and an `ORDER BY`:

```sql
SELECT s.`Project Name` AS `Project`, s.Designer, s.Amount, s.`Revised Stage` AS `Stage`,
       s.`Created Date` AS `Created`, s.`Project Notes` AS `Notes`
FROM Salesforce_Data AS s
WHERE s.`Revised Stage` = 'Lost' AND YEAR(s.`Created Date`) = {Y}
ORDER BY s.Amount DESC
```

- The executor returns at most 50 rows and flags truncation. `LIMIT N` cuts ties arbitrarily; for top-N use `RANK() OVER (ORDER BY … DESC)` and keep `rnk <= N` (section 6 shows the shape).
- A sum over no rows is NULL and the result is reported as BLANK: answer 0. `COALESCE(SUM(…), 0)` makes it explicit.
- The server prints exact column totals under every multi-row result; quote them instead of adding.
- Loose names: `s.Designer LIKE '%<designer>%'`. Comparisons, `=`, `IN` and `LIKE` are case-insensitive on these views. In `LIKE`, `_` and `%` are wildcards: match exact project names with `=`.
- Columns that do not exist: `Leads`.`Lead Owner/Designer` (use `` `Lead Creator Name` ``), `Leads`.`Created Month` (use `` `Lead Creation Year-Month` ``), `Salesforce_Data`.`Lead Owner` (use `Designer`).
- The tool answers with `Error: …` (the server's message), `No rows returned.` (filter matched nothing, often a spelling) or a BLANK result (aggregate over no rows).

## 2. Dates and periods

- Write the anchor as a literal, `{A}` = `DATE 'yyyy-mm-dd'`. Don't use `CURDATE()` or `NOW()`: the sandbox is a frozen copy and the server clock is not the franchise's date.

| Business date | Column | Type |
|---|---|---|
| opportunity created | `Salesforce_Data`.`` `Created Date` `` | DATETIME (midnight) |
| sold | `Salesforce_Data`.`` `Original Project Sold Date` `` | DATETIME (midnight) |
| lead created | `Leads`.`` `Lead Created Date` `` | DATE |
| appointment period | `Appointments`.`` `Appointment Start Time` `` | DATE |
| appointment activity (last activity only) | `Appointments`.`` `Activity Date and Time` `` | DATE |

- Window predicates on the column (they work on DATE and DATETIME alike):
  - calendar year: `YEAR(col) = {Y}`; calendar month: `YEAR(col) = {Y} AND MONTH(col) = {M}`; quarter: `YEAR(col) = {Y} AND QUARTER(col) = q`;
  - year to date: `col >= MAKEDATE({Y}, 1) AND col < {A} + INTERVAL 1 DAY`;
  - last month: `col >= DATE_FORMAT({A} - INTERVAL 1 MONTH, '%Y-%m-01') AND col < DATE_FORMAT({A}, '%Y-%m-01')`;
  - last N days: `col >= {A} - INTERVAL (N - 1) DAY AND col < {A} + INTERVAL 1 DAY`;
  - same period last year: `col >= MAKEDATE({Y} - 1, 1) AND col < {A} - INTERVAL 1 YEAR + INTERVAL 1 DAY`.
- Always use a half-open range (`>= start AND < end + 1 day`), never `<= 'yyyy-mm-dd'`, on DATETIME columns.
- Weeks start on Sunday: `{A} - INTERVAL (DAYOFWEEK({A}) - 1) DAY` is the Sunday of the anchor's week (`DAYOFWEEK`: Sunday = 1). Group weeks by `` `Start of Week` `` (created) or `` `Sold Start of Week` `` (sold) on `Salesforce_Data`, or `DateTable`.`` `Start Of Week` ``. `Appointments`.`` `Start Of WeekA` `` follows the activity date, not the start date: don't use it for appointment weeks. `DateTable`.`WeekdayNumber` counts Monday = 1.
- Calendar bound: the model's calendars cover 2020-01-01..2030-12-31 only. A year or month breakdown over all time must stay inside it, `col >= DATE '2020-01-01' AND col < DATE '2031-01-01'` (or join `DateTable`), and the answer should say older records exist (opportunities created before 2020: ICLA 1,535, ICCH 1,175, ICAU 1,025 on the 2026-11-07 data). Plain all-time counts and sums have no date condition.

```sql
SELECT YEAR(s.`Created Date`) AS `Year`, COUNT(*) AS `Projects`, SUM(s.Amount) AS `Amount`
FROM Salesforce_Data AS s
WHERE s.`Created Date` >= DATE '2020-01-01' AND s.`Created Date` < DATE '2031-01-01'
GROUP BY YEAR(s.`Created Date`)
ORDER BY `Year`
```

- There are no time-intelligence functions: build every comparison window with `INTERVAL`, `MAKEDATE`, `DATE_FORMAT` and `LAST_DAY`.

## 3. Projects, stages and money

Over `Salesforce_Data AS s`:

| Business term | SQL |
|---|---|
| all-stage amount ("revenue", "bid amount") | `SUM(s.Amount)` |
| sold revenue | `` SUM(CASE WHEN s.`Revised Stage` = 'Sold' THEN s.Amount END) `` |
| open pipeline / lost amount | `` SUM(CASE WHEN s.`Revised Stage` = 'Proposed' THEN s.Amount END) `` / `'Lost'` |
| projects | `COUNT(*)` |
| sold / proposed / lost / dead-lead count | `` SUM(s.`Revised Stage` = 'Sold') `` / `'Proposed'` / `'Lost'` / `'Dead Lead'` |
| lost or dead | `` s.`Revised Stage` IN ('Lost', 'Dead Lead') `` |
| Sold % (revenue share) | `` SUM(CASE WHEN s.`Revised Stage` = 'Sold' THEN s.Amount END) / NULLIF(SUM(s.Amount), 0) ``; Proposed % and Lost % likewise |
| average sold value | `` SUM(CASE WHEN s.`Revised Stage` = 'Sold' THEN s.Amount END) / NULLIF(SUM(s.`Revised Stage` = 'Sold'), 0) `` |
| average amount per opportunity | `AVG(s.Amount)` |
| "Avg project value" card, deals above $1,000 | `AVG(CASE WHEN s.Amount > 1000 THEN s.Amount END)` |
| sale cycle | `` AVG(s.`Sale Cycle`) `` (NULLs are skipped, as in the model) |
| distinct lead sources / designers | `` COUNT(DISTINCT s.`Lead Source`) `` / `COUNT(DISTINCT s.Designer)` |

- Stage values: `` `Revised Stage` `` ∈ ('Sold', 'Proposed', 'Lost', 'Dead Lead'); raw `Stage` ∈ ('Complete', 'Ready to Install', 'Ready to Order', 'Incomplete', 'Propose', 'Design', 'Lost', 'Dead Lead'). `` `Lead Source Group` `` ∈ ('HOUSE', 'SG,R,R', 'STOW').
- Stage funnel (created basis for a period):

```sql
SELECT s.`Revised Stage` AS `Stage`, COUNT(*) AS `Projects`, SUM(s.Amount) AS `Amount`
FROM Salesforce_Data AS s
WHERE YEAR(s.`Created Date`) = {Y}
GROUP BY s.`Revised Stage`
ORDER BY `Projects` DESC
```

- Top N projects are rows (ties kept):

```sql
SELECT `Project`, Designer, Amount, `Sold date`
FROM (
  SELECT s.`Project Name` AS `Project`, s.Designer, s.Amount, s.`Original Project Sold Date` AS `Sold date`,
         RANK() OVER (ORDER BY s.Amount DESC) AS rnk
  FROM Salesforce_Data AS s
  WHERE s.`Revised Stage` = 'Sold'
) AS t
WHERE rnk <= 10
ORDER BY Amount DESC
```

- Self-gen by stage (add a designer or year condition to the `WHERE` as needed):

```sql
SELECT s.`Revised Stage` AS `Stage`, SUM(s.Amount) AS `Bid amount`, COUNT(*) AS `Projects`
FROM Salesforce_Data AS s
WHERE s.`Lead Source` LIKE '%self%'
GROUP BY s.`Revised Stage`
ORDER BY `Bid amount` DESC
```

- Share of a total with a window function:

```sql
SELECT s.Designer, SUM(s.Amount) AS `Sold`,
       SUM(s.Amount) / SUM(SUM(s.Amount)) OVER () AS `Share`
FROM Salesforce_Data AS s
WHERE s.`Revised Stage` = 'Sold' AND YEAR(s.{SALES_DATE}) = {Y}
GROUP BY s.Designer
ORDER BY `Sold` DESC
```

- Volume guard on rate rankings: `HAVING COUNT(*) >= 30` (projects), `>= 50` (leads), `` SUM(s.`Revised Stage` = 'Sold') >= 5 `` (sold).
- Project to lead: `Leads`.`` `Converted Project Name` `` = '<project>'; `` `Lead ID` `` is an opaque Salesforce ID, never a project name.

## 4. Sales date basis

| Franchise | `{SALES_DATE}` | Group by month / week |
|---|---|---|
| ICLA | `` `Created Date` `` | `` YEAR(s.`Created Date`) ``, `` MONTH(s.`Created Date`) ``; `` `Start of Week` `` |
| ICCH, ICAU | `` `Original Project Sold Date` `` | `` `Sold Year` ``, `` `Sold Month Number` ``, `` `Sold Month` ``; `` `Sold Start of Week` `` |

- Every sold figure needs both conditions: `` `Revised Stage` `` = 'Sold' and the window on `{SALES_DATE}`. The sold-date column alone is not enough: 45 / 33 / 37 non-Sold rows (ICLA / ICCH / ICAU) still carry a sold date.
- Sales this year on the franchise basis:

```sql
SELECT SUM(s.Amount) AS `Sold revenue`, COUNT(*) AS `Sold projects`, AVG(s.Amount) AS `Average sold value`
FROM Salesforce_Data AS s
WHERE s.`Revised Stage` = 'Sold' AND YEAR(s.{SALES_DATE}) = {Y}
```

- Rule 6, a person's sales with no period (this year and all time in one query):

```sql
SELECT
  SUM(CASE WHEN YEAR(s.{SALES_DATE}) = {Y} THEN s.Amount END) AS `Sold revenue this year`,
  SUM(YEAR(s.{SALES_DATE}) = {Y}) AS `Sold projects this year`,
  SUM(s.Amount) AS `Sold revenue all time`,
  COUNT(*) AS `Sold projects all time`
FROM Salesforce_Data AS s
WHERE s.`Revised Stage` = 'Sold' AND s.Designer LIKE '%<designer>%'
```

  For a lead source, swap the designer condition for `` s.`Lead Source` LIKE '%<source>%' ``.
- Monthly sales trend on the sold basis (ICCH, ICAU); for ICLA group by `` YEAR(s.`Created Date`) `` and `` MONTH(s.`Created Date`) `` in the same shape:

```sql
SELECT s.`Sold Year` AS `Year`, s.`Sold Month Number` AS `Month number`, s.`Sold Month` AS `Month`,
       SUM(s.Amount) AS `Sold revenue`, COUNT(*) AS `Sold projects`
FROM Salesforce_Data AS s
WHERE s.`Revised Stage` = 'Sold' AND s.`Sold Year` = {Y}
GROUP BY s.`Sold Year`, s.`Sold Month Number`, s.`Sold Month`
ORDER BY `Month number`
```

- The other basis (follow-up "by created date" / "by sold date"): swap `{SALES_DATE}` for the other column.
- Lost, Dead Lead, Proposed, all-stage amount, project counts and stage mixes filter `` `Created Date` ``.
- Sold rows with no sold date fail any window on `` `Original Project Sold Date` `` (NULL) and stay in all-time totals. Sold dates exist only from mid-2023, so for a sold-basis window before 2024 return both bases in one row (one conditional sum on `` `Original Project Sold Date` ``, one on `` `Created Date` ``).
- Sale cycle for a period: `` AVG(s.`Sale Cycle`) `` with `` YEAR(s.{SALES_DATE}) = {Y} `` in the `WHERE`.

## 5. Closing rate

**C1 (default)**, the model's `[Closing Rate]` with its parts `[Projects Created (Period)]` and `[Projects Sold (Period)]`. Two independent counts over the same window `[d0, d1]`, sold by sold date and created by created date, capped at 1. The window stays inside the calendar: `d0 >= DATE '2020-01-01'`, `d1 <= DATE '2030-12-31'`; "all time" is exactly that range.

```sql
WITH w AS (SELECT MAKEDATE({Y}, 1) AS d0, {A} AS d1)
SELECT
  LEAST(1, SUM(s.`Revised Stage` = 'Sold' AND s.`Original Project Sold Date` >= w.d0
                AND s.`Original Project Sold Date` < w.d1 + INTERVAL 1 DAY)
           / NULLIF(SUM(s.`Created Date` >= w.d0 AND s.`Created Date` < w.d1 + INTERVAL 1 DAY), 0)) AS `Closing rate`,
  SUM(s.`Created Date` >= w.d0 AND s.`Created Date` < w.d1 + INTERVAL 1 DAY) AS `Projects created`,
  SUM(s.`Revised Stage` = 'Sold' AND s.`Original Project Sold Date` >= w.d0
      AND s.`Original Project Sold Date` < w.d1 + INTERVAL 1 DAY) AS `Projects sold`
FROM Salesforce_Data AS s CROSS JOIN w
```

- The two counts must not share a `WHERE`: a `WHERE` on `` `Created Date` `` drops sales of projects created before the window, which the numerator needs.
- Last 4 months: `d0 = {A} - INTERVAL 4 MONTH`, `d1 = {A}`. A calendar month: its first and last day. All time: `DATE '2020-01-01'` to `DATE '2030-12-31'`.
- Per designer on the active roster, volume-guarded:

```sql
WITH w AS (SELECT MAKEDATE({Y}, 1) AS d0, {A} AS d1),
per AS (
  SELECT d.Designer,
         SUM(s.`Created Date` >= w.d0 AND s.`Created Date` < w.d1 + INTERVAL 1 DAY) AS created_n,
         SUM(s.`Revised Stage` = 'Sold' AND s.`Original Project Sold Date` >= w.d0
             AND s.`Original Project Sold Date` < w.d1 + INTERVAL 1 DAY) AS sold_n
  FROM Salesforce_Data AS s
  JOIN dim_designers AS d ON d.Designer = s.Designer
  CROSS JOIN w
  GROUP BY d.Designer
)
SELECT Designer, created_n AS `Projects created`, sold_n AS `Projects sold`,
       LEAST(1, sold_n / NULLIF(created_n, 0)) AS `Closing rate`
FROM per
WHERE created_n >= 30
ORDER BY `Closing rate` DESC
```

  By lead source: group by `` s.`Lead Source` `` without the `dim_designers` join.

**C2**, the model's `[Closing Rate (Cohort)]`: as C1 without the cap, Dead Lead left out of the denominator.

```sql
WITH w AS (SELECT MAKEDATE({Y}, 1) AS d0, {A} AS d1)
SELECT
  SUM(s.`Revised Stage` = 'Sold' AND s.`Original Project Sold Date` >= w.d0
      AND s.`Original Project Sold Date` < w.d1 + INTERVAL 1 DAY)
  / NULLIF(SUM(s.`Revised Stage` <> 'Dead Lead' AND s.`Created Date` >= w.d0
               AND s.`Created Date` < w.d1 + INTERVAL 1 DAY), 0) AS `Cohort closing rate`,
  SUM(s.`Revised Stage` <> 'Dead Lead' AND s.`Created Date` >= w.d0
      AND s.`Created Date` < w.d1 + INTERVAL 1 DAY) AS `Projects created excl. Dead Lead`,
  SUM(s.`Revised Stage` = 'Sold' AND s.`Original Project Sold Date` >= w.d0
      AND s.`Original Project Sold Date` < w.d1 + INTERVAL 1 DAY) AS `Projects sold`
FROM Salesforce_Data AS s CROSS JOIN w
```

**C3**, the KPI Overview tile (no model measure): the 120 days ending on the anchor date.

```sql
WITH w AS (SELECT {A} - INTERVAL 119 DAY AS d0, {A} + INTERVAL 1 DAY AS d1)
SELECT
  SUM(s.Stage IN ('Ready to Install', 'Ready to Order', 'Complete', 'Incomplete')
      AND s.`Original Project Sold Date` >= w.d0 AND s.`Original Project Sold Date` < w.d1)
  / NULLIF(SUM(s.Stage <> 'Dead Lead' AND s.`Created Date` >= w.d0 AND s.`Created Date` < w.d1), 0)
    AS `120-day closing rate`,
  SUM(s.Stage IN ('Ready to Install', 'Ready to Order', 'Complete', 'Incomplete')
      AND s.`Original Project Sold Date` >= w.d0 AND s.`Original Project Sold Date` < w.d1) AS `Sold`,
  SUM(s.Stage <> 'Dead Lead' AND s.`Created Date` >= w.d0 AND s.`Created Date` < w.d1) AS `Created excl. Dead Lead`
FROM Salesforce_Data AS s CROSS JOIN w
```

**C4**. This week / this month: the weekly report (section 9).

```sql
SELECT r.window_name AS `Window`, r.value AS `Closing rate (count-based)`, r.report_date AS `As of`
FROM report_scorecard AS r
WHERE r.report_date = (SELECT MAX(report_date) FROM report_scorecard)
  AND r.metric_key = 'closing_rate'
  AND r.window_name IN ('current_week', 'current_mtd')
```

Any other window, inline on created date:

```sql
SELECT SUM(s.`Revised Stage` = 'Sold') / NULLIF(SUM(s.`Revised Stage` IN ('Sold', 'Lost', 'Dead Lead')), 0)
         AS `Closing rate (count-based)`,
       SUM(s.`Revised Stage` = 'Sold') AS `Sold`,
       SUM(s.`Revised Stage` IN ('Sold', 'Lost', 'Dead Lead')) AS `Decided`
FROM Salesforce_Data AS s
WHERE s.`Created Date` >= MAKEDATE({Y}, 1) AND s.`Created Date` < {A} + INTERVAL 1 DAY
```

- `NULLIF(…, 0)` makes a zero denominator NULL: report "no projects created in the window", not 0%.
- Not closing rates: Sold % (revenue share), lead conversion rate, appointment success rate.

## 6. Designers

- Sales rankings group by `Salesforce_Data`.`Designer` (every name, active or not) and leave unassigned rows out:

```sql
SELECT Designer, `Sold revenue`, `Sold projects`
FROM (
  SELECT s.Designer, SUM(s.Amount) AS `Sold revenue`, COUNT(*) AS `Sold projects`,
         RANK() OVER (ORDER BY SUM(s.Amount) DESC) AS rnk
  FROM Salesforce_Data AS s
  WHERE s.`Revised Stage` = 'Sold' AND YEAR(s.{SALES_DATE}) = {Y}
    AND s.Designer IS NOT NULL AND s.Designer <> ''
  GROUP BY s.Designer
) AS t
WHERE rnk <= 5
ORDER BY `Sold revenue` DESC
```

- Active roster = `JOIN dim_designers AS d ON d.Designer = s.Designer` (the view holds active designers only; `active_flag` is always 1). The model shows the other designers' rows under a blank designer; a `LEFT JOIN dim_designers AS d` with `d.Designer IS NULL` finds them when you need that "inactive or unassigned" figure.
- "List the active designers":

```sql
SELECT d.Designer
FROM dim_designers AS d
WHERE d.`Total Projects` > 0
ORDER BY d.Designer
```

- Designers active in a period:

```sql
SELECT COUNT(DISTINCT s.Designer) AS `Active designers`
FROM Salesforce_Data AS s
WHERE YEAR(s.`Created Date`) = {Y} AND s.Designer <> ''
```

- Top performing (no metric): the ranking above with no date condition and `rnk <= 1`.
- Staff: `Appointments`.`` `Assigned Staff Member` ``, e.g. `` COUNT(DISTINCT a.`Assigned Staff Member`) `` with `` a.`Project Stage` = 'Sold' ``. The Appointments page's designer slicer reaches appointments through `Appointments`.`designer_sk` = `dim_designers`.`designer_sk`.

## 7. Leads and lead journey

Over `Leads AS l`:

| Business term | SQL |
|---|---|
| leads | `COUNT(*)` |
| converted leads | `` SUM(l.`Lead Status` = 'Moved to Studio') `` |
| lead conversion rate | `` SUM(l.`Lead Status` = 'Moved to Studio') / NULLIF(COUNT(*), 0) `` |
| lost leads | `` SUM(l.`Lead Status` IN ('Junk', 'Duplicate', 'Cancelled')) `` |
| junk only / cancelled only | `` SUM(l.`Lead Status` = 'Junk') `` / `= 'Cancelled'` |
| junk and duplicate | `` SUM(l.`Lead Status` IN ('Junk', 'Duplicate')) ``, and ÷ `COUNT(*)` for the % |
| no response | `` SUM(l.`Lead Status` LIKE '%No Response%') `` |
| in nurturing now | `` SUM(l.`Lead Status` = 'Lead Nurturing') `` |
| ever in a status | `` SELECT COUNT(*) FROM Leads_Journey AS j WHERE j.`Lead Journey` LIKE '%Nurturing%' `` |
| form type | `` SUM(l.`Lead Capture Form Type` = 'Brochure download') ``, `'Consultation request'`, `'Manual Entry'` |
| channel | `` l.`Lead Source Channel` ``; referral: `IN ('Referral', 'Referral - Company')` |

- Period: `` `Lead Created Date` ``.
- Journey metrics: `` `Lead Milestones` `` joined to `Leads` on `` `Lead ID` `` for the period and channel; keep only `` m.`Exclude From Journey` = 0 `` (the model's JN measures do).

```sql
SELECT COUNT(*) AS `Leads`,
       SUM(m.`Booked Appointment` = 1) AS `Booked`,
       SUM(m.`Booked Appointment` = 1) / NULLIF(COUNT(*), 0) AS `Booked %`,
       SUM(m.`Reached Studio` = 1) AS `Reached studio`,
       SUM(m.`Reached Studio` = 1) / NULLIF(COUNT(*), 0) AS `Studio %`,
       SUM(m.`Journey Outcome` = 'Sold') AS `Sold`,
       SUM(m.`Journey Outcome` = 'Sold') / NULLIF(COUNT(*), 0) AS `Win %`,
       COALESCE(SUM(m.`Sold Amount`), 0) AS `Sold value`,
       COALESCE(SUM(m.`Sold Amount`), 0) / NULLIF(COUNT(*), 0) AS `Sold value per lead`
FROM `Lead Milestones` AS m
JOIN Leads AS l ON l.`Lead ID` = m.`Lead ID`
WHERE m.`Exclude From Journey` = 0 AND YEAR(l.`Lead Created Date`) = {Y}
```

  By channel: add `` l.`Lead Source Channel` `` to the select list and `GROUP BY` it. First contact: group by `` m.`Contact Path` ``; attempts: `` m.`Contact Attempts` ``.
- Typical days are medians, and MySQL has no `MEDIAN`. The model's median (`MEDIANX`) of an even count is the mean of the two middle values; reproduce it with window functions. Days to book uses `` `Days to Scheduled` ``, days to studio `` `Days to Studio` `` (both `>= 0`); days to sale uses `` `Days to Sold` `` `>= 0` and `` `Journey Outcome` `` = 'Sold':

```sql
WITH x AS (
  SELECT m.`Days to Sold` AS v
  FROM `Lead Milestones` AS m
  JOIN Leads AS l ON l.`Lead ID` = m.`Lead ID`
  WHERE m.`Exclude From Journey` = 0 AND m.`Days to Sold` >= 0 AND m.`Journey Outcome` = 'Sold'
    AND YEAR(l.`Lead Created Date`) = {Y}
),
r AS (SELECT v, ROW_NUMBER() OVER (ORDER BY v) AS rn, COUNT(*) OVER () AS n FROM x)
SELECT AVG(v) AS `Typical days to sale`
FROM r
WHERE rn IN (FLOOR((n + 1) / 2), CEIL((n + 1) / 2))
```

  Per channel: add `` l.`Lead Source Channel` AS ch `` in `x`, `PARTITION BY ch` in both window functions, and `GROUP BY ch` in the final select.

## 8. Appointments

Over `Appointments AS a`:

| Business term | SQL |
|---|---|
| appointments | `COUNT(*)` |
| appointments on sold / lost / proposed projects | `` SUM(a.`Project Stage` = 'Sold') `` / `'Lost'` / `'Proposed'` |
| success rate | `` SUM(a.`Project Stage` = 'Sold') / NULLIF(COUNT(*), 0) `` |
| design / installation appointments | `` SUM(a.`Activity Type` = 'Design') `` / `'Installation'` |
| first appointments (flag) | `` SUM(a.`Is First Appointment` = 1) `` |

Franchise rule, added to the `WHERE` of every appointment query:

| Franchise | `WHERE` conditions |
|---|---|
| ICLA | `` COALESCE(a.`Activity Type`, '') NOT IN ('Installation', 'Unspecified') AND a.`Appointment Subject` LIKE '%1st%' AND a.`Appointment Subject` NOT LIKE '%Cancelled%' AND COALESCE(a.`Assigned Staff Member`, '') NOT IN ({ICLA_EXCLUDED_STAFF_SQL}) AND a.`Appointment Start Time` >= DATE '2025-06-01' `` |
| ICCH | `` COALESCE(a.`Appointment Subject`, '') NOT LIKE '%available%' `` |
| ICAU | none |

The `COALESCE` wrappers keep entries with a blank field, as the report does: `NOT IN` and `NOT LIKE` on NULL are not true in SQL.

- Period on `` `Appointment Start Time` ``:

```sql
-- ICCH
SELECT COUNT(*) AS `Appointments`
FROM Appointments AS a
WHERE YEAR(a.`Appointment Start Time`) = {Y} AND MONTH(a.`Appointment Start Time`) = {M}
  AND COALESCE(a.`Appointment Subject`, '') NOT LIKE '%available%'
```

```sql
-- ICLA
SELECT COUNT(*) AS `First design appointments`
FROM Appointments AS a
WHERE YEAR(a.`Appointment Start Time`) = {Y}
  AND COALESCE(a.`Activity Type`, '') NOT IN ('Installation', 'Unspecified')
  AND a.`Appointment Subject` LIKE '%1st%'
  AND a.`Appointment Subject` NOT LIKE '%Cancelled%'
  AND COALESCE(a.`Assigned Staff Member`, '') NOT IN ({ICLA_EXCLUDED_STAFF_SQL})
  AND a.`Appointment Start Time` >= DATE '2025-06-01'
```

- Success rate by source (NULL channel is a group of its own: label it "No source recorded"):

```sql
SELECT COALESCE(a.`Lead Source Channel`, 'No source recorded') AS `Source`,
       COUNT(*) AS `Appointments`,
       SUM(a.`Project Stage` = 'Sold') AS `On sold projects`,
       SUM(a.`Project Stage` = 'Sold') / COUNT(*) AS `Success rate`
FROM Appointments AS a
WHERE YEAR(a.`Appointment Start Time`) = {Y}
GROUP BY COALESCE(a.`Lead Source Channel`, 'No source recorded')
HAVING COUNT(*) >= 30
ORDER BY `Success rate` DESC
```

  Add the franchise rule to the `WHERE`.
- **Appointments to projects by name fans out.** A row-to-row `JOIN` of `Appointments` and `Salesforce_Data` on `` `Project Name` `` counts an appointment once per project sharing its name. Test membership with `EXISTS` (or `IN`), which counts each appointment once:

```sql
SELECT COUNT(*) AS `Appointments on sold projects`
FROM Appointments AS a
WHERE YEAR(a.`Appointment Start Time`) = {Y}
  AND EXISTS (SELECT 1 FROM Salesforce_Data AS s
              WHERE s.`Project Name` = a.`Project Name` AND s.`Revised Stage` = 'Sold')
```

- Last activity, one row per project (all appointment entries, no franchise rule): aggregate appointments per name first, then join.

```sql
SELECT s.`Project Name` AS `Project`, s.Amount, la.`Last activity`
FROM Salesforce_Data AS s
LEFT JOIN (SELECT a.`Project Name`, MAX(a.`Activity Date and Time`) AS `Last activity`
           FROM Appointments AS a
           WHERE a.`Project Name` IS NOT NULL
           GROUP BY a.`Project Name`) AS la
       ON la.`Project Name` = s.`Project Name`
WHERE s.Designer LIKE '%<designer>%' AND s.`Revised Stage` = 'Proposed'
ORDER BY la.`Last activity` DESC
```

- Values that look right but match nothing: `` `Project Stage` `` = 'Propose' (the data says 'Proposed') and 'Complete' (never occurs). The `` `Opportunities to Appointments` `` bridge has one row per converted lead: never use it to count projects.

## 9. Weekly report views

| View | Columns |
|---|---|
| `report_scorecard` | `report_date`, `metric_key` ∈ (sold_dollars, sold_count, apv_sold, closing_rate, open_pipeline_dollars, new_leads, lead_conversion, dead_leads_with_amount, real_appt_pct), `metric_label`, `metric_kind` (money / count / percent), `window_name` ∈ (current_week, prior_week, current_mtd, prior_mtd, current_ytd, prior_ytd), `value` |
| `report_designer_leaderboard` | `designer_name`, `sold_dollars_mtd`, `sold_count_mtd`, `decided_count_mtd`, `total_opps_mtd`, `closing_rate_mtd`, `sold_dollars_ytd`, `rank_by_sold` |
| `report_lead_source_roi` | `source_group`, `sold_dollars_mtd`, `opp_count_mtd`, `closing_rate_mtd`, `rank_by_sold` |
| `report_channel_roi` | `channel_name`, `new_leads_mtd`, `sold_opps_mtd`, `sold_dollars_mtd`, `decided_n`, `closing_rate`, `rank_by_sold` |
| `report_designer_concentration` | `top1_name`, `top1_dollars_ytd`, `top1_share`, `top2_name`, `top2_dollars_ytd`, `top2_combined_share`, `total_ytd`, `active_designers_ytd`, `warn_flag` |
| `report_appointment_funnel` | `activity_type`, `count_mtd`, `count_prior_mtd`, `mom_delta`, `share_of_mtd`, `tied_to_sold_pct`, `avg_cycle_days_mtd_sold`, `hygiene_flag`, `hygiene_pct`, `total_mtd_all`, `rank_by_count` |

Every view keeps all daily snapshots: filter to the latest `report_date`. Rates and shares are fractions (0–1).

```sql
SELECT r.metric_label AS `Metric`, r.window_name AS `Window`, r.value AS `Value`, r.report_date AS `As of`
FROM report_scorecard AS r
WHERE r.report_date = (SELECT MAX(report_date) FROM report_scorecard)
  AND r.metric_key IN ('sold_dollars', 'sold_count')
  AND r.window_name IN ('current_mtd', 'prior_mtd')
ORDER BY r.metric_key, r.window_name
```

```sql
SELECT `Designer`, `Sold this month`, `Sold projects`, `Closing rate (count-based)`
FROM (
  SELECT b.designer_name AS `Designer`, b.sold_dollars_mtd AS `Sold this month`,
         b.sold_count_mtd AS `Sold projects`, b.closing_rate_mtd AS `Closing rate (count-based)`,
         RANK() OVER (ORDER BY b.sold_dollars_mtd DESC) AS rnk
  FROM report_designer_leaderboard AS b
  WHERE b.report_date = (SELECT MAX(report_date) FROM report_designer_leaderboard)
) AS t
WHERE rnk <= 5
ORDER BY `Sold this month` DESC
```

## 10. "Why" questions and forecasts

- Contributors to a change between two windows, on the sales basis (this example: this month vs the same month last year):

```sql
SELECT s.Designer,
       SUM(CASE WHEN YEAR(s.{SALES_DATE}) = {Y} - 1 AND MONTH(s.{SALES_DATE}) = {M} THEN s.Amount ELSE 0 END) AS `Period A`,
       SUM(CASE WHEN YEAR(s.{SALES_DATE}) = {Y} AND MONTH(s.{SALES_DATE}) = {M} THEN s.Amount ELSE 0 END) AS `Period B`,
       SUM(CASE WHEN YEAR(s.{SALES_DATE}) = {Y} AND MONTH(s.{SALES_DATE}) = {M} THEN s.Amount ELSE 0 END)
     - SUM(CASE WHEN YEAR(s.{SALES_DATE}) = {Y} - 1 AND MONTH(s.{SALES_DATE}) = {M} THEN s.Amount ELSE 0 END) AS `Change`
FROM Salesforce_Data AS s
WHERE s.`Revised Stage` = 'Sold'
  AND ((YEAR(s.{SALES_DATE}) = {Y} - 1 AND MONTH(s.{SALES_DATE}) = {M})
       OR (YEAR(s.{SALES_DATE}) = {Y} AND MONTH(s.{SALES_DATE}) = {M}))
GROUP BY s.Designer
ORDER BY `Change` DESC
```

  Swap `s.Designer` for `` s.`Lead Source` `` or `` s.`Lead Source Group` ``. The `WHERE` keeps only groups with sales in one of the windows, as the model does.
- Volume vs value: sold count, sold revenue and average sold value for each window in one row (conditional sums as above).
- Funnel for two windows: leads (`` `Lead Created Date` ``), appointments under the franchise rule (`` `Appointment Start Time` ``), sold projects (sales basis).
- Weeks inside one window: group by `` `Sold Start of Week` `` (sold) or `` `Start of Week` `` (created).
- History for a forecast question: the monthly trend of section 4 over the last 12–24 months, plus the Proposed count and amount for the open pipeline.

## 11. Result mechanics

- Return raw numbers; don't format them with `FORMAT()` or `CONCAT()` (text columns lose the server's totals). Rates are fractions: present them as X.X%.
- Give every output column a readable alias in backticks; the alias is what the owner sees in the table and the Excel file.
- Always end with `ORDER BY`: rows have no inherent order.

## 12. MySQL traps

1. **Identifiers in backticks, strings in single quotes, never double quotes.** Names with spaces, punctuation or reserved words (`` `Revised Stage` ``, `` `Lead Milestones` ``, `` `Index` ``, `` `Year-Month` ``, `` `Converted Project: Project Name` ``) need backticks. Without `ANSI_QUOTES`, `"Revised Stage" = 'Sold'` compares two strings and silently returns nothing; the bot's session adds `ANSI_QUOTES`, which turns `"Sold"` into a column name and fails with "Unknown column".
2. **`ONLY_FULL_GROUP_BY` is on.** Every selected column must be grouped or aggregated; group by the same expression you select, or by its alias.
3. **No `MEDIAN` or `PERCENTILE`.** Use the window-function median of section 7.
4. **Dates.** No `CURDATE()` / `NOW()`; use `{A}`. `Salesforce_Data` dates are DATETIME: use half-open ranges. `DAYOFWEEK` is Sunday = 1, `DateTable`.`WeekdayNumber` is Monday = 1.
5. **NULL.** `x NOT IN (…)`, `x <> 'v'` and `x NOT LIKE '…'` are not true when `x` is NULL: wrap with `COALESCE(x, '')` where blanks should pass. `COUNT(col)` skips NULLs, `COUNT(*)` does not. `SUM` over no rows is NULL. `= ''` doesn't match NULL.
6. **Fan-out.** Joining `Appointments` to `Salesforce_Data` (or `Leads` to `Salesforce_Data`) on project name multiplies rows: aggregate one side per name first, or use `EXISTS`.
7. **Ties.** `LIMIT N` drops tied rows arbitrarily; rank with `RANK()` when ties matter.
8. **Zero groups.** Conditional sums (`SUM(CASE …)`) over all rows return every group, including those with nothing in the window; the model leaves such groups out. Put the window in the `WHERE`, or add `HAVING … > 0`.
9. **Two date roles.** A window on `` `Created Date` `` in the `WHERE` silently changes a sold-date count into a created-date count; closing rates need both counts in the select list (section 5).
10. **`LIKE` wildcards.** `_` and `%` match any character(s); `'%self%'` is fine, but compare full project names with `=`.
11. **One statement only**, no variables, no `SET`; the executor wraps your query to cap the rows.
