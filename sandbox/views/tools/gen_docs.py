"""Generate daxvssql/sandbox/views/SCHEMA.md and PARITY_NOTES.md."""
import json
import re
from pathlib import Path

from sbq import q

HERE = Path(__file__).parent
OUT = Path(r"C:\Project Data Centralization\daxvssql\sandbox\views")
TM = json.loads((HERE / "tmdl.json").read_text(encoding="utf-8"))
COUNTS = json.loads((HERE / "rowcounts.json").read_text(encoding="utf-8"))
VERSION = json.loads(Path(r"C:\Project Data Centralization\daxvssql\.sandbox\data_version.json").read_text(encoding="utf-8"))


def coltypes(schema, view):
    _, rows = q("SELECT COLUMN_NAME, COLUMN_TYPE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s "
                "ORDER BY ORDINAL_POSITION", (schema, view.lower()))
    return rows


def clean_desc(d):
    if not d:
        return ""
    d = re.split(r"\s*Synonyms:", d)[0].strip()
    return d.replace("\u2014", "-").replace("|", "/")


# ----------------------------------------------------------------- per-view text
PURPOSE = {
    "Salesforce_Data": "One row per opportunity (Salesforce project): stage, amount, created and sold dates, designer, lead source. `Project ID` is unique; `Project Name` is NOT.",
    "Leads": "One row per lead (`Lead ID` unique): source channel, status, contact details, conversion to a project.",
    "Appointments": "One row per calendar event or CRM task (`appointment_key` unique). Includes tasks (`Activity Type` = 'Unspecified') and internal or availability blocks.",
    "Leads_Journey": "One row per lead (`Lead ID` unique) summarising its status-change history.",
    "Opportunities to Appointments": "Funnel bridge: one row per converted lead (`Lead ID` unique) with the project it became and its first appointment.",
    "DateTable": "Calendar, one row per day from 2020-01-01 to 2030-12-31. Created-date role (and lead, appointment dates).",
    "SoldDateTable": "Identical copy of DateTable (DAX calculated table `= DateTable`), used for the sold-date role.",
    "dim_designers": "Designer roster: ACTIVE designers of this franchise only (`active_flag` = 1). `Designer` unique.",
    "dim_projects": "Distinct project names of this franchise; the hub that Salesforce_Data, Leads, Appointments, Opportunities to Appointments (and Projects Part List) relate to.",
    "Lead Source Channels": "Distinct lead-source names (shared lookup, all franchises).",
    "Source_Sort": "Lead source with its display sort order (shared lookup).",
    "LS Group Sort": "Lead-source group (3 groups) with its display sort order (shared lookup).",
    "dim_entity": "Franchise list: code, display name, region. Lists ALL franchises, like the model; holds no franchise data.",
    "Lead Milestones": "One row per lead: contact attempts, booked/studio/sold milestones and days to each, from the lead status history and the lead's project. Feeds the Opportunity Journey page.",
    "report_scorecard": "Weekly-report snapshot, long shape: one row per `metric_key` x `window_name` x `report_date`.",
    "report_designer_leaderboard": "Weekly-report snapshot: one row per designer per `report_date`.",
    "report_lead_source_roi": "Weekly-report snapshot: one row per lead-source group per `report_date`.",
    "report_designer_concentration": "Weekly-report snapshot: one row per `report_date` (top-1/top-2 designer revenue concentration).",
    "report_channel_roi": "Weekly-report snapshot: one row per channel per `report_date`.",
    "report_appointment_funnel": "Weekly-report snapshot: one row per activity type per `report_date`.",
    "Projects Part List": "ICLA only. Closet-parts bill of materials: one row per part line per project (each project's current list, not history).",
}

SOURCE = {
    "Salesforce_Data": "`icall_gold.fact_opportunities` (= fact_sales), 24 selected columns (ICAU 25) + 4 calculated columns",
    "Leads": "`icall_gold.fact_leads`, all columns, `Lead Created Date` cast to date",
    "Appointments": "`icall_gold.fact_appointments`, all columns, 3 date casts",
    "Leads_Journey": "`icall_gold.fact_lead_journey`, all columns",
    "Opportunities to Appointments": "`icall_gold.fact_opportunities_to_appointments`, all columns, 2 date casts",
    "DateTable": "`icall_gold.dim_date`, all rows",
    "SoldDateTable": "view over DateTable",
    "dim_designers": "`icall_gold.dim_designers`, `active_flag` = 1, two renames",
    "dim_projects": "`icall_gold.dim_projects`, distinct `Project Name`",
    "Lead Source Channels": "`icall_gold.dim_lead_sources`, distinct name",
    "Source_Sort": "`icall_gold.dim_lead_sources`, renamed columns",
    "LS Group Sort": "`icall_gold.dim_lead_sources`, one row per group",
    "dim_entity": "`icall_gold.dim_entity`, all rows, Title Case renames",
    "Lead Milestones": "the model's native SQL query over fact_leads, silver_lead_status_history and fact_sales",
    "Projects Part List": "`icall_gold.icla_project_parts_list`",
}

