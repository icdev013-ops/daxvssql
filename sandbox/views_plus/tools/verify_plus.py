"""Arm B+ metric views vs the rules/sql_bindings.md SQL, on the same sandbox data, through the pocplus accounts.

Each check runs the metric-view form and the bindings form (over the pass-through views) and compares the full
result sets. Prints only PASS/FAIL per check; the values go to daxvssql/runs/ (gitignored). ICLA's excluded
staff names are read from private/rules_names.md and passed as query parameters, never printed.
usage: python sandbox/views_plus/tools/verify_plus.py      exit code 1 on any mismatch
"""
import json
import re
import sys
from decimal import Decimal
from pathlib import Path

import mysql.connector

POC = Path(__file__).resolve().parents[3]
ENV = POC / ".env"
A = "2026-11-07"
Y = 2026
SALES = {"ICLA": "`Created Date`", "ICCH": "`Original Project Sold Date`", "ICAU": "`Original Project Sold Date`"}


def env(key):
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip()
    raise KeyError(key)


def staff():
    lines = (POC / "private" / "rules_names.md").read_text(encoding="utf-8").splitlines()
    i = next(i for i, l in enumerate(lines) if re.match(r"^##\s+`?\{ICLA_EXCLUDED_STAFF\}`?", l))
    out = []
    for l in lines[i + 1:]:
        if re.match(r"^##\s", l):
            break
        m = re.match(r"^\d+\.\s+(.+?)\s*$", l)
        if m:
            out.append(m.group(1))
    return out


def rule(ent, names):
    if ent == "ICLA":
        ph = ", ".join(["%s"] * len(names))
        return (" AND COALESCE(a.`Activity Type`, '') NOT IN ('Installation', 'Unspecified') AND a.`Appointment Subject` LIKE '%1st%'"
                " AND a.`Appointment Subject` NOT LIKE '%Cancelled%' AND COALESCE(a.`Assigned Staff Member`, '') NOT IN (" + ph + ")"
                " AND a.`Appointment Start Time` >= DATE '2025-06-01'"), list(names)
    if ent == "ICCH":
        return " AND COALESCE(a.`Appointment Subject`, '') NOT LIKE '%available%'", []
    return "", []


def norm(v):
    if isinstance(v, Decimal):
        return float(round(v, 6))
    if isinstance(v, float):
        return round(v, 6)
    return v


