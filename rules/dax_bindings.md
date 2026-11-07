# DAX bindings

How each section of the business rules maps to the franchise's Power BI semantic model, in the same section order. The model's measures carry much of the business logic; their definitions are shown where they matter.

Placeholders used below:

| Placeholder | Meaning |
|---|---|
| `{A}` | the anchor date from the context line, written as `DATE(yyyy, m, d)` |
| `{Y}`, `{M}` | the anchor's year and month number |
| `{SALES_DATE}` | the franchise's sales-date column (section 4): `Salesforce_Data[Created Date]` for ICLA, `Salesforce_Data[Original Project Sold Date]` for ICCH and ICAU |
| `{SALES_CAL}` | the matching calendar table: `DateTable` for ICLA, `SoldDateTable` for ICCH and ICAU |
| `{ICLA_EXCLUDED_STAFF_DAX}` | ICLA's excluded office-staff list as a DAX set `{"…", "…"}`, supplied with the prompt |
| `<designer>`, `<source>` | a name taken from the user's question |

If a placeholder still shows braces in your prompt, substitute it yourself from the context line and the tables here.

## 0. Model map

The dataset holds one franchise only; its entity filter is built into every table, so never filter on `entity`.

| Table | Grain / use |
|---|---|
| `Salesforce_Data` | one row per opportunity (project): `Project ID` (unique), `Project Name` (not unique), `Stage`, `Revised Stage`, `Amount`, `Created Date`, `Original Project Sold Date`, `Designer`, `Lead Source`, `Lead Source Group`, `Project Notes`, `Consumer Name: Full Name`, `Sale Cycle`, `Last Modified Date`; calculated `Sold Year`, `Sold Month`, `Sold Month Number`, `Sold Start of Week`; `Start of Week` (created). ICAU only: `Additional Lead Source` |
| `Leads` | one row per lead: `Lead ID`, `Lead Created Date`, `Lead Status`, `Lead Source Channel`, `Converted Project Name`, `Lead Capture Form Type`, contact fields, `Project Notes`, `Initial Comments`, `Lead Description` |
| `Appointments` | one row per calendar entry or task: `Appointment Start Time`, `Activity Date and Time`, `Activity Type`, `Appointment Subject`, `Assigned Staff Member`, `Project Name`, `Project Stage`, `Lead Source Channel`, `Is First Appointment`, `designer_sk` |
| `Leads_Journey` | one row per lead: `Current Status`, `Lead Journey` (status path text), `Total Status Changes` |
| `'Lead Milestones'` | one row per lead: `Contact Path`, `Contact Attempts`, `Booked Appointment`, `Reached Studio`, `Journey Outcome`, `Sold Amount`, `Days to Scheduled`, `Days to Studio`, `Days to Sold`, `Exclude From Journey` |
| `'Opportunities to Appointments'` | one row per converted lead with its first appointment (funnel bridge) |
| `dim_designers` | **active** designers only (`Designer`, `Total Projects`, `Sort`) |
| `dim_projects` | distinct project names (hub) |
| `DateTable`, `SoldDateTable` | calendars 2020-01-01..2030-12-31: `Date`, `Year`, `Month`, `MonthNumber`, `Quarter`, `Year-Month`, `Start Of Week` (Sunday) |
| `report_scorecard`, `report_designer_leaderboard`, `report_lead_source_roi`, `report_channel_roi`, `report_designer_concentration`, `report_appointment_funnel` | weekly-report snapshots (section 9) |
| `'Projects Part List'` | ICLA only: parts per project |

Relationships (many-to-one, single direction, active unless noted):

| From | To | Note |
|---|---|---|
| `Salesforce_Data[Created Date]` | `DateTable[Date]` | active: DateTable filters give the **created** basis |
| `Salesforce_Data[Original Project Sold Date]` | `SoldDateTable[Date]` | active: SoldDateTable filters give the **sold** basis |
| `Salesforce_Data[Original Project Sold Date]` | `DateTable[Date]` | inactive; no measure uses it |
| `Leads[Lead Created Date]`, `Appointments[Appointment Start Time]` | `DateTable[Date]` | `Appointments[Activity Date and Time]` is not related |
| `'Opportunities to Appointments'[Activity Date and Time]` | `DateTable[Date]` | inactive; funnel measures activate it |
| `Salesforce_Data[Project Name]`, `Leads[Converted Project Name]`, `Appointments[Project Name]` | `dim_projects[Project Name]` | three separate single-direction links: **no filter flows between Salesforce_Data, Leads and Appointments** |
| `'Opportunities to Appointments'[Converted Project: Project Name]` | `dim_projects` | both directions |
| `'Lead Milestones'[Lead ID]` | `Leads[Lead ID]` | 1:1, both directions: Leads filters reach the JN measures |
| `Leads[Lead ID]` | `Leads_Journey[Lead ID]` | |
| `Salesforce_Data[Designer]` | `dim_designers[Designer]` | inactive designers' rows fall under a blank designer |
| `Appointments[designer_sk]` | `dim_designers[designer_sk]` | |
| `Salesforce_Data[Lead Source]` → `Source_Sort`; `[Lead Source Group]` → `'LS Group Sort'`; `Leads` / `Appointments[Lead Source Channel]` → `'Lead Source Channels'` | | lookups |
| `report_*` | none | |

