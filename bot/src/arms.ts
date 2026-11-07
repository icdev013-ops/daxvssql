/**
 * Test arms. Everything except the query language is held constant between A and B: model, per-turn settings,
 * loop, guards (neutral wording), CONTEXT line, rulesDoc, formatter, history window.
 *
 *   A0  production as deployed (believed d1b6a70): .vendor/prod prompts, recipe fast path ON, production CONTEXT
 *       line and guard wording, DAX_LOG markers, no shared rules.
 *   A   DAX, fair: .vendor/wt prompts (SAAS commit 9ef5e4f = production + the 2026-11-07 work), fast path OFF,
 *       shared CONTEXT line, neutral guards, QUERY_LOG markers, business_rules.md + dax_bindings.md.
 *   B   SQL, fair: SQL prompt pair (prompts/sql.ts) over the per-franchise views, query_sql, fast path OFF,
 *       the same CONTEXT line, guards and markers as A, business_rules.md + sql_bindings.md.
 *   B+  stub: B plus curated metric views; built after A and B.
 */
import type { Anchor } from "./context";
import { fairContextLine, productionContextLine, SALES_BASIS } from "./context";
import type { MarkerStyle } from "./history";
import { buildSqlFullSystem, buildSqlVisionSystem, schemaSource } from "./prompts/sql";
import { renderRules, rulesBlock, type RenderedRules, type RulesTemplates } from "./prompts/rules";
import { daxTool, sqlTool, type QueryTool } from "./tools/index";
import { datasetRefreshInfo } from "./tools/dax";
import { sandboxDataVersion } from "./tools/sql";
import { loadVendor, type Intent, type VendorBundle, type VendorVersion } from "./vendor";

export type ArmId = "A0" | "A" | "B" | "B+";
export const ARM_IDS: ArmId[] = ["A0", "A", "B", "B+"];

export interface GuardTexts {
  hallucinationCorrection: string;
  hallucinationNotice: string;
  refusalCorrection: string;
  refusalLastRoundNote: string;
  hallucinationLastRoundNote: string;
  malformedNoBudget: string;
  malformedUnparseable: string;
  emptyNoRows: string;
  emptyFailed: string;
  /** Detects a failed query quoted in the previous assistant turn ("please continue" after an error). */
  priorFailureRe: RegExp;
  continueAfterFailureHint: string;
}

export interface ArmRuntime {
  id: ArmId;
  label: string;
  vendor: VendorBundle;
  fastPath: boolean;
  marker: MarkerStyle;
  tool: QueryTool;
  rules: RulesTemplates | null;
  /** The shared rules for this turn (anchor and franchise filled), or null (A0, or --no-rules). */
  renderRules(branch: string, anchor: Anchor): RenderedRules | null;
  wrapUpPrompt: string;
  guards: GuardTexts;
  basisLineBackstop: boolean;
  buildVision(branch: string, intent: Intent, rules: RenderedRules | null): string;
  buildFull(branch: string, intent: Intent, rules: RenderedRules | null): string;
  contextLine(anchor: Anchor, branch: string, liveOk: boolean): string;
  dataVersion(branch: string): Promise<Record<string, unknown>>;
  meta(): Record<string, unknown>;
}

const NON_ADMIN = { isAdmin: false };   // franchise owners are not admins: the PRIVACY block applies

function neutralWrapUp(vendor: VendorBundle): string {
  return vendor.route.WRAP_UP_SYSTEM_PROMPT
    .replace("a result table from a deterministic DAX query", "a result table from a deterministic query")
    .replace("no DAX talk", "no query talk");
}

function productionGuards(vendor: VendorBundle): GuardTexts {
  return {
    hallucinationCorrection: vendor.HALLUCINATION_CORRECTION_PROMPT,
    hallucinationNotice: vendor.HALLUCINATION_NOTICE,
    refusalCorrection: vendor.route.REFUSAL_CORRECTION_PROMPT,
    refusalLastRoundNote: "\n\n_(Note: I should have run the query and let the UI render the Excel + chart for you. Please re-ask if needed.)_",
    hallucinationLastRoundNote: "\n\n_(Note: I couldn't verify those numbers against live data. Please re-ask if the figures look off.)_",
    malformedNoBudget: "\n\nThe model produced a malformed tool call and I couldn't recover within the round budget. Please retry your question.",
    malformedUnparseable: "\n\nThe model produced a malformed tool call I couldn't parse. Please retry your question.",
    emptyNoRows: vendor.route.EMPTY_ANSWER_NO_ROWS_TEXT,
    emptyFailed: vendor.route.EMPTY_ANSWER_FAILED_TEXT,
    priorFailureRe: /Power BI returned 400|DatasetExecuteQueriesError/,
    continueAfterFailureHint: vendor.route.CONTINUE_AFTER_FAILURE_HINT,
  };
}

