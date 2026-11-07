/**
 * OpenRouter client. Ported from production app/api/copilot/route.ts:
 *   requestSettingsFor / effortFor   per-turn settings (env overrides removed: the POC pins them)
 *   fetchUpstream / readUntil / readChunk   the stall guard, unchanged in logic:
 *     headers timeout 25 s; first MEANINGFUL output within 25 s (45 s at effort medium+), heartbeats do not
 *     count; mid-stream idle 30 s; one retry on timeout, 5xx, 429 or 408.
 *   SSE accumulation of content, tool-call deltas, reasoning_details, finish_reason and usage.
 * Additions for the POC: the served model, provider and generation id OpenRouter reports, the reported cost
 * when present, timing per attempt, and the attempt count.
 */
import type { ReadableStreamDefaultReader, ReadableStreamReadResult } from "node:stream/web";
import { openrouterKey } from "./env";

export const OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions";
/** Every arm, every call (plan: held constant). Production .env.local overrides to Gemini are ignored. */
export const POC_MODEL = "openai/gpt-6-luna";

export type Effort = "none" | "low" | "medium" | "high" | "xhigh";
export type Turn = "vision" | "dax" | "wrap";

const isOpenAIReasoner = (model: string) => /^openai\/gpt-(5|6)/.test(model);

/** route.ts effortFor without the OPENROUTER_*_REASONING_EFFORT env overrides. */
export function effortFor(model: string, turn: Turn): Effort | null {
  if (isOpenAIReasoner(model)) return turn === "dax" ? "medium" : turn === "wrap" ? "none" : "low";
  return /^google\/gemini-3/.test(model) ? "low" : null;
}

/** route.ts requestSettingsFor without OPENROUTER_VERBOSITY (unset in production as far as the repo shows). */
export function requestSettingsFor(model: string, turn: Turn, maxTokens: number): Record<string, unknown> {
  const effort = effortFor(model, turn);
  const out: Record<string, unknown> = {};
  if (!isOpenAIReasoner(model)) out.temperature = 0.3;
  out.max_tokens = effort === "medium" || effort === "high" || effort === "xhigh" ? Math.max(maxTokens, 8000) : maxTokens;
  if (effort) out.reasoning = { effort };
  return out;
}

// Pricing table from route.ts (USD per 1M tokens) and its cached-token approximation.
const PRICING_USD: Record<string, { input: number; output: number }> = {
  "openai/gpt-6-luna": { input: 0.10, output: 0.50 },
  "openai/gpt-6-luna-pro": { input: 0.10, output: 0.50 },
};
const CACHED_INPUT_DISCOUNT = 0.25;
export function computeCostUsd(model: string, prompt = 0, completion = 0, cached = 0): number | null {
  const p = PRICING_USD[model];
  if (!p) return null;
  const cachedT = Math.min(cached, prompt);
  const i = ((prompt - cachedT) * p.input + cachedT * p.input * CACHED_INPUT_DISCOUNT) / 1_000_000;
  const o = (completion * p.output) / 1_000_000;
  return Number((i + o).toFixed(6));
}

