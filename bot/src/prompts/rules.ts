/**
 * Shared business-rules document, injected identically into arm A and arm B (A0 never gets it):
 *   A: daxvssql/rules/business_rules.md + rules/dax_bindings.md
 *   B: daxvssql/rules/business_rules.md + rules/sql_bindings.md
 * appended at the same place in both arms' round-0 and round-1+ system prompts, after the arm's own prompt.
 *
 * Placeholders are filled the same way for both arms (only the syntax of the value differs):
 *   {A}            anchor date: DAX `DATE(yyyy, m, d)`, SQL `DATE 'yyyy-mm-dd'`
 *   {Y} / {M}      anchor year / month number
 *   {SALES_DATE}   per franchise (ICLA created, ICAU and ICCH sold): DAX `Salesforce_Data[...]`, SQL `` `...` ``
 *   {SALES_CAL}    DateTable (ICLA) / SoldDateTable (ICCH, ICAU)
 *   {ICLA_EXCLUDED_STAFF_DAX} / {ICLA_EXCLUDED_STAFF_SQL} / {ICLA_EXCLUDED_STAFF}
 *                  read at runtime from daxvssql/private/rules_names.md (gitignored). The names never enter a
 *                  committed file; they only reach the prompt.
 */
import fs from "node:fs";
import path from "node:path";
import type { Anchor } from "../context";
import { SALES_BASIS } from "../context";
import { POC_DIR } from "../paths";
import { sha256 } from "../util";

export const DEFAULT_RULES_DIR = path.join(POC_DIR, "rules");
export const PRIVATE_NAMES_FILE = path.join(POC_DIR, "private", "rules_names.md");

export type RulesDialect = "dax" | "sql";
interface Doc { path: string; text: string; sha256: string }

export interface RulesTemplates {
  business: Doc;
  bindings: Record<RulesDialect, Doc>;
  /** Names for {ICLA_EXCLUDED_STAFF*}; null when the private file is missing. */
  staff: string[] | null;
  staffSha256: string | null;
}

export interface RenderedRules { text: string; sha256: string; unfilled: string[]; dialect: RulesDialect }

function readDoc(p: string): Doc {
  const text = fs.readFileSync(p, "utf8").replace(/\r\n/g, "\n").trim();
  return { path: p, text, sha256: sha256(text) };
}

/** The numbered list under the "## `{ICLA_EXCLUDED_STAFF}`" heading of the private names file. */
export function parseStaffList(text: string): string[] {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const start = lines.findIndex((l) => /^##\s+`?\{ICLA_EXCLUDED_STAFF\}`?/.test(l));
  if (start < 0) return [];
  const out: string[] = [];
  for (const l of lines.slice(start + 1)) {
    if (/^##\s/.test(l)) break;
    const m = /^\d+\.\s+(.+?)\s*$/.exec(l);
    if (m) out.push(m[1]);
  }
  return out;
}

export function loadRulesTemplates(dir: string = DEFAULT_RULES_DIR, namesFile: string = PRIVATE_NAMES_FILE): RulesTemplates {
  let staff: string[] | null = null;
  let staffSha: string | null = null;
  if (fs.existsSync(namesFile)) {
    const parsed = parseStaffList(fs.readFileSync(namesFile, "utf8"));
    if (parsed.length) { staff = parsed; staffSha = sha256(parsed.join("\n")); }
  }
  return {
    business: readDoc(path.join(dir, "business_rules.md")),
    bindings: { dax: readDoc(path.join(dir, "dax_bindings.md")), sql: readDoc(path.join(dir, "sql_bindings.md")) },
    staff,
    staffSha256: staffSha,
  };
}

export function renderRules(t: RulesTemplates, dialect: RulesDialect, franchise: string, anchor: Anchor): RenderedRules {
  const sold = (SALES_BASIS[franchise] ?? "created") === "sold";
  const values: Record<string, string | null> = {
    A: dialect === "dax"
      ? `DATE(${anchor.year}, ${anchor.month}, ${anchor.day})`
      : `DATE '${anchor.iso}'`,
    Y: String(anchor.year),
    M: String(anchor.month),
    SALES_DATE: dialect === "dax"
      ? (sold ? "Salesforce_Data[Original Project Sold Date]" : "Salesforce_Data[Created Date]")
      : (sold ? "`Original Project Sold Date`" : "`Created Date`"),
    SALES_CAL: sold ? "SoldDateTable" : "DateTable",
    ICLA_EXCLUDED_STAFF_DAX: t.staff ? `{${t.staff.map((n) => `"${n.replace(/"/g, '""')}"`).join(", ")}}` : null,
    ICLA_EXCLUDED_STAFF_SQL: t.staff ? t.staff.map((n) => `'${n.replace(/'/g, "''")}'`).join(", ") : null,
    ICLA_EXCLUDED_STAFF: t.staff ? t.staff.join(", ") : null,
  };
  const raw = `${t.business.text}\n\n${t.bindings[dialect].text}`;
  const text = raw.replace(/\{([A-Z][A-Z_]*)\}/g, (m, key: string) => (key in values && values[key] !== null ? values[key]! : m));
  const unfilled = [...new Set([...text.matchAll(/\{([A-Z][A-Z_]*)\}/g)].map((m) => m[1]).filter((k) => k in values))];
  return { text, sha256: sha256(text), unfilled, dialect };
}

/** The block appended to a fair arm's system prompts. Same wrapper text for A and B. */
export function rulesBlock(r: RenderedRules | null): string {
  if (!r) return "";
  return `\n\n=== SHARED BUSINESS RULES AND ${r.dialect.toUpperCase()} BINDINGS (apply these; where they disagree with anything above, they win) ===\n${r.text}`;
}