def main():
    names = staff()
    report, fails = {}, 0
    for ent in ["ICLA", "ICCH", "ICAU"]:
        cn = mysql.connector.connect(host="127.0.0.1", port=3307, user=f"pocplus_{ent.lower()}",
                                     password=env(f"SANDBOX_{ent}_PLUS_PASSWORD"), database=f"pocplus_{ent.lower()}",
                                     charset="utf8mb4", use_pure=True)
        cur = cn.cursor()

        def one(sql, args=()):
            cur.execute(sql, args)
            return tuple(norm(x) for x in cur.fetchall()[0])

        def allrows(sql, args=()):
            cur.execute(sql, args)
            return [tuple(norm(x) for x in r) for r in cur.fetchall()]

        SD = SALES[ent]
        r, ra = rule(ent, names)
        ytd_s = f"MAKEDATE({Y}, 1)"
        ytd_e = f"DATE '{A}' + INTERVAL 1 DAY"
        checks = {
            "metric_sales rows / distinct project_id = Salesforce_Data rows": (
                "SELECT COUNT(*), COUNT(DISTINCT project_id) FROM metric_sales",
                "SELECT COUNT(*), COUNT(DISTINCT `Project ID`) FROM Salesforce_Data"),
            "YTD sold revenue + count, franchise basis": (
                f"SELECT SUM(sold_amount), SUM(is_sold) FROM metric_sales WHERE is_sold = 1 AND sales_date >= {ytd_s} AND sales_date < {ytd_e}",
                f"SELECT SUM(Amount), COUNT(*) FROM Salesforce_Data WHERE `Revised Stage` = 'Sold' AND {SD} >= {ytd_s} AND {SD} < {ytd_e}"),
            "2024 sold revenue, franchise basis": (
                "SELECT SUM(sold_amount) FROM metric_sales WHERE is_sold = 1 AND YEAR(sales_date) = 2024",
                f"SELECT SUM(Amount) FROM Salesforce_Data WHERE `Revised Stage` = 'Sold' AND YEAR({SD}) = 2024"),
            "all-time sold revenue": (
                "SELECT SUM(sold_amount), SUM(is_sold) FROM metric_sales",
                "SELECT SUM(CASE WHEN `Revised Stage` = 'Sold' THEN Amount END), SUM(`Revised Stage` = 'Sold') FROM Salesforce_Data"),
            "YTD lost / dead / proposed amounts (created)": (
                f"SELECT SUM(CASE WHEN is_lost = 1 THEN amount END), SUM(CASE WHEN is_dead_lead = 1 THEN amount END), SUM(CASE WHEN is_proposed = 1 THEN amount END) FROM metric_sales WHERE sales_date >= {ytd_s} AND sales_date < {ytd_e} AND is_sold = 0",
                f"SELECT SUM(CASE WHEN `Revised Stage`='Lost' THEN Amount END), SUM(CASE WHEN `Revised Stage`='Dead Lead' THEN Amount END), SUM(CASE WHEN `Revised Stage`='Proposed' THEN Amount END) FROM Salesforce_Data WHERE `Created Date` >= {ytd_s} AND `Created Date` < {ytd_e}"),
            "YTD sold by designer (top designers) franchise basis": (
                f"SELECT designer, SUM(sold_amount) FROM metric_sales WHERE is_sold = 1 AND sales_date >= {ytd_s} AND sales_date < {ytd_e} GROUP BY designer ORDER BY 2 DESC, 1",
                f"SELECT Designer, SUM(Amount) FROM Salesforce_Data WHERE `Revised Stage` = 'Sold' AND {SD} >= {ytd_s} AND {SD} < {ytd_e} GROUP BY Designer ORDER BY 2 DESC, 1"),
            "active designer flag = dim_designers join": (
                "SELECT SUM(is_active_designer) FROM metric_sales",
                "SELECT COUNT(*) FROM Salesforce_Data s JOIN dim_designers d ON d.Designer = s.Designer"),
        }
        # closing rates: bindings section 5
        def c1(d0, d1):
            return (f"SELECT LEAST(1, SUM(`Revised Stage` = 'Sold' AND `Original Project Sold Date` >= {d0} AND `Original Project Sold Date` < {d1} + INTERVAL 1 DAY)"
                    f" / NULLIF(SUM(`Created Date` >= {d0} AND `Created Date` < {d1} + INTERVAL 1 DAY), 0)),"
                    f" SUM(`Created Date` >= {d0} AND `Created Date` < {d1} + INTERVAL 1 DAY),"
                    f" SUM(`Revised Stage` = 'Sold' AND `Original Project Sold Date` >= {d0} AND `Original Project Sold Date` < {d1} + INTERVAL 1 DAY) FROM Salesforce_Data")

        def c1m(d0, d1):
            return (f"SELECT LEAST(1, SUM(projects_sold) / NULLIF(SUM(projects_created), 0)), SUM(projects_created), SUM(projects_sold)"
                    f" FROM metric_closing_daily WHERE event_date >= {d0} AND event_date <= {d1}")
        A_ = f"DATE '{A}'"
        checks["C1 YTD"] = (c1m(ytd_s, A_), c1(ytd_s, A_))
        checks["C1 last 4 months"] = (c1m(f"{A_} - INTERVAL 4 MONTH", A_), c1(f"{A_} - INTERVAL 4 MONTH", A_))
        checks["C1 all time (2020-2030)"] = (c1m("DATE '2020-01-01'", "DATE '2030-12-31'"), c1("DATE '2020-01-01'", "DATE '2030-12-31'"))
        checks["C1 2025 calendar year"] = (c1m("DATE '2025-01-01'", "DATE '2025-12-31'"), c1("DATE '2025-01-01'", "DATE '2025-12-31'"))
        checks["C2 YTD"] = (
            f"SELECT SUM(projects_sold) / NULLIF(SUM(projects_created_excl_dead), 0), SUM(projects_created_excl_dead) FROM metric_closing_daily WHERE event_date >= {ytd_s} AND event_date <= {A_}",
            f"SELECT SUM(`Revised Stage` = 'Sold' AND `Original Project Sold Date` >= {ytd_s} AND `Original Project Sold Date` < {ytd_e}) / NULLIF(SUM(`Revised Stage` <> 'Dead Lead' AND `Created Date` >= {ytd_s} AND `Created Date` < {ytd_e}), 0), SUM(`Revised Stage` <> 'Dead Lead' AND `Created Date` >= {ytd_s} AND `Created Date` < {ytd_e}) FROM Salesforce_Data")
        checks["C3 120 days"] = (
            f"SELECT SUM(projects_sold) / NULLIF(SUM(projects_created_excl_dead), 0), SUM(projects_sold), SUM(projects_created_excl_dead) FROM metric_closing_daily WHERE event_date >= {A_} - INTERVAL 119 DAY AND event_date <= {A_}",
            f"SELECT SUM(Stage IN ('Ready to Install','Ready to Order','Complete','Incomplete') AND `Original Project Sold Date` >= {A_} - INTERVAL 119 DAY AND `Original Project Sold Date` < {A_} + INTERVAL 1 DAY) / NULLIF(SUM(Stage <> 'Dead Lead' AND `Created Date` >= {A_} - INTERVAL 119 DAY AND `Created Date` < {A_} + INTERVAL 1 DAY), 0), SUM(Stage IN ('Ready to Install','Ready to Order','Complete','Incomplete') AND `Original Project Sold Date` >= {A_} - INTERVAL 119 DAY AND `Original Project Sold Date` < {A_} + INTERVAL 1 DAY), SUM(Stage <> 'Dead Lead' AND `Created Date` >= {A_} - INTERVAL 119 DAY AND `Created Date` < {A_} + INTERVAL 1 DAY) FROM Salesforce_Data")
        checks["C4 YTD"] = (
            f"SELECT SUM(c4_sold) / NULLIF(SUM(c4_decided), 0), SUM(c4_sold), SUM(c4_decided) FROM metric_closing_daily WHERE event_date >= {ytd_s} AND event_date <= {A_}",
            f"SELECT SUM(`Revised Stage` = 'Sold') / NULLIF(SUM(`Revised Stage` IN ('Sold', 'Lost', 'Dead Lead')), 0), SUM(`Revised Stage` = 'Sold'), SUM(`Revised Stage` IN ('Sold', 'Lost', 'Dead Lead')) FROM Salesforce_Data WHERE `Created Date` >= {ytd_s} AND `Created Date` < {ytd_e}")
        checks["C1 YTD per active designer (>= 30 created)"] = (
            f"SELECT designer, SUM(projects_created), SUM(projects_sold), LEAST(1, SUM(projects_sold) / NULLIF(SUM(projects_created), 0)) FROM metric_closing_daily WHERE is_active_designer = 1 AND event_date >= {ytd_s} AND event_date <= {A_} GROUP BY designer HAVING SUM(projects_created) >= 30 ORDER BY 4 DESC, 1",
            f"WITH w AS (SELECT {ytd_s} AS d0, {A_} AS d1), per AS (SELECT d.Designer, SUM(s.`Created Date` >= w.d0 AND s.`Created Date` < w.d1 + INTERVAL 1 DAY) AS created_n, SUM(s.`Revised Stage` = 'Sold' AND s.`Original Project Sold Date` >= w.d0 AND s.`Original Project Sold Date` < w.d1 + INTERVAL 1 DAY) AS sold_n FROM Salesforce_Data AS s JOIN dim_designers AS d ON d.Designer = s.Designer CROSS JOIN w GROUP BY d.Designer) SELECT Designer, created_n, sold_n, LEAST(1, sold_n / NULLIF(created_n, 0)) FROM per WHERE created_n >= 30 ORDER BY 4 DESC, 1")
        checks["C1 YTD by lead source"] = (
            f"SELECT lead_source, SUM(projects_created), SUM(projects_sold) FROM metric_closing_daily WHERE event_date >= {ytd_s} AND event_date <= {A_} GROUP BY lead_source HAVING SUM(projects_created) > 0 ORDER BY 1",
            f"SELECT `Lead Source`, SUM(`Created Date` >= {ytd_s} AND `Created Date` < {ytd_e}), SUM(`Revised Stage` = 'Sold' AND `Original Project Sold Date` >= {ytd_s} AND `Original Project Sold Date` < {ytd_e}) FROM Salesforce_Data GROUP BY `Lead Source` HAVING SUM(`Created Date` >= {ytd_s} AND `Created Date` < {ytd_e}) > 0 ORDER BY 1")
        # appointments: bindings section 8
        checks["appointments unique per key"] = (
            "SELECT COUNT(*) - COUNT(DISTINCT appointment_key) FROM metric_appointments_reported", "SELECT 0")
        checks["appointments 2026 (franchise rule), success rate, on sold projects (EXISTS)"] = (
            "SELECT COUNT(*), SUM(is_stage_sold) / COUNT(*), SUM(matched_project_sold) FROM metric_appointments_reported WHERE YEAR(appointment_date) = 2026",
            ("SELECT COUNT(*), SUM(a.`Project Stage` = 'Sold') / COUNT(*), SUM(EXISTS (SELECT 1 FROM Salesforce_Data s WHERE s.`Project Name` = a.`Project Name` AND s.`Revised Stage` = 'Sold'))"
             " FROM Appointments a WHERE YEAR(a.`Appointment Start Time`) = 2026" + r, ra))
        checks["appointments all rows (franchise rule)"] = (
            "SELECT COUNT(*) FROM metric_appointments_reported", ("SELECT COUNT(*) FROM Appointments a WHERE 1 = 1" + r, ra))
        checks["appointments 2026 by source"] = (
            "SELECT appointment_source, COUNT(*), SUM(is_stage_sold) FROM metric_appointments_reported WHERE YEAR(appointment_date) = 2026 GROUP BY 1 ORDER BY 1",
            ("SELECT COALESCE(a.`Lead Source Channel`, 'No source recorded'), COUNT(*), SUM(a.`Project Stage` = 'Sold') FROM Appointments a WHERE YEAR(a.`Appointment Start Time`) = 2026" + r + " GROUP BY 1 ORDER BY 1", ra))
        checks["appointment project match: single-match ids are real projects"] = (
            "SELECT COUNT(*) FROM metric_appointments_reported a JOIN metric_sales s ON s.project_id = a.matched_project_id",
            "SELECT COUNT(*) FROM metric_appointments_reported WHERE matched_projects = 1")
        checks["metric_designers = roster; has_projects = Total Projects > 0"] = (
            "SELECT COUNT(*), SUM(has_projects) FROM metric_designers",
            "SELECT COUNT(*), SUM(`Total Projects` > 0) FROM dim_designers")
        out = {}
        for label, (m, b) in checks.items():
            bs, bargs = (b if isinstance(b, tuple) else (b, ()))
            mv = allrows(m)
            bv = allrows(bs, bargs)
            ok = mv == bv
            fails += not ok
            out[label] = {"ok": ok, "metric": mv[:15], "binding": bv[:15], "rows": len(mv)}
            print(f"{'PASS' if ok else 'FAIL'}  {ent}  {label}  ({len(mv)} row{'s' if len(mv) != 1 else ''})")
        report[ent] = out
        cn.close()
    dest = POC / "runs" / f"plus_verify_sql_{A}.json"
    dest.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print(f"\n{fails} failing checks; values in {dest} (gitignored)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