const UPSTREAM_HEADERS_TIMEOUT_MS = 25_000;
const UPSTREAM_IDLE_TIMEOUT_MS = 30_000;
export const UPSTREAM_TIMEOUT_MSG = "the AI model took too long to respond. Please ask your question again";
const UPSTREAM_FIRST_OUTPUT_TIMEOUT_MS = 25_000;
const UPSTREAM_FIRST_OUTPUT_REASONING_TIMEOUT_MS = 45_000;
const MEANINGFUL_SSE = /"content":"[^"]|"tool_calls"|"reasoning":"[^"]|"reasoning_details":\[\{|"finish_reason":"|"error"/;
const SSE_DATA_LINE = /(^|\n)data:/;
const sseText = new TextDecoder();

function headers(): Record<string, string> {
  return {
    Authorization: `Bearer ${openrouterKey()}`,
    "Content-Type": "application/json",
    // Distinct title so POC traffic is separable from the portal's in OpenRouter's activity view.
    "HTTP-Referer": "https://inspired-closets-saas-reporting.vercel.app",
    "X-Title": "IC Insightful BOT POC (daxvssql, local)",
  };
}

function concatChunks(parts: Uint8Array[]): Uint8Array {
  if (parts.length === 1) return parts[0];
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let o = 0;
  for (const p of parts) { out.set(p, o); o += p.length; }
  return out;
}

async function readUntil(
  reader: ReadableStreamDefaultReader<Uint8Array>, ac: AbortController, timeoutMs: number,
  accept: (text: string) => boolean,
): Promise<ReadableStreamReadResult<Uint8Array>> {
  const deadline = Date.now() + timeoutMs;
  const parts: Uint8Array[] = [];
  for (;;) {
    const remaining = deadline - Date.now();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const expired = new Promise<never>((_, reject) => {
      timer = setTimeout(() => { ac.abort(); reject(new Error(UPSTREAM_TIMEOUT_MSG)); }, Math.max(0, remaining));
    });
    let r: ReadableStreamReadResult<Uint8Array>;
    try {
      r = await Promise.race([reader.read(), expired]);
    } finally {
      if (timer) clearTimeout(timer);
    }
    if (r.done) return parts.length ? { done: false, value: concatChunks(parts) } : r;
    parts.push(r.value);
    if (accept(sseText.decode(r.value))) return { done: false, value: concatChunks(parts) };
  }
}

export interface UpstreamAttempt { attempt: number; status?: number; error?: string; ms: number }

async function fetchUpstream(model: string, body: Record<string, unknown>, attempts: UpstreamAttempt[]):
  Promise<{ reader: ReadableStreamDefaultReader<Uint8Array>; first: ReadableStreamReadResult<Uint8Array>; ac: AbortController; firstOutputMs: number }> {
  let lastErr: Error = new Error("upstream unavailable");
  for (let attempt = 0; attempt < 2; attempt++) {
    const t0 = Date.now();
    const ac = new AbortController();
    const timer = setTimeout(() => ac.abort(), UPSTREAM_HEADERS_TIMEOUT_MS);
    try {
      const resp = await fetch(OPENROUTER_URL, { method: "POST", headers: headers(), body: JSON.stringify(body), signal: ac.signal });
      clearTimeout(timer);
      if (resp.ok && resp.body) {
        const effort = (body.reasoning as { effort?: string } | undefined)?.effort;
        const firstMs = effort === "medium" || effort === "high" || effort === "xhigh"
          ? UPSTREAM_FIRST_OUTPUT_REASONING_TIMEOUT_MS : UPSTREAM_FIRST_OUTPUT_TIMEOUT_MS;
        const reader = resp.body.getReader();
        try {
          const first = await readUntil(reader, ac, firstMs, (t) => MEANINGFUL_SSE.test(t));
          attempts.push({ attempt: attempt + 1, status: resp.status, ms: Date.now() - t0 });
          return { reader, first, ac, firstOutputMs: Date.now() - t0 };
        } catch {
          lastErr = new Error(UPSTREAM_TIMEOUT_MSG);
          attempts.push({ attempt: attempt + 1, status: resp.status, error: `stalled: no output in ${firstMs} ms`, ms: Date.now() - t0 });
          continue;
        }
      }
      const detail = await resp.text().catch(() => "");
      lastErr = new Error(`Upstream ${model} ${resp.status}: ${detail.slice(0, 400)}`);
      attempts.push({ attempt: attempt + 1, status: resp.status, error: lastErr.message, ms: Date.now() - t0 });
      const retryable = resp.status >= 500 || resp.status === 429 || resp.status === 408;
      if (!retryable) break;
    } catch (e) {
      clearTimeout(timer);
      lastErr = ac.signal.aborted ? new Error(UPSTREAM_TIMEOUT_MSG) : (e instanceof Error ? e : new Error(String(e)));
      attempts.push({ attempt: attempt + 1, error: lastErr.message, ms: Date.now() - t0 });
    }
  }
  throw lastErr;
}

export interface ToolCallAcc { id: string; name: string; args: string }

export interface RoundCall {
  content: string;
  toolCalls: ToolCallAcc[];
  finishReason: string | null;
  reasoningDetails: Record<string, unknown>[];
  usage: { promptTokens: number; completionTokens: number; cachedTokens: number; reasoningTokens: number; reportedCostUsd: number | null };
  served: { model: string | null; provider: string | null; generationId: string | null };
  firstOutputMs: number;
  totalMs: number;
  attempts: UpstreamAttempt[];
  streamErrors?: string[];
}

/**
 * One streamed chat completion with the stall guard. `onContent` receives content deltas as they arrive
 * (the engine routes them through the malformed-tool-call probe, as the route does).
 */
export async function streamChat(
  model: string, body: Record<string, unknown>, onContent: (delta: string) => void,
): Promise<RoundCall> {
  const t0 = Date.now();
  const attempts: UpstreamAttempt[] = [];
  const { reader, first, ac, firstOutputMs } = await fetchUpstream(model, body, attempts);
  const decoder = new TextDecoder();
  const acc = new Map<number, ToolCallAcc>();
  const reasoningDetails: Record<string, unknown>[] = [];
  const usage = { promptTokens: 0, completionTokens: 0, cachedTokens: 0, reasoningTokens: 0, reportedCostUsd: null as number | null };
  const served = { model: null as string | null, provider: null as string | null, generationId: null as string | null };
  const streamErrors: string[] = [];
  let content = "";
  let finishReason: string | null = null;
  let buffer = "";
  let pending: ReadableStreamReadResult<Uint8Array> | null = first;
  let done = false;
  while (!done) {
    const r = pending ?? await readUntil(reader, ac, UPSTREAM_IDLE_TIMEOUT_MS, (t) => SSE_DATA_LINE.test(t));
    pending = null;
    if (r.done) break;
    buffer += decoder.decode(r.value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const raw of lines) {
      const line = raw.trim();
      if (!line.startsWith("data:")) continue;
      const payload = line.slice(5).trim();
      if (payload === "[DONE]") { done = true; break; }
      try {
        const json = JSON.parse(payload);
        if (json?.model && !served.model) served.model = json.model;
        if (json?.provider && !served.provider) served.provider = json.provider;
        if (json?.id && !served.generationId) served.generationId = json.id;
        // Production ignores an error object inside the stream (the round just ends without content);
        // the POC keeps that behaviour and records it.
        if (json?.error) streamErrors.push(JSON.stringify(json.error).slice(0, 300));
        const choice = json?.choices?.[0];
        const delta = choice?.delta;
        if (delta?.content && typeof delta.content === "string") {
          content += delta.content;
          onContent(delta.content);
        }
        if (Array.isArray(delta?.tool_calls)) {
          for (const tc of delta.tool_calls) {
            const idx: number = tc.index ?? 0;
            const existing = acc.get(idx) ?? { id: "", name: "", args: "" };
            if (tc.id) existing.id = tc.id;
            if (tc.function?.name) existing.name = tc.function.name;
            if (typeof tc.function?.arguments === "string") existing.args += tc.function.arguments;
            acc.set(idx, existing);
          }
        }
        if (Array.isArray(delta?.reasoning_details)) reasoningDetails.push(...delta.reasoning_details);
        if (choice?.finish_reason) finishReason = choice.finish_reason;
        if (json?.usage) {
          usage.promptTokens += json.usage.prompt_tokens ?? 0;
          usage.completionTokens += json.usage.completion_tokens ?? 0;
          usage.cachedTokens += json.usage.cached_tokens ?? json.usage.prompt_tokens_details?.cached_tokens ?? 0;
          usage.reasoningTokens += json.usage.completion_tokens_details?.reasoning_tokens ?? 0;
          if (typeof json.usage.cost === "number") usage.reportedCostUsd = (usage.reportedCostUsd ?? 0) + json.usage.cost;
        }
      } catch {
        // tolerate keep-alives and partial JSON fragments (as the route does)
      }
    }
  }
  return {
    content, toolCalls: [...acc.entries()].sort((a, b) => a[0] - b[0]).map(([, v]) => v), finishReason,
    reasoningDetails, usage, served, firstOutputMs, totalMs: Date.now() - t0, attempts, streamErrors,
  };
}

/** route.ts phraseResultFallback's request: one bounded, non-streaming wording call. */
export async function completeOnce(model: string, body: Record<string, unknown>): Promise<RoundCall> {
  const t0 = Date.now();
  const resp = await fetch(OPENROUTER_URL, {
    method: "POST", headers: headers(), body: JSON.stringify({ ...body, stream: false }),
    signal: AbortSignal.timeout(UPSTREAM_HEADERS_TIMEOUT_MS + UPSTREAM_IDLE_TIMEOUT_MS),
  });
  if (!resp.ok) {
    const detail = await resp.text().catch(() => "");
    throw new Error(`wrap-up ${model} ${resp.status}: ${detail.slice(0, 200)}`);
  }
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const json = (await resp.json()) as any;
  const u = json?.usage ?? {};
  return {
    content: (json?.choices?.[0]?.message?.content ?? "").trim(),
    toolCalls: [], finishReason: json?.choices?.[0]?.finish_reason ?? null, reasoningDetails: [],
    usage: {
      promptTokens: u.prompt_tokens ?? 0, completionTokens: u.completion_tokens ?? 0,
      cachedTokens: u.cached_tokens ?? u.prompt_tokens_details?.cached_tokens ?? 0,
      reasoningTokens: u.completion_tokens_details?.reasoning_tokens ?? 0,
      reportedCostUsd: typeof u.cost === "number" ? u.cost : null,
    },
    served: { model: json?.model ?? null, provider: json?.provider ?? null, generationId: json?.id ?? null },
    firstOutputMs: Date.now() - t0, totalMs: Date.now() - t0,
    attempts: [{ attempt: 1, status: resp.status, ms: Date.now() - t0 }],
  };
}

export function mergeReasoningDetails(items: Record<string, unknown>[]): Record<string, unknown>[] {
  const merged = new Map<string, Record<string, unknown>>();
  for (const it of items) {
    const key = `${it.type}:${it.index ?? 0}`;
    const cur = merged.get(key);
    if (!cur) { merged.set(key, { ...it }); continue; }
    for (const f of ["text", "summary"]) if (typeof it[f] === "string") cur[f] = String(cur[f] ?? "") + it[f];
    for (const f of ["data", "signature", "id", "format"]) if (it[f] !== undefined && it[f] !== null) cur[f] = it[f];
  }
  return Array.from(merged.values());
}
