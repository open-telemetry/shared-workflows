import json
import unittest
from pathlib import Path

CASES = Path(__file__).resolve().parent / "eval" / "reviewer_feedback_cases.json"
LABELS = {"substantive", "noise"}
STABILITIES = {"stable", "flaky"}


class EvalFixtureTest(unittest.TestCase):
    """Guards the eval fixture's shape. Scoring it requires model calls and is manual."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.data = json.loads(CASES.read_text(encoding="utf-8"))
        cls.cases = cls.data["cases"]

    def test_counts_match_the_cases(self) -> None:
        counts = self.data["counts"]
        self.assertEqual(counts["cases"], len(self.cases))
        for stability in ("stable", "flaky"):
            self.assertEqual(
                counts[stability],
                sum(1 for case in self.cases if case["stability"] == stability),
            )
        self.assertEqual(
            counts["adjudicated"],
            sum(1 for case in self.cases if case["adjudicated"]),
        )

    def test_case_ids_are_unique(self) -> None:
        ids = [case["id"] for case in self.cases]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_case_has_a_traceable_body(self) -> None:
        for case in self.cases:
            with self.subTest(case=case["id"]):
                self.assertTrue(case["body"].strip())
                self.assertTrue(case["repo"])
                self.assertIsInstance(case["pull_request"], int)

    def test_labels_are_known(self) -> None:
        for case in self.cases:
            with self.subTest(case=case["id"]):
                for field in ("baseline", "adjudicated"):
                    if case[field] is not None:
                        self.assertIn(case[field], LABELS)
                for observed in case["observed_runs"]:
                    self.assertIn(observed, LABELS)

    def test_stability_agrees_with_the_recorded_runs(self) -> None:
        expected_runs = self.data["baseline_configuration"]["runs"]
        self.assertGreater(expected_runs, 1)
        for case in self.cases:
            with self.subTest(case=case["id"]):
                self.assertIn(case["stability"], STABILITIES)
                self.assertEqual(expected_runs, len(case["observed_runs"]))
                distinct = set(case["observed_runs"])
                if case["stability"] == "stable":
                    self.assertEqual(1, len(distinct))
                    self.assertEqual(case["baseline"], distinct.pop())
                else:
                    self.assertGreater(len(distinct), 1)
                    self.assertIsNone(case["baseline"])


if __name__ == "__main__":
    unittest.main()
