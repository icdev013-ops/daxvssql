"""Row-level-security test for the Route B and Route B+ sandbox.

MySQL has no native RLS, so franchise isolation rests on three things, and this
test proves each one for every franchise account of Route B (poc_icla, poc_icch,
poc_icau) and of Route B+ (pocplus_icla, pocplus_icch, pocplus_icau):

  A. Views (static): every view that reads a tenant table (one with an `entity`
     column) carries its own franchise literal and no other franchise's literal;
     every view that reads no tenant table is on the reviewed tenant-free list;
     all views are SQL SECURITY DEFINER owned by root@localhost.
  B. Grants: each account holds exactly USAGE + SELECT on its own schema, and no
     other account (except root) can reach the poc_* schemas.
  C. Data: through each account, every `entity` column holds only that franchise,
     and no Salesforce record id belonging to another franchise appears in ANY
     text column of ANY view the account can read.
  D. Privileges: through each account, every route to other data or to writes
     fails: base schemas, other franchise schemas, mysql.*, performance_schema
     history, catalog of other schemas, view definitions, DDL, DML, temp tables,
     OUTFILE, LOAD_FILE, SET GLOBAL; user variables can't widen a view.

Route B+ (pocplus_<ent>, built by `sandbox.py apply-plus`) holds no base-table reads at all: every view
must resolve only to views of poc_<ent>, the same franchise's Route B mirror proven by A. Pass-through
views must read exactly the poc_<ent> view of the same name with the same columns, every poc_<ent> view
must have one, and every other view must be a metric_* view that reads tenant data (none is tenant-free).
The B+ accounts get the same B/C/D checks plus: same resource limits as poc_<ent>, no table-level grants,
and no read of the Route B schemas (their own franchise's included).

Nothing is printed except counts and object names (no row data, no secrets).
usage: python sandbox/rls_test.py [--only poc|plus]     exit code 1 on any failure (default: both)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import mysql.connector

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
FRANCHISES = ["ICLA", "ICCH", "ICAU"]
ALL_CODES = ["ICAU", "ICCH", "ICDF", "ICLA", "ICMA", "ICMN", "ICSE", "ICTA", "DEMO"]
TENANT_FREE = {"datetable", "solddatetable", "lead source channels", "source_sort", "ls group sort"}
SFID = re.compile(r"^[A-Za-z0-9]{15}(?:[A-Za-z0-9]{3})?$")

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))


def env(key: str) -> str:
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip()
    raise KeyError(key)


def connect(user: str, pwd: str):
    return mysql.connector.connect(host="127.0.0.1", port=3307, user=user, password=pwd,
                                   charset="utf8mb4", use_pure=True, autocommit=True)


def q(cn, sql: str, args=None):
    cur = cn.cursor()
    cur.execute(sql, args or ())
    rows = cur.fetchall() if cur.with_rows else []
    cur.close()
    return rows


# ----------------------------------------------------------------------------- A. views
def load_tenant_tables(root) -> set[tuple[str, str]]:
    tenant_tables = {(s.lower(), t.lower()) for s, t in q(root,
        "SELECT TABLE_SCHEMA, TABLE_NAME FROM information_schema.COLUMNS WHERE COLUMN_NAME='entity' "
        "AND TABLE_SCHEMA IN ('icall_gold','icall_silver','ic_weekly_report')")}
    # every entity code that exists anywhere in the data, not just a hard-coded list
    for s, t in sorted(tenant_tables):
        for (code,) in q(root, f"SELECT DISTINCT `entity` FROM `{s}`.`{t}` WHERE `entity` IS NOT NULL"):
            if code and code not in ALL_CODES:
                ALL_CODES.append(code)
    print(f"     entity codes in data: {', '.join(sorted(ALL_CODES))}")
    return tenant_tables


def static_views(root, tenant_tables: set[tuple[str, str]]) -> None:
    for ent in FRANCHISES:
        schema = f"poc_{ent.lower()}"
        views = q(root, "SELECT TABLE_NAME, VIEW_DEFINITION, DEFINER, SECURITY_TYPE FROM information_schema.VIEWS "
                        "WHERE TABLE_SCHEMA=%s", (schema,))
        usage = {}
        for v, s, t in q(root, "SELECT VIEW_NAME, TABLE_SCHEMA, TABLE_NAME FROM information_schema.VIEW_TABLE_USAGE "
                               "WHERE VIEW_SCHEMA=%s", (schema,)):
            usage.setdefault(v.lower(), set()).add((s.lower(), t.lower()))
        tables = q(root, "SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s "
                         "AND TABLE_TYPE<>'VIEW'", (schema,))[0][0]
        check(f"A {ent}: schema holds views only", tables == 0, f"{tables} base tables")
        bad = []
        for name, defn, definer, sec in views:
            n = name.lower()
            # resolve views-over-views inside the same schema
            base, todo, seen = set(), list(usage.get(n, ())), set()
            while todo:
                s, t = todo.pop()
                if (s, t) in seen:
                    continue
                seen.add((s, t))
                if s == schema:
                    todo.extend(usage.get(t, ()))
                else:
                    base.add((s, t))
            reads_tenant = bool(base & tenant_tables)
            foreign = [c for c in ALL_CODES if c != ent and f"'{c}'" in defn]
            if definer.lower() != "root@localhost" or sec != "DEFINER":
                bad.append(f"{name}: definer {definer}/{sec}")
            if foreign:
                bad.append(f"{name}: other franchise literal {foreign}")
            if reads_tenant and f"'{ent}'" not in defn:
                bad.append(f"{name}: reads tenant table without '{ent}' filter")
            if not reads_tenant and n not in TENANT_FREE and not (usage.get(n, set()) & {(schema, x) for x in TENANT_FREE}):
                bad.append(f"{name}: tenant-free view not on the reviewed list")
        check(f"A {ent}: {len(views)} views filtered to own franchise, DEFINER root", not bad, "; ".join(bad))


def static_views_plus(root, tenant_tables: set[tuple[str, str]]) -> None:
    """Route B+: every pocplus_<ent> view resolves only to views of poc_<ent> (proven by A above)."""
    for ent in FRANCHISES:
        base, schema = f"poc_{ent.lower()}", f"pocplus_{ent.lower()}"
        views = q(root, "SELECT TABLE_NAME, VIEW_DEFINITION, DEFINER, SECURITY_TYPE FROM information_schema.VIEWS "
                        "WHERE TABLE_SCHEMA=%s", (schema,))
        if not views:
            check(f"A {ent} B+: schema {schema} exists with views", False, "run `python sandbox/sandbox.py apply-plus`")
            continue
        base_views = {r[0].lower() for r in q(root, "SELECT TABLE_NAME FROM information_schema.VIEWS WHERE TABLE_SCHEMA=%s",
                                               (base,))}
        # poc_<ent> views that read tenant data (directly or through other poc_<ent> views)
        base_usage: dict[str, set[tuple[str, str]]] = {}
        for v, s, t in q(root, "SELECT VIEW_NAME, TABLE_SCHEMA, TABLE_NAME FROM information_schema.VIEW_TABLE_USAGE "
                               "WHERE VIEW_SCHEMA=%s", (base,)):
            base_usage.setdefault(v.lower(), set()).add((s.lower(), t.lower()))

        def resolve(usage: dict[str, set[tuple[str, str]]], own: str, start: set[tuple[str, str]]) -> set[tuple[str, str]]:
            out, todo, seen = set(), list(start), set()
            while todo:
                s, t = todo.pop()
                if (s, t) in seen:
                    continue
                seen.add((s, t))
                if s == own:
                    todo.extend(usage.get(t, ()))
                else:
                    out.add((s, t))
            return out
        base_tenant = {v for v in base_views if resolve(base_usage, base, base_usage.get(v, set())) & tenant_tables}
        usage: dict[str, set[tuple[str, str]]] = {}
        for v, s, t in q(root, "SELECT VIEW_NAME, TABLE_SCHEMA, TABLE_NAME FROM information_schema.VIEW_TABLE_USAGE "
                               "WHERE VIEW_SCHEMA=%s", (schema,)):
            usage.setdefault(v.lower(), set()).add((s.lower(), t.lower()))
        cols = {}
        for sch, t, c in q(root, "SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS "
                                 "WHERE TABLE_SCHEMA IN (%s, %s) ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION",
                                 (base, schema)):
            cols.setdefault((sch.lower(), t.lower()), []).append(c)
        tables = q(root, "SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s "
                         "AND TABLE_TYPE<>'VIEW'", (schema,))[0][0]
        check(f"A {ent} B+: schema holds views only", tables == 0, f"{tables} base tables")
        bad, names, n_metric = [], set(), 0
        for name, defn, definer, sec in views:
            n = name.lower()
            names.add(n)
            ext = resolve(usage, schema, usage.get(n, set()))
            foreign = [c for c in ALL_CODES if c != ent and f"'{c}'" in defn]
            if definer.lower() != "root@localhost" or sec != "DEFINER":
                bad.append(f"{name}: definer {definer}/{sec}")
            if foreign:
                bad.append(f"{name}: other franchise literal {foreign}")
            outside = sorted(f"{s}.{t}" for s, t in ext if s != base or t not in base_views)
            if outside or not ext:
                bad.append(f"{name}: reads outside the {ent} mirror {base}: {outside or 'nothing'}")
            if n in base_views:      # pass-through
                if ext != {(base, n)} or any(s == schema for s, _ in usage.get(n, set())):
                    bad.append(f"{name}: pass-through must read exactly {base}.{n}")
                if cols.get((schema, n)) != cols.get((base, n)):
                    bad.append(f"{name}: columns differ from {base}.{n}")
            else:                    # governed metric view
                n_metric += 1
                if not n.startswith("metric_"):
                    bad.append(f"{name}: not a pass-through and not a metric_ view")
                if not ({t for s, t in ext if s == base} & base_tenant) and n not in TENANT_FREE:
                    bad.append(f"{name}: reads no tenant view (tenant-free view not on the reviewed list)")
        missing = sorted(base_views - names)
        if missing:
            bad.append(f"no pass-through for {base}: {missing}")
        check(f"A {ent} B+: {len(views)} views ({n_metric} metric) resolve only to {base}, DEFINER root", not bad,
              "; ".join(bad))


# ----------------------------------------------------------------------------- B. grants
def grants(root, prefix: str = "poc") -> None:
    tag = "" if prefix == "poc" else " B+"
    for ent in FRANCHISES:
        u = f"{prefix}_{ent.lower()}"
        try:
            g = sorted(r[0] for r in q(root, f"SHOW GRANTS FOR '{u}'@'localhost'"))
        except mysql.connector.Error as e:
            check(f"B {ent}{tag}: account {u} exists", False, str(e).splitlines()[0][:80])
            continue
        want = sorted([f"GRANT USAGE ON *.* TO `{u}`@`localhost`", f"GRANT SELECT ON `{u}`.* TO `{u}`@`localhost`"])
        check(f"B {ent}{tag}: grants are exactly USAGE + SELECT on own schema", g == want, "; ".join(g))
        if prefix != "poc":
            lim = lambda user: q(root, "SELECT max_questions, max_updates, max_connections, max_user_connections "
                                       "FROM mysql.user WHERE user=%s AND host='localhost'", (user,))
            mine, ref = lim(u), lim(f"poc_{ent.lower()}")
            check(f"B {ent}{tag}: resource limits equal poc_{ent.lower()}'s", bool(mine) and mine == ref,
                  f"{mine} vs {ref}")
    like = prefix.replace("_", "\\_")
    others = q(root, "SELECT DISTINCT GRANTEE, TABLE_SCHEMA FROM information_schema.SCHEMA_PRIVILEGES "
                     f"WHERE TABLE_SCHEMA LIKE '{like}\\_%'")
    stray = [f"{g}->{s}" for g, s in others if g.strip("'").split("'@'")[0] != s]
    check(f"B no account other than the owner (and root) can reach a {prefix} schema", not stray, "; ".join(stray))
    glob = q(root, f"SELECT GRANTEE, PRIVILEGE_TYPE FROM information_schema.USER_PRIVILEGES WHERE GRANTEE LIKE '''{like}\\_%'")
    glob = [f"{g}:{p}" for g, p in glob if p != "USAGE"]
    check(f"B{tag} franchise accounts hold no global privileges", not glob, "; ".join(glob))
    if prefix != "poc":
        tbl = q(root, "SELECT GRANTEE, TABLE_SCHEMA, TABLE_NAME FROM information_schema.TABLE_PRIVILEGES "
                      f"WHERE TABLE_SCHEMA LIKE '{like}\\_%' OR GRANTEE LIKE '''{like}\\_%'")
        check(f"B{tag} no table-level grants on or for {prefix} schemas", not tbl,
              "; ".join(f"{g}->{s}.{t}" for g, s, t in tbl))


# ----------------------------------------------------------------------------- C. data
def foreign_ids(root) -> dict[str, set[str]]:
    """Salesforce ids per entity from every id-like column of every tenant table."""
    cols = q(root, "SELECT c.TABLE_SCHEMA, c.TABLE_NAME, c.COLUMN_NAME FROM information_schema.COLUMNS c "
                   "JOIN information_schema.TABLES t ON t.TABLE_SCHEMA=c.TABLE_SCHEMA AND t.TABLE_NAME=c.TABLE_NAME "
                   "WHERE c.TABLE_SCHEMA IN ('icall_gold','icall_silver') AND t.TABLE_TYPE='BASE TABLE' "
                   "AND c.DATA_TYPE IN ('char','varchar') AND c.CHARACTER_MAXIMUM_LENGTH BETWEEN 15 AND 64 "
                   "AND (c.COLUMN_NAME REGEXP '(^|[ _])(id|ID|Id)$' OR c.COLUMN_NAME REGEXP 'ID') "
                   "AND EXISTS (SELECT 1 FROM information_schema.COLUMNS e WHERE e.TABLE_SCHEMA=c.TABLE_SCHEMA "
                   "AND e.TABLE_NAME=c.TABLE_NAME AND e.COLUMN_NAME='entity')")
    ids: dict[str, set[str]] = {}
    for s, t, c in cols:
        for e, v in q(root, f"SELECT DISTINCT entity, `{c}` FROM `{s}`.`{t}` WHERE `{c}` REGEXP "
                            "'^[A-Za-z0-9]{15}([A-Za-z0-9]{3})?$'"):
            ids.setdefault(e, set()).add(v)
    # an id seen under two entities is not a fingerprint (shouldn't happen with SF ids)
    owners: dict[str, set[str]] = {}
    for e, vs in ids.items():
        for v in vs:
            owners.setdefault(v, set()).add(e)
    return {e: {v for v in vs if len(owners[v]) == 1} for e, vs in ids.items()}


def data_scan(accounts, ids: dict[str, set[str]], prefix: str = "poc") -> None:
    for ent in FRANCHISES:
        cn = accounts[ent]
        schema = f"{prefix}_{ent.lower()}"
        lbl = ent if prefix == "poc" else f"{ent} B+"
        foreign = set().union(*(v for e, v in ids.items() if e != ent))
        own = ids.get(ent, set())
        cols = q(cn, "SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s",
                 (schema,))
        ent_bad, leaks, own_seen, scanned = [], [], 0, 0
        for t, c, dt in cols:
            if c.lower() == "entity":
                vals = {r[0] for r in q(cn, f"SELECT DISTINCT `entity` FROM `{schema}`.`{t}`")}
                if vals - {ent}:
                    ent_bad.append(f"{t}: {sorted(vals - {ent})}")
            if dt in ("char", "varchar", "text", "mediumtext", "longtext"):
                scanned += 1
                vals = {r[0] for r in q(cn, f"SELECT DISTINCT `{c}` FROM `{schema}`.`{t}` WHERE `{c}` REGEXP "
                                            "'^[A-Za-z0-9]{15}([A-Za-z0-9]{3})?$'")}
                hit = vals & foreign
                own_seen += len(vals & own)
                if hit:
                    leaks.append(f"{t}.{c}: {len(hit)} foreign ids")
        check(f"C {lbl}: every entity column holds only {ent}", not ent_bad, "; ".join(ent_bad))
        check(f"C {lbl}: no other franchise's Salesforce id in {scanned} text columns",
              not leaks and own_seen > 0, "; ".join(leaks) or f"{own_seen} own ids seen")


# ----------------------------------------------------------------------------- D. privilege probes
def probes(accounts, prefix: str = "poc") -> None:
    for ent in FRANCHISES:
        cn = accounts[ent]
        schema = f"{prefix}_{ent.lower()}"
        other = f"{prefix}_{[e for e in FRANCHISES if e != ent][0].lower()}"
        lbl = ent if prefix == "poc" else f"{ent} B+"
        must_fail = {
            "read base Gold table": "SELECT COUNT(*) FROM icall_gold.fact_sales",
            "read Silver": "SELECT COUNT(*) FROM icall_silver.silver_lead_status_history",
            "read weekly report": "SELECT COUNT(*) FROM ic_weekly_report.report_scorecard",
            "read other franchise schema": f"SELECT COUNT(*) FROM `{other}`.`salesforce_data`",
            "read mysql.user": "SELECT COUNT(*) FROM mysql.user",
            "read statement history": "SELECT COUNT(*) FROM performance_schema.events_statements_history_long",
            "create table": f"CREATE TABLE `{schema}`.`rls_x` (a INT)",
            "create temporary table": "CREATE TEMPORARY TABLE rls_tmp (a INT)",
            "update a view": f"UPDATE `{schema}`.`dim_entity` SET `Region`='x'",
            "insert into a view": f"INSERT INTO `{schema}`.`dim_entity` (`entity`) VALUES ('ZZZZ')",
            "delete from a view": f"DELETE FROM `{schema}`.`leads` LIMIT 1",
            "drop a view": f"DROP VIEW `{schema}`.`leads`",
            "set global": "SET GLOBAL max_execution_time=0",
            "write a file": f"SELECT 1 FROM `{schema}`.`dim_entity` INTO OUTFILE 'rls_probe.txt'",
            "create a function": f"CREATE FUNCTION `{schema}`.f() RETURNS INT DETERMINISTIC RETURN 1",
        }
        if prefix != "poc":
            must_fail.update({
                "read own franchise Route B schema": f"SELECT COUNT(*) FROM `poc_{ent.lower()}`.`salesforce_data`",
                "read other franchise Route B schema": f"SELECT COUNT(*) FROM `poc_{other[len(prefix) + 1:]}`.`leads`",
                "update a metric view": f"UPDATE `{schema}`.`metric_sales` SET `amount` = 0",
                "delete from a metric view": f"DELETE FROM `{schema}`.`metric_appointments_reported` LIMIT 1",
            })
        failed_to_block = []
        for label, sql in must_fail.items():
            try:
                q(cn, sql)
                failed_to_block.append(label)
            except mysql.connector.Error:
                pass
        check(f"D {lbl}: {len(must_fail)} forbidden operations all denied", not failed_to_block,
              "NOT denied: " + ", ".join(failed_to_block))
        schemas = {r[0].lower() for r in q(cn, "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA")}
        check(f"D {lbl}: catalog shows no other data schema",
              schemas <= {"information_schema", "performance_schema", schema}, ", ".join(sorted(schemas)))
        n = q(cn, "SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA NOT IN "
                  "('information_schema','performance_schema', %s)", (schema,))[0][0]
        check(f"D {lbl}: catalog lists no tables outside own schema", n == 0, f"{n} visible")
        defs = [r[0] for r in q(cn, "SELECT VIEW_DEFINITION FROM information_schema.VIEWS WHERE TABLE_SCHEMA=%s",
                                (schema,)) if r[0]]
        check(f"D {lbl}: view definitions are hidden (no SHOW VIEW)", not defs, f"{len(defs)} readable")
        lf = q(cn, "SELECT LOAD_FILE('C:/Windows/win.ini')")[0][0]
        check(f"D {lbl}: LOAD_FILE returns nothing", lf is None)
        procs = q(cn, "SELECT COUNT(*) FROM information_schema.PROCESSLIST WHERE USER <> %s",
                  (f"{prefix}_{ent.lower()}",))[0][0]
        check(f"D {lbl}: process list shows no other sessions", procs == 0, f"{procs} visible")
        q(cn, "SET @e := 'ICSE'")
        widened = q(cn, f"SELECT COUNT(*) FROM `{schema}`.`salesforce_data` "
                        "WHERE CAST(`entity` AS BINARY) = CAST(@e AS BINARY)")[0][0]
        check(f"D {lbl}: a user variable can't widen a view", widened == 0, f"{widened} rows")


def main() -> int:
    only = None
    if len(sys.argv) > 1:
        if len(sys.argv) != 3 or sys.argv[1] != "--only" or sys.argv[2] not in ("poc", "plus"):
            sys.exit(__doc__)
        only = sys.argv[2]
    sets = [p for p in ("poc", "pocplus") if only is None or p == {"poc": "poc", "plus": "pocplus"}[only]]
    root = connect("root", env("SANDBOX_ROOT_PASSWORD"))
    tenant_tables = load_tenant_tables(root)
    accounts: dict[str, dict] = {}
    for prefix in sets:
        suffix = "" if prefix == "poc" else "_PLUS"
        try:
            accounts[prefix] = {e: connect(f"{prefix}_{e.lower()}", env(f"SANDBOX_{e}{suffix}_PASSWORD"))
                                for e in FRANCHISES}
        except (KeyError, mysql.connector.Error) as e:
            check(f"{prefix} accounts can sign in", False,
                  f"{type(e).__name__} (run `python sandbox/sandbox.py {'apply-views' if prefix == 'poc' else 'apply-plus'}`)")
    if "poc" in sets:
        static_views(root, tenant_tables)
    if "pocplus" in sets:
        static_views_plus(root, tenant_tables)
    for prefix in sets:
        grants(root, prefix)
    if accounts:
        ids = foreign_ids(root)
        print(f"     fingerprints: {sum(len(v) for v in ids.values())} unique Salesforce ids across {len(ids)} entities")
        for prefix, acc in accounts.items():
            data_scan(acc, ids, prefix)
            probes(acc, prefix)
    fails = [r for r in results if not r[1]]
    print(f"\nRLS test ({' + '.join(sets)}): {len(results) - len(fails)}/{len(results)} checks passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
