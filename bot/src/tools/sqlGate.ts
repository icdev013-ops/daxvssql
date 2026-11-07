/**
 * SQL gate for query_sql. Pure function, no I/O, unit-tested offline (test/gate.test.ts).
 *
 * Accepts exactly one SELECT (or WITH ... SELECT, UNION of SELECTs) whose every table is a view in the
 * franchise's schema (unqualified names resolve there) or a CTE defined in the same query. Rejects:
 *   - more than one statement (any `;` except trailing ones);
 *   - optimizer-hint comments `/*+` and MySQL executable comments `/*!` (they run code);
 *   - INTO (OUTFILE / DUMPFILE / variables), FOR UPDATE / FOR SHARE / LOCK IN SHARE MODE, PROCEDURE;
 *   - any `@` (user variables, system variables) and `:=` assignment;
 *   - SLEEP, BENCHMARK, GET_LOCK, LOAD_FILE and similar functions (list below), plus identity and
 *     version probes (USER(), DATABASE(), VERSION(), ...);
 *   - information_schema, performance_schema, mysql and sys, and any other schema;
 *   - anything node-sql-parser cannot parse as MySQL (fail closed).
 *
 * The session runs with ANSI_QUOTES, so "x" is an identifier on the server. The gate rewrites every
 * double-quoted identifier to backticks before parsing and returns that text, which is what runs: the gate
 * validates exactly the statement the server executes, and the server reads it the same way it would have
 * read the original.
 *
 * Defence in depth only: the real boundary is the SELECT-only account per franchise schema.
 */
import nodeSqlParser from "node-sql-parser";   // CommonJS package: default import, then destructure

const { Parser } = nodeSqlParser;

export interface GateOptions {
  /** e.g. "poc_icla" */
  schema: string;
  /** View names in that schema (any case). */
  views: Iterable<string>;
  maxLength?: number;
}

export type GateResult =
  | { ok: true; sql: string; tables: string[]; ctes: string[] }
  | { ok: false; reason: string; code: GateRejectCode };

export type GateRejectCode =
  | "empty" | "too_long" | "lex" | "hint_comment" | "executable_comment" | "multi_statement" | "variable"
  | "into" | "locking" | "procedure" | "function" | "system_schema" | "parse" | "not_select" | "table";

const DENY_FUNCTIONS = [
  "SLEEP", "BENCHMARK", "GET_LOCK", "RELEASE_LOCK", "RELEASE_ALL_LOCKS", "IS_FREE_LOCK", "IS_USED_LOCK",
  "LOAD_FILE", "MASTER_POS_WAIT", "SOURCE_POS_WAIT", "WAIT_FOR_EXECUTED_GTID_SET",
  "WAIT_UNTIL_SQL_THREAD_AFTER_GTIDS", "SYS_EXEC", "SYS_EVAL", "SYS_GET", "SYS_SET", "EXTRACTVALUE", "UPDATEXML",
  "DATABASE", "SCHEMA", "USER", "CURRENT_USER", "SESSION_USER", "SYSTEM_USER", "CURRENT_ROLE", "VERSION",
  "CONNECTION_ID", "PS_CURRENT_THREAD_ID", "PS_THREAD_ID", "LAST_INSERT_ID", "ROW_COUNT", "FOUND_ROWS",
  "ROLES_GRAPHML", "STATEMENT_DIGEST", "STATEMENT_DIGEST_TEXT", "GTID_SUBSET", "GTID_SUBTRACT", "RANDOM_BYTES",
];
// Function-call form, optionally backticked/double-quoted name: SLEEP(1), `sleep` (1).
const DENY_CALL_RE = new RegExp(`(^|[^\\w$.])[\`"]?(${DENY_FUNCTIONS.join("|")}|INTERNAL_\\w+)[\`"]?\\s*\\(`, "i");
// Niladic forms that need no parentheses.
const DENY_BARE_RE = /\b(CURRENT_USER|SESSION_USER|SYSTEM_USER|CURRENT_ROLE)\b/i;
const SYSTEM_SCHEMA_RE = /\b(information_schema|performance_schema|mysql|sys)\b\s*\./i;
const SYSTEM_SCHEMA_ANY_RE = /\b(information_schema|performance_schema)\b/i;

