"""Run a scored batch end to end (PREREGISTRATION.md "Data protocol").

  1. refs (pre):  gold/validate.py runs every reference SQL (sandbox) and reference DAX (dataset)
                  for the chosen cases and anchor -> gold/private/refs_<anchor>_<tag>.jsonl
  2. cases:       resolved questions from that calibration file -> runs/<tag>_cases.jsonl
                  (multi-turn templates keep conv/turn so each arm carries its own history)
  3. arms:        bot CLI per run index, arm order alternating between runs (A,B then B,A ...)
  4. refs (post): the same references again; a case whose reference moved between pre and post
                  on the source an arm reads is set aside (data drift, not a route error)
  5. score + stats

usage (from the daxvssql folder):
  python harness/run.py --anchor 2026-11-07 --tag pilot1 --splits test --sample 30 --arms A,B --runs 1
  python harness/run.py --anchor 2026-11-08 --tag full1 --splits test,dev,robustness,multiturn,why_forecast,unanswerable,ambiguity --arms A,B --runs 3
  python harness/run.py --tag pilot1 --score-only          # re-score an existing batch

Everything this writes holds real franchise data and lives in gitignored folders.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
GOLD = ROOT / "gold"
PRIV = GOLD / "private"
RUNS = ROOT / "runs"
BOT = Path(os.environ["POC_BOT_DIR"]) if os.environ.get("POC_BOT_DIR") else ROOT / "bot"
NPX = shutil.which("npx.cmd") or shutil.which("npx") or "npx"
SOURCE = {"A": "dax", "A0": "dax", "B": "sql", "B+": "sql"}


def sh(cmd: list[str], cwd: Path = ROOT) -> None:
    print("$", " ".join(cmd), flush=True)
    r = subprocess.run(cmd, cwd=cwd)
    if r.returncode != 0:
        sys.exit(f"step failed ({r.returncode}): {' '.join(cmd[:4])} ...")


def load_jsonl(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()] if p.exists() else []


def templates() -> dict[str, dict]:
    d = yaml.safe_load((GOLD / "templates.yaml").read_text(encoding="utf-8"))
    return {t["id"]: t for t in (d.get("templates") if isinstance(d, dict) else d)}


def stratified_sample(tpls: dict, splits: set[str], n: int, seed: int) -> set[str]:
    """Pick about n cases (template x franchise) spread round-robin over question types."""
    by_type = defaultdict(list)
    for t in tpls.values():
        if t["split"] in splits and t.get("franchises"):
            by_type[(t.get("types") or ["untyped"])[0]].append(t)
    rng = random.Random(seed)
    for v in by_type.values():
        rng.shuffle(v)
    chosen, cases = set(), 0
    while cases < n and any(by_type.values()):
        for k in sorted(by_type):
            if by_type[k] and cases < n:
                t = by_type[k].pop()
                chosen.add(t["id"])
                cases += len(t["franchises"])
    return chosen


def paraphrase_questions(t: dict, r: dict, anchor: str) -> list[str]:
    """Robustness templates: the reworded variants, with the same params and period tokens filled."""
    sys.path.insert(0, str(GOLD))
    import validate  # noqa: E402  (gold/validate.py)
    a = validate.dt.date.fromisoformat(anchor)
    tokens = {**validate.anchor_tokens(a), **validate.period_tokens(t.get("period") or "all_time", a)}
    return [validate.fill(p, tokens, r.get("params") or {}, t["id"]) for p in t.get("paraphrases") or []]


def build_cases(calib: list[dict], tpls: dict, anchor: str, out: Path) -> tuple[int, int]:
    usable, skipped, seen = [], 0, set()
    for r in calib:
        if "case_id" not in r:
            continue
        if r.get("status") in ("disagree", "error"):
            skipped += 1          # the gold reference itself is not trustworthy for this case
            continue
        if "#alt" in r["case_id"]:  # alternate reading of a normal question: a scoring alternative only
            continue
        # rubric splits carry supporting references as T#r1@E / T#f1@E / T#evidence@E: ask T@E once
        base = re.sub(r"#[^@]+", "", r["case_id"])
        if base in seen:
            continue
        seen.add(base)
        r = {**r, "case_id": base}
        t = tpls.get(r["template_id"], {})
        if "question" not in r and t.get("split") != "robustness":
            continue
        if t.get("split") == "robustness" and t.get("paraphrases"):
            # the original wording is already the Test case; run only the rewordings
            for k, q in enumerate(paraphrase_questions(t, r, anchor), 1):
                usable.append({"id": r["case_id"].replace("@", f"~p{k}@"), "franchise": r["franchise"],
                               "anchor": anchor, "question": q})
            continue
        case = {"id": r["case_id"], "franchise": r["franchise"], "anchor": anchor, "question": r["question"]}
        if t.get("conversation"):
            case.update(conv=t["conversation"], turn=int(t.get("turn") or 1))
        usable.append(case)
    out.write_text("".join(json.dumps(c) + "\n" for c in usable), encoding="utf-8")
    return len(usable), skipped


def drift(pre: Path, post: Path) -> set[tuple[str, str]]:
    a = {(r["case_id"], r["source"]): r for r in load_jsonl(pre)}
    b = {(r["case_id"], r["source"]): r for r in load_jsonl(post)}
    return {k for k in a if k in b and (a[k].get("rows") != b[k].get("rows"))}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--anchor")
    ap.add_argument("--tag", required=True)
    ap.add_argument("--splits", default="test")
    ap.add_argument("--ids", default="", help="comma list of template ids (overrides --sample)")
    ap.add_argument("--sample", type=int, default=0, help="about N cases, stratified by question type")
    ap.add_argument("--franchises", default="")
    ap.add_argument("--arms", default="A,B")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--run-offset", type=int, default=0, help="first run index = offset + 1 (batches of one run each)")
    ap.add_argument("--dax-rpm", type=int, default=25, help="reference DAX calls per minute (shared service principal)")
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--score-only", action="store_true")
    ap.add_argument("--reuse-refs", default="", help="reuse the cases and SQL references of an earlier batch tag "
                    "(SQL-only arms: the sandbox is frozen, so no reference pass and no drift check)")
    a = ap.parse_args()

    RUNS.mkdir(exist_ok=True)
    # Per-batch overrides (logged in DEVIATIONS.md), read at start so a running driver can be steered
    # without killing it: runs/batch_overrides.json = {"<tag>": {"splits": "...", "concurrency": 3}}
    ov_file = RUNS / "batch_overrides.json"
    if ov_file.exists():
        for k, v in (json.loads(ov_file.read_text(encoding="utf-8")).get(a.tag) or {}).items():
            print(f"override for {a.tag}: {k} = {v!r} (was {getattr(a, k, None)!r})")
            setattr(a, k, v)
    tpls = templates()
    meta_file = RUNS / f"{a.tag}_meta.json"
    if a.score_only:
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
    else:
        if not a.anchor:
            sys.exit("--anchor is required")
        splits = set(a.splits.split(","))
        ids = set(filter(None, a.ids.split(","))) or (stratified_sample(tpls, splits, a.sample, a.seed) if a.sample else set())
        # multi-turn: pull in every turn of a sampled conversation
        convs = {tpls[i].get("conversation") for i in ids if tpls.get(i, {}).get("conversation")}
        ids |= {t["id"] for t in tpls.values() if t.get("conversation") in convs and convs}
        sel = ["--splits", a.splits] + (["--ids", ",".join(sorted(ids))] if ids else []) + \
              (["--franchises", a.franchises] if a.franchises else [])
        if a.reuse_refs:
            if any(SOURCE.get(x) == "dax" for x in a.arms.split(",")):
                sys.exit("--reuse-refs is only for SQL arms (B, B+): DAX references drift with the datasets")
            src_tag = a.reuse_refs
            calib = [r for r in load_jsonl(PRIV / f"calibration_{a.anchor}_{src_tag}.jsonl")
                     if r.get("split") in splits and (not ids or r.get("template_id") in ids)]
            shutil.copyfile(PRIV / f"refs_{a.anchor}_{src_tag}.jsonl", PRIV / f"refs_{a.anchor}_{a.tag}.jsonl")
        else:
            sh([sys.executable, "gold/validate.py", "--anchor", a.anchor, "--tag", a.tag, "--no-probes",
                "--dax-rpm", str(a.dax_rpm)] + sel)
            calib = load_jsonl(PRIV / f"calibration_{a.anchor}_{a.tag}.jsonl")
        n, skipped = build_cases(calib, tpls, a.anchor, RUNS / f"{a.tag}_cases.jsonl")
        print(f"cases: {n} usable, {skipped} set aside because SQL and DAX references disagree")
        arms = a.arms.split(",")
        run_files = []
        for r in range(a.run_offset + 1, a.run_offset + a.runs + 1):
            order = arms if r % 2 else list(reversed(arms))
            for arm in order:
                rid = f"{a.tag}-{arm.replace('+', 'plus')}-r{r}"
                sh([NPX, "tsx", "src/cli.ts", "--arm", arm, "--anchor", a.anchor, "--cases",
                    str(RUNS / f"{a.tag}_cases.jsonl"), "--run-id", rid, "--run-index", str(r),
                    "--concurrency", str(a.concurrency)], cwd=BOT)
                run_files.append(str(RUNS / f"{rid}.jsonl"))
        if a.reuse_refs:
            moved = set()   # frozen sandbox: SQL references cannot move
        else:
            sh([sys.executable, "gold/validate.py", "--anchor", a.anchor, "--tag", f"{a.tag}_post", "--no-probes",
                "--dax-rpm", str(a.dax_rpm)] + sel)
            moved = drift(PRIV / f"refs_{a.anchor}_{a.tag}.jsonl", PRIV / f"refs_{a.anchor}_{a.tag}_post.jsonl")
        meta = {"anchor": a.anchor, "tag": a.tag, "arms": arms, "runs": a.runs, "run_files": run_files,
                "cases": n, "gold_disagree_skipped": skipped, "drifted": sorted(map(list, moved))}
        meta_file.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        print(f"reference drift during the batch: {len(moved)} case/source pair(s) set aside")

    # score (drifted pairs excluded)
    moved = {tuple(x) for x in meta["drifted"]}
    runs = [r for f in meta["run_files"] for r in load_jsonl(Path(f))]
    keep = [r for r in runs if (r.get("case_id"), SOURCE.get(r.get("arm"), "")) not in moved]
    filtered = RUNS / f"{meta['tag']}_runs_kept.jsonl"
    filtered.write_text("".join(json.dumps(r) + "\n" for r in keep), encoding="utf-8")
    scored = RUNS / f"{meta['tag']}_scored.jsonl"
    sh([sys.executable, "harness/score.py", "--runs", str(filtered), "--refs",
        str(PRIV / f"refs_{meta['anchor']}_{meta['tag']}.jsonl"), "--gold", str(GOLD / "templates.yaml"),
        "--out", str(scored)])
    arms = meta["arms"]
    if len(arms) >= 2:
        sh([sys.executable, "harness/stats.py", "--scored", str(scored), "--gold", str(GOLD / "templates.yaml"),
            "--runs", *meta["run_files"], "--a", arms[0], "--b", arms[1]])


if __name__ == "__main__":
    main()
