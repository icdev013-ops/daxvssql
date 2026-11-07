# Gold set

The questions and reference queries that score the DAX vs SQL POC (see [PREREGISTRATION.md](../PREREGISTRATION.md)). Business rules come from [rules/business_rules.md](../rules/business_rules.md); nothing in this folder is ever shown to the chatbot (leakage).

| File | What |
|---|---|
| `templates.yaml` | Every template: question, franchises, params, period, answer shape, tolerances, `reference_sql` and `reference_dax` |
| `validate.py` | Calibration: runs both references for every template x franchise and compares them |
| `private/` | Calibration output with real values (gitignored, never commit) |

## How a case works

A **template** is a question with paired reference queries. A **case** is a template on one franchise: case id `<template id>@<ENT>`, for example `T020@ICCH`. The references store queries, never numbers. At scoring time each answer is compared with its reference re-run seconds later on the source that answer came from: Route A (DAX) against `reference_dax` on the published dataset, Route B (SQL) against `reference_sql` on the sandbox views.

`reference_sql` is written against the franchise's view schema (`poc_icla`, `poc_icch`, `poc_icau`) with unqualified view names: the views are already entity-filtered. Identifiers are in backticks and strings in single quotes, so the SQL runs with or without `ANSI_QUOTES`. `reference_dax` is an `EVALUATE` query that uses the model's measures wherever one exists. Both return the same columns in the same order (`result_columns`).

## Template fields

| Field | Meaning |
|---|---|
| `id` | `DPQnn` dev (production question `PQnn`), `Tnnn` test, `Rnnn` robustness, `MTnn-k` multi-turn turn k, `Wnn` why/forecast, `Unn` unanswerable, `Ann` ambiguity |
| `split` | dev, test, robustness, multiturn, why_forecast, unanswerable, ambiguity |
| `source` | where the question came from: `prod70:PQnn`, `e2e170:Qnnn`, `prodlog` (real demand themes), `recipe:<ID>`, `fresh` (written for this set without looking at any bot output) |
| `types` | pre-declared question types: `appointments_sales`, `row_level_list`, `cohort_funnel`, `closing_rate`, `sold_vs_created`, `appointments_rules`, `ranking`, `time_intelligence` (a template can carry several, or none) |
| `question` | the text asked, phrased the way owners talk; may hold tokens such as `{designer}` or `{year}` |
| `paraphrases` | robustness entries only: the two rewordings |
| `franchises` | the franchises the template runs on (Test: 2 or 3) |
| `params` | placeholders resolved at run time; each has a `param_sql` run on the franchise's views (first column of the first row) |
| `period`, `periods` | anchor-relative windows (below); `periods` adds named extra windows such as `{prev: last_year_ytd}` |
| `date_basis` | documentation: `franchise_sales`, `created`, `sold (explicit)`, `lead_created`, `appointment_start`, ... |
| `answer_shape` | `number`, `ranked_list`, `series`, `table` or `rubric` |
| `result_columns`, `col_types` | output columns in order, each typed `count`, `currency`, `percent`, `number`, `date` or `text` |
| `headline` | number shape: the columns whose values must appear in the answer |
| `label_column`, `value_columns` | list shapes: the row identifier and the numeric columns compared per row |
| `top_k`, `order_by` | ranked lists: how many leading rows must match, the ranking column, direction and tie rule |
| `rubric` | rubric shapes: 2-4 pass criteria for the judge |
| `alternates` | other acceptable readings, each with its own references (and optional overrides) |
| `readings` | ambiguity templates: each acceptable reading with references; the first is the rules' default |
| `facts` | why/forecast (and some unanswerable) templates: reference queries for numbers an answer may quote |
| `evidence` | unanswerable templates: a query showing why there is no answer (`expect` = the value both routes must return) |
| `known_difference` | `{ENT or all: reason}` for a documented, accepted SQL/DAX difference |
| `notes` | definition choices and traps |

