"""Calibrate the gold set: run every reference SQL and reference DAX pair and compare them.

For each template x franchise this script
  1. resolves the anchor-relative period(s) and the franchise macros (sales date basis,
     appointment rules) for the anchor date,
  2. resolves the template params by running each `param_sql` on the franchise's sandbox views,
  3. runs `reference_sql` on the sandbox MySQL (schema poc_<ent>, 127.0.0.1:3307) and
     `reference_dax` on the franchise's Power BI dataset (executeQueries, service principal),
  4. compares the two results column by column under strict calibration tolerances.

Output (gitignored, holds real values):
  gold/private/calibration_<anchor>.jsonl   one line per case (and per alternate / reading)
  gold/private/refs_<anchor>.jsonl          reference results in the scorer's format
  gold/private/calibration_<anchor>_summary.md
and a short summary on stdout (counts only, no values).

usage (from the daxvssql folder):
  python gold/validate.py --anchor 2026-11-07                       # Test + Dev (default)
  python gold/validate.py --anchor 2026-11-07 --splits all
  python gold/validate.py --anchor 2026-11-07 --ids T001,T020 --franchises ICLA
  python gold/validate.py --anchor 2026-11-07 --sql-only            # no Power BI calls
  python gold/validate.py --anchor 2026-11-07 --probes-only         # checksum probes only
  python gold/validate.py --list                                    # split / type counts, no queries
  python gold/validate.py --shim ...                                # views not applied yet: run the
                                                                    # view SQL as CTEs over icall_gold

Secrets: sandbox passwords come from daxvssql/.env, Power BI service-principal keys from the
SAAS repo's .env.local. Both are read at run time and never printed. Power BI traffic is capped at
--dax-rpm calls per minute (default 25); the service principal is shared with production.
"""
from __future__ import annotations

import argparse
import calendar
import datetime as dt
import decimal
import glob
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

import yaml

GOLD = Path(__file__).resolve().parent
ROOT = GOLD.parent                                            # daxvssql/
PRIVATE = GOLD / "private"
SANDBOX_ENV = ROOT / ".env"
VIEWS_DIR = ROOT / "sandbox" / "views"
PBI_ENV = ROOT.parent / "Inspired Closets SAAS Reporting" / ".env.local"
REPORTS = ROOT.parent / "Power BI Report" / "per_entity"
ICLA_APPT_PAGE = (REPORTS / "ICLA" / "Salesforce BI ICLA.Report" / "definition" / "pages" /
                  "1fbd30ec4bb09bae9500" / "page.json")
FRANCHISES = ["ICLA", "ICCH", "ICAU"]
TYPE_TAGS = ["appointments_sales", "row_level_list", "cohort_funnel", "closing_rate", "sold_vs_created",
             "appointments_rules", "ranking", "time_intelligence"]
QUOTAS = {"appointments_sales": 30, "row_level_list": 30, "cohort_funnel": 30, "closing_rate": 20,
          "sold_vs_created": 20, "appointments_rules": 20, "ranking": 20, "time_intelligence": 20}
TOKEN = re.compile(r"\{([A-Za-z_][A-Za-z0-9_.]*)(?:\|([a-z_]+))?\}")


# ============================================================================ env / secrets
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


# ============================================================================ periods
def add_months(d: dt.date, n: int) -> dt.date:
    """EDATE: same day n months later, clipped to the month end."""
    y, m = divmod(d.month - 1 + n, 12)
    y, m = d.year + y, m + 1
    return dt.date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def month_end(d: dt.date) -> dt.date:
    return dt.date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def week_start(d: dt.date) -> dt.date:
    """Weeks start on Sunday (the model's Start of Week columns and the KPI page)."""
    return d - dt.timedelta(days=(d.weekday() + 1) % 7)


