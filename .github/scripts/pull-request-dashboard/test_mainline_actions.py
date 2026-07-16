from __future__ import annotations

import json
import unittest
from subprocess import CompletedProcess
from unittest.mock import patch

from dashboard import (
    add_wait_age_facts,
    add_reviewers,
    build_discussion_state,
    build_dashboard_update_for_pr,
    group_pr_conversation_items,
    normalize_events,
    route_pr,
)
from classification import (
    classify_discussions,
    deterministic_classification_record,
    parse_discussion_decision,
    run_llm_for_mainline_batch,
)
from notifications import reviewer_logins_for_notification
from render import reviewer_icon


ROOT_TIMESTAMP = "2026-07-14T01:00:00Z"


def mainline_discussion(
    discussion_id: str,
    requester: str = "reviewer",
    source_kind: str | None = None,
    source_id: int | None = None,
) -> dict:
    discussion = {
        "discussion_id": discussion_id,
        "discussion_kind": "pr-conversation-item",
        "requester": requester,
        "root_timestamp": ROOT_TIMESTAMP,
        "comments": [],
    }
    if source_kind is not None:
        discussion["source_kind"] = source_kind
    if source_id is not None:
        discussion["source_id"] = source_id
    return discussion


def review_thread_discussion(discussion_id: str) -> dict:
    return {
        "discussion_id": discussion_id,
        "discussion_kind": "review-comment-thread",
        "comments": [],
    }


def classification(discussion_id: str, evidence_kind: str) -> dict:
    return {
        "discussion_id": discussion_id,
        "discussion_kind": "pr-conversation-item",
        "decision": {
            "discussion_action": "author",
            "evidence_kind": evidence_kind,
            "reason": "action requested",
        },
    }


def event(kind: str, timestamp: str, actor: str, actor_role: str, **values: object) -> dict:
    return {
        "kind": kind,
        "timestamp": timestamp,
        "actor": actor,
        "actor_role": actor_role,
        "body": values.pop("body", kind),
        "state": values.pop("state", None),
        "is_merge_from_base_by_non_author": False,
        **values,
    }


def stored_discussion_record(kind: str, timestamp: str) -> dict:
    return {
        "open": True,
        "waiting_on": "reviewer",
        "waiting_since": timestamp,
        "evidence": {"kind": kind, "timestamp": timestamp},
    }


