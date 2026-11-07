# daxvssql

A proof of concept that compares two ways for a franchise BI chatbot to answer owners' business questions:

- **Route A (DAX):** the LLM writes DAX against each franchise's Power BI semantic model (`executeQueries` REST API). This is how the production bot works today.
- **Route B (SQL):** the same LLM writes SQL against per-franchise MySQL views that mirror the Power BI tables.

Everything except the query language is held constant: same model, settings, loop, business rules, examples and formatter. The arms, gold-set rules, statistics and decision rule are fixed in [PREREGISTRATION.md](PREREGISTRATION.md) before any scored run.

Franchises in scope: ICLA, ICCH, ICAU.

## Layout

| Path | What |
|---|---|
| `sandbox/sandbox.py` | Second MySQL 8.0 instance on 127.0.0.1:3307: `init`, `start`, `stop`, `status`, `refresh` (read-only dump of production → restore), `apply-views`, `apply-plus` (arm B+ schemas and accounts only), `probe` (isolation check), `purge` |
| `sandbox/views/` | View templates: one schema `poc_<ent>` per franchise mirroring the Power BI tables, plus `SCHEMA.md` and `PARITY_NOTES.md` |
| `sandbox/views_plus/` | Arm B+: governed metric views (`metric_sales`, `metric_closing_daily`, `metric_appointments_reported`, `metric_designers`) in schemas `pocplus_<ent>`, next to pass-through copies of every `poc_<ent>` view; `METRIC_VIEWS.md` (also the B+ prompt section) and `PARITY.md` |
| `bot/` | Standalone TypeScript runner: the production chatbot loop with a pluggable `query_dax` / `query_sql` tool per arm |
| `harness/` | Gold set, runner, scorer, statistics (Python) |
| `PREREGISTRATION.md` | The frozen evaluation plan and decision rule |
| `DEVIATIONS.md` | Every change made after registration, with date and reason |

## What never goes into git

Secrets (`.env`), the sandbox datadir and dumps (`.sandbox/`, `dumps/`), run logs (`runs/`, `*.jsonl`), gold answers with real numbers, and vendored copies of the production chatbot code (`bot/.vendor/`, which can hold staff names). See `.gitignore`.

## Row-level security (Route B)

Route A gets franchise isolation for free: each franchise has its own Power BI semantic model. MySQL has no native row-level security, and Route B's data sits in one shared database, so isolation is built in layers. Each layer is enforced by a machine test, not by the prompt.

| Layer | What it guarantees | Proven by |
|---|---|---|
| Views | Each franchise has its own schema (`poc_icla`, `poc_icch`, `poc_icau`) of `SQL SECURITY DEFINER` views. Every view that reads a tenant table carries a literal `entity = '<ENT>'` filter, and no other franchise's code. A view that reads no tenant table must be on a reviewed tenant-free list (date tables, lead-source taxonomy). | `rls_test.py` A |
| Accounts | One account per franchise, holding exactly `USAGE` + `SELECT` on its own schema: no `SHOW VIEW`, no global privileges, no other account reaching a `poc_*` schema. | `rls_test.py` B |
| Data | Through each account, every `entity` column holds only that franchise. None of the 145k Salesforce record ids belonging to any other franchise (including DEMO, the offboarded ICMA and UNMAPPED rows) appears in any text column of any view. | `rls_test.py` C |
| Privileges | Through each account these all fail or return nothing:<br>• reads: base schemas, other franchise schemas, `mysql.*`, statement history, the catalog of other schemas, view definitions, other sessions, `LOAD_FILE`<br>• writes: DDL, DML, temporary tables, `OUTFILE`, `SET GLOBAL`<br>A user variable can't widen a view. | `rls_test.py` D |
| Bot | The SQL tool picks the account from the franchise the signed-in caller supplies, never from model output. Each franchise has its own connection pool. A parser-based gate allows a single SELECT/WITH over that franchise's views only, with no hints, variables, `OUTFILE` or sleep functions. Each session sets a time limit, read-only mode and `ANSI_QUOTES`. | `bot/test/gate.test.ts`, safety battery |

Arm B+ adds schemas `pocplus_icla`, `pocplus_icch`, `pocplus_icau` and one SELECT-only account each (same limits, no `SHOW VIEW`). Their views hold no base-table reads: each is either a pass-through of the same-named `poc_<ent>` view or a `metric_*` view over those pass-throughs, so every row still comes through the `entity` filter proven above. The B+ accounts cannot read any `poc_*` schema directly. `rls_test.py` runs all four check groups on both families (`--only poc` / `--only plus` for one).

Run `python sandbox/rls_test.py` after every `refresh`, `apply-views` or `apply-plus`, and re-run `apply-plus` after `apply-views`. Any failure blocks scored runs.

## Quick start

```bash
python sandbox/sandbox.py start          # bring the sandbox up
python sandbox/sandbox.py refresh        # fresh snapshot of production (read-only dump)
python sandbox/sandbox.py apply-views    # (re)build per-franchise views + accounts
python sandbox/sandbox.py apply-plus     # (re)build arm B+ schemas pocplus_<ent> + accounts (after apply-views)
python sandbox/sandbox.py probe          # check each account sees only its own schema
python sandbox/sandbox.py stop           # stop when not in use
python sandbox/sandbox.py purge          # delete datadir, dumps and sandbox passwords
```

## Try it: local test page

```bash
python sandbox/sandbox.py start        # if the sandbox isn't running
cd bot && npm install && npm run vendor # first time only
npm run playground                      # -> http://127.0.0.1:4317
```

Pick a franchise and a mode (side by side DAX vs SQL, one route, or production as-is), set the "as of" date, and ask. Each answer shows its time, cost, rounds and the exact DAX or SQL that ran; follow-up questions keep each route's own chat history. The page binds to 127.0.0.1 and refuses other machines. Turns are logged to `runs/playground-<date>.jsonl` (gitignored).

CLI usage is in `bot/README.md`.