def resolve_period(spec: str, a: dt.date) -> tuple[dt.date | None, dt.date | None]:
    """Inclusive [d0, d1] for an anchor-relative period name. all_time -> (None, None)."""
    q0 = dt.date(a.year, 3 * ((a.month - 1) // 3) + 1, 1)
    fixed = {
        "all_time": (None, None),
        "today": (a, a),
        "yesterday": (a - dt.timedelta(1), a - dt.timedelta(1)),
        "ytd": (dt.date(a.year, 1, 1), a),
        "this_year": (dt.date(a.year, 1, 1), dt.date(a.year, 12, 31)),
        "last_year": (dt.date(a.year - 1, 1, 1), dt.date(a.year - 1, 12, 31)),
        "last_year_ytd": (dt.date(a.year - 1, 1, 1), add_months(a, -12)),
        "two_years_ago": (dt.date(a.year - 2, 1, 1), dt.date(a.year - 2, 12, 31)),
        "mtd": (a.replace(day=1), a),
        "this_month": (a.replace(day=1), month_end(a)),
        "last_month": (add_months(a.replace(day=1), -1), a.replace(day=1) - dt.timedelta(1)),
        "last_month_mtd": (add_months(a.replace(day=1), -1), add_months(a, -1)),
        "two_months_ago": (add_months(a.replace(day=1), -2), add_months(a.replace(day=1), -1) - dt.timedelta(1)),
        "next_month": (add_months(a.replace(day=1), 1), month_end(add_months(a.replace(day=1), 1))),
        "next_month_last_year": (add_months(a.replace(day=1), -11), month_end(add_months(a.replace(day=1), -11))),
        "last_month_last_year": (add_months(a.replace(day=1), -13), add_months(a.replace(day=1), -12) - dt.timedelta(1)),
        "this_quarter": (q0, add_months(q0, 3) - dt.timedelta(1)),
        "qtd": (q0, a),
        "last_quarter": (add_months(q0, -3), q0 - dt.timedelta(1)),
        "last_quarter_last_year": (add_months(q0, -15), add_months(q0, -12) - dt.timedelta(1)),
        "q1_this_year": (dt.date(a.year, 1, 1), dt.date(a.year, 3, 31)),
        "q1_last_year": (dt.date(a.year - 1, 1, 1), dt.date(a.year - 1, 3, 31)),
        "this_week": (week_start(a), week_start(a) + dt.timedelta(6)),
        "wtd": (week_start(a), a),
        "last_week": (week_start(a) - dt.timedelta(7), week_start(a) - dt.timedelta(1)),
        "next_week": (week_start(a) + dt.timedelta(7), week_start(a) + dt.timedelta(13)),
        "last_4_months": (add_months(a, -4), a),           # the Salesforce "Closing % - Last 4 Months" window
        "prior_4_months": (add_months(a, -8), add_months(a, -4) - dt.timedelta(1)),
        "last_12_months": (add_months(a, -12), a),
        "last_12_full_months": (add_months(a.replace(day=1), -12), a.replace(day=1) - dt.timedelta(1)),
        "since_2025_06": (dt.date(2025, 6, 1), a),
        "ytd_full_months": (dt.date(a.year, 1, 1), a.replace(day=1) - dt.timedelta(1)),
        "next_monday": (a + dt.timedelta(7 - a.weekday()), a + dt.timedelta(7 - a.weekday())),
        "from_today": (a, dt.date(2030, 12, 31)),
        "before_last_year": (dt.date(2000, 1, 1), dt.date(a.year - 2, 12, 31)),
    }
    if spec in fixed:
        return fixed[spec]
    m = re.fullmatch(r"last_(\d+)_days", spec)               # N days ending on the anchor, inclusive
    if m:
        return a - dt.timedelta(int(m.group(1)) - 1), a
    m = re.fullmatch(r"last_(\d+)_full_weeks", spec)         # N complete Sun-Sat weeks before this week
    if m:
        ws = week_start(a)
        return ws - dt.timedelta(7 * int(m.group(1))), ws - dt.timedelta(1)
    m = re.fullmatch(r"year_(\d{4})", spec)
    if m:
        y = int(m.group(1))
        return dt.date(y, 1, 1), dt.date(y, 12, 31)
    m = re.fullmatch(r"year_minus_(\d)", spec)
    if m:
        y = a.year - int(m.group(1))
        return dt.date(y, 1, 1), dt.date(y, 12, 31)
    m = re.fullmatch(r"range_(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})", spec)
    if m:
        return dt.date.fromisoformat(m.group(1)), dt.date.fromisoformat(m.group(2))
    raise ValueError(f"unknown period {spec!r}")


def dax_date(d: dt.date) -> str:
    return f"DATE({d.year}, {d.month}, {d.day})"


def period_tokens(spec: str, a: dt.date, prefix: str = "") -> dict[str, str]:
    d0, d1 = resolve_period(spec, a)
    p = prefix + "." if prefix else ""
    if d0 is None:
        return {f"{p}period": spec}
    nxt = d1 + dt.timedelta(1)
    return {
        f"{p}period": spec,
        f"{p}d0": d0.isoformat(), f"{p}d1": d1.isoformat(), f"{p}d1_next": nxt.isoformat(),
        f"{p}d0_dax": dax_date(d0), f"{p}d1_dax": dax_date(d1), f"{p}d1_next_dax": dax_date(nxt),
        f"{p}year": str(d0.year), f"{p}month": str(d0.month), f"{p}days": str((d1 - d0).days + 1),
        f"{p}mname": calendar.month_name[d0.month], f"{p}quarter": f"Q{(d0.month - 1) // 3 + 1}",
        f"{p}label": f"{d0.isoformat()}..{d1.isoformat()}",
    }


def anchor_tokens(a: dt.date) -> dict[str, str]:
    return {"anchor": a.isoformat(), "anchor_dax": dax_date(a), "anchor_next": (a + dt.timedelta(1)).isoformat(),
            "anchor_next_dax": dax_date(a + dt.timedelta(1)), "anchor_year": str(a.year),
            "anchor_prev_year": str(a.year - 1), "anchor_prev2_year": str(a.year - 2),
            "anchor_next_year": str(a.year + 1),
            "anchor_mname": calendar.month_name[a.month]}


# ============================================================================ substitution
def sql_lit(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, (int, float, decimal.Decimal)) and not isinstance(v, bool):
        return str(v)
    return "'" + str(v).replace("\\", "\\\\").replace("'", "''") + "'"


def dax_lit(v) -> str:
    if v is None:
        return "BLANK()"
    if isinstance(v, (int, float, decimal.Decimal)) and not isinstance(v, bool):
        return str(v)
    return '"' + str(v).replace('"', '""') + '"'


def fill(text: str, tokens: dict, params: dict, what: str) -> str:
    """Replace {name}, {name|sql}, {name|dax}, {name|first} in text. Unknown tokens raise."""
    def rep(m):
        name, flt = m.group(1), m.group(2)
        if name in params:
            v = params[name]
        elif name in tokens:
            v = tokens[name]
        else:
            raise KeyError(f"{what}: unknown token {{{name}}}")
        if flt == "sql":
            return sql_lit(v)
        if flt == "dax":
            return dax_lit(v)
        if flt == "first":
            return str(v).split()[0] if v else ""
        if flt == "lower":
            return str(v).lower()
        if flt:
            raise KeyError(f"{what}: unknown filter |{flt}")
        return str(v)
    # macros may contain tokens themselves (e.g. the staff list); expand twice
    out = TOKEN.sub(rep, text)
    return TOKEN.sub(rep, out) if TOKEN.search(out) else out


def icla_staff() -> list[str]:
    """Staff names the ICLA Appointments pages exclude, read from the report page filter at run time
    (the names are never stored in this repo)."""
    j = json.loads(ICLA_APPT_PAGE.read_text(encoding="utf-8"))
    for f in j["filterConfig"]["filters"]:
        if f.get("field", {}).get("Column", {}).get("Property") == "Assigned Staff Member":
            vals = f["filter"]["Where"][0]["Condition"]["Not"]["Expression"]["In"]["Values"]
            return [v[0]["Literal"]["Value"][1:-1].replace("''", "'") for v in vals]
    raise RuntimeError("ICLA staff exclusion filter not found in the Appointments page")


def runtime_tokens() -> dict[str, str]:
    staff = icla_staff()
    return {"ICLA_STAFF_SQL": ", ".join(sql_lit(s) for s in staff),
            "ICLA_STAFF_DAX": ", ".join(dax_lit(s) for s in staff)}


def franchise_tokens(gold: dict, ent: str, rt: dict) -> dict[str, str]:
    out = {"ent": ent}
    for name, per in (gold.get("macros") or {}).items():
        if isinstance(per, dict):
            if ent in per:
                out[name] = per[ent]
        else:
            out[name] = per
    out.update(rt)
    return out


# ============================================================================ SQL
class Sandbox:
    def __init__(self, user: str, shim: bool):
        import mysql.connector  # noqa: F401  (imported lazily: --list needs no driver)
        self.env = read_env(SANDBOX_ENV)
        self.user, self.shim = user, shim
        self.conns: dict[str, object] = {}
        self._defs: dict[str, dict[str, str]] = {}

    def conn(self, ent: str):
        import mysql.connector
        if ent not in self.conns:
            schema = f"poc_{ent.lower()}"
            if self.user == "root" or self.shim:
                user, pwd, db = "root", self.env["SANDBOX_ROOT_PASSWORD"], ("icall_gold" if self.shim else schema)
            else:
                user, pwd, db = f"poc_{ent.lower()}", self.env[f"SANDBOX_{ent}_PASSWORD"], schema
            c = mysql.connector.connect(host="127.0.0.1", port=3307, user=user, password=pwd, database=db,
                                        autocommit=True, connection_timeout=20)
            cur = c.cursor()
            # same session settings as the bot's SQL route: ANSI_QUOTES, read-only, time limit
            cur.execute("SET SESSION sql_mode = CONCAT(@@sql_mode, ',ANSI_QUOTES')")
            cur.execute("SET SESSION TRANSACTION READ ONLY")
            cur.execute("SET SESSION max_execution_time = 15000")
            cur.close()
            self.conns[ent] = c
        return self.conns[ent]

    def view_defs(self, ent: str) -> dict[str, str]:
        if ent not in self._defs:
            schema = f"poc_{ent.lower()}"
            files = sorted(VIEWS_DIR.glob("*.sql"))
            files = [f for f in files if ".only_" not in f.name or f.name.endswith(f".only_{ent}.sql")]
            defs: dict[str, str] = {}
            for f in files:
                txt = f.read_text(encoding="utf-8").replace("{ENT}", ent).replace("{SCHEMA}", schema)
                txt = "\n".join(line for line in txt.splitlines() if not line.strip().startswith("--"))
                for m in re.finditer(r"CREATE OR REPLACE SQL SECURITY DEFINER VIEW `[^`]+`\.`([^`]+)` AS\s*(.*?);\s*(?=CREATE|\Z)",
                                     txt, re.S):
                    defs[m.group(1)] = m.group(2).replace(f"`{schema}`.", "")
            self._defs[ent] = defs
        return self._defs[ent]

    def shimmed(self, sql: str, ent: str) -> str:
        defs = self.view_defs(ent)
        used = [n for n in defs if re.search(r"`" + re.escape(n) + r"`|(?<![\w`])" + re.escape(n) + r"(?![\w`])", sql)]
        if "SoldDateTable" in used and "DateTable" not in used:
            used.append("DateTable")
        ctes = ",\n".join(f"`{n}` AS ({defs[n]})" for n in defs if n in used)
        s = sql.strip().rstrip(";")
        if not ctes:
            return s
        if re.match(r"(?is)^\s*WITH\s+RECURSIVE\s", s):
            return re.sub(r"(?is)^\s*WITH\s+RECURSIVE\s", "WITH RECURSIVE " + ctes + ",\n", s, count=1)
        if re.match(r"(?is)^\s*WITH\s", s):
            return re.sub(r"(?is)^\s*WITH\s", "WITH " + ctes + ",\n", s, count=1)
        return "WITH " + ctes + "\n" + s

    def run(self, ent: str, sql: str) -> dict:
        t0 = time.time()
        try:
            c = self.conn(ent)
            cur = c.cursor()
            cur.execute(self.shimmed(sql, ent) if self.shim else sql.strip().rstrip(";"))
            cols = [d[0] for d in cur.description] if cur.description else []
            rows = [list(r) for r in cur.fetchall()] if cur.description else []
            cur.close()
            return {"columns": cols, "rows": [[norm_cell(x) for x in r] for r in rows],
                    "ms": int((time.time() - t0) * 1000)}
        except Exception as e:  # noqa: BLE001
            self.conns.pop(ent, None)
            return {"error": f"{type(e).__name__}: {str(e)[:500]}", "ms": int((time.time() - t0) * 1000)}


# ============================================================================ DAX
class PowerBI:
    def __init__(self, rpm: int):
        self.env = read_env(PBI_ENV)
        self.rpm = rpm
        self.calls: list[float] = []
        self.tok: tuple[str, float] | None = None
        self.n = 0

    def token(self) -> str:
        if self.tok and self.tok[1] > time.time() + 120:
            return self.tok[0]
        e = self.env
        body = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": e["PBI_CLIENT_ID"],
                                       "client_secret": e["PBI_CLIENT_SECRET"],
                                       "scope": "https://analysis.windows.net/powerbi/api/.default"}).encode()
        r = json.loads(urllib.request.urlopen(urllib.request.Request(
            f"https://login.microsoftonline.com/{e['PBI_TENANT_ID']}/oauth2/v2.0/token", data=body), timeout=30).read())
        self.tok = (r["access_token"], time.time() + int(r.get("expires_in", 3600)))
        return self.tok[0]

    def throttle(self) -> None:
        now = time.time()
        self.calls = [c for c in self.calls if now - c < 60]
        if len(self.calls) >= self.rpm:
            time.sleep(60 - (now - self.calls[0]) + 0.3)
            now = time.time()
            self.calls = [c for c in self.calls if now - c < 60]
        self.calls.append(time.time())

    def run(self, ent: str, query: str) -> dict:
        t0 = time.time()
        self.throttle()
        self.n += 1
        ws, ds = self.env["PBI_WORKSPACE_ID"], self.env[f"PBI_DS_{ent}"]
        body = json.dumps({"queries": [{"query": query}], "serializerSettings": {"includeNulls": True}}).encode()
        req = urllib.request.Request(
            f"https://api.powerbi.com/v1.0/myorg/groups/{ws}/datasets/{ds}/executeQueries", data=body,
            headers={"Authorization": "Bearer " + self.token(), "Content-Type": "application/json"})
        for attempt in range(3):
            try:
                j = json.loads(urllib.request.urlopen(req, timeout=120).read())
                break
            except urllib.error.HTTPError as e:
                msg = e.read().decode("utf-8", "replace")
                if e.code == 429 and attempt < 2:
                    time.sleep(30)
                    continue
                return {"error": f"HTTP {e.code}: {msg[:600]}", "ms": int((time.time() - t0) * 1000)}
            except Exception as e:  # noqa: BLE001
                if attempt < 2:
                    time.sleep(5)
                    continue
                return {"error": f"{type(e).__name__}: {str(e)[:300]}", "ms": int((time.time() - t0) * 1000)}
        res = (j.get("results") or [{}])[0]
        # an error inside an HTTP 200 is still an error
        if "error" in res:
            return {"error": "query error: " + json.dumps(res["error"])[:600], "ms": int((time.time() - t0) * 1000)}
        tables = res.get("tables") or []
        if not tables:
            return {"error": "no table in response", "ms": int((time.time() - t0) * 1000)}
        t = tables[0]
        if "error" in t:
            return {"error": "table error: " + json.dumps(t["error"])[:600], "ms": int((time.time() - t0) * 1000)}
        rows = t.get("rows") or []
        cols = list(rows[0].keys()) if rows else []
        return {"columns": [re.sub(r"^.*\[(.*)\]$", r"\1", c) for c in cols],
                "rows": [[norm_cell(r.get(c)) for c in cols] for r in rows],
                "ms": int((time.time() - t0) * 1000)}


