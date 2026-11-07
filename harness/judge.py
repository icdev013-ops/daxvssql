"""Layer-2 judge: grades the turns the deterministic scorer could not decide, and rubric cases.

The judge is a pinned model from a different vendor than the chatbot (Google vs OpenAI) and sees
one answer at a time with the route hidden: query logs, chart JSON and any DAX/SQL wording are
stripped, and the answer's own table is rendered as plain text. It gets the question, the
reference result table and the case's scoring rule, and may say "unknown".

usage:
  python harness/judge.py judge --scored runs/<tag>_scored.jsonl --runs runs/<tag>_runs_kept.jsonl \
         --refs gold/private/refs_<anchor>_<tag>.jsonl --gold gold/templates.yaml --out runs/<tag>_judged.jsonl
  python harness/judge.py kappa --judged runs/<tag>_judged.jsonl --labels adjudication/<tag>_labels.csv
  python harness/judge.py merge --judged runs/<tag>_judged.jsonl [--labels ...] --out adjudication/<tag>_adj.csv

Calibrate before trusting it (PREREGISTRATION.md): Cohen's kappa >= 0.8 against hand labels,
with similar error rates on A and B answers.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

import requests
import yaml

sys.path.insert(0, str(Path(__file__).parent))
from score import VIZ_BLOCK, load_jsonl, strip_markers  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
JUDGE_MODEL = "google/gemini-3.1-pro-preview"
SAAS_ENV = ROOT.parent / "Inspired Closets SAAS Reporting" / ".env.local"
CLASSES = ["value_mismatch", "wrong_business_logic", "wrong_table_or_field", "wrong_date_window",
           "aggregation_or_fanout", "narration_mismatch", "presentation", "wrong_refusal",
           "false_answer_to_unanswerable", "wrong_franchise", "execution_error", "timeout", ""]

SYSTEM = """You grade one answer from a business-analytics chatbot for a closet-design franchise.
You get: the owner's question, the scoring rule, the CORRECT reference result (a table computed by a
trusted query), and the chatbot's answer. Decide whether the answer is correct.

Rules:
- Judge the substance, not the style. Rounded figures are fine when they round correctly
  (e.g. $3.80M for 3,804,118; 52% for 51.94%).
- For ranked lists, the requested top entries must match in order (ties may be in either order).
- For rubric cases, apply the listed pass criteria.
- For unanswerable questions, the answer passes only if it declines or clearly states it cannot
  answer; a confident number is a fail ("false_answer_to_unanswerable").
- If the answer reasonably interprets an ambiguous question and states its interpretation, and its
  numbers are right for that interpretation (an alternate reference may be given), it passes.
- If the reference itself looks inapplicable or you cannot tell, answer "unknown". Never guess.

