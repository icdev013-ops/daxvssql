# Governed metric views

This franchise's schema also holds four **metric views** that already apply the hardest rules of the business-rules document: the sales date basis, the four closing-rate definitions, the franchise's appointment counting rule, the appointment-to-project match and the active designer roster. On the same data they return exactly what the bindings' SQL over `Salesforce_Data`, `Appointments` and `dim_designers` returns, so **prefer them for the questions below instead of rebuilding the logic yourself**. Everything else (leads, lead journey, weekly report, notes, customer fields, raw calendar entries, last activity) still comes from the mirrored views and the bindings above.

Their column names are lower-case snake_case (no backticks needed). Flags are 1/0 integers. Every row belongs to this franchise.

| Question | Use |
|---|---|
| sold revenue, sold count, average sold value, sales trend or sales ranking for a period | `metric_sales`: `is_sold = 1` and the window on `sales_date` |
| Lost, Dead Lead or Proposed for a period | `metric_sales`: the stage flag and the window on `sales_date` (it is the created date for these stages) |
| all-stage amount, project counts, stage mix for a period | `metric_sales`: the window on `created_date` |
| the other date basis, when the user asks for it | `metric_sales`: `is_sold = 1` and the window on `created_date` or `sold_date` |
| closing rate (C1, C2, C3; C4 outside "this week" / "this month"), overall, per designer or per lead source | `metric_closing_daily` |
| closing rate this week or this month, or from the weekly report | `report_scorecard` (unchanged) |
| appointment counts, rates, breakdowns and lists | `metric_appointments_reported` (franchise rule already applied) |
| appointments on sold projects, projects with appointments | `metric_appointments_reported` project-match columns |
| list or count the active designers | `metric_designers` |
| a project's last activity | `Appointments` (every entry), not `metric_appointments_reported` |

## `metric_sales`: one row per project

| Column | Meaning |
|---|---|
| `project_id` | unique project id |
| `project_name` | project name (not unique) |
| `stage` | Revised Stage: 'Sold', 'Proposed', 'Lost', 'Dead Lead' |
| `raw_stage` | Salesforce stage (use only when the user names one) |
| `amount` | project amount, every stage |
| `sold_amount` | `amount` when Sold, else NULL |
| `is_sold`, `is_proposed`, `is_lost`, `is_dead_lead` | stage flags |
| `created_date` | created date (DATETIME at midnight) |
| `sold_date` | original project sold date (DATETIME, NULL when none) |
| `sales_date` | the franchise's default period date: on a Sold row the franchise sales basis (ICLA created date, ICCH and ICAU sold date); on Lost, Dead Lead and Proposed rows always the created date. NULL on a Sold row that has no sold date when the basis is the sold date |
| `sales_date_basis` | what `sales_date` holds on this row: 'sold date' or 'created date'. Name it in the answer ("by sold date") and offer the other basis |
| `designer` | the designer on the project, active or not (sales rankings use every name) |
| `is_active_designer` | 1 when the designer is on the active roster |
| `lead_source`, `lead_source_group` | lead source (free text: match with LIKE) and its group (HOUSE / SG,R,R / STOW) |
| `sale_cycle_days` | days from created to sold |

- Sold figures need `is_sold = 1` **and** the window on `sales_date`. Sales this year to date:

```sql
SELECT SUM(sold_amount) AS `Sold revenue`, SUM(is_sold) AS `Sold projects`, AVG(sold_amount) AS `Average sold value`,
       MIN(sales_date_basis) AS `Basis`
FROM metric_sales
WHERE is_sold = 1 AND sales_date >= MAKEDATE({Y}, 1) AND sales_date < {A} + INTERVAL 1 DAY
```

