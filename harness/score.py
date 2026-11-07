"""Layer-1 scorer: deterministic number checks against reference results.

Inputs
  --runs   bot run log(s), JSONL, one line per turn. Needs: case_id, arm, run (int),
           answer (full text incl. ```viz block), and optionally error / queries.
  --refs   reference results, JSONL: {case_id, source: "sql"|"dax", columns, rows, error?}
           Arm A/A0 is scored against source "dax", arm B/B+ against source "sql"
           (same-source scoring, see PREREGISTRATION.md "Data protocol").
  --gold   gold/templates.yaml (answer_shape, result_columns, col_types, headline,
           label_column, value_columns, top_k, order_by, rubric)
  --out    scored JSONL, one line per turn: verdict pass | fail | undecidable,
           failure_class, loud, detail

Verdicts are provisional. `undecidable` goes to the judge (layer 2); every
discordant pair goes to a person (layer 3). Final verdicts live in the
adjudication file that stats.py reads.
"""
from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

# ----------------------------------------------------------------------------- text cleanup
LOG_MARKER = re.compile(r"\[[A-Z]+_LOG_R\d+:[^\n]*\]")
VIZ_BLOCK = re.compile(r"```viz\s*\n(.*?)```", re.S)
LOOKUP = re.compile(r"\[Looking up live data…\]|\[CONTINUE_AVAILABLE\]")
ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ][\d:.]+Z?)?\b")
YEAR_MONTH = re.compile(r"\b\d{4}-\d{2}\b")
REFUSAL = re.compile(r"\b(i (?:can(?:no|')t|am unable|don't have|do not have)|not (?:available|possible)|"
                     r"unable to|no data (?:is )?available|outside (?:of )?(?:my|the) scope)\b", re.I)
LOUD = re.compile(r"\b(error|timed out|timeout|something went wrong|try again)\b", re.I)


def strip_markers(text: str) -> str:
    text = LOG_MARKER.sub(" ", text or "")
    text = VIZ_BLOCK.sub(" ", text)
    return LOOKUP.sub(" ", text)


def viz_blocks(text: str) -> list[dict]:
    out = []
    for m in VIZ_BLOCK.finditer(text or ""):
        try:
            out.append(json.loads(m.group(1)))
        except json.JSONDecodeError:
            pass
    return out


# ----------------------------------------------------------------------------- numbers
NUM = re.compile(r"(?<![\w.])(-)?(\$)?\s?(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s?([KkMmBb](?![a-z]))?\s?(%)?")


class Num:
    __slots__ = ("value", "decimals", "mult", "pct", "cur", "bold")

    def __init__(self, value, decimals, mult, pct, cur, bold):
        self.value, self.decimals, self.mult, self.pct, self.cur, self.bold = value, decimals, mult, pct, cur, bold

    def display_tol(self) -> float:
        """Half a unit of the last displayed digit, in absolute terms."""
        return 0.5 * (10 ** -self.decimals) * self.mult

    def __repr__(self):
        return f"Num({self.value}{'%' if self.pct else ''}{' bold' if self.bold else ''})"


def extract_numbers(text: str) -> list[Num]:
    clean = ISO_DATE.sub(" ", strip_markers(text))
    clean = YEAR_MONTH.sub(" ", clean)
    bold_spans = [(m.start(), m.end()) for m in re.finditer(r"\*\*(.+?)\*\*", clean, re.S)]
    out: list[Num] = []
    for m in NUM.finditer(clean):
        neg, cur, intpart, frac, suf, pct = m.groups()
        v = float(intpart.replace(",", "") + (frac or ""))
        decimals = len(frac) - 1 if frac else 0
        mult = {"k": 1e3, "m": 1e6, "b": 1e9}.get((suf or "").lower(), 1.0)
        v *= mult
        if neg:
            v = -v
        bold = any(a <= m.start() < b for a, b in bold_spans)
        out.append(Num(v, decimals, mult, bool(pct), bool(cur), bold))
    return out


