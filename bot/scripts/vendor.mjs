#!/usr/bin/env node
/**
 * Vendor the production chatbot code the POC engine imports.
 *
 *   .vendor/prod/  files at SAAS commit d1b6a70 (A0: believed to be the deployed code)
 *   .vendor/wt/    files at SAAS commit 9ef5e4f (A and B: production plus the committed 2026-11-07 work)
 * Both come from `git show <commit>:<path>` only, never from the working copy (another session may be editing
 * it). A required file missing from a commit is an error, never a fallback to the working copy.
 *
 * Read-only towards the SAAS repo: the only git commands run are `git show` and `git rev-parse`.
 * Nothing in the SAAS repo is written.
 *
 * .vendor/ is gitignored: these files can hold staff and designer names, and this repo is public.
 *
 * What it does per version:
 *   1. Starts from the entry modules below and follows their imports (the import closure).
 *   2. Rewrites `@/lib/...` imports to relative paths, drops `import "server-only"`, points
 *      `lucide-react` at a generated stub and gives .tsx files a no-op JSX factory, so the files load
 *      under tsx/Node without Next.js.
 *   3. Copies app/api/copilot/route.ts as plain text (it is never executed) and extracts the constants
 *      the engine needs from it into route-consts.ts (wrap-up prompt, refusal patterns, limits).
 *   4. Writes MANIFEST.json: commit (short and full), SAAS HEAD at vendoring time, per-file sha256, a combined hash.
 *
 * usage: node scripts/vendor.mjs [--only prod|wt] [--saas <path>] [--prod-ref <commit>] [--wt-ref <commit>]
 */
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const BOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const args = process.argv.slice(2);
const argVal = (k) => { const i = args.indexOf(k); return i >= 0 ? args[i + 1] : undefined; };
const SAAS = path.resolve(argVal("--saas") ?? process.env.SAAS_DIR ?? path.join(BOT, "..", "..", "Inspired Closets SAAS Reporting"));
const ONLY = argVal("--only");
const REFS = { prod: argVal("--prod-ref") ?? "d1b6a70", wt: argVal("--wt-ref") ?? "9ef5e4f" };

const ENTRIES = [
  "lib/copilot/system-vision.ts",
  "lib/copilot/system-dax.ts",
  "lib/copilot/semantic-knowledge.ts",
  "lib/copilot/franchise-rules.ts",      // absent at d1b6a70; optional
  "lib/copilot/dax-recipes.ts",
  "lib/copilot/recipe-executor.ts",
  "lib/copilot/tools.ts",
  "lib/copilot/pbi-dax.ts",
  "lib/copilot/pbi-datasets.ts",
  "lib/copilot/branch-data.ts",
  "lib/copilot/intent.ts",
  "lib/copilot/chart-override.ts",
  "lib/copilot/hallucination-guard.ts",
  "lib/copilot/tool-call-recovery.ts",
  "lib/copilot/variance-playbook.ts",
  "lib/copilot/pbi-vocabulary.ts",
  "lib/branches.ts",
];
// franchise-rules.ts is optional only for prod (it was added in 9ef5e4f); for wt every entry is required.
const OPTIONAL = { prod: new Set(["lib/copilot/franchise-rules.ts"]), wt: new Set() };
const ROUTE = "app/api/copilot/route.ts";
// Packages the vendored code may import as-is (installed in bot/package.json).
const ALLOWED_PACKAGES = new Set(["@upstash/vector"]);

function git(...a) {
  return execFileSync("git", ["-C", SAAS, ...a], { encoding: "utf8", maxBuffer: 64 * 1024 * 1024, stdio: ["ignore", "pipe", "pipe"] });
}
const sha256 = (s) => createHash("sha256").update(s).digest("hex");

// Working-tree files are checked out with CRLF on this PC; git (and so every deploy) stores LF. Normalise to LF so
// multi-line template literals in the prompts are byte-identical to what a build from git would send.
const toLf = (s) => s.replace(/\r\n/g, "\n");
function readFrom(version, rel) {
  try { return toLf(git("show", `${REFS[version]}:${rel}`)); } catch { return null; }
}

/** Resolve an import specifier found in `fromRel` to a repo-relative source path, or a package name. */
function resolveSpec(version, fromRel, spec) {
  let base;
  if (spec.startsWith("@/")) base = spec.slice(2);
  else if (spec.startsWith("./") || spec.startsWith("../")) base = path.posix.normalize(path.posix.join(path.posix.dirname(fromRel), spec));
  else return { pkg: spec };
  for (const ext of ["", ".ts", ".tsx", "/index.ts"]) {
    const cand = base + ext;
    if (/\.(ts|tsx)$/.test(cand) && readFrom(version, cand) !== null) return { rel: cand };
  }
  return { missing: base };
}

