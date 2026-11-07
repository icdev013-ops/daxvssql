# bot: the POC chatbot runner

A standalone TypeScript (Node 22, run with `tsx`) copy of the IC Insightful BOT loop with a pluggable query tool per test arm. It never touches Supabase, the portal's rate limiter, Next.js, the production MySQL on port 3306, or any file in the chatbot repo (it only runs `git show` and `git rev-parse` there).

| Arm | Code base | Query tool | Recipe fast path | Shared rules |
|---|---|---|---|---|
| `A0` | SAAS commit `d1b6a70` (believed deployed) | `query_dax` | on | none (production prompts) |
| `A` | SAAS commit `9ef5e4f` (production + 2026-11-07 work) | `query_dax` | off | `rules/business_rules.md` + `rules/dax_bindings.md` |
| `B` | same loop and guards as A; SQL prompt pair in `src/prompts/sql.ts` | `query_sql` (sandbox views) | off | `rules/business_rules.md` + `rules/sql_bindings.md` |
| `B+` | stub (curated metric views come later) | | | |

## Setup

```bash
cd daxvssql/bot
npm install
npm run vendor        # git show d1b6a70 -> .vendor/prod, git show 9ef5e4f -> .vendor/wt (gitignored)
npm test              # gate, RLS and fidelity tests (the live RLS tests skip if the sandbox is down)
npm run typecheck
```

Secrets are read at runtime and never printed: `OPENROUTER_API_KEY`, `PBI_*` and `UPSTASH_VECTOR_*` from the chatbot repo's `.env.local`, and only `SANDBOX_ICLA_PASSWORD`, `SANDBOX_ICCH_PASSWORD`, `SANDBOX_ICAU_PASSWORD` from `daxvssql/.env` (every other key in that file is dropped while parsing). Arm B needs the sandbox running with its views (`python sandbox/sandbox.py start`).

## Running

```bash
# one question
npx tsx src/cli.ts --arm A --franchise ICLA --anchor 2026-11-07 --q "How much have we sold this year?"

# one question with prior turns ([{"role":"user","content":...},{"role":"assistant","content":...}])
npx tsx src/cli.ts --arm B --franchise ICLA --anchor 2026-11-07 --q "Yes, by sold date please" --history hist.json

# a batch: one JSON object per line {"id","question","franchise","anchor","history","arm"}
npx tsx src/cli.ts --arm B --anchor 2026-11-07 --cases ../runs/cases.jsonl --concurrency 2 --run-id pilot-B-1

# build the prompts only (no network): sizes, hashes, CONTEXT line; optionally write them out
npx tsx src/cli.ts --arm B --franchise ICAU --anchor 2026-11-07 --dry-run --dump-prompts ../runs/prompts
```

Options: `--franchise` must be ICLA, ICCH or ICAU; `--anchor` (YYYY-MM-DD) is the date the CONTEXT line and the rules carry, never the clock; `--concurrency` is 1 (default) or 2; `--rules-dir <dir>` / `--no-rules` change the shared rules; `--embeddings` lets A0's recipe matcher query the production Upstash index read-only (off by default); `--json` prints the whole record. Keep case files and prompt dumps under `daxvssql/runs/` (gitignored): answers and prompts can hold staff and designer names.

## What a run writes

One JSON line per turn to `daxvssql/runs/<run-id>.jsonl`: question, franchise, arm, anchor date (and whether it matches this PC's date), history and the window kept, the answer (as the portal would store it) and its visible text, every executed query with what actually ran and its raw result (50 rows, long text capped), guard firings, errors, tokens and cost per round (price-table estimate and OpenRouter's reported cost), latency per round, round count, the served model, provider and generation id of every call, a sha256 of every prompt, the tool definitions and the rendered rules, `code_base` (both pinned SAAS commits), and a data version: the dataset's last refresh (one cached GET per franchise per run) for A0 and A, `.sandbox/data_version.json` for B.

## How it maps to production (`app/api/copilot/route.ts`)