Robustness entries are YAML merge keys over their Test template (`<<: *Tnnn`), so they carry identical references; `validate.py` reuses the Test result for them. Multi-turn turns are separate entries with `conversation`, `turn`, the text as typed in `question` and the full question in `resolved_question`; turn 2 and 3 are asked with the earlier turns in history.

## Tokens

Tokens are `{name}`, `{name|filter}` or `{period.name}`. A DAX table constructor such as `{"Sold", "Lost"}` is never a token. `validate.py` refuses unknown tokens.

**Periods** (inclusive windows computed from the anchor date; weeks run Sunday to Saturday):

| Period | Window for anchor 2026-11-07 |
|---|---|
| `ytd` / `this_year` | Jan 1 to the anchor / Jan 1 to Dec 31 |
| `last_year`, `last_year_ytd`, `two_years_ago` | 2025; Jan 1 2025 to Nov 7 2025; 2024 |
| `mtd`, `this_month`, `last_month`, `last_month_mtd`, `two_months_ago`, `next_month`, `next_month_last_year` | Nov 1-7; Nov; Oct; Oct 1-7; Sep; Dec; Dec 2025 |
| `qtd`, `last_quarter`, `last_quarter_last_year`, `q1_this_year` | Oct 1 to Nov 7; Q3 2026; Q3 2025; Q1 2026 |
| `wtd`, `this_week`, `last_week`, `last_N_full_weeks` | Nov 1-7; Nov 1-7; Oct 25-31; the N full weeks before this week |
| `last_N_days` | the N days ending on the anchor, inclusive |
| `last_4_months`, `prior_4_months`, `last_12_months` | EDATE(anchor, -4) to anchor (the Salesforce "Last 4 Months" window); the 4 months before that; EDATE(anchor, -12) to anchor |
| `last_12_full_months`, `ytd_full_months` | Nov 2025 to Oct 2026; Jan to Oct 2026 |
| `yesterday`, `next_monday`, `from_today`, `before_last_year`, `all_time` | as named; `all_time` sets no tokens |

Each period gives `{d0}`, `{d1}`, `{d1_next}` (exclusive end) as `YYYY-MM-DD`, `{d0_dax}`, `{d1_dax}`, `{d1_next_dax}` as `DATE(y, m, d)`, plus `{year}`, `{month}`, `{mname}`, `{quarter}`, `{days}`. Anchor tokens: `{anchor}`, `{anchor_dax}`, `{anchor_next}`, `{anchor_year}`, `{anchor_next_year}`. References use half-open ranges (`>= d0 AND < d1_next`), which are right for both DATE and DATETIME columns.

**Macros** (per franchise, defined at the top of `templates.yaml`):
- `{sales_date_sql}` / `{sales_date_dax}` / `{sales_cal_dax}`: the sales date basis (ICLA created date and `DateTable`; ICCH and ICAU sold date and `SoldDateTable`), with `other_*` for the other basis.
- `{appt_rule_sql}`, `{appt_rule_dax}` (CALCULATE filter arguments wrapped in KEEPFILTERS) and `{appt_rule_pred_dax}` (row predicate): appointments as each franchise's report counts them. ICLA's office-staff exclusion list is read at run time from the ICLA report's Appointments page filter (`{ICLA_STAFF_SQL}` / `{ICLA_STAFF_DAX}`); the names are never stored in this repo.

**Params**: `{designer}` is the raw value, `{designer|sql}` a SQL string literal, `{designer|dax}` a DAX string literal, `{designer|first}` the first word (owners often use first names; first-name params are picked so the first name is unique in the franchise). `param_sql` picks a real value by rank or profile ("designer with 3-25 lost projects last quarter"), so the file holds no names.

## Tolerances

Scoring (`harness/score.py`) uses the column types:

| Type | Tolerance |
|---|---|
| count | exact |
| currency, number | ±0.5% relative, or the rounding the answer displays |
| percent | ±0.1 points, or the displayed rounding; both references return percentages as 0-100 |
| ranked list | the top_k labels in order and their values; tied values may appear in either order |
| series, table | every row's label and value columns |
| blank | a blank count or sum is 0 |

