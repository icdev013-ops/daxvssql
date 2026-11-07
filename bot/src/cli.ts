/**
 * CLI for the POC runner.
 *
 *   single:  tsx src/cli.ts --arm A --franchise ICLA --anchor 2026-11-07 --q "How much did we sell this year?"
 *   batch:   tsx src/cli.ts --arm B --anchor 2026-11-07 --cases cases.jsonl [--concurrency 2]
 *   dry run: tsx src/cli.ts --arm B --franchise ICAU --anchor 2026-11-07 --q "..." --dry-run [--dump-prompts <dir>]
 *
 * Options:
 *   --arm A0|A|B|B+          test arm (B+ = B plus the governed metric views of sandbox/views_plus)
 *   --franchise ICLA|ICCH|ICAU   the franchise the turn runs as (cases may set their own)
 *   --anchor YYYY-MM-DD      the date the CONTEXT line carries (cases may set their own); never the clock
 *   --q "<question>"         single question;  --history <file.json>  prior turns [{role, content}, ...]
 *   --cases <file.jsonl>     one case per line: {"id","question","franchise","anchor","history","arm"}
 *   --concurrency 1|2        batch workers (default 1, max 3)
 *   --run-id <id>            log file name (default: timestamp + arm); log goes to daxvssql/runs/<run-id>.jsonl
 *   --rules-dir <dir>        shared rules for A and B (default daxvssql/rules: business_rules.md + dax_bindings.md or
 *                            sql_bindings.md; staff names from daxvssql/private/rules_names.md); --no-rules to omit
 *   --embeddings             A0 only: allow the recipe matcher's read-only Upstash embedding pass (default off)
 *   --json                   print the full turn record instead of the summary
 */
import fs from "node:fs";
import path from "node:path";
import { z } from "zod";
import { ARM_IDS, loadArm, type ArmId, type ArmRuntime } from "./arms";
import { parseAnchor } from "./context";
import { runTurn, type TurnRecord } from "./engine";
import { assertFranchise, FRANCHISES } from "./franchise";
import type { ChatTurn } from "./history";
import { RunLog } from "./log";
import { RUNS_DIR } from "./paths";
import { loadRulesTemplates } from "./prompts/rules";
import { closePools } from "./tools/sql";
import { daxLimiter } from "./tools/throttle";
import { newRunId, sha256 } from "./util";

const Turn = z.object({ role: z.enum(["user", "assistant"]), content: z.string() });
const Case = z.object({
  id: z.string().optional(),
  question: z.string().min(1),
  franchise: z.string().optional(),
  anchor: z.string().optional(),
  history: z.array(Turn).optional(),
  arm: z.string().optional(),
  /** Multi-turn: cases sharing `conv` run in `turn` order on one worker, each turn seeing this
   *  arm's OWN earlier answers in the conversation as history (as a real chat would). */
  conv: z.string().optional(),
  turn: z.number().int().optional(),
});
type CaseT = z.infer<typeof Case>;

function parseArgs(argv: string[]) {
  const out: Record<string, string | boolean> = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith("--")) throw new Error(`unexpected argument ${a}`);
    const key = a.slice(2);
    const next = argv[i + 1];
    if (next === undefined || next.startsWith("--")) out[key] = true;
    else { out[key] = next; i++; }
  }
  return out;
}

