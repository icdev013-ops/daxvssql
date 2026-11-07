/**
 * One shared chatbot loop for every arm: a port of production app/api/copilot/route.ts POST(), minus what only
 * exists for the web app (Supabase auth, branch lookup, rate limiter, usage table, images, streaming to a browser,
 * DAX redaction of the stored answer). Faithful parts, in route order:
 *   chart-override strip -> intent -> [A0 only: recipe fast path + gates + wrap-up call + viz block]
 *   -> round 0 on the slim prompt, rounds 1+ on the full prompt (3 rounds, 5 for briefings; tools only while
 *      another tool round fits) -> requestSettingsFor per turn -> stall guard -> malformed tool-call recovery
 *   -> tool rounds with query-log markers -> refusal guard -> hallucination guard -> finish_reason "length"
 *   -> empty-answer guard (wording call, then deterministic text, then fixed texts) -> [fair arms when the working
 *      tree has it: rule-14 basis-line backstop] -> viz block -> continuation marker.
 * The CONTEXT date is the caller's anchor date, never the clock.
 */
import fs from "node:fs";
import path from "node:path";
import type { ArmRuntime } from "./arms";
import { VENDOR_DIR } from "./paths";
import { parseAnchor, SALES_BASIS } from "./context";
import { localIsoDate, sha256 } from "./util";
import { assertFranchise } from "./franchise";
import { deterministicResultText, formatSqlResultForModel, vizBlock, type QueryResult } from "./format";
import { CONTINUE_MARKER, queryLogMarker, TOOL_MARKER, visibleAnswer, visibleProseEmpty, windowHistory, type ChatTurn } from "./history";
import { completeOnce, computeCostUsd, effortFor, POC_MODEL, requestSettingsFor, streamChat, type RoundCall } from "./llm";
import { gateCandidate, tryMatchRecipe } from "./recipes";
import { executeDax } from "./tools/dax";
import type { QueryLogEntry, ToolContext } from "./tools/index";

const MAX_QUESTION_CHARS = 4000;
const ARGS_LOG_CAP = 8000;

export interface TurnInput {
  arm: ArmRuntime;
  franchise: string;
  anchor: string;
  question: string;
  history?: ChatTurn[];
  runId: string;
  caseId?: string;
  /** A0 only: allow the recipe matcher's read-only Upstash embedding pass (production's pass 2). */
  embeddings?: boolean;
}

export interface RoundRecord {
  round: number;
  kind: "vision" | "dax" | "wrap" | "fallback";
  requested_model: string;
  served_model: string | null;
  provider: string | null;
  generation_id: string | null;
  effort: string | null;
  max_tokens: number;
  tools_offered: boolean;
  system_prompt_sha256?: string;
  prompt_tokens: number;
  completion_tokens: number;
  cached_tokens: number;
  reasoning_tokens: number;
  cost_usd_estimated: number | null;
  cost_usd_reported: number | null;
  latency_ms: number;
  first_output_ms: number;
  finish_reason: string | null;
  tool_calls: { name: string; args: string }[];
  attempts: unknown[];
  stream_errors?: string[];
}

export interface TurnRecord {
  schema: "daxvssql.turn.v1";
  run_id: string;
  case_id?: string;
  started_at: string;
  finished_at: string;
  arm: string;
  arm_label: string;
  franchise: string;
  anchor_date: string;
  local_clock_date: string;
  anchor_matches_local_clock: boolean;
  question: string;
  history: ChatTurn[];
  history_window: { kept_turns: number; omitted_turns: number };
  intent: unknown;
  chart_override?: string;
  path: "fast" | "slow";
  recipe: Record<string, unknown> | null;
  answer: string;
  answer_visible: string;
  rounds: RoundRecord[];
  round_count: number;
  queries: QueryLogEntry[];
  query_count: number;
  tool_calls_count: number;
  guards_fired: string[];
  needs_continuation: boolean;
  errors: string[];
  tokens: { prompt: number; completion: number; cached: number; reasoning: number };
  cost_usd: { estimated: number; reported: number | null };
  latency_ms: number;
  context_line?: string;
  prompts: Record<string, string | null>;
  data_version: Record<string, unknown>;
  arm_meta: Record<string, unknown>;
  /** Both pinned SAAS commits, on every line: A0 = prod (d1b6a70), A and B = wt (9ef5e4f). */
  code_base: Record<string, unknown>;
  live_queries_available: boolean;
}

