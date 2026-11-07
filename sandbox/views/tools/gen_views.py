"""Generate daxvssql/sandbox/views/*.sql from the parsed TMDL (tmdl.json).

The output files are static SQL templates; this generator only guards against
typos in long column lists. Re-run after a model change, then review the diff.
"""
import json
import re
from pathlib import Path

HERE = Path(__file__).parent
OUT = Path(r"C:\Project Data Centralization\daxvssql\sandbox\views")
TM = json.loads((HERE / "tmdl.json").read_text(encoding="utf-8"))
REF = TM["ICLA"]          # canonical model for shared tables (identical in ICCH/ICAU apart from noted exceptions)
BT = lambda s: "`" + s.replace("`", "``") + "`"

LEAD_MILESTONES_TMDL = Path(r"C:\Project Data Centralization\Power BI Report\per_entity\ICLA"
                            r"\Salesforce BI ICLA.SemanticModel\definition\tables\Lead Milestones.tmdl")


def cols(tbl, ent="ICLA"):
    return TM[ent][tbl]["columns"]


def simple_view(view, source, ent="ICLA", casts=(), calc=None, entity_filter=True, extra_where=None, header=""):
    calc = calc or {}
    lines = []
    for c in cols(view, ent):
        name = c["name"]
        if c.get("expr"):
            lines.append(f"{calc[name]} AS {BT(name)}")
            continue
        src = c.get("sourceColumn") or name
        if name in casts:
            lines.append(f"CAST({BT(src)} AS DATE) AS {BT(name)}")
        elif src != name:
            lines.append(f"{BT(src)} AS {BT(name)}")
        else:
            lines.append(BT(name))
    where = []
    if entity_filter:
        where.append("`entity` = '{ENT}'")
    if extra_where:
        where.append(extra_where)
    sql = header
    sql += f"CREATE OR REPLACE SQL SECURITY DEFINER VIEW `{{SCHEMA}}`.{BT(view)} AS\nSELECT\n  "
    sql += ",\n  ".join(lines)
    sql += f"\nFROM {source}"
    if where:
        sql += "\nWHERE " + "\n  AND ".join(where)
    return sql + ";\n"


SOLD = "`Original Project Sold Date`"
SALES_CALC = {
    # DAX: IF(ISBLANK(sold), BLANK(), YEAR(sold))  -> NULL stays NULL
    "Sold Year": f"YEAR({SOLD})",
    "Sold Month Number": f"MONTH({SOLD})",
    # DAX FORMAT(sold, "mmm") under en-US -> Jan..Dec. ELT instead of DATE_FORMAT('%b') so the
    # result never depends on the session lc_time_names.
    "Sold Month": f"ELT(MONTH({SOLD}), 'Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec')",
    # DAX: sold - WEEKDAY(sold, 1) + 1 = the Sunday on or before the sold date (DAYOFWEEK: Sunday = 1)
    "Sold Start of Week": f"DATE_SUB({SOLD}, INTERVAL DAYOFWEEK({SOLD}) - 1 DAY)",
}


def salesforce_data(ent):
    extra = " (the 24 shared ones plus `Other Lead Source`, exposed as `Additional Lead Source`)" if ent == "ICAU" else ""
    hdr = (f"-- Salesforce_Data: one row per opportunity (project).\n"
           f"-- M: icall_gold.fact_opportunities (passthrough view over fact_sales), entity filter, Table.SelectColumns\n"
           f"-- keeps {24 + (ent == 'ICAU')} Gold columns{extra}. The 4 Sold* columns are the DAX calculated columns.\n")
    return simple_view("Salesforce_Data", "icall_gold.fact_opportunities", ent=ent, calc=SALES_CALC, header=hdr)


def lead_milestones():
    text = LEAD_MILESTONES_TMDL.read_text(encoding="utf-8-sig")
    m = re.search(r'Query = "(.*?)"\]\)', text, re.S)
    q = m.group(1).replace('""', '"')
    n = q.count("'ICLA'")
    assert n == 3, n
    q = q.replace("'ICLA'", "'{ENT}'")
    # pretty-break the one-line native query (whitespace only, no token changes)
    before = re.sub(r"\s+", "", q)
    q = re.sub(r"(AS `[^`]+`), ", r"\1,\n      ", q)
    q = q.replace("SELECT l.`Lead ID`, ", "SELECT l.`Lead ID`,\n      ", 1)
    for kw in [" FROM icall_gold.fact_leads AS l ", " LEFT JOIN ( SELECT entity, `Lead ID`",
               " LEFT JOIN ( SELECT entity, project_sk", " WHERE l.entity = "]:
        assert q.count(kw) == 1, kw
        q = q.replace(kw, "\n    " + kw.strip() + (" " if kw.endswith(" ") else ""))
    assert re.sub(r"\s+", "", q) == before
    order = [c["name"] for c in cols("Lead Milestones")]
    hdr = ("-- Lead Milestones: one row per lead, journey milestones from the lead status history.\n"
           "-- M: native SQL query (MySQL.Database ... [Query = ...]) reused verbatim with the entity literal,\n"
           "-- wrapped so the columns come out in the model order. Power Query then only types the columns.\n")
    sql = hdr + "CREATE OR REPLACE SQL SECURITY DEFINER VIEW `{SCHEMA}`.`Lead Milestones` AS\nSELECT\n  "
    sql += ",\n  ".join(f"m.{BT(c)}" for c in order)
    sql += "\nFROM (\n    " + q + "\n) AS m;\n"
    return sql


