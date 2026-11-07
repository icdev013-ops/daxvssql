# Deviations from the pre-registration

Every change to the arms, gold-set rules, scoring, statistics or decision rule made after 2026-11-07 is logged here with its date and reason.

| Date | Change | Reason |
|---|---|---|
| 2026-11-07 | `dim_entity` view filtered to the franchise's own row instead of mirroring the model's 9 rows; franchise accounts lose `SHOW VIEW` | Row-level security: every view an account can read holds only its franchise's rows, and Route B can't read view logic Route A can't see. Before any run. |