def within(ref: float, cand: Num | float, col_type: str) -> bool:
    """Type tolerance OR displayed-rounding tolerance (an answer may round for display)."""
    if ref is None:
        return False
    c = cand.value if isinstance(cand, Num) else float(cand)
    disp = cand.display_tol() if isinstance(cand, Num) else 0.0
    diff = abs(c - ref)
    if col_type == "count":
        tol = 0.0
    elif col_type == "percent":
        tol = 0.1
    elif col_type in ("currency", "number"):
        tol = 0.005 * abs(ref)
    else:
        tol = 0.0
    return diff <= max(tol, disp) + 1e-9


def to_float(x) -> float | None:
    if x is None or x == "":
        return 0.0  # production rule: blank = 0
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).replace(",", "").replace("$", "").replace("%", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def norm_label(x) -> str:
    return re.sub(r"\s+", " ", str(x if x is not None else "")).strip().lower()


# ----------------------------------------------------------------------------- table views of an answer
def answer_tables(answer: str) -> list[tuple[list[str], list[list]]]:
    """viz blocks first (exact values the owner sees as a table), then markdown tables."""
    tables = []
    for v in viz_blocks(answer):
        cols, data = v.get("columns") or [], v.get("data") or []
        names = [c.get("name") if isinstance(c, dict) else str(c) for c in cols]
        rows = [[r.get(n) for n in names] if isinstance(r, dict) else list(r) for r in data]
        tables.append((names, rows))
    body = strip_markers(answer)
    for block in re.findall(r"((?:^\|.*\|\s*$\n?)+)", body, re.M):
        lines = [l.strip() for l in block.strip().splitlines()]
        if len(lines) < 3:
            continue
        hdr = [h.strip() for h in lines[0].strip("|").split("|")]
        rows = [[c.strip() for c in l.strip("|").split("|")] for l in lines[2:]]
        tables.append((hdr, rows))
    return tables


def bare(col: str) -> str:
    """'Salesforce_Data[Designer]' / '[Sold Amount]' / 'designer' -> 'designer'"""
    m = re.search(r"\[([^\]]+)\]\s*$", col or "")
    return norm_label(m.group(1) if m else col)


def pick_col(names: list[str], wanted: str, fallback_idx: int | None) -> int | None:
    w = bare(wanted)
    for i, n in enumerate(names):
        if bare(n) == w:
            return i
    return fallback_idx if fallback_idx is not None and fallback_idx < len(names) else None


# ----------------------------------------------------------------------------- shape scorers
def ref_index(tpl: dict, ref: dict) -> dict[str, int]:
    cols = tpl.get("result_columns") or ref.get("columns") or []
    return {c: i for i, c in enumerate(cols)}


def score_number(tpl, ref, answer, raw_values) -> tuple[str, str, str]:
    idx = ref_index(tpl, ref)
    rows = ref.get("rows") or []
    if not rows:
        return "undecidable", "", "reference returned no rows"
    nums = extract_numbers(answer)
    bold = [n for n in nums if n.bold]
    pool = bold or nums
    missing, narration = [], []
    for col in tpl.get("headline") or list(idx)[:1]:
        v = to_float(rows[0][idx[col]]) if col in idx else None
        t = (tpl.get("col_types") or {}).get(col, "number")
        if v is None:
            return "undecidable", "", f"reference value for {col} not numeric"
        if any(within(v, n, t) for n in pool) or (bold and any(within(v, n, t) for n in nums)):
            continue
        if any(within(v, float(x), t) for x in raw_values):
            narration.append(col)
        missing.append(f"{col}={v}")
    if not missing:
        return "pass", "", ""
    if narration and len(narration) == len(missing):
        return "fail", "narration_mismatch", f"right value in query result, not in prose: {missing}"
    if not nums:
        if REFUSAL.search(strip_markers(answer)):
            return "fail", "wrong_refusal", "answer declined"
        return "undecidable", "", "no numbers in answer"
    return "fail", "value_mismatch", f"missing {missing}; answer had {[n.value for n in pool][:8]}"


def ranked_reference(tpl, ref) -> list[list]:
    idx = ref_index(tpl, ref)
    rows = list(ref.get("rows") or [])
    ob = tpl.get("order_by")
    col = ob.get("column") if isinstance(ob, dict) else ob
    if col in idx:
        rows.sort(key=lambda r: -(to_float(r[idx[col]]) or 0.0))
    return rows


def score_list_like(tpl, ref, answer, shape) -> tuple[str, str, str]:
    idx = ref_index(tpl, ref)
    lab = tpl.get("label_column")
    vals = tpl.get("value_columns") or []
    types = tpl.get("col_types") or {}
    if lab not in idx:
        return "undecidable", "", "template has no label_column"
    ref_rows = ranked_reference(tpl, ref) if shape == "ranked_list" else list(ref.get("rows") or [])
    if shape == "ranked_list":
        ref_rows = ref_rows[: int(tpl.get("top_k") or 5)]
    if not ref_rows:
        return "undecidable", "", "reference returned no rows"
    tables = answer_tables(answer)
    best = None
    for names, rows in tables:
        li = pick_col(names, lab, 0)
        if li is None:
            continue
        got = {norm_label(r[li]): r for r in rows if li < len(r)}
        order = [norm_label(r[li]) for r in rows if li < len(r)]
        ok_labels, ok_vals, problems = 0, 0, []
        for pos, rr in enumerate(ref_rows):
            key = norm_label(rr[idx[lab]])
            if key not in got:
                problems.append(f"missing {key!r}")
                continue
            ok_labels += 1
            row_ok = True
            for vc in vals:
                vi = pick_col(names, vc, None)
                if vi is None or vi >= len(got[key]):
                    continue
                rv, cv = to_float(rr[idx[vc]]), to_float(got[key][vi])
                if rv is None or cv is None or not within(rv, cv, types.get(vc, "number")):
                    row_ok = False
                    problems.append(f"{key!r} {vc}: ref {rv} vs {cv}")
            ok_vals += row_ok
        if shape == "ranked_list" and ok_labels == len(ref_rows):
            ref_order = [norm_label(r[idx[lab]]) for r in ref_rows]
            seen = [k for k in order if k in ref_order]
            # the order the owner READS: first mention of each label in the prose (a DAX TOPN
            # returns its rows unsorted, so the attached table can be in any order)
            prose = strip_markers(answer).lower()
            pos = {k: prose.find(k) for k in ref_order}
            prose_seen = sorted((k for k in ref_order if pos[k] >= 0), key=lambda k: pos[k])
            ok_order = any(s[: len(ref_order)] == ref_order or ties_explain(tpl, ref_rows, idx, s)
                           for s in (seen, prose_seen) if len(s) >= len(ref_order))
            if not ok_order:
                problems.append("order differs")
        score = (ok_labels == len(ref_rows) and ok_vals == len(ref_rows) and not problems, problems)
        if best is None or (score[0] and not best[0]) or len(score[1]) < len(best[1]):
            best = score
    if best is None:
        if REFUSAL.search(strip_markers(answer)):
            return "fail", "wrong_refusal", "answer declined"
        return "undecidable", "", "no table in answer; list may be in prose"
    if best[0]:
        return "pass", "", ""
    return "fail", "value_mismatch", "; ".join(best[1][:6])


def ties_explain(tpl, ref_rows, idx, seen) -> bool:
    ob = tpl.get("order_by")
    col = ob.get("column") if isinstance(ob, dict) else ob
    if col not in idx:
        return False
    val = {norm_label(r[idx[tpl["label_column"]]]): to_float(r[idx[col]]) for r in ref_rows}
    want = sorted(val, key=lambda k: -(val[k] or 0))
    return [val[k] for k in seen[: len(want)]] == [val[k] for k in want]


# ----------------------------------------------------------------------------- driver
def raw_result_values(turn: dict) -> list[float]:
    vals = []
    for q in turn.get("queries") or []:
        rows = q.get("rows") if q.get("rows") is not None else (q.get("result") or {}).get("rows")
        for row in rows or []:
            cells = row.values() if isinstance(row, dict) else row
            for c in cells:
                f = to_float(c) if not isinstance(c, str) or re.match(r"^-?[\d,.$%]+$", c or "") else None
                if f is not None:
                    vals.append(f)
    return vals


ALT_OVERRIDES = ("result_columns", "col_types", "headline", "label_column", "value_columns", "top_k",
                 "order_by", "answer_shape")


def score_with_alternates(tpl: dict, ref: dict | None, turn: dict, alts: list[tuple[int, dict]]) -> dict:
    """Primary reading first; if it fails, any listed alternate reading that passes makes it a pass."""
    first = score_turn(tpl, ref, turn)
    if first["verdict"] == "pass" or not alts:
        return first
    for n, aref in alts:
        spec = (tpl.get("alternates") or [])[n - 1] if n - 1 < len(tpl.get("alternates") or []) else {}
        atpl = {**tpl, **{k: spec[k] for k in ALT_OVERRIDES if k in spec}}
        s = score_turn(atpl, aref, turn)
        if s["verdict"] == "pass":
            return {**s, "detail": f"alternate reading {n}: {spec.get('label', '')}"[:200]}
    return first


def score_turn(tpl: dict, ref: dict | None, turn: dict) -> dict:
    answer = turn.get("answer") or ""
    base = {k: turn.get(k) for k in ("case_id", "template_id", "arm", "run", "franchise", "conv", "turn")}
    errs = turn.get("error") or "; ".join(turn.get("errors") or [])
    visible = turn.get("answer_visible", strip_markers(answer))
    if not str(visible).strip():
        cls = "timeout" if "timeout" in str(errs).lower() or "stall" in str(errs).lower() else "execution_error"
        return {**base, "verdict": "fail", "failure_class": cls, "loud": True, "detail": str(errs)[:200]}
    shape = tpl.get("answer_shape", "number")
    if shape == "rubric":
        return {**base, "verdict": "undecidable", "failure_class": "", "loud": False, "detail": "rubric case: judge"}
    if ref is None or ref.get("error"):
        return {**base, "verdict": "undecidable", "failure_class": "", "loud": False,
                "detail": f"no reference result ({(ref or {}).get('error', 'missing')})"}
    if shape == "number":
        v, cls, det = score_number(tpl, ref, answer, raw_result_values(turn))
    else:
        v, cls, det = score_list_like(tpl, ref, answer, shape)
    loud = v == "fail" and (cls in ("execution_error", "timeout") or bool(LOUD.search(strip_markers(answer))))
    return {**base, "verdict": v, "failure_class": cls, "loud": loud, "detail": det}


def load_jsonl(paths: list[str]) -> list[dict]:
    rows = []
    for p in paths:
        for line in Path(p).read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", nargs="+", required=True)
    ap.add_argument("--refs", nargs="+", required=True)
    ap.add_argument("--gold", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    gold = yaml.safe_load(Path(a.gold).read_text(encoding="utf-8"))
    templates = {t["id"]: t for t in (gold.get("templates") if isinstance(gold, dict) else gold)}
    refs, alts = {}, {}
    for r in load_jsonl(a.refs):
        m = re.match(r"^(.*?)#alt(\d+)(@.*)$", r["case_id"])
        if m:   # "T001#alt2@ICAU" -> alternate 2 of case "T001@ICAU"
            alts.setdefault((m.group(1) + m.group(3), r["source"]), []).append((int(m.group(2)), r))
        else:
            refs[(r["case_id"], r["source"])] = r
    out, counts = [], {}
    for turn in load_jsonl(a.runs):
        tid = turn.get("template_id") or str(turn.get("case_id", "")).split("@")[0]
        tpl = templates.get(tid)
        if tpl is None:
            continue
        src = "dax" if str(turn.get("arm", "")).upper().startswith("A") else "sql"
        key = (turn.get("case_id"), src)
        s = score_with_alternates(tpl, refs.get(key), turn, sorted(alts.get(key, []), key=lambda x: x[0]))
        s["template_id"] = tid
        out.append(s)
        counts[(s["arm"], s["verdict"])] = counts.get((s["arm"], s["verdict"]), 0) + 1
    Path(a.out).write_text("".join(json.dumps(r) + "\n" for r in out), encoding="utf-8")
    for (arm, v), n in sorted(counts.items()):
        print(f"{arm:4} {v:12} {n}")


if __name__ == "__main__":
    main()
