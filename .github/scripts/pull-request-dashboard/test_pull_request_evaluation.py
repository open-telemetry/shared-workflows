from __future__ import annotations

from dataclasses import FrozenInstanceError
import unittest
from unittest.mock import patch

from dashboard_contracts import (
    DashboardRoute,
    EvaluationFailure,
    EvaluationSuccess,
)
from dashboard_test_support import stored_dashboard_result
from discussion_lifecycle import resolve_discussions
from github_cli import TransientGhError
from pull_request_evaluation import (
    PullRequestEvaluationConfig,
    PullRequestEvaluationInput,
    evaluate_pull_request,
)


def evaluation_config() -> PullRequestEvaluationConfig:
    return PullRequestEvaluationConfig(
        repo="owner/repo",
        owner="owner",
        repo_name="repo",
        approver_logins=frozenset({"reviewer"}),
        classifier_model="model",
        required_approvals=1,
        non_blocking_check_patterns=("optional-*",),
        require_clean_copilot_review_branches=frozenset({"main"}),
    )


def raw_pr(
    *,
    state: str = "OPEN",
    draft: bool = False,
    author: str = "author",
) -> dict[str, object]:
    return {
        "summary": {"number": 7, "author": {"login": author}},
        "pr": {
            "id": "PR_7",
            "state": state,
            "isDraft": draft,
            "title": "Evaluation contract",
            "url": "https://example.test/pull/7",
            "author": {"login": author},
            "assignees": [],
            "mergeStateStatus": "CLEAN",
            "mergeable": "MERGEABLE",
            "createdAt": "2026-08-16T07:00:00Z",
            "updatedAt": "2026-08-16T08:00:00Z",
            "headRefOid": "abcdef123456",
            "baseRefName": "feature",
        },
        "commits": [],
        "issue_comments": [],
        "review_comments": [],
        "reviews": [],
        "review_threads": [],
        "review_requests": [],
        "checks": [],
        "non_blocking_check_failures": [],
    }


class PullRequestEvaluationContractTest(unittest.TestCase):
    def test_inputs_are_frozen(self) -> None:
        config = evaluation_config()
        source = PullRequestEvaluationInput({"number": 7})

        with self.assertRaises(FrozenInstanceError):
            config.required_approvals = 2  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            source.previous_result = stored_dashboard_result()  # type: ignore[misc]

    @patch(
        "pull_request_evaluation.classify_discussion_domains",
        return_value=([], [], []),
    )
    @patch("pull_request_evaluation._fetch_pr_raw")
    def test_success_uses_the_effective_copilot_author(
        self,
        fetch_raw,
        _classify,
    ) -> None:
        raw = raw_pr(author="app/copilot-swe-agent")
        raw["pr"]["assignees"] = [{"login": "human-author"}]
        fetch_raw.return_value = raw

        result = evaluate_pull_request(
            evaluation_config(),
            PullRequestEvaluationInput({"number": 7}),
        )

        self.assertIsNotNone(result)
        self.assertIsInstance(result, EvaluationSuccess)
        assert isinstance(result, EvaluationSuccess)
        self.assertEqual("human-author", result.facts.author)
        self.assertEqual(7, result.pr_number)
        self.assertEqual("Evaluation contract", result.pr_title)
        self.assertEqual("https://example.test/pull/7", result.pr_url)

    @patch(
        "pull_request_evaluation.resolve_discussions",
        wraps=resolve_discussions,
    )
    @patch(
        "pull_request_evaluation.classify_discussion_domains",
        return_value=([], [], []),
    )
    @patch("pull_request_evaluation._fetch_pr_raw")
    def test_cached_top_level_history_reaches_the_discussion_lifecycle(
        self,
        fetch_raw,
        _classify,
        resolve,
    ) -> None:
        fetch_raw.return_value = raw_pr()
        history = {
            "pr-review-456": {"evidence": {"commit": "2026-08-16T07:30:00Z"}},
        }

        result = evaluate_pull_request(
            evaluation_config(),
            PullRequestEvaluationInput(
                {"number": 7},
                previous_result=stored_dashboard_result(
                    7,
                    top_level_history=history,
                ),
            ),
        )

        self.assertIsNotNone(result)
        assert result is not None
        resolve.assert_called_once()
        self.assertEqual(history, dict(resolve.call_args.args[2]))

    def test_closed_and_draft_pull_requests_are_not_results(self) -> None:
        for name, raw in (
            ("closed", raw_pr(state="CLOSED")),
            ("draft", raw_pr(draft=True)),
        ):
            with self.subTest(name=name), patch(
                "pull_request_evaluation._fetch_pr_raw",
                return_value=raw,
            ):
                self.assertIsNone(
                    evaluate_pull_request(
                        evaluation_config(),
                        PullRequestEvaluationInput({"number": 7}),
                    )
                )

    def test_transient_github_failure_has_the_stable_error_shape(self) -> None:
        error = TransientGhError("temporary")
        with patch(
            "pull_request_evaluation._fetch_pr_raw",
            side_effect=error,
        ):
            result = evaluate_pull_request(
                evaluation_config(),
                PullRequestEvaluationInput({"number": 7}),
            )

        self.assertIsInstance(result, EvaluationFailure)
        assert isinstance(result, EvaluationFailure)
        self.assertEqual(7, result.pr_number)
        self.assertIs(result.route, DashboardRoute.TRANSIENT_FAILURE)
        self.assertEqual(repr(error), result.error)
        self.assertIsNone(result.facts)
        self.assertEqual((), result.diagnostics.review_threads)

    def test_unexpected_failure_is_contained_and_logged(self) -> None:
        error = ValueError("broken")
        with (
            patch(
                "pull_request_evaluation._fetch_pr_raw",
                side_effect=error,
            ),
            patch("pull_request_evaluation.traceback.print_exc") as print_exc,
        ):
            result = evaluate_pull_request(
                evaluation_config(),
                PullRequestEvaluationInput({"number": 7}),
            )

        self.assertIsInstance(result, EvaluationFailure)
        assert isinstance(result, EvaluationFailure)
        self.assertIs(DashboardRoute.UNKNOWN, result.route)
        self.assertEqual(repr(error), result.error)
        print_exc.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
