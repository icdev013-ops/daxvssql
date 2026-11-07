/**
 * Offline fidelity checks: the engine's ports against the vendored production code (no network).
 * Skipped when .vendor/ is missing (run `npm run vendor`).
 */
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { describe, it } from "node:test";
import { loadArm } from "../src/arms";
import { fairContextLine, parseAnchor, productionContextLine, substitutePlaceholders } from "../src/context";
import { FRANCHISES } from "../src/franchise";
import { formatSqlResultForModel, type QueryResult } from "../src/format";
import { sanitizeHistory, windowHistory } from "../src/history";
import { requestSettingsFor } from "../src/llm";
import { VENDOR_DIR } from "../src/paths";
import { loadRulesTemplates } from "../src/prompts/rules";
import { daxTextToSql, slimSchemaFromDoc } from "../src/prompts/sql";
import { contextPlaceholders, gateCandidate, tryMatchRecipe } from "../src/recipes";
import { loadVendor, vendorAvailable } from "../src/vendor";

const skip = vendorAvailable("prod") && vendorAvailable("wt") ? false : ".vendor missing: run npm run vendor";
const anchor = parseAnchor("2026-11-07");

describe("formatter parity with production formatDaxResultForModel", { skip }, () => {
  const samples: QueryResult[] = [
    { columns: ["Salesforce_Data[Designer]", "[Sold]", "[Closing Rate]"], rows: [
      { "Salesforce_Data[Designer]": "A", "[Sold]": 1200.5, "[Closing Rate]": 0.41 },
      { "Salesforce_Data[Designer]": "B|C", "[Sold]": 800, "[Closing Rate]": null },
      { "Salesforce_Data[Designer]": null, "[Sold]": 99.25, "[Closing Rate]": 0.1 },
    ], rowCount: 3, truncated: false, latencyMs: 5 },
    { columns: ["[Sold]"], rows: [{ "[Sold]": null }], rowCount: 1, truncated: false, latencyMs: 1 },
    { columns: ["Count", "Year"], rows: Array.from({ length: 50 }, (_, i) => ({ Count: i, Year: 2000 + i })), rowCount: 120, truncated: true, latencyMs: 1 },
    { error: "Power BI returned 400: bad", latencyMs: 1 },
    { columns: ["n"], rows: [{ n: 7 }], rowCount: 1, truncated: false, latencyMs: 1 },
  ];
  for (const version of ["prod", "wt"] as const) {
    it(`${version}: tables, footers, totals, blank and errors are identical`, async () => {
      const v = await loadVendor(version);
      for (const s of samples) assert.equal(formatSqlResultForModel(s), v.formatDaxResultForModel(s));
    });
  }
  it("differs only in the no-rows hints", async () => {
    const v = await loadVendor("wt");
    const empty = { columns: [], rows: [], rowCount: 0, latencyMs: 1 };
    assert.match(v.formatDaxResultForModel(empty), /CONTAINSSTRING/);
    assert.match(formatSqlResultForModel(empty), /LIKE '%text%'/);
    assert.doesNotMatch(formatSqlResultForModel(empty), /CONTAINSSTRING|SEMANTIC MODEL/);
  });
});