# ============================================================================ comparison
def norm_cell(x):
    if isinstance(x, decimal.Decimal):
        return float(x)
    if isinstance(x, dt.datetime):
        return x.date().isoformat() if x.time() == dt.time(0) else x.isoformat(timespec="seconds")
    if isinstance(x, dt.date):
        return x.isoformat()
    if isinstance(x, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}T00:00:00(\.0+)?Z?", x):
        return x[:10]
    if isinstance(x, bool):
        return int(x)
    return x


def as_num(x):
    if x is None or x == "":
        return 0.0                                  # blank = 0 (production formatter rule)
    if isinstance(x, (int, float)):
        return float(x)
    try:
        return float(str(x).replace(",", ""))
    except ValueError:
        return None


def cell_equal(a, b, ctype: str) -> tuple[bool, bool]:
    """(strict_equal, within_scoring_tolerance)."""
    if ctype in ("count", "currency", "percent", "number"):
        x, y = as_num(a), as_num(b)
        if x is None or y is None:
            return False, False
        d = abs(x - y)
        if ctype == "count":
            return d < 1e-9, d < 1e-9
        if ctype == "percent":
            return d <= 1e-4, d <= 0.1
        return d <= max(0.01, 1e-9 * abs(x)), d <= 0.005 * max(abs(x), abs(y))
    if ctype == "date":
        sa, sb = str(a or "")[:10], str(b or "")[:10]
        return sa == sb, sa == sb
    sa = re.sub(r"\s+", " ", str(a if a is not None else "")).strip().lower()
    sb = re.sub(r"\s+", " ", str(b if b is not None else "")).strip().lower()
    return sa == sb, sa == sb


