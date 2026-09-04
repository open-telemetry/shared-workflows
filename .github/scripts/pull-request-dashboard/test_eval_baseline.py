import contextlib
from datetime import datetime
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from threading import Barrier, BrokenBarrierError, Lock
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent / "eval"))

from classification_policy import RawModelResponse  # noqa: E402
from classification_test_support import FakeModelRunner  # noqa: E402
import regenerate_baseline  # noqa: E402
from regenerate_baseline import answers, measure, rebuild, run_batch  # noqa: E402


def case(case_id: str, *, adjudicated_label=None) -> dict:
    return {
        "id": case_id,
        "repo": "repo",
        "pull_request": 1,
        "requester": "reviewer",
        "pr_author": "author",
        "review_state": None,
        "root_timestamp": "2026-01-01T00:00:00Z",
        "body": "body",
        "role": "scored",
        "stability": "stable",
        "recorded_label": "no_author_action",
        "adjudicated_label": adjudicated_label,
        "run_actions": [],
        "run_labels": [],
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

        self.assertEqual("scored", rebuilt["cases"][0]["role"])
        self.assertEqual("stable", rebuilt["cases"][0]["stability"])
        self.assertEqual("author_action", rebuilt["cases"][0]["recorded_label"])

    def test_runs_that_disagree_are_flaky_without_a_recorded_label(self) -> None:
        trials = [{"a": "author_action"}, {"a": "no_author_action"}]
        rebuilt = rebuild(payload(case("a")), trials, "model")

        self.assertEqual("scored", rebuilt["cases"][0]["role"])
        self.assertEqual("flaky", rebuilt["cases"][0]["stability"])
        self.assertIsNone(rebuilt["cases"][0]["recorded_label"])

    def test_one_unanswered_run_leaves_a_case_as_context(self) -> None:
        trials = [{"a": "author_action"}, {}, {"a": "author_action"}]
        rebuilt = rebuild(payload(case("a")), trials, "model")

        # Agreement among the runs that answered is not evidence the classifier
        # is stable on a case it sometimes drops.
        self.assertEqual("context", rebuilt["cases"][0]["role"])
        self.assertIsNone(rebuilt["cases"][0]["stability"])
        self.assertIsNone(rebuilt["cases"][0]["recorded_label"])
        self.assertEqual([None], rebuilt["cases"][0]["run_actions"][1:2])

    def test_a_human_decision_survives_a_disagreeing_measurement(self) -> None:
        cases = payload(case("a", adjudicated_label="no_author_action"))
        rebuilt = rebuild(cases, [{"a": "author_action"}] * 3, "model")

        self.assertEqual("no_author_action", rebuilt["cases"][0]["adjudicated_label"])
        self.assertEqual("author_action", rebuilt["cases"][0]["recorded_label"])

    def test_counts_and_configuration_describe_the_new_measurement(self) -> None:
        cases = payload(
            case("a", adjudicated_label="no_author_action"),
            case("b"),
            case("c"),
        )
        trials = [
            {"a": "author_action", "b": "author_action", "c": "author_action"},
            {"a": "author_action", "b": "no_author_action"},
        ]
        rebuilt = rebuild(cases, trials, "measured-model")

        self.assertEqual(
            {
                "cases": 3,
                "scored": 2,
                "context": 1,
                "stable": 1,
                "flaky": 1,
                "adjudicated": 1,
            },
            rebuilt["counts"],
        )
        self.assertEqual("measured-model", rebuilt["baseline_configuration"]["model"])
        self.assertEqual(2, rebuilt["baseline_configuration"]["runs"])
        self.assertEqual(
            regenerate_baseline.PROMPT, rebuilt["baseline_configuration"]["prompt"]
        )
        self.assertEqual(
            rebuilt["baseline_generated_at"],
            rebuilt["measurements_updated_at"],
        )
        self.assertNotIn("generated_at", rebuilt)

    def test_rebuild_uses_one_measurement_date(self) -> None:
        with patch.object(regenerate_baseline, "datetime") as clock:
            clock.now.side_effect = [
                datetime.fromisoformat("2026-09-02T23:59:59+00:00"),
                datetime.fromisoformat("2026-09-03T00:00:00+00:00"),
            ]

            rebuilt = rebuild(
                payload(case("a")),
                [{"a": "author_action"}],
                "model",
            )

        self.assertEqual("2026-09-02", rebuilt["baseline_generated_at"])
        self.assertEqual("2026-09-02", rebuilt["measurements_updated_at"])
        self.assertEqual("2026-09-02", rebuilt["cases"][0]["measurement_date"])
        clock.now.assert_called_once_with(regenerate_baseline.UTC)


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

    def run_with(self, response: RawModelResponse | Exception) -> dict:
        return run_batch(
            [case("a")],
            "model",
            "salt",
            FakeModelRunner([response]),
        )

    def test_a_successful_call_is_cached(self) -> None:
        raw = self.run_with(RawModelResponse(0, "{}"))

        self.assertEqual(0, raw["returncode"])
        self.assertEqual(1, len(self.entries()))

    def test_a_failed_call_is_not_cached(self) -> None:
        # Caching a failure would make every later run replay it instead of
        # retrying the call.
        raw = self.run_with(RawModelResponse(1, ""))

        self.assertEqual(1, raw["returncode"])
        self.assertEqual([], self.entries())

    def test_a_raising_call_is_not_cached(self) -> None:
        raw = self.run_with(RuntimeError("throttled"))

        self.assertIn("throttled", raw["error"])
        self.assertEqual([], self.entries())


class MeasureTest(unittest.TestCase):
    def test_injected_runner_calls_do_not_overlap(self) -> None:
        cache = tempfile.TemporaryDirectory()
        self.addCleanup(cache.cleanup)
        barrier = Barrier(2)
        state_lock = Lock()
        state = {"active": 0, "calls": 0, "overlap": False}

        class Runner:
            def run(self, _request) -> RawModelResponse:
                with state_lock:
                    state["active"] += 1
                    state["calls"] += 1
                    if state["active"] > 1:
                        state["overlap"] = True
                try:
                    barrier.wait(timeout=0.2)
                except BrokenBarrierError:
                    pass
                finally:
                    with state_lock:
                        state["active"] -= 1
                return RawModelResponse(0, '{"items":[]}', "")

        with (
            patch.object(regenerate_baseline, "CACHE_DIR", Path(cache.name)),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            measure(
                [case(f"case-{index}") for index in range(20)],
                "model",
                1,
                2,
                Runner(),
            )

        self.assertEqual(state["calls"], 2)
        self.assertFalse(state["overlap"])


if __name__ == "__main__":
    unittest.main()
