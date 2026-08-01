import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent / "eval"))

import regenerate_baseline  # noqa: E402
from regenerate_baseline import answers, rebuild, run_batch  # noqa: E402


def case(case_id: str, *, adjudicated=None) -> dict:
    return {
        "id": case_id,
        "repo": "repo",
        "pull_request": 1,
        "requester": "reviewer",
        "pr_author": "author",
        "review_state": None,
        "root_timestamp": "2026-01-01T00:00:00Z",
        "body": "body",
        "stability": "stable",
        "baseline": "noise",
        "observed_runs": [],
        "observed_actions": [],
        "adjudicated": adjudicated,
    }


def payload(*cases: dict) -> dict:
    return {
        "baseline_configuration": {"model": "old", "prompt": "old", "runs": 1},
        "counts": {},
        "cases": list(cases),
    }


def response(*items: dict) -> dict:
    return {"returncode": 0, "stdout": json.dumps({"items": list(items)})}


class AnswersTest(unittest.TestCase):
    def test_verdicts_are_keyed_by_case_id(self) -> None:
        raw = response({"discussion_id": "a", "verdict": "author_action"})

        self.assertEqual({"a": "author_action"}, answers(raw, [case("a")]))

    def test_a_failed_call_answers_nothing(self) -> None:
        raw = {"returncode": 1, "stdout": json.dumps({"items": [{"discussion_id": "a"}]})}

        self.assertEqual({}, answers(raw, [case("a")]))

    def test_an_unparseable_response_answers_nothing(self) -> None:
        self.assertEqual({}, answers({"returncode": 0, "stdout": "sorry"}, [case("a")]))

    def test_a_response_without_items_answers_nothing(self) -> None:
        raw = {"returncode": 0, "stdout": json.dumps({"verdict": "author_action"})}

        self.assertEqual({}, answers(raw, [case("a")]))

    def test_an_id_that_was_not_asked_for_is_ignored(self) -> None:
        raw = response(
            {"discussion_id": "a", "verdict": "author_action"},
            {"discussion_id": "invented", "verdict": "author_action"},
        )

        self.assertEqual({"a": "author_action"}, answers(raw, [case("a")]))

    def test_an_unknown_verdict_is_not_an_answer(self) -> None:
        raw = response({"discussion_id": "a", "verdict": "maybe"})

        self.assertEqual({}, answers(raw, [case("a")]))

    def test_a_duplicated_id_drops_the_answer_it_already_had(self) -> None:
        raw = response(
            {"discussion_id": "a", "verdict": "author_action"},
            {"discussion_id": "a", "verdict": "no_author_action"},
            {"discussion_id": "b", "verdict": "no_author_action"},
        )

        # Production fails a discussion whose id comes back twice rather than
        # picking one, so neither answer can be trusted here either.
        self.assertEqual({"b": "no_author_action"}, answers(raw, [case("a"), case("b")]))


class RebuildTest(unittest.TestCase):
    def test_runs_that_agree_are_stable(self) -> None:
        rebuilt = rebuild(payload(case("a")), [{"a": "author_action"}] * 3, "model")

        self.assertEqual("stable", rebuilt["cases"][0]["stability"])
        self.assertEqual("substantive", rebuilt["cases"][0]["baseline"])

    def test_runs_that_disagree_are_flaky_without_a_baseline(self) -> None:
        trials = [{"a": "author_action"}, {"a": "no_author_action"}]
        rebuilt = rebuild(payload(case("a")), trials, "model")

        self.assertEqual("flaky", rebuilt["cases"][0]["stability"])
        self.assertIsNone(rebuilt["cases"][0]["baseline"])

    def test_one_unanswered_run_leaves_a_case_unobserved(self) -> None:
        trials = [{"a": "author_action"}, {}, {"a": "author_action"}]
        rebuilt = rebuild(payload(case("a")), trials, "model")

        # Agreement among the runs that answered is not evidence the classifier
        # is stable on a case it sometimes drops.
        self.assertEqual("unobserved", rebuilt["cases"][0]["stability"])
        self.assertIsNone(rebuilt["cases"][0]["baseline"])
        self.assertEqual([None], rebuilt["cases"][0]["observed_actions"][1:2])

    def test_a_human_decision_survives_a_disagreeing_measurement(self) -> None:
        cases = payload(case("a", adjudicated="noise"))
        rebuilt = rebuild(cases, [{"a": "author_action"}] * 3, "model")

        self.assertEqual("noise", rebuilt["cases"][0]["adjudicated"])
        self.assertEqual("substantive", rebuilt["cases"][0]["baseline"])

    def test_counts_and_configuration_describe_the_new_measurement(self) -> None:
        cases = payload(
            case("a", adjudicated="noise"),
            case("b"),
            case("c"),
        )
        trials = [
            {"a": "author_action", "b": "author_action", "c": "author_action"},
            {"a": "author_action", "b": "no_author_action"},
        ]
        rebuilt = rebuild(cases, trials, "measured-model")

        self.assertEqual(
            {"cases": 3, "stable": 1, "flaky": 1, "unobserved": 1, "adjudicated": 1},
            rebuilt["counts"],
        )
        self.assertEqual("measured-model", rebuilt["baseline_configuration"]["model"])
        self.assertEqual(2, rebuilt["baseline_configuration"]["runs"])
        self.assertEqual(
            regenerate_baseline.PROMPT, rebuilt["baseline_configuration"]["prompt"]
        )


class RunBatchCachingTest(unittest.TestCase):
    def setUp(self) -> None:
        cache = tempfile.TemporaryDirectory()
        self.addCleanup(cache.cleanup)
        self.cache = Path(cache.name)
        patcher = patch.object(regenerate_baseline, "CACHE_DIR", self.cache)
        patcher.start()
        self.addCleanup(patcher.stop)

    def entries(self) -> list[Path]:
        return list(self.cache.iterdir())

    def run_with(self, proc) -> dict:
        with patch.object(regenerate_baseline.classification, "run_copilot", proc):
            return run_batch([case("a")], "model", "salt")

    def test_a_successful_call_is_cached(self) -> None:
        raw = self.run_with(lambda prompt, model: _Completed(0, "{}"))

        self.assertEqual(0, raw["returncode"])
        self.assertEqual(1, len(self.entries()))

    def test_a_failed_call_is_not_cached(self) -> None:
        # Caching a failure would make every later run replay it instead of
        # retrying the call.
        raw = self.run_with(lambda prompt, model: _Completed(1, ""))

        self.assertEqual(1, raw["returncode"])
        self.assertEqual([], self.entries())

    def test_a_raising_call_is_not_cached(self) -> None:
        def explode(prompt: str, model: str):
            raise RuntimeError("throttled")

        raw = self.run_with(explode)

        self.assertIn("throttled", raw["error"])
        self.assertEqual([], self.entries())


class _Completed:
    def __init__(self, returncode: int, stdout: str) -> None:
        self.returncode = returncode
        self.stdout = stdout


if __name__ == "__main__":
    unittest.main()