NOTE = {  # factual additions (verified against the data or the Gold procs); TMDL text comes first
    ("Salesforce_Data", "Stage"): "Raw Salesforce stage: Complete, Ready to Install, Ready to Order, Incomplete, Design, Propose, Lost, Dead Lead.",
    ("Salesforce_Data", "Revised Stage"): "Exactly 4 values: 'Sold' (Stage Complete / Ready to Install / Ready to Order / Incomplete), 'Proposed' (Design / Propose), 'Lost', 'Dead Lead'.",
    ("Salesforce_Data", "Original Project Sold Date"): "Sold date, midnight datetime. NULL when never sold (and for many older Sold rows).",
    ("Salesforce_Data", "Created Date"): "Opportunity created date, midnight datetime.",
    ("Salesforce_Data", "Amount"): "Project value in dollars (all stages).",
    ("Salesforce_Data", "Start of Week"): "Sunday on or before `Created Date`.",
    ("Salesforce_Data", "Created Month"): "'Jan'..'Dec' of `Created Date` (no year).",
    ("Salesforce_Data", "Sold Year"): "Calculated column: YEAR(sold date), NULL when no sold date.",
    ("Salesforce_Data", "Sold Month Number"): "Calculated column: MONTH(sold date), NULL when no sold date.",
    ("Salesforce_Data", "Sold Month"): "Calculated column: 'Jan'..'Dec' of the sold date, NULL when no sold date.",
    ("Salesforce_Data", "Sold Start of Week"): "Calculated column: Sunday on or before the sold date, NULL when no sold date.",
    ("Salesforce_Data", "Designer"): "Designer name as text (includes inactive and x-prefixed former designers).",
    ("Salesforce_Data", "Sale Cycle"): "Days from `Created Date` to sold date.",
    ("Salesforce_Data", "Sale to Complete Days"): "Days from sold date to `Final Completion Date`.",
    ("Salesforce_Data", "Lead Time"): "Days from sold date to the target install date.",
    ("Salesforce_Data", "Additional Lead Source"): "ICAU only.",
    ("Salesforce_Data", "Target Ready to Order Date"): "Empty for ICAU.",
    ("Salesforce_Data", "Owner First Appointments Last 12 Months"): "Empty for ICAU.",
    ("Leads", "Index"): "Reserved word: always write `Index`.",
    ("Leads", "Lead Created Date"): "DATE (the model casts it to date).",
    ("Leads", "Is Referral Lead"): "1/0.", ("Leads", "Is Converted Lead"): "1/0.", ("Leads", "Has Showroom Visit"): "1/0.",
    ("Leads", "is_likely_duplicate"): "1/0 Gold duplicate flag.",
    ("Leads", "Lead Age in Days"): "Computed when Gold was last built, not live.",
    ("Leads", "Lead Creation Year-Month"): "'YYYY-MM'.",
    ("Leads", "Converted Project Name"): "Joins `dim_projects`.`Project Name` / `Salesforce_Data`.`Project Name`.",
    ("Appointments", "Activity Date and Time"): "DATE only: the model casts it to date (time of day dropped). Not related to DateTable.",
    ("Appointments", "Appointment Start Time"): "DATE only: the model casts it to date (time of day dropped). Related to DateTable.",
    ("Appointments", "Start Of WeekA"): "DATE. Sunday on or before `Activity Date and Time` (not the start time). The trailing A is part of the name.",
    ("Appointments", "Activity Type"): "Values in the data: Design, Installation, Unspecified (Unspecified = CRM tasks such as calls and emails).",
    ("Appointments", "Is First Appointment"): "1/0.",
    ("Appointments", "Is Future Appointment"): "Text 'TRUE'/'FALSE', computed when Gold was last built, not live.",
    ("Appointments", "Is Real Appointment"): "Text 'TRUE'/'FALSE' (not boolean).",
    ("Appointments", "Project Stage"): "Unspecified, Sold, Lost, Design, Proposed, Cancelled, Incomplete ('Complete' never occurs).",
    ("Appointments", "Project Name"): "NULL for tasks and internal events. Joins `dim_projects`. Repeats across appointments.",
    ("Appointments", "designer_sk"): "Joins `dim_designers`.`designer_sk`.",
    ("Appointments", "Activity Year-Month"): "'YYYY-MM'.",
    ("Opportunities to Appointments", "Index"): "Reserved word: always write `Index`.",
    ("Opportunities to Appointments", "Created Date"): "DATE (cast by the model). Lead created date.",
    ("Opportunities to Appointments", "Activity Date and Time"): "DATE (cast by the model). First appointment date. Relationship to DateTable is inactive.",
    ("Opportunities to Appointments", "Project Stage"): "Cancelled, Design, Lost, Proposed, Sold.",
    ("Opportunities to Appointments", "Converted Project: Project Name"): "Joins `dim_projects`.`Project Name`.",
    ("DateTable", "Date"): "DATE, unique.",
    ("DateTable", "Month"): "'Jan'..'Dec'.", ("DateTable", "Quarter"): "'Q1'..'Q4'.",
    ("DateTable", "Year-Month"): "'YYYY-MM'.", ("DateTable", "MonthYear"): "'Nov 2026' style.",
    ("DateTable", "Weekday"): "'Monday'..'Sunday'.", ("DateTable", "WeekdayNumber"): "Monday = 1 .. Sunday = 7.",
    ("DateTable", "YearQuarter"): "'2026 Q4' style.", ("DateTable", "Start Of Week"): "Sunday on or before `Date`.",
    ("dim_designers", "Designer"): "Unique. Active designers only.",
    ("dim_designers", "Sort"): "Display order of designers.",
    ("dim_designers", "active_flag"): "Always 1 here.",
    ("dim_entity", "entity"): "Franchise code, e.g. ICLA.",
    ("Lead Milestones", "Lead ID"): "Unique. Joins `Leads`.`Lead ID`.",
    ("Lead Milestones", "Contact Attempts"): "Number of distinct status values containing 'Attempt' in the lead's status history (0 when none).",
    ("Lead Milestones", "Studio Step"): "'Reached the studio' or 'No studio visit'.",
    ("Leads_Journey", "Lead ID"): "Unique. Joins `Leads`.`Lead ID`.",
    ("Lead Milestones", "Contact Path"): "'Booked first try', 'Booked after follow-up', 'Not booked', 'No contact history' (leads created before 2025-01-01).",
    ("Lead Milestones", "Reached Studio"): "1/0.",
    ("Lead Milestones", "Journey Outcome"): "'Sold', 'Proposed', 'Lost', 'Dead Lead' (from the lead's project), else 'Nurturing', 'Closed', 'Still working' (from Lead Status).",
    ("Lead Milestones", "Sold Amount"): "Amount of the lead's sold project, else NULL.",
    ("Lead Milestones", "Days to Scheduled"): "Days from lead created to the first 'Scheduled' status, 2 decimals.",
    ("Lead Milestones", "Days to Studio"): "Days from lead created to 'Moved to Studio' (or to the project's created date), 2 decimals.",
    ("Lead Milestones", "Days to Sold"): "Days from lead created to the project's sold date (NULL unless the project is sold).",
    ("Lead Milestones", "Exclude From Journey"): "1 when Lead Status is Duplicate or Junk.",
    ("Lead Milestones", "Has Status History"): "1 when the lead was created on or after 2025-01-01.",
    ("Lead Milestones", "Booked Appointment"): "1/0.",
    ("report_scorecard", "metric_key"): "apv_sold, closing_rate, dead_leads_with_amount, lead_conversion, new_leads, open_pipeline_dollars, real_appt_pct, sold_count, sold_dollars.",
    ("report_scorecard", "metric_kind"): "count, money or percent.",
    ("report_scorecard", "window_name"): "current_week, prior_week, current_mtd, prior_mtd, current_ytd, prior_ytd.",
    ("report_appointment_funnel", "hygiene_flag"): "0/1 (boolean in the model).",
    ("report_designer_concentration", "warn_flag"): "0/1 (boolean in the model).",
    ("Projects Part List", "project_name"): "Joins `dim_projects`.`Project Name`.",
}

