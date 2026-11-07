/**
 * Typed access to the vendored production modules (.vendor/prod = d1b6a70, .vendor/wt = working tree).
 * Run `npm run vendor` first. Nothing from the SAAS repo is committed here; the engine loads it at runtime.
 */
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { VENDOR_DIR } from "./paths";

export type VendorVersion = "prod" | "wt";

export interface Intent {
  needsVariance: boolean;
  needsGuide: boolean;
  needsBriefing: boolean;
  needsVocab: boolean;
  classes: string[];
}

export interface DaxRecipe {
  id: string;
  title: string;
  matches: string[];
  matchPattern: RegExp;
  dax: string;
  defaults?: Record<string, string>;
  returns: string;
  skipEmbedding?: boolean;
  handlesVariance?: boolean;
  handlesBriefing?: boolean;
  intents?: string[];
  entities?: string[];
  emptyFallbackHint?: string;
  salesBasis?: string;
}

export interface TableSpec {
  name: string;
  kind: string;
  description: string;
  useFor: string[];
  columns: Record<string, { dataType: string; desc: string; hidden?: boolean; calculated?: boolean }>;
  relatedMeasures?: string[];
}
export interface MeasureSpec { name: string; table: string; description: string; dax: string; formatString?: string }
export interface RelationshipSpec { from: { table: string; column: string }; to: { table: string; column: string }; active?: boolean; notes?: string }

export interface VendorManifest {
  version: VendorVersion;
  ref: string;
  commit: string;
  saas_head: string;
  vendored_at: string;
  missing_optional: string[];
  files: { path: string; sha256: string; bytes: number; source: string }[];
  combined_sha256: string;
}

export interface VendorBundle {
  version: VendorVersion;
  manifest: VendorManifest;
  buildVisionSystem(branch: string, intent: Intent, flags?: { isAdmin?: boolean }): string;
  buildDaxSystem(branch: string, intent: Intent, flags?: { isAdmin?: boolean }): string;
  detectIntent(question: string, hasImage: boolean): Intent;
  extractChartOverride(question: string): { question: string; chartOverride?: string };
  looksHallucinated(text: string): boolean;
  HALLUCINATION_CORRECTION_PROMPT: string;
  HALLUCINATION_NOTICE: string;
  COPILOT_TOOLS: readonly unknown[];
  periodMeasureWindowError(dax: string): string | null;
  /** Only in the working tree (added 2026-11-07); undefined for prod. */
  closeOpenParens?: (dax: string) => string;
  formatDaxResultForModel(result: unknown): string;
  RECIPES: DaxRecipe[];
  RECIPES_BY_ID: Record<string, DaxRecipe>;
  recipeVersion(r: DaxRecipe): Promise<string>;
  fillRecipeDax(recipe: DaxRecipe, placeholders: Record<string, string>): string;
  newMalformedProbe(): { suppressed: boolean; accumulated: string };
  probeDelta(probe: unknown, delta: string, emit: (t: string) => void): void;
  probeFinish(probe: unknown, emit: (t: string) => void): void;
  extractDaxFromMalformed(text: string): string | null;
  getBranchMeta(code: string): { code: string; displayName: string; branchLabel: string };
  WEEKLY_REPORT_BRANCHES: string[];
  renderBranchContext(code: string): string;
  renderFranchiseRules?: (code: string) => string;
  TABLES: Record<string, TableSpec>;
  MEASURES: Record<string, MeasureSpec>;
  RELATIONSHIPS: RelationshipSpec[];
  COMMON_QUESTIONS: Record<string, { table: string; measures?: string[]; notes?: string }>;
  UI_ONLY_MEASURES?: string;
  VARIANCE_PLAYBOOK: string;
  PBI_VOCABULARY_PRIMER: string;
  WEEKLY_TABLES_BLOCK: string;
  renderGuideForPrompt(): string;
  route: {
    WRAP_UP_SYSTEM_PROMPT: string;
    REFUSAL_PATTERNS: RegExp[];
    USER_ASKED_FOR_EXPORT_RE: RegExp;
    REFUSAL_CORRECTION_PROMPT: string;
    MAX_HISTORY_TURNS: number;
    MAX_TOKENS: number;
    MAX_TOOL_ROUNDS: number;
    MAX_HISTORY_VIZ_CHARS: number;
    DEFAULT_VISION_MODEL: string;
    DEFAULT_DAX_MODEL: string;
    BRANCH_SCALAR_RECIPES: string[];
    HAS_EMPTY_NAME_LOOKUP: boolean;
    HAS_BASIS_FOLLOWUP_SKIP: boolean;
    HAS_BASIS_LINE_BACKSTOP: boolean;
    EMPTY_ANSWER_NO_ROWS_TEXT: string;
    EMPTY_ANSWER_FAILED_TEXT: string;
    CONTINUE_AFTER_FAILURE_HINT: string;
    SOLD_DATE_BRANCHES: string[] | null;
  };
}

