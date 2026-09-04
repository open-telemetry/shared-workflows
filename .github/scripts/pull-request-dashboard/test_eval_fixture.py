import json
import re
import unittest
from datetime import date
from itertools import groupby
from pathlib import Path

from utils import truncate

CASES = Path(__file__).resolve().parent / "eval" / "reviewer_feedback_cases.json"
LABELS = {"author_action", "no_author_action"}
ROLES = {"scored", "context"}
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
        for role in ("scored", "context"):
            self.assertEqual(
                counts[role],
                sum(1 for case in self.cases if case["role"] == role),
            )
        for stability in ("stable", "flaky"):
            self.assertEqual(
                counts[stability],
                sum(1 for case in self.cases if case["stability"] == stability),
            )
        self.assertEqual(
            counts["adjudicated"],
            sum(1 for case in self.cases if case["adjudicated_label"]),
        )

    def test_measurement_dates_describe_the_mixed_vintage_corpus(self) -> None:
        baseline = date.fromisoformat(self.data["baseline_generated_at"])
        updated = date.fromisoformat(self.data["measurements_updated_at"])
        measurements = []

        self.assertLessEqual(baseline, updated)
        self.assertNotIn("generated_at", self.data)
        for case in self.cases:
            with self.subTest(case=case["id"]):
                measured = date.fromisoformat(case["measurement_date"])
                self.assertLessEqual(baseline, measured)
                self.assertLessEqual(measured, updated)
                measurements.append(measured)
        self.assertEqual(baseline, min(measurements))
        self.assertEqual(updated, max(measurements))

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

    def test_cases_are_in_the_order_production_sends_them(self) -> None:
        # Batch boundaries follow this order, so a case out of place would be
        # measured in a batch the dashboard never assembles.
        seen: set[tuple[str, int]] = set()
        for group, cases in groupby(self.cases, key=lambda c: (c["repo"], c["pull_request"])):
            with self.subTest(pull_request=group):
                self.assertNotIn(group, seen)
                seen.add(group)
                stamps = [case["root_timestamp"] for case in cases]
                self.assertTrue(all(stamps))
                self.assertEqual(sorted(stamps), stamps)

    def test_every_field_the_scorer_reads_is_present(self) -> None:
        for case in self.cases:
            with self.subTest(case=case["id"]):
                for field in ("id", "requester", "pr_author", "body"):
                    self.assertIsInstance(case[field], str)
                    self.assertTrue(case[field].strip(), f"{field} is empty")

    def test_the_note_only_names_fields_a_case_really_has(self) -> None:
        """The note instructs humans, so a stale field name there silently misfiles work."""
        for name in re.findall(r"`([^`]+)`", self.data["note"]):
            self.assertIn(name, self.cases[0], f"the note tells a human to use `{name}`")

    def test_labels_are_known(self) -> None:
        for case in self.cases:
            with self.subTest(case=case["id"]):
                for field in ("recorded_label", "adjudicated_label"):
                    if case[field] is not None:
                        self.assertIn(case[field], LABELS)
                for observed in case["run_labels"]:
                    self.assertIn(observed, LABELS)

    def test_stability_agrees_with_the_recorded_runs(self) -> None:
        expected_runs = self.data["baseline_configuration"]["runs"]
        self.assertGreater(expected_runs, 1)
        for case in self.cases:
            with self.subTest(case=case["id"]):
                self.assertIn(case["role"], ROLES)
                self.assertEqual(expected_runs, len(case["run_actions"]))
                # context is not a dimension of its own: it is exactly the cases a
                # run left unanswered, so stability has nothing to describe
                if case["role"] == "context":
                    self.assertIsNone(case["stability"])
                    self.assertIsNone(case["recorded_label"])
                    self.assertIn(None, case["run_actions"])
                    continue
                self.assertNotIn(None, case["run_actions"])
                self.assertIn(case["stability"], STABILITIES)
                self.assertEqual(expected_runs, len(case["run_labels"]))
                distinct = set(case["run_labels"])
                if case["stability"] == "stable":
                    self.assertEqual(1, len(distinct))
                    self.assertEqual(case["recorded_label"], distinct.pop())
                else:
                    self.assertGreater(len(distinct), 1)
                    self.assertIsNone(case["recorded_label"])

    def test_run_labels_are_the_mapped_run_actions(self) -> None:
        action_labels = self.data["action_labels"]
        self.assertTrue(action_labels)
        for case in self.cases:
            with self.subTest(case=case["id"]):
                self.assertEqual(
                    [
                        action_labels[action]
                        for action in case["run_actions"]
                        if action is not None
                    ],
                    case["run_labels"],
                )


if __name__ == "__main__":
    unittest.main()
