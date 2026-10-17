from __future__ import annotations

from dataclasses import replace
import unittest
from unittest.mock import patch

from classification_test_support import FakeClassificationOperation
from copilot_review import copilot_review_status, open_copilot_finding_urls
from copilot_review_delivery import deliver_copilot_review_findings
from copilot_review_findings import pending_copilot_summary_findings
from dashboard_status import DASHBOARD_APP_SLUG
from dashboard_contracts import DashboardRoute, EvaluationSuccess
from dashboard_test_support import (
    actor,
    dashboard_facts,
    dashboard_state,
    pull_request_metadata,
    pull_request_source,
    review_source,
    review_thread,
    review_thread_comment,
    stored_dashboard_result,
)
from discussion_lifecycle import DiscussionInput, prepare_discussions
from pull_request_activity import reviewer_actor_login
from pull_request_evaluation import (
    PullRequestEvaluationConfig,
    PullRequestEvaluationInput,
    evaluate_pull_request,
)


HEAD = "a" * 40
REVIEW_URL = "https://github.com/owner/repo/pull/7#pullrequestreview-42"
FINDING = """<details>
<summary><picture><source srcset="medium.svg"><img alt="Medium severity"></picture> Restrict parsing to authority</summary>

`src/\u200bParser.java:91`

[General] The parser searches the database path and query too.

Keep the helper input inside the authority.
</details>"""


def overview(*items: str) -> str:
    return (
        "<!-- ccr-overview-v2 -->\n\n**No open issues**\n\n"
        f"<details><summary><strong>{len(items)} previously missed "
        f"{'issue' if len(items) == 1 else 'issues'}</strong></summary>\n\n"
        "In code that hasn't changed since last review\n\n"
        + "\n".join(items)
        + "\n</details>\n\n"
        "<details><summary><strong>1 resolved since last review</strong></summary>\n"
        "- A fixed finding\n</details>"
    )


def missed_review(**changes):
    values = {
        "database_id": 42,
        "commit_id": HEAD,
        "url": REVIEW_URL,
        "actor": actor("copilot-pull-request-reviewer[bot]"),
        "body": overview(FINDING),
    }
    values.update(changes)
    return review_source(**values)


def copied_thread(finding, **changes):
    return review_thread(
        comments=(review_thread_comment(
            actor=actor(f"{DASHBOARD_APP_SLUG}[bot]"),
            body=finding.comment_body(),
        ),),
        **changes,
    )