Measures that return text, HTML or SVG and must never be queried: every `Date Summary *` and `Filter Summary *`, `JN Sub *`, `JN TT *`, `JN Leads Card`, `KPI Sub *`, `KPI Page HTML v2`, `KPI Page HTML`, `Report Guide HTML`, `Goals Page HTML`, `Journey Path SVG`, `Dynamic Legend 1`–`5`, `Tooltip Control`, `Amount $`, `Appointments X Amount`.

## 1. Answering-rule mechanics

- Every query starts with `EVALUATE` and returns a table. Wrap scalars in one `ROW("Name", expr, "Name 2", expr2)`.
- Rule 4 measures: sold count `[Count Sold]`, sold revenue `[Revenue Sold]`, all projects `[Count Total Projects]`, leads `[Total Lead Count]`. Don't rebuild them with `COUNTROWS` / `SUM`, and don't add `NOT ISBLANK(<date>)` filters.
- Lists return rows with `SELECTCOLUMNS` and clean aliases, filtered by `CALCULATETABLE`:

```dax
EVALUATE
CALCULATETABLE(
    SELECTCOLUMNS(Salesforce_Data,
        "Project", Salesforce_Data[Project Name], "Designer", Salesforce_Data[Designer],
        "Amount", Salesforce_Data[Amount], "Stage", Salesforce_Data[Revised Stage],
        "Created", Salesforce_Data[Created Date], "Notes", Salesforce_Data[Project Notes]),
    Salesforce_Data[Revised Stage] = "Lost",
    YEAR(Salesforce_Data[Created Date]) = {Y})
ORDER BY [Amount] DESC
```

- The server returns at most 50 rows and flags truncation. Wrap rankings in `TOPN` (it keeps ties at the cut-off).
- A BLANK result comes back as "Result was BLANK": answer 0 for counts and sums.
- The server prints exact column totals under every multi-row result; quote them instead of adding.
- Loose names: `CONTAINSSTRING(Salesforce_Data[Designer], "<designer>")` (case-insensitive). `=` and `IN` are exact but also case-insensitive.
- Columns that do not exist (production 400s): `Leads[Lead Owner/Designer]` (use `Leads[Lead Creator Name]`), `Leads[Created Month]` (use `Leads[Lead Creation Year-Month]`), `Salesforce_Data[Lead Owner]` (use `Salesforce_Data[Designer]`).
- The tool answers with `Error: …` (malformed DAX), `No rows returned.` (filter matched nothing, often a spelling) or `Result was BLANK` (filter inside `CALCULATE` matched nothing).

## 2. Dates and periods

- Write the anchor as a literal, `{A}` = `DATE(yyyy, m, d)`. Don't use `TODAY()` or `NOW()`: they run on the Power BI service clock (UTC), not the franchise's date.

| Business date | Column | Calendar it filters through |
|---|---|---|
| opportunity created | `Salesforce_Data[Created Date]` | `DateTable` |
| sold | `Salesforce_Data[Original Project Sold Date]` | `SoldDateTable` |
| lead created | `Leads[Lead Created Date]` | `DateTable` |
| appointment period | `Appointments[Appointment Start Time]` | `DateTable` |
| appointment activity (last activity only) | `Appointments[Activity Date and Time]` | none |

All of these hold dates without a time of day.

- Window filters as `CALCULATE` / `CALCULATETABLE` arguments on the column:
  - calendar year: `YEAR(col) = {Y}`; calendar month: `YEAR(col) = {Y}, MONTH(col) = {M}`; quarter: `YEAR(col) = {Y}, QUARTER(col) = q`;
  - year to date: `col >= DATE({Y}, 1, 1) && col <= {A}`;
  - last month: `YEAR(col) = YEAR(EDATE({A}, -1)), MONTH(col) = MONTH(EDATE({A}, -1))`;
  - last N days: `col >= {A} - (N - 1) && col <= {A}`;
  - same period last year: `col >= DATE({Y} - 1, 1, 1) && col <= EDATE({A}, -12)`.
- Weeks start on Sunday: `{A} - WEEKDAY({A}, 1) + 1` is the Sunday of the anchor's week. Group weeks by `DateTable[Start Of Week]`, `Salesforce_Data[Start of Week]` (created) or `Salesforce_Data[Sold Start of Week]` (sold). `Appointments[Start Of WeekA]` follows the activity date, not the start date: don't use it for appointment weeks.
- Filters on the calendar instead of the column must be table filters: `FILTER(ALL(DateTable[Date]), DateTable[Date] >= DATE({Y}, 1, 1) && DateTable[Date] <= {A})`. A bare `DateTable[Date] >= …` argument inside `SUMMARIZECOLUMNS` is a query error.
- Calendar bound: `DateTable` and `SoldDateTable` run 2020-01-01..2030-12-31. Rows dated outside it sit under a blank `Year` / `Month` member when you group by the calendar; leave that row out and say older records exist (opportunities created before 2020: ICLA 1,535, ICCH 1,175, ICAU 1,025 on the 2026-11-07 data). Plain all-time counts and sums have no date filter.

```dax
EVALUATE
FILTER(
    SUMMARIZECOLUMNS(DateTable[Year], "Projects", [Count Total Projects], "Amount", [Total Revenue]),
    NOT ISBLANK(DateTable[Year]))
ORDER BY DateTable[Year]
```