def compare(spec: dict, sql: dict, dax: dict) -> tuple[str, list[str]]:
    """agree | near (within scoring tolerance only) | disagree | error."""
    if sql.get("error") or dax.get("error"):
        return "error", [f"sql: {sql.get('error')}" if sql.get("error") else "",
                         f"dax: {dax.get('error')}" if dax.get("error") else ""]
    cols = spec.get("result_columns") or []
    types = spec.get("col_types") or {}
    diffs: list[str] = []
    for name, res in (("sql", sql), ("dax", dax)):
        if res["rows"] and len(res["rows"][0]) != len(cols):
            diffs.append(f"{name} returned {len(res['rows'][0])} columns, expected {len(cols)}")
    if diffs:
        return "disagree", diffs
    shape = spec.get("answer_shape", "number")
    srows, drows = sql["rows"], dax["rows"]
    near = False
    if shape == "number":
        if len(srows) != len(drows):
            # a scalar with no rows on one side and blanks on the other is still a match
            if not srows and all(as_num(c) in (0.0, None) for r in drows for c in r):
                return "agree", ["sql returned no row, dax blank"]
            if not drows and all(as_num(c) in (0.0, None) for r in srows for c in r):
                return "agree", ["dax returned no row, sql blank/zero"]
            return "disagree", [f"row count sql {len(srows)} vs dax {len(drows)}"]
        pairs = list(zip(srows, drows))
    else:
        lab = spec.get("label_column") or cols[0]
        li = cols.index(lab) if lab in cols else 0
        key = lambda r: re.sub(r"\s+", " ", str(r[li] if r[li] is not None else "")).strip().lower()
        if shape == "ranked_list":
            if len(srows) != len(drows):
                diffs.append(f"row count sql {len(srows)} vs dax {len(drows)}")
            ob = spec.get("order_by") or {}
            oc = ob.get("column") if isinstance(ob, dict) else ob
            oi = cols.index(oc) if oc in cols else None
            for pos, (a, b) in enumerate(zip(srows, drows)):
                if key(a) != key(b):
                    tied = oi is not None and cell_equal(a[oi], b[oi], types.get(oc, "number"))[0]
                    if not tied:
                        diffs.append(f"rank {pos + 1}: sql {key(a)!r} vs dax {key(b)!r}")
            smap, dmap = {key(r): r for r in srows}, {key(r): r for r in drows}
            pairs = [(smap[k], dmap[k]) for k in smap if k in dmap]
        else:
            smap, dmap = {key(r): r for r in srows}, {key(r): r for r in drows}
            if len(smap) != len(srows) or len(dmap) != len(drows):
                # labels repeat (e.g. two projects with the same name): compare whole rows as a multiset
                def canon(r):
                    out = []
                    for i, c in enumerate(cols):
                        t = types.get(c, "text")
                        if t in ("count", "currency", "percent", "number"):
                            v = as_num(r[i])
                            out.append(round(v, 2) if v is not None else None)
                        elif t == "date":
                            out.append(str(r[i] or "")[:10])
                        else:
                            out.append(re.sub(r"\s+", " ", str(r[i] if r[i] is not None else "")).strip().lower())
                    return tuple(out)
                cs, cd = Counter(canon(r) for r in srows), Counter(canon(r) for r in drows)
                if cs == cd:
                    return "agree", [f"duplicate labels; compared {len(srows)} rows as a multiset"]
                only_s, only_d = list((cs - cd).elements()), list((cd - cs).elements())
                return "disagree", [f"multiset compare: {len(only_s)} rows only in sql, {len(only_d)} only in dax",
                                    f"e.g. sql {only_s[:1]} dax {only_d[:1]}"]
            only_s, only_d = sorted(set(smap) - set(dmap)), sorted(set(dmap) - set(smap))
            if only_s:
                diffs.append(f"{len(only_s)} labels only in sql, e.g. {only_s[:3]}")
            if only_d:
                diffs.append(f"{len(only_d)} labels only in dax, e.g. {only_d[:3]}")
            pairs = [(smap[k], dmap[k]) for k in smap if k in dmap]
    for a, b in pairs:
        for i, c in enumerate(cols):
            strict, tol = cell_equal(a[i], b[i], types.get(c, "text"))
            if not strict:
                if tol:
                    near = True
                diffs.append(f"{c}: sql {a[i]!r} vs dax {b[i]!r}" + (" (within tolerance)" if tol else ""))
    hard = [d for d in diffs if "(within tolerance)" not in d]
    if hard:
        return "disagree", diffs[:12]
    return ("near" if near else "agree"), diffs[:12]


