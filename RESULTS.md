# Results: DAX vs SQL chatbot POC

Run date 2026-11-07, anchor date 2026-11-07. Franchises ICLA, ICCH, ICAU. Model `openai/gpt-6-luna` for every answer.

## Decision

Applying the pre-registered rule: **inconclusive, so stay on DAX.**

On 268 held-out test cases, the SQL route (B) scored **96.3%** and the DAX route (A) **94.8%**. The difference is **+1.5 points**, with a 95% confidence interval of −1.8 to +4.8 and an exact McNemar p of 0.48. The two routes disagreed on 18 cases (11 favour SQL, 7 favour DAX); at that rate the test can detect a gap of about 4.4 points. Once both routes get the same model, rules and examples, SQL is as accurate as DAX, but not enough better to justify building a production SQL route, the second copy of every business rule it implies, and per-franchise database isolation.

The governed-metric variant (B+, SQL plus curated metric views) did **not** help. It scored 94.0%, 2.2 points below plain SQL (95% CI −4.7 to 0.0, p = 0.11).

## Headline numbers (test split, one run per arm, adjudicated)

| | A: DAX | B: SQL | B+: SQL + metric views |
|---|---|---|---|
| Accuracy (268 cases) | **94.8%** | **96.3%** | 94.0% |
| ICAU / ICCH / ICLA | 93.6 / 94.8 / 96.1% | 96.8 / 94.8 / 97.4% | 93.6 / 93.8 / 94.7% |
| Silent errors (confident wrong answers) | 4.9% | 3.7% | 5.6% |
| Median latency | 6.9 s | 5.8 s | 6.6 s |
| 95th-percentile latency | 19.2 s | 16.4 s | 17.7 s |
| Cost per 1,000 questions (OpenRouter billed) | $1.24 | $1.37 | $1.42 |

B+ was run 3 times. It answered all three runs correctly on 88% of cases, and on 8% of cases the verdict changed between runs.

### By question type (A vs B, test split)

| Type | Cases | B − A | DAX-only wins | SQL-only wins | p |
|---|---|---|---|---|---|
| Sold vs created date basis | 81 | +4.9 pts | 0 | 4 | 0.13 |
| Appointments ↔ sales | 31 | +6.5 pts | 2 | 4 | 0.69 |
| Time intelligence | 86 | +2.3 pts | 0 | 2 | 0.50 |
| Appointments under franchise rules | 54 | +1.9 pts | 3 | 4 | 1.00 |
| Closing rate | 29 | 0 | 0 | 0 | 1.00 |
| Cohort / funnel | 56 | 0 | 2 | 2 | 1.00 |
| Ranking | 49 | −2.0 pts | 1 | 0 | 1.00 |
| Row-level lists | 39 | −2.6 pts | 3 | 2 | 1.00 |

No type shows a significant difference. Before the run, the research predicted SQL would win on appointments↔sales, lists and cohorts, and DAX on closing rate and the date basis. Only the appointments↔sales direction held, and it isn't significant. Both routes got every closing-rate case right: once the definitions are written down, the measure carries no advantage.

### Other splits

| Split | A: DAX | B: SQL |
|---|---|---|
| Dev (26 production questions, in-sample) | 100% | 100% |
| Robustness: paraphrased test questions | 93.1% (231) | 93.0% (129 of 232 run) |
| Multi-turn conversations | 97.1% (35) | not run |

## Why answers failed

Both routes mostly fail on **meaning, not syntax**. The commonest error for both is the **date window**:

- reading "so far this year" as the whole year, or "this year" for appointments as year to date (the rulebook makes "this year" the calendar year including booked appointments);
- reading "last week" as the current week;
- reading "last month" as a rolling month instead of the calendar month.

Errors specific to DAX:
- matching appointments to projects by name, which double-counts repeated project names;
- the per-row `CALCULATE` trap that silently shrinks per-project counts;
- a measure used outside its intended filter (an average cycle measure has no stage filter, so it includes non-sold projects);
- a roster definition ignored;
- a transient Power BI 503 error followed by a wrong fallback;
- an answer whose table showed only the last query's 7 rows out of 35.

