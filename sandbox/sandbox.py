"""Sandbox MySQL for the DAX vs SQL POC.

A second MySQL 8.0 instance on this PC, separate from production:
  * port 3307, bound to 127.0.0.1 only, X protocol off, 1 GB buffer pool
  * datadir under daxvssql/.sandbox/ (gitignored; the parent repo ignores daxvssql/)
  * restored from a consistent dump of production, read-only use only
  * per-franchise schemas of mirrored views + one SELECT-only account per franchise

Production is only ever READ (mysqldump --single-transaction). Nothing here writes to it.
Secrets are never printed: production creds come from ../config/credentials.env,
sandbox creds are generated into daxvssql/.env.

Arm B+ adds per-franchise schemas pocplus_<ent> (pass-through views over poc_<ent> plus the governed
metric views in sandbox/views_plus/) and one SELECT-only account per franchise: `apply-plus`. It never
drops or alters a poc_* schema or account.

usage: python sandbox/sandbox.py <init|start|stop|status|refresh|apply-views|apply-plus|probe|purge>
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # daxvssql/
SB = ROOT / ".sandbox"
DATA = SB / "data"
DUMPS = ROOT / "dumps"
INI = SB / "my.ini"
ENV = ROOT / ".env"
PROD_ENV = ROOT.parent / "config" / "credentials.env"
BIN = Path(r"C:\Program Files\MySQL\MySQL Server 8.0\bin")
PORT = 3307
FRANCHISES = ["ICLA", "ICCH", "ICAU"]

# What the POC needs from production. ic_weekly_report first: six icall_gold
# views select from it, so it must exist before those views are created.
DUMP_DATABASES = ["ic_weekly_report", "icall_gold"]
DUMP_TABLES = {"icall_silver": ["silver_lead_status_history"]}

INI_TEXT = f"""[mysqld]
port={PORT}
bind-address=127.0.0.1
mysqlx=0
datadir={DATA.as_posix()}
log-error={(SB / 'mysqld.err').as_posix()}
pid-file={(SB / 'mysqld.pid').as_posix()}
secure-file-priv={(SB / 'files').as_posix()}
local_infile=0
# match production so SQL behaves the same
sql-mode="ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION"
lower_case_table_names=1
character-set-server=utf8mb4
collation-server=utf8mb4_0900_ai_ci
max_allowed_packet=64M
# keep it small: production already holds a 20 GB buffer pool on this PC
innodb_buffer_pool_size=1G
max_connections=40
# defence in depth: no SELECT may run longer than 15 s server-wide
max_execution_time=15000
"""


# ----------------------------------------------------------------------------- env
def read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def set_env_key(key: str, value: str) -> None:
    env = read_env(ENV)
    env[key] = value
    lines = [f"{k}={v}" for k, v in env.items()]
    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")


def sandbox_secret(key: str) -> str:
    env = read_env(ENV)
    if key not in env:
        set_env_key(key, secrets.token_urlsafe(24))
        env = read_env(ENV)
    return env[key]


# ----------------------------------------------------------------------------- mysql helpers
def run(cmd: list[str], pwd: str | None = None, stdin=None, stdout=None, check=True) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("MYSQL_PWD", None)
    if pwd is not None:
        env["MYSQL_PWD"] = pwd           # never on the command line
    return subprocess.run(cmd, env=env, stdin=stdin, stdout=stdout or subprocess.PIPE,
                          stderr=subprocess.PIPE, check=check)


def sb_mysql(sql: str, db: str | None = None, user: str = "root", pwd: str | None = None) -> str:
    pwd = sandbox_secret("SANDBOX_ROOT_PASSWORD") if pwd is None and user == "root" else pwd
    cmd = [str(BIN / "mysql.exe"), "-h", "127.0.0.1", "-P", str(PORT), "-u", user, "-N", "-B"]
    if db:
        cmd += ["-D", db]
    p = subprocess.run(cmd + ["-e", sql], env={**os.environ, "MYSQL_PWD": pwd or ""},
                       capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip())
    return p.stdout


def is_up() -> bool:
    p = subprocess.run([str(BIN / "mysqladmin.exe"), "-h", "127.0.0.1", "-P", str(PORT), "-u", "root",
                        "ping"], env={**os.environ, "MYSQL_PWD": read_env(ENV).get("SANDBOX_ROOT_PASSWORD", "")},
                       capture_output=True, text=True)
    return "alive" in p.stdout


# ----------------------------------------------------------------------------- commands
def cmd_init() -> None:
    if DATA.exists() and any(DATA.iterdir()):
        sys.exit(f"datadir already exists: {DATA} (use purge first)")
    for d in (SB, DATA.parent, SB / "files", DUMPS):
        d.mkdir(parents=True, exist_ok=True)
    INI.write_text(INI_TEXT, encoding="utf-8")
    print("initialising datadir ...")
    r = subprocess.run([str(BIN / "mysqld.exe"), f"--defaults-file={INI}", "--initialize-insecure",
                        "--console"], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("mysqld --initialize failed:\n" + r.stderr[-2000:])
    cmd_start()
    pwd = sandbox_secret("SANDBOX_ROOT_PASSWORD")
    # root has no password right after --initialize-insecure
    p = subprocess.run([str(BIN / "mysql.exe"), "-h", "127.0.0.1", "-P", str(PORT), "-u", "root", "-e",
                        f"ALTER USER 'root'@'localhost' IDENTIFIED BY '{pwd}';"],
                       env={k: v for k, v in os.environ.items() if k != "MYSQL_PWD"},
                       capture_output=True, text=True)
    if p.returncode != 0:
        sys.exit("could not set sandbox root password: " + p.stderr.strip())
    print("sandbox initialised; root password stored in daxvssql/.env (not printed)")


def cmd_start() -> None:
    if read_env(ENV).get("SANDBOX_ROOT_PASSWORD") and is_up():
        print(f"already running on 127.0.0.1:{PORT}")
        return
    flags = 0x00000008 | 0x00000200 | 0x08000000   # DETACHED_PROCESS | NEW_PROCESS_GROUP | NO_WINDOW
    subprocess.Popen([str(BIN / "mysqld.exe"), f"--defaults-file={INI}"], creationflags=flags,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True)
    for _ in range(60):
        time.sleep(1)
        p = subprocess.run([str(BIN / "mysqladmin.exe"), "-h", "127.0.0.1", "-P", str(PORT), "-u", "root",
                            "ping"], env={**os.environ, "MYSQL_PWD": read_env(ENV).get("SANDBOX_ROOT_PASSWORD", "")},
                           capture_output=True, text=True)
        if "alive" in p.stdout or "Access denied" in p.stderr:
            print(f"sandbox up on 127.0.0.1:{PORT}")
            return
    sys.exit(f"sandbox did not start; see {SB / 'mysqld.err'}")


def cmd_stop() -> None:
    if not is_up():
        print("not running")
        return
    run([str(BIN / "mysqladmin.exe"), "-h", "127.0.0.1", "-P", str(PORT), "-u", "root", "shutdown"],
        pwd=sandbox_secret("SANDBOX_ROOT_PASSWORD"))
    print("sandbox stopped")


def cmd_status() -> None:
    up = is_up()
    print(f"running: {up}")
    vf = SB / "data_version.json"
    if vf.exists():
        print("data version:", vf.read_text(encoding="utf-8"))
    if up:
        print(sb_mysql("SELECT TABLE_SCHEMA, COUNT(*) FROM information_schema.TABLES "
                       "WHERE TABLE_SCHEMA NOT IN ('mysql','sys','performance_schema','information_schema') "
                       "GROUP BY 1;"))


def prod_pwd() -> str:
    env = read_env(PROD_ENV)
    if "MYSQL_PASSWORD" not in env:
        sys.exit(f"MYSQL_PASSWORD missing in {PROD_ENV}")
    return env["MYSQL_PASSWORD"]


def cmd_refresh() -> None:
    """Consistent read-only dump of production -> restore into the sandbox."""
    if not is_up():
        sys.exit("sandbox is not running (python sandbox/sandbox.py start)")
    prod = read_env(PROD_ENV)
    host, port, user = prod.get("MYSQL_HOST", "127.0.0.1"), prod.get("MYSQL_PORT", "3306"), prod.get("MYSQL_USER", "root")
    if host not in ("localhost", "127.0.0.1"):
        sys.exit("refusing: production MYSQL_HOST is not local")
    DUMPS.mkdir(exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    started = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    base = [str(BIN / "mysqldump.exe"), "-h", host, "-P", port, "-u", user, "--single-transaction",
            "--no-tablespaces", "--set-gtid-purged=OFF", "--skip-routines", "--skip-events",
            "--default-character-set=utf8mb4", "--quick"]
    files: list[Path] = []
    f1 = DUMPS / f"prod_{stamp}_dbs.sql"
    with open(f1, "wb") as fh:
        print("dumping", ", ".join(DUMP_DATABASES), "...")
        run(base + ["--databases", *DUMP_DATABASES], pwd=prod_pwd(), stdout=fh)
    files.append(f1)
    for db, tables in DUMP_TABLES.items():
        f = DUMPS / f"prod_{stamp}_{db}.sql"
        with open(f, "wb") as fh:
            print(f"dumping {db}: {', '.join(tables)} ...")
            run(base + [db, *tables], pwd=prod_pwd(), stdout=fh)
        files.append(f)
    root = sandbox_secret("SANDBOX_ROOT_PASSWORD")
    for db in DUMP_DATABASES + list(DUMP_TABLES):
        sb_mysql(f"DROP DATABASE IF EXISTS `{db}`;")
    for db in DUMP_TABLES:
        sb_mysql(f"CREATE DATABASE `{db}` CHARACTER SET utf8mb4;")
    mysql = [str(BIN / "mysql.exe"), "-h", "127.0.0.1", "-P", str(PORT), "-u", "root",
             "--default-character-set=utf8mb4"]
    with open(f1, "rb") as fh:
        print("restoring databases ...")
        run(mysql, pwd=root, stdin=fh)
    for db, f in zip(DUMP_TABLES, files[1:]):
        with open(f, "rb") as fh:
            print(f"restoring {db} tables ...")
            run(mysql + ["-D", db], pwd=root, stdin=fh)
    finished = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    version = {"dump_started": started, "restore_finished": finished, "files": [f.name for f in files]}
    (SB / "data_version.json").write_text(json.dumps(version, indent=2), encoding="utf-8")
    print("refresh done:", json.dumps(version))
    print("note: dump files under daxvssql/dumps/ hold personal data; `purge` deletes them")


def cmd_apply_views() -> None:
    """Create per-franchise mirrored-view schemas and SELECT-only accounts.

    Reads sandbox/views/*.sql in name order. Each file is a template where
    {ENT} -> franchise code (ICLA) and {SCHEMA} -> poc_icla.
    A file named like `30_parts.only_ICLA.sql` is applied to that franchise only.
    """
    if not is_up():
        sys.exit("sandbox is not running")
    all_tmpls = sorted((ROOT / "sandbox" / "views").glob("*.sql"))
    if not all_tmpls:
        sys.exit("no view templates in sandbox/views/")
    root = sandbox_secret("SANDBOX_ROOT_PASSWORD")
    mysql = [str(BIN / "mysql.exe"), "-h", "127.0.0.1", "-P", str(PORT), "-u", "root",
             "--default-character-set=utf8mb4"]
    for ent in FRANCHISES:
        tmpls = [t for t in all_tmpls if ".only_" not in t.name or t.name.endswith(f".only_{ent}.sql")]
        schema = f"poc_{ent.lower()}"
        sb_mysql(f"DROP DATABASE IF EXISTS `{schema}`; CREATE DATABASE `{schema}` CHARACTER SET utf8mb4;")
        for t in tmpls:
            sql = t.read_text(encoding="utf-8").replace("{ENT}", ent).replace("{SCHEMA}", schema)
            p = subprocess.run(mysql + ["-D", schema], input=sql.encode("utf-8"),
                               env={**os.environ, "MYSQL_PWD": root}, capture_output=True)
            if p.returncode != 0:
                sys.exit(f"{t.name} failed for {ent}: {p.stderr.decode(errors='replace').strip()}")
        user = f"poc_{ent.lower()}"
        pwd = sandbox_secret(f"SANDBOX_{ent}_PASSWORD")
        sb_mysql(
            f"DROP USER IF EXISTS '{user}'@'localhost';"
            f"CREATE USER '{user}'@'localhost' IDENTIFIED BY '{pwd}' "
            f"WITH MAX_QUERIES_PER_HOUR 3000 MAX_USER_CONNECTIONS 4;"
            # SELECT only: no SHOW VIEW, so Route B can't read the view definitions
            # (Route A can't see Power Query either; keeps the arms symmetric)
            f"GRANT SELECT ON `{schema}`.* TO '{user}'@'localhost';"
        )
        n = sb_mysql(f"SELECT COUNT(*) FROM information_schema.VIEWS WHERE TABLE_SCHEMA='{schema}';").strip()
        print(f"{ent}: schema {schema} with {n} views; account {user} (SELECT only, password in .env)")


# ----------------------------------------------------------------------------- arm B+ (pocplus_*)
PLUS_DIR = ROOT / "sandbox" / "views_plus"
PRIVATE_NAMES = ROOT / "private" / "rules_names.md"
# Decision 2026-11-07 (rules/business_rules.md section 4): ICLA sells on created date, ICCH and ICAU on sold date.
SALES_DATE_COLUMN = {"ICLA": "`Created Date`", "ICCH": "`Original Project Sold Date`", "ICAU": "`Original Project Sold Date`"}
SALES_BASIS_LABEL = {"ICLA": "created date", "ICCH": "sold date", "ICAU": "sold date"}
# Same resource limits as the Route B accounts (cmd_apply_views); rls_test.py compares them.
PLUS_ACCOUNT_LIMITS = "WITH MAX_QUERIES_PER_HOUR 3000 MAX_USER_CONNECTIONS 4"


def plus_names(ent: str) -> tuple[str, str, str]:
    """(Route B mirror schema, B+ schema, B+ account). Hard guard: B+ only ever touches pocplus_* objects."""
    base, schema = f"poc_{ent.lower()}", f"pocplus_{ent.lower()}"
    if ent not in FRANCHISES or not schema.startswith("pocplus_") or schema == base:
        sys.exit(f"refusing: unexpected franchise {ent!r}")
    return base, schema, schema


def icla_excluded_staff_sql() -> str:
    """ICLA's excluded office-staff list as a SQL list, read from the gitignored private file at apply time.
    Same parse as bot/src/prompts/rules.ts parseStaffList. Never printed, never written to disk."""
    if not PRIVATE_NAMES.exists():
        sys.exit(f"missing {PRIVATE_NAMES} (ICLA appointment rule needs it); nothing applied for ICLA")
    lines = PRIVATE_NAMES.read_text(encoding="utf-8").replace("\r\n", "\n").split("\n")
    start = next((i for i, l in enumerate(lines) if re.match(r"^##\s+`?\{ICLA_EXCLUDED_STAFF\}`?", l)), None)
    names: list[str] = []
    if start is not None:
        for l in lines[start + 1:]:
            if re.match(r"^##\s", l):
                break
            m = re.match(r"^\d+\.\s+(.+?)\s*$", l)
            if m:
                names.append(m.group(1))
    if not names:
        sys.exit(f"no names under {{ICLA_EXCLUDED_STAFF}} in {PRIVATE_NAMES}; nothing applied for ICLA")
    return ", ".join("'" + n.replace("\\", "\\\\").replace("'", "''") + "'" for n in names)


def sb_mysql_stdin(sql: str, db: str | None = None) -> None:
    """Run SQL as the sandbox administrator through stdin (keeps passwords and names off the command line).
    Error text is trimmed before the `near '...'` part so a failure cannot echo template values."""
    cmd = [str(BIN / "mysql.exe"), "-h", "127.0.0.1", "-P", str(PORT), "-u", "root", "--default-character-set=utf8mb4"]
    if db:
        cmd += ["-D", db]
    p = subprocess.run(cmd, input=sql.encode("utf-8"), capture_output=True,
                       env={**os.environ, "MYSQL_PWD": sandbox_secret("SANDBOX_ROOT_PASSWORD")})
    if p.returncode != 0:
        msg = p.stderr.decode(errors="replace").strip().splitlines()
        raise RuntimeError(re.sub(r"\s+near '.*$", " (near ... trimmed)", msg[-1] if msg else "mysql failed"))


def plus_template_sql(t: Path, ent: str, schema: str) -> str:
    sql = t.read_text(encoding="utf-8")
    values = {"ENT": ent, "SCHEMA": schema, "SALES_DATE": SALES_DATE_COLUMN[ent], "SALES_BASIS": SALES_BASIS_LABEL[ent]}
    if "{ICLA_EXCLUDED_STAFF_SQL}" in sql:
        if ent != "ICLA":
            sys.exit(f"{t.name}: the ICLA staff list may only be used in an ICLA-only template")
        values["ICLA_EXCLUDED_STAFF_SQL"] = icla_excluded_staff_sql()
    sql = re.sub(r"\{([A-Z][A-Z_]*)\}", lambda m: values.get(m.group(1), m.group(0)), sql)
    left = sorted(set(re.findall(r"\{([A-Z][A-Z_]*)\}", sql)))
    if left:
        sys.exit(f"{t.name}: unfilled placeholders {left} for {ent}")
    return sql


def cmd_apply_plus() -> None:
    """Arm B+: (re)create ONLY the pocplus_<ent> schemas, their views and the pocplus_<ent> accounts.

    Per franchise:
      1. DROP + CREATE schema pocplus_<ent> (never a poc_* schema; see plus_names);
      2. one pass-through SQL SECURITY DEFINER view per view of poc_<ent> (same name, same columns in order),
         read from the catalog, so B+ sees everything B sees. Re-run apply-plus after every apply-views;
      3. the metric views from sandbox/views_plus/*.sql in name order ({ENT}, {SCHEMA}, {SALES_DATE},
         {SALES_BASIS}; {ICLA_EXCLUDED_STAFF_SQL} from private/rules_names.md for ICLA only). A file named
         like `30_x.only_ICLA.sql` is applied to that franchise only;
      4. account pocplus_<ent>: SELECT on its own pocplus_ schema only (no SHOW VIEW), Route B limits,
         password SANDBOX_<ENT>_PLUS_PASSWORD in daxvssql/.env (never printed).
    """
    if not is_up():
        sys.exit("sandbox is not running")
    all_tmpls = sorted(PLUS_DIR.glob("*.sql"))
    if not all_tmpls:
        sys.exit("no view templates in sandbox/views_plus/")
    for ent in FRANCHISES:
        base, schema, user = plus_names(ent)
        cols = sb_mysql("SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS c "
                        f"WHERE TABLE_SCHEMA = '{base}' AND EXISTS (SELECT 1 FROM information_schema.VIEWS v "
                        f"WHERE v.TABLE_SCHEMA = '{base}' AND v.TABLE_NAME = c.TABLE_NAME) "
                        "ORDER BY TABLE_NAME, ORDINAL_POSITION;")
        views: dict[str, list[str]] = {}
        for line in cols.splitlines():
            if line.strip():
                v, c = line.split("\t", 1)
                views.setdefault(v, []).append(c)
        if not views:
            sys.exit(f"{base} has no views: run apply-views first (nothing changed for {ent})")
        q = lambda name: "`" + name.replace("`", "``") + "`"
        stmts = [f"DROP DATABASE IF EXISTS {q(schema)};", f"CREATE DATABASE {q(schema)} CHARACTER SET utf8mb4;"]
        for v, cl in views.items():
            stmts.append(f"CREATE OR REPLACE SQL SECURITY DEFINER VIEW {q(schema)}.{q(v)} AS "
                         f"SELECT {', '.join(q(c) for c in cl)} FROM {q(base)}.{q(v)};")
        sb_mysql_stdin("\n".join(stmts))
        tmpls = [t for t in all_tmpls if ".only_" not in t.name or t.name.endswith(f".only_{ent}.sql")]
        for t in tmpls:
            try:
                sb_mysql_stdin(plus_template_sql(t, ent, schema), db=schema)
            except RuntimeError as e:
                sys.exit(f"{t.name} failed for {ent}: {e}")
        pwd = sandbox_secret(f"SANDBOX_{ent}_PLUS_PASSWORD")
        sb_mysql_stdin(
            f"DROP USER IF EXISTS '{user}'@'localhost';\n"
            f"CREATE USER '{user}'@'localhost' IDENTIFIED BY '{pwd}' {PLUS_ACCOUNT_LIMITS};\n"
            # SELECT only, own B+ schema only: no SHOW VIEW, nothing on poc_* (the views run as their definer)
            f"GRANT SELECT ON {q(schema)}.* TO '{user}'@'localhost';\n")
        n = sb_mysql(f"SELECT COUNT(*) FROM information_schema.VIEWS WHERE TABLE_SCHEMA='{schema}';").strip()
        metrics = sb_mysql(f"SELECT GROUP_CONCAT(TABLE_NAME ORDER BY TABLE_NAME) FROM information_schema.VIEWS "
                           f"WHERE TABLE_SCHEMA='{schema}' AND TABLE_NAME LIKE 'metric\\_%';").strip()
        print(f"{ent}: schema {schema} with {n} views ({len(views)} pass-through over {base}; metric: {metrics}); "
              f"account {user} (SELECT only, password in .env)")
    # the metric views must expose the same columns in every franchise
    sig = sb_mysql("SELECT t.v, COUNT(DISTINCT t.sig), COUNT(*) FROM (SELECT TABLE_SCHEMA AS s, TABLE_NAME AS v, "
                   "GROUP_CONCAT(CONCAT(COLUMN_NAME, ':', DATA_TYPE) ORDER BY ORDINAL_POSITION) AS sig "
                   "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA LIKE 'pocplus\\_%' AND TABLE_NAME LIKE 'metric\\_%' "
                   "GROUP BY TABLE_SCHEMA, TABLE_NAME) AS t GROUP BY t.v;")
    rows = [line.split("\t") for line in sig.splitlines() if line.strip()]
    drift = [v for v, nsig, nschema in rows if nsig != "1" or nschema != str(len(FRANCHISES))]
    if drift:
        sys.exit(f"metric views differ in columns between franchises (or are missing in one): {', '.join(drift)}")
    print(f"metric views identical in columns across {len(FRANCHISES)} franchises: {', '.join(sorted(r[0] for r in rows))}")


def cmd_probe() -> None:
    """Isolation smoke test: each franchise account sees only its own schema."""
    for ent in FRANCHISES:
        user, schema = f"poc_{ent.lower()}", f"poc_{ent.lower()}"
        pwd = sandbox_secret(f"SANDBOX_{ent}_PASSWORD")
        schemas = sb_mysql("SELECT GROUP_CONCAT(SCHEMA_NAME) FROM information_schema.SCHEMATA "
                           "WHERE SCHEMA_NAME NOT IN ('information_schema','performance_schema');",
                           user=user, pwd=pwd).strip()
        print(f"{ent}: visible schemas = {schemas}")
        for other in ("icall_gold", *(f"poc_{e.lower()}" for e in FRANCHISES if e != ent)):
            try:
                sb_mysql(f"SELECT 1 FROM information_schema.TABLES LIMIT 1; SHOW TABLES FROM `{other}`;",
                         user=user, pwd=pwd)
                print(f"  !! {ent} account can list {other}")
            except RuntimeError as e:
                print(f"  ok: {ent} -> {other} denied ({str(e).splitlines()[-1][:60]})")
        try:
            sb_mysql(f"CREATE TABLE `{schema}`.x (a int);", user=user, pwd=pwd)
            print(f"  !! {ent} account could CREATE")
        except RuntimeError:
            print(f"  ok: {ent} CREATE denied")


def cmd_purge() -> None:
    if is_up():
        cmd_stop()
        time.sleep(3)
    for d in (SB, DUMPS):
        if d.exists():
            shutil.rmtree(d)
            print("deleted", d)
    env = read_env(ENV)
    for k in [k for k in env if k.startswith("SANDBOX_")]:
        env.pop(k)
    ENV.write_text("".join(f"{k}={v}\n" for k, v in env.items()), encoding="utf-8")
    print("sandbox purged (datadir, dumps and sandbox passwords removed)")


COMMANDS = {"init": cmd_init, "start": cmd_start, "stop": cmd_stop, "status": cmd_status,
            "refresh": cmd_refresh, "apply-views": cmd_apply_views, "apply-plus": cmd_apply_plus,
            "probe": cmd_probe, "purge": cmd_purge}

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in COMMANDS:
        sys.exit(__doc__)
    COMMANDS[sys.argv[1]]()