function summary(r: TurnRecord): string {
  const served = [...new Set(r.rounds.map((x) => `${x.served_model ?? "?"} via ${x.provider ?? "?"}`))].join(", ");
  const q = r.queries.map((x) => `  [r${x.round} ${x.dialect} ${x.ok ? "ok" : "ERR"} ${x.row_count ?? 0} rows ${x.latency_ms} ms${x.blocked_by ? ` blocked:${x.blocked_by}` : ""}] ${(x.executed ?? x.query).replace(/\s+/g, " ").slice(0, 160)}${x.error ? `\n      error: ${x.error.slice(0, 200)}` : ""}`).join("\n");
  return [
    `--- ${r.case_id ? r.case_id + " " : ""}${r.arm} ${r.franchise} ${r.anchor_date} (${r.path} path)`,
    `Q: ${r.question}`,
    `A: ${r.answer_visible}`,
    `rounds=${r.round_count} queries=${r.query_count} latency=${(r.latency_ms / 1000).toFixed(1)}s tokens=${r.tokens.prompt}+${r.tokens.completion} (reasoning ${r.tokens.reasoning}) cost≈$${r.cost_usd.estimated.toFixed(5)}${r.cost_usd.reported !== null ? ` (OpenRouter $${r.cost_usd.reported.toFixed(5)})` : ""}`,
    `served: ${served || "-"}${r.guards_fired.length ? ` · guards: ${r.guards_fired.join(", ")}` : ""}${r.errors.length ? ` · errors: ${r.errors.join(" | ").slice(0, 300)}` : ""}`,
    q,
  ].filter(Boolean).join("\n");
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help || args.h) {
    console.log(fs.readFileSync(new URL(import.meta.url), "utf8").split("*/")[0]);
    return;
  }
  const armArg = String(args.arm ?? "");
  if (!ARM_IDS.includes(armArg as ArmId)) throw new Error(`--arm must be one of ${ARM_IDS.join(", ")}`);
  const rules = args["no-rules"] ? null : loadRulesTemplates(typeof args["rules-dir"] === "string" ? args["rules-dir"] : undefined);
  const armCache = new Map<string, Promise<ArmRuntime>>();
  const getArm = (id: string) => {
    if (!ARM_IDS.includes(id as ArmId)) throw new Error(`unknown arm ${id}`);
    let p = armCache.get(id);
    if (!p) { p = loadArm(id as ArmId, rules); armCache.set(id, p); }
    return p;
  };
  const defaultAnchor = typeof args.anchor === "string" ? args.anchor : undefined;
  const defaultFranchise = typeof args.franchise === "string" ? args.franchise : undefined;

  // ---- dry run: build prompts only, no network
  if (args["dry-run"]) {
    const arm = await getArm(armArg);
    const franchise = assertFranchise(defaultFranchise);
    const anchor = parseAnchor(defaultAnchor ?? "");
    const q = typeof args.q === "string" ? args.q : "How much have we sold this year?";
    const intent = arm.vendor.detectIntent(arm.vendor.extractChartOverride(q).question, false);
    const rendered = arm.renderRules(franchise, anchor);
    const vision = arm.buildVision(franchise, intent, rendered, anchor);
    const full = arm.buildFull(franchise, intent, rendered, anchor);
    const ctx = arm.contextLine(anchor, franchise, true);
    console.log(JSON.stringify({
      arm: arm.id, franchise, anchor: anchor.iso, intent,
      rules: rendered ? { dialect: rendered.dialect, sha256: rendered.sha256, chars: rendered.text.length, unfilled: rendered.unfilled, staff_names_loaded: rules?.staff?.length ?? 0 } : null,
      vision: { chars: vision.length, sha256: sha256(vision) }, full: { chars: full.length, sha256: sha256(full) },
      tools_sha256: sha256(JSON.stringify(arm.tool.definitions)), context_line: ctx, meta: arm.meta(),
    }, null, 2));
    if (typeof args["dump-prompts"] === "string") {
      const dir = path.resolve(args["dump-prompts"]);
      fs.mkdirSync(dir, { recursive: true });
      const stem = `${arm.id.replace("+", "plus")}_${franchise}`;
      fs.writeFileSync(path.join(dir, `${stem}_round0.txt`), vision);
      fs.writeFileSync(path.join(dir, `${stem}_round1plus.txt`), full);
      fs.writeFileSync(path.join(dir, `${stem}_tools.json`), JSON.stringify(arm.tool.definitions, null, 2));
      console.log(`prompts written to ${dir} (they can hold staff names: keep them out of git)`);
    }
    return;
  }

  // ---- collect cases
  let cases: CaseT[];
  if (typeof args.cases === "string") {
    cases = fs.readFileSync(args.cases, "utf8").split(/\r?\n/).filter((l) => l.trim())
      .map((l, i) => {
        const parsed = Case.safeParse(JSON.parse(l));
        if (!parsed.success) throw new Error(`case line ${i + 1}: ${parsed.error.message}`);
        return parsed.data;
      });
  } else if (typeof args.q === "string") {
    const history = typeof args.history === "string" ? z.array(Turn).parse(JSON.parse(fs.readFileSync(args.history, "utf8"))) : [];
    cases = [{ question: args.q, history }];
  } else {
    throw new Error("give --q \"<question>\" or --cases <file.jsonl> (or --dry-run)");
  }
  for (const c of cases) {
    assertFranchise(c.franchise ?? defaultFranchise);
    parseAnchor(c.anchor ?? defaultAnchor ?? "");
  }

  const concurrency = Math.min(3, Math.max(1, Number(args.concurrency ?? 1) || 1));
  const runId = typeof args["run-id"] === "string" ? args["run-id"] : newRunId(armArg.replace("+", "plus"));
  const log = new RunLog(runId);
  console.error(`run ${runId}: ${cases.length} case(s), arm ${armArg}, concurrency ${concurrency} -> ${path.relative(process.cwd(), log.file) || log.file}`);

  // Units of work: a lone case, or a whole conversation (same conv + franchise) in turn order.
  const runIndex = args["run-index"] !== undefined ? Number(args["run-index"]) : undefined;
  const units: { c: CaseT; i: number }[][] = [];
  const convs = new Map<string, { c: CaseT; i: number }[]>();
  cases.forEach((c, i) => {
    if (!c.conv) { units.push([{ c, i }]); return; }
    const key = `${c.conv}@${c.franchise ?? defaultFranchise}`;
    if (!convs.has(key)) { const u: { c: CaseT; i: number }[] = []; convs.set(key, u); units.push(u); }
    convs.get(key)!.push({ c, i });
  });
  for (const u of convs.values()) u.sort((a, b) => (a.c.turn ?? 0) - (b.c.turn ?? 0));

  let next = 0, failed = 0;
  const worker = async () => {
    for (;;) {
      const u = next++;
      if (u >= units.length) return;
      let convHistory: ChatTurn[] | null = null;
      for (const { c, i } of units[u]) {
        try {
          const arm = await getArm(c.arm ?? armArg);
          const history: ChatTurn[] = convHistory ?? ((c.history ?? []) as ChatTurn[]);
          const rec = await runTurn({
            arm, franchise: (c.franchise ?? defaultFranchise)!, anchor: (c.anchor ?? defaultAnchor)!,
            question: c.question, history, runId, caseId: c.id ?? `case-${i + 1}`,
            embeddings: !!args.embeddings,
          });
          const logged = { ...rec, ...(runIndex !== undefined ? { run: runIndex } : {}),
            ...(c.conv ? { conv: c.conv, turn: c.turn } : {}) };
          await log.append(logged as TurnRecord);
          console.log(args.json ? JSON.stringify(rec, null, 2) : summary(rec));
          if (rec.errors.length) failed++;
          if (c.conv) convHistory = [...history, { role: "user", content: c.question }, { role: "assistant", content: rec.answer }];
        } catch (e) {
          failed++;
          console.error(`case ${c.id ?? i + 1} failed: ${e instanceof Error ? e.message : String(e)}`);
          if (c.conv) break;   // a broken conversation can't continue meaningfully
        }
      }
    }
  };
  await Promise.all(Array.from({ length: concurrency }, worker));
  console.error(`done: ${cases.length - failed}/${cases.length} without errors; log ${log.file}; DAX throttle waited ${daxLimiter.waitedMs} ms`);
}

main()
  .catch((e) => { console.error(e instanceof Error ? e.message : String(e)); process.exitCode = 1; })
  .finally(() => closePools());

export { FRANCHISES, RUNS_DIR };