class CopilotSummaryFindingsTest(unittest.TestCase):
    def test_extracts_the_summary_only_finding_and_preserves_its_text(self) -> None:
        review = missed_review()
        findings = pending_copilot_summary_findings((review,), HEAD, ())

        self.assertEqual(1, len(findings))
        finding = findings[0]
        self.assertEqual("src/Parser.java", finding.path)
        self.assertEqual(91, finding.line)
        self.assertEqual("Restrict parsing to authority", finding.title)
        self.assertEqual("Medium severity", finding.severity)
        self.assertEqual(
            "[General] The parser searches the database path and query too.\n\n"
            "Keep the helper input inside the authority.",
            finding.body,
        )
        body = finding.comment_body()
        self.assertIn(finding.body, body)
        self.assertIn("Medium severity", body)
        self.assertIn(f"[Copilot's review]({REVIEW_URL})", body)
        self.assertIn("Reply here to discuss or reject it", body)
        self.assertEqual(
            (True, False, True),
            copilot_review_status((review,), HEAD, ()),
        )
        self.assertEqual((REVIEW_URL,), open_copilot_finding_urls((), (review,), HEAD))

    def test_extracts_multiple_findings_without_the_resolved_section(self) -> None:
        second = FINDING.replace("authority", "the host").replace(":91", ":100")
        review = missed_review(body=overview(FINDING, second))

        findings = pending_copilot_summary_findings((review,), HEAD, ())

        self.assertEqual([91, 100], [finding.line for finding in findings])
        self.assertNotEqual(findings[0].identity, findings[1].identity)

    def test_copies_each_identity_once(self) -> None:
        review = missed_review(body=overview(FINDING, FINDING))

        self.assertEqual(1, len(pending_copilot_summary_findings((review,), HEAD, ())))

    def test_html_entities_are_decoded_in_the_title(self) -> None:
        review = missed_review(body=overview(FINDING.replace("authority", "host &amp; port")))

        finding, = pending_copilot_summary_findings((review,), HEAD, ())

        self.assertEqual("Restrict parsing to host & port", finding.title)

    def test_uses_only_the_latest_current_head_copilot_overview(self) -> None:
        old = missed_review()
        clean = replace(
            old, database_id=43, submitted_at="2026-08-17T07:00:00Z", body=overview(),
        )
        for review in (
            clean,
            replace(old, actor=actor("human")),
            replace(old, commit_id="old-head"),
            replace(old, state="PENDING"),
            replace(old, state="DISMISSED"),
            replace(old, body="Previously missed issues without an overview."),
        ):
            with self.subTest(review=review):
                self.assertEqual((), pending_copilot_summary_findings((review,), HEAD, ()))
        self.assertEqual((), pending_copilot_summary_findings((clean, old), HEAD, ()))
        self.assertEqual((), pending_copilot_summary_findings((old,), "", ()))

    def test_existing_copies_suppress_reposting_even_after_resolution(self) -> None:
        review = missed_review()
        finding, = pending_copilot_summary_findings((review,), HEAD, ())
        for resolved, outdated in ((False, False), (True, False), (False, True)):
            with self.subTest(resolved=resolved, outdated=outdated):
                thread = copied_thread(
                    finding, is_resolved=resolved, is_outdated=outdated,
                )
                self.assertEqual(
                    (), pending_copilot_summary_findings((review,), HEAD, (thread,)),
                )
                self.assertEqual(
                    (True, False, not resolved and not outdated),
                    copilot_review_status((review,), HEAD, (thread,)),
                )
        repeated = replace(review, database_id=50, url=REVIEW_URL + "-new")
        self.assertEqual(
            (), pending_copilot_summary_findings((repeated,), HEAD, (copied_thread(finding),)),
        )
        next_head = replace(review, commit_id="b" * 40)
        self.assertEqual(
            1,
            len(pending_copilot_summary_findings(
                (next_head,), next_head.commit_id, (copied_thread(finding),),
            )),
        )

    def test_a_human_cannot_spoof_a_promoted_copilot_finding(self) -> None:
        review = missed_review()
        finding, = pending_copilot_summary_findings((review,), HEAD, ())
        spoof = review_thread(comments=(review_thread_comment(
            actor=actor("human"), body=finding.comment_body(),
        ),))

        self.assertEqual(
            (finding,), pending_copilot_summary_findings((review,), HEAD, (spoof,)),
        )
        self.assertEqual("human", reviewer_actor_login(spoof.comments[0].actor, finding.comment_body()))

    def test_promoted_threads_use_copilot_lifecycle_and_reviewer_identity(self) -> None:
        finding, = pending_copilot_summary_findings((missed_review(),), HEAD, ())
        thread = copied_thread(finding)
        thread = replace(thread, comments=(*thread.comments, review_thread_comment(
            actor=actor("author"), body="This is intentional, so I disagree.",
            created_at="2026-08-17T07:00:00Z",
        )))

        prepared = prepare_discussions(DiscussionInput(
            (thread,), (), "author", frozenset(), "no",
        ))

        self.assertTrue(prepared.review_threads[0]["strict_author_action"])
        self.assertEqual(
            "copilot-pull-request-reviewer[bot]",
            prepared.review_threads[0]["comments"][0]["actor"],
        )
        self.assertEqual(
            (thread.comments[0].url,), open_copilot_finding_urls((thread,)),
        )

    def test_unsupported_or_incomplete_findings_fail_explicitly(self) -> None:
        invalid = (
            overview(FINDING).replace("1 previously", "2 previously"),
            overview(FINDING).replace("1 previously", "Several previously"),
            overview(FINDING).replace("src/\u200bParser.java:91", "no location"),
            overview(FINDING).replace(":91`", ":0`"),
            overview(FINDING).replace("src/\u200bParser.java", "../Parser.java"),
            overview(FINDING).replace("src/\u200bParser.java", "/Parser.java"),
            overview(FINDING).replace("</details>", "", 1),
        )
        for body in invalid:
            with self.subTest(body=body):
                with self.assertRaisesRegex(ValueError, "Copilot review 42"):
                    pending_copilot_summary_findings((missed_review(body=body),), HEAD, ())


class CopilotSummaryFindingDeliveryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.review = missed_review()
        self.source = pull_request_source(
            pull_request=pull_request_metadata(head_sha=HEAD),
            reviews=(self.review,),
        )
        self.load = self.enterContext(patch(
            "copilot_review_delivery.load_dashboard_state_cache",
            return_value=dashboard_state(stored_dashboard_result(
                7, facts=dashboard_facts(
                    head_sha=HEAD, copilot_review_findings_to_promote=True,
                ),
            )),
        ))
        self.fetch = self.enterContext(patch(
            "copilot_review_delivery.fetch_pull_request_source",
            return_value=self.source,
        ))
        self.locations = self.enterContext(patch(
            "copilot_review_delivery.fetch_pr_review_comment_locations",
            return_value={"src/Parser.java": frozenset({91})},
        ))
        self.create = self.enterContext(patch(
            "copilot_review_delivery.create_pr_review_comment",
        ))

    def test_posts_an_attributed_inline_comment(self) -> None:
        self.assertEqual([], deliver_copilot_review_findings("owner/repo", 7))

        finding, = pending_copilot_summary_findings((self.review,), HEAD, ())
        self.create.assert_called_once_with(
            "owner/repo", 7, HEAD, "src/Parser.java", 91, finding.comment_body(),
        )
        self.fetch.assert_called_once_with(
            "owner/repo", "owner", "repo", 7, include_commits=False,
        )

    def test_uses_a_file_comment_when_the_line_is_not_in_the_diff(self) -> None:
        self.locations.return_value = {"src/Parser.java": frozenset()}

        self.assertEqual([], deliver_copilot_review_findings("owner/repo"))

        self.assertIsNone(self.create.call_args.args[4])
        self.assertIn("src/Parser.java:91", self.create.call_args.args[5])

    def test_unrelated_or_unprepared_prs_do_not_fetch_or_post(self) -> None:
        self.assertEqual([], deliver_copilot_review_findings("owner/repo", 8))
        self.load.return_value = dashboard_state(stored_dashboard_result(7))
        self.assertEqual([], deliver_copilot_review_findings("owner/repo"))
        self.load.return_value = None
        self.assertEqual([], deliver_copilot_review_findings("owner/repo"))
        self.fetch.assert_not_called()
        self.create.assert_not_called()

    def test_changed_heads_drafts_and_closed_prs_are_not_posted(self) -> None:
        for metadata in (
            replace(self.source.pull_request, head_sha="b" * 40),
            replace(self.source.pull_request, is_draft=True),
            replace(self.source.pull_request, state="CLOSED"),
            replace(self.source.pull_request, head_sha=""),
        ):
            with self.subTest(metadata=metadata):
                self.fetch.return_value = replace(self.source, pull_request=metadata)
                self.assertEqual([], deliver_copilot_review_findings("owner/repo"))
        self.create.assert_not_called()
        self.locations.assert_not_called()

    def test_retry_reads_github_to_avoid_reposting_an_existing_copy(self) -> None:
        finding, = pending_copilot_summary_findings((self.review,), HEAD, ())
        self.create.side_effect = RuntimeError("connection lost after posting")
        self.assertEqual(
            ["PR #7: connection lost after posting"],
            deliver_copilot_review_findings("owner/repo"),
        )
        self.fetch.return_value = replace(
            self.source, review_threads=(copied_thread(finding, is_resolved=True),),
        )

        self.assertEqual([], deliver_copilot_review_findings("owner/repo"))
        self.assertEqual(1, self.create.call_count)

    def test_partial_delivery_retries_only_the_remaining_finding(self) -> None:
        review = missed_review(body=overview(
            FINDING, FINDING.replace("authority", "the host").replace(":91", ":100"),
        ))
        self.fetch.return_value = replace(self.source, reviews=(review,))
        self.locations.return_value = {"src/Parser.java": frozenset({91, 100})}
        self.create.side_effect = [None, RuntimeError("unavailable")]

        self.assertEqual(
            ["PR #7: unavailable"], deliver_copilot_review_findings("owner/repo"),
        )
        first, second = pending_copilot_summary_findings((review,), HEAD, ())
        self.fetch.return_value = replace(
            self.source, reviews=(review,), review_threads=(copied_thread(first),),
        )
        self.create.side_effect = None

        self.assertEqual([], deliver_copilot_review_findings("owner/repo"))
        self.assertEqual(3, self.create.call_count)
        self.assertEqual(second.comment_body(), self.create.call_args.args[-1])

    def test_reports_a_finding_outside_the_pr_diff_without_posting(self) -> None:
        self.locations.return_value = {}

        errors = deliver_copilot_review_findings("owner/repo")

        self.assertEqual(1, len(errors))
        self.assertIn("outside PR #7's diff", errors[0])
        self.create.assert_not_called()

    def test_failures_do_not_prevent_other_prs_from_being_processed(self) -> None:
        result = self.load.return_value.results[0]
        self.load.return_value = dashboard_state(result, replace(result, pr_number=8))
        self.fetch.side_effect = [RuntimeError("unavailable"), self.source]

        self.assertEqual(
            ["PR #7: unavailable"], deliver_copilot_review_findings("owner/repo"),
        )
        self.assertEqual(8, self.create.call_args.args[1])