SORTBY = {}
HIDDEN = {}
for ent in ["ICLA", "ICAU"]:
    for t, tbl in TM[ent].items():
        for c in tbl["columns"]:
            if c.get("sortByColumn"):
                SORTBY[(t, c["name"])] = c["sortByColumn"].strip("'")
            if c.get("isHidden"):
                HIDDEN[(t, c["name"])] = True

ORDER = ["Salesforce_Data", "Leads", "Appointments", "Leads_Journey", "Opportunities to Appointments",
         "Lead Milestones", "dim_projects", "dim_designers", "DateTable", "SoldDateTable",
         "Lead Source Channels", "Source_Sort", "LS Group Sort", "dim_entity",
         "report_scorecard", "report_designer_leaderboard", "report_lead_source_roi",
         "report_designer_concentration", "report_channel_roi", "report_appointment_funnel", "Projects Part List"]


def view_section(t):
    ent = "ICLA"
    rows = coltypes("poc_icla", t)
    if t == "Salesforce_Data":
        rows = coltypes("poc_icau", t)   # superset: ICAU adds Additional Lead Source
        ent = "ICAU"
    tdesc = clean_desc(TM["ICLA"].get(t, TM["ICAU"].get(t))["table_desc"]) if t in TM["ICLA"] else ""
    descs = {c["name"]: clean_desc(c.get("desc")) for c in TM[ent][t]["columns"]}
    if t == "Salesforce_Data":
        descs = {**{c["name"]: clean_desc(c.get("desc")) for c in TM["ICLA"][t]["columns"]}, **{k: v for k, v in descs.items() if v}}
    out = [f"### `{t}`", "", PURPOSE[t]]
    if t in SOURCE:
        out[-1] += f" Source: {SOURCE[t]}."
    if t.startswith("report_"):
        out[-1] += f" Source: `icall_gold.{t}` (weekly snapshot)."
    out += ["", "| Column | Type | Notes |", "|---|---|---|"]
    for name, typ in rows:
        bits = []
        d = descs.get(name)
        if d:
            bits.append(d.rstrip(".") + ".")
        n = NOTE.get((t, name)) or (NOTE.get(("DateTable", name)) if t == "SoldDateTable" else None)
        if n:
            bits.append(n)
        sb = SORTBY.get((t, name)) or (SORTBY.get(("DateTable", name)) if t == "SoldDateTable" else None)
        if sb:
            bits.append(f"Sort by `{sb}`.")
        if HIDDEN.get((t, name)):
            bits.append("(hidden in the model)")
        out.append(f"| `{name}` | {typ} | {' '.join(bits)} |")
    return "\n".join(out) + "\n"


