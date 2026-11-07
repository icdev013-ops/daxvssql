/**
 * B+ parity: metric views (sandbox, pocplus accounts) vs the Power BI measures (published datasets, executeQueries,
 * read-only service principal via the bot's dax.ts). One DAX call per franchise, >= 7 s apart (<= 10 a minute).
 * Prints only match status and relative differences; values go to daxvssql/runs/ (gitignored). Query text with
 * the ICLA staff list is never printed. Window: 2026 to the anchor 2026-11-07 (the sandbox snapshot date).
 * usage (from bot/): npx tsx scripts/parity_plus.ts
 */
import fs from "node:fs";
import path from "node:path";
import { POC_DIR as POC } from "../src/paths";
import { loadRulesTemplates } from "../src/prompts/rules";
import { datasetRefreshInfo, executeDax } from "../src/tools/dax";
import { closePools, executeSql } from "../src/tools/sql";
const SALES: Record<string, string> = {
  ICLA: "Salesforce_Data[Created Date]", ICCH: "Salesforce_Data[Original Project Sold Date]", ICAU: "Salesforce_Data[Original Project Sold Date]",
};
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function apptRule(ent: string, staff: string[]): string {
  if (ent === "ICLA") {
    const set = `{${staff.map((n) => `"${n.replace(/"/g, '""')}"`).join(", ")}}`;
    return `, NOT Appointments[Activity Type] IN {"Installation", "Unspecified"}, CONTAINSSTRING(Appointments[Appointment Subject], "1st"), ` +
      `NOT CONTAINSSTRING(Appointments[Appointment Subject], "Cancelled"), NOT Appointments[Assigned Staff Member] IN ${set}, ` +
      `Appointments[Appointment Start Time] >= DATE(2025, 6, 1)`;
  }
  if (ent === "ICCH") return `, NOT CONTAINSSTRING(Appointments[Appointment Subject], "available")`;
  return "";
}

function dax(ent: string, staff: string[]): string {
  const sd = SALES[ent];
  const win = `${sd} >= DATE(2026, 1, 1) && ${sd} < DATE(2026, 11, 8)`;
  const cwin = `Salesforce_Data[Created Date] >= DATE(2026, 1, 1) && Salesforce_Data[Created Date] < DATE(2026, 11, 8)`;
  const r = apptRule(ent, staff);
  return `EVALUATE
ROW(
  "sold_revenue_ytd", CALCULATE([Revenue Sold], ${win}),
  "sold_count_ytd", CALCULATE([Count Sold], ${win}),
  "c1_ytd", CALCULATE([Closing Rate], FILTER(ALL(DateTable[Date]), DateTable[Date] >= DATE(2026, 1, 1) && DateTable[Date] <= DATE(2026, 11, 7))),
  "c1_created", CALCULATE([Projects Created (Period)], FILTER(ALL(DateTable[Date]), DateTable[Date] >= DATE(2026, 1, 1) && DateTable[Date] <= DATE(2026, 11, 7))),
  "c1_sold", CALCULATE([Projects Sold (Period)], FILTER(ALL(DateTable[Date]), DateTable[Date] >= DATE(2026, 1, 1) && DateTable[Date] <= DATE(2026, 11, 7))),
  "c4_ytd", DIVIDE(CALCULATE([Count Sold], ${cwin}), CALCULATE(COUNTROWS(Salesforce_Data), Salesforce_Data[Revised Stage] IN {"Sold", "Lost", "Dead Lead"}, ${cwin})),
  "appts_2026", CALCULATE([Total Activity Count], YEAR(Appointments[Appointment Start Time]) = 2026${r}),
  "appt_success_2026", CALCULATE([Project Success Rate], YEAR(Appointments[Appointment Start Time]) = 2026${r}),
  "designers_listed", COUNTROWS(FILTER(dim_designers, dim_designers[Total Projects] > 0))
)`;
}

