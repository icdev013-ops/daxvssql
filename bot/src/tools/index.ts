/**
 * Pluggable query tools. Both mirror production lib/copilot/tools.ts dispatchTool:
 *   counters -> empty-query short-circuit -> {YEAR}/{PREV_YEAR}/{MONTH} repair (anchor date, not the clock)
 *   -> the arm's language guard -> execute -> query log entry -> last-result slot (viz block) -> formatted text.
 * Language guards (each arm keeps what it would have in production):
 *   query_dax: production's period-window guard (vendored) and closeOpenParens when the vendored code has it;
 *   query_sql: the SQL gate (inside executeSql) and ANSI_QUOTES.
 */
import type { Anchor } from "../context";
import { substitutePlaceholders } from "../context";
import { formatSqlResultForModel, type QueryResult } from "../format";
import type { VendorBundle } from "../vendor";
import { daxLiveAvailable, executeDax } from "./dax";
import { executeSql, sqlLiveAvailable } from "./sql";

export type Dialect = "dax" | "sql";

export interface QueryLogEntry {
  round: number;
  dialect: Dialect;
  /** As the model wrote it. */
  query: string;
  /** What actually ran (after placeholder repair, closeOpenParens or gate normalisation). */
  executed?: string;
  ok: boolean;
  row_count?: number;
  truncated?: boolean;
  error?: string;
  latency_ms: number;
  throttle_wait_ms?: number;
  placeholders_substituted?: boolean;
  /** Guard that stopped the query before execution (period-window guard / SQL gate). */
  blocked_by?: string;
  gate?: unknown;
  columns?: string[];
  /** Raw rows as returned (already capped at 50). */
  rows?: Record<string, unknown>[];
  /** Raw response body for DAX (capped). */
  raw?: string;
  recovered_from_malformed?: boolean;
}

export interface ToolContext {
  branch: string;
  anchor: Anchor;
  round: number;
  counters: { toolCallsCount: number; queriesCount: number };
  log: QueryLogEntry[];
  lastResult: { result?: QueryResult };
}

export interface QueryTool {
  name: "query_dax" | "query_sql";
  dialect: Dialect;
  /** Argument holding the query text. */
  argName: "dax" | "sql";
  definitions: readonly unknown[];
  liveAvailable(branch: string): boolean;
  dispatch(name: string, args: Record<string, unknown>, ctx: ToolContext): Promise<string>;
}

function unknownTool(name: string, ctx: ToolContext): string {
  ctx.counters.toolCallsCount += 1;
  return JSON.stringify({ error: `Unknown tool: ${name}` });
}

export function daxTool(vendor: VendorBundle): QueryTool {
  return {
    name: "query_dax",
    dialect: "dax",
    argName: "dax",
    definitions: vendor.COPILOT_TOOLS,
    liveAvailable: daxLiveAvailable,
    async dispatch(name, args, ctx) {
      if (name !== "query_dax") return unknownTool(name, ctx);
      ctx.counters.toolCallsCount += 1;
      ctx.counters.queriesCount += 1;
      const dax = typeof args.dax === "string" ? args.dax : "";
      if (!dax.trim()) {
        ctx.log.push({ round: ctx.round, dialect: "dax", query: "", ok: false, latency_ms: 0,
          error: "Empty DAX query (model emitted tool call without `dax` content)" });
        return [
          "Error: Your tool call had an empty `dax` parameter.",
          "You must write the complete EVALUATE query INSIDE the tool call's `dax` argument.",
          "Re-call query_dax now with the actual DAX query as the `dax` value.",
          "Do not respond with text — re-call the tool with valid DAX.",
        ].join(" ");
      }
      const { text: finalDax, substituted } = substitutePlaceholders(dax, ctx.anchor);
      const periodGuard = vendor.periodMeasureWindowError(finalDax);
      if (periodGuard) {
        ctx.log.push({ round: ctx.round, dialect: "dax", query: dax, executed: finalDax, ok: false, error: periodGuard,
          latency_ms: 0, placeholders_substituted: substituted, blocked_by: "period_window_guard" });
        return periodGuard;
      }
      const r = await executeDax(ctx.branch, finalDax, { closeOpenParens: vendor.closeOpenParens });
      ctx.log.push({
        round: ctx.round, dialect: "dax", query: dax, executed: r.executed, ok: !r.error, row_count: r.rowCount,
        truncated: r.truncated, error: r.error, latency_ms: r.latencyMs, throttle_wait_ms: r.throttleWaitMs,
        placeholders_substituted: substituted, columns: r.columns, rows: r.rows, raw: r.raw,
      });
      if (!r.error && r.rows && r.rows.length > 0) ctx.lastResult.result = r;
      return vendor.formatDaxResultForModel(r);
    },
  };
}

