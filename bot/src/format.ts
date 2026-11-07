/**
 * Query result shape + the result formatter the model reads.
 *
 * Both tools return QueryResult (production's DaxResult shape from lib/copilot/pbi-dax.ts), so the 50-row cap,
 * truncation flag, server-computed column totals, blank = 0 rule and the table/viz block are shared by every arm.
 *
 * - DAX arms call the vendored production formatDaxResultForModel itself (see tools/dax.ts), unchanged.
 * - The SQL arm calls formatSqlResultForModel below: a port of the same function (table, footer, blank-scalar
 *   message and column totals are identical; test/format.test.ts checks that against the vendored function).
 *   Only the two DAX-only hints in the "No rows returned." message are bound to SQL (LIKE instead of
 *   CONTAINSSTRING, SCHEMA instead of SEMANTIC MODEL, plus the ANSI_QUOTES hint), and the footer says
 *   "more than N" when the true row count is unknown (MySQL's sql_select_limit stops at 51 rows).
 *   The production example name in the no-rows hint is replaced by a generic one (no names in committed files).
 */

export const MAX_ROWS_RETURNED = 50;

export interface QueryResult {
  columns?: string[];
  rows?: Record<string, unknown>[];
  rowCount?: number;
  truncated?: boolean;
  /** SQL only: rowCount is a lower bound (the server stopped at the select limit). */
  rowCountIsLowerBound?: boolean;
  error?: string;
  latencyMs: number;
}

export function formatSqlResultForModel(result: QueryResult): string {
  if (result.error) {
    return `Error: ${result.error}`;
  }

  const rows = result.rows ?? [];
  const cols = result.columns ?? [];

  if (rows.length === 0) {
    return [
      "No rows returned.",
      "Common causes:",
      "  - Filter value doesn't match any data exactly (e.g. a first name only vs the designer's full name).",
      "    For partial / fuzzy matches use `column` LIKE '%text%'.",
      "  - Date filter excludes all rows (current year may have no data yet — verify the year first).",
      "  - A double-quoted value is read as a column name (ANSI_QUOTES is on): string values take single quotes.",
      "  - Column or view name typo. Check the SCHEMA section.",
    ].join("\n");
  }

  // Blank scalar: single row of all-null values (same text as production).
  if (rows.length === 1 && cols.every((c) => rows[0][c] === null || rows[0][c] === undefined)) {
    return [
      "Result was BLANK (all values null/empty).",
      "For a count or a sum this means ZERO: no rows match the filter. Answer 0 and name the filter you used.",
      "Only if the filter names a designer, lead source or other value that may be misspelled (it is not in the",
      "BRANCH SNAPSHOT), check that name with a broader query before answering.",
    ].join("\n");
  }

  const header = "| " + cols.join(" | ") + " |";
  const sep = "| " + cols.map(() => "---").join(" | ") + " |";
  const body = rows.map((r) =>
    "| " + cols.map((c) => formatCell(r[c])).join(" | ") + " |",
  ).join("\n");

  const footer = result.truncated
    ? result.rowCountIsLowerBound
      ? `\n\n_(showing first ${rows.length} of more than ${rows.length} rows — truncated)_`
      : `\n\n_(showing first ${rows.length} of ${result.rowCount} rows — truncated)_`
    : `\n\n_(${rows.length} row${rows.length === 1 ? "" : "s"})_`;

  return `${header}\n${sep}\n${body}${footer}${columnTotals(cols, rows, !!result.truncated)}`;
}

// Production 2026-11-06: the server sums additive columns exactly and hands the totals over.
const NON_ADDITIVE = /%|rate|percent|pct|share|avg|average|mean|median|ratio|year|month|week|day|date|rank|\bid\b|key/i;
function columnTotals(cols: string[], rows: Record<string, unknown>[], truncated: boolean): string {
  if (rows.length < 2) return "";
  const parts: string[] = [];
  for (const c of cols) {
    const label = c.replace(/^[^[]*\[/, "").replace(/\]$/, "");
    if (NON_ADDITIVE.test(label)) continue;
    let sum = 0, n = 0, numeric = true;
    for (const r of rows) {
      const v = r[c];
      if (v === null || v === undefined) continue;
      if (typeof v !== "number") { numeric = false; break; }
      sum += v; n++;
    }
    if (!numeric || n < 2) continue;
    parts.push(`${label} = ${Number.isInteger(sum) ? String(sum) : sum.toFixed(2)}`);
  }
  if (!parts.length) return "";
  return `\nColumn totals${truncated ? " of the rows shown" : ""} (computed exactly by the server; use these and never add numbers up yourself): ${parts.join("; ")}`;
}

function formatCell(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "number") {
    return Number.isInteger(v) ? String(v) : v.toString();
  }
  return String(v).replace(/\|/g, "\\|");
}

/** route.ts deterministicResultText: last-resort zero-LLM wording for the empty-answer guard. */
export function deterministicResultText(result: QueryResult): string {
  const rows = result.rows ?? [];
  const cols = result.columns ?? [];
  if (rows.length === 0) return "";
  if (rows.length === 1) {
    const row = rows[0];
    const parts = cols.map((c) => `**${c}:** ${String(row[c] ?? "—")}`);
    return "Here's what I found — " + parts.join(" · ") + ".";
  }
  return `Here are the ${rows.length} matching results, shown in the table below.`;
}

/** route.ts viz block: emitted when a result has 2+ rows, or 1 row with 2+ columns. */
export function vizBlock(
  result: QueryResult | undefined,
  opts: { question: string; branch: string; chartOverride?: string; recipeId?: string; fastPath?: boolean },
): string {
  if (!result || !result.rows || !result.columns) return "";
  // Fast path uses rowCount ?? 0; slow path uses rowCount ?? rows.length (both as in route.ts).
  const rc = opts.fastPath ? (result.rowCount ?? 0) : (result.rowCount ?? result.rows.length);
  const cc = result.columns.length;
  const isTabular = rc >= 2 || (rc === 1 && cc >= 2);
  if (!isTabular) return "";
  const viz = {
    type: "table" as const,
    ...(opts.recipeId ? { recipeId: opts.recipeId } : {}),
    title: opts.question.slice(0, 120),
    columns: result.columns,
    data: result.rows,
    branch: opts.branch,
    rowCount: rc,
    ...(opts.chartOverride ? { chartOverride: opts.chartOverride } : {}),
  };
  return "\n\n```viz\n" + JSON.stringify(viz) + "\n```\n";
}