class CopilotSummaryFindingEvaluationTest(unittest.IsolatedAsyncioTestCase):
    @patch("pull_request_evaluation.fetch_pull_request_source")
    async def test_summary_only_findings_hold_the_gate_until_the_copy_is_resolved(
        self, fetch_source,
    ) -> None:
        review = missed_review()
        finding, = pending_copilot_summary_findings((review,), HEAD, ())
        config = PullRequestEvaluationConfig(
            repo="owner/repo",
            owner="owner",
            repo_name="repo",
            approver_logins=frozenset({"reviewer"}),
            classifier_model="model",
            required_approvals=1,
            require_clean_copilot_review_branches=frozenset({"main"}),
        )
        for resolved in (False, True):
            with self.subTest(resolved=resolved):
                fetch_source.return_value = pull_request_source(
                    pull_request=pull_request_metadata(head_sha=HEAD),
                    reviews=(review,),
                    review_threads=(
                        (copied_thread(finding, is_resolved=True),)
                        if resolved else ()
                    ),
                )

                result = await evaluate_pull_request(
                    config, PullRequestEvaluationInput(7), FakeClassificationOperation(),
                )

                self.assertIsInstance(result, EvaluationSuccess)
                assert isinstance(result, EvaluationSuccess)
                self.assertEqual(
                    DashboardRoute.APPROVER if resolved else DashboardRoute.AUTHOR,
                    result.route,
                )
                self.assertEqual(not resolved, result.facts.copilot_review_needed)
                self.assertEqual(not resolved, result.facts.copilot_review_findings_to_promote)
                self.assertEqual(
                    () if resolved else (REVIEW_URL,),
                    result.facts.author_action_review_thread_urls,
                )
