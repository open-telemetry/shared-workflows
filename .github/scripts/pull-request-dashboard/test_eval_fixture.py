import json
import unittest
from pathlib import Path

from utils import truncate

CASES = Path(__file__).resolve().parent / "eval" / "reviewer_feedback_cases.json"
LABELS = {"substantive", "noise"}
STABILITIES = {"stable", "flaky", "unobserved"}


class EvalFixtureTest(unittest.TestCase):
    """Guards the eval fixture's shape. Scoring it requires model calls and is manual."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.data = json.loads(CASES.read_text(encoding="utf-8"))
        cls.cases = cls.data["cases"]

    def test_counts_match_the_cases(self) -> None:
        counts = self.data["counts"]
        self.assertEqual(counts["cases"], len(self.cases))
        for stability in ("stable", "flaky", "unobserved"):
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

    def test_bodies_are_what_production_sends(self) -> None:
        # derive_top_level_items truncates before classifying, so a body recorded
        # verbatim would be scored against input the dashboard never sends.
        for case in self.cases:
            with self.subTest(case=case["id"]):
                self.assertEqual(truncate(case["body"]), case["body"])

    def test_every_field_the_scorer_reads_is_present(self) -> None:
        for case in self.cases:
            with self.subTest(case=case["id"]):
                for field in ("id", "requester", "pr_author", "body"):
                    self.assertIsInstance(case[field], str)
                    self.assertTrue(case[field].strip(), f"{field} is empty")

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
                self.assertEqual(expected_runs, len(case["observed_actions"]))
                if case["stability"] == "unobserved":
                    self.assertIsNone(case["baseline"])
                    self.assertIn(None, case["observed_actions"])
                    continue
                self.assertEqual(expected_runs, len(case["observed_runs"]))
                distinct = set(case["observed_runs"])
                if case["stability"] == "stable":
                    self.assertEqual(1, len(distinct))
                    self.assertEqual(case["baseline"], distinct.pop())
                else:
                    self.assertGreater(len(distinct), 1)
                    self.assertIsNone(case["baseline"])

    def test_observed_runs_are_the_mapped_observed_actions(self) -> None:
        action_labels = self.data["action_labels"]
        self.assertTrue(action_labels)
        for case in self.cases:
            with self.subTest(case=case["id"]):
                self.assertEqual(
                    [
                        action_labels[action]
                        for action in case["observed_actions"]
                        if action is not None
                    ],
                    case["observed_runs"],
                )


if __name__ == "__main__":
    unittest.main()
