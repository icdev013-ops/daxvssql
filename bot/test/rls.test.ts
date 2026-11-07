/**
 * Row-level-security tests for the bot's SQL route.
 *   Offline: static source scan, franchise allowlist, cross-franchise gate cases.
 *   Live (skipped when the sandbox on 127.0.0.1:3307 is not reachable): the database denies a cross-franchise
 *   read when the gate is bypassed; franchises queried in alternating order see only their own rows; tool
 *   arguments cannot switch franchise; pooled connections carry the session settings, user and schema.
 * Run: npm test
 */
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { after, describe, it } from "node:test";
import { parseAnchor } from "../src/context";
import { hasSandboxPassword, retainedSandboxKeys } from "../src/env";
import { assertFranchise, FRANCHISES } from "../src/franchise";
import { BOT_DIR } from "../src/paths";
import { sqlTool, type ToolContext } from "../src/tools/index";
import { closePools, executeSql, franchiseViews, poolFor, schemaFor } from "../src/tools/sql";
import { gateSql } from "../src/tools/sqlGate";

// ---------------------------------------------------------------------------------------------------- static
describe("bot never uses the sandbox administrator account", () => {
  const files: string[] = [];
  const walk = (dir: string) => {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      const p = path.join(dir, e.name);
      if (e.isDirectory()) walk(p);
      else if (/\.(ts|tsx|mjs|js|json)$/.test(e.name)) files.push(p);
    }
  };
  for (const d of ["src", "scripts"]) walk(path.join(BOT_DIR, d));
  files.push(path.join(BOT_DIR, "package.json"));

  it(`scans ${files.length} source files`, () => assert.ok(files.length >= 10));
  for (const f of files) {
    it(`${path.relative(BOT_DIR, f)} has no administrator credential or user reference`, () => {
      const text = fs.readFileSync(f, "utf8");
      assert.doesNotMatch(text, /SANDBOX_ROOT/i, "references the administrator password key");
      assert.doesNotMatch(text, /["'`]root["'`@]/i, "references the administrator user as a string");
      assert.doesNotMatch(text, /user\s*[:=]\s*["'`]?root\b/i, "connects as the administrator user");
    });
  }
  it("keeps only the three franchise passwords from daxvssql/.env", () => {
    const keys = retainedSandboxKeys();
    for (const k of keys) assert.match(k, /^SANDBOX_(ICLA|ICCH|ICAU)_PASSWORD$/);
  });
});

// ---------------------------------------------------------------------------------------------- allowlist
describe("franchise allowlist", () => {
  for (const bad of ["ROOT", "root", "ICDF", "ICSE", "DEMO", "icla", "poc_icla", "", null, undefined, "ICLA; DROP"]) {
    it(`rejects ${JSON.stringify(bad)}`, () => {
      assert.throws(() => assertFranchise(bad));
      assert.throws(() => schemaFor(bad as string));
      assert.equal(hasSandboxPassword(bad as string), false);
    });
  }
  it("accepts ICLA, ICCH, ICAU", () => {
    for (const f of FRANCHISES) assert.equal(assertFranchise(f), f);
    assert.equal(schemaFor("ICAU"), "poc_icau");
  });
  it("rejects a non-franchise before touching the database", async () => {
    await assert.rejects(executeSql("ROOT", "SELECT 1"));
    assert.throws(() => poolFor("ICDF"));
  });
});

// ------------------------------------------------------------------------------------ gate, cross-franchise
describe("gate rejects every other schema (running as ICAU)", () => {
  const icau = { schema: "poc_icau", views: ["Salesforce_Data", "Leads", "Appointments", "dim_entity"] };
  const cases = [
    "SELECT COUNT(*) FROM poc_icla.salesforce_data",
    "SELECT COUNT(*) FROM `poc_icla`.`Salesforce_Data`",
    "SELECT COUNT(*) FROM \"poc_icch\".\"Leads\"",
    "SELECT * FROM Salesforce_Data WHERE `Designer` IN (SELECT `Designer` FROM poc_icla.Salesforce_Data)",
    "SELECT `entity` FROM Salesforce_Data UNION ALL SELECT `entity` FROM poc_icla.Salesforce_Data",
    "WITH x AS (SELECT * FROM poc_icch.Leads) SELECT COUNT(*) FROM x",
    "SELECT COUNT(*) FROM icall_gold.fact_sales",
    "SELECT * FROM information_schema.TABLES",
    "SELECT * FROM mysql.user",
    "SELECT * FROM performance_schema.events_statements_history",
    "SELECT * FROM sys.version",
    "SELECT COUNT(*) FROM fact_sales",
  ];
  for (const sql of cases) {
    it(sql, () => {
      const r = gateSql(sql, icau);
      assert.equal(r.ok, false);
    });
  }
  it("accepts the same view unqualified or qualified with its own schema", () => {
    assert.ok(gateSql("SELECT COUNT(*) FROM salesforce_data", icau).ok);
    assert.ok(gateSql("SELECT COUNT(*) FROM poc_icau.Salesforce_Data", icau).ok);
  });
});

// ----------------------------------------------------------------------------------------------------- live
async function sandboxReachable(): Promise<boolean> {
  if (!FRANCHISES.every((f) => hasSandboxPassword(f))) return false;
  try {
    await Promise.race([franchiseViews("ICAU"), new Promise((_, r) => setTimeout(() => r(new Error("timeout")), 8000))]);
    return true;
  } catch {
    return false;
  }
}
const live = await sandboxReachable();
const skip = live ? false : "sandbox on 127.0.0.1:3307 not reachable (or franchise passwords missing)";

describe("live: database-side isolation and bot-side routing", { skip }, () => {
  after(() => closePools());

  it("cross-franchise read as ICAU: rejected by the gate", async () => {
    const r = await executeSql("ICAU", "SELECT COUNT(*) AS n FROM poc_icla.salesforce_data");
    assert.ok(r.error?.startsWith("Query rejected by the safety gate"), r.error);
    assert.equal(r.gate.ok, false);
  });

  it("cross-franchise read as ICAU with the gate bypassed: denied by MySQL", async () => {
    for (const sql of [
      "SELECT COUNT(*) FROM poc_icla.salesforce_data",
      "SELECT COUNT(*) FROM poc_icch.leads",
      "SELECT COUNT(*) FROM icall_gold.fact_sales",
      "SELECT COUNT(*) FROM mysql.user",
    ]) {
      await assert.rejects(poolFor("ICAU").query(sql), (e: { errno?: number }) => {
        assert.ok([1142, 1044, 1049].includes(e.errno ?? 0), `${sql}: unexpected errno ${e.errno}`);
        return true;
      }, sql);
    }
  });

  it("franchises queried in alternating order see only their own entity", async () => {
    const order = ["ICAU", "ICLA", "ICAU", "ICLA", "ICCH", "ICLA", "ICCH", "ICAU"];
    for (const ent of order) {
      for (const view of ["Salesforce_Data", "Leads", "Appointments", "dim_entity"]) {
        const r = await executeSql(ent, `SELECT DISTINCT \`entity\` AS e FROM ${view}`);
        assert.equal(r.error, undefined, `${ent} ${view}: ${r.error}`);
        const values = (r.rows ?? []).map((x) => x.e);
        assert.ok(values.length > 0, `${ent} ${view}: no rows`);
        assert.deepEqual(values, [ent], `${ent} ${view} returned ${JSON.stringify(values)}`);
      }
    }
  });

  it("query_sql ignores franchise or schema arguments from the model", async () => {
    const ctx: ToolContext = {
      branch: "ICAU", anchor: parseAnchor("2026-11-07"), round: 0,
      counters: { toolCallsCount: 0, queriesCount: 0 }, log: [], lastResult: {},
    };
    await sqlTool().dispatch("query_sql",
      { sql: "SELECT DISTINCT `entity` AS e FROM Salesforce_Data", franchise: "ICLA", schema: "poc_icla", entity: "ICLA", user: "poc_icla" },
      ctx);
    assert.equal(ctx.log.length, 1);
    assert.deepEqual(ctx.log[0].rows?.map((x) => x.e), ["ICAU"]);
  });

  it("one pool per franchise; pooled connections keep their user, schema and session settings", async () => {
    assert.notEqual(poolFor("ICAU"), poolFor("ICLA"));
    assert.equal(poolFor("ICAU"), poolFor("ICAU"));
    for (const ent of FRANCHISES) {
      // Two at once so both pooled connections are exercised.
      const results = await Promise.all([0, 1, 2, 3].map(() => poolFor(ent).query(
        "SELECT CURRENT_USER() AS u, DATABASE() AS db, @@SESSION.transaction_read_only AS ro, " +
        "@@SESSION.max_execution_time AS met, @@SESSION.sql_select_limit AS lim, @@SESSION.sql_mode AS mode")));
      for (const [rows] of results) {
        const r = (rows as Record<string, unknown>[])[0];
        assert.equal(String(r.u).split("@")[0], schemaFor(ent));
        assert.equal(r.db, schemaFor(ent));
        assert.equal(Number(r.ro), 1);
        assert.equal(Number(r.met), 10000);
        assert.equal(Number(r.lim), 51);
        assert.match(String(r.mode), /ANSI_QUOTES/);
      }
    }
  });

  it("writes and session changes are refused by the gate", async () => {
    for (const sql of ["DELETE FROM Salesforce_Data", "SET SESSION TRANSACTION READ WRITE", "USE poc_icla", "SELECT 1; SELECT 2"]) {
      const r = await executeSql("ICAU", sql);
      assert.ok(r.error?.startsWith("Query rejected by the safety gate"), `${sql}: ${r.error}`);
    }
  });
});
