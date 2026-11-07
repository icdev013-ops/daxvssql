# Parity notes: mirrored views vs the Power BI models

The views in this folder reproduce what the ICLA, ICCH and ICAU models import: the same tables, columns, column order, Power Query steps and calculated columns and tables, and the same rows for the same Gold data. This file lists every place where the SQL side cannot match the model exactly, and why. `SCHEMA.md` is the schema reference; this file is for whoever interprets differences between the two routes.

## Verified after applying (2026-11-07)

- Sandbox data: dump started 2026-11-07T01:29:45-08:00, restore finished 2026-11-07T01:30:32-08:00.
- Every view's row count equals the count of its source with the partition's filters, read through the franchise's own SELECT-only account.
- Every view's column names and order equal the franchise's TMDL column list, calculated columns included (`Salesforce_Data` has 28 columns, 29 in ICAU).
- No view returns a row of another entity (checked on every view with an `entity` column; `Lead Milestones` filters on the entity in all three of its sub-queries and every one of its `Lead ID`s exists in `Leads`).
- Each franchise account can read only its own schema: `information_schema` shows it only its own schema; `SHOW TABLES` on `icall_gold` and on the other franchise schemas, `SELECT` on `icall_gold`, `icall_silver`, `ic_weekly_report` and on the other franchise schemas, and `CREATE` are all denied (`sandbox.py probe` plus direct SELECT attempts through each account).
- The four sold-date calculated columns agree with their DAX definitions on every row (0 mismatches), and the views also work with ANSI_QUOTES added to the session `sql_mode`.
- 2026 year-to-date checks match the values in the research notes taken from live Gold: sold by sold date ICLA 501 / $5,211,995.05, ICCH 865 / $7,516,889.57, ICAU 429 / $6,035,311.27; sold by created date 469 / 814 / 398; created 963 / 1,509 / 917; pre-2020 opportunities 1,535 / 1,175 / 1,025.

## Row counts per franchise

| View | ICLA | ICCH | ICAU |
|---|---|---|---|
| `Salesforce_Data` | 10,726 | 12,013 | 9,109 |
| `Leads` | 15,273 | 15,948 | 13,540 |
| `Appointments` | 16,907 | 20,868 | 31,224 |
| `Leads_Journey` | 15,266 | 15,942 | 13,537 |
| `Opportunities to Appointments` | 1,438 | 4,609 | 2,390 |
| `Lead Milestones` | 15,273 | 15,948 | 13,540 |
| `dim_projects` | 10,761 | 12,013 | 9,115 |
| `dim_designers` | 14 | 31 | 30 |
| `DateTable` | 4,018 | 4,018 | 4,018 |
| `SoldDateTable` | 4,018 | 4,018 | 4,018 |
| `Lead Source Channels` | 64 | 64 | 64 |
| `Source_Sort` | 64 | 64 | 64 |
| `LS Group Sort` | 3 | 3 | 3 |
| `dim_entity` | 1 | 1 | 1 |
| `report_scorecard` | 8,478 | 7,776 | 8,478 |
| `report_designer_leaderboard` | 594 | 980 | 914 |
| `report_lead_source_roi` | 303 | 375 | 364 |
| `report_designer_concentration` | 157 | 144 | 157 |
| `report_channel_roi` | 2,254 | 2,422 | 2,551 |
| `report_appointment_funnel` | 465 | 426 | 484 |
| `Projects Part List` | 11,557 | n/a | n/a |

`DateTable`, `SoldDateTable`, `Lead Source Channels`, `Source_Sort` and `LS Group Sort` are tenant-agnostic and identical in every schema, as in the models. `dim_entity` is filtered to the franchise (see gap 18).

## Where the views cannot match the model exactly

