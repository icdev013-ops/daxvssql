/**
 * query_sql: runs gated SQL against the sandbox MySQL (127.0.0.1:3307) as the franchise's own SELECT-only
 * account poc_<ent>.
 *
 * Row-level security on the bot side (the database side is proven by sandbox/rls_test.py):
 *   1. Only the three franchise accounts poc_icla / poc_icch / poc_icau are ever used; the sandbox
 *      administrator account and its password are never read (env.ts drops that key while parsing).
 *   2. The account comes from the caller-supplied franchise, validated against ICLA / ICCH / ICAU. Model output
 *      and tool arguments never choose it: query_sql reads only its `sql` argument.
 *   3. One connection pool per franchise, keyed by franchise, created with that franchise's user and default
 *      database. A pooled connection is never re-pointed (no changeUser, and the gate rejects USE / SET).
 *   4. The gate's allowlist is that franchise's view names only (read through its own account); unqualified
 *      names resolve to poc_<ent>; any schema-qualified reference to another schema is rejected before execution.
 *
 * Every new pooled connection gets, before its first query:
 *   SET SESSION max_execution_time = 10000           (10 s; the server-wide cap is 15 s)
 *   SET SESSION TRANSACTION READ ONLY
 *   sql_mode + ANSI_QUOTES                           ("x" is an identifier: loud error instead of a silent 0)
 *   SET SESSION sql_select_limit = 51                (detects truncation without wrapping the query, because
 *                                                     MySQL may drop an ORDER BY inside a derived table)
 * and multipleStatements is off. The gate forbids SET and variables, so a query cannot undo these.
 * Results come back in production's DaxResult shape (QueryResult), so the formatter, totals and guards are shared.
 */
import fs from "node:fs";
import mysql from "mysql2/promise";
import { hasSandboxPassword, sandboxPassword } from "../env";
import { assertFranchise, type Franchise } from "../franchise";
import { MAX_ROWS_RETURNED, type QueryResult } from "../format";
import { SANDBOX_DATA_VERSION } from "../paths";
import { gateSql, type GateRejectCode } from "./sqlGate";

export const SANDBOX_HOST = "127.0.0.1";
export const SANDBOX_PORT = 3307;
const SELECT_LIMIT = MAX_ROWS_RETURNED + 1;
const STATEMENT_TIMEOUT_MS = 10_000;
/** The franchise accounts allow 4 connections each (MAX_USER_CONNECTIONS); the bot uses at most 2. */
const POOL_SIZE = 2;

export const schemaFor = (entity: string) => `poc_${assertFranchise(entity).toLowerCase()}`;

export const SESSION_SETUP = [
  `SET SESSION max_execution_time = ${STATEMENT_TIMEOUT_MS}`,
  "SET SESSION TRANSACTION READ ONLY",
  "SET SESSION sql_mode = CONCAT_WS(',', NULLIF(@@SESSION.sql_mode, ''), 'ANSI_QUOTES')",
  `SET SESSION sql_select_limit = ${SELECT_LIMIT}`,
];

export interface SqlExecution extends QueryResult {
  /** The SQL that ran (gate-normalised: double quotes -> backticks, trailing ; removed). */
  executed: string;
  gate: { ok: boolean; code?: GateRejectCode; reason?: string; tables?: string[] };
  mysqlErrorCode?: string;
  franchise: Franchise;
}

const pools = new Map<Franchise, mysql.Pool>();

/** The franchise's pool (created on first use). Exported for the RLS tests. */
export function poolFor(entity: string): mysql.Pool {
  const ent = assertFranchise(entity);
  let pool = pools.get(ent);
  if (!pool) {
    pool = mysql.createPool({
      host: SANDBOX_HOST,
      port: SANDBOX_PORT,
      user: schemaFor(ent),
      password: sandboxPassword(ent),
      database: schemaFor(ent),
      multipleStatements: false,
      connectionLimit: POOL_SIZE,
      maxIdle: POOL_SIZE,
      waitForConnections: true,
      queueLimit: 50,
      dateStrings: true,            // dates as strings, like the DAX JSON's ISO strings
      decimalNumbers: true,         // DECIMAL sums as numbers, so column totals work
      supportBigNumbers: true,
      bigNumberStrings: false,
      connectTimeout: 10_000,
    });
    // Session settings on every new physical connection. Commands on one connection run in order, so these
    // complete before the connection's first query; if one fails, the connection is destroyed (fail closed).
    (pool as unknown as { pool: { on(ev: string, fn: (c: CoreConn) => void): void } }).pool.on("connection", (conn) => {
      for (const sql of SESSION_SETUP) {
        conn.query(sql, (err: unknown) => { if (err) conn.destroy(); });
      }
    });
    pools.set(ent, pool);
  }
  return pool;
}
type CoreConn = { query(sql: string, cb: (err: unknown) => void): void; destroy(): void };