- Don't use the model's time-intelligence measures for anchored periods: `[Revenue Previous Year]` (`SAMEPERIODLASTYEAR`), `[Revenue Previous Month]`, `[Revenue YoY Growth]`, `[Current Month Lead Count]`, `[Monthly Lead Growth Rate]` (ignores the year), `[Month Over Month Lead Growth]`, `[Current Month Activity Count]` (uses the activity date and `TODAY()`), `[Future Appointment Count]` (`TODAY()`). Write the windows explicitly.
- Auto date/time is off: `Salesforce_Data[Created Date].[Year]` and similar do not exist.

## 3. Projects, stages and money

| Business term | DAX |
|---|---|
| all-stage amount ("revenue", "bid amount") | `[Total Revenue]` = `SUM(Salesforce_Data[Amount])` |
| sold revenue | `[Revenue Sold]` = `CALCULATE(SUM(Salesforce_Data[Amount]), Salesforce_Data[Revised Stage] = "Sold")` |
| open pipeline / lost amount | `[Revenue Proposed]`, `[Revenue Lost]` (same shape, `"Proposed"` / `"Lost"`) |
| projects | `[Count Total Projects]` = `COUNTROWS(Salesforce_Data)` |
| sold / proposed / lost count | `[Count Sold]`, `[Count Proposed]`, `[Count Lost]` (`CALCULATE(COUNTROWS(Salesforce_Data), Salesforce_Data[Revised Stage] = "…")`) |
| dead leads (no measure) | `CALCULATE(COUNTROWS(Salesforce_Data), Salesforce_Data[Revised Stage] = "Dead Lead")`; amount: `CALCULATE([Total Revenue], Salesforce_Data[Revised Stage] = "Dead Lead")` |
| lost or dead | `Salesforce_Data[Revised Stage] IN {"Lost", "Dead Lead"}` |
| Sold % / Proposed % / Lost % (revenue shares) | `[Sold Percentage]` = `DIVIDE([Revenue Sold], [Total Revenue], 0)`; `[Proposed Percentage]`, `[Lost Percentage]` likewise |
| average sold value | `[Average Project Value Sold]` = `DIVIDE([Revenue Sold], [Count Sold], 0)` |
| average amount per opportunity | `AVERAGE(Salesforce_Data[Amount])` |
| "Avg project value" card, deals above $1,000 | `[APV]` = `DIVIDE([Total Revenue (>1000)], [Total Records (>1000)], 0)`; `[Total Revenue (>1000)]` = `SUMX(FILTER(Salesforce_Data, Salesforce_Data[Amount] > 1000), Salesforce_Data[Amount])` |
| sale cycle | `[Av Cycle]` = `AVERAGE(Salesforce_Data[Sale Cycle])` |
| distinct lead sources / designers | `DISTINCTCOUNTNOBLANK(Salesforce_Data[Lead Source])` / `DISTINCTCOUNTNOBLANK(Salesforce_Data[Designer])` |

- Stage values: `Salesforce_Data[Revised Stage]` ∈ {"Sold", "Proposed", "Lost", "Dead Lead"}; raw `Salesforce_Data[Stage]` ∈ {"Complete", "Ready to Install", "Ready to Order", "Incomplete", "Propose", "Design", "Lost", "Dead Lead"}. `Salesforce_Data[Lead Source Group]` ∈ {"HOUSE", "SG,R,R", "STOW"}.
- Stage funnel (created basis for a period):

```dax
EVALUATE
CALCULATETABLE(
    SUMMARIZECOLUMNS(Salesforce_Data[Revised Stage],
        "Projects", [Count Total Projects], "Amount", [Total Revenue]),
    YEAR(Salesforce_Data[Created Date]) = {Y})
ORDER BY [Projects] DESC
```

- Top N projects are rows:

```dax
EVALUATE
TOPN(10,
    CALCULATETABLE(
        SELECTCOLUMNS(Salesforce_Data,
            "Project", Salesforce_Data[Project Name], "Designer", Salesforce_Data[Designer],
            "Amount", Salesforce_Data[Amount], "Sold date", Salesforce_Data[Original Project Sold Date]),
        Salesforce_Data[Revised Stage] = "Sold"),
    [Amount], DESC)
ORDER BY [Amount] DESC
```

- Self-gen by stage (sum the rows for the total bid amount; add a designer or year filter inside each `CALCULATE` as needed):

```dax
EVALUATE
SUMMARIZECOLUMNS(Salesforce_Data[Revised Stage],
    "Bid amount", CALCULATE(SUM(Salesforce_Data[Amount]), CONTAINSSTRING(Salesforce_Data[Lead Source], "self")),
    "Projects", CALCULATE(COUNTROWS(Salesforce_Data), CONTAINSSTRING(Salesforce_Data[Lead Source], "self")))
```

- Share of a total, captured once in a variable:

```dax
EVALUATE
VAR _total = CALCULATE([Revenue Sold], YEAR({SALES_DATE}) = {Y})
RETURN
    TOPN(5,
        SUMMARIZECOLUMNS(Salesforce_Data[Designer],
            "Sold", CALCULATE([Revenue Sold], YEAR({SALES_DATE}) = {Y}),
            "Share", DIVIDE(CALCULATE([Revenue Sold], YEAR({SALES_DATE}) = {Y}), _total)),
        [Sold], DESC)
ORDER BY [Sold] DESC
```

- Volume guard on rate rankings: wrap the summary in `FILTER(…, [n] >= 30)` (projects), `>= 50` (leads), `>= 5` (sold).
- Project to lead: filter `Leads[Converted Project Name] = "<project>"`; `Lead ID` is an opaque Salesforce ID, never a project name.