function roundRecord(round: number, kind: RoundRecord["kind"], body: Record<string, unknown>, call: RoundCall, systemSha?: string): RoundRecord {
  const effort = (body.reasoning as { effort?: string } | undefined)?.effort ?? null;
  return {
    round, kind, requested_model: String(body.model), served_model: call.served.model, provider: call.served.provider,
    generation_id: call.served.generationId, effort, max_tokens: Number(body.max_tokens), tools_offered: !!body.tools,
    system_prompt_sha256: systemSha,
    prompt_tokens: call.usage.promptTokens, completion_tokens: call.usage.completionTokens,
    cached_tokens: call.usage.cachedTokens, reasoning_tokens: call.usage.reasoningTokens,
    cost_usd_estimated: computeCostUsd(String(body.model), call.usage.promptTokens, call.usage.completionTokens, call.usage.cachedTokens),
    cost_usd_reported: call.usage.reportedCostUsd,
    latency_ms: call.totalMs, first_output_ms: call.firstOutputMs, finish_reason: call.finishReason,
    tool_calls: call.toolCalls.map((t) => ({ name: t.name, args: t.args.slice(0, ARGS_LOG_CAP) })),
    attempts: call.attempts, ...(call.streamErrors?.length ? { stream_errors: call.streamErrors } : {}),
  };
}

/** Query text from GLM-style XML tool markup (production's recovery, with a SELECT/WITH check for SQL). */
function extractFromMalformed(arm: ArmRuntime, text: string): string | null {
  if (arm.tool.dialect === "dax") return arm.vendor.extractDaxFromMalformed(text);
  const cands = [
    /<arg_value>([\s\S]*?)<\/arg_value>/i, /<arg_value>([\s\S]*?)<\/tool_call>/i, /<arg_value>([\s\S]*?)```/, /<arg_value>([\s\S]+)/i,
  ].map((re) => text.match(re)?.[1]).filter((x): x is string => !!x);
  const direct = text.match(/(?:SELECT|WITH)\b[\s\S]+?(?=<\/(?:arg_value|tool_call)>|```|$)/i);
  if (direct) cands.push(direct[0]);
  for (const raw of cands) {
    const cleaned = raw.replace(/^```[a-z]*\s*/i, "").replace(/```\s*$/i, "").trim();
    if (/^(SELECT|WITH)\b/i.test(cleaned)) return cleaned;
  }
  return null;
}

type Msg = Record<string, unknown>;

let codeBaseCache: Record<string, unknown> | null = null;
function codeBase(armId: string): Record<string, unknown> {
  if (!codeBaseCache) {
    const read = (v: "prod" | "wt") => {
      try {
        const m = JSON.parse(fs.readFileSync(path.join(VENDOR_DIR, v, "MANIFEST.json"), "utf8"));
        return { ref: m.ref, commit: m.commit, bundle_sha256: m.combined_sha256, vendored_at: m.vendored_at };
      } catch { return null; }
    };
    codeBaseCache = { a0: read("prod"), a_b: read("wt") };
  }
  return { ...codeBaseCache, this_arm_uses: armId === "A0" ? "a0" : "a_b" };
}