def mirror():
    parts = ["""-- 10_mirror.sql: mirrored Power BI model tables for franchise {ENT}, created in schema {SCHEMA}.
-- Applied by `python sandbox/sandbox.py apply-views`, which substitutes {ENT} and {SCHEMA}.
-- One view per table that the model "Salesforce BI {ENT}" imports, named exactly like the Power BI table,
-- with the Power BI column names, column order and Power Query steps (entity filter, column selection,
-- renames, datetime-to-date casts, active-only designers, distinct lookups, the Lead Milestones native SQL)
-- and the DAX calculated columns and calculated tables. No measures.
-- Column lists were generated from the TMDL and reviewed by hand. See SCHEMA.md and PARITY_NOTES.md.
-- Objects that exist in one franchise model only live in NN_name.only_ENT.sql files.
-- No semicolons or apostrophes inside comments: the mysql client parses them.
""".replace("{ENT} and {SCHEMA}", "the franchise code and schema")]

    parts.append(salesforce_data("ICLA"))

    parts.append(simple_view(
        "Leads", "icall_gold.fact_leads", casts={"Lead Created Date"},
        header="-- Leads: one row per Salesforce lead.\n-- M: icall_gold.fact_leads, `Lead Created Date` cast to date, entity filter. All Gold columns kept.\n"))

    parts.append(simple_view(
        "Appointments", "icall_gold.fact_appointments",
        casts={"Activity Date and Time", "Appointment Start Time", "Start Of WeekA"},
        header="-- Appointments: one row per calendar event or task (Salesforce Event/Task).\n"
               "-- M: icall_gold.fact_appointments, 3 datetime columns cast to date (time of day dropped), entity filter.\n"))

    parts.append(simple_view(
        "Leads_Journey", "icall_gold.fact_lead_journey",
        header="-- Leads_Journey: one row per lead, status-history summary.\n-- M: icall_gold.fact_lead_journey, entity filter.\n"))

    parts.append(simple_view(
        "Opportunities to Appointments", "icall_gold.fact_opportunities_to_appointments",
        casts={"Activity Date and Time", "Created Date"},
        header="-- Opportunities to Appointments: one row per converted lead with its first appointment (funnel bridge).\n"
               "-- M: icall_gold.fact_opportunities_to_appointments, 2 datetime columns cast to date, entity filter.\n"))

    parts.append(simple_view(
        "DateTable", "icall_gold.dim_date", entity_filter=False,
        header="-- DateTable: calendar 2020-01-01 to 2030-12-31, one row per day.\n-- M: icall_gold.dim_date, no entity filter (tenant-agnostic), no other steps.\n"))

    sd = [c["name"] for c in cols("SoldDateTable")]
    assert sd == [c["name"] for c in cols("DateTable")]
    parts.append("-- SoldDateTable: DAX calculated table SoldDateTable = DateTable (role-playing calendar for the sold date).\n"
                 "CREATE OR REPLACE SQL SECURITY DEFINER VIEW `{SCHEMA}`.`SoldDateTable` AS\nSELECT\n  "
                 + ",\n  ".join(BT(c) for c in sd) + "\nFROM `{SCHEMA}`.`DateTable`;\n")

    parts.append(simple_view(
        "dim_designers", "icall_gold.dim_designers", extra_where="`active_flag` = 1",
        header="-- dim_designers: designer roster, ACTIVE designers only.\n"
               "-- M: icall_gold.dim_designers (view over dim_designer), entity filter, active_flag = 1, two renames.\n"))

    parts.append("-- dim_projects: distinct project names, the hub between Salesforce_Data, Leads, Appointments\n"
                 "-- and Opportunities to Appointments. M: icall_gold.dim_projects, entity filter, keep Project Name, Table.Distinct.\n"
                 "CREATE OR REPLACE SQL SECURITY DEFINER VIEW `{SCHEMA}`.`dim_projects` AS\n"
                 "SELECT DISTINCT\n  `Project Name`\nFROM icall_gold.dim_projects\nWHERE `entity` = '{ENT}';\n")

    parts.append("-- Lead Source Channels: distinct lead-source names (tenant-agnostic lookup).\n"
                 "-- M: icall_gold.dim_lead_sources, keep the name, Table.Distinct. No entity filter.\n"
                 "CREATE OR REPLACE SQL SECURITY DEFINER VIEW `{SCHEMA}`.`Lead Source Channels` AS\n"
                 "SELECT DISTINCT\n  `Inspired Closets Lead Source`\nFROM icall_gold.dim_lead_sources;\n")

    parts.append("-- Source_Sort: lead source with its display sort (tenant-agnostic lookup).\n"
                 "-- M: icall_gold.dim_lead_sources, rename Inspired Closets Lead Source to Lead Source and Source Sort to Sort,\n"
                 "-- Table.Distinct on Lead Source. The name is the primary key of dim_lead_source, so DISTINCT is equivalent.\n"
                 "CREATE OR REPLACE SQL SECURITY DEFINER VIEW `{SCHEMA}`.`Source_Sort` AS\n"
                 "SELECT DISTINCT\n  `Source Sort` AS `Sort`,\n  `Inspired Closets Lead Source` AS `Lead Source`\n"
                 "FROM icall_gold.dim_lead_sources;\n")

    assert [c["name"] for c in cols("LS Group Sort")] == ["Lead Source Group", "Sort"]
    parts.append("-- LS Group Sort: lead-source group with its display sort (tenant-agnostic lookup, 3 groups).\n"
                 "-- M: icall_gold.dim_lead_sources, keep group and Group Sort renamed to Sort, Table.Distinct on the group.\n"
                 "-- Every group carries a single Group Sort value, so MIN() only makes the one-row-per-group rule explicit.\n"
                 "CREATE OR REPLACE SQL SECURITY DEFINER VIEW `{SCHEMA}`.`LS Group Sort` AS\n"
                 "SELECT\n  `Lead Source Group`,\n  MIN(`Group Sort`) AS `Sort`\n"
                 "FROM icall_gold.dim_lead_sources\nGROUP BY `Lead Source Group`;\n")

    parts.append(simple_view(
        "dim_entity", "icall_gold.dim_entity", entity_filter=False,
        header="-- dim_entity: franchise master list. The model imports ALL rows (no entity filter), so this view does too.\n"
               "-- M: icall_gold.dim_entity, no steps. Columns renamed to Title Case in the model.\n"))

    parts.append(lead_milestones())

    for r, what in [("report_scorecard", "scorecard metrics, long shape (one row per metric_key per window per report_date)"),
                    ("report_designer_leaderboard", "designer leaderboard (one row per designer per report_date)"),
                    ("report_lead_source_roi", "lead-source group ROI (one row per source_group per report_date)"),
                    ("report_designer_concentration", "designer concentration (one row per report_date)"),
                    ("report_channel_roi", "channel ROI (one row per channel_name per report_date)"),
                    ("report_appointment_funnel", "appointment funnel (one row per activity_type per report_date)")]:
        parts.append(simple_view(
            r, f"icall_gold.{r}",
            header=f"-- {r}: weekly-report snapshot, {what}.\n"
                   f"-- M: icall_gold.{r} (passthrough view over ic_weekly_report.{r}), entity filter.\n"))
    return "\n".join(parts)