- Windows on `sales_date`, `created_date` and `sold_date` are DATETIME: use half-open ranges (`>= start AND < end + INTERVAL 1 DAY`), or `YEAR()` / `MONTH()`.
- Sales rankings: `GROUP BY designer` (or `lead_source`, `lead_source_group`) over `is_sold = 1` rows in the window; add `is_active_designer = 1` only when the user says "active". A person's sales with no period: one query with a conditional sum for this year on `sales_date` and the all-time sum.
- **All-stage figures for a period** (total amount, project count, stage mix, a breakdown across stages) use `created_date` for every stage, Sold included; say so.
- The other basis: `is_sold = 1` with the window on `created_date` or `sold_date`. Sold projects with no sold date never count on the sold basis but stay in all-time totals (no date filter). Before 2024 most sales have no sold date: for such a window return both bases in one row (`SUM(CASE WHEN sold_date … THEN sold_amount END)`, `SUM(CASE WHEN created_date … THEN sold_amount END)`).
- Sale cycle for a period: `AVG(sale_cycle_days)` over every row whose franchise sales-basis column is in the window: `sold_date` for ICCH and ICAU, `created_date` for ICLA (no stage filter, as the report's average cycle).

## `metric_closing_daily`: closing-rate building blocks

One row per day × designer × lead source × lead source group that has at least one event: a project created that day, or a Sold project whose sold date is that day. Days run 2020-01-01..2030-12-31 only (the reporting calendar), so "all time" is the sum over every row.

| Column | Meaning |
|---|---|
| `event_date` | the day (DATE) |
| `designer`, `is_active_designer` | the project's designer; 1 when on the active roster |
| `lead_source`, `lead_source_group` | as in `metric_sales` |
| `projects_created` | projects created that day, every stage |
| `projects_created_excl_dead` | projects created that day, Dead Lead left out |
| `projects_sold` | Sold projects whose sold date is that day |
| `c4_sold` | Sold projects created that day |
| `c4_decided` | Sold + Lost + Dead Lead projects created that day (Proposed never) |

A window is a sum over its days, then **one** division. Never divide per row or average rates.

| Definition | Formula over the window's rows |
|---|---|
| **C1** closing rate (Salesforce Community), the default | `LEAST(1, SUM(projects_sold) / NULLIF(SUM(projects_created), 0))`: capped at 100%, on the window total |
| **C2** cohort closing rate (sold-date page) | `SUM(projects_sold) / NULLIF(SUM(projects_created_excl_dead), 0)`, no cap |
| **C3** 120-day closing rate (KPI Overview) | the C2 formula over `event_date >= {A} - INTERVAL 119 DAY AND event_date <= {A}` |
| **C4** count-based (weekly report) | `SUM(c4_sold) / NULLIF(SUM(c4_decided), 0)`, both on the created date |

- Windows (`event_date` is a DATE, so `<= {A}` is inclusive): year to date `event_date >= MAKEDATE({Y}, 1) AND event_date <= {A}`; last 4 months `event_date >= {A} - INTERVAL 4 MONTH AND event_date <= {A}`; a month or year with `YEAR()` / `MONTH()`; all time no condition. No period named = year to date.
- C1 year to date, with its two counts:

```sql
SELECT LEAST(1, SUM(projects_sold) / NULLIF(SUM(projects_created), 0)) AS `Closing rate`,
       SUM(projects_created) AS `Projects created`, SUM(projects_sold) AS `Projects sold`
FROM metric_closing_daily
WHERE event_date >= MAKEDATE({Y}, 1) AND event_date <= {A}
```

- Per designer (the report's table: active roster, at least 30 projects created in the window):

```sql
SELECT designer AS `Designer`, SUM(projects_created) AS `Projects created`, SUM(projects_sold) AS `Projects sold`,
       LEAST(1, SUM(projects_sold) / NULLIF(SUM(projects_created), 0)) AS `Closing rate`
FROM metric_closing_daily
WHERE is_active_designer = 1 AND event_date >= MAKEDATE({Y}, 1) AND event_date <= {A}
GROUP BY designer
HAVING SUM(projects_created) >= 30
ORDER BY `Closing rate` DESC
```

  Per lead source: `GROUP BY lead_source` without the roster condition. Per month: `GROUP BY YEAR(event_date), MONTH(event_date)` with the formula per group.
- A NULL rate means no projects were created in the window: say so, not 0%. Name the definition and the window in the answer.

## `metric_appointments_reported`: appointments as the franchise's report counts them

One row per appointment that passes this franchise's counting rule (ICLA: first design appointments from 2025-06-01, office staff excluded; ICCH: every entry except availability holds; ICAU: every calendar entry, tasks included). Never add the rule again.

| Column | Meaning |
|---|---|
| `appointment_key` | unique appointment id |
| `appointment_date` | the period date (appointment start date, DATE) |
| `activity_type`, `subject`, `assigned_staff` | as on the calendar entry |
| `appointment_source` | lead source channel, 'No source recorded' when blank |
| `project_name`, `project_stage` | the appointment's project name and project stage |
| `is_stage_sold` | 1 when `project_stage` = 'Sold' |
| `is_first_appointment` | the customer's-first-appointment flag |
| `designer` | the appointment's designer on the active roster (NULL otherwise) |
| `matched_projects` | how many projects carry this project name (0 = none; names repeat) |
| `matched_project_id` | the project's id when exactly one project has this name, else NULL |
| `matched_project_sold` | 1 when a Sold project has this name |
| `counting_rule` | the rule's name, to say in the answer (ICLA: "first design appointments") |

- Count: `COUNT(*)` with the window on `appointment_date`. The current month or year includes appointments already booked for later in it.
- Appointment success rate: `SUM(is_stage_sold) / NULLIF(COUNT(*), 0)`; by source `GROUP BY appointment_source` with `HAVING COUNT(*) >= 30` for rankings.
- Appointments on sold projects (project match): `SUM(matched_project_sold)`. Each appointment is one row, so nothing fans out.
- Project figures from appointments: `JOIN metric_sales AS s ON s.project_id = a.matched_project_id` (one project per appointment, uniquely named projects only), or go from `metric_sales` with `EXISTS (SELECT 1 FROM metric_appointments_reported AS a WHERE a.project_name = s.project_name)` to count each project once.
- ICLA has no rows before 2025-06-01 under its rule: say so when asked about earlier periods.

```sql
SELECT appointment_source AS `Source`, COUNT(*) AS `Appointments`, SUM(is_stage_sold) AS `On sold projects`,
       SUM(is_stage_sold) / NULLIF(COUNT(*), 0) AS `Success rate`, MIN(counting_rule) AS `Counted`
FROM metric_appointments_reported
WHERE YEAR(appointment_date) = {Y}
GROUP BY appointment_source
HAVING COUNT(*) >= 30
ORDER BY `Success rate` DESC
```

## `metric_designers`: the active roster

| Column | Meaning |
|---|---|
| `designer` | active designer (unassigned and blank names excluded) |
| `sort_order` | the report's display order |
| `total_projects` | projects on the roster record |
| `has_projects` | 1 when the designer has at least one project |

- "List the active designers": `SELECT designer FROM metric_designers WHERE has_projects = 1 ORDER BY designer`.
- "How many designers were active in <period>" counts distinct designers on projects created in that period: `SELECT COUNT(DISTINCT designer) FROM metric_sales WHERE <window on created_date>`.
- Sales rankings are not limited to this roster (use `metric_sales`).