/** Guard wording shared by A and B: production's text with the DAX words made neutral; only the tool name differs. */
function fairGuards(vendor: VendorBundle, toolName: string): GuardTexts {
  const p = productionGuards(vendor);
  return {
    ...p,
    hallucinationCorrection: p.hallucinationCorrection.replace(/\bquery_dax\b/g, toolName),
    refusalCorrection:
      "STOP — that response is wrong. The UI auto-renders an interactive table with an Excel download button on every multi-row query result. " +
      `Run ${toolName} NOW with a query that returns the rows the user asked for. Give the columns clean aliases (e.g. "Project Name", "Amount", "Stage") so they render cleanly. ` +
      "Do not apologize and do not ask for more details — pick sensible default columns and execute the query. The Excel + chart will appear automatically.",
    priorFailureRe: /Power BI returned (?:400|an error)|DatasetExecuteQueriesError|MySQL returned|Query rejected by the safety gate/,
    continueAfterFailureHint:
      "\n\n[SYSTEM NOTE — your previous query failed with an error. DO NOT repeat the same query shape or column references; you will fail again. Re-derive the answer from scratch. The most common cause of repeat failure here is a hallucinated column name (e.g. a lead-owner or created-month column on Leads — neither exists). Before generating the next query, mentally check every table and column reference against the schema.]",
  };
}

const fairMarkers = (prompt: string) => prompt.replace(/DAX_LOG_R/g, "QUERY_LOG_R");

async function dataVersionDax(branch: string) { return datasetRefreshInfo(branch); }
async function dataVersionSql() { return sandboxDataVersion(); }

const codeBaseMeta = (vendor: VendorBundle) => ({
  vendor: vendor.version, vendor_ref: vendor.manifest.ref, vendor_commit: vendor.manifest.commit,
  vendor_bundle: vendor.manifest.combined_sha256, saas_head_at_vendoring: vendor.manifest.saas_head,
});
const rulesMeta = (rules: RulesTemplates | null, dialect: "dax" | "sql") => (rules ? {
  rules_business_sha256: rules.business.sha256, rules_bindings_file: `${dialect}_bindings.md`,
  rules_bindings_sha256: rules.bindings[dialect].sha256, rules_staff_list_sha256: rules.staffSha256,
} : { rules: null });

export async function loadArm(id: ArmId, rules: RulesTemplates | null): Promise<ArmRuntime> {
  if (id === "B+") {
    throw new Error("arm B+ is a stub: it needs the curated metric views (closing rate, sales by basis, appointments as reported), built after A and B");
  }
  const version: VendorVersion = id === "A0" ? "prod" : "wt";
  const vendor = await loadVendor(version);

  if (id === "A0") {
    const tool = daxTool(vendor);
    return {
      id, label: "A0 production (d1b6a70), DAX, recipe fast path on", vendor, fastPath: true, marker: "DAX_LOG", tool,
      rules: null, renderRules: () => null, wrapUpPrompt: vendor.route.WRAP_UP_SYSTEM_PROMPT, guards: productionGuards(vendor),
      basisLineBackstop: false,
      buildVision: (b, i) => vendor.buildVisionSystem(b, i, NON_ADMIN),
      buildFull: (b, i) => vendor.buildDaxSystem(b, i, NON_ADMIN),
      contextLine: (a, b, live) => productionContextLine(a, b, vendor.getBranchMeta(b), live,
        vendor.route.SOLD_DATE_BRANCHES ?? ["ICAU", "ICCH", "ICDF", "ICSE", "ICTA"]),
      dataVersion: dataVersionDax,
      meta: () => codeBaseMeta(vendor),
    };
  }

  if (id === "A") {
    const tool = daxTool(vendor);
    return {
      id, label: "A DAX fair (SAAS 9ef5e4f), fast path off", vendor, fastPath: false, marker: "QUERY_LOG", tool, rules,
      renderRules: (b, a) => (rules ? renderRules(rules, "dax", b, a) : null),
      wrapUpPrompt: neutralWrapUp(vendor), guards: fairGuards(vendor, tool.name),
      basisLineBackstop: vendor.route.HAS_BASIS_LINE_BACKSTOP,
      buildVision: (b, i, r) => fairMarkers(vendor.buildVisionSystem(b, i, NON_ADMIN)) + rulesBlock(r),
      buildFull: (b, i, r) => fairMarkers(vendor.buildDaxSystem(b, i, NON_ADMIN)) + rulesBlock(r),
      contextLine: (a, b, live) => fairContextLine(a, b, vendor.getBranchMeta(b), live),
      dataVersion: dataVersionDax,
      meta: () => ({ ...codeBaseMeta(vendor), ...rulesMeta(rules, "dax") }),
    };
  }

  // B
  const tool = sqlTool();
  const deps = (b: string) => ({ vendor, salesBasis: SALES_BASIS[b] ?? "created" as const });
  return {
    id, label: "B SQL fair (views mirroring the Power BI tables), fast path off", vendor, fastPath: false, marker: "QUERY_LOG",
    tool, rules, renderRules: (b, a) => (rules ? renderRules(rules, "sql", b, a) : null),
    wrapUpPrompt: neutralWrapUp(vendor), guards: fairGuards(vendor, tool.name),
    basisLineBackstop: vendor.route.HAS_BASIS_LINE_BACKSTOP,
    buildVision: (b, i, r) => buildSqlVisionSystem(b, i, deps(b), NON_ADMIN) + rulesBlock(r),
    buildFull: (b, i, r) => buildSqlFullSystem(b, i, deps(b), NON_ADMIN) + rulesBlock(r),
    contextLine: (a, b, live) => fairContextLine(a, b, vendor.getBranchMeta(b), live),
    dataVersion: async () => dataVersionSql(),
    meta: () => ({ ...codeBaseMeta(vendor), ...rulesMeta(rules, "sql"), schema_source: schemaSource().kind }),
  };
}