Ported as is, in route order: chart-type suffix strip; intent detection; A0's recipe matcher (regex pass, intent, entity, variance, briefing and breakdown gates, wrap-up call, viz block); round 0 on the slim prompt and rounds 1+ on the full prompt; 3 rounds (5 for briefings) with tools offered only while another tool round fits; `requestSettingsFor` (Luna: effort low on round 0, medium later with max_tokens raised to 8,000, none for wording, no temperature); the stall guard (25 s headers, first meaningful output within 25 s or 45 s at medium effort with heartbeats ignored, 30 s idle, one retry on timeout, 5xx, 429 or 408); malformed tool-call recovery; query-log markers; refusal and hallucination guards; finish_reason `length`; the empty-answer guard (wording call, deterministic text, fixed texts); the 3-exchange history window with dead-turn cleanup; the result formatter (50-row cap, truncation flag, server-computed column totals, blank = 0) and the viz block. Prompts, recipes, intent rules, guard texts and constants are imported from the vendored code, not copied.

Deliberate differences:

- **Clock**: the CONTEXT date, `{YEAR}`/`{MONTH}` repair and recipe placeholders use the caller's anchor date. DAX `TODAY()` and SQL `CURDATE()` still run on the Power BI and sandbox server clocks; each record says whether the anchor matches this PC's date.
- **Model**: every call is `openai/gpt-6-luna`; the `OPENROUTER_*` model and effort environment overrides are ignored (the local `.env.local` points both turns at Gemini).
- **CONTEXT line, guards, markers (A and B)**: one shared CONTEXT line without DAX column or tool names, carrying the decided date basis; guard and wrap-up texts with the DAX words made neutral (only the tool name differs); `[QUERY_LOG_Rn: …]` markers (A's vendored prompt text is rewritten to match). A0 keeps production's text exactly.
- **query_dax**: an `error` object inside an HTTP 200 body is an error (production reports it as no rows); throttled to 30 queries a minute per process; `closeOpenParens` only where the vendored code has it (9ef5e4f, not d1b6a70).
- **Not reproduced**: Supabase sign-in, branch lookup and rate limiter, the usage table (replaced by the JSONL log), screenshots, streaming to a browser, and the non-admin DAX redaction of the stored answer (the PRIVACY prompt block is still included). The `[CONTINUE_AVAILABLE]` sentinel is recorded as `needs_continuation`. OpenRouter requests carry a distinct `X-Title` so POC traffic is separable.
- **Arm B only**: the gate replaces the period-window guard; the formatter's two DAX hints in the no-rows message are bound to SQL and a truncated result says "more than 50" (the sandbox stops at 51 rows); example person names in the cookbook are placeholders such as `<designer>` because this repo is public.

## Row-level security on the SQL route

- Only the three franchise accounts `poc_icla`, `poc_icch`, `poc_icau` are used; the sandbox administrator account is never read or used (static test).
- The account comes from the caller's franchise, checked against ICLA / ICCH / ICAU; `query_sql` reads only its `sql` argument, so a franchise or schema argument from the model is ignored (live test).
- One connection pool per franchise, created with that franchise's user and default schema and never re-pointed; every new connection gets `max_execution_time = 10000`, `TRANSACTION READ ONLY`, `ANSI_QUOTES` and `sql_select_limit = 51`, with `multipleStatements` off (live test).
- The gate (`src/tools/sqlGate.ts`) allows one SELECT / WITH … SELECT whose tables are that franchise's views (read through its own account) or CTEs; anything schema-qualified to another schema, hints `/*+`, executable comments `/*!`, INTO, locking reads, variables, `SLEEP`/`BENCHMARK`/`GET_LOCK`/`LOAD_FILE` and similar functions, and system schemas are rejected before execution. With the gate bypassed, MySQL itself denies cross-franchise reads (live test).

## Files

| Path | What |
|---|---|
| `scripts/vendor.mjs` | Extracts the production files at the two pinned commits into `.vendor/` and generates `route-consts.ts` from route.ts |
| `src/engine.ts` | The shared loop |
| `src/arms.ts` | A0 / A / B / B+ |
| `src/llm.ts` | OpenRouter client, per-turn settings, stall guard |
| `src/tools/` | `query_dax`, `query_sql`, the SQL gate, the DAX throttle |
| `src/prompts/sql.ts`, `sqlMetrics.ts` | Arm B's prompt pair (first draft) and the SQL form of the report's measures |
| `src/prompts/rules.ts` | Loads and fills the shared rules and bindings |
| `src/recipes.ts` | A0's recipe matcher |
| `test/` | `gate.test.ts`, `rls.test.ts`, `fidelity.test.ts` |