const IMPORT_RE = /(?:import|export)\s+(?:type\s+)?(?:[^'";]*?\s+from\s+)?["']([^"']+)["']/g;

function vendorVersion(version) {
  const outRoot = path.join(BOT, ".vendor", version);
  fs.rmSync(outRoot, { recursive: true, force: true });
  fs.mkdirSync(outRoot, { recursive: true });

  const queue = [...ENTRIES];
  const seen = new Map();          // rel -> source text
  const missing = [];
  const packages = new Set();
  while (queue.length) {
    const rel = queue.shift();
    if (seen.has(rel)) continue;
    const src = readFrom(version, rel);
    if (src === null) {
      if (OPTIONAL[version].has(rel)) { missing.push(rel); continue; }
      throw new Error(`[${version}] required file ${rel} is not in commit ${REFS[version]}; not falling back to the working copy`);
    }
    seen.set(rel, src);
    for (const m of src.matchAll(IMPORT_RE)) {
      const spec = m[1];
      if (spec === "server-only") continue;
      const r = resolveSpec(version, rel, spec);
      if (r.rel) queue.push(r.rel);
      else if (r.pkg) packages.add(r.pkg);
      else throw new Error(`[${version}] ${rel} imports ${spec}, which does not resolve`);
    }
  }

  const shimDir = path.join(outRoot, "__shims__");
  fs.mkdirSync(shimDir, { recursive: true });
  const lucideNames = new Set();

  for (const [rel, src] of seen) {
    const outFile = path.join(outRoot, rel);
    fs.mkdirSync(path.dirname(outFile), { recursive: true });
    const relTo = (target) => {
      let r = path.posix.relative(path.posix.dirname(rel), target);
      if (!r.startsWith(".")) r = "./" + r;
      return r;
    };
    let out = src.replace(/^\s*import\s+["']server-only["'];?\s*$/m, "// (vendor) server-only import removed");
    out = out.replace(IMPORT_RE, (full, spec) => {
      if (spec.startsWith("@/")) {
        const r = resolveSpec(version, rel, spec);
        return full.replace(spec, relTo(r.rel.replace(/\.(ts|tsx)$/, "")));
      }
      if (spec === "lucide-react") {
        const names = full.match(/\{([\s\S]*?)\}/)?.[1] ?? "";
        for (const part of names.split(",")) {
          const n = part.trim().split(/\s+as\s+/)[0].trim();
          if (n) lucideNames.add(n);
        }
        return full.replace(spec, relTo("__shims__/lucide-react"));
      }
      if (spec.startsWith("./") || spec.startsWith("../") || ALLOWED_PACKAGES.has(spec)) return full;
      throw new Error(`[${version}] ${rel} imports package ${spec}; add a shim or allow it`);
    });
    if (rel.endsWith(".tsx")) {
      // The engine only needs text renderers from .tsx files; JSX (icons) becomes null.
      out = "/** @jsxRuntime classic */\n/** @jsx __vendorJsx */\n/** @jsxFrag __vendorFrag */\n" +
        "const __vendorJsx = (..._a: unknown[]): null => null;\nconst __vendorFrag = null;\n" + out;
    }
    fs.writeFileSync(outFile, out);
  }
  fs.writeFileSync(path.join(shimDir, "lucide-react.ts"),
    "// (vendor) stub icons: the bot never renders UI\nconst Icon = (..._a: unknown[]): null => null;\n" +
    [...lucideNames].sort().map((n) => `export const ${n} = Icon;`).join("\n") + "\n");

  // ---- route.ts: plain-text copy + extracted constants ----
  const route = readFrom(version, ROUTE);
  if (route === null) throw new Error(`[${version}] ${ROUTE} not found`);
  fs.mkdirSync(path.join(outRoot, "app/api/copilot"), { recursive: true });
  fs.writeFileSync(path.join(outRoot, "app/api/copilot/route.source.txt"), route);
  const grab = (name, re) => {
    const m = route.match(re);
    if (!m) throw new Error(`[${version}] could not extract ${name} from route.ts`);
    return m[1];
  };
  const consts = [
    "// (vendor) constants extracted from app/api/copilot/route.ts; regenerate with scripts/vendor.mjs",
    `export const WRAP_UP_SYSTEM_PROMPT: string = ${grab("WRAP_UP_SYSTEM_PROMPT", /const WRAP_UP_SYSTEM_PROMPT = (\[[\s\S]*?\]\.join\("\\n"\));/)};`,
    `export const REFUSAL_PATTERNS: RegExp[] = ${grab("REFUSAL_PATTERNS", /const REFUSAL_PATTERNS: RegExp\[\] = (\[[\s\S]*?\n\s*\]);/)};`,
    `export const USER_ASKED_FOR_EXPORT_RE: RegExp = ${grab("userAskedForExport", /const userAskedForExport = (\/.+?\/[a-z]*)\.test\(userQuestionLower\);/)};`,
    `export const REFUSAL_CORRECTION_PROMPT: string = ${grab("refusal correction", /content:\s*\n\s*("STOP — that response is wrong\.[^\n]*"),\n/)};`,
    `export const MAX_HISTORY_TURNS = ${grab("MAX_HISTORY_TURNS", /const MAX_HISTORY_TURNS\s*=\s*(\d+);/)};`,
    `export const MAX_TOKENS = ${grab("MAX_TOKENS", /const MAX_TOKENS\s*=\s*(\d+);/)};`,
    `export const MAX_TOOL_ROUNDS = ${grab("MAX_TOOL_ROUNDS", /const MAX_TOOL_ROUNDS\s*=\s*(\d+);/)};`,
    `export const MAX_HISTORY_VIZ_CHARS = ${grab("MAX_HISTORY_VIZ_CHARS", /const MAX_HISTORY_VIZ_CHARS = ([\d_]+);/)};`,
    `export const DEFAULT_VISION_MODEL = ${grab("DEFAULT_VISION_MODEL", /const DEFAULT_VISION_MODEL = ("[^"]+");/)};`,
    `export const DEFAULT_DAX_MODEL = ${grab("DEFAULT_DAX_MODEL", /const DEFAULT_DAX_MODEL = ("[^"]+");/)};`,
    `export const BRANCH_SCALAR_RECIPES: string[] = ${grab("BRANCH_SCALAR_RECIPES", /const BRANCH_SCALAR_RECIPES = new Set\((\[[^\]]*\])\);/)};`,
    `export const EMPTY_ANSWER_NO_ROWS_TEXT: string = ${grab("empty-answer no-rows text", /filled =\n\s*("I ran the numbers[^\n]*");/)};`,
    `export const EMPTY_ANSWER_FAILED_TEXT: string = ${grab("empty-answer failed text", /filled =\n\s*("I wasn't able[^\n]*");/)};`,
    `export const CONTINUE_AFTER_FAILURE_HINT: string = ${grab("please-continue hint", /const continueAfterFailureHint = \(isPleaseContinue && priorDaxFailed\)\n\s*\? ("[^\n]*")\n/)};`,
    `export const HAS_EMPTY_NAME_LOOKUP = ${/emptyNameLookup/.test(route)};`,
    `export const HAS_BASIS_FOLLOWUP_SKIP = ${/isBasisFollowup/.test(route)};`,
    `export const HAS_BASIS_LINE_BACKSTOP = ${/Rule 14 backstop/.test(route)};`,
  ];
  const sold = route.match(/const SOLD_DATE_BRANCHES = new Set<string>\((\[[^\]]*\])\);/);
  consts.push(`export const SOLD_DATE_BRANCHES: string[] | null = ${sold ? sold[1] : "null"};`);
  fs.writeFileSync(path.join(outRoot, "route-consts.ts"), consts.join("\n") + "\n");

  // ---- manifest ----
  const head = git("rev-parse", "HEAD").trim();
  const commit = git("rev-parse", `${REFS[version]}^{commit}`).trim();
  const files = [...[...seen.entries()].map(([rel, src]) => ({ rel, src })), { rel: ROUTE, src: route }]
    .sort((a, b) => a.rel.localeCompare(b.rel))
    .map(({ rel, src }) => ({ path: rel, sha256: sha256(src), bytes: Buffer.byteLength(src), source: `git:${REFS[version]}` }));
  const manifest = {
    version,
    ref: REFS[version],
    commit,
    saas_head: head,
    vendored_at: new Date().toISOString(),
    missing_optional: missing,
    packages: [...packages].sort(),
    files,
    combined_sha256: sha256(files.map((f) => `${f.path}:${f.sha256}`).join("\n")),
  };
  fs.writeFileSync(path.join(outRoot, "MANIFEST.json"), JSON.stringify(manifest, null, 2));
  return manifest;
}

const versions = ONLY ? [ONLY] : ["prod", "wt"];
for (const v of versions) {
  const m = vendorVersion(v);
  console.log(`${v}: ${m.files.length} files from commit ${m.ref} (${m.commit.slice(0, 12)}; SAAS HEAD ${m.saas_head.slice(0, 7)})` +
    (m.missing_optional.length ? `, missing optional: ${m.missing_optional.join(", ")}` : "") +
    `; bundle ${m.combined_sha256.slice(0, 12)}`);
}