### Left out on purpose
1. **Measures.** None are mirrored; that difference is the point of the test. Not mirrored: 78 `_measures` (77 in ICAU), 44 `_measures_L&A`, 3 table measures on `Salesforce_Data` (`Appointments X Amount`, `Amount $`, `Tooltip Control`) and, in ICLA, 7 measures on `Projects Part List`. Totals: ICLA 132, ICCH 125, ICAU 124.
2. **The measure-holder tables `_measures` and `_measures_L&A`.** Both are entered-data tables with one text column (`Column1`) and zero rows (payload `[]`); they exist only to hold measures, so they have no view.
3. **Auto date/time tables.** None of the three TMDL definitions contains a `LocalDateTable_*` or `DateTableTemplate_*` table (auto date/time is off), so there is nothing to skip; any such artefact left in a local Desktop session is not part of the published definition.
4. **Report-page rules.** Filters that live only in the `.Report` page JSON (the ICLA appointment rule set with its named-staff exclusion list, the ICCH 'available' subject filter, the hidden-page designer exclusions, the `Revised Stage` = 'Sold' page filter, slicer defaults) are not part of the model and are not mirrored. The ICLA staff list contains person names and is not reproduced in any file here. Both routes have to get these rules from the shared rules document.

### Model behaviour that SQL has no equivalent for
5. **Relationships.** MySQL views carry no relationships. `SCHEMA.md` lists all 23 (ICLA 25) as join keys with cardinality, active/inactive state and filter direction, but the SQL writer must choose the join, its type and the date role itself. Specifically: (a) unmatched keys go to a blank member in Power BI and need a LEFT JOIN in SQL; (b) bidirectional filtering (`Opportunities to Appointments` to `dim_projects`, `Lead Milestones` to `Leads`, `Source_Sort` to `Lead Source Channels`) has no SQL analogue; (c) inactive relationships and `USERELATIONSHIP` become an explicit choice of column; (d) the active-only `dim_designers` and the 2020-2030 `DateTable` bound only apply in SQL when the query actually joins to them.
6. **Sort-by-column metadata** (month and weekday names, `Sold Month`, designers, lead sources, groups, `Contact Path`, `Journey Outcome`) cannot be attached to a view column; `SCHEMA.md` names the sort column so SQL can `ORDER BY` it. In the model `SoldDateTable`.`MonthYear` has no sort-by although `DateTable`.`MonthYear` has; this only affects display order.
7. **Hidden columns** stay visible in SQL (`Sold Month Number`, the `dim_designers` helper columns, the `report_*` `id` / `entity` / `created_at` columns, three `Lead Milestones` helper columns, `Projects Part List`.`id` / `entity`, `Opportunities to Appointments`.`entity`). DAX can reference hidden columns too, so no data is added; `SCHEMA.md` marks them.
8. **Descriptions.** The model's column descriptions are not stored on the views. `SCHEMA.md` repeats them without the Q&A synonym lists, which are often misleading for SQL (for example "Project ID" listed as a synonym of `Appointments`.`Project Name`).