describe("CONTEXT line", { skip }, () => {
  it("A0 line is production's text (every literal piece occurs in the d1b6a70 route)", () => {
    const route = fs.readFileSync(path.join(VENDOR_DIR, "prod/app/api/copilot/route.source.txt"), "utf8");
    const line = productionContextLine(anchor, "ICAU", { displayName: "X", branchLabel: "Y" }, true, ["ICAU"]);
    for (const piece of [
      "live_data=AVAILABLE (use query_dax for any numeric/ranking/comparison question; the screenshot may be stale)",
      "sales_basis=SOLD_DATE (this franchise reports sold revenue, sold counts and designer sold rankings by Salesforce_Data[Original Project Sold Date]",
      "screenshot=NONE (no screenshot is attached and the user CANNOT attach one",
    ]) {
      assert.ok(line.includes(piece));
      assert.ok(route.includes(piece), `route lacks: ${piece}`);
    }
    assert.match(line, /^\[CONTEXT: today=2026-11-07 year=2026 prev_year=2025 month=11 active_branch=ICAU \(X, Y\)/);
  });
  it("A and B share one line with no DAX names, and the decided basis per franchise", () => {
    const la = fairContextLine(anchor, "ICLA", { displayName: "X", branchLabel: "Y" }, true);
    const ch = fairContextLine(anchor, "ICCH", { displayName: "X", branchLabel: "Y" }, true);
    assert.match(la, /sales_basis=CREATED_DATE/);
    assert.match(ch, /sales_basis=SOLD_DATE/);
    for (const l of [la, ch]) assert.doesNotMatch(l, /query_dax|query_sql|Salesforce_Data\[|DAX/);
  });
  it("placeholders come from the anchor date", () => {
    assert.equal(substitutePlaceholders("YEAR({YEAR}) {PREV_YEAR} {MONTH}", anchor).text, "YEAR(2026) 2025 11");
    assert.deepEqual(contextPlaceholders("ICLA", parseAnchor("2026-01-15")), {
      YEAR: "2026", MONTH: "1", PREV_YEAR: "2025", PREV_MONTH: "12", MONTH_PARENT_YEAR: "2025", BRANCH: "ICLA" });
  });
});

describe("per-turn settings (requestSettingsFor)", () => {
  it("Luna: round 0 low/2500, later rounds medium/8000, wording none/2500, no temperature", () => {
    assert.deepEqual(requestSettingsFor("openai/gpt-6-luna", "vision", 2500), { max_tokens: 2500, reasoning: { effort: "low" } });
    assert.deepEqual(requestSettingsFor("openai/gpt-6-luna", "dax", 2500), { max_tokens: 8000, reasoning: { effort: "medium" } });
    assert.deepEqual(requestSettingsFor("openai/gpt-6-luna", "wrap", 2500), { max_tokens: 2500, reasoning: { effort: "none" } });
    assert.deepEqual(requestSettingsFor("openai/gpt-6-luna", "vision", 3500), { max_tokens: 3500, reasoning: { effort: "low" } });
  });
});

describe("history window", () => {
  it("drops dead turns with their question, keeps the last 3 exchanges and stubs the rest", () => {
    const turns = [
      { role: "user" as const, content: "q1" }, { role: "assistant" as const, content: "a1" },
      { role: "user" as const, content: "q2" }, { role: "assistant" as const, content: "[QUERY_LOG_R0: SELECT 1]" },
      { role: "user" as const, content: "q3" }, { role: "assistant" as const, content: "a3" },
      { role: "user" as const, content: "q4" }, { role: "assistant" as const, content: "_(error: x)_" },
      { role: "user" as const, content: "q5" }, { role: "assistant" as const, content: "a5" },
      { role: "user" as const, content: "q6" }, { role: "assistant" as const, content: "a6" },
      { role: "user" as const, content: "q7" }, { role: "assistant" as const, content: "a7" },
      { role: "user" as const, content: "dangling" },
    ];
    assert.deepEqual(sanitizeHistory(turns, 4000).map((t) => t.content), ["q1", "a1", "q3", "a3", "q5", "a5", "q6", "a6", "q7", "a7"]);
    const w = windowHistory(turns, 3, 4000);
    assert.equal(w.omitted, 4);
    assert.match(String(w.messages[0].content), /^\[4 earlier conversation turns omitted/);
    assert.deepEqual(w.kept.map((t) => t.content), ["q5", "a5", "q6", "a6", "q7", "a7"]);
  });
});

describe("arm prompts build offline for every franchise", { skip }, () => {
  const rules = loadRulesTemplates();
  for (const armId of ["A0", "A", "B"] as const) {
    for (const f of FRANCHISES) {
      it(`${armId} ${f}`, async () => {
        const arm = await loadArm(armId, rules);
        const intent = arm.vendor.detectIntent("show me top 5 designers this year as a table and why sales changed", false);
        const rendered = arm.renderRules(f, anchor);
        const vision = arm.buildVision(f, intent, rendered);
        const full = arm.buildFull(f, intent, rendered);
        assert.ok(vision.length > 5000 && full.length > vision.length * 0.8);
        if (armId === "A0") {
          assert.equal(rendered, null);
          assert.doesNotMatch(vision + full, /SHARED BUSINESS RULES/);
        } else {
          assert.ok(rendered && vision.includes(rendered.text) && full.includes(rendered.text), "rules injected in both rounds");
          assert.deepEqual(rendered.unfilled, [], `unfilled: ${rendered.unfilled.join(", ")}`);
          assert.doesNotMatch(vision + full, /DAX_LOG_R/);
          assert.ok(vision.endsWith(rendered.text) && full.endsWith(rendered.text), "rules at the same place: the end");
        }
        if (armId === "B") {
          assert.doesNotMatch(vision.slice(0, vision.indexOf("=== SHARED")) + full.slice(0, full.indexOf("=== SHARED")),
            /query_dax|CONTAINSSTRING|SUMMARIZECOLUMNS|EVALUATE/);
          assert.match(full, /query_sql/);
        }
      });
    }
  }
  it("A and B carry the same business rules, filled the same way, and the same CONTEXT line and wrap-up prompt", async () => {
    const a = await loadArm("A", rules);
    const b = await loadArm("B", rules);
    for (const f of FRANCHISES) {
      const ra = a.renderRules(f, anchor)!;
      const rb = b.renderRules(f, anchor)!;
      const biz = (t: string) => t.slice(0, t.indexOf(rules.bindings.dax.text.slice(0, 40)) >= 0 ? t.indexOf(rules.bindings.dax.text.slice(0, 40)) : undefined);
      assert.equal(biz(ra.text), rb.text.slice(0, biz(ra.text).length), `${f}: business rules differ`);
      assert.match(ra.text, f === "ICLA" ? /Salesforce_Data\[Created Date\]/ : /Salesforce_Data\[Original Project Sold Date\]/);
      assert.match(rb.text, f === "ICLA" ? /`Created Date`/ : /`Original Project Sold Date`/);
      assert.match(ra.text, /DATE\(2026, 11, 7\)/);
      assert.match(rb.text, /DATE '2026-11-07'/);
      assert.equal(a.contextLine(anchor, f, true), b.contextLine(anchor, f, true));
    }
    assert.equal(a.wrapUpPrompt, b.wrapUpPrompt);
    assert.doesNotMatch(a.wrapUpPrompt, /DAX/);
    assert.equal(a.guards.refusalCorrection.replace("query_dax", "X"), b.guards.refusalCorrection.replace("query_sql", "X"));
  });
  it("private names never appear in a committed bot file", () => {
    const staff: string[] = rules.staff ?? [];
    if (!staff.length) return;
    const files: string[] = [];
    const walk = (d: string) => {
      for (const e of fs.readdirSync(d, { withFileTypes: true })) {
        const p = path.join(d, e.name);
        if (e.isDirectory()) { if (!["node_modules", ".vendor"].includes(e.name)) walk(p); }
        else if (/\.(ts|mjs|js|json|md)$/.test(e.name)) files.push(p);
      }
    };
    walk(path.join(VENDOR_DIR, ".."));
    for (const file of files) {
      const text = fs.readFileSync(file, "utf8");
      const hits: number = staff.filter((n) => text.includes(n)).length;
      assert.equal(hits, 0, `${path.basename(file)} contains ${hits} name(s) from the private list`);
    }
  });
  it("B+ is a stub", async () => {
    await assert.rejects(loadArm("B+", rules), /stub/);
  });
});

describe("SQL notation helpers", () => {
  it("rewrites DAX references in descriptive text", () => {
    assert.equal(daxTextToSql(`Apply: NOT CONTAINSSTRING(Appointments[Appointment Subject], "available").`),
      "Apply: Appointments.`Appointment Subject` NOT LIKE '%available%'.");
    assert.equal(daxTextToSql(`NOT Appointments[Activity Type] IN {"Installation", "Unspecified"}`),
      "Appointments.`Activity Type` NOT IN ('Installation', 'Unspecified')");
    assert.equal(daxTextToSql(`'Lead Milestones'[Exclude From Journey] = 0`), "`Lead Milestones`.`Exclude From Journey` = 0");
  });
  it("extracts a slim schema from a SCHEMA.md-shaped document", () => {
    const doc = "# T\n\n## Views\n\n### `Leads`\n\nOne row per lead. More text.\n\n| Column | Type | Notes |\n|---|---|---|\n| `Lead ID` | varchar | x |\n| `Lead Status` | varchar | y |\n\n## Other\n";
    assert.equal(slimSchemaFromDoc(doc).split("\n").slice(-3).join("\n"), "### `Leads`\nOne row per lead.\nColumns: `Lead ID`, `Lead Status`");
  });
});

describe("A0 recipe matcher (regex pass, offline)", { skip }, () => {
  it("matches production recipes by regex and fills the anchor date", async () => {
    const v = await loadVendor("prod");
    let hits = 0;
    for (const r of v.RECIPES.slice(0, 135)) {
      const phrase = r.matches.find((m) => r.matchPattern.test(m));
      if (!phrase) continue;
      const intent = v.detectIntent(phrase, false);
      const m = await tryMatchRecipe(v, phrase, "ICLA", intent.classes, anchor, { embeddings: false });
      if (!m.match) continue;
      hits++;
      assert.equal(m.embeddingQueried, false);
      if ("YEAR" in m.match.placeholders) assert.equal(m.match.placeholders.YEAR, "2026");
      gateCandidate(m.match, intent, phrase, v.route.BRANCH_SCALAR_RECIPES);
    }
    assert.ok(hits >= 50, `only ${hits} recipes matched their own phrasings`);
  });
});
