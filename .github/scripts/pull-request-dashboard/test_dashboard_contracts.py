from __future__ import annotations

from dataclasses import FrozenInstanceError
import unittest

from dashboard_contracts import (
    DashboardCommandReply,
    DashboardRoute,
    DashboardState,
    EvaluationDraft,
    EvaluationFailure,
    StoredDashboardResult,
)
from dashboard_test_support import (
    dashboard_facts,
    evaluation_failure,
    evaluation_success,
    stored_dashboard_result,
)


class DashboardCommandReplyContractTest(unittest.TestCase):
    def test_routed_reply_rejects_failure_routes(self) -> None:
        for route in (
            DashboardRoute.UNKNOWN,
            DashboardRoute.TRANSIENT_FAILURE,
        ):
            with self.subTest(route=route):
                with self.assertRaisesRegex(
                    ValueError,
                    "routed dashboard command replies require a successful route",
                ):
                    DashboardCommandReply(1, "routed", route=route)

    def test_non_routed_reply_rejects_persistent_handoff(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "only routed replies may persist a reviewer handoff",
        ):
            DashboardCommandReply(
                1,
                "unauthorized",
                persistent_handoff=True,
            )

    def test_persistent_handoff_reply_rejects_author_route(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "persistent reviewer handoff replies require a reviewer route",
        ):
            DashboardCommandReply(
                1,
                "routed",
                route=DashboardRoute.AUTHOR,
                persistent_handoff=True,
            )


class EvaluationResultContractTest(unittest.TestCase):
    def test_success_rejects_failure_routes(self) -> None:
        for route in (
            DashboardRoute.UNKNOWN,
            DashboardRoute.TRANSIENT_FAILURE,
        ):
            with self.subTest(route=route):
                with self.assertRaisesRegex(
                    ValueError,
                    "successful evaluations require a successful route",
                ):
                    evaluation_success(route=route)

    def test_failure_rejects_success_routes_and_empty_errors(self) -> None:
        for route in (
            DashboardRoute.AUTHOR,
            DashboardRoute.APPROVER,
            DashboardRoute.MAINTAINER,
        ):
            with self.subTest(route=route):
                with self.assertRaisesRegex(
                    ValueError,
                    "failed evaluations require a failure route",
                ):
                    evaluation_failure(route=route)

        with self.assertRaisesRegex(
            ValueError,
            "failed evaluations require an error",
        ):
            EvaluationFailure(
                pr_number=1,
                route=DashboardRoute.UNKNOWN,
                error="",
            )

    def test_evaluation_results_are_immutable(self) -> None:
        success = evaluation_success()
        failure = evaluation_failure()
        draft = EvaluationDraft(1, "Draft", "https://example.test/pull/1")

        with self.assertRaises(FrozenInstanceError):
            success.route = DashboardRoute.MAINTAINER  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            failure.error = "different"  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            draft.pr_title = "different"  # type: ignore[misc]


class StoredDashboardResultContractTest(unittest.TestCase):
    def test_projection_keeps_only_persisted_success_fields(self) -> None:
        facts = dashboard_facts(
            author="alice",
            head_sha="current-head",
            waiting_since="2026-08-16T12:00:00Z",
        )
        evaluated = evaluation_success(
            17,
            route=DashboardRoute.APPROVER,
            facts=facts,
            pr_title="Typed contracts",
            pr_url="https://github.com/open-telemetry/example/pull/17",
            pending_actions={"thread": {"action": "author"}},
            top_level_history={"feedback": {"timestamp": "2026-08-16T10:00:00Z"}},
        )

        stored = StoredDashboardResult.from_evaluation(evaluated)

        self.assertEqual(17, stored.pr_number)
        self.assertEqual(evaluated.pr_url, stored.pr_url)
        self.assertIs(DashboardRoute.APPROVER, stored.route)
        self.assertIs(facts, stored.facts)
        self.assertEqual(evaluated.top_level_history, stored.top_level_history)
        self.assertFalse(hasattr(stored, "pr_title"))
        self.assertFalse(hasattr(stored, "pending_actions"))
        self.assertFalse(hasattr(stored, "diagnostics"))

    def test_stored_results_reject_failure_routes(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "failed evaluations cannot be stored",
        ):
            stored_dashboard_result(route=DashboardRoute.UNKNOWN)

    def test_dashboard_state_rejects_duplicate_pull_requests(self) -> None:
        result = stored_dashboard_result(7)

        with self.assertRaisesRegex(
            ValueError,
            "dashboard state contains duplicate pull requests",
        ):
            DashboardState(results=(result, result))

    def test_dashboard_state_keeps_drafts_out_of_routed_results(self) -> None:
        result = stored_dashboard_result(7)

        with self.assertRaisesRegex(
            ValueError,
            "dashboard state cannot route a draft pull request",
        ):
            DashboardState(results=(result,), draft_pr_numbers=frozenset({7}))


if __name__ == "__main__":
    unittest.main()
