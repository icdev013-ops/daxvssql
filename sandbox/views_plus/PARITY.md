# Parity: arm B+ metric views

Two checks, run 2026-11-07 with anchor date 2026-11-07. This file records outcomes and relative differences only: the values themselves are in `daxvssql/runs/` (gitignored), because the repo is public.

## 1. Metric views vs the verified bindings SQL (same sandbox data)

`python sandbox/views_plus/tools/verify_plus.py` runs each metric-view query and the matching query of `rules/sql_bindings.md` (over the pass-through views) through the franchise's own `pocplus_<ent>` account and compares the complete result sets. Result: **66 / 66 checks identical** (22 per franchise × ICLA, ICCH, ICAU).

| Check (per franchise) | Bindings section | Result |
|---|---|---|
| `metric_sales` has one row per project (row count and distinct `project_id` = `Salesforce_Data`) | 0 | identical |
| sold revenue and count, year to date, franchise basis | 4 | identical |
| sold revenue 2024, franchise basis | 4 | identical |
| sold revenue and count, all time | 3 | identical |
| Lost / Dead Lead / Proposed amounts, year to date, created date | 4 | identical |
| sold revenue by designer, year to date, franchise basis (every designer) | 6 | identical |
| `is_active_designer` = inner join to `dim_designers` | 6 | identical |
| C1 year to date (rate, created, sold) | 5 | identical |
| C1 last 4 months | 5 | identical |
| C1 all time (2020-2030 calendar) | 5 | identical |
| C1 calendar year 2025 | 5 | identical |
| C2 year to date | 5 | identical |
| C3 (120 days ending on the anchor date, raw-stage numerator) | 5 | identical |
| C4 year to date | 5 | identical |
| C1 per active designer, at least 30 projects created | 5 | identical |
| C1 by lead source | 5 | identical |
| `metric_appointments_reported` has one row per appointment | 8 | identical |
| appointments 2026 under the franchise rule, success rate, appointments on sold projects (EXISTS) | 8 | identical |
| all appointments under the franchise rule | 8 | identical |
| appointments 2026 by source (count, on sold projects) | 8 | identical |
| uniquely matched `matched_project_id` values are real projects | 8 | identical |
| `metric_designers` = roster; `has_projects` = `Total Projects` > 0 | 6 | identical |

ICLA's 2026 first design appointments from `metric_appointments_reported` equal the count stated in `rules/CONFLICTS.md` item 25.

## 2. Metric views vs the Power BI measures (published datasets)

`npx tsx scripts/parity_plus.ts` (from `bot/`) sends one `executeQueries` call per franchise (3 calls, at least 7 s apart, read-only service principal as in `bot/src/tools/dax.ts`) and runs the matching metric-view queries on the sandbox. DAX side: `[Revenue Sold]` and `[Count Sold]` with a window on the franchise's sales-date column; `[Closing Rate]`, `[Projects Created (Period)]`, `[Projects Sold (Period)]` with a `DateTable[Date]` table filter; C4 inline; `[Total Activity Count]` and `[Project Success Rate]` with the franchise's report-page appointment rule (ICLA's staff list supplied at run time); the roster count from `dim_designers[Total Projects] > 0`. Window: 2026-01-01 to 2026-11-07.

Data versions: sandbox dump started 2026-11-07 01:29 PT; the three datasets last refreshed 2026-11-07 03:36 PT.

| Aggregate | ICLA | ICCH | ICAU |
|---|---|---|---|
| sold revenue, year to date, franchise basis (ICLA created, ICCH and ICAU sold) | exact | exact | exact |
| sold count, year to date, franchise basis | exact | exact | exact |
| C1 projects created, year to date | exact | exact | exact |
| C1 projects sold, year to date | exact | exact | exact |
| C1 closing rate, year to date | equal to 4 decimals | equal to 4 decimals | equal to 4 decimals |
| C4 closing rate, year to date | equal to 4 decimals | equal to 4 decimals | equal to 4 decimals |
| appointments 2026 under the franchise rule (ICLA: first design appointments) | exact | exact | exact |
| appointment success rate 2026 | equal to 4 decimals | equal to 4 decimals | equal to 4 decimals |
| active designers with at least one project | exact | exact | exact |

"Equal to 4 decimals": the SQL rate is exactly the DAX rate rounded to 4 decimal places, because MySQL returns a division of integers as DECIMAL with 4 decimals (`div_precision_increment` = 4). The largest gap is 0.005 percentage points, far inside the scorer's 0.1-point tolerance; arm B's bindings divide the same way. C1 recomputed from the two exact counts equals the DAX `[Closing Rate]` exactly.

No difference traced to data drift between the sandbox snapshot and the datasets: every count and amount in the window matched exactly.

## Re-running

After `refresh`, `apply-views` or a template change: `python sandbox/sandbox.py apply-plus`, then `python sandbox/views_plus/tools/verify_plus.py`, then `python sandbox/rls_test.py`. The DAX side needs the chatbot repo's `.env.local` (service principal and dataset ids).