REL = [
    # from, to, cardinality, active, direction, scope
    ("Salesforce_Data.`Created Date`", "DateTable.`Date`", "many-to-one", "active", "single", ""),
    ("Salesforce_Data.`Original Project Sold Date`", "SoldDateTable.`Date`", "many-to-one", "active", "single", ""),
    ("Salesforce_Data.`Original Project Sold Date`", "DateTable.`Date`", "many-to-one", "**inactive**", "single", ""),
    ("Salesforce_Data.`Project Name`", "dim_projects.`Project Name`", "many-to-one", "active", "single", ""),
    ("Salesforce_Data.`Designer`", "dim_designers.`Designer`", "many-to-one", "active", "single", ""),
    ("Salesforce_Data.`Lead Source`", "Source_Sort.`Lead Source`", "many-to-one", "active", "single", ""),
    ("Salesforce_Data.`Lead Source Group`", "`LS Group Sort`.`Lead Source Group`", "many-to-one", "active", "single", ""),
    ("Salesforce_Data.`entity`", "dim_entity.`entity`", "many-to-one", "active", "single", ""),
    ("Leads.`Lead Created Date`", "DateTable.`Date`", "many-to-one", "active", "single", ""),
    ("Leads.`Converted Project Name`", "dim_projects.`Project Name`", "many-to-one", "active", "single", ""),
    ("Leads.`Lead Source Channel`", "`Lead Source Channels`.`Inspired Closets Lead Source`", "many-to-one", "active", "single", ""),
    ("Leads.`Lead ID`", "Leads_Journey.`Lead ID`", "many-to-one (1:1 in data)", "active", "single", ""),
    ("Leads.`entity`", "dim_entity.`entity`", "many-to-one", "active", "single", ""),
    ("`Lead Milestones`.`Lead ID`", "Leads.`Lead ID`", "many-to-one (1:1 in data)", "active", "**both**", ""),
    ("Appointments.`Appointment Start Time`", "DateTable.`Date`", "many-to-one", "active", "single", ""),
    ("Appointments.`Project Name`", "dim_projects.`Project Name`", "many-to-one", "active", "single", ""),
    ("Appointments.`designer_sk`", "dim_designers.`designer_sk`", "many-to-one", "active", "single", ""),
    ("Appointments.`Lead Source Channel`", "`Lead Source Channels`.`Inspired Closets Lead Source`", "many-to-one", "active", "single", ""),
    ("Appointments.`entity`", "dim_entity.`entity`", "many-to-one", "active", "single", ""),
    ("`Opportunities to Appointments`.`Converted Project: Project Name`", "dim_projects.`Project Name`", "many-to-one", "active", "**both**", ""),
    ("`Opportunities to Appointments`.`Activity Date and Time`", "DateTable.`Date`", "many-to-one", "**inactive**", "single", ""),
    ("Source_Sort.`Lead Source`", "`Lead Source Channels`.`Inspired Closets Lead Source`", "**one-to-one**", "active", "**both**", ""),
    ("Leads_Journey.`entity_sk`", "dim_entity.`entity_sk`", "many-to-one", "**inactive**", "single", ""),
    ("`Projects Part List`.`project_name`", "dim_projects.`Project Name`", "many-to-one", "active", "single", "ICLA only"),
    ("`Projects Part List`.`entity`", "dim_entity.`entity`", "many-to-one", "active", "single", "ICLA only"),
]