def icau_sales():
    return ("-- 20_salesforce_data.only_ICAU.sql: the ICAU model keeps one extra Salesforce_Data column,\n"
            "-- `Additional Lead Source` (Gold column `Other Lead Source`, the free-text detail shown when Lead Source = Other).\n"
            "-- Applied to ICAU only, after 10_mirror.sql: it REPLACES the shared Salesforce_Data view with the same\n"
            "-- definition plus that column in the ICAU model position (after Lead Source). Keep the two in step.\n\n"
            + salesforce_data("ICAU"))


def icla_parts():
    return ("-- 30_projects_part_list.only_ICLA.sql: table that exists in the ICLA model only.\n\n"
            + simple_view("Projects Part List", "icall_gold.icla_project_parts_list",
                          header="-- Projects Part List: closet-parts bill of materials, one row per part line per project (current list, not history).\n"
                                 "-- M: icall_gold.icla_project_parts_list, entity filter. Derived fields are generated columns in MySQL.\n"))


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "10_mirror.sql").write_text(mirror(), encoding="utf-8", newline="\n")
    (OUT / "20_salesforce_data.only_ICAU.sql").write_text(icau_sales(), encoding="utf-8", newline="\n")
    (OUT / "30_projects_part_list.only_ICLA.sql").write_text(icla_parts(), encoding="utf-8", newline="\n")
    print("written to", OUT)
