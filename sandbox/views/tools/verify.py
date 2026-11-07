"""Verify the mirrored views: counts, columns vs TMDL, entity isolation, calc columns."""
import json
import time
from pathlib import Path

import mysql.connector
from sbq import q, conn

HERE = Path(__file__).parent
TM = json.loads((HERE / "tmdl.json").read_text(encoding="utf-8"))
ENTS = ["ICLA", "ICCH", "ICAU"]
SKIP = {"_measures", "_measures_L&A"}

EXPECTED = {   # independent count from base tables, same filters as the partitions
    "Salesforce_Data": "SELECT COUNT(*) FROM icall_gold.fact_sales WHERE entity=%s",
    "Leads": "SELECT COUNT(*) FROM icall_gold.fact_leads WHERE entity=%s",
    "Appointments": "SELECT COUNT(*) FROM icall_gold.fact_appointments WHERE entity=%s",
    "Leads_Journey": "SELECT COUNT(*) FROM icall_gold.fact_lead_journey WHERE entity=%s",
    "Opportunities to Appointments": "SELECT COUNT(*) FROM icall_gold.fact_opportunities_to_appointments WHERE entity=%s",
    "DateTable": "SELECT COUNT(*) FROM icall_gold.dim_date WHERE %s IS NOT NULL",
    "SoldDateTable": "SELECT COUNT(*) FROM icall_gold.dim_date WHERE %s IS NOT NULL",
    "dim_designers": "SELECT COUNT(*) FROM icall_gold.dim_designer WHERE entity=%s AND active_flag=1",
    "dim_projects": "SELECT COUNT(DISTINCT `Project Name`) FROM icall_gold.dim_project WHERE entity=%s",
    "Lead Source Channels": "SELECT COUNT(DISTINCT `Inspired Closets Lead Source`) FROM icall_gold.dim_lead_source WHERE %s IS NOT NULL",
    "Source_Sort": "SELECT COUNT(DISTINCT `Inspired Closets Lead Source`) FROM icall_gold.dim_lead_source WHERE %s IS NOT NULL",
    "LS Group Sort": "SELECT COUNT(DISTINCT `Lead Source Group`) FROM icall_gold.dim_lead_source WHERE %s IS NOT NULL",
    "dim_entity": "SELECT COUNT(*) FROM icall_gold.dim_entity WHERE %s IS NOT NULL",
    "Lead Milestones": "SELECT COUNT(*) FROM icall_gold.fact_leads WHERE entity=%s",
    "Projects Part List": "SELECT COUNT(*) FROM icall_gold.icla_project_parts_list WHERE entity=%s",
}
for r in ["report_scorecard", "report_designer_leaderboard", "report_lead_source_roi",
          "report_designer_concentration", "report_channel_roi", "report_appointment_funnel"]:
    EXPECTED[r] = f"SELECT COUNT(*) FROM ic_weekly_report.{r} WHERE entity=%s"

results = {}
problems = []
for ent in ENTS:
    schema = f"poc_{ent.lower()}"
    user = schema
    model_tables = [t for t in TM[ent] if t not in SKIP]
    _, vrows = q("SELECT TABLE_NAME FROM information_schema.VIEWS WHERE TABLE_SCHEMA=%s", (schema,))
    views = {r[0].lower(): r[0] for r in vrows}
    if set(views) != {t.lower() for t in model_tables}:
        problems.append(f"{ent}: view set {sorted(views)} != model {sorted(model_tables)}")
    results[ent] = {}
    c = conn(user, schema)
    cur = c.cursor()
    for t in model_tables:
        # columns, in order, vs TMDL
        _, crow = q("SELECT COLUMN_NAME, COLUMN_TYPE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s "
                    "AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION", (schema, t.lower()))
        vcols = [r[0] for r in crow]
        tcols = [col["name"] for col in TM[ent][t]["columns"]]
        if vcols != tcols:
            problems.append(f"{ent}.{t}: columns differ\n   view={vcols}\n   tmdl={tcols}")
        # row count through the franchise account
        t0 = time.time()
        cur.execute(f"SELECT COUNT(*) FROM `{t}`")
        n = cur.fetchone()[0]
        dt = time.time() - t0
        _, e = q(EXPECTED[t], (ent,))
        exp = e[0][0]
        results[ent][t] = n
        flag = "" if n == exp else f"  !! expected {exp}"
        if n != exp:
            problems.append(f"{ent}.{t}: {n} != expected {exp}")
        # entity leakage
        leak = ""
        if "entity" in vcols and t != "dim_entity":
            cur.execute(f"SELECT GROUP_CONCAT(DISTINCT entity) FROM `{t}`")
            ents = cur.fetchone()[0]
            if ents not in (ent, None):
                problems.append(f"{ent}.{t}: entity values {ents}")
            leak = f" entity={ents}"
        print(f"{ent:5} {t:32} {n:>8}{flag}{leak}  ({dt:.2f}s)")
    # cross-franchise read must fail
    for other in ENTS:
        if other == ent:
            continue
        try:
            cur.execute(f"SELECT COUNT(*) FROM poc_{other.lower()}.Salesforce_Data")
            cur.fetchall()
            problems.append(f"{ent} can read poc_{other.lower()}")
        except mysql.connector.Error as ex:
            print(f"  ok {ent} -> poc_{other.lower()}.Salesforce_Data denied ({ex.errno})")
    for stmt in ["SELECT COUNT(*) FROM icall_gold.fact_sales", "SELECT COUNT(*) FROM icall_silver.silver_lead_status_history",
                 "SELECT COUNT(*) FROM ic_weekly_report.report_scorecard"]:
        try:
            cur.execute(stmt)
            cur.fetchall()
            problems.append(f"{ent} can run {stmt}")
        except mysql.connector.Error as ex:
            print(f"  ok {ent} denied: {stmt.split('FROM ')[1]} ({ex.errno})")
    c.close()

(HERE / "rowcounts.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
print("\nPROBLEMS:" if problems else "\nno problems", *problems, sep="\n")