## 4. Sales date basis

| Franchise | `{SALES_DATE}` | `{SALES_CAL}` | Group by month / week |
|---|---|---|---|
| ICLA | `Salesforce_Data[Created Date]` | `DateTable` | `DateTable[Year]`, `DateTable[MonthNumber]`; `Salesforce_Data[Start of Week]` |
| ICCH, ICAU | `Salesforce_Data[Original Project Sold Date]` | `SoldDateTable` | `Salesforce_Data[Sold Year]`, `[Sold Month Number]`, `[Sold Month]` or `SoldDateTable[Year]`, `[MonthNumber]`; `Salesforce_Data[Sold Start of Week]` |

- `[Count Sold]` and `[Revenue Sold]` have no date of their own: under a `DateTable` filter they count by created date (active relationship), under a `SoldDateTable` filter by sold date. Filtering the column directly (`YEAR({SALES_DATE}) = {Y}`) gives the same result as filtering its calendar. Grouping a sold-basis trend by `DateTable[Month]` silently switches it to created date.
- Sales this year on the franchise basis:

```dax
EVALUATE
ROW(
    "Sold revenue", CALCULATE([Revenue Sold], YEAR({SALES_DATE}) = {Y}),
    "Sold projects", CALCULATE([Count Sold], YEAR({SALES_DATE}) = {Y}),
    "Average sold value", CALCULATE([Average Project Value Sold], YEAR({SALES_DATE}) = {Y}))
```

- Rule 6, a person's sales with no period (this year and all time in one query):

```dax
EVALUATE
ROW(
    "Sold revenue {Y}", CALCULATE([Revenue Sold], CONTAINSSTRING(Salesforce_Data[Designer], "<designer>"), YEAR({SALES_DATE}) = {Y}),
    "Sold projects {Y}", CALCULATE([Count Sold], CONTAINSSTRING(Salesforce_Data[Designer], "<designer>"), YEAR({SALES_DATE}) = {Y}),
    "Sold revenue all time", CALCULATE([Revenue Sold], CONTAINSSTRING(Salesforce_Data[Designer], "<designer>")),
    "Sold projects all time", CALCULATE([Count Sold], CONTAINSSTRING(Salesforce_Data[Designer], "<designer>")))
```

  For a lead source, swap the designer filter for `CONTAINSSTRING(Salesforce_Data[Lead Source], "<source>")`.
- Monthly sales trend on the sold basis (ICCH, ICAU); for ICLA use `DateTable` in the same shape:

```dax
EVALUATE
SUMMARIZECOLUMNS(SoldDateTable[Year], SoldDateTable[MonthNumber], SoldDateTable[Month],
    FILTER(ALL(SoldDateTable[Year]), SoldDateTable[Year] = {Y}),
    "Sold revenue", [Revenue Sold], "Sold projects", [Count Sold])
ORDER BY SoldDateTable[MonthNumber]
```

- The other basis (follow-up "by created date" / "by sold date"): swap `{SALES_DATE}` for the other column, `Salesforce_Data[Created Date]` or `Salesforce_Data[Original Project Sold Date]`.
- Lost, Dead Lead, Proposed, `[Total Revenue]`, `[Count Total Projects]` and stage mixes filter `Salesforce_Data[Created Date]`.
- Sold rows with a blank sold date drop out of any sold-date filter automatically and stay in unfiltered all-time totals. Sold dates exist only from mid-2023, so for a sold-basis window before 2024 return both bases in one `ROW` (one `CALCULATE` on `Salesforce_Data[Original Project Sold Date]`, one on `Salesforce_Data[Created Date]`).
- Sale cycle for a period: `CALCULATE([Av Cycle], YEAR({SALES_DATE}) = {Y})`.

## 5. Closing rate

**C1 (default).** `[Closing Rate]`, with its parts `[Projects Created (Period)]` and `[Projects Sold (Period)]`:

```dax
VAR _MinDate = MIN(DateTable[Date])
VAR _MaxDate = MAX(DateTable[Date])
VAR _Created = CALCULATE(COUNTROWS(Salesforce_Data), REMOVEFILTERS(DateTable),
    Salesforce_Data[Created Date] >= _MinDate && Salesforce_Data[Created Date] <= _MaxDate)
VAR _Sold = CALCULATE(COUNTROWS(Salesforce_Data), REMOVEFILTERS(DateTable),
    Salesforce_Data[Revised Stage] = "Sold",
    Salesforce_Data[Original Project Sold Date] >= _MinDate && Salesforce_Data[Original Project Sold Date] <= _MaxDate)
VAR _Raw = DIVIDE(_Sold, _Created, BLANK())
RETURN IF(ISBLANK(_Raw), BLANK(), IF(_Raw > 1, 1, _Raw))
```

- **Period rule (hard).** The window is `MIN`/`MAX` of `DateTable[Date]`. Set it only with a table filter on `DateTable[Date]`; never filter `Salesforce_Data[Created Date]` or `[Original Project Sold Date]` alongside these measures (that leaves the window at 2020–2030 and returns a meaningless rate). The server rejects such queries. With no `DateTable` filter the window is the whole 2020–2030 calendar.
- Year to date:

