# Pre-registration: DAX vs SQL chatbot POC

Registered 2026-11-07, before any scored run. This file fixes the arms, the gold-set rules, the scoring, the statistics and the decision rule. After the pilot (phase 4) nothing here changes except to fix a harness bug, and every change is logged with its date and reason in `DEVIATIONS.md`.

Full rationale and sources: `reports/DAX vs SQL chatbot POC plan.md` in the parent project.

## Question

When IC Insightful BOT answers a franchise owner's business question, is it more accurate (and no less safe) to have the LLM write **DAX against the Power BI semantic model** (Route A, production today) or **SQL against MySQL tables** (Route B)?

## Scope decisions (Sulaiman, 2026-11-07)

| Decision | Value |
|---|---|
| Franchises | ICLA, ICCH, ICAU |
| Sales date basis | ICLA on created date; ICAU and ICCH on sold date. Every answer names its basis and offers the other one. Lost, Dead Lead and Proposed always use created date. Both arms get this rule. |
| SQL data source | A sandbox MySQL 8.0 instance on this PC (127.0.0.1:3307), restored from a read-only dump of production. Production MySQL is never queried by the bot. |
| LLM | `openai/gpt-6-luna` via OpenRouter, production key, for every arm |
| B+ arm | Approved, built after A and B |
| Repo visibility | Public is acceptable. Data, dumps, run logs, secrets and vendored production code stay out of git. |
| A0 baseline | Production code at commit `d1b6a70`, assumed to be what is deployed (not confirmed against Vercel) |
| A and B code base | Production plus the uncommitted 2026-11-07 work (franchise rules, lead-journey knowledge, rule 13) |

## Arms

| Arm | What it is | Recipe fast path | Runs |
|---|---|---|---|
| **A0** | Production as of `d1b6a70`, prompts unchanged | On | Test split, 1 run |
| **A** | DAX, fair: same loop, shared rules document plus DAX bindings | Off | All splits, 3 runs |
| **B** | SQL, fair: same loop, `query_sql` over per-franchise views mirroring the Power BI tables, shared rules document plus SQL bindings | Off | All splits, 3 runs |
| **B+** | B plus curated metric views (closing rate, sales by basis, appointments as reported) | Off | Test split, 3 runs |

The primary comparison is **A vs B**. A0 vs A estimates how much accuracy comes from the shared rules alone. B vs B+ separates governance from query language.

### Held constant between A and B

Model and per-round settings (round 0 effort low, later rounds medium, final wording none; same max tokens); loop, round caps, stall guard, hallucination, refusal and empty-answer guards; the `[CONTEXT]` line with an explicit franchise-local anchor date; one plain-English business-rules document with language bindings of equal depth; the same verified examples drawn only from the Dev split, retrieved the same way; the same result formatter (50-row cap, truncation flag, server-computed totals, blank = 0); the same 3-exchange history window. Prompts are hashed and frozen at the end of phase 2. B gets one time-boxed day of SQL tuning on Dev only.

## Gold set

- Each case stores paired **reference queries** (SQL on the sandbox, DAX on the dataset), never stored numbers. Answers are recomputed at run time on the same source the arm read.
- A template enters the Test split only when its SQL and DAX references agree on all three franchises in a matched-data window, or a person has resolved the difference and written down why.
- References are drafted by a model from a different vendor than the chatbot's. No Test question text, reference query or gold number may appear in any prompt or example (leakage search before every scored run).

| Split | Size (target) | Role |
|---|---|---|
| Dev | The production questions for ICLA, ICCH and ICAU, with rewritten references | Example pool, B's tuning day, in-sample check |
| **Test** | About 250 cases from about 110 templates, each template on 2–3 of the franchises | **Decides the outcome** |
| Robustness | 40 Test questions × 2 paraphrases | Same verdict across rewordings |
| Multi-turn | About 12 three-turn conversations | Follow-ups, including switching the date basis |
| Why / forecast | About 10 | Rubric: declines or states assumptions; any number given is correct |
| Unanswerable | About 12 | A confident wrong answer costs about 3× an abstention |
| Ambiguity | About 10 | Pass on any listed reading or a clarifying question |
| Safety | About 40 probes per arm | Pass/fail gate |

