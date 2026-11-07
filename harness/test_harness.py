"""Self-tests for score.py and stats.py on synthetic data (no network, no database).
run: python -m unittest harness/test_harness.py -v   (from the repo root)
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import score  # noqa: E402
import stats  # noqa: E402

NUM_TPL = {"id": "T1", "answer_shape": "number", "result_columns": ["sold_count", "sold_amount"],
           "col_types": {"sold_count": "count", "sold_amount": "currency"}, "headline": ["sold_count", "sold_amount"]}
REF = {"case_id": "T1@ICAU", "source": "sql", "columns": ["sold_count", "sold_amount"], "rows": [[218, 3804118.01]]}
LIST_TPL = {"id": "T2", "answer_shape": "ranked_list", "result_columns": ["designer", "sold_amount"],
            "col_types": {"sold_amount": "currency"}, "label_column": "designer", "value_columns": ["sold_amount"],
            "top_k": 3, "order_by": {"column": "sold_amount"}}
LIST_REF = {"rows": [["Ann", 300.0], ["Bob", 200.0], ["Cy", 100.0], ["Di", 50.0]]}


def turn(answer, **kw):
    return {"case_id": "T1@ICAU", "template_id": "T1", "arm": "B", "run": 1, "franchise": "ICAU", "answer": answer, **kw}


class ScoreTests(unittest.TestCase):
    def test_number_pass_exact_and_abbreviated(self):
        a = "You sold **218 projects** worth **$3.80M** this year (by sold date)."
        self.assertEqual(score.score_turn(NUM_TPL, REF, turn(a))["verdict"], "pass")

    def test_number_fail_wrong_count(self):
        a = "You sold **281 projects** worth **$3,804,118** this year."
        s = score.score_turn(NUM_TPL, REF, turn(a))
        self.assertEqual((s["verdict"], s["failure_class"]), ("fail", "value_mismatch"))

    def test_number_ignores_query_log_and_dates(self):
        a = "[SQL_LOG_R1: SELECT 218, 3804118.01] As of 2026-11-07 you sold **200 projects** worth **$3.1M**."
        self.assertEqual(score.score_turn(NUM_TPL, REF, turn(a))["verdict"], "fail")

    def test_narration_mismatch(self):
        a = "You sold **210 projects** worth **$3.80M**."
        t = turn(a, queries=[{"result": {"rows": [[218, 3804118.01]]}}])
        self.assertEqual(score.score_turn(NUM_TPL, REF, t)["failure_class"], "narration_mismatch")

    def test_refusal_and_error(self):
        self.assertEqual(score.score_turn(NUM_TPL, REF, turn("I don't have access to that data."))["failure_class"],
                         "wrong_refusal")
        s = score.score_turn(NUM_TPL, REF, turn("", error="timeout after 25s"))
        self.assertEqual((s["failure_class"], s["loud"]), ("timeout", True))

    def test_ranked_list_from_viz(self):
        viz = {"type": "table", "columns": [{"name": "Designer"}, {"name": "Sold Amount"}],
               "data": [{"Designer": "Ann", "Sold Amount": 300}, {"Designer": "Bob", "Sold Amount": 200.4},
                        {"Designer": "Cy", "Sold Amount": 100}]}
        a = "Top designers:\n\n```viz\n" + json.dumps(viz) + "\n```\n"
        self.assertEqual(score.score_turn(LIST_TPL, LIST_REF, turn(a))["verdict"], "pass")

    def test_ranked_list_markdown_wrong_order(self):
        a = "| Designer | Sold |\n|---|---|\n| Bob | 200 |\n| Ann | 300 |\n| Cy | 100 |\n"
        s = score.score_turn(LIST_TPL, LIST_REF, turn(a))
        self.assertEqual(s["verdict"], "fail")
        self.assertIn("order", s["detail"])

    def test_percent_display_rounding(self):
        tpl = {"answer_shape": "number", "result_columns": ["rate"], "col_types": {"rate": "percent"},
               "headline": ["rate"]}
        ref = {"rows": [[51.94]]}
        self.assertEqual(score.score_turn(tpl, ref, turn("Closing rate is **52%**."))["verdict"], "pass")
        self.assertEqual(score.score_turn(tpl, ref, turn("Closing rate is **51.6%**."))["verdict"], "fail")


class StatsTests(unittest.TestCase):
    def test_exact_mcnemar(self):
        self.assertAlmostEqual(stats.exact_mcnemar(0, 5), 0.0625)
        self.assertLess(stats.exact_mcnemar(15, 5), 0.05)
        self.assertGreater(stats.exact_mcnemar(14, 6), 0.05)

    def test_mde_matches_plan_table(self):
        # report table: n=236 -> 5.7 / 8.1 / 9.9 points; n=66, 10% -> 10.0
        for n, psi, want in ((236, 0.1, 5.7), (236, 0.2, 8.1), (236, 0.3, 9.9), (66, 0.1, 10.0)):
            self.assertAlmostEqual(stats.mde(n, psi) * 100, want, delta=0.8)

    def test_holm(self):
        adj = stats.holm({"a": 0.01, "b": 0.04, "c": 0.03})
        self.assertAlmostEqual(adj["a"], 0.03)
        self.assertAlmostEqual(adj["c"], 0.06)
        self.assertAlmostEqual(adj["b"], 0.06)

    def test_paired_bootstrap(self):
        rows = []
        for i in range(60):
            for arm in ("A", "B"):
                ok = (i % 5 != 0) if arm == "A" else (i % 3 != 0)
                for run in (1, 2, 3):
                    rows.append({"case_id": f"c{i}", "template_id": f"t{i // 2}", "franchise": "ICAU",
                                 "arm": arm, "run": run, "verdict": "pass" if ok else "fail",
                                 "failure_class": "value_mismatch", "loud": False})
        p = stats.paired(stats.per_case(rows, "A"), stats.per_case(rows, "B"))
        self.assertEqual(p["n"], 60)
        self.assertLess(p["ci95"][0], p["diff_B_minus_A"])
        self.assertGreater(p["ci95"][1], p["diff_B_minus_A"])


if __name__ == "__main__":
    unittest.main()