```dax
EVALUATE
CALCULATETABLE(
    ROW("Closing rate", [Closing Rate],
        "Projects created", [Projects Created (Period)],
        "Projects sold", [Projects Sold (Period)]),
    FILTER(ALL(DateTable[Date]), DateTable[Date] >= DATE({Y}, 1, 1) && DateTable[Date] <= {A}))
```

  Last 4 months: `DateTable[Date] >= EDATE({A}, -4) && DateTable[Date] <= {A}`. A calendar month: first to last day of that month.
- Per designer on the active roster, volume-guarded:

```dax
EVALUATE
FILTER(
    SUMMARIZECOLUMNS(dim_designers[Designer],
        FILTER(ALL(DateTable[Date]), DateTable[Date] >= DATE({Y}, 1, 1) && DateTable[Date] <= {A}),
        "Projects created", [Projects Created (Period)],
        "Projects sold", [Projects Sold (Period)],
        "Closing rate", [Closing Rate]),
    NOT ISBLANK(dim_designers[Designer]) && [Projects created] >= 30)
ORDER BY [Closing rate] DESC
```

  By lead source: group by `Salesforce_Data[Lead Source]` in the same shape.

**C2.** `[Closing Rate (Cohort)]` = `DIVIDE([Projects Sold (Cohort)], [Projects Created (Cohort)])`, window from `SoldDateTable[Date]`, no cap:

```dax
'Projects Created (Cohort)' = VAR _Min = MIN(SoldDateTable[Date]) VAR _Max = MAX(SoldDateTable[Date])
RETURN CALCULATE(COUNTROWS(Salesforce_Data), REMOVEFILTERS(DateTable), REMOVEFILTERS(SoldDateTable),
    FILTER(ALL(Salesforce_Data[Created Date], Salesforce_Data[Original Project Sold Date], Salesforce_Data[Revised Stage]),
        Salesforce_Data[Revised Stage] <> "Dead Lead" && Salesforce_Data[Created Date] >= _Min && Salesforce_Data[Created Date] <= _Max))
'Projects Sold (Cohort)'    = … same, with Salesforce_Data[Revised Stage] = "Sold" && Salesforce_Data[Original Project Sold Date] >= _Min && … <= _Max
```

  Set the window with `FILTER(ALL(SoldDateTable[Date]), SoldDateTable[Date] >= … && SoldDateTable[Date] <= …)`, in the same `CALCULATETABLE(ROW(…), …)` shape as C1.

