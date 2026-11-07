"""Summarise PBI TMDL tables (columns, calc exprs, partitions) for ICLA/ICCH/ICAU."""
import json, re, sys
from pathlib import Path

BASE = Path(r"C:\Project Data Centralization\Power BI Report\per_entity")
ENTS = ["ICLA", "ICCH", "ICAU"]
SKIP = {"_measures.tmdl", "_measures_L&A.tmdl"}


def unq(name: str) -> str:
    name = name.strip()
    if name.startswith("'") and name.endswith("'"):
        return name[1:-1].replace("''", "'")
    return name


def parse(path: Path):
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    tbl = {"columns": [], "partitions": [], "measures": [], "table_desc": None}
    i = 0
    pending_desc = []
    cur = None
    while i < len(lines):
        ln = lines[i]
        s = ln.strip()
        indent = len(ln) - len(ln.lstrip("\t"))
        if s.startswith("///"):
            pending_desc.append(s[3:].strip())
            i += 1
            continue
        if indent == 0 and s.startswith("table "):
            tbl["name"] = unq(s[6:])
            tbl["table_desc"] = " ".join(pending_desc) or None
            pending_desc = []
        elif indent == 1 and s.startswith("column "):
            rest = s[7:]
            expr = None
            if "=" in rest and not rest.startswith("'") or (rest.startswith("'") and "' =" in rest):
                if rest.startswith("'"):
                    nm, expr = rest.split("' =", 1)
                    nm = nm + "'"
                else:
                    nm, expr = rest.split("=", 1)
                expr = expr.strip()
                # multi-line expr
                j = i + 1
                if expr == "":
                    buf = []
                    while j < len(lines) and (len(lines[j]) - len(lines[j].lstrip("\t"))) >= 3 or (j < len(lines) and lines[j].strip() == ""):
                        if lines[j].strip() == "" and j + 1 < len(lines) and (len(lines[j+1]) - len(lines[j+1].lstrip("\t"))) < 3:
                            break
                        buf.append(lines[j].strip())
                        j += 1
                    expr = " ".join(b for b in buf if b)
                    i = j - 1
            else:
                nm = rest
            cur = {"name": unq(nm), "expr": expr, "desc": " ".join(pending_desc) or None}
            pending_desc = []
            tbl["columns"].append(cur)
        elif indent == 1 and s.startswith("measure "):
            m = re.match(r"measure ('(?:[^']|'')*'|\S+)", s)
            tbl["measures"].append(unq(m.group(1)))
            cur = None
            pending_desc = []
        elif indent == 1 and s.startswith("partition "):
            m = re.match(r"partition (.+?) = (\w+)", s)
            j = i + 1
            buf = []
            while j < len(lines) and (lines[j].strip() == "" or (len(lines[j]) - len(lines[j].lstrip("\t"))) >= 2):
                buf.append(lines[j].rstrip())
                j += 1
            tbl["partitions"].append({"name": unq(m.group(1)), "kind": m.group(2), "body": "\n".join(buf).strip()})
            i = j - 1
            cur = None
            pending_desc = []
        elif indent == 1 and s.startswith("hierarchy "):
            cur = None
            pending_desc = []
        elif indent == 2 and cur is not None and ":" in s and not s.startswith("annotation"):
            k, v = s.split(":", 1)
            cur[k.strip()] = v.strip()
        elif indent == 2 and cur is not None and s == "isHidden":
            cur["isHidden"] = True
        elif indent == 2 and cur is not None and s.startswith("annotation"):
            pass
        else:
            if indent <= 1:
                pending_desc = [] if not s.startswith("///") else pending_desc
        i += 1
    return tbl


out = {}
for e in ENTS:
    d = BASE / e / f"Salesforce BI {e}.SemanticModel" / "definition" / "tables"
    out[e] = {}
    for f in sorted(d.glob("*.tmdl")):
        if f.name in SKIP:
            continue
        t = parse(f)
        out[e][t["name"]] = t

Path(sys.argv[1]).write_text(json.dumps(out, indent=1), encoding="utf-8")

# print compact summary for ICLA + diffs
ref = out["ICLA"]
for tname, t in ref.items():
    print(f"\n=== {tname}  (desc={t['table_desc']!r}) measures={len(t['measures'])}")
    for c in t["columns"]:
        flags = []
        if c.get("isHidden"): flags.append("hidden")
        if c.get("expr"): flags.append("CALC=" + c["expr"][:160])
        if c.get("sourceColumn") and c["sourceColumn"] != c["name"]: flags.append("src=" + c["sourceColumn"])
        if c.get("desc"): flags.append("DESC=" + c["desc"][:200])
        print(f"  - {c['name']} :: {c.get('dataType')} {c.get('formatString','')} {' | '.join(flags)}")
    for p in t["partitions"]:
        print(f"  [partition {p['kind']}]\n" + "\n".join("     " + x for x in p["body"].splitlines()))
for e in ["ICCH", "ICAU"]:
    print(f"\n##### diffs {e} vs ICLA")
    for tname in sorted(set(out[e]) | set(ref)):
        if tname not in out[e]:
            print("  missing in", e, tname); continue
        if tname not in ref:
            print("  extra in", e, tname); continue
        a = [(c["name"], c.get("dataType"), c.get("expr"), c.get("sourceColumn")) for c in ref[tname]["columns"]]
        b = [(c["name"], c.get("dataType"), c.get("expr"), c.get("sourceColumn")) for c in out[e][tname]["columns"]]
        if a != b:
            print(f"  COLUMNS differ in {tname}: only ICLA={set(a)-set(b)} only {e}={set(b)-set(a)}")
        pa = [p["body"].replace("ICLA", "<E>") for p in ref[tname]["partitions"]]
        pb = [p["body"].replace(e, "<E>") for p in out[e][tname]["partitions"]]
        if pa != pb:
            print(f"  PARTITION differs in {tname}")