interface Lexed {
  /** SQL with "double-quoted" identifiers rewritten as `backtick` identifiers; comments kept. */
  normalized: string;
  /** Code only: string literals -> '', comments -> ' ', identifiers kept (backticked). */
  code: string;
  /** Code only with identifiers blanked too: for keyword checks (a column named `Lock` must not trip them). */
  keywords: string;
  /** Offsets (in `code`) of every `;`. */
  semicolons: number[];
}

type LexError = { error: string; code: GateRejectCode };

function lex(sql: string): Lexed | LexError {
  let normalized = "", code = "", keywords = "";
  const semicolons: number[] = [];
  const n = sql.length;
  let i = 0;
  while (i < n) {
    const c = sql[i];
    const next = sql[i + 1];
    // single-quoted string: backslash escapes and '' doubling (MySQL default, no NO_BACKSLASH_ESCAPES)
    if (c === "'") {
      let j = i + 1;
      for (;;) {
        if (j >= n) return { error: "unterminated string literal", code: "lex" };
        if (sql[j] === "\\") { j += 2; continue; }
        if (sql[j] === "'") { if (sql[j + 1] === "'") { j += 2; continue; } break; }
        j++;
      }
      normalized += sql.slice(i, j + 1);
      code += "''"; keywords += "''";
      i = j + 1;
      continue;
    }
    // "double-quoted" identifier (ANSI_QUOTES) -> backticks
    if (c === '"') {
      let j = i + 1, ident = "";
      for (;;) {
        if (j >= n) return { error: "unterminated double-quoted identifier", code: "lex" };
        if (sql[j] === '"') { if (sql[j + 1] === '"') { ident += '"'; j += 2; continue; } break; }
        ident += sql[j]; j++;
      }
      const bt = "`" + ident.replace(/`/g, "``") + "`";
      normalized += bt; code += bt; keywords += "`x`";
      i = j + 1;
      continue;
    }
    // `backtick` identifier
    if (c === "`") {
      let j = i + 1;
      for (;;) {
        if (j >= n) return { error: "unterminated backtick identifier", code: "lex" };
        if (sql[j] === "`") { if (sql[j + 1] === "`") { j += 2; continue; } break; }
        j++;
      }
      const lit = sql.slice(i, j + 1);
      normalized += lit; code += lit; keywords += "`x`";
      i = j + 1;
      continue;
    }
    // comments
    if (c === "/" && next === "*") {
      if (sql[i + 2] === "+") return { error: "optimizer-hint comments (/*+ ... */) are not allowed", code: "hint_comment" };
      if (sql[i + 2] === "!") return { error: "MySQL executable comments (/*! ... */) are not allowed", code: "executable_comment" };
      const end = sql.indexOf("*/", i + 2);
      if (end < 0) return { error: "unterminated comment", code: "lex" };
      normalized += sql.slice(i, end + 2); code += " "; keywords += " ";
      i = end + 2;
      continue;
    }
    if ((c === "-" && next === "-" && (i + 2 >= n || /\s/.test(sql[i + 2]))) || c === "#") {
      const end = sql.indexOf("\n", i);
      const stop = end < 0 ? n : end;
      normalized += sql.slice(i, stop); code += " "; keywords += " ";
      i = stop;
      continue;
    }
    if (c === ";") semicolons.push(code.length);
    normalized += c; code += c; keywords += c;
    i++;
  }
  return { normalized, code, keywords, semicolons };
}

/** Walk every object in the AST. */
function walk(node: unknown, visit: (o: Record<string, unknown>) => void, seen = new Set<unknown>()): void {
  if (!node || typeof node !== "object" || seen.has(node)) return;
  seen.add(node);
  if (Array.isArray(node)) { for (const x of node) walk(x, visit, seen); return; }
  visit(node as Record<string, unknown>);
  for (const v of Object.values(node as Record<string, unknown>)) walk(v, visit, seen);
}

const parser = new Parser();
const reject = (code: GateRejectCode, reason: string): GateResult => ({ ok: false, code, reason });

export function gateSql(input: unknown, opts: GateOptions): GateResult {
  if (typeof input !== "string" || !input.trim()) return reject("empty", "the query is empty");
  const maxLength = opts.maxLength ?? 20_000;
  if (input.length > maxLength) return reject("too_long", `the query is longer than ${maxLength} characters`);

  const lexed = lex(input);
  if ("error" in lexed) return reject(lexed.code, lexed.error);

  // One statement: semicolons only at the very end (followed by nothing but whitespace or other trailing ;).
  const codeTrim = lexed.code.replace(/[\s;]+$/, "");
  if (lexed.semicolons.some((pos) => pos < codeTrim.length)) {
    return reject("multi_statement", "only one statement is allowed (remove the ; separators)");
  }
  const sql = lexed.normalized.replace(/[\s;]+$/, "");

  const kw = lexed.keywords;
  if (/@/.test(kw)) return reject("variable", "user or system variables (@...) are not allowed");
  if (/:=/.test(kw)) return reject("variable", "variable assignment (:=) is not allowed");
  if (/\bINTO\b/i.test(kw)) return reject("into", "SELECT ... INTO (OUTFILE, DUMPFILE or variables) is not allowed");
  if (/\bFOR\s+(UPDATE|SHARE)\b|\bLOCK\b/i.test(kw)) return reject("locking", "locking reads (FOR UPDATE / FOR SHARE / LOCK IN SHARE MODE) are not allowed");
  if (/\bPROCEDURE\b/i.test(kw)) return reject("procedure", "PROCEDURE clauses are not allowed");
  const fn = lexed.code.match(DENY_CALL_RE) ?? kw.match(DENY_BARE_RE);
  if (fn) return reject("function", `the function ${(fn[2] ?? fn[1]).toUpperCase()} is not allowed`);
  const plainCode = lexed.code.replace(/`/g, "");
  const sys = plainCode.match(SYSTEM_SCHEMA_RE) ?? plainCode.match(SYSTEM_SCHEMA_ANY_RE);
  if (sys) return reject("system_schema", `system schema ${sys[1].toLowerCase()} is not accessible`);

  let ast: unknown;
  try {
    ast = parser.astify(sql, { database: "MySQL" });
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    return reject("parse", `the gate could not parse this as one MySQL 8 SELECT (${msg.split("\n")[0].slice(0, 160)})`);
  }
  const stmts = Array.isArray(ast) ? ast : [ast];
  if (stmts.length !== 1) return reject("multi_statement", "only one statement is allowed");
  const stmt = stmts[0] as Record<string, unknown>;
  if (stmt?.type !== "select") return reject("not_select", `only SELECT is allowed (got ${String(stmt?.type ?? "unknown").toUpperCase()})`);

  const ctes = new Set<string>();
  let bad: GateResult | null = null;
  walk(stmt, (o) => {
    if (bad) return;
    const into = o.into as Record<string, unknown> | undefined;
    if (into && typeof into === "object" && (into.type || into.expr || into.keyword)) bad = reject("into", "SELECT ... INTO is not allowed");
    if (o.locking_read) bad = reject("locking", "locking reads are not allowed");
    if (o.type && o.type !== "select" && (o.type === "insert" || o.type === "update" || o.type === "delete" || o.type === "replace")) {
      bad = reject("not_select", `${String(o.type).toUpperCase()} is not allowed`);
    }
    if (Array.isArray(o.with)) {
      for (const w of o.with as Array<{ name?: unknown }>) {
        const nm = typeof w.name === "string" ? w.name : (w.name as { value?: string } | undefined)?.value;
        if (nm) ctes.add(nm.toLowerCase());
      }
    }
  });
  if (bad) return bad;

  let list: string[];
  try {
    list = parser.tableList(sql, { database: "MySQL" });
  } catch (e) {
    return reject("parse", `the gate could not list the tables (${(e instanceof Error ? e.message : String(e)).slice(0, 120)})`);
  }
  const schema = opts.schema.toLowerCase();
  const views = new Set([...opts.views].map((v) => v.toLowerCase()));
  const tables: string[] = [];
  for (const entry of list) {
    const [op, dbRaw, tableRaw] = entry.split("::");
    const db = dbRaw === "null" ? null : dbRaw.toLowerCase();
    const table = tableRaw.toLowerCase();
    if (op.toLowerCase() !== "select") return reject("not_select", `${op.toUpperCase()} is not allowed`);
    if (db === null && ctes.has(table)) continue;
    if (db === null && table === "dual") continue;
    if (db !== null && db !== schema) return reject("table", `${dbRaw}.${tableRaw} is outside this franchise's schema`);
    if (!views.has(table)) return reject("table", `${tableRaw} is not one of this franchise's views`);
    tables.push(tableRaw);
  }
  return { ok: true, sql, tables: [...new Set(tables)], ctes: [...ctes] };
}