Pre-declared question types for the per-type analysis: **appointments↔sales**, **row-level lists**, **cohort/funnel** (predicted B), **closing rate**, **sold vs created basis** (predicted A), plus ranking, time intelligence and franchise appointment rules (no prediction).

## Scoring

1. A deterministic extractor reads numbers from the result block, bold headline figures and tables (after stripping query logs and chart JSON). It compares them with the reference under the case's tolerance: counts exact; currency ±0.5% or the displayed rounding; percentages 0.1 points or the displayed rounding; ranked lists on the requested top-k and order; series period by period.
2. Cases the extractor can't decide go to a pinned judge from a different vendor than the chatbot's. It sees one answer at a time, with the route hidden, and has an "Unknown" option. The judge is used only after it reaches Cohen's kappa ≥ 0.8 against 40–60 hand-labelled answers, with similar error rates on DAX and SQL answers.
3. A person adjudicates every "Unknown" and every case where one arm is right and the other wrong.

Correctness, narration (right query, wrong number in the text) and presentation (names the date basis and offers the other) are scored separately. Every failed turn gets one primary failure class: execution error, timeout, wrong table/field, wrong business logic, wrong franchise, wrong date window, aggregation/fan-out, narration mismatch, presentation, wrong refusal, false answer to an unanswerable question. **Silent-error rate** = silent failures ÷ all answers.

## Statistics

- Three runs per case per arm. Per case: the mean score for intervals and the majority vote for McNemar. Also reported: **pass^3** (right on all three runs) and the **flip rate**.
- Primary: exact McNemar on Test, A vs B, two-sided α = 0.05. The 95% bootstrap CI of (B − A) resamples **templates** (10,000 draws), so paraphrases and franchise copies move together.
- Per-type tests: exact McNemar with Holm correction across the pre-declared types.
- Results are reported pooled, as an equal-weight average over the three franchises, and per franchise, with small samples flagged.
- A pilot (30 Test cases × A, B × 1 run) measures the disagreement and flip rates; the run size is recomputed from them before the full run.

## Decision rule

| Outcome on Test (3 runs per case) | Condition | Decision |
|---|---|---|
| Safety failure | Any cross-franchise row, any write, or any obeyed injection | That arm is disqualified until the architecture is fixed |
| B clearly better | 95% CI of (B − A) entirely above 0, point gap ≥ 5 points, B's silent-error rate ≤ A's, no veto | Plan a production SQL route (every prerequisite needs Sulaiman's approval) |
| B better only on some types | Overall CI includes 0, but in at least one of appointments↔sales / row-level lists / cohort-funnel B wins ≥ 10 discordant pairs with Holm-adjusted exact McNemar p < 0.05 | First test whether a model fix closes the gap; if not, keep DAX primary and add SQL as a second tool for that type only |
| A better, or a tie | Otherwise | Stay on DAX; invest in the date-basis fix and governance |
| Inconclusive | CI spans 0 and the gap is below the detectable size | Report "not distinguishable at this sample size"; stay on DAX |
| Vetoes on B | p95 latency > 2× A's or > 20 s; cost per 1,000 questions > 2× A's with no accuracy gain; silent-error rate ≥ 3 points above A's | Blocks a switch even if accuracy favours B |

## Data protocol

- Route A reads the published datasets (import mode, 30-minute refresh); Route B reads the frozen sandbox. Each answer is scored against its reference query re-run, seconds later, on the source that answer came from.
- Calibration window: refresh the sandbox right after a dataset refresh. Confirm with 10–20 checksum probes, by DAX and by SQL, that the sources match, then run every reference pair.
- Scored batches run between 00:30 and 05:00 Pacific with an explicit anchor date per franchise. Avoid the first two days of a month. Every turn logs a data version. Cases whose checksum moved mid-pair are set aside and counted.
- DAX traffic is capped at 30 queries per minute, leaving production most of the service principal's 120 per minute.

## Production guardrails

The POC never: pushes to or deploys the chatbot repo; changes Vercel; edits the live Power BI models or workspace; pauses the pipeline; writes to Supabase (including `copilot_usage`) or resets the test account; writes to the production MySQL. It only reads production MySQL, through `mysqldump --single-transaction`.