export async function closePools(): Promise<void> {
  const all = [...pools.values()];
  pools.clear();
  viewCache.clear();
  await Promise.all(all.map((p) => p.end().catch(() => undefined)));
}

/** The franchise's view names, read once per process through its own account (no view definitions needed). */
const viewCache = new Map<Franchise, Promise<string[]>>();
export function franchiseViews(entity: string): Promise<string[]> {
  const ent = assertFranchise(entity);
  let p = viewCache.get(ent);
  if (!p) {
    p = (async () => {
      const [rows] = await poolFor(ent).query(
        "SELECT TABLE_NAME AS name FROM information_schema.TABLES WHERE TABLE_SCHEMA = ? AND TABLE_TYPE = 'VIEW'",
        [schemaFor(ent)]);
      const names = (rows as { name: string }[]).map((r) => r.name);
      if (!names.length) throw new Error(`no views visible in ${schemaFor(ent)}`);
      return names;
    })();
    p.catch(() => viewCache.delete(ent));
    viewCache.set(ent, p);
  }
  return p;
}

export function sqlLiveAvailable(entity: string): boolean {
  return hasSandboxPassword(entity);
}

/**
 * Gate, then run `sqlIn` as `entity`'s account. `entity` must come from the caller (CLI / harness), never from
 * the model.
 */
export async function executeSql(entity: string, sqlIn: string): Promise<SqlExecution> {
  const ent = assertFranchise(entity);
  const started = Date.now();
  let views: string[];
  try {
    views = await franchiseViews(ent);
  } catch (err) {
    return {
      error: `Live data unavailable: could not reach the sandbox database (${err instanceof Error ? err.message : String(err)})`,
      latencyMs: Date.now() - started, executed: sqlIn, gate: { ok: false, reason: "sandbox unreachable" }, franchise: ent,
    };
  }
  const gate = gateSql(sqlIn, { schema: schemaFor(ent), views });
  if (!gate.ok) {
    return {
      error: `Query rejected by the safety gate: ${gate.reason}. Only one read-only SELECT (or WITH ... SELECT) over this franchise's views is allowed.`,
      latencyMs: Date.now() - started, executed: sqlIn, gate: { ok: false, code: gate.code, reason: gate.reason }, franchise: ent,
    };
  }

  const t0 = Date.now();
  try {
    const [rowsRaw, fields] = await poolFor(ent).query({ sql: gate.sql, rowsAsArray: true });
    const latencyMs = Date.now() - t0;
    const fieldList = (fields ?? []) as { name: string }[];
    const columns: string[] = [];
    for (const f of fieldList) {   // unique names: duplicate aliases would otherwise overwrite each other
      let name = f.name, k = 2;
      while (columns.includes(name)) name = `${f.name} (${k++})`;
      columns.push(name);
    }
    const arrays = (Array.isArray(rowsRaw) ? rowsRaw : []) as unknown[][];
    const all = arrays.map((arr) => Object.fromEntries(columns.map((c, i) => [c, normalise(arr[i])])));
    const rowCount = all.length;
    const truncated = rowCount > MAX_ROWS_RETURNED;
    const rows = truncated ? all.slice(0, MAX_ROWS_RETURNED) : all;
    return {
      columns: rows.length ? columns : [], rows, rowCount, truncated,
      // sql_select_limit stops the top-level result at 51 rows; an explicit LIMIT overrides it (count exact then).
      rowCountIsLowerBound: truncated && rowCount === SELECT_LIMIT,
      latencyMs, executed: gate.sql, gate: { ok: true, tables: gate.tables }, franchise: ent,
    };
  } catch (err) {
    const e = err as { code?: string; errno?: number; sqlMessage?: string; message?: string };
    const msg = e.sqlMessage ?? e.message ?? String(err);
    const timedOut = e.errno === 3024 || /maximum statement execution time exceeded/i.test(msg);
    return {
      error: timedOut
        ? "Query timed out (10s) — simplify the SQL, narrow the date range, or filter to fewer rows."
        : `MySQL returned ${e.code ?? "an error"}${e.errno ? ` (${e.errno})` : ""}: ${msg.slice(0, 600)}`,
      latencyMs: Date.now() - t0, executed: gate.sql, gate: { ok: true, tables: gate.tables }, mysqlErrorCode: e.code,
      franchise: ent,
    };
  }
}

function normalise(v: unknown): unknown {
  if (typeof v === "bigint") return Number(v);
  if (Buffer.isBuffer(v)) return v.length === 1 ? v[0] : v.toString("utf8");   // BIT(1) -> 0/1
  return v;
}

/** Data version for Route B turns: the sandbox's dump/restore stamp. */
export function sandboxDataVersion(): Record<string, unknown> {
  try {
    return { source: "sandbox_data_version", ...JSON.parse(fs.readFileSync(SANDBOX_DATA_VERSION, "utf8")) };
  } catch (err) {
    return { source: "sandbox_data_version", error: err instanceof Error ? err.message : String(err) };
  }
}