class MainlineActionLedgerTest(unittest.TestCase):
    @patch("classification.print_copilot_otel_file")
    @patch("classification.subprocess.run")
    def test_mainline_batch_maps_ids_and_rejects_incomplete_results(
        self,
        run_copilot,
        _print_otel,
    ) -> None:
        run_copilot.return_value = CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps({
                "items": [
                    {
                        "discussion_id": "second",
                        "discussion_action": "none",
                        "evidence_kind": "none",
                        "reason": "No action",
                    },
                    {
                        "discussion_id": "first",
                        "discussion_action": "author",
                        "evidence_kind": "commit",
                        "reason": "Change requested",
                    },
                    {
                        "discussion_id": "second",
                        "discussion_action": "none",
                        "evidence_kind": "none",
                        "reason": "Duplicate",
                    },
                ],
            }),
            stderr="",
        )
        discussions = [
            mainline_discussion("first"),
            mainline_discussion("second"),
            mainline_discussion("missing"),
        ]

        records = run_llm_for_mainline_batch(discussions, "model")

        self.assertEqual([record["discussion_id"] for record in records], ["first", "second", "missing"])
        self.assertEqual(records[0]["decision"]["evidence_kind"], "commit")
        self.assertFalse(records[0]["failed"])
        self.assertTrue(records[1]["failed"])
        self.assertIn("duplicate discussion_id", records[1]["error"])
        self.assertTrue(records[2]["failed"])
        self.assertIn("this discussion_id", records[2]["error"])

    @patch("classification.save_classification_cache")
    @patch("classification.load_classification_cache", return_value={})
    @patch("classification.run_llm_for_mainline_batch")
    @patch("classification.run_llm_for_discussion")
    def test_mixed_discussions_use_kind_specific_classification_pipelines(
        self,
        run_inline,
        run_batch,
        _load_cache,
        save_cache,
    ) -> None:
        run_inline.side_effect = lambda discussion, _model: {
            "discussion_id": discussion["discussion_id"],
            "discussion_kind": discussion["discussion_kind"],
            "failed": False,
            "decision": {"discussion_action": "reviewer", "reason": "Author replied"},
        }
        run_batch.side_effect = lambda discussions, _model: [
            {
                "discussion_id": discussion["discussion_id"],
                "discussion_kind": discussion["discussion_kind"],
                "failed": False,
                "decision": {
                    "discussion_action": "author",
                    "evidence_kind": "reply",
                    "reason": "Reviewer asked a question",
                },
            }
            for discussion in discussions
        ]
        discussions = [
            mainline_discussion("top-level"),
            review_thread_discussion("inline"),
        ]

        classifications = classify_discussions(123, discussions, "model")

        self.assertEqual(run_inline.call_args.args[0]["discussion_id"], "inline")
        self.assertEqual(
            [discussion["discussion_id"] for discussion in run_batch.call_args.args[0]],
            ["top-level"],
        )
        self.assertEqual(
            [record["discussion_id"] for record in classifications],
            ["top-level", "inline"],
        )
        self.assertEqual(len(save_cache.call_args.args[1]), 2)

    @patch("classification.MAX_MAINLINE_CLASSIFICATIONS_PER_PR", 20)
    @patch("classification.MAINLINE_CLASSIFICATION_BATCH_SIZE", 10)
    @patch("classification.save_classification_cache")
    @patch("classification.load_classification_cache", return_value={})
    @patch("classification.run_llm_for_mainline_batch")
    def test_uncached_mainline_classification_is_batched_and_bounded(
        self,
        run_batch,
        _load_cache,
        save_cache,
    ) -> None:
        run_batch.side_effect = lambda discussions, _model: [
            {
                "discussion_id": discussion["discussion_id"],
                "discussion_kind": discussion["discussion_kind"],
                "failed": False,
                "decision": {
                    "discussion_action": "none",
                    "evidence_kind": "none",
                    "reason": "No action",
                },
            }
            for discussion in discussions
        ]
        discussions = [mainline_discussion(f"item-{index}") for index in range(23)]

        classifications = classify_discussions(123, discussions, "model")

        self.assertEqual(run_batch.call_count, 2)
        self.assertEqual([len(call.args[0]) for call in run_batch.call_args_list], [10, 10])
        self.assertEqual(len(classifications), 23)
        self.assertEqual(
            [record["decision"]["discussion_action"] for record in classifications],
            ["none"] * 20 + ["unclear"] * 3,
        )
        self.assertFalse(any(record.get("failed") for record in classifications))
        self.assertEqual(len(save_cache.call_args.args[1]), 20)

        _load_cache.return_value = save_cache.call_args.args[1]
        run_batch.reset_mock()

        classifications = classify_discussions(123, discussions, "model")

        self.assertEqual(run_batch.call_count, 1)
        self.assertEqual(len(run_batch.call_args.args[0]), 3)
        self.assertEqual(
            [record["decision"]["discussion_action"] for record in classifications],
            ["none"] * 23,
        )
        self.assertEqual(len(save_cache.call_args.args[1]), 23)

    def test_unclear_item_sets_reviewer_wait_age(self) -> None:
        discussions = [mainline_discussion("unclear")]
        classifications = [
            {
                "discussion_id": "unclear",
                "discussion_kind": "pr-conversation-item",
                "decision": {"discussion_action": "unclear", "reason": "ambiguous"},
            },
        ]
        discussion_state = {
            "unclear": {
                "open": True,
                "waiting_on": "reviewer",
                "waiting_since": ROOT_TIMESTAMP,
            },
        }
        facts = {
            "last_author_activity_at": "2026-07-14T04:00:00Z",
            "created_at": "2026-07-13T01:00:00Z",
        }

        add_wait_age_facts(facts, "approver", discussions, discussion_state)

        self.assertEqual(facts["waiting_since"], "2026-07-14T01:00:00+00:00")
        self.assertEqual(facts["waiting_age_basis"], "oldest_pending_thread")

    @patch("dashboard.build_pr_result")
    def test_dashboard_refresh_reuses_stored_discussion_state(self, build_result) -> None:
        build_result.return_value = None
        previous_state = {
            "pr-review-456": stored_discussion_record("commit", "2026-07-14T03:00:00Z"),
        }

        build_dashboard_update_for_pr(
            "open-telemetry/example",
            "open-telemetry",
            "example",
            {123},
            {"reviewer"},
            123,
            "model",
            1,
            {
                "prs": {
                    "123": {
                        "pr_number": 123,
                        "discussion_state": previous_state,
                    }
                }
            },
        )

        self.assertEqual(
            build_result.call_args.kwargs["previous_discussion_state"],
            previous_state,
        )

    def test_mainline_decision_requires_matching_action_and_evidence(self) -> None:
        _, reviewer_valid = parse_discussion_decision(
            '{"discussion_action":"reviewer","evidence_kind":"commit","reason":"invalid action"}',
            require_evidence_kind=True,
        )
        _, mismatched_valid = parse_discussion_decision(
            '{"discussion_action":"author","evidence_kind":"none","reason":"invalid evidence"}',
            require_evidence_kind=True,
        )
        external, external_valid = parse_discussion_decision(
            '{"discussion_action":"external","evidence_kind":"external","reason":"blocked elsewhere"}',
            require_evidence_kind=True,
        )

        self.assertFalse(reviewer_valid)
        self.assertFalse(mismatched_valid)
        self.assertTrue(external_valid)
        self.assertEqual(external["discussion_action"], "external")

    def test_changes_requested_forces_author_action(self) -> None:
        decision, valid = parse_discussion_decision(
            '{"discussion_action":"none","evidence_kind":"commit","reason":"code change requested"}',
            require_evidence_kind=True,
            forced_action="author",
        )
        _, invalid_evidence = parse_discussion_decision(
            '{"discussion_action":"external","evidence_kind":"external","reason":"invalid evidence"}',
            require_evidence_kind=True,
            forced_action="author",
        )

        self.assertTrue(valid)
        self.assertEqual(decision["discussion_action"], "author")
        self.assertEqual(decision["evidence_kind"], "commit")
        self.assertFalse(invalid_evidence)

    def test_top_level_comments_get_stable_individual_items(self) -> None:
        raw = {
            "issue_comments": [
                {
                    "id": 101,
                    "created_at": ROOT_TIMESTAMP,
                    "updated_at": ROOT_TIMESTAMP,
                    "user": {"login": "reviewer"},
                    "body": "Please update the code.",
                }
            ],
            "reviews": [
                {
                    "id": 202,
                    "submitted_at": "2026-07-14T02:00:00Z",
                    "updated_at": "2026-07-14T03:00:00Z",
                    "user": {"login": "reviewer"},
                    "state": "APPROVED",
                    "body": "Please update the PR description.",
                }
            ],
        }

        items = group_pr_conversation_items(raw, "author", {"reviewer"}, {"conflicts": "no"})

        self.assertEqual(
            [item["discussion_id"] for item in items],
            ["pr-issue-comment-101", "pr-review-202"],
        )
        self.assertEqual(items[1]["root_timestamp"], "2026-07-14T03:00:00Z")
        events = normalize_events(
            {
                "commits": [],
                "issue_comments": [],
                "review_comments": [],
                "reviews": raw["reviews"],
            },
            "author",
            {"reviewer"},
        )
        self.assertEqual(events[0]["timestamp"], "2026-07-14T02:00:00Z")

    def test_top_level_items_require_github_identity_and_requester(self) -> None:
        raw = {
            "issue_comments": [
                {
                    "created_at": ROOT_TIMESTAMP,
                    "updated_at": ROOT_TIMESTAMP,
                    "user": {"login": "reviewer"},
                    "body": "Missing comment id.",
                },
                {
                    "id": 101,
                    "created_at": ROOT_TIMESTAMP,
                    "updated_at": ROOT_TIMESTAMP,
                    "user": {},
                    "body": "Missing requester.",
                },
            ],
            "reviews": [],
        }

        self.assertEqual(
            group_pr_conversation_items(raw, "author", {"reviewer"}, {"conflicts": "no"}),
            [],
        )

    def test_resolved_conflict_review_body_is_not_an_action_item(self) -> None:
        raw = {
            "issue_comments": [],
            "reviews": [
                {
                    "id": 202,
                    "submitted_at": ROOT_TIMESTAMP,
                    "user": {"login": "reviewer"},
                    "state": "COMMENTED",
                    "body": "Please resolve the merge conflict.",
                }
            ],
        }

        self.assertEqual(
            group_pr_conversation_items(raw, "author", {"reviewer"}, {"conflicts": "no"}),
            [],
        )

    def test_changes_requested_conflict_review_remains_an_action_item(self) -> None:
        raw = {
            "issue_comments": [],
            "reviews": [
                {
                    "id": 202,
                    "submitted_at": ROOT_TIMESTAMP,
                    "user": {"login": "reviewer"},
                    "state": "CHANGES_REQUESTED",
                    "body": "Please resolve the merge conflict.",
                }
            ],
        }

        items = group_pr_conversation_items(
            raw,
            "author",
            {"reviewer"},
            {"conflicts": "no"},
        )

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["review_state"], "CHANGES_REQUESTED")

    def test_empty_changes_requested_review_is_an_author_action(self) -> None:
        raw = {
            "issue_comments": [],
            "reviews": [
                {
                    "id": 202,
                    "submitted_at": ROOT_TIMESTAMP,
                    "user": {"login": "reviewer"},
                    "state": "CHANGES_REQUESTED",
                    "body": "",
                }
            ],
        }

        items = group_pr_conversation_items(raw, "author", {"reviewer"}, {"conflicts": "no"})
        self.assertEqual(len(items), 1)
        record = deterministic_classification_record(items[0])
        assert record is not None

        self.assertEqual(
            record["decision"],
            {
                "discussion_action": "author",
                "evidence_kind": "commit",
                "reason": "Reviewer explicitly requested changes",
            },
        )

    def test_commit_advances_only_commit_action(self) -> None:
        discussions = [mainline_discussion("code"), mainline_discussion("description")]
        classifications = [
            classification("code", "commit"),
            classification("description", "description"),
        ]
        events = [event("commit", "2026-07-14T03:00:00Z", "author", "author")]

        discussion_state = build_discussion_state(discussions, classifications, events, {}, "author")

        self.assertEqual(discussion_state["code"]["waiting_on"], "reviewer")
        self.assertEqual(classifications[0]["decision"]["discussion_action"], "author")
        self.assertEqual(discussion_state["description"]["waiting_on"], "author")
        self.assertEqual(classifications[1]["decision"]["discussion_action"], "author")

    def test_author_reply_advances_only_reply_action(self) -> None:
        discussions = [
            mainline_discussion("code"),
            mainline_discussion("description"),
            mainline_discussion("reply"),
        ]
        classifications = [
            classification("code", "commit"),
            classification("description", "description"),
            classification("reply", "reply"),
        ]
        events = [
            event("issue-comment", "2026-07-14T03:00:00Z", "author", "author"),
        ]

        discussion_state = build_discussion_state(discussions, classifications, events, {}, "author")

        self.assertEqual(discussion_state["code"]["waiting_on"], "author")
        self.assertEqual(discussion_state["description"]["waiting_on"], "author")
        self.assertEqual(discussion_state["reply"]["waiting_on"], "reviewer")

    def test_maintainer_cherry_pick_uses_original_author_date(self) -> None:
        events = normalize_events(
            {
                "commits": [
                    {
                        "sha": "abcdef123456",
                        "author": {"login": "author"},
                        "committer": {"login": "maintainer"},
                        "commit": {
                            "author": {
                                "name": "Author",
                                "date": "2026-07-13T03:00:00Z",
                            },
                            "committer": {"date": "2026-07-14T03:00:00Z"},
                            "message": "Cherry-pick requested change",
                        },
                        "parents": [{}],
                    }
                ],
                "issue_comments": [],
                "review_comments": [],
                "reviews": [],
            },
            "author",
            {"reviewer"},
        )

        self.assertEqual(events[0]["actor"], "author")
        self.assertEqual(events[0]["timestamp"], "2026-07-13T03:00:00Z")

        classifications = [classification("code", "commit")]
        discussion_state = build_discussion_state(
            [mainline_discussion("code")],
            classifications,
            events,
            {},
            "author",
        )

        self.assertEqual(discussion_state["code"]["waiting_on"], "author")

    def test_cherry_pick_by_author_is_author_evidence(self) -> None:
        events = normalize_events(
            {
                "commits": [
                    {
                        "sha": "abcdef123456",
                        "author": {"login": "original-author"},
                        "committer": {"login": "author"},
                        "commit": {
                            "author": {
                                "name": "Original Author",
                                "date": "2026-07-13T03:00:00Z",
                            },
                            "committer": {"date": "2026-07-14T03:00:00Z"},
                            "message": "Cherry-pick requested change",
                        },
                        "parents": [{}],
                    }
                ],
                "issue_comments": [],
                "review_comments": [],
                "reviews": [],
            },
            "author",
            {"reviewer"},
        )

        self.assertEqual(events[0]["actor"], "author")
        self.assertEqual(events[0]["actor_role"], "author")
        self.assertEqual(events[0]["timestamp"], "2026-07-14T03:00:00Z")

    def test_author_commit_without_committer_date_uses_author_date(self) -> None:
        events = normalize_events(
            {
                "commits": [
                    {
                        "sha": "abcdef123456",
                        "author": {"login": "author"},
                        "committer": {"login": "author"},
                        "commit": {
                            "author": {
                                "name": "Author",
                                "date": "2026-07-14T03:00:00Z",
                            },
                            "committer": {},
                            "message": "Address requested change",
                        },
                        "parents": [{}],
                    }
                ],
                "issue_comments": [],
                "review_comments": [],
                "reviews": [],
            },
            "author",
            {"reviewer"},
        )

        self.assertEqual(events[0]["actor"], "author")
        self.assertEqual(events[0]["timestamp"], "2026-07-14T03:00:00Z")

    def test_description_edit_advances_description_action(self) -> None:
        discussions = [mainline_discussion("description")]
        classifications = [classification("description", "description")]
        metadata = {
            "lastEditedAt": "2026-07-14T03:00:00Z",
            "editor": {"login": "author"},
        }

        discussion_state = build_discussion_state(discussions, classifications, [], metadata, "author")

        self.assertEqual(discussion_state["description"]["waiting_on"], "reviewer")
        self.assertEqual(classifications[0]["decision"]["discussion_action"], "author")

    def test_description_evidence_survives_later_non_author_edit(self) -> None:
        discussions = [mainline_discussion("description")]
        first_classifications = [classification("description", "description")]
        first_state = build_discussion_state(
            discussions,
            first_classifications,
            [],
            {
                "lastEditedAt": "2026-07-14T03:00:00Z",
                "editor": {"login": "author"},
            },
            "author",
        )
        previous_state = {"description": first_state["description"]}
        classifications = [classification("description", "description")]

        discussion_state = build_discussion_state(
            discussions,
            classifications,
            [],
            {
                "lastEditedAt": "2026-07-14T04:00:00Z",
                "editor": {"login": "maintainer"},
            },
            "author",
            previous_state,
        )

        self.assertEqual(discussion_state["description"]["waiting_on"], "reviewer")
        self.assertEqual(
            discussion_state["description"]["evidence"]["timestamp"],
            "2026-07-14T03:00:00Z",
        )

    def test_description_evidence_survives_removed_confirmation(self) -> None:
        discussions = [mainline_discussion("description")]
        classifications = [classification("description", "description")]
        previous_state = {
            "description": stored_discussion_record("description", "2026-07-14T03:00:00Z"),
        }
        confirmation = event(
            "review-state",
            "2026-07-14T04:00:00Z",
            "reviewer",
            "approver",
            state="APPROVED",
        )

        discussion_state = build_discussion_state(
            discussions,
            classifications,
            [confirmation],
            {},
            "author",
            previous_state,
        )

        self.assertFalse(discussion_state["description"]["open"])
        self.assertEqual(
            discussion_state["description"]["evidence"]["timestamp"],
            "2026-07-14T03:00:00Z",
        )

        refreshed_classifications = [classification("description", "description")]
        refreshed_state = build_discussion_state(
            discussions,
            refreshed_classifications,
            [],
            {
                "lastEditedAt": "2026-07-14T05:00:00Z",
                "editor": {"login": "maintainer"},
            },
            "author",
            {"description": discussion_state["description"]},
        )

        self.assertEqual(refreshed_state["description"]["waiting_on"], "reviewer")
        self.assertEqual(
            refreshed_state["description"]["evidence"]["timestamp"],
            "2026-07-14T03:00:00Z",
        )

    def test_same_refresh_description_evidence_survives_removed_confirmation(self) -> None:
        discussions = [mainline_discussion("description")]
        classifications = [classification("description", "description")]
        confirmation = event(
            "review-state",
            "2026-07-14T04:00:00Z",
            "reviewer",
            "approver",
            state="APPROVED",
        )

        discussion_state = build_discussion_state(
            discussions,
            classifications,
            [confirmation],
            {
                "lastEditedAt": "2026-07-14T03:00:00Z",
                "editor": {"login": "author"},
            },
            "author",
        )

        self.assertFalse(discussion_state["description"]["open"])
        self.assertEqual(
            discussion_state["description"]["evidence"]["timestamp"],
            "2026-07-14T03:00:00Z",
        )

        refreshed_classifications = [classification("description", "description")]
        refreshed_state = build_discussion_state(
            discussions,
            refreshed_classifications,
            [],
            {
                "lastEditedAt": "2026-07-14T05:00:00Z",
                "editor": {"login": "maintainer"},
            },
            "author",
            {"description": discussion_state["description"]},
        )

        self.assertEqual(refreshed_state["description"]["waiting_on"], "reviewer")
        self.assertEqual(
            refreshed_state["description"]["evidence"]["timestamp"],
            "2026-07-14T03:00:00Z",
        )

    def test_evidence_before_edited_root_is_not_reused(self) -> None:
        discussions = [mainline_discussion("description")]
        classifications = [classification("description", "description")]

        discussion_state = build_discussion_state(
            discussions,
            classifications,
            [],
            {},
            "author",
            {
                "description": stored_discussion_record("description", "2026-07-14T00:00:00Z"),
            },
        )

        self.assertEqual(discussion_state["description"]["waiting_on"], "author")
        self.assertNotIn("evidence", discussion_state["description"])

    def test_stored_evidence_kind_must_match_current_decision(self) -> None:
        classifications = [classification("code", "commit")]

        discussion_state = build_discussion_state(
            [mainline_discussion("code")],
            classifications,
            [],
            {},
            "author",
            {
                "code": stored_discussion_record("description", "2026-07-14T03:00:00Z"),
            },
        )

        self.assertEqual(discussion_state["code"]["waiting_on"], "author")
        self.assertNotIn("evidence", discussion_state["code"])

    def test_reviewer_confirmation_closes_item(self) -> None:
        discussions = [mainline_discussion("code")]
        classifications = [classification("code", "commit")]
        events = [
            event("commit", "2026-07-14T02:00:00Z", "author", "author"),
            event(
                "review-state",
                "2026-07-14T03:00:00Z",
                "reviewer",
                "approver",
                state="APPROVED",
            ),
        ]

        discussion_state = build_discussion_state(discussions, classifications, events, {}, "author")

        self.assertFalse(discussion_state["code"]["open"])
        self.assertEqual(classifications[0]["decision"]["discussion_action"], "author")

    def test_reviewer_confirmation_before_author_evidence_does_not_close_item(self) -> None:
        discussions = [mainline_discussion("code")]
        classifications = [classification("code", "commit")]
        events = [
            event(
                "review-state",
                "2026-07-14T02:00:00Z",
                "reviewer",
                "approver",
                state="APPROVED",
            ),
            event("commit", "2026-07-14T03:00:00Z", "author", "author"),
        ]

        discussion_state = build_discussion_state(discussions, classifications, events, {}, "author")

        self.assertEqual(discussion_state["code"]["waiting_on"], "reviewer")
        self.assertEqual(classifications[0]["decision"]["discussion_action"], "author")
        self.assertEqual(discussion_state["code"]["evidence"]["timestamp"], "2026-07-14T03:00:00Z")

    def test_edited_review_confirms_with_edit_timestamp(self) -> None:
        events = normalize_events(
            {
                "commits": [],
                "issue_comments": [],
                "review_comments": [],
                "reviews": [
                    {
                        "id": 202,
                        "submitted_at": "2026-07-14T00:00:00Z",
                        "updated_at": "2026-07-14T03:00:00Z",
                        "user": {"login": "reviewer"},
                        "state": "COMMENTED",
                        "body": "This is addressed.",
                    }
                ],
            },
            "author",
            {"reviewer"},
        )
        classifications = [classification("code", "commit")]

        discussion_state = build_discussion_state(
            [mainline_discussion("code")],
            classifications,
            events,
            {},
            "author",
        )

        self.assertEqual(events[0]["timestamp"], "2026-07-14T00:00:00Z")
        self.assertFalse(discussion_state["code"]["open"])
        self.assertEqual(discussion_state["code"]["confirmation"]["timestamp"], "2026-07-14T03:00:00Z")

    def test_reviewer_confirmation_closes_item_without_detected_author_evidence(self) -> None:
        discussions = [mainline_discussion("code")]
        classifications = [classification("code", "commit")]
        events = [
            event(
                "review-state",
                "2026-07-14T03:00:00Z",
                "reviewer",
                "approver",
                state="APPROVED",
            ),
        ]

        discussion_state = build_discussion_state(discussions, classifications, events, {}, "author")

        self.assertFalse(discussion_state["code"]["open"])
        self.assertEqual(classifications[0]["decision"]["discussion_action"], "author")

    def test_reviewer_confirmation_survives_later_author_evidence(self) -> None:
        discussions = [mainline_discussion("code")]
        confirmation = event(
            "review-state",
            "2026-07-14T03:00:00Z",
            "reviewer",
            "approver",
            state="APPROVED",
        )
        first_state = build_discussion_state(
            discussions,
            [classification("code", "commit")],
            [confirmation],
            {},
            "author",
        )

        refreshed_state = build_discussion_state(
            discussions,
            [classification("code", "commit")],
            [confirmation, event("commit", "2026-07-14T04:00:00Z", "author", "author")],
            {},
            "author",
            first_state,
        )

        self.assertFalse(refreshed_state["code"]["open"])
        self.assertEqual(
            refreshed_state["code"]["confirmation"]["timestamp"],
            "2026-07-14T03:00:00Z",
        )
        self.assertEqual(
            refreshed_state["code"]["evidence"]["timestamp"],
            "2026-07-14T04:00:00Z",
        )

    def test_later_actionable_request_does_not_confirm_older_item(self) -> None:
        discussions = [
            mainline_discussion("first", source_kind="issue-comment", source_id=101),
            mainline_discussion("second", source_kind="issue-comment", source_id=102),
        ]
        classifications = [
            classification("first", "commit"),
            classification("second", "commit"),
        ]
        events = [
            event(
                "issue-comment",
                "2026-07-14T03:00:00Z",
                "reviewer",
                "approver",
                source_id=102,
            ),
        ]

        discussion_state = build_discussion_state(discussions, classifications, events, {}, "author")

        self.assertEqual(discussion_state["first"]["waiting_on"], "author")
        self.assertEqual(classifications[0]["decision"]["discussion_action"], "author")

    def test_filtered_conflict_request_does_not_confirm_older_item(self) -> None:
        discussions = [
            mainline_discussion("request", source_kind="issue-comment", source_id=101),
        ]
        classifications = [classification("request", "commit")]
        events = [
            event("commit", "2026-07-14T02:00:00Z", "author", "author"),
            event(
                "issue-comment",
                "2026-07-14T03:00:00Z",
                "reviewer",
                "approver",
                source_id=102,
                body="Please resolve the merge conflict.",
            ),
        ]

        discussion_state = build_discussion_state(discussions, classifications, events, {}, "author")

        self.assertEqual(discussion_state["request"]["waiting_on"], "reviewer")
        self.assertEqual(classifications[0]["decision"]["discussion_action"], "author")
        self.assertEqual(discussion_state["request"]["evidence"]["timestamp"], "2026-07-14T02:00:00Z")

    def test_later_acknowledgement_confirms_older_item(self) -> None:
        discussions = [
            mainline_discussion("request", source_kind="issue-comment", source_id=101),
            mainline_discussion("ack", source_kind="issue-comment", source_id=102),
        ]
        classifications = [
            classification("request", "commit"),
            {
                "discussion_id": "ack",
                "discussion_kind": "pr-conversation-item",
                "decision": {"discussion_action": "none", "evidence_kind": "none"},
            },
        ]
        events = [
            event(
                "issue-comment",
                "2026-07-14T03:00:00Z",
                "reviewer",
                "approver",
                source_id=102,
            ),
        ]

        discussion_state = build_discussion_state(discussions, classifications, events, {}, "author")

        self.assertFalse(discussion_state["request"]["open"])
        self.assertEqual(classifications[0]["decision"]["discussion_action"], "author")

    def test_repeated_changes_requested_review_does_not_close_item(self) -> None:
        discussions = [mainline_discussion("code")]
        discussions[0]["review_state"] = "CHANGES_REQUESTED"
        classifications = [classification("code", "commit")]
        events = [
            event(
                "review-state",
                "2026-07-14T03:00:00Z",
                "reviewer",
                "approver",
                state="CHANGES_REQUESTED",
            ),
        ]

        discussion_state = build_discussion_state(discussions, classifications, events, {}, "author")

        self.assertEqual(discussion_state["code"]["waiting_on"], "author")
        self.assertEqual(classifications[0]["decision"]["discussion_action"], "author")

    def test_commented_review_does_not_clear_changes_requested_item(self) -> None:
        discussions = [mainline_discussion("code")]
        discussions[0]["review_state"] = "CHANGES_REQUESTED"
        classifications = [classification("code", "commit")]
        events = [
            event("commit", "2026-07-14T02:00:00Z", "author", "author"),
            event(
                "review-state",
                "2026-07-14T03:00:00Z",
                "reviewer",
                "approver",
                state="COMMENTED",
            ),
            event(
                "review-state",
                "2026-07-14T04:00:00Z",
                "other-reviewer",
                "approver",
                state="APPROVED",
            ),
        ]
        facts = {"approval_count": 1, "is_maintenance_bot": False}

        discussion_state = build_discussion_state(discussions, classifications, events, {}, "author")

        self.assertEqual(discussion_state["code"]["waiting_on"], "reviewer")
        self.assertEqual(route_pr(facts, discussion_state, 1), "approver")

    def test_different_reviewer_does_not_close_item(self) -> None:
        discussions = [mainline_discussion("code")]
        classifications = [classification("code", "commit")]
        events = [
            event("commit", "2026-07-14T02:00:00Z", "author", "author"),
            event(
                "review-state",
                "2026-07-14T03:00:00Z",
                "other-reviewer",
                "approver",
                state="APPROVED",
            ),
        ]

        discussion_state = build_discussion_state(discussions, classifications, events, {}, "author")

        self.assertEqual(discussion_state["code"]["waiting_on"], "reviewer")
        self.assertEqual(classifications[0]["decision"]["discussion_action"], "author")

    def test_reviewer_confirmation_closes_external_and_unclear_items(self) -> None:
        events = [
            event(
                "issue-comment",
                "2026-07-14T03:00:00Z",
                "reviewer",
                "approver",
            ),
        ]
        for action in ("external", "unclear"):
            with self.subTest(action=action):
                discussions = [mainline_discussion(action)]
                classifications = [classification(action, action)]
                classifications[0]["decision"]["discussion_action"] = action

                discussion_state = build_discussion_state(discussions, classifications, events, {}, "author")

                self.assertFalse(discussion_state[action]["open"])
                self.assertEqual(classifications[0]["decision"]["discussion_action"], action)

    def test_pending_confirmation_overrides_approval_and_remains_visible(self) -> None:
        discussions = [mainline_discussion("code")]
        discussions[0]["comments"] = [
            event("issue-comment", ROOT_TIMESTAMP, "reviewer", "approver"),
        ]
        classifications = [classification("code", "commit")]
        discussion_state = {
            "code": {
                "open": True,
                "waiting_on": "reviewer",
                "waiting_since": "2026-07-14T02:00:00Z",
            },
        }
        facts = {"approval_count": 1, "is_maintenance_bot": False, "assignees": []}
        events = [
            event(
                "review-state",
                ROOT_TIMESTAMP,
                "reviewer",
                "approver",
                state="APPROVED",
            )
        ]

        self.assertEqual(route_pr(facts, discussion_state, 1), "approver")
        add_reviewers(facts, events, discussions, discussion_state)
        reviewer = facts["reviewers"][0]
        self.assertTrue(reviewer["mainline_feedback"])
        self.assertFalse(reviewer["open_thread"])
        self.assertEqual(reviewer_icon(reviewer), "📌\u2060✅")
        self.assertEqual(reviewer_logins_for_notification(facts), ["reviewer"])

    def test_inline_and_mainline_feedback_keep_both_badges(self) -> None:
        mainline = mainline_discussion("mainline")
        mainline["comments"] = [
            event("issue-comment", ROOT_TIMESTAMP, "reviewer", "approver"),
        ]
        inline = {
            "discussion_id": "inline",
            "discussion_kind": "review-comment-thread",
            "comments": [
                event("review-comment", ROOT_TIMESTAMP, "reviewer", "approver"),
            ],
        }
        classifications = [classification("mainline", "commit")]
        classifications.append(
            {
                "discussion_id": "inline",
                "discussion_kind": "review-comment-thread",
                "decision": {"discussion_action": "author", "reason": "inline request"},
            }
        )
        facts = {"assignees": []}
        discussion_state = {
            "mainline": {"open": True, "waiting_on": "author"},
            "inline": {"open": True, "waiting_on": "author"},
        }

        add_reviewers(facts, [], [mainline, inline], discussion_state)

        reviewer = facts["reviewers"][0]
        self.assertTrue(reviewer["mainline_feedback"])
        self.assertTrue(reviewer["open_thread"])
        self.assertEqual(reviewer_icon(reviewer), "💬\u2060📌")


if __name__ == "__main__":
    unittest.main()