"""Semantic spot checks on the mirrored views (run as each franchise account)."""
from sbq import conn, q

CHECKS = {
    "sold calc cols wrong (should be 0)": """
        SELECT SUM(CASE WHEN `Original Project Sold Date` IS NULL
                         THEN (`Sold Year` IS NOT NULL) + (`Sold Month Number` IS NOT NULL) + (`Sold Month` IS NOT NULL) + (`Sold Start of Week` IS NOT NULL)
                         ELSE (`Sold Year` <> YEAR(`Original Project Sold Date`))
                            + (`Sold Month Number` <> MONTH(`Original Project Sold Date`))
                            + (BINARY `Sold Month` <> DATE_FORMAT(`Original Project Sold Date`, '%b'))
                            + (DAYOFWEEK(`Sold Start of Week`) <> 1)
                            + (`Sold Start of Week` > `Original Project Sold Date`)
                            + (DATEDIFF(`Original Project Sold Date`, `Sold Start of Week`) > 6) END)
        FROM Salesforce_Data""",
    "rows with sold date": "SELECT COUNT(`Original Project Sold Date`) FROM Salesforce_Data",
    "Amount by Revised Stage": "SELECT GROUP_CONCAT(CONCAT(`Revised Stage`,'=',n,'/',amt) ORDER BY `Revised Stage` SEPARATOR ' | ') FROM (SELECT `Revised Stage`, COUNT(*) n, SUM(Amount) amt FROM Salesforce_Data GROUP BY 1) x",
    "YTD 2026 sold by sold date (n / $)": "SELECT CONCAT(COUNT(*), ' / ', SUM(Amount)) FROM Salesforce_Data WHERE `Revised Stage`='Sold' AND `Original Project Sold Date` >= '2026-01-01' AND `Original Project Sold Date` < '2026-11-08'",
    "YTD 2026 sold by created": "SELECT COUNT(*) FROM Salesforce_Data WHERE `Revised Stage`='Sold' AND `Created Date` >= '2026-01-01' AND `Created Date` < '2026-11-08'",
    "YTD 2026 created (all)": "SELECT COUNT(*) FROM Salesforce_Data WHERE `Created Date` >= '2026-01-01' AND `Created Date` < '2026-11-08'",
    "pre-2020 created (outside DateTable)": "SELECT COUNT(*) FROM Salesforce_Data s LEFT JOIN DateTable d ON d.`Date` = s.`Created Date` WHERE d.`Date` IS NULL",
    "duplicate Project Name rows": "SELECT COUNT(*) - COUNT(DISTINCT `Project Name`) FROM Salesforce_Data",
    "Designer rows not in dim_designers": "SELECT COUNT(*) FROM Salesforce_Data s LEFT JOIN dim_designers d ON d.Designer = s.Designer WHERE d.Designer IS NULL",
    "Appointments rows w/ designer_sk not in dim_designers": "SELECT COUNT(*) FROM Appointments a LEFT JOIN dim_designers d ON d.designer_sk = a.designer_sk WHERE d.designer_sk IS NULL",
    "Appointments NULL Project Name": "SELECT COUNT(*) FROM Appointments WHERE `Project Name` IS NULL",
    "Appointments matched to a Salesforce_Data row by name": "SELECT COUNT(*) FROM Appointments a WHERE EXISTS (SELECT 1 FROM Salesforce_Data s WHERE s.`Project Name` = a.`Project Name`)",
    "Appointments 2026+ (Start Time)": "SELECT COUNT(*) FROM Appointments WHERE `Appointment Start Time` >= '2026-01-01'",
    "Appointments NULL Start Time": "SELECT COUNT(*) FROM Appointments WHERE `Appointment Start Time` IS NULL",
    "Lead Milestones ids not in Leads": "SELECT COUNT(*) FROM `Lead Milestones` m LEFT JOIN Leads l ON l.`Lead ID` = m.`Lead ID` WHERE l.`Lead ID` IS NULL",
    "Lead Milestones distinct ids": "SELECT COUNT(DISTINCT `Lead ID`) FROM `Lead Milestones`",
    "Lead Milestones outcome mix": "SELECT GROUP_CONCAT(CONCAT(`Journey Outcome`,'=',n) ORDER BY o SEPARATOR ' | ') FROM (SELECT `Journey Outcome`, MIN(`Journey Outcome Order`) o, COUNT(*) n FROM `Lead Milestones` GROUP BY 1) x",
    "Leads with no Leads_Journey row": "SELECT COUNT(*) FROM Leads l LEFT JOIN Leads_Journey j ON j.`Lead ID` = l.`Lead ID` WHERE j.`Lead ID` IS NULL",
    "Leads_Journey rows with no Leads row": "SELECT COUNT(*) FROM Leads_Journey j LEFT JOIN Leads l ON l.`Lead ID` = j.`Lead ID` WHERE l.`Lead ID` IS NULL",
    "O2A project names not in dim_projects": "SELECT COUNT(*) FROM `Opportunities to Appointments` o LEFT JOIN dim_projects p ON p.`Project Name` = o.`Converted Project: Project Name` WHERE p.`Project Name` IS NULL",
    "O2A NULL project name": "SELECT COUNT(*) FROM `Opportunities to Appointments` WHERE `Converted Project: Project Name` IS NULL",
    "Salesforce_Data Lead Source not in Source_Sort": "SELECT COUNT(*) FROM Salesforce_Data s LEFT JOIN Source_Sort x ON x.`Lead Source` = s.`Lead Source` WHERE x.`Lead Source` IS NULL",
    "Leads channel not in Lead Source Channels (non-null)": "SELECT COUNT(*) FROM Leads s LEFT JOIN `Lead Source Channels` x ON x.`Inspired Closets Lead Source` = s.`Lead Source Channel` WHERE x.`Inspired Closets Lead Source` IS NULL AND s.`Lead Source Channel` IS NOT NULL AND s.`Lead Source Channel` <> ''",
    "latest report_date": "SELECT MAX(report_date) FROM report_scorecard",
}


