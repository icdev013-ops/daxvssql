# Deviations from the pre-registration

Every change to the arms, gold-set rules, scoring, statistics or decision rule made after 2026-11-07 is logged here with its date and reason.

| Date | Change | Reason |
|---|---|---|
| 2026-11-07 | `dim_entity` view filtered to the franchise's own row instead of mirroring the model's 9 rows; franchise accounts lose `SHOW VIEW` | Row-level security: every view an account can read holds only its franchise's rows, and Route B can't read view logic Route A can't see. Before any run. |
| 2026-11-07 | A and B code base pinned to SAAS commit `9ef5e4f` (the 2026-11-07 work, now committed) instead of the uncommitted working copy. A0 stays `d1b6a70`. The production date-basis fix (`c500cb9`, `4fb371d`) is NOT in any arm's vendored code; A and B get the same rule through `rules/business_rules.md` (rule 14). | The working copy was being edited by another session (Step 0); a moving baseline would make runs irreproducible. Optional extra reference arm: post-fix production `4fb371d`. |
| 2026-11-07 | Route B's "one day of SQL tuning on Dev" took a single Dev pass and changed nothing | Dev showed no B-specific failure to fix (25/25, 0 failed queries). The only shared miss was a scorer gap, now fixed; prompts frozen (hashes in PREREGISTRATION.md). |
| 2026-11-07 | Scorer: ranked-list order is read from the prose ranking when the table is unsorted; alternate readings listed in the gold set count as a pass | Found on Dev: a DAX TOPN returns rows unsorted although the answer ranks them correctly; DPQ55's lead-journey reading was already a listed alternate but the scorer ignored alternates. Both fixed before any Test run. |
| 2026-11-07 | Template definitions signed off by the coordinator (Claude), not a person; first-pass hand labels and discordant-pair adjudication also by the coordinator, with the adjudication sheet left for Sulaiman to audit | Sulaiman asked for the POC to run to completion without stopping. The judge (Google) and the labeller (Anthropic) are different vendors from each other and from the chatbot (OpenAI). |