const SQL = `SELECT
  (SELECT SUM(sold_amount) FROM metric_sales WHERE is_sold = 1 AND sales_date >= DATE '2026-01-01' AND sales_date < DATE '2026-11-08') AS sold_revenue_ytd,
  (SELECT SUM(is_sold) FROM metric_sales WHERE is_sold = 1 AND sales_date >= DATE '2026-01-01' AND sales_date < DATE '2026-11-08') AS sold_count_ytd,
  (SELECT LEAST(1, SUM(projects_sold) / NULLIF(SUM(projects_created), 0)) FROM metric_closing_daily WHERE event_date >= DATE '2026-01-01' AND event_date <= DATE '2026-11-07') AS c1_ytd,
  (SELECT SUM(projects_created) FROM metric_closing_daily WHERE event_date >= DATE '2026-01-01' AND event_date <= DATE '2026-11-07') AS c1_created,
  (SELECT SUM(projects_sold) FROM metric_closing_daily WHERE event_date >= DATE '2026-01-01' AND event_date <= DATE '2026-11-07') AS c1_sold,
  (SELECT SUM(c4_sold) / NULLIF(SUM(c4_decided), 0) FROM metric_closing_daily WHERE event_date >= DATE '2026-01-01' AND event_date <= DATE '2026-11-07') AS c4_ytd,
  (SELECT COUNT(*) FROM metric_appointments_reported WHERE YEAR(appointment_date) = 2026) AS appts_2026,
  (SELECT SUM(is_stage_sold) / NULLIF(COUNT(*), 0) FROM metric_appointments_reported WHERE YEAR(appointment_date) = 2026) AS appt_success_2026,
  (SELECT COUNT(*) FROM metric_designers WHERE has_projects = 1) AS designers_listed`;

async function main() {
  const staff = loadRulesTemplates(path.join(POC, "rules"), path.join(POC, "private", "rules_names.md")).staff ?? [];
  if (!staff.length) throw new Error("private staff list missing");
  const out: Record<string, unknown> = {};
  let first = true;
  for (const ent of ["ICLA", "ICCH", "ICAU"]) {
    if (!first) await sleep(7000);
    first = false;
    const s = await executeSql(ent, SQL, "pocplus");
    if (s.error) throw new Error(`${ent} SQL: ${s.error}`);
    const d = await executeDax(ent, dax(ent, staff));
    if (d.error) { console.log(`${ent}: DAX error ${d.error.replace(/\{[^}]*\}/g, "{…}").slice(0, 300)}`); out[ent] = { error: true }; continue; }
    const sqlRow = s.rows![0] as Record<string, number | null>;
    const daxRow = Object.fromEntries(Object.entries(d.rows![0]).map(([k, v]) => [k.replace(/^\[|\]$/g, ""), v])) as Record<string, number | null>;
    const refresh = await datasetRefreshInfo(ent).catch((e) => ({ error: String(e) }));
    const cmp: Record<string, unknown> = {};
    for (const k of Object.keys(sqlRow)) {
      const a = sqlRow[k] === null ? null : Number(sqlRow[k]);
      const b = daxRow[k] === null || daxRow[k] === undefined ? null : Number(daxRow[k]);
      const exact = a !== null && b !== null && Math.abs(a - b) < (Number.isInteger(a) && Number.isInteger(b) ? 0.5 : 1e-6 * Math.max(1, Math.abs(b)));
      const rel = a !== null && b !== null && b !== 0 ? (a - b) / Math.abs(b) : null;
      cmp[k] = { sql: a, dax: b, exact, rel_diff: rel };
      console.log(`${ent} ${k.padEnd(18)} ${exact ? "EXACT" : "DIFF "} ${rel === null ? "" : `rel ${(rel * 100).toFixed(3)}%`}`);
    }
    out[ent] = { compare: cmp, dataset_refresh: refresh, dax_latency_ms: d.latencyMs };
  }
  out.sandbox_version = JSON.parse(fs.readFileSync(path.join(POC, ".sandbox", "data_version.json"), "utf8"));
  out.taken_at = new Date().toISOString();
  const dest = path.join(POC, "runs", "plus_parity_dax_20261107.json");
  fs.writeFileSync(dest, JSON.stringify(out, null, 1));
  console.log(`values written to ${dest} (gitignored)`);
}

main().catch((e) => { console.error(e instanceof Error ? e.message : String(e)); process.exitCode = 1; }).finally(() => closePools());