### Type and value representation
9. **Date casts.** The model casts six datetime columns to `type date` but stores them as dateTime at midnight; the views return DATE. Values and comparisons are the same; only the display differs (`2026-01-05` vs `2026-01-05 00:00:00`). All other datetime columns keep their Gold DATETIME type, as in the model.
10. **Decimal vs double.** Power BI stores `Amount`, `Total Amount`, the `report_*` money and rate columns and the `Lead Milestones` day and amount columns as double; the views return MySQL DECIMAL. SQL sums are exact and DAX sums are floating point; at these magnitudes the difference is far below one cent and disappears when results are rounded to cents. `Lead Milestones` `Days to Scheduled` / `Days to Studio` come back as DECIMAL(23,2) rather than a double with the same value.
11. **Booleans.** `report_appointment_funnel`.`hygiene_flag` and `report_designer_concentration`.`warn_flag` are TRUE/FALSE in the model (MySQL `tinyint(1)` read as logical) and 0/1 in SQL.
12. **Text.** Both sides compare text case-insensitively (Gold uses `utf8mb4_unicode_ci`). Power BI keeps one spelling per case-insensitive value in a column (the first loaded), while SQL returns each row's stored spelling; MySQL also ignores trailing spaces in comparisons. On the current data this changes no counts: project names, designer names and lead-source names are unique under the same collation by primary key.
13. **Distinct steps.** Power Query's `Table.Distinct` (case-sensitive, keeps the first row) becomes `SELECT DISTINCT` for `dim_projects`, `Lead Source Channels` and `Source_Sort`, and `GROUP BY ... MIN()` for `LS Group Sort`. They return the same rows today because `dim_project` is unique on (entity, `Project Name`), `dim_lead_source` is unique on the lead-source name, and each lead-source group has one `Group Sort`. If Gold ever breaks those properties, Power BI would keep the first row (or fail to refresh a one-side duplicate) while SQL would keep both or the minimum.
14. **`Sold Month`** uses `ELT(MONTH(...), 'Jan', ...)` instead of `DATE_FORMAT('%b')`, so it does not depend on the session's `lc_time_names`; it equals DAX `FORMAT(..., "mmm")` under the model's en-US culture. `Sold Start of Week` stays DATETIME, like the DAX column.
15. **Blank and NULL.** DAX treats blank as equal to '' and 0 in comparisons; SQL NULL does not equal ''. The data is identical but the same-looking filter behaves differently (for example on `Appointments`.`Project Name`, NULL for 4,248 ICLA, 5,052 ICCH and 9,510 ICAU rows).

### Franchise differences and duplication
16. **ICAU `Additional Lead Source`.** Only the ICAU model keeps this column (Gold `Other Lead Source`, filled on 1,844 of 9,109 ICAU rows). The simplest faithful approach is an override: `20_salesforce_data.only_ICAU.sql` re-creates `Salesforce_Data` for ICAU with the shared definition plus that column in the model's position. The cost is a second copy of the `Salesforce_Data` definition, which has to be kept in step with `10_mirror.sql` (both were generated from the TMDL in one pass and diffed: the only difference is that line). ICLA and ICCH Gold rows also carry `Other Lead Source` (591 and 1,087 filled) but their models drop it, so their views do too.
17. **ICLA `Projects Part List`** is in `30_projects_part_list.only_ICLA.sql` with its two ICLA-only relationships.
18. **`dim_entity` is filtered to the franchise's own row (deliberate deviation, 2026-11-07).** The model imports all nine entity codes unfiltered. For row-level security every view a franchise account can read holds only that franchise's rows, so the view keeps one row. Only a question like "which franchises exist?" is affected. Enforced by `sandbox/rls_test.py`.

### Query-time computation
19. **Derived tables are computed per query.** Power BI materialises `Lead Milestones`, the distinct lookups and `SoldDateTable` at refresh; the views compute them each time they are read. On this data a full `Lead Milestones` scan takes about 0.1 s and every other view about 0.02 s or less, far inside the sandbox's 15 s `max_execution_time`, so this affects latency only, not results. Filters on the date-cast columns cannot use Gold's (entity, datetime) index ranges; also a latency-only effect at these volumes.

### Data timing
20. **Different snapshots.** The sandbox is a frozen dump (see the timestamps above); each Power BI dataset reflects its last scheduled refresh from production. Counts match only when both sides hold the same Gold state, which is why the POC scores each route against a reference query on its own source. Gold columns computed at build time (`Is Future Appointment`, `Lead Age in Days`, `Lead Age Category`, `active_flag`) are equally frozen on both sides.
21. **Comment drift in the model.** The `Salesforce_Data` partition comment says `Final Completion Date` is dropped, but its column list keeps it; the views follow the code, not the comment.

### Access asymmetry (not a data gap)
22. `sandbox.py` grants each franchise account `SHOW VIEW`, so Route B can read the view definitions (`SHOW CREATE VIEW`), including the source schema names, the entity literal and the `Lead Milestones` query. Route A is not shown the Power Query behind its tables (whether `INFO.PARTITIONS()` would work through executeQueries with the portal identity was not tested). Isolation still holds (the account has no privileges on the source schemas), but if the comparison should hide implementation logic equally, drop `SHOW VIEW` from the grant.