/** SQL twin of production's query_dax definition: same structure, the five shapes translated, no names. */
export const SQL_TOOL_DEFINITIONS = [
  {
    type: "function",
    function: {
      name: "query_sql",
      description: [
        "Run a read-only SQL query (MySQL 8) against the active franchise's database views.",
        "Result is returned as a markdown table (or 'Error: ...' / 'No rows returned.' / 'Result was BLANK').",
        "",
        "CRITICAL: Never call this tool without SQL content in `sql`. Only emit a tool call AFTER you've",
        "written the complete SELECT statement. Empty `sql` wastes a round and triggers an immediate",
        "error — write the SQL first, THEN call the tool.",
        "",
        "BEFORE WRITING: use view and column names EXACTLY as they appear in the SCHEMA section.",
        "",
        "COMMON SHAPES (copy these):",
        "  Scalar:  SELECT SUM(CASE WHEN `Revised Stage` = 'Sold' THEN `Amount` END) AS `Sold` FROM Salesforce_Data",
        "  Grouped: SELECT `Designer`, SUM(CASE WHEN `Revised Stage` = 'Sold' THEN `Amount` END) AS `Sold` FROM Salesforce_Data GROUP BY `Designer`",
        "  Top N:   SELECT ... GROUP BY ... ORDER BY `Sold` DESC LIMIT 10",
        "  Filter:  SELECT ... FROM Salesforce_Data WHERE `Revised Stage` = 'Sold' GROUP BY ...",
        "  Year:    YEAR(`Created Date`) = 2026",
        "  Fuzzy match (use this for loose user input like first-name only):",
        "    `Designer` LIKE '%<first name>%'",
        "",
        "If the result is 'No rows returned.' or 'Result was BLANK', the filter probably didn't match any data —",
        "broaden it (try LIKE '%text%' for names, drop the date filter to verify data exists, etc.)",
      ].join("\n"),
      parameters: {
        type: "object",
        required: ["sql"],
        additionalProperties: false,
        properties: {
          sql: {
            type: "string",
            minLength: 10,
            description:
              "REQUIRED — must contain ONE complete MySQL SELECT (or WITH ... SELECT) statement. Never empty. Backtick-quote view and column names that contain spaces or are reserved words (e.g. `Revised Stage`, `Lead Milestones`). String values take single quotes ('Sold'); double quotes mean an identifier. No semicolons between statements: one statement only.",
          },
        },
      },
    },
  },
] as const;

export function sqlTool(): QueryTool {
  return {
    name: "query_sql",
    dialect: "sql",
    argName: "sql",
    definitions: SQL_TOOL_DEFINITIONS,
    liveAvailable: sqlLiveAvailable,
    async dispatch(name, args, ctx) {
      if (name !== "query_sql") return unknownTool(name, ctx);
      ctx.counters.toolCallsCount += 1;
      ctx.counters.queriesCount += 1;
      const sql = typeof args.sql === "string" ? args.sql : "";
      if (!sql.trim()) {
        ctx.log.push({ round: ctx.round, dialect: "sql", query: "", ok: false, latency_ms: 0,
          error: "Empty SQL query (model emitted tool call without `sql` content)" });
        return [
          "Error: Your tool call had an empty `sql` parameter.",
          "You must write the complete SELECT query INSIDE the tool call's `sql` argument.",
          "Re-call query_sql now with the actual SQL query as the `sql` value.",
          "Do not respond with text — re-call the tool with valid SQL.",
        ].join(" ");
      }
      const { text: finalSql, substituted } = substitutePlaceholders(sql, ctx.anchor);
      const r = await executeSql(ctx.branch, finalSql);
      ctx.log.push({
        round: ctx.round, dialect: "sql", query: sql, executed: r.executed, ok: !r.error, row_count: r.rowCount,
        truncated: r.truncated, error: r.error, latency_ms: r.latencyMs, placeholders_substituted: substituted,
        blocked_by: r.gate.ok ? undefined : "sql_gate", gate: r.gate, columns: r.columns, rows: r.rows,
      });
      if (!r.error && r.rows && r.rows.length > 0) ctx.lastResult.result = r;
      return formatSqlResultForModel(r);
    },
  };
}
