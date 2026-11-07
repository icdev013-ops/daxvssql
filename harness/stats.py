"""Paired statistics and the pre-registered decision rule (PREREGISTRATION.md).

usage:
  python harness/stats.py --scored scored.jsonl [--adjudication adj.csv] --gold gold/templates.yaml
                          [--runs runs/*.jsonl] [--a A] [--b B] [--seed 7]
  python harness/stats.py --mde 236 --disagree 0.1 0.2 0.3

Inputs
  scored.jsonl     score.py output (verdict pass|fail|undecidable, failure_class, loud, ...)
  adj.csv          optional human/judge overrides: case_id,arm,run,verdict,failure_class,loud
  templates.yaml   for question-type tags and split
  runs/*.jsonl     bot logs, for latency and cost (fields latency_ms / cost_usd, or totals)

Only the Test split decides; other splits are reported separately.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

PREDECLARED_B = ["appointments_sales", "row_level_list", "cohort_funnel"]
LOUD_CLASSES = {"execution_error", "timeout", "wrong_refusal"}


# ----------------------------------------------------------------------------- stats primitives
def exact_mcnemar(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value (binomial test on discordant pairs)."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def holm(pvals: dict[str, float]) -> dict[str, float]:
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m, out, running = len(items), {}, 0.0
    for i, (k, p) in enumerate(items):
        running = max(running, min(1.0, (m - i) * p))
        out[k] = running
    return out


def norm_ppf(p: float) -> float:
    # Acklam's rational approximation; plenty for power tables
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02, 1.383577518672690e+02,
         -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02, 6.680131188771972e+01,
         -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00, -2.549732539343734e+00,
         4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00]
    pl = 0.02425
    if p < pl:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > 1 - pl:
        return -norm_ppf(1 - p)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def mde(n: int, psi: float, alpha: float = 0.05, power: float = 0.8) -> float:
    """Smallest detectable accuracy gap (proportion) for a paired McNemar design
    with n cases and a discordant-pair rate psi (Connor 1987 approximation)."""
    za, zb = norm_ppf(1 - alpha / 2), norm_ppf(power)
    lo, hi = 1e-6, psi
    for _ in range(80):
        d = (lo + hi) / 2
        need = (za * math.sqrt(psi) + zb * math.sqrt(max(psi - d * d, 0))) ** 2 / (d * d)
        lo, hi = (d, hi) if need > n else (lo, d)
    return hi


# ----------------------------------------------------------------------------- loading
def load_jsonl(paths):
    rows = []
    for p in paths or []:
        for line in Path(p).read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def final_verdicts(scored, adj_path):
    over = {}
    if adj_path:
        with open(adj_path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                over[(r["case_id"], r["arm"], str(r["run"]))] = r
    out = []
    for s in scored:
        key = (s["case_id"], s["arm"], str(s.get("run")))
        if key in over:
            o = over[key]
            s = {**s, "verdict": o["verdict"], "failure_class": o.get("failure_class", ""),
                 "loud": str(o.get("loud", "")).lower() in ("1", "true", "yes")}
        out.append(s)
    return out


# ----------------------------------------------------------------------------- analysis
def per_case(rows, arm):
    """case_id -> dict(mean, majority, all_pass, flips, silent_rate, template, franchise)"""
    by = defaultdict(list)
    meta = {}
    for r in rows:
        if r["arm"] != arm or r["verdict"] == "undecidable":
            continue
        ok = r["verdict"] == "pass"
        silent = (not ok) and r.get("failure_class") not in LOUD_CLASSES and not r.get("loud")
        by[r["case_id"]].append((ok, silent))
        meta[r["case_id"]] = (r.get("template_id"), r.get("franchise"))
    out = {}
    for cid, v in by.items():
        oks = [o for o, _ in v]
        out[cid] = {"mean": sum(oks) / len(oks), "majority": sum(oks) * 2 > len(oks), "n": len(oks),
                    "all_pass": all(oks), "flip": 0 < sum(oks) < len(oks),
                    "silent": sum(s for _, s in v) / len(v), "template": meta[cid][0], "franchise": meta[cid][1]}
    return out


def arm_summary(pc):
    if not pc:
        return {}
    vals = list(pc.values())
    fr = defaultdict(list)
    for v in vals:
        fr[v["franchise"]].append(v["mean"])
    return {"cases": len(vals), "accuracy": np.mean([v["mean"] for v in vals]),
            "pass_all_runs": np.mean([v["all_pass"] for v in vals]),
            "flip_rate": np.mean([v["flip"] for v in vals]),
            "silent_error_rate": np.mean([v["silent"] for v in vals]),
            "franchise_equal_weight": np.mean([np.mean(x) for x in fr.values()]),
            "per_franchise": {k: (len(x), float(np.mean(x))) for k, x in sorted(fr.items())}}


def paired(pa, pb, cases=None, seed=7, draws=10_000):
    common = sorted(set(pa) & set(pb) & (set(cases) if cases is not None else set(pa)))
    if not common:
        return None
    b = sum(1 for c in common if pa[c]["majority"] and not pb[c]["majority"])   # A right, B wrong
    c_ = sum(1 for c in common if pb[c]["majority"] and not pa[c]["majority"])  # B right, A wrong
    diff = {c: pb[c]["mean"] - pa[c]["mean"] for c in common}
    by_t = defaultdict(list)
    for c in common:
        by_t[pa[c]["template"]].append(diff[c])
    tkeys = list(by_t)
    rng = np.random.default_rng(seed)
    sums = np.array([sum(by_t[t]) for t in tkeys])
    cnts = np.array([len(by_t[t]) for t in tkeys])
    idx = rng.integers(0, len(tkeys), size=(draws, len(tkeys)))
    boot = sums[idx].sum(1) / cnts[idx].sum(1)
    return {"n": len(common), "templates": len(tkeys), "A_right_B_wrong": b, "B_right_A_wrong": c_,
            "diff_B_minus_A": float(np.mean(list(diff.values()))),
            "ci95": (float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))),
            "mcnemar_p": exact_mcnemar(b, c_)}


def latency_cost(runs, arm):
    lat, cost = [], []
    for r in runs:
        if r.get("arm") != arm:
            continue
        l = r.get("latency_ms") or (r.get("totals") or {}).get("latency_ms")
        c = r.get("cost_usd") or (r.get("totals") or {}).get("cost_usd")
        if l is not None:
            lat.append(float(l))
        if c is not None:
            cost.append(float(c))
    return {"p50_ms": float(np.percentile(lat, 50)) if lat else None,
            "p95_ms": float(np.percentile(lat, 95)) if lat else None,
            "cost_per_1000": float(np.mean(cost) * 1000) if cost else None}


def decide(prim, sa, sb, lca, lcb, per_type, mde_pts):
    """Apply the pre-registered rule. Safety is judged separately (any violation disqualifies)."""
    vetoes = []
    if lca["p95_ms"] and lcb["p95_ms"] and (lcb["p95_ms"] > 2 * lca["p95_ms"] or lcb["p95_ms"] > 20_000):
        vetoes.append("latency")
    if lca["cost_per_1000"] and lcb["cost_per_1000"] and lcb["cost_per_1000"] > 2 * lca["cost_per_1000"] \
            and prim["diff_B_minus_A"] <= 0:
        vetoes.append("cost")
    if sb["silent_error_rate"] - sa["silent_error_rate"] >= 0.03:
        vetoes.append("silent errors")
    lo, hi = prim["ci95"]
    if lo > 0 and prim["diff_B_minus_A"] >= 0.05 and sb["silent_error_rate"] <= sa["silent_error_rate"] and not vetoes:
        return "B clearly better -> plan a production SQL route (each prerequisite needs approval)", vetoes
    wins = [t for t, r in per_type.items() if t in PREDECLARED_B and r and r["B_right_A_wrong"] >= 10
            and r.get("holm_p", 1) < 0.05 and r["B_right_A_wrong"] > r["A_right_B_wrong"]]
    if lo <= 0 <= hi and wins:
        return f"B better only on {wins} -> test a model fix first; else DAX primary + SQL tool for that type", vetoes
    if lo <= 0 <= hi and abs(prim["diff_B_minus_A"]) < mde_pts:
        return "Inconclusive: not distinguishable at this sample size -> stay on DAX", vetoes
    return "A better or tie -> stay on DAX; invest in the date-basis fix and governance", vetoes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scored")
    ap.add_argument("--adjudication")
    ap.add_argument("--gold")
    ap.add_argument("--runs", nargs="*")
    ap.add_argument("--a", default="A")
    ap.add_argument("--b", default="B")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--mde", type=int)
    ap.add_argument("--disagree", type=float, nargs="*", default=[0.1, 0.2, 0.3])
    a = ap.parse_args()

    if a.mde:
        for psi in a.disagree:
            print(f"n={a.mde} disagree={psi:.0%}: detectable gap {mde(a.mde, psi) * 100:.1f} points")
        return

    rows = final_verdicts(load_jsonl([a.scored]), a.adjudication)
    gold = yaml.safe_load(Path(a.gold).read_text(encoding="utf-8"))
    tpls = {t["id"]: t for t in (gold.get("templates") if isinstance(gold, dict) else gold)}
    split = lambda r: (tpls.get(r.get("template_id")) or {}).get("split", "test")
    runs = load_jsonl(a.runs)

    und = sum(1 for r in rows if r["verdict"] == "undecidable")
    if und:
        print(f"WARNING: {und} turns still undecidable (excluded). Adjudicate before deciding.\n")

    for sp in sorted({split(r) for r in rows}):
        sub = [r for r in rows if split(r) == sp]
        pa, pb = per_case(sub, a.a), per_case(sub, a.b)
        print(f"=== split: {sp}")
        for arm, pc in ((a.a, pa), (a.b, pb)):
            s = arm_summary(pc)
            if s:
                print(f"  {arm}: cases {s['cases']}, accuracy {s['accuracy']:.1%}, pass^k {s['pass_all_runs']:.1%}, "
                      f"flip {s['flip_rate']:.1%}, silent errors {s['silent_error_rate']:.1%}, "
                      f"franchise-equal {s['franchise_equal_weight']:.1%}, per franchise {s['per_franchise']}")
        p = paired(pa, pb, seed=a.seed)
        if p:
            print(f"  paired: n {p['n']} ({p['templates']} templates), B-A {p['diff_B_minus_A']:+.1%}, "
                  f"95% CI [{p['ci95'][0]:+.1%}, {p['ci95'][1]:+.1%}], discordant A>B {p['A_right_B_wrong']} "
                  f"B>A {p['B_right_A_wrong']}, exact McNemar p {p['mcnemar_p']:.4f}")
        if sp != "test" or not p:
            continue
        per_type = {}
        tags = sorted({tag for t in tpls.values() for tag in (t.get("types") or t.get("tags") or [])})
        for tag in tags:
            cases = [c for c in pa if tag in ((tpls.get(pa[c]["template"]) or {}).get("types")
                                               or (tpls.get(pa[c]["template"]) or {}).get("tags") or [])]
            per_type[tag] = paired(pa, pb, cases=cases, seed=a.seed)
        adj = holm({t: r["mcnemar_p"] for t, r in per_type.items() if r and t in PREDECLARED_B})
        for t, r in per_type.items():
            if r:
                if t in adj:
                    r["holm_p"] = adj[t]
                print(f"  type {t:22} n {r['n']:4} B-A {r['diff_B_minus_A']:+.1%} A>B {r['A_right_B_wrong']:3} "
                      f"B>A {r['B_right_A_wrong']:3} p {r['mcnemar_p']:.4f}"
                      + (f" holm {r['holm_p']:.4f}" if "holm_p" in r else ""))
        sa, sb = arm_summary(pa), arm_summary(pb)
        lca, lcb = latency_cost(runs, a.a), latency_cost(runs, a.b)
        print(f"  latency/cost {a.a}: {lca}\n  latency/cost {a.b}: {lcb}")
        disc = (p["A_right_B_wrong"] + p["B_right_A_wrong"]) / p["n"]
        gap = mde(p["n"], max(disc, 1e-3))
        print(f"  observed disagreement {disc:.1%}; detectable gap at this n: {gap * 100:.1f} points")
        verdict, vetoes = decide(p, sa, sb, lca, lcb, per_type, gap)
        print(f"\nDECISION (pre-registered rule, safety judged separately): {verdict}"
              + (f"\n  vetoes on B: {vetoes}" if vetoes else ""))


if __name__ == "__main__":
    main()