Calibration is stricter: two references agree when counts are equal, amounts within one cent and percentages within 0.0001 points. `near` means the two differ but sit inside the scoring tolerance; `validate.py` reports it separately because it usually signals a logic difference, not rounding.

## Running the calibration

```bash
python gold/validate.py --list                                     # counts per split and type vs quotas
python gold/validate.py --anchor 2026-11-07                        # Test + Dev
python gold/validate.py --anchor 2026-11-07 --splits all
python gold/validate.py --anchor 2026-11-07 --ids T020,T042 --franchises ICCH --show
python gold/validate.py --anchor 2026-11-07 --probes-only          # data-version check only
```

- SQL runs on the sandbox (127.0.0.1:3307) as the franchise account `poc_<ent>` (`--sql-user root` to use root), with the bot's session settings (ANSI_QUOTES, read-only, 15 s limit). `--shim` runs the view definitions as CTEs over `icall_gold` when the views are not applied.
- DAX runs through `executeQueries` with the portal's service principal (keys read at run time from the SAAS repo's `.env.local`), `includeNulls: true`, at most 25 calls a minute (the principal is shared with production). An error inside an HTTP 200 counts as an error.
- Output goes to `gold/private/`: `calibration_<anchor>.jsonl` (one line per case, alternate, reading, fact and evidence, with the resolved question, params and both results), `refs_<anchor>.jsonl` (the scorer's reference format) and a summary with agree / near / disagree / error counts per template.
- **Drift.** The sandbox is a frozen dump; the datasets refresh every 30 minutes but only change when the pipeline rebuilds Gold. The checksum probes (opportunity, lead, appointment, journey and designer row counts and Amount sums, by both routes) run before and after each calibration. If they differ, a disagreement may be data drift: refresh the sandbox right after a dataset refresh and re-run. Future-dated appointment templates (`next_month`, `next_monday`, `from_today`) move fastest.

## Traps found while calibrating

- **DAX, context transition into appointments.** `CALCULATE` inside an iteration over `Salesforce_Data` rows (`ADDCOLUMNS(FILTER(Salesforce_Data, ...), ..., CALCULATE(...))`) also filters `DateTable` (part of the expanded row), and through it the appointment dates, so per-project appointment counts silently shrink. Count appointments with `FILTER(ALL(Appointments), Appointments[Project Name] = _p && ...)` or with `TREATAS` on the project names instead (T074, T079).
- **MySQL, decimal constants.** On the `DECIMAL(18,2)` `Amount` column, `WHERE Amount > -0.001` behaves like `Amount > 0` (the constant is rounded to the column's scale) and drops zero-amount projects; the same test inside a `SELECT` expression is correct. Use `Amount >= 0` (T113).
- **DAX, the active roster's blank member.** `VALUES(dim_designers[Designer])` includes a blank row that collects the projects of inactive and x-prefixed designers; in ICLA it passed the volume guard and ranked as a "designer". Rankings on the active roster filter `NOT ISBLANK(dim_designers[Designer])` (rules section 6; T111, T120, MT05-3).
- **Duplicate project names.** `Project Name` is not unique; lists can repeat a name, and the validator then compares the rows as a multiset. Joins between appointments and projects go through the name set (`IN`, `TREATAS`) so each appointment and each project counts once.

## Adding or changing a template

1. Write the question the way an owner would, and decide the definition from `rules/business_rules.md` (sales basis, Lost/Dead Lead/Proposed on created date, appointment rule, closing-rate definition). If the question can honestly mean two things, put it in the ambiguity split with each reading.
2. Write both references, with the same column names and order, percentages as 0-100.
3. Run `validate.py --ids <id> --show` until both agree on every franchise, or document the difference in `known_difference`.
4. Run `python harness/pii_lint.py gold/templates.yaml` before committing: no person names, customer data or result numbers in this folder outside `private/`.