export async function runTurn(input: TurnInput): Promise<TurnRecord> {
  const started = Date.now();
  const startedAt = new Date().toISOString();
  const arm = input.arm;
  const v = arm.vendor;
  const branch = assertFranchise(input.franchise);
  const anchor = parseAnchor(input.anchor);
  const question = (input.question ?? "").trim();
  if (!question) throw new Error("question is required");

  const errors: string[] = [];
  const guardsFired: string[] = [];
  const rounds: RoundRecord[] = [];
  const log: QueryLogEntry[] = [];
  const counters = { toolCallsCount: 0, queriesCount: 0 };
  const lastResult: { result?: QueryResult } = {};
  const rules = arm.renderRules(branch, anchor);
  const prompts: Record<string, string | null> = { tools: sha256(JSON.stringify(arm.tool.definitions)), rules: rules?.sha256 ?? null };
  if (rules?.unfilled.length) errors.push(`rules placeholders left unfilled: ${rules.unfilled.join(", ")}`);
  const dataVersionP = arm.dataVersion(branch).catch((e) => ({ error: e instanceof Error ? e.message : String(e) }));
  const liveOk = arm.tool.liveAvailable(branch);

  let answer = "";
  let path: "fast" | "slow" = "slow";
  let recipeInfo: Record<string, unknown> | null = null;
  let needsContinuation = false;
  let contextLine: string | undefined;
  let historyWindow = { kept_turns: 0, omitted_turns: 0 };

  const { question: matchable, chartOverride } = v.extractChartOverride(question);
  const intent = v.detectIntent(matchable, false);

  const finish = async (): Promise<TurnRecord> => {
    const tokens = rounds.reduce((t, r) => ({
      prompt: t.prompt + r.prompt_tokens, completion: t.completion + r.completion_tokens,
      cached: t.cached + r.cached_tokens, reasoning: t.reasoning + r.reasoning_tokens,
    }), { prompt: 0, completion: 0, cached: 0, reasoning: 0 });
    const reported = rounds.every((r) => r.cost_usd_reported === null) ? null
      : Number(rounds.reduce((s, r) => s + (r.cost_usd_reported ?? 0), 0).toFixed(6));
    prompts.bundle = sha256(JSON.stringify(Object.entries(prompts).filter(([k]) => k !== "bundle").sort()));
    const local = localIsoDate();
    return {
      schema: "daxvssql.turn.v1", run_id: input.runId, case_id: input.caseId, started_at: startedAt,
      finished_at: new Date().toISOString(), arm: arm.id, arm_label: arm.label, franchise: branch,
      anchor_date: anchor.iso, local_clock_date: local, anchor_matches_local_clock: local === anchor.iso,
      question, history: input.history ?? [], history_window: historyWindow, intent,
      ...(chartOverride ? { chart_override: chartOverride } : {}), path, recipe: recipeInfo,
      answer, answer_visible: visibleAnswer(answer), rounds,
      round_count: rounds.filter((r) => r.kind === "vision" || r.kind === "dax").length,
      queries: log, query_count: log.length, tool_calls_count: counters.toolCallsCount,
      guards_fired: guardsFired, needs_continuation: needsContinuation, errors, tokens,
      cost_usd: { estimated: Number(rounds.reduce((s, r) => s + (r.cost_usd_estimated ?? 0), 0).toFixed(6)), reported },
      latency_ms: Date.now() - started, context_line: contextLine, prompts,
      data_version: await dataVersionP, arm_meta: arm.meta(), code_base: codeBase(arm.id), live_queries_available: liveOk,
    };
  };

  if (question.length > MAX_QUESTION_CHARS) {
    errors.push("Question too long.");
    return finish();
  }

  // ---------------------------------------------------------------- A0: recipe fast path
  if (arm.fastPath) {
    const history = input.history ?? [];
    const basisFollowup = v.route.HAS_BASIS_FOLLOWUP_SKIP && history.length > 0 &&
      /^(?:yes|yeah|yep|yup|sure|ok(?:ay)?|please|go ahead|do it|yes please|show me|(?:yes,?\s*)?(?:by|on|using)\s+(?:the\s+)?(?:sold|created)\s+date|(?:sold|created)\s+date(?:\s+please)?)[\s.!?]*$/i.test(matchable.trim());
    const m = basisFollowup ? { match: null, embeddingQueried: false } as Awaited<ReturnType<typeof tryMatchRecipe>>
      : await tryMatchRecipe(v, matchable, branch, intent.classes, anchor, { embeddings: !!input.embeddings });
    const { matched, skipReason } = gateCandidate(m.match, intent, matchable, v.route.BRANCH_SCALAR_RECIPES);
    recipeInfo = m.match
      ? { id: m.match.recipe.id, version: m.match.version, method: m.match.method, similarity: m.match.similarity ?? null,
          skip_reason: skipReason ?? null, embedding_queried: m.embeddingQueried, embedding_error: m.embeddingError ?? null }
      : { id: null, embedding_queried: m.embeddingQueried, embedding_error: m.embeddingError ?? null, embeddings_enabled: !!input.embeddings };

    if (matched) {
      let dax = "";
      let exec: Awaited<ReturnType<typeof executeDax>> | null = null;
      let failReason: string | undefined;
      try {
        dax = v.fillRecipeDax(matched.recipe, matched.placeholders);
      } catch (e) {
        failReason = e instanceof Error ? e.message : String(e);
      }
      if (!failReason) {
        exec = await executeDax(branch, dax, { closeOpenParens: v.closeOpenParens });
        log.push({ round: 0, dialect: "dax", query: dax, executed: exec.executed, ok: !exec.error, row_count: exec.rowCount,
          truncated: exec.truncated, error: exec.error, latency_ms: exec.latencyMs, throttle_wait_ms: exec.throttleWaitMs,
          columns: exec.columns, rows: exec.rows, raw: exec.raw });
        if (exec.error) failReason = `DAX error: ${exec.error}`;
      }
      const emptyNameLookup = !failReason && v.route.HAS_EMPTY_NAME_LOOKUP && exec!.rowCount === 0 && /\{NAME\d?\}/.test(matched.recipe.dax);
      if (!failReason && !emptyNameLookup && exec) {
        path = "fast";
        const formatted = v.formatDaxResultForModel(exec);
        const fallbackNote = exec.rowCount === 0 && matched.recipe.emptyFallbackHint
          ? `\n\nNote for the assistant: ${matched.recipe.emptyFallbackHint}` : "";
        const userContent = `Question: ${question}\n\nWhat this query computed: ${matched.recipe.title}. ${matched.recipe.returns.split(/ — | Phase \d/)[0]}\n\nResult table:\n${formatted}${fallbackNote}\n\nWrite the answer now.`;
        prompts.wrap = sha256(arm.wrapUpPrompt);
        const body = {
          model: POC_MODEL,
          messages: [{ role: "system", content: arm.wrapUpPrompt }, { role: "user", content: userContent }],
          stream: true, stream_options: { include_usage: true }, ...requestSettingsFor(POC_MODEL, "wrap", v.route.MAX_TOKENS),
        };
        try {
          const call = await streamChat(POC_MODEL, body, (d) => { answer += d; });
          rounds.push(roundRecord(0, "wrap", body, call, prompts.wrap ?? undefined));
          answer += vizBlock(exec, { question, branch, chartOverride, recipeId: matched.recipe.id, fastPath: true });
        } catch (e) {
          const msg = e instanceof Error ? e.message : String(e);
          errors.push(msg);
          answer += `\n\n_(error: ${msg})_`;
        }
        return finish();
      }
      recipeInfo.skip_reason = emptyNameLookup ? `empty_name_lookup:${matched.recipe.id}` : `exec_failed:${matched.recipe.id}: ${failReason}`;
    }
  }

  // ---------------------------------------------------------------- slow path
  const visionSystem = arm.buildVision(branch, intent, rules);
  const fullSystem = arm.buildFull(branch, intent, rules);
  prompts.vision = sha256(visionSystem);
  prompts.full = sha256(fullSystem);
  contextLine = arm.contextLine(anchor, branch, liveOk);

  const win = windowHistory(input.history ?? [], v.route.MAX_HISTORY_TURNS, v.route.MAX_HISTORY_VIZ_CHARS);
  historyWindow = { kept_turns: win.kept.length, omitted_turns: win.omitted };
  const isPleaseContinue = /^please\s+continue\.?$/i.test(question);
  const lastAssistant = [...win.kept].reverse().find((t) => t.role === "assistant");
  const priorFailed = !!lastAssistant && arm.guards.priorFailureRe.test(lastAssistant.content);
  const hint = isPleaseContinue && priorFailed ? arm.guards.continueAfterFailureHint : "";
  if (hint) guardsFired.push("continue_after_failure_hint");

  const messages: Msg[] = [{ role: "system", content: visionSystem }, ...win.messages, { role: "user", content: `${contextLine}\n\n${question}${hint}` }];
  const maxTokens = intent.needsBriefing ? 3500 : v.route.MAX_TOKENS;
  const maxRounds = intent.needsBriefing ? 5 : v.route.MAX_TOOL_ROUNDS;
  let hallucinationCorrected = false;

  try {
    for (let round = 0; round < maxRounds; round++) {
      const isLastRound = round === maxRounds - 1;
      if (round === 1) messages[0] = { role: "system", content: fullSystem };
      const turn = round === 0 ? "vision" : "dax";
      const body: Record<string, unknown> = {
        model: POC_MODEL,
        messages: messages.map((m) => ({ ...m })),
        stream: true,
        stream_options: { include_usage: true },
        ...requestSettingsFor(POC_MODEL, turn, maxTokens),
        ...(liveOk && !isLastRound ? { tools: arm.tool.definitions, tool_choice: "auto" } : {}),
      };
      let roundContent = "";
      const probe = v.newMalformedProbe();
      const onEmit = (t: string) => { roundContent += t; answer += t; };
      const call = await streamChat(POC_MODEL, body, (d) => v.probeDelta(probe, d, onEmit));
      v.probeFinish(probe, onEmit);
      rounds.push(roundRecord(round, turn, body, call, round === 0 ? prompts.vision! : prompts.full!));
      const ctx: ToolContext = { branch, anchor, round, counters, log, lastResult };

      // Malformed tool-call recovery (legacy GLM-4.5V markup in plain text).
      if (probe.suppressed && call.toolCalls.length === 0) {
        guardsFired.push("malformed_tool_call");
        if (isLastRound) { answer += arm.guards.malformedNoBudget; break; }
        const extracted = extractFromMalformed(arm, probe.accumulated);
        if (extracted) {
          answer += TOOL_MARKER;
          const id = `recover_${round}_${Date.now()}`;
          messages.push({ role: "assistant", content: null, tool_calls: [{ id, type: "function", function: { name: arm.tool.name, arguments: JSON.stringify({ [arm.tool.argName]: extracted }) } }] });
          const result = await arm.tool.dispatch(arm.tool.name, { [arm.tool.argName]: extracted }, ctx);
          if (log.length) log[log.length - 1].recovered_from_malformed = true;
          messages.push({ role: "tool", content: result, tool_call_id: id });
          answer += queryLogMarker(arm.marker, round, extracted);
          continue;
        }
        answer += arm.guards.malformedUnparseable;
        break;
      }

      if (call.finishReason === "tool_calls" && call.toolCalls.length > 0) {
        if (isLastRound) { needsContinuation = true; break; }
        answer += TOOL_MARKER;
        messages.push({
          role: "assistant", content: roundContent || null,
          tool_calls: call.toolCalls.map((c) => ({ id: c.id, type: "function", function: { name: c.name, arguments: c.args || "{}" } })),
        });
        for (const c of call.toolCalls) {
          let parsed: Record<string, unknown> = {};
          try { parsed = JSON.parse(c.args || "{}"); } catch { parsed = { _parse_error: c.args }; }
          const result = await arm.tool.dispatch(c.name, parsed, ctx);
          messages.push({ role: "tool", content: result, tool_call_id: c.id });
          if (c.name === arm.tool.name) {
            const q = typeof parsed[arm.tool.argName] === "string" ? (parsed[arm.tool.argName] as string) : "";
            if (q.trim()) answer += queryLogMarker(arm.marker, round, q);
          }
        }
        continue;
      }

      // Refusal guard (excel / chart / report asked, model refused, no query run).
      const askedForExport = v.route.USER_ASKED_FOR_EXPORT_RE.test(question.toLowerCase());
      const containsRefusal = v.route.REFUSAL_PATTERNS.some((re) => re.test(roundContent));
      if (!hallucinationCorrected && askedForExport && counters.queriesCount === 0 && call.finishReason === "stop"
        && call.toolCalls.length === 0 && containsRefusal) {
        guardsFired.push(isLastRound ? "refusal_last_round" : "refusal");
        if (isLastRound) { answer += arm.guards.refusalLastRoundNote; break; }
        hallucinationCorrected = true;
        messages.push({ role: "assistant", content: roundContent });
        messages.push({ role: "user", content: arm.guards.refusalCorrection });
        continue;
      }

      // Numerical hallucination guard (numbers stated with no query run).
      if (!hallucinationCorrected && counters.queriesCount === 0 && call.finishReason === "stop"
        && call.toolCalls.length === 0 && v.looksHallucinated(roundContent)) {
        guardsFired.push(isLastRound ? "hallucination_last_round" : "hallucination");
        if (isLastRound) { answer += arm.guards.hallucinationLastRoundNote; break; }
        hallucinationCorrected = true;
        answer += arm.guards.hallucinationNotice;
        messages.push({ role: "assistant", content: roundContent });
        messages.push({ role: "user", content: arm.guards.hallucinationCorrection });
        continue;
      }

      if (call.finishReason === "length") needsContinuation = true;
      break;
    }

    // Empty-answer guard.
    if (visibleProseEmpty(answer)) {
      guardsFired.push("empty_answer");
      const r = lastResult.result;
      const haveResult = !!r && !r.error && (r.rowCount ?? 0) > 0 && !!r.rows && r.rows.length > 0;
      let filled = "";
      if (haveResult) {
        try {
          const formatted = arm.tool.dialect === "dax" ? v.formatDaxResultForModel(r) : formatSqlResultForModel(r!);
          prompts.wrap = sha256(arm.wrapUpPrompt);
          const body = {
            model: POC_MODEL,
            messages: [{ role: "system", content: arm.wrapUpPrompt }, { role: "user", content: `Question: ${question}\n\nResult table:\n${formatted}\n\nWrite the answer now.` }],
            ...requestSettingsFor(POC_MODEL, "wrap", v.route.MAX_TOKENS),
          };
          const call = await completeOnce(POC_MODEL, body);
          rounds.push(roundRecord(rounds.length, "fallback", body, call, prompts.wrap));
          filled = call.content;
        } catch (e) {
          errors.push(`empty-answer wrap-up failed: ${e instanceof Error ? e.message : String(e)}`);
        }
        if (!filled.trim()) filled = deterministicResultText(r!);
      } else if (log.some((e) => e.ok)) {
        filled = arm.guards.emptyNoRows;
      } else {
        filled = arm.guards.emptyFailed;
      }
      if (filled.trim()) answer += (answer === "" || answer.endsWith("\n") ? "" : "\n\n") + filled;
      needsContinuation = false;
    }

    // Rule-14 basis-line backstop (working tree 4fb371d+; fair arms only).
    if (arm.basisLineBackstop) {
      const OFFER = { sold: "Want this by created date too?", created: "Want this by sold date too?" } as const;
      const LABEL = { sold: "by sold date", created: "by created date" } as const;
      const visible = answer.replace(/\[(?:DAX|QUERY)_LOG_R\d+:[^\n]*\]\n/g, "");
      const offersCreated = visible.includes(OFFER.sold);
      const offersSold = visible.includes(OFFER.created);
      if (offersCreated !== offersSold) {
        const used = offersCreated ? "sold" : "created";
        if (!/\b(?:sold|created)[- ]date\b/i.test(visible.split(OFFER[used]).join(""))) {
          answer += `\n\n_Figures are ${LABEL[used]}._`;
          guardsFired.push("basis_line_backstop");
        }
      }
    }

    answer += vizBlock(lastResult.result, { question, branch, chartOverride });
    // The "[CONTINUE_AVAILABLE]" sentinel is post-stream only in production (the client strips it and it is never
    // part of the stored answer); the record carries it as needs_continuation instead.
    void CONTINUE_MARKER;
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    errors.push(msg);
    answer += `\n\n_(error: ${msg})_`;
  }
  return finish();
}

export const _internals = { effortFor, SALES_BASIS };