const cache = new Map<VendorVersion, Promise<VendorBundle>>();

export function vendorAvailable(version: VendorVersion): boolean {
  return fs.existsSync(path.join(VENDOR_DIR, version, "MANIFEST.json"));
}

export function loadVendor(version: VendorVersion): Promise<VendorBundle> {
  let p = cache.get(version);
  if (!p) {
    p = doLoad(version);
    cache.set(version, p);
  }
  return p;
}

async function doLoad(version: VendorVersion): Promise<VendorBundle> {
  const root = path.join(VENDOR_DIR, version);
  if (!vendorAvailable(version)) {
    throw new Error(`.vendor/${version} is missing: run \`npm run vendor\` in daxvssql/bot first`);
  }
  const imp = (rel: string) => import(pathToFileURL(path.join(root, rel)).href);
  const [vision, dax, intent, chart, hall, tools, pbiDax, recipes, exec, recovery, branches, branchData,
    knowledge, variance, vocab, guide, routeConsts] = await Promise.all([
    imp("lib/copilot/system-vision.ts"), imp("lib/copilot/system-dax.ts"), imp("lib/copilot/intent.ts"),
    imp("lib/copilot/chart-override.ts"), imp("lib/copilot/hallucination-guard.ts"), imp("lib/copilot/tools.ts"),
    imp("lib/copilot/pbi-dax.ts"), imp("lib/copilot/dax-recipes.ts"), imp("lib/copilot/recipe-executor.ts"),
    imp("lib/copilot/tool-call-recovery.ts"), imp("lib/branches.ts"), imp("lib/copilot/branch-data.ts"),
    imp("lib/copilot/semantic-knowledge.ts"), imp("lib/copilot/variance-playbook.ts"),
    imp("lib/copilot/pbi-vocabulary.ts"), imp("lib/guide/content.tsx"), imp("route-consts.ts"),
  ]);
  const franchiseRulesPath = path.join(root, "lib/copilot/franchise-rules.ts");
  const fr = fs.existsSync(franchiseRulesPath) ? await imp("lib/copilot/franchise-rules.ts") : null;
  const manifest = JSON.parse(fs.readFileSync(path.join(root, "MANIFEST.json"), "utf8")) as VendorManifest;
  return {
    version,
    manifest,
    buildVisionSystem: vision.buildVisionSystem,
    buildDaxSystem: dax.buildDaxSystem,
    detectIntent: intent.detectIntent,
    extractChartOverride: chart.extractChartOverride,
    looksHallucinated: hall.looksHallucinated,
    HALLUCINATION_CORRECTION_PROMPT: hall.HALLUCINATION_CORRECTION_PROMPT,
    HALLUCINATION_NOTICE: hall.HALLUCINATION_NOTICE,
    COPILOT_TOOLS: tools.COPILOT_TOOLS,
    periodMeasureWindowError: tools.periodMeasureWindowError,
    closeOpenParens: pbiDax.closeOpenParens,
    formatDaxResultForModel: pbiDax.formatDaxResultForModel,
    RECIPES: recipes.RECIPES,
    RECIPES_BY_ID: recipes.RECIPES_BY_ID,
    recipeVersion: recipes.recipeVersion,
    fillRecipeDax: exec.fillRecipeDax,
    newMalformedProbe: recovery.newMalformedProbe,
    probeDelta: recovery.probeDelta,
    probeFinish: recovery.probeFinish,
    extractDaxFromMalformed: recovery.extractDaxFromMalformed,
    getBranchMeta: branches.getBranchMeta,
    WEEKLY_REPORT_BRANCHES: branches.WEEKLY_REPORT_BRANCHES,
    renderBranchContext: branchData.renderBranchContext,
    renderFranchiseRules: fr?.renderFranchiseRules,
    TABLES: knowledge.TABLES,
    MEASURES: knowledge.MEASURES,
    RELATIONSHIPS: knowledge.RELATIONSHIPS,
    COMMON_QUESTIONS: knowledge.COMMON_QUESTIONS,
    UI_ONLY_MEASURES: knowledge.UI_ONLY_MEASURES,
    VARIANCE_PLAYBOOK: variance.VARIANCE_PLAYBOOK,
    PBI_VOCABULARY_PRIMER: vocab.PBI_VOCABULARY_PRIMER,
    WEEKLY_TABLES_BLOCK: dax.WEEKLY_TABLES_BLOCK,
    renderGuideForPrompt: guide.renderGuideForPrompt,
    route: routeConsts,
  } as VendorBundle;
}