def schema_md():
    h = f"""# Route B schema: mirrored Power BI tables

Each franchise has its own MySQL schema (`poc_icla`, `poc_icch`, `poc_icau`) holding one view per table of that franchise's Power BI model, with the same table names, column names, column order and rows. The views are defined in `10_mirror.sql` plus the franchise-only files. The franchise is fixed by the schema: every row of every view belongs to the connected franchise, so no entity filter is needed (the only exceptions are the shared lookups `DateTable`, `SoldDateTable`, `Lead Source Channels`, `Source_Sort`, `LS Group Sort` and the franchise list `dim_entity`). There are **no measures**: every metric has to be written as SQL. Column notes repeat the model's descriptions (without its Q&A synonym lists), followed by facts checked against the data. Gaps against the model are listed in `PARITY_NOTES.md`.

**Conventions**
- Quote every name that has a space, punctuation or a reserved word with backticks: `` `Revised Stage` ``, `` `Lead Milestones` ``, `` `Index` ``, `` `Year-Month` ``, `` `Converted Project: Project Name` ``, `` `Inspired Closets Lead Source.1` ``. Without ANSI_QUOTES, `"Revised Stage"` is a string literal, so `"Revised Stage" = 'Sold'` silently matches nothing.
- View names are case-insensitive (the server stores them in lower case); column names are as listed.
- DATE columns hold dates only. The DATETIME date columns of `Salesforce_Data` and `Leads`.`Lead Conversion Date`, `Opportunities to Appointments`.`Converted Date` and `Leads_Journey`.`First Status Date` hold midnight values. `Leads`.`Lead Creation Timestamp`, `Leads_Journey`.`Latest Status Date` / `Last Updated` / `Latest File Date` and the audit columns (`Data Ingested At`, `created_at`, `source_created_at`, `data_ingested_at`) carry a time of day. Filter any DATETIME column with a half-open range (`>= '2026-01-01' AND < '2026-02-01'`), never `<= '2026-01-31'`.
- NULL is Power BI's blank. Text comparisons are case-insensitive, as in Power BI.
- Rows have no inherent order: always `ORDER BY` (see each column's "Sort by" note for the model's display order).

| Views | Shared by ICLA, ICCH, ICAU |
|---|---|
| Facts | `Salesforce_Data`, `Leads`, `Appointments`, `Leads_Journey`, `Opportunities to Appointments`, `Lead Milestones` |
| Dimensions and lookups | `dim_projects`, `dim_designers`, `DateTable`, `SoldDateTable`, `Lead Source Channels`, `Source_Sort`, `LS Group Sort`, `dim_entity` |
| Weekly-report snapshots (no relationships) | `report_scorecard`, `report_designer_leaderboard`, `report_lead_source_roi`, `report_designer_concentration`, `report_channel_roi`, `report_appointment_funnel` |
| Franchise-only | `Projects Part List` (ICLA); column `Salesforce_Data`.`Additional Lead Source` (ICAU) |

## Relationships as join keys

These are the model's relationships (`relationships.tmdl`). MySQL has no foreign keys: write the join yourself. In Power BI a row whose key has no match on the "one" side still counts, under a blank member; in SQL use a LEFT JOIN to keep it. "Active" is the path Power BI filters through by default; an inactive relationship is only used when a measure asks for it. "Both" means filters flow both ways in Power BI; SQL has no such notion.

| From (many side) | To (one side) | Cardinality | Active | Filter direction | Scope |
|---|---|---|---|---|---|
""" + "\n".join(f"| {a} | {b} | {c} | {d} | {e} | {f or 'all'} |" for a, b, c, d, e, f in REL) + """

Join form: `FROM Salesforce_Data AS s LEFT JOIN dim_designers AS d ON d.Designer = s.Designer`. `Salesforce_Data` and `Appointments` have **no** relationship to each other; both point at `dim_projects`, and Power BI filters do not flow from one to the other through it.

## Traps

1. **Appointments to sales is a name match that fans out.** The only link is `Project Name`, which repeats in `Salesforce_Data` (60 / 60 / 52 duplicate-name rows in ICLA / ICCH / ICAU) and across many appointments, and is NULL for about a quarter to a third of appointments (tasks, internal and availability events). Joining the two views row to row double-counts: aggregate each side per `Project Name` first, or use `EXISTS`.
2. **DateTable only covers 2020-01-01 to 2030-12-31.** Opportunities created before 2020 (1,535 ICLA, 1,175 ICCH, 1,025 ICAU) fall outside it. Power BI's "all time" through DateTable leaves them out; an unbounded SQL query includes them.
3. **Two date roles on `Salesforce_Data`.** `Created Date` relates to `DateTable` (active); `Original Project Sold Date` relates to `SoldDateTable` (active) and to `DateTable` (inactive). In SQL pick the column explicitly. `Original Project Sold Date` is NULL for unsold rows and for many older Sold rows (2,865 ICLA, 3,051 ICCH, 2,030 ICAU), and some non-Sold rows still carry one (45 / 33 / 37 sold-then-cancelled rows), so sold-date questions need `Revised Stage` = 'Sold' as well.
4. **`dim_designers` holds active designers only** (14 ICLA, 31 ICCH, 30 ICAU). `Salesforce_Data` rows whose `Designer` is not active (4,488 / 2,872 / 1,380) and appointments whose `designer_sk` is not active (7,212 / 1,181 / 2,660) drop out of an inner join; Power BI shows them under a blank designer. Grouping by `Salesforce_Data`.`Designer` instead lists inactive and x-prefixed former designers by name.
5. **Date-cast columns.** `Appointments`.`Activity Date and Time`, `Appointment Start Time` and `Start Of WeekA`, `Leads`.`Lead Created Date` and `Opportunities to Appointments`.`Activity Date and Time` and `Created Date` are DATE: the model drops the time of day, and so do the views.
6. **Week starts are Sundays** (`Start of Week`, `Start Of WeekA`, `Sold Start of Week`, `DateTable`.`Start Of Week`), but `DateTable`.`WeekdayNumber` counts Monday = 1 to Sunday = 7. `Start Of WeekA` follows `Activity Date and Time`, not `Appointment Start Time`.
7. **Flags have mixed types.** `Is Real Appointment` and `Is Future Appointment` are the text values 'TRUE' / 'FALSE'; `Is First Appointment`, `Is Converted Lead`, `Is Referral Lead`, `Has Showroom Visit`, `is_likely_duplicate` and the `Lead Milestones` 0/1 columns are integers; `hygiene_flag` and `warn_flag` are 0/1.
8. **`Appointments` mixes events and tasks.** `Activity Type` = 'Unspecified' rows are CRM tasks (6,249 ICLA, 15,069 ICCH, 14,296 ICAU). `Appointments`.`Project Stage` never contains 'Complete'.
9. **Frozen Gold values.** `Is Future Appointment`, `Lead Age in Days`, `Lead Age Category` and `dim_designers`.`active_flag` were computed when Gold was last built, not at query time.
10. **`Revised Stage` has exactly four values:** 'Sold', 'Proposed', 'Lost', 'Dead Lead'. Dead Lead is separate from Lost.
11. **`report_*` views keep every weekly snapshot** (about 150 `report_date` values). For current figures filter `report_date = (SELECT MAX(report_date) FROM <view>)`. They relate to nothing.
12. **`dim_entity` lists every franchise.** It is the model's franchise list, not data; all fact rows belong to this franchise only.
13. **`Leads_Journey` has a few leads missing** (7 ICLA, 6 ICCH, 3 ICAU leads have no journey row); `Lead Milestones` has exactly one row per lead.
14. **Blank versus NULL.** Power BI treats blank as equal to '' and 0; in SQL `= ''` does not match NULL, so use `IS NULL` or `COALESCE`.

## Views

"""
    body = "\n".join(view_section(t) for t in ORDER)
    return h + body