Errors specific to SQL:
- a staff member looked up in the designer roster;
- "still has a dollar amount" counted with $0 amounts;
- "sold so far" restricted to sales inside the cohort window;
- lists of more than 50 rows given without their total.

## Things the POC found beyond the comparison

1. **A shared rulebook makes both routes near-perfect on known questions.** With the same rulebook, both routes scored 100% on the 26 production Dev questions and about 95% on fresh ones. The rulebook includes the 2026-11-07 date-basis decision. Shipping it in the production prompt is the biggest lever, whichever route ships. It raises cost from about $0.91 to about $1.24 per 1,000 questions, because the prompt grows by about 15,000 tokens.
2. **Date windows are the main remaining error class for every route.** Explicit examples for "so far", "last week", "last month" and "this year" for appointments would address it directly.
3. **The rulebook contradicts itself in one place.** "First appointment" for ICCH and ICAU means the flagged first appointment (rulebook line 163), but a natural reading is the project's earliest appointment, and the gold set used that. They give $1.66M vs $2.44M on the same question.
4. **Production's table shows only the last query's result.** When a model runs a second, narrower query, the owner sees the narrower table even if the prose says "full list" (seen on the DAX route).
5. **Power BI returned transient 500/503 errors** on a few queries during the run, and the bot's fallbacks were sometimes wrong.
6. **Model measures with misleading names or no filters:**
   - `[Staff Success Rate]` is always 0;
   - `[Sold/Lost/Proposed Project Count]` count appointment rows, not projects;
   - `[Av Cycle]` has no stage filter.

## How this was run

- **Arms.** A: DAX through the production loop with the fast path off. B: SQL over per-franchise views mirroring the Power BI tables. B+: B plus curated metric views. A and B use the same model, per-round settings, loop, guards, formatter, history window, shared rulebook (with bindings of equal depth) and verified examples. Prompts were frozen by hash before the test runs (PREREGISTRATION.md).
- **Isolation.** Route B and B+ read a sandbox copy of MySQL through per-franchise SELECT-only accounts. Row-level security is proven by `sandbox/rls_test.py`: 38/38 checks for B and 42/42 for B+, including a scan of 145k Salesforce ids. Nothing in production was changed.
- **Gold set.** 250 templates; every test case has paired SQL and DAX references that agree on all franchises. Each answer is scored against its reference re-run on the source that arm read. Route A's references were re-run after its answers: none of the checked references had moved.
- **Scoring.** A deterministic number check first, with rule-based fixes applied to every arm alike (see DEVIATIONS.md). Every case where A and B split, and every case both failed, was adjudicated by hand by the coordinator (Claude Opus 5.5) against the rulebook; for B+, the cases where it split from B and the cases adjudicated for A/B. Each override and its reason is in `adjudication/eval_r1.csv`, which is local and gitignored because it quotes franchise data.

## Limits

- **One run per arm, not three.** Sulaiman stopped testing after batch 1. The test split is complete for A and B; run-to-run variation is measured only for B+ (8% flips), so A and B probably carry similar noise.
- **Route B was not run** on the multi-turn, why/forecast, unanswerable and ambiguity splits, or on 103 of the 232 paraphrase cases.
- **The live safety battery was not run.** Franchise isolation is proven at the database level and in the SQL gate's unit tests (59 hostile statements), but not through end-to-end prompts. Under the decision rule, that gate only matters if a switch to SQL were on the table.
- **A0 (production as deployed, with recipes) was not run**, so the size of the gain from the rulebook alone versus production is not measured.
- **A single adjudicator.** The blind-judge reliability pass was not needed for the decision: every test-split case was decided by the number check or by hand. Sulaiman can audit the adjudication sheet.
- **Data timing.** SQL arms read a snapshot taken at 01:29 PST; the DAX arm read the live datasets between 03:37 and 04:21 PST.