# ============================================================================ templates
def load_gold(path: Path) -> dict:
    gold = yaml.safe_load(path.read_text(encoding="utf-8"))
    tpls = gold["templates"]
    ids = Counter(t["id"] for t in tpls)
    dup = [i for i, n in ids.items() if n > 1]
    if dup:
        raise SystemExit(f"duplicate template ids: {dup}")
    return gold


REF_FIELDS = ("reference_sql", "reference_dax", "result_columns", "col_types", "answer_shape", "params",
              "period", "periods", "label_column", "value_columns", "order_by")


def specs_for(tpl: dict) -> list[tuple[str, dict]]:
    """(suffix, spec) pairs to calibrate: the main reference, alternates, readings, facts, evidence."""
    out = []
    if tpl.get("reference_sql") and tpl.get("reference_dax"):
        out.append(("", tpl))
    for key, tag in (("alternates", "alt"), ("readings", "r"), ("facts", "f")):
        for i, alt in enumerate(tpl.get(key) or [], 1):
            if alt.get("reference_sql") and alt.get("reference_dax"):
                merged = {k: v for k, v in tpl.items() if k not in ("alternates", "readings", "facts")}
                merged.update(alt)
                if tpl.get("answer_shape") == "rubric" and "answer_shape" not in alt:
                    merged["answer_shape"] = "number"
                out.append((f"#{tag}{i}", merged))
    ev = tpl.get("evidence")
    if ev and ev.get("reference_sql") and ev.get("reference_dax"):
        merged = {k: v for k, v in tpl.items() if k not in ("alternates", "readings", "facts", "evidence")}
        merged.update(ev)
        merged.setdefault("answer_shape", "number")
        if merged["answer_shape"] == "rubric":
            merged["answer_shape"] = "number"
        out.append(("#evidence", merged))
    return out