Reply with JSON only: {"verdict": "pass" | "fail" | "unknown",
 "failure_class": one of __CLASSES__ (empty when pass),
 "rationale": "<one sentence>"}""".replace("__CLASSES__", json.dumps([c for c in CLASSES if c]))

NEUTRAL = [(re.compile(r"\b(DAX|SQL|MySQL|Power BI|semantic model|query_dax|query_sql)\b", re.I), "query")]


def key() -> str:
    for line in SAAS_ENV.read_text(encoding="utf-8").splitlines():
        if line.startswith("OPENROUTER_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"')
    sys.exit("OPENROUTER_API_KEY missing")


def render_answer(answer: str) -> str:
    tables = []
    for m in VIZ_BLOCK.finditer(answer or ""):
        try:
            v = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        cols = [c.get("name") if isinstance(c, dict) else str(c) for c in v.get("columns") or []]
        cols = [re.sub(r"^.*\[([^\]]+)\]$", r"\1", c or "") for c in cols]
        rows = [[r.get(c0) for c0 in [x.get("name") if isinstance(x, dict) else x for x in v.get("columns") or []]]
                if isinstance(r, dict) else r for r in (v.get("data") or [])[:30]]
        tables.append("TABLE SHOWN WITH THE ANSWER:\n" + " | ".join(cols) + "\n" +
                      "\n".join(" | ".join("" if x is None else str(x) for x in r) for r in rows))
    text = strip_markers(answer)
    for rx, rep in NEUTRAL:
        text = rx.sub(rep, text)
    return text.strip() + ("\n\n" + "\n\n".join(tables) if tables else "")


def render_ref(ref: dict | None, alts: list[dict]) -> str:
    def one(r):
        if not r:
            return "(no reference result)"
        rows = (r.get("rows") or [])[:30]
        return " | ".join(r.get("columns") or []) + "\n" + "\n".join(" | ".join(str(x) for x in row) for row in rows)
    out = "REFERENCE RESULT:\n" + one(ref)
    for i, a in enumerate(alts, 1):
        lab = str(a.get("label") or f"alt{i}")
        kind = {"r": "READING", "f": "FACT", "e": "EVIDENCE"}.get(lab[:1], "ALTERNATE READING")
        out += f"\n\n{kind} {lab} (supporting reference):\n" + one(a)
    return out


def rule_text(tpl: dict) -> str:
    keep = {k: tpl.get(k) for k in ("answer_shape", "headline", "label_column", "value_columns", "top_k",
                                    "order_by", "col_types", "rubric", "date_basis", "period") if tpl.get(k)}
    return "SCORING RULE: " + json.dumps(keep, default=str)


def ask(sess: requests.Session, user: str) -> dict:
    body = {"model": JUDGE_MODEL, "temperature": 0, "max_tokens": 600,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]}
    for attempt in range(4):
        r = sess.post("https://openrouter.ai/api/v1/chat/completions", json=body, timeout=120)
        if r.status_code == 200:
            msg = r.json()["choices"][0]["message"]["content"] or ""
            m = re.search(r"\{.*\}", msg, re.S)
            try:
                out = json.loads(m.group(0)) if m else {}
            except json.JSONDecodeError:
                out = {}
            if out.get("verdict") in ("pass", "fail", "unknown"):
                out["served_model"] = r.json().get("model")
                return out
        time.sleep(2 * (attempt + 1))
    return {"verdict": "unknown", "failure_class": "", "rationale": "judge call failed"}


def cmd_judge(a) -> None:
    gold = yaml.safe_load(Path(a.gold).read_text(encoding="utf-8"))
    tpls = {t["id"]: t for t in (gold.get("templates") if isinstance(gold, dict) else gold)}
    turns = {(t["case_id"], t["arm"], str(t.get("run"))): t for t in load_jsonl([a.runs])}
    refs: dict[tuple[str, str], dict] = {}
    alts: dict[tuple[str, str], list] = {}
    for r in load_jsonl([a.refs]):
        base = re.sub(r"#[^@]+", "", r["case_id"])   # T#alt2@E / T#r1@E / T#f1@E / T#evidence@E -> T@E
        if "#" in r["case_id"]:          # alternate reading, ambiguity reading, fact or evidence reference
            r = {**r, "label": re.search(r"#([^@]+)", r["case_id"]).group(1)}
            alts.setdefault((base, r["source"]), []).append(r)
        else:
            refs[(base, r["source"])] = r
    todo = [s for s in load_jsonl([a.scored]) if s["verdict"] == "undecidable" or a.all]
    sess = requests.Session()
    sess.headers.update({"Authorization": f"Bearer {key()}", "X-Title": "daxvssql-judge"})
    out = []
    for i, s in enumerate(todo, 1):
        t = turns.get((s["case_id"], s["arm"], str(s.get("run"))))
        tpl = tpls.get(s["template_id"], {})
        if not t:
            continue
        src = "dax" if s["arm"].startswith("A") else "sql"
        q = t["question"] + (f"\n(In context this means: {tpl['resolved_question']})" if tpl.get("resolved_question") else "")
        user = (f"QUESTION: {q}\n\n{rule_text(tpl)}\n\n{render_ref(refs.get((s['case_id'], src)), alts.get((s['case_id'], src), []))}"
                f"\n\nCHATBOT ANSWER:\n{render_answer(t.get('answer') or '')}")
        j = ask(sess, user)
        out.append({**{k: s.get(k) for k in ("case_id", "template_id", "arm", "run", "franchise")},
                    "layer1": s["verdict"], "verdict": j["verdict"], "failure_class": j.get("failure_class", ""),
                    "rationale": j.get("rationale", ""), "judge_model": j.get("served_model", JUDGE_MODEL)})
        print(f"[{i}/{len(todo)}] {s['case_id']} {s['arm']} r{s.get('run')}: {j['verdict']}", flush=True)
    Path(a.out).write_text("".join(json.dumps(r) + "\n" for r in out), encoding="utf-8")
    print(Counter((r["arm"], r["verdict"]) for r in out))


def kappa(x: list[str], y: list[str]) -> float:
    n = len(x)
    if not n:
        return float("nan")
    cats = sorted(set(x) | set(y))
    po = sum(a == b for a, b in zip(x, y)) / n
    pe = sum((x.count(c) / n) * (y.count(c) / n) for c in cats)
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


def cmd_kappa(a) -> None:
    judged = {(r["case_id"], r["arm"], str(r["run"])): r for r in load_jsonl([a.judged])}
    pairs = []
    with open(a.labels, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            j = judged.get((row["case_id"], row["arm"], str(row["run"])))
            if j and row.get("label") in ("pass", "fail"):
                pairs.append((row["label"], j["verdict"], row["arm"]))
    x, y = [p[0] for p in pairs], [p[1] for p in pairs]
    k = kappa(x, y)
    print(f"pairs {len(pairs)}  agreement {sum(a == b for a, b in zip(x, y)) / max(len(x), 1):.1%}  kappa {k:.3f}"
          f"  -> {'OK (>= 0.8)' if k >= 0.8 else 'NOT calibrated (< 0.8)'}")
    for arm in sorted({p[2] for p in pairs}):
        sub = [p for p in pairs if p[2] == arm]
        err = sum(p[0] != p[1] for p in sub) / len(sub)
        print(f"  {arm}: {len(sub)} pairs, judge error rate {err:.1%}")


def cmd_merge(a) -> None:
    labels = {}
    if a.labels:
        with open(a.labels, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if row.get("label") in ("pass", "fail"):
                    labels[(row["case_id"], row["arm"], str(row["run"]))] = row
    rows = []
    for j in load_jsonl([a.judged]):
        k = (j["case_id"], j["arm"], str(j["run"]))
        h = labels.get(k)
        verdict = h["label"] if h else (j["verdict"] if j["verdict"] != "unknown" else "undecidable")
        cls = (h.get("failure_class") if h else j.get("failure_class")) or ""
        rows.append({"case_id": k[0], "arm": k[1], "run": k[2], "verdict": verdict, "failure_class": cls,
                     "loud": str(cls in ("execution_error", "timeout")).lower(),
                     "source": "human" if h else "judge"})
    for k, h in labels.items():
        if not any((r["case_id"], r["arm"], r["run"]) == k for r in rows):
            rows.append({"case_id": k[0], "arm": k[1], "run": k[2], "verdict": h["label"],
                         "failure_class": h.get("failure_class", ""), "loud": "false", "source": "human"})
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    with open(a.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["case_id", "arm", "run", "verdict", "failure_class", "loud", "source"])
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} overrides to {a.out}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    j = sub.add_parser("judge")
    for f in ("--scored", "--runs", "--refs", "--gold", "--out"):
        j.add_argument(f, required=True)
    j.add_argument("--all", action="store_true", help="judge every turn, not only undecidable ones (for calibration)")
    k = sub.add_parser("kappa")
    k.add_argument("--judged", required=True)
    k.add_argument("--labels", required=True)
    m = sub.add_parser("merge")
    m.add_argument("--judged", required=True)
    m.add_argument("--labels")
    m.add_argument("--out", required=True)
    a = ap.parse_args()
    {"judge": cmd_judge, "kappa": cmd_kappa, "merge": cmd_merge}[a.cmd](a)


if __name__ == "__main__":
    main()
