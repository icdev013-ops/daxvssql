"""Sandbox MySQL for the DAX vs SQL POC.

A second MySQL 8.0 instance on this PC, separate from production:
  * port 3307, bound to 127.0.0.1 only, X protocol off, 1 GB buffer pool
  * datadir under daxvssql/.sandbox/ (gitignored; the parent repo ignores daxvssql/)
  * restored from a consistent dump of production, read-only use only
  * per-franchise schemas of mirrored views + one SELECT-only account per franchise

Production is only ever READ (mysqldump --single-transaction). Nothing here writes to it.
Secrets are never printed: production creds come from ../config/credentials.env,
sandbox creds are generated into daxvssql/.env.

usage: python sandbox/sandbox.py <init|start|stop|status|refresh|apply-views|probe|purge>
"""
from __future__ import annotations

import datetime as dt
import json
import os
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
            f"GRANT SELECT, SHOW VIEW ON `{schema}`.* TO '{user}'@'localhost';"
        )
        n = sb_mysql(f"SELECT COUNT(*) FROM information_schema.VIEWS WHERE TABLE_SCHEMA='{schema}';").strip()
        print(f"{ent}: schema {schema} with {n} views; account {user} (SELECT only, password in .env)")


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
            "refresh": cmd_refresh, "apply-views": cmd_apply_views, "probe": cmd_probe, "purge": cmd_purge}

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in COMMANDS:
        sys.exit(__doc__)
    COMMANDS[sys.argv[1]]()
