import contextlib
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "eval"))

from score_reviewer_feedback import batch_cases, majority, report, summarize  # noqa: E402


def case(case_id: str, *, stability="stable", label="no_author_action", adjudicated=None) -> dict:
    return {
        "id": case_id,
        "repo": "repo",
        "pull_request": 1,
        "body": "body",
        "role": "scored",
        "stability": stability,
        "recorded_label": label,
        "adjudicated_label": adjudicated,
    }


class MajorityTest(unittest.TestCase):
    def test_unanimous(self) -> None:
        self.assertEqual("no_author_action", majority(["no_author_action", "no_author_action", "no_author_action"]))

    def test_more_than_half_wins(self) -> None:
        self.assertEqual("no_author_action", majority(["no_author_action", "no_author_action", "author_action"]))

    def test_a_tie_has_no_majority(self) -> None:
        self.assertIsNone(majority(["no_author_action", "author_action"]))
        self.assertIsNone(majority(["author_action", "no_author_action"]))

    def test_no_answers(self) -> None:
        self.assertIsNone(majority([]))


class SummarizeTest(unittest.TestCase):
    def test_agreeing_with_the_baseline_is_not_drift(self) -> None:
        summary = summarize([case("a")], [{"a": "no_author_action"}] * 3)

        self.assertEqual([], summary["drift"])
        self.assertEqual([], summary["inconsistent"])
        self.assertEqual([], summary["no_answer"])

    def test_a_changed_label_is_drift(self) -> None:
        summary = summarize([case("a")], [{"a": "author_action"}] * 3)

        self.assertEqual(1, len(summary["drift"]))
        self.assertEqual("author_action", summary["drift"][0]["got"])

    def test_disagreement_between_trials_is_flaky(self) -> None:
        summary = summarize(
            [case("a")], [{"a": "no_author_action"}, {"a": "author_action"}, {"a": "no_author_action"}]
        )

        self.assertEqual(1, len(summary["inconsistent"]))
        self.assertEqual([], summary["drift"])

    def test_a_case_missing_from_one_trial_is_partial(self) -> None:
        summary = summarize([case("a")], [{"a": "no_author_action"}, {}, {"a": "no_author_action"}])

        self.assertEqual(1, len(summary["partial_answer"]))
        # An unreliable candidate must not look stable by dropping cases.
        self.assertEqual([], summary["inconsistent"])
        self.assertEqual([], summary["drift"])

    def test_a_case_missing_everywhere_has_no_answer(self) -> None:
        summary = summarize([case("a")], [{}, {}, {}])

        self.assertEqual(1, len(summary["no_answer"]))
        self.assertEqual([], summary["partial_answer"])
        self.assertEqual([], summary["drift"])

    def test_a_tie_is_unscored(self) -> None:
        summary = summarize([case("a")], [{"a": "no_author_action"}, {"a": "author_action"}])

        self.assertEqual(1, len(summary["tied"]))
        self.assertEqual([], summary["drift"])
        self.assertEqual(1, len(summary["inconsistent"]))

    def test_unscored_adjudicated_cases_stay_in_the_denominator(self) -> None:
        cases = [
            case("a", adjudicated="no_author_action"),
            case("b", adjudicated="author_action"),
        ]
        summary = summarize(cases, [{"a": "no_author_action"}] * 3)

        self.assertEqual(2, len(summary["adjudicated"]))
        self.assertEqual(1, len(summary["scored"]))
        self.assertEqual(1, summary["correct"])

    def test_a_wrong_prediction_is_not_counted_correct(self) -> None:
        cases = [case("a", adjudicated="author_action")]
        summary = summarize(cases, [{"a": "no_author_action"}] * 3)

        self.assertEqual(1, len(summary["scored"]))
        self.assertEqual(0, summary["correct"])

    def test_flaky_is_reported_for_a_case_recorded_as_flaky(self) -> None:
        cases = [case("a", stability="flaky", label=None)]
        summary = summarize(
            cases, [{"a": "no_author_action"}, {"a": "author_action"}, {"a": "no_author_action"}]
        )

        self.assertEqual(1, len(summary["inconsistent"]))
        # A case with no baseline can never drift.
        self.assertEqual([], summary["drift"])

    def test_unsettled_stable_cases_shrink_the_drift_denominator(self) -> None:
        cases = [case("a"), case("b")]
        summary = summarize(cases, [{"a": "no_author_action"}] * 3)

        self.assertEqual(2, len(summary["stable"]))
        self.assertEqual(1, len(summary["stable_settled"]))
        # Dropping a case must not be able to report a clean drift of zero.
        self.assertEqual([], summary["drift"])


class ReportTest(unittest.TestCase):
    """The drift branch only runs when there is drift, so it needs its own cover."""

    def render(self, cases: list[dict], trials: list[dict]) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            report(cases, trials, baseline_flaky=0, baseline_runs=len(trials))
        return out.getvalue()

    def test_a_clean_run_reports_no_drift(self) -> None:
        text = self.render([case("a")], [{"a": "no_author_action"}] * 3)

        self.assertIn("drift          0", text)

    def test_drift_details_are_printed(self) -> None:
        text = self.render([case("a")], [{"a": "author_action"}] * 3)

        self.assertIn("drift          1", text)
        self.assertIn("no_author_action -> author_action", text)


class BatchCasesTest(unittest.TestCase):
    def test_a_batch_never_mixes_pull_requests(self) -> None:
        cases = [
            {**case(f"{repo}-{number}-{index}"), "repo": repo, "pull_request": number}
            for repo in ("repo-a", "repo-b")
            for number in (1, 2)
            for index in range(3)
        ]

        for batch in batch_cases(cases):
            with self.subTest(batch=[c["id"] for c in batch]):
                self.assertEqual(
                    1, len({(c["repo"], c["pull_request"]) for c in batch})
                )

    def test_a_large_pull_request_is_split(self) -> None:
        cases = [case(f"a-{index}") for index in range(25)]

        batches = batch_cases(cases)

        self.assertEqual([10, 10, 5], [len(b) for b in batches])


if __name__ == "__main__":
    unittest.main()