**C3.** No measure; inline (the KPI page's own logic):

```dax
EVALUATE
VAR _a0 = {A} - 119
VAR _a1 = {A} + 1
VAR _sold = CALCULATE(COUNTROWS(Salesforce_Data),
    Salesforce_Data[Stage] IN {"Ready to Install", "Ready to Order", "Complete", "Incomplete"},
    Salesforce_Data[Original Project Sold Date] >= _a0 && Salesforce_Data[Original Project Sold Date] < _a1,
    REMOVEFILTERS(DateTable), REMOVEFILTERS(SoldDateTable))
VAR _created = CALCULATE(COUNTROWS(Salesforce_Data),
    Salesforce_Data[Stage] <> "Dead Lead",
    Salesforce_Data[Created Date] >= _a0 && Salesforce_Data[Created Date] < _a1,
    REMOVEFILTERS(DateTable), REMOVEFILTERS(SoldDateTable))
RETURN ROW("120-day closing rate", DIVIDE(_sold, _created), "Sold", _sold, "Created excl. Dead Lead", _created)
```

**C4.** This week / this month: the weekly report (section 9), `metric_key = "closing_rate"`, windows `current_week` / `current_mtd`. Any other window, inline on created date:

```dax
EVALUATE
VAR _sold = CALCULATE([Count Sold], Salesforce_Data[Created Date] >= DATE({Y}, 1, 1) && Salesforce_Data[Created Date] <= {A})
VAR _decided = CALCULATE(COUNTROWS(Salesforce_Data),
    Salesforce_Data[Revised Stage] IN {"Sold", "Lost", "Dead Lead"},
    Salesforce_Data[Created Date] >= DATE({Y}, 1, 1) && Salesforce_Data[Created Date] <= {A})
RETURN ROW("Closing rate (count-based)", DIVIDE(_sold, _decided), "Sold", _sold, "Decided", _decided)
```

- `DIVIDE` returns BLANK when the denominator is 0: report "no projects created in the window", not 0%.
- Not closing rates: `[Sold Percentage]` (revenue share), `[Lead Conversion Rate]`, `[Project Success Rate]`.

## 6. Designers

- Sales rankings group by `Salesforce_Data[Designer]` (every name, active or not). Leave unassigned rows out with `NOT ISBLANK(Salesforce_Data[Designer])`:

```dax
EVALUATE
TOPN(5,
    FILTER(
        SUMMARIZECOLUMNS(Salesforce_Data[Designer],
            "Sold revenue", CALCULATE([Revenue Sold], YEAR({SALES_DATE}) = {Y}),
            "Sold projects", CALCULATE([Count Sold], YEAR({SALES_DATE}) = {Y})),
        NOT ISBLANK(Salesforce_Data[Designer])),
    [Sold revenue], DESC)
ORDER BY [Sold revenue] DESC
```

- Active roster = `dim_designers[Designer]` (the table holds active designers only; `dim_designers[active_flag]` is always 1). Grouping by it puts inactive designers' projects under a blank `Designer`: drop it with `NOT ISBLANK(dim_designers[Designer])`.
- "List the active designers": `EVALUATE SELECTCOLUMNS(FILTER(dim_designers, dim_designers[Total Projects] > 0), "Designer", dim_designers[Designer]) ORDER BY [Designer]`.
- Designers active in a period: `EVALUATE ROW("Active designers", CALCULATE(DISTINCTCOUNTNOBLANK(Salesforce_Data[Designer]), YEAR(Salesforce_Data[Created Date]) = {Y}))`.
- Top performing (no metric): `TOPN(1, SUMMARIZECOLUMNS(Salesforce_Data[Designer], "Sold", [Revenue Sold]), [Sold], DESC)`, no date filter.
- Staff: `Appointments[Assigned Staff Member]`, e.g. `CALCULATE(DISTINCTCOUNTNOBLANK(Appointments[Assigned Staff Member]), Appointments[Project Stage] = "Sold")`. The Appointments page's designer slicer reaches appointments through `Appointments[designer_sk]`.

## 7. Leads and lead journey

| Business term | DAX |
|---|---|
| leads | `[Total Lead Count]` = `COUNTROWS(Leads)` |
| converted leads | `[Converted Lead Count]` = `CALCULATE(COUNTROWS(Leads), Leads[Lead Status] = "Moved to Studio")` |
| lead conversion rate | `[Lead Conversion Rate]` = `DIVIDE([Converted Lead Count], [Total Lead Count], 0)` |
| lost leads | `[Lost Lead Count]` = `CALCULATE(COUNTROWS(Leads), Leads[Lead Status] IN {"Junk", "Duplicate", "Cancelled"})` |
| junk only / cancelled only | `CALCULATE(COUNTROWS(Leads), Leads[Lead Status] = "Junk")` / `= "Cancelled"` |
| junk and duplicate | `[Junk & Duplicate Count]`, `[Junk & Duplicate %]` |
| no response | `[No Response Lead Count]` = `CALCULATE(COUNTROWS(Leads), CONTAINSSTRING(Leads[Lead Status], "No Response"))` |
| in nurturing now | `[Leads in Nurturing Stage]` = `CALCULATE(COUNTROWS(Leads), Leads[Lead Status] = "Lead Nurturing")` |
| ever in a status | `CALCULATE(COUNTROWS(Leads_Journey), CONTAINSSTRING(Leads_Journey[Lead Journey], "Nurturing"))` |
| form type | `[Brochure Count]`, `[Consultation Count]`, `[Manual Count]` |
| channel | `Leads[Lead Source Channel]`; referral: `Leads[Lead Source Channel] IN {"Referral", "Referral - Company"}` |

- Period: `Leads[Lead Created Date]`.
- Journey measures (on `'Lead Milestones'`, which `Leads` filters through the both-ways 1:1 link):
  - `[JN Leads]` = `CALCULATE(COUNTROWS('Lead Milestones'), 'Lead Milestones'[Exclude From Journey] = 0)`
  - `[JN Booked]`, `[JN Studio]`, `[JN Sold]` = `[JN Leads]` restricted to `'Lead Milestones'[Booked Appointment] = 1` / `[Reached Studio] = 1` / `[Journey Outcome] = "Sold"` (each `+ 0`)
  - `[JN Booked %]`, `[JN Studio %]`, `[JN Win %]` = each ÷ `[JN Leads]`; `[JN Sold Value]` = `SUM('Lead Milestones'[Sold Amount])` over non-excluded leads; `[JN Sold per Lead]` = `[JN Sold Value]` ÷ `[JN Leads]`
  - `[JN Days to Booked]`, `[JN Days to Studio]`, `[JN Days to Sold]` = `MEDIANX` over non-excluded leads with the day value `>= 0` (Days to Sold also needs `[Journey Outcome] = "Sold"`)

```dax
EVALUATE
CALCULATETABLE(
    ROW("Leads", [JN Leads], "Booked", [JN Booked], "Booked %", [JN Booked %],
        "Reached studio", [JN Studio], "Studio %", [JN Studio %], "Sold", [JN Sold], "Win %", [JN Win %],
        "Sold value", [JN Sold Value], "Typical days to book", [JN Days to Booked],
        "Typical days to studio", [JN Days to Studio], "Typical days to sale", [JN Days to Sold]),
    YEAR(Leads[Lead Created Date]) = {Y})
```

  By channel: `SUMMARIZECOLUMNS(Leads[Lead Source Channel], …)` inside the same `CALCULATETABLE`. First contact: group by `'Lead Milestones'[Contact Path]`; attempts: `'Lead Milestones'[Contact Attempts]`.

## 8. Appointments

| Business term | DAX |
|---|---|
| appointments | `[Total Activity Count]` = `COUNTROWS(Appointments)` |
| appointments on sold projects | `[Sold Project Count]` = `CALCULATE(COUNTROWS(Appointments), Appointments[Project Stage] = "Sold")` |
| on lost projects | `[Lost Project Count]` (`"Lost"`) |
| on proposed projects | `CALCULATE(COUNTROWS(Appointments), Appointments[Project Stage] = "Proposed")` (not `[Proposed Project Count]`, below) |
| success rate | `[Project Success Rate]` = `DIVIDE([Sold Project Count], [Total Activity Count], 0)` |
| design / installation appointments | `[Design Appointment Count]`, `[Installation Appointment Count]` |
| first appointments (flag) | `CALCULATE(COUNTROWS(Appointments), Appointments[Is First Appointment] = 1)` |

Franchise rule, added as filter arguments to every appointment `CALCULATE` / `CALCULATETABLE`:

| Franchise | Filter arguments |
|---|---|
| ICLA | `NOT Appointments[Activity Type] IN {"Installation", "Unspecified"}, CONTAINSSTRING(Appointments[Appointment Subject], "1st"), NOT CONTAINSSTRING(Appointments[Appointment Subject], "Cancelled"), NOT Appointments[Assigned Staff Member] IN {ICLA_EXCLUDED_STAFF_DAX}, Appointments[Appointment Start Time] >= DATE(2025, 6, 1)` |
| ICCH | `NOT CONTAINSSTRING(Appointments[Appointment Subject], "available")` |
| ICAU | none |

`CONTAINSSTRING` on a blank subject is FALSE, so the `NOT` tests keep blank subjects, as the report does.

- Period on `Appointments[Appointment Start Time]`:

```dax
// ICCH
EVALUATE
ROW("Appointments",
    CALCULATE([Total Activity Count],
        YEAR(Appointments[Appointment Start Time]) = {Y}, MONTH(Appointments[Appointment Start Time]) = {M},
        NOT CONTAINSSTRING(Appointments[Appointment Subject], "available")))
```

```dax
// ICLA
EVALUATE
ROW("First design appointments",
    CALCULATE([Total Activity Count],
        YEAR(Appointments[Appointment Start Time]) = {Y},
        NOT Appointments[Activity Type] IN {"Installation", "Unspecified"},
        CONTAINSSTRING(Appointments[Appointment Subject], "1st"),
        NOT CONTAINSSTRING(Appointments[Appointment Subject], "Cancelled"),
        NOT Appointments[Assigned Staff Member] IN {ICLA_EXCLUDED_STAFF_DAX},
        Appointments[Appointment Start Time] >= DATE(2025, 6, 1)))
```

- Success rate by source (blank `Lead Source Channel` is a group of its own: label it "No source recorded"):

```dax
EVALUATE
TOPN(10,
    FILTER(
        CALCULATETABLE(
            SUMMARIZECOLUMNS(Appointments[Lead Source Channel],
                "Appointments", [Total Activity Count], "On sold projects", [Sold Project Count],
                "Success rate", [Project Success Rate]),
            YEAR(Appointments[Appointment Start Time]) = {Y}
            /* , franchise rule arguments */),
        [Appointments] >= 30),
    [Success rate], DESC)
```

- **No relationship between Appointments and Salesforce_Data.** A `Salesforce_Data` filter in a query over `Appointments` (or the reverse) does nothing. Move project sets across by name with `TREATAS`, which counts each appointment once:

```dax
EVALUATE
ROW("Appointments on sold projects",
    CALCULATE([Total Activity Count],
        TREATAS(CALCULATETABLE(VALUES(Salesforce_Data[Project Name]), Salesforce_Data[Revised Stage] = "Sold"),
            Appointments[Project Name]),
        YEAR(Appointments[Appointment Start Time]) = {Y}))
```

- Last activity, one row per project (all appointment entries, no franchise rule):

```dax
EVALUATE
TOPN(50,
    CALCULATETABLE(
        ADDCOLUMNS(
            SUMMARIZE(Salesforce_Data, Salesforce_Data[Project ID], Salesforce_Data[Project Name], Salesforce_Data[Amount]),
            "Last activity",
                VAR p = Salesforce_Data[Project Name]
                RETURN CALCULATE(MAX(Appointments[Activity Date and Time]), Appointments[Project Name] = p)),
        CONTAINSSTRING(Salesforce_Data[Designer], "<designer>"),
        Salesforce_Data[Revised Stage] = "Proposed"),
    [Last activity], DESC)
ORDER BY [Last activity] DESC
```

  A bare `CALCULATE(MAX(Appointments[Activity Date and Time]))` returns the newest appointment of all projects.
- Broken or misleading measures: `[Proposed Project Count]` filters `Project Stage = "Propose"`, which never occurs (always blank); `[Staff Success Rate]` filters "Complete", which never occurs (always 0); `[Opportunities to Sold Count]`, `[Opportunities to Propose Count]`, `[Opportunities to Lost Count]`, `[Opportunities to Appointments Count]` and the matching `%` measures count appointment-grain bridge rows, never projects (`[Opportunities to Propose …]` also looks for "Propose"); `[Real Appointment Count]` / `%` exist only in the Seattle and Tacoma models.

## 9. Weekly report tables

| Table | Columns |
|---|---|
| `report_scorecard` | `report_date`, `metric_key` ∈ {sold_dollars, sold_count, apv_sold, closing_rate, open_pipeline_dollars, new_leads, lead_conversion, dead_leads_with_amount, real_appt_pct}, `metric_label`, `metric_kind` (money / count / percent), `window_name` ∈ {current_week, prior_week, current_mtd, prior_mtd, current_ytd, prior_ytd}, `value` |
| `report_designer_leaderboard` | `designer_name`, `sold_dollars_mtd`, `sold_count_mtd`, `decided_count_mtd`, `total_opps_mtd`, `closing_rate_mtd`, `sold_dollars_ytd`, `rank_by_sold` |
| `report_lead_source_roi` | `source_group`, `sold_dollars_mtd`, `opp_count_mtd`, `closing_rate_mtd`, `rank_by_sold` |
| `report_channel_roi` | `channel_name`, `new_leads_mtd`, `sold_opps_mtd`, `sold_dollars_mtd`, `decided_n`, `closing_rate`, `rank_by_sold` |
| `report_designer_concentration` | `top1_name`, `top1_dollars_ytd`, `top1_share`, `top2_name`, `top2_dollars_ytd`, `top2_combined_share`, `total_ytd`, `active_designers_ytd`, `warn_flag` |
| `report_appointment_funnel` | `activity_type`, `count_mtd`, `count_prior_mtd`, `mom_delta`, `share_of_mtd`, `tied_to_sold_pct`, `avg_cycle_days_mtd_sold`, `hygiene_flag`, `hygiene_pct`, `total_mtd_all`, `rank_by_count` |

Every table keeps all daily snapshots: filter to the latest `report_date`. Rates and shares are fractions (0–1).

```dax
EVALUATE
VAR _d = MAXX(ALL(report_scorecard[report_date]), report_scorecard[report_date])
RETURN
    SELECTCOLUMNS(
        FILTER(report_scorecard,
            report_scorecard[report_date] = _d
                && report_scorecard[metric_key] IN {"sold_dollars", "sold_count"}
                && report_scorecard[window_name] IN {"current_mtd", "prior_mtd"}),
        "Metric", report_scorecard[metric_label], "Window", report_scorecard[window_name],
        "Value", report_scorecard[value], "As of", report_scorecard[report_date])
```

```dax
EVALUATE
VAR _d = MAXX(ALL(report_designer_leaderboard[report_date]), report_designer_leaderboard[report_date])
RETURN
    TOPN(5,
        SELECTCOLUMNS(FILTER(report_designer_leaderboard, report_designer_leaderboard[report_date] = _d),
            "Designer", report_designer_leaderboard[designer_name],
            "Sold this month", report_designer_leaderboard[sold_dollars_mtd],
            "Sold projects", report_designer_leaderboard[sold_count_mtd],
            "Closing rate (count-based)", report_designer_leaderboard[closing_rate_mtd]),
        [Sold this month], DESC)
ORDER BY [Sold this month] DESC
```

## 10. "Why" questions and forecasts

- Contributors to a change between two windows (A and B), on the sales basis:

```dax
EVALUATE
ADDCOLUMNS(
    SUMMARIZECOLUMNS(Salesforce_Data[Designer],
        "Period A", CALCULATE([Revenue Sold], {SALES_DATE} >= DATE(…) && {SALES_DATE} <= DATE(…)),
        "Period B", CALCULATE([Revenue Sold], {SALES_DATE} >= DATE(…) && {SALES_DATE} <= DATE(…))),
    "Change", [Period B] - [Period A])
ORDER BY [Change] DESC
```

  Swap `Salesforce_Data[Designer]` for `[Lead Source]` or `[Lead Source Group]`.
- Volume vs value: one `ROW` with `[Count Sold]`, `[Average Project Value Sold]` and `[Revenue Sold]` for each window.
- Funnel for two windows: `[Total Lead Count]` (lead created), `[Total Activity Count]` with the franchise rule (appointment start), `[Count Sold]` (sales basis).
- Weeks inside one window: group by `Salesforce_Data[Sold Start of Week]` (sold) or `Salesforce_Data[Start of Week]` (created).
- History for a forecast question: the monthly trend pattern of section 4 over the last 12–24 months, plus `[Revenue Proposed]` / `[Count Proposed]` for the open pipeline.

## 11. Result mechanics

- Return raw numbers; don't wrap them in `FORMAT` (text columns lose the server's totals). Rates come back as fractions: present them as X.X%.
- Give every output column a readable alias; the alias is what the owner sees in the table and the Excel file.
- Sort with `ORDER BY` at the very end of the query.