def run(ent):
    schema = f"poc_{ent.lower()}"
    c = conn(schema, schema)
    cur = c.cursor()
    print(f"===== {ent}")
    for k, sql in CHECKS.items():
        cur.execute(sql)
        print(f"  {k:58} {cur.fetchone()[0]}")
    # ANSI_QUOTES session (the planned executor adds it): views must still work
    cur.execute("SET SESSION sql_mode = CONCAT(@@sql_mode, ',ANSI_QUOTES')")
    cur.execute('SELECT COUNT(*) FROM "Lead Milestones" WHERE "Exclude From Journey" = 0')
    print(f"  {'ANSI_QUOTES: Lead Milestones non-excluded':58} {cur.fetchone()[0]}")
    cur.execute('SELECT COUNT(*) FROM Salesforce_Data WHERE "Revised Stage" = \'Sold\'')
    print(f"  {'ANSI_QUOTES: Sold rows':58} {cur.fetchone()[0]}")
    c.close()


for e in ["ICLA", "ICCH", "ICAU"]:
    run(e)

print("\nview column types (poc_icla), cast/calc columns:")
_, rows = q("SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='poc_icla' "
            "AND (COLUMN_NAME LIKE 'Sold%%' OR COLUMN_NAME IN ('Lead Created Date','Activity Date and Time','Appointment Start Time','Start Of WeekA','Created Date','Sort','Contact Attempts','Days to Scheduled','Days to Studio','Days to Sold','Sold Amount','Reached Studio')) ORDER BY 1,2")
for r in rows:
    print("  ", *r)
_, rows = q("SELECT COLUMN_NAME, COLUMN_TYPE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='poc_icau' AND TABLE_NAME='salesforce_data' AND COLUMN_NAME='Additional Lead Source'")
print("   ICAU", rows)
_, rows = q("SELECT COUNT(*), SUM(`Other Lead Source` IS NOT NULL AND `Other Lead Source` <> '') FROM icall_gold.fact_sales WHERE entity='ICAU'")
print("   ICAU Other Lead Source filled:", rows)
_, rows = q("SELECT entity, SUM(`Other Lead Source` IS NOT NULL AND `Other Lead Source` <> '') FROM icall_gold.fact_sales WHERE entity IN ('ICLA','ICCH') GROUP BY entity")
print("   ICLA/ICCH Other Lead Source filled (not exposed):", rows)