def parity_md():
    ents = ["ICLA", "ICCH", "ICAU"]
    rows = []
    for t in ORDER:
        vals = [COUNTS[e].get(t) for e in ents]
        rows.append(f"| `{t}` | " + " | ".join(f"{v:,}" if v is not None else "n/a" for v in vals) + " |")
    counts_table = "| View | ICLA | ICCH | ICAU |\n|---|---|---|---|\n" + "\n".join(rows)
    return f"""# Parity notes: mirrored views vs the Power BI models

The views in this folder reproduce what the ICLA, ICCH and ICAU models import: the same tables, columns, column order, Power Query steps and calculated columns and tables, and the same rows for the same Gold data. This file lists every place where the SQL side cannot match the model exactly, and why. `SCHEMA.md` is the schema reference; this file is for whoever interprets differences between the two routes.

## Verified after applying (2026-11-07)

- Sandbox data: dump started {VERSION['dump_started']}, restore finished {VERSION['restore_finished']}.
- Every view's row count equals the count of its source with the partition's filters, read through the franchise's own SELECT-only account.
- Every view's column names and order equal the franchise's TMDL column list, calculated columns included (`Salesforce_Data` has 28 columns, 29 in ICAU).
- No view returns a row of another entity (checked on every view with an `entity` column; `Lead Milestones` filters on the entity in all three of its sub-queries and every one of its `Lead ID`s exists in `Leads`).
- Each franchise account can read only its own schema: `information_schema` shows it only its own schema; `SHOW TABLES` on `icall_gold` and on the other franchise schemas, `SELECT` on `icall_gold`, `icall_silver`, `ic_weekly_report` and on the other franchise schemas, and `CREATE` are all denied (`sandbox.py probe` plus direct SELECT attempts through each account).
- The four sold-date calculated columns agree with their DAX definitions on every row (0 mismatches), and the views also work with ANSI_QUOTES added to the session `sql_mode`.
- 2026 year-to-date checks match the values in the research notes taken from live Gold: sold by sold date ICLA 501 / $5,211,995.05, ICCH 865 / $7,516,889.57, ICAU 429 / $6,035,311.27; sold by created date 469 / 814 / 398; created 963 / 1,509 / 917; pre-2020 opportunities 1,535 / 1,175 / 1,025.

## Row counts per franchise

{counts_table}

`DateTable`, `SoldDateTable`, `Lead Source Channels`, `Source_Sort`, `LS Group Sort` and `dim_entity` are tenant-agnostic and identical in every schema, as in the models.

## Where the views cannot match the model exactly

### Left out on purpose
1. **Measures.** None are mirrored; that difference is the point of the test. Not mirrored: 78 `_measures` (77 in ICAU), 44 `_measures_L&A`, 3 table measures on `Salesforce_Data` (`Appointments X Amount`, `Amount $`, `Tooltip Control`) and, in ICLA, 7 measures on `Projects Part List`. Totals: ICLA 132, ICCH 125, ICAU 124.
2. **The measure-holder tables `_measures` and `_measures_L&A`.** Both are entered-data tables with one text column (`Column1`) and zero rows (payload `[]`); they exist only to hold measures, so they have no view.
3. **Auto date/time tables.** None of the three TMDL definitions contains a `LocalDateTable_*` or `DateTableTemplate_*` table (auto date/time is off), so there is nothing to skip; any such artefact left in a local Desktop session is not part of the published definition.
4. **Report-page rules.** Filters that live only in the `.Report` page JSON (the ICLA appointment rule set with its named-staff exclusion list, the ICCH 'available' subject filter, the hidden-page designer exclusions, the `Revised Stage` = 'Sold' page filter, slicer defaults) are not part of the model and are not mirrored. The ICLA staff list contains person names and is not reproduced in any file here. Both routes have to get these rules from the shared rules document.

### Model behaviour that SQL has no equivalent for
5. **Relationships.** MySQL views carry no relationships. `SCHEMA.md` lists all 23 (ICLA 25) as join keys with cardinality, active/inactive state and filter direction, but the SQL writer must choose the join, its type and the date role itself. Specifically: (a) unmatched keys go to a blank member in Power BI and need a LEFT JOIN in SQL; (b) bidirectional filtering (`Opportunities to Appointments` to `dim_projects`, `Lead Milestones` to `Leads`, `Source_Sort` to `Lead Source Channels`) has no SQL analogue; (c) inactive relationships and `USERELATIONSHIP` become an explicit choice of column; (d) the active-only `dim_designers` and the 2020-2030 `DateTable` bound only apply in SQL when the query actually joins to them.
6. **Sort-by-column metadata** (month and weekday names, `Sold Month`, designers, lead sources, groups, `Contact Path`, `Journey Outcome`) cannot be attached to a view column; `SCHEMA.md` names the sort column so SQL can `ORDER BY` it. In the model `SoldDateTable`.`MonthYear` has no sort-by although `DateTable`.`MonthYear` has; this only affects display order.
7. **Hidden columns** stay visible in SQL (`Sold Month Number`, the `dim_designers` helper columns, the `report_*` `id` / `entity` / `created_at` columns, three `Lead Milestones` helper columns, `Projects Part List`.`id` / `entity`, `Opportunities to Appointments`.`entity`). DAX can reference hidden columns too, so no data is added; `SCHEMA.md` marks them.
8. **Descriptions.** The model's column descriptions are not stored on the views. `SCHEMA.md` repeats them without the Q&A synonym lists, which are often misleading for SQL (for example "Project ID" listed as a synonym of `Appointments`.`Project Name`).

### Type and value representation
9. **Date casts.** The model casts six datetime columns to `type date` but stores them as dateTime at midnight; the views return DATE. Values and comparisons are the same; only the display differs (`2026-01-05` vs `2026-01-05 00:00:00`). All other datetime columns keep their Gold DATETIME type, as in the model.
10. **Decimal vs double.** Power BI stores `Amount`, `Total Amount`, the `report_*` money and rate columns and the `Lead Milestones` day and amount columns as double; the views return MySQL DECIMAL. SQL sums are exact and DAX sums are floating point; at these magnitudes the difference is far below one cent and disappears when results are rounded to cents. `Lead Milestones` `Days to Scheduled` / `Days to Studio` come back as DECIMAL(23,2) rather than a double with the same value.
11. **Booleans.** `report_appointment_funnel`.`hygiene_flag` and `report_designer_concentration`.`warn_flag` are TRUE/FALSE in the model (MySQL `tinyint(1)` read as logical) and 0/1 in SQL.
12. **Text.** Both sides compare text case-insensitively (Gold uses `utf8mb4_unicode_ci`). Power BI keeps one spelling per case-insensitive value in a column (the first loaded), while SQL returns each row's stored spelling; MySQL also ignores trailing spaces in comparisons. On the current data this changes no counts: project names, designer names and lead-source names are unique under the same collation by primary key.
13. **Distinct steps.** Power Query's `Table.Distinct` (case-sensitive, keeps the first row) becomes `SELECT DISTINCT` for `dim_projects`, `Lead Source Channels` and `Source_Sort`, and `GROUP BY ... MIN()` for `LS Group Sort`. They return the same rows today because `dim_project` is unique on (entity, `Project Name`), `dim_lead_source` is unique on the lead-source name, and each lead-source group has one `Group Sort`. If Gold ever breaks those properties, Power BI would keep the first row (or fail to refresh a one-side duplicate) while SQL would keep both or the minimum.
14. **`Sold Month`** uses `ELT(MONTH(...), 'Jan', ...)` instead of `DATE_FORMAT('%b')`, so it does not depend on the session's `lc_time_names`; it equals DAX `FORMAT(..., "mmm")` under the model's en-US culture. `Sold Start of Week` stays DATETIME, like the DAX column.
15. **Blank and NULL.** DAX treats blank as equal to '' and 0 in comparisons; SQL NULL does not equal ''. The data is identical but the same-looking filter behaves differently (for example on `Appointments`.`Project Name`, NULL for 4,248 ICLA, 5,052 ICCH and 9,510 ICAU rows).

### Franchise differences and duplication
16. **ICAU `Additional Lead Source`.** Only the ICAU model keeps this column (Gold `Other Lead Source`, filled on 1,844 of 9,109 ICAU rows). The simplest faithful approach is an override: `20_salesforce_data.only_ICAU.sql` re-creates `Salesforce_Data` for ICAU with the shared definition plus that column in the model's position. The cost is a second copy of the `Salesforce_Data` definition, which has to be kept in step with `10_mirror.sql` (both were generated from the TMDL in one pass and diffed: the only difference is that line). ICLA and ICCH Gold rows also carry `Other Lead Source` (591 and 1,087 filled) but their models drop it, so their views do too.
17. **ICLA `Projects Part List`** is in `30_projects_part_list.only_ICLA.sql` with its two ICLA-only relationships.
18. **`dim_entity` lists all nine entity codes** (including DEMO, the offboarded ICMA and ICMN) with display names and regions, because the model imports the table unfiltered. It holds no fact data, so it was excluded from the entity-leak check.

### Query-time computation
19. **Derived tables are computed per query.** Power BI materialises `Lead Milestones`, the distinct lookups and `SoldDateTable` at refresh; the views compute them each time they are read. On this data a full `Lead Milestones` scan takes about 0.1 s and every other view about 0.02 s or less, far inside the sandbox's 15 s `max_execution_time`, so this affects latency only, not results. Filters on the date-cast columns cannot use Gold's (entity, datetime) index ranges; also a latency-only effect at these volumes.

### Data timing
20. **Different snapshots.** The sandbox is a frozen dump (see the timestamps above); each Power BI dataset reflects its last scheduled refresh from production. Counts match only when both sides hold the same Gold state, which is why the POC scores each route against a reference query on its own source. Gold columns computed at build time (`Is Future Appointment`, `Lead Age in Days`, `Lead Age Category`, `active_flag`) are equally frozen on both sides.
21. **Comment drift in the model.** The `Salesforce_Data` partition comment says `Final Completion Date` is dropped, but its column list keeps it; the views follow the code, not the comment.

### Access asymmetry (not a data gap)
22. `sandbox.py` grants each franchise account `SHOW VIEW`, so Route B can read the view definitions (`SHOW CREATE VIEW`), including the source schema names, the entity literal and the `Lead Milestones` query. Route A is not shown the Power Query behind its tables (whether `INFO.PARTITIONS()` would work through executeQueries with the portal identity was not tested). Isolation still holds (the account has no privileges on the source schemas), but if the comparison should hide implementation logic equally, drop `SHOW VIEW` from the grant.
"""


if __name__ == "__main__":
    (OUT / "SCHEMA.md").write_text(schema_md(), encoding="utf-8", newline="\n")
    (OUT / "PARITY_NOTES.md").write_text(parity_md(), encoding="utf-8", newline="\n")
    print("ok")