def resolve(spec: dict, ent: str, anchor: dt.date, base_tokens: dict, sandbox: Sandbox | None,
            params_cache: dict) -> tuple[dict, dict]:
    """Tokens + params for one spec. Params run on the franchise's views (cached per template x franchise)."""
    tokens = dict(base_tokens)
    tokens.update(anchor_tokens(anchor))
    if spec.get("period"):
        tokens.update(period_tokens(spec["period"], anchor))
    for name, ps in (spec.get("periods") or {}).items():
        tokens.update(period_tokens(ps, anchor, prefix=name))
    params: dict = {}
    for name, p in (spec.get("params") or {}).items():
        ck = (spec.get("id"), ent, name)
        if ck in params_cache:
            params[name] = params_cache[ck]
            continue
        if sandbox is None:
            raise RuntimeError("params need the sandbox")
        res = sandbox.run(ent, fill(p["param_sql"], tokens, params, f"param {name}"))
        if res.get("error"):
            raise RuntimeError(f"param {name}: {res['error']}")
        if not res["rows"] or res["rows"][0][0] in (None, ""):
            raise RuntimeError(f"param {name}: no value")
        params[name] = res["rows"][0][0]
        params_cache[ck] = params[name]
    return tokens, params


def cases(gold: dict, splits: set[str] | None, ids: set[str] | None, ents: set[str] | None):
    for tpl in gold["templates"]:
        if splits and tpl["split"] not in splits:
            continue
        if ids and tpl["id"] not in ids and tpl.get("paraphrase_of") not in ids:
            continue
        for ent in tpl.get("franchises") or []:
            if ents and ent not in ents:
                continue
            yield tpl, ent


# ============================================================================ listing / quotas
def listing(gold: dict) -> str:
    tpls = gold["templates"]
    lines = ["| Split | Templates | Cases |", "|---|---|---|"]
    by_split: dict[str, list] = defaultdict(list)
    for t in tpls:
        by_split[t["split"]].append(t)
    for s in ("dev", "test", "robustness", "multiturn", "why_forecast", "unanswerable", "ambiguity"):
        ts = by_split.get(s, [])
        lines.append(f"| {s} | {len(ts)} | {sum(len(t.get('franchises') or []) for t in ts)} |")
    test = by_split.get("test", [])
    lines += ["", "Test split by question type (a template can carry several tags):", "",
              "| Type | Templates | Cases | Quota (cases) | Met |", "|---|---|---|---|---|"]
    for tag in TYPE_TAGS:
        ts = [t for t in test if tag in (t.get("types") or [])]
        n = sum(len(t.get("franchises") or []) for t in ts)
        lines.append(f"| {tag} | {len(ts)} | {n} | {QUOTAS[tag]} | {'yes' if n >= QUOTAS[tag] else 'NO'} |")
    fresh = [t for t in test if str(t.get("source", "")).startswith("fresh")]
    per_ent = Counter(e for t in test for e in t.get("franchises") or [])
    lines += ["", f"Test templates written fresh: {len(fresh)}",
              "Test cases per franchise: " + ", ".join(f"{e} {per_ent[e]}" for e in FRANCHISES),
              f"Robustness: {len(by_split.get('robustness', []))} templates x 2 paraphrases",
              f"Multi-turn conversations: {len({t.get('conversation') for t in by_split.get('multiturn', [])})}"]
    return "\n".join(lines)


# ============================================================================ checksum probes
def run_probes(gold: dict, ents: list[str], sandbox: Sandbox | None, pbi: PowerBI | None) -> list[dict]:
    out = []
    for ent in ents:
        for p in gold.get("checksum_probes") or []:
            row = {"probe": p["id"], "franchise": ent}
            if sandbox:
                r = sandbox.run(ent, p["sql"])
                row["sql"] = r.get("rows", [[None]])[0] if not r.get("error") else r["error"]
            if pbi:
                r = pbi.run(ent, p["dax"])
                row["dax"] = r.get("rows", [[None]])[0] if not r.get("error") else r["error"]
            if sandbox and pbi and isinstance(row.get("sql"), list) and isinstance(row.get("dax"), list):
                row["match"] = all(cell_equal(a, b, "number")[0] for a, b in zip(row["sql"], row["dax"]))
            out.append(row)
    return out


