"""Public-repo guard: fail if any real person name, email or phone number from
the franchise data appears in files about to be committed.

Builds the set of full names (2-3 words) from the name columns of the sandbox
views (designers, staff, customers, lead creators) and scans the given files
(default: everything `git ls-files` + staged files) word-pair by word-pair.
Hits are printed masked (initials + length) so the check itself leaks nothing.

usage: python harness/pii_lint.py [files...]      exit code 1 on any hit
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import mysql.connector

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
NAME_COLS = {
    "salesforce_data": ["Designer", "Consumer Name: Full Name"],
    "leads": ["Lead Creator Name", "Converted Customer Name", "Customer Full Name"],
    "appointments": ["Lead Name", "Assigned Staff Member", "Created By User"],
    "opportunities to appointments": ["Converted Contact: Full Name", "Assigned"],
    "dim_designers": ["Designer"],
}
FIRST_LAST = [("leads", "Customer First Name", "Customer Last Name")]
EMAIL_COLS = [("leads", "Customer Email")]
PHONE_COLS = [("leads", "Customer Phone")]
SCHEMAS = ["poc_icla", "poc_icch", "poc_icau"]
WORD = re.compile(r"[A-Za-z][A-Za-z'.-]+")
# Words that also appear in names but are ordinary vocabulary in this repo
STOP = {"lead", "leads", "sold", "lost", "dead", "project", "projects", "designer", "designers", "source",
        "date", "created", "name", "total", "amount", "the", "and", "of", "to", "in", "for", "by", "x", "unassigned",
        # placeholder values seen in the data ("First Last", "Test Customer", ...)
        "first", "last", "test", "customer", "unknown", "full", "new", "none", "null", "n/a",
        # lead-status words some records carry as a name ("Not Junk", "Duplicate Lead", ...)
        "not", "junk", "duplicate", "cancelled", "canceled", "spam", "use", "no", "yes"}


def env(key: str) -> str:
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip()
    raise KeyError(key)


def norm(words: list[str]) -> str:
    return " ".join(w.lower().strip(".'-") for w in words)


def load_sensitive() -> tuple[set[str], set[str], set[str]]:
    cn = mysql.connector.connect(host="127.0.0.1", port=3307, user="root", password=env("SANDBOX_ROOT_PASSWORD"),
                                 charset="utf8mb4", use_pure=True)
    cur = cn.cursor()
    names, emails, phones = set(), set(), set()

    def add_name(v):
        ws = WORD.findall(str(v or ""))
        ws = [w for w in ws if w.lower() not in STOP]
        if 2 <= len(ws) <= 4 and len("".join(ws)) >= 6:
            names.add(norm(ws[:2]))
            names.add(norm(ws[-2:]))

    for s in SCHEMAS:
        cur.execute(f"SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA='{s}'")
        have = {r[0].lower() for r in cur.fetchall()}
        for t, cols in NAME_COLS.items():
            if t not in have:
                continue
            for c in cols:
                cur.execute(f"SELECT DISTINCT `{c}` FROM `{s}`.`{t}` WHERE `{c}` IS NOT NULL")
                for (v,) in cur.fetchall():
                    add_name(v)
        for t, f, l in FIRST_LAST:
            cur.execute(f"SELECT DISTINCT `{f}`, `{l}` FROM `{s}`.`{t}`")
            for a, b in cur.fetchall():
                add_name(f"{a or ''} {b or ''}")
        for t, c in EMAIL_COLS:
            cur.execute(f"SELECT DISTINCT LOWER(`{c}`) FROM `{s}`.`{t}` WHERE `{c}` LIKE '%@%'")
            emails.update(v for (v,) in cur.fetchall() if v)
        for t, c in PHONE_COLS:
            cur.execute(f"SELECT DISTINCT `{c}` FROM `{s}`.`{t}` WHERE `{c}` IS NOT NULL")
            for (v,) in cur.fetchall():
                d = re.sub(r"\D", "", str(v))
                if len(d) >= 10:
                    phones.add(d[-10:])
    cn.close()
    return names, emails, phones


def files_to_scan(args: list[str]) -> list[Path]:
    if args:
        return [(ROOT / a).resolve() if not Path(a).is_absolute() else Path(a) for a in args]
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "--cached", "--others", "--exclude-standard"],
                         capture_output=True, text=True, check=True).stdout.split("\n")
    return [ROOT / p for p in out if p.strip()]


def mask(s: str) -> str:
    return " ".join(w[0].upper() + "*" * (len(w) - 1) for w in s.split())


def main() -> int:
    names, emails, phones = load_sensitive()
    hits = 0
    for f in files_to_scan(sys.argv[1:]):
        if not f.is_file() or f.suffix.lower() in (".png", ".jpg", ".gz", ".zip", ".pdf"):
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for ln, line in enumerate(text.splitlines(), 1):
            ws = [w for w in WORD.findall(line) if w.lower() not in STOP]
            pairs = {norm(ws[i:i + 2]) for i in range(len(ws) - 1)}
            for p in pairs & names:
                print(f"NAME   {f.relative_to(ROOT)}:{ln}  {mask(p)}")
                hits += 1
            for e in re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", line):
                if e.lower() in emails:
                    print(f"EMAIL  {f.relative_to(ROOT)}:{ln}  {e[0]}***@***")
                    hits += 1
            for d in re.findall(r"\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}", line):
                dd = re.sub(r"\D", "", d)[-10:]
                if dd in phones:
                    print(f"PHONE  {f.relative_to(ROOT)}:{ln}  ***{dd[-2:]}")
                    hits += 1
    print(f"pii_lint: {len(names)} names, {len(emails)} emails, {len(phones)} phones checked; {hits} hit(s)")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
