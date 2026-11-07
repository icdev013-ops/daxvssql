# daxvssql

A proof of concept that compares two ways for a franchise BI chatbot to answer owners' business questions:

- **Route A (DAX):** the LLM writes DAX against each franchise's Power BI semantic model (`executeQueries` REST API). This is how the production bot works today.
- **Route B (SQL):** the same LLM writes SQL against per-franchise MySQL views that mirror the Power BI tables.

Everything except the query language is held constant: same model, settings, loop, business rules, examples and formatter. The arms, gold-set rules, statistics and decision rule are fixed in [PREREGISTRATION.md](PREREGISTRATION.md) before any scored run.

Franchises in scope: ICLA, ICCH, ICAU.

## Layout

| Path | What |
|---|---|
| `sandbox/sandbox.py` | Second MySQL 8.0 instance on 127.0.0.1:3307: `init`, `start`, `stop`, `status`, `refresh` (read-only dump of production → restore), `apply-views`, `probe` (isolation check), `purge` |
| `sandbox/views/` | View templates: one schema `poc_<ent>` per franchise mirroring the Power BI tables, plus `SCHEMA.md` and `PARITY_NOTES.md` |
| `bot/` | Standalone TypeScript runner: the production chatbot loop with a pluggable `query_dax` / `query_sql` tool per arm |
| `harness/` | Gold set, runner, scorer, statistics (Python) |
| `PREREGISTRATION.md` | The frozen evaluation plan and decision rule |
| `DEVIATIONS.md` | Every change made after registration, with date and reason |

## What never goes into git

Secrets (`.env`), the sandbox datadir and dumps (`.sandbox/`, `dumps/`), run logs (`runs/`, `*.jsonl`), gold answers with real numbers, and vendored copies of the production chatbot code (`bot/.vendor/`, which can hold staff names). See `.gitignore`.

## Isolation

- **Franchises:** each franchise has its own schema of DEFINER views with a literal entity filter, and its own SELECT-only account (`poc_icla`, `poc_icch`, `poc_icau`) that can see only that schema.
- **Bot connections:** the bot picks the account from the franchise the caller supplies, never from model output.
- **Query gate:** every SQL statement passes a parser-based gate before it runs (single SELECT, allowlisted views, no hints, variables, OUTFILE or sleep functions). Each session also sets a time limit, read-only mode and `ANSI_QUOTES`.

## Quick start

```bash
python sandbox/sandbox.py start          # bring the sandbox up
python sandbox/sandbox.py refresh        # fresh snapshot of production (read-only dump)
python sandbox/sandbox.py apply-views    # (re)build per-franchise views + accounts
python sandbox/sandbox.py probe          # check each account sees only its own schema
python sandbox/sandbox.py stop           # stop when not in use
python sandbox/sandbox.py purge          # delete datadir, dumps and sandbox passwords
```

Bot usage is in `bot/README.md`.
