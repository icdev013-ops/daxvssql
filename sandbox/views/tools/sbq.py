"""Sandbox query helper (127.0.0.1:3307). Password read from daxvssql/.env, never printed."""
import sys
from pathlib import Path

import mysql.connector

ENV = Path(r"C:\Project Data Centralization\daxvssql\.env")


def _env(key):
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith(key + "="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise KeyError(key)


def conn(user="root", db=None):
    pwd = _env("SANDBOX_ROOT_PASSWORD") if user == "root" else _env(f"SANDBOX_{user.split('_')[1].upper()}_PASSWORD")
    return mysql.connector.connect(host="127.0.0.1", port=3307, user=user, password=pwd, database=db,
                                   charset="utf8mb4", use_pure=True)


def q(sql, params=None, user="root", db=None):
    c = conn(user, db)
    try:
        cur = c.cursor()
        cur.execute(sql, params or ())
        rows = cur.fetchall() if cur.with_rows else []
        cols = [d[0] for d in cur.description] if cur.description else []
        return cols, rows
    finally:
        c.close()


if __name__ == "__main__":
    sql = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read()
    for stmt in [s for s in sql.split(";\n") if s.strip()]:
        cols, rows = q(stmt)
        print("\t".join(cols))
        for r in rows:
            print("\t".join("NULL" if v is None else str(v) for v in r))
        print("--")