# ============================================================================ main
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gold", default=str(GOLD / "templates.yaml"))
    ap.add_argument("--anchor", default=None, help="anchor date YYYY-MM-DD (default: the file's anchor_default)")
    ap.add_argument("--splits", default="test,dev", help="comma list or 'all'")
    ap.add_argument("--ids", default="")
    ap.add_argument("--franchises", default="")
    ap.add_argument("--sql-only", action="store_true")
    ap.add_argument("--dax-only", action="store_true")
    ap.add_argument("--probes-only", action="store_true")
    ap.add_argument("--no-probes", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--shim", action="store_true", help="run view SQL as CTEs over icall_gold (views not applied)")
    ap.add_argument("--sql-user", default="franchise", choices=["franchise", "root"])
    ap.add_argument("--dax-rpm", type=int, default=25)
    ap.add_argument("--tag", default="", help="suffix for output file names")
    ap.add_argument("--show", action="store_true", help="print each case's first rows (local debugging only)")
    a = ap.parse_args()

    gold = load_gold(Path(a.gold))
    if a.list:
        print(listing(gold))
        return
    anchor = dt.date.fromisoformat(a.anchor or str(gold.get("anchor_default")))
    splits = None if a.splits == "all" else set(a.splits.split(","))
    ids = set(a.ids.split(",")) if a.ids else None
    ents = set(a.franchises.split(",")) if a.franchises else None
    sandbox = None if a.dax_only else Sandbox("root" if a.sql_user == "root" else "franchise", a.shim)
    pbi = None if a.sql_only else PowerBI(min(a.dax_rpm, 25))
    rt = runtime_tokens()
    PRIVATE.mkdir(exist_ok=True)
    stamp = anchor.isoformat() + (f"_{a.tag}" if a.tag else "")
    probe_ents = sorted(ents or set(FRANCHISES))

    probes_before = [] if a.no_probes else run_probes(gold, probe_ents, sandbox, pbi)
    if a.probes_only:
        for p in probes_before:
            print(p["probe"], p["franchise"], "match" if p.get("match") else "MISMATCH" if "match" in p else "")
        (PRIVATE / f"probes_{stamp}.json").write_text(json.dumps(probes_before, indent=1, default=str), encoding="utf-8")
        return

    out_path = PRIVATE / f"calibration_{stamp}.jsonl"
    ref_path = PRIVATE / f"refs_{stamp}.jsonl"
    results: list[dict] = []
    params_cache: dict = {}
    parent_results: dict[tuple[str, str], dict] = {}
    by_id = {t["id"]: t for t in gold["templates"]}
    with open(out_path, "w", encoding="utf-8") as fo, open(ref_path, "w", encoding="utf-8") as fr:
        for tpl, ent in cases(gold, splits, ids, ents):
            base = franchise_tokens(gold, ent, rt)
            parent = by_id.get(tpl.get("paraphrase_of") or "")
            specs = specs_for(tpl)
            if not specs:
                rec = {"case_id": f"{tpl['id']}@{ent}", "template_id": tpl["id"], "split": tpl["split"], "franchise": ent,
                       "anchor": anchor.isoformat(), "types": tpl.get("types") or [], "status": "rubric", "diffs": []}
                try:
                    tokens, params = resolve({**tpl}, ent, anchor, base, sandbox, params_cache)
                    rec["params"] = params
                    rec["question"] = fill(tpl.get("question", ""), tokens, params, "question")
                except Exception as e:  # noqa: BLE001
                    rec.update({"status": "error", "diffs": [f"resolve: {e}"]})
                results.append(rec)
                fo.write(json.dumps(rec, default=str) + "\n")
                continue
            for suffix, spec in specs:
                case_id = f"{tpl['id']}{suffix}@{ent}"
                rec = {"case_id": case_id, "template_id": tpl["id"], "split": tpl["split"], "franchise": ent,
                       "anchor": anchor.isoformat(), "types": tpl.get("types") or []}
                # robustness copies reuse the parent's result when their references are identical
                if parent is not None and not suffix and all(parent.get(k) == tpl.get(k) for k in REF_FIELDS):
                    pr = parent_results.get((parent["id"], ent))
                    if pr:
                        rec.update({k: pr[k] for k in ("sql", "dax", "status", "diffs", "params", "period")})
                        rec["reused_from"] = parent["id"]
                        results.append(rec)
                        fo.write(json.dumps(rec, default=str) + "\n")
                        continue
                try:
                    tokens, params = resolve({**spec, "id": tpl["id"]}, ent, anchor, base, sandbox, params_cache)
                    rec["params"] = params
                    rec["period"] = tokens.get("label") or tokens.get("period")
                    if tpl.get("question"):
                        rec["question"] = fill(tpl["question"], tokens, params, "question")
                    if tpl.get("paraphrases"):
                        rec["paraphrases"] = [fill(p, tokens, params, "paraphrase") for p in tpl["paraphrases"]]
                    sql_text = fill(spec["reference_sql"], tokens, params, "reference_sql")
                    dax_text = fill(spec["reference_dax"], tokens, params, "reference_dax")
                except Exception as e:  # noqa: BLE001
                    rec.update({"status": "error", "diffs": [f"resolve: {e}"], "sql": {}, "dax": {}})
                    results.append(rec)
                    fo.write(json.dumps(rec, default=str) + "\n")
                    continue
                sres = sandbox.run(ent, sql_text) if sandbox else {"error": "skipped"}
                dres = pbi.run(ent, dax_text) if pbi else {"error": "skipped"}
                if sandbox and pbi:
                    status, diffs = compare(spec, sres, dres)
                    if "expect" in spec and status == "agree":
                        got = (sres.get("rows") or [[None]])[0][0]
                        if as_num(got) != as_num(spec["expect"]):
                            status, diffs = "disagree", [f"expected {spec['expect']}, both routes returned {got}"]
                else:
                    one = sres if sandbox else dres
                    status, diffs = ("ran" if not one.get("error") else "error"), [one.get("error") or ""]
                kd = (tpl.get("known_difference") or {})
                if status in ("disagree", "near") and (ent in kd or "all" in kd) and not suffix:
                    status = "documented"
                    diffs = [f"known: {kd.get(ent) or kd.get('all')}"] + diffs
                rec.update({"status": status, "diffs": [d for d in diffs if d],
                            "sql": {k: sres.get(k) for k in ("columns", "rows", "error", "ms")},
                            "dax": {k: dres.get(k) for k in ("columns", "rows", "error", "ms")}})
                if a.show:
                    def brief(res):
                        if not res:
                            return None
                        if res.get("error"):
                            return res["error"][:300]
                        return [[str(c)[:28] for c in r] for r in (res.get("rows") or [])[:4]] + \
                            ([f"... {len(res['rows'])} rows"] if len(res.get("rows") or []) > 4 else [])
                    print(case_id, status, [d[:160] for d in rec["diffs"][:4]])
                    print("   sql:", brief(sres))
                    print("   dax:", brief(dres))
                if not suffix:
                    parent_results[(tpl["id"], ent)] = rec
                results.append(rec)
                fo.write(json.dumps(rec, default=str) + "\n")
                for src, res in (("sql", sres), ("dax", dres)):
                    if res and not str(res.get("error", "")).startswith("skipped"):
                        fr.write(json.dumps({"case_id": case_id, "source": src, "columns": spec.get("result_columns"),
                                             "rows": res.get("rows"), "error": res.get("error")}, default=str) + "\n")
                fo.flush()
                fr.flush()
                if not a.show:
                    sys.stdout.write({"agree": ".", "near": "~", "disagree": "X", "error": "E",
                                      "documented": "d", "ran": "r", "rubric": "-"}.get(status, "?"))
                    sys.stdout.flush()
    print()
    probes_after = [] if a.no_probes else run_probes(gold, probe_ents, sandbox, pbi)
    summary = summarize(gold, results, probes_before, probes_after, anchor, pbi.n if pbi else 0)
    (PRIVATE / f"calibration_{stamp}_summary.md").write_text(summary, encoding="utf-8")
    print(summary)
    print(f"\nwrote {out_path.relative_to(ROOT)}, {ref_path.relative_to(ROOT)} and the summary (gitignored)")


def summarize(gold, results, probes_before, probes_after, anchor, dax_calls) -> str:
    st = Counter(r["status"] for r in results)
    lines = [f"# Calibration {anchor.isoformat()}", "",
             f"Cases run: {len(results)}  |  " + "  ".join(f"{k}: {v}" for k, v in sorted(st.items())),
             f"Power BI executeQueries calls: {dax_calls}", ""]
    for name, probes in (("before", probes_before), ("after", probes_after)):
        if probes:
            bad = [f"{p['probe']}@{p['franchise']}" for p in probes if p.get("match") is False]
            lines.append(f"Checksum probes {name}: {len(probes)} run, mismatches: {bad or 'none'}")
    lines += ["", "## Per template", "", "| Template | Split | agree | near | disagree | error | documented | Notes |",
              "|---|---|---|---|---|---|---|---|"]
    per: dict[str, Counter] = defaultdict(Counter)
    notes: dict[str, list[str]] = defaultdict(list)
    split_of = {}
    rubric_only = Counter(r["split"] for r in results if r["status"] == "rubric")
    for r in results:
        if r["status"] == "rubric":
            continue
        tid = r["template_id"]
        split_of[tid] = r["split"]
        per[tid][r["status"]] += 1
        if r["status"] not in ("agree", "ran"):
            notes[tid].append(f"{r['case_id'].split('@')[1]}{'/' + r['case_id'].split('@')[0].split('#')[1] if '#' in r['case_id'] else ''}: "
                              + "; ".join(r.get("diffs") or [])[:160].replace("|", "/"))
    for tid in per:
        c = per[tid]
        lines.append(f"| {tid} | {split_of[tid]} | {c['agree']} | {c['near']} | {c['disagree']} | {c['error']} | "
                     f"{c['documented']} | {' ; '.join(notes[tid])[:300].replace(chr(10), ' ')} |")
    tstat: dict[str, str] = {}
    for tid, c in per.items():
        if c["error"]:
            tstat[tid] = "error"
        elif c["disagree"]:
            tstat[tid] = "disagree"
        elif c["near"]:
            tstat[tid] = "near"
        elif c["documented"]:
            tstat[tid] = "documented"
        else:
            tstat[tid] = "agree"
    lines += ["", "## Templates (all cases of a template must agree)", ""]
    if rubric_only:
        lines.append("Rubric-only cases (no reference to calibrate; question resolved for the runner): "
                     + ", ".join(f"{k} {v}" for k, v in sorted(rubric_only.items())))
    for s in sorted(set(split_of.values())):
        ts = [t for t in tstat if split_of[t] == s]
        cnt = Counter(tstat[t] for t in ts)
        ok = cnt["agree"] + cnt["documented"]
        lines.append(f"- {s}: {len(ts)} templates; agree {cnt['agree']}, documented {cnt['documented']}, near {cnt['near']}, "
                     f"disagree {cnt['disagree']}, error {cnt['error']} -> {100 * ok / max(1, len(ts)):.1f}% agree or documented")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