## 12. DAX traps

1. `[Closing Rate]` and its two Period measures read their window from `DateTable[Date]` only (section 5).
2. The calendar you filter decides the sales basis: `DateTable` = created, `SoldDateTable` = sold (section 4).
3. `Appointments`, `Salesforce_Data` and `Leads` do not filter each other; a `SUMMARIZECOLUMNS` filter on an unrelated table is a silent no-op. Use `TREATAS` or a per-row lookup (section 8).
4. `SUMMARIZECOLUMNS` drops groups whose measures are all blank, and keeps a blank group for unmatched keys (inactive designers, pre-2020 dates, blank channels). Filter the blank group out or label it.
5. `DISTINCTCOUNT` counts a blank as one value: use `DISTINCTCOUNTNOBLANK`.
6. Appointment-grain `[Opportunities to … Count]` measures, `[Proposed Project Count]` and `[Staff Success Rate]` give wrong numbers (section 8).
7. `TODAY()` and the time-intelligence measures ignore the anchor date (section 2).
8. Syntax that Power BI rejects with HTTP 400: a bare scalar after `EVALUATE` (wrap it in `ROW`); closing `ROW(` early and listing more pairs after it; `ORDER BY` inside `CALCULATETABLE` or `SUMMARIZECOLUMNS`; SQL-style `WHERE`; auto-date suffixes such as `.[MonthNo]`; table names with spaces not in single quotes (`'Lead Milestones'`, `'Opportunities to Appointments'`, `'LS Group Sort'`).
9. More than 50 rows are cut off (flagged as truncated): aggregate, or use `TOPN`.
