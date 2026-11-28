import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import audit
import hosted


REPO = "open-telemetry/opentelemetry-java-instrumentation"
FORK = "opentelemetrybot/opentelemetry-java-instrumentation"
RULE = {"path": ".github/copilot-instructions.md", "text": "- Keep examples current."}


def finding():
    return {
        "repo": REPO, "sha": "a" * 40, "tree": "b" * 40, "base": "main",
        "rule": RULE, "first_line": 3, "last_line": 3,
        "title": "Update the timeout example", "explanation": "Use the documented default.",
        "fix": [{"path": "README.md", "mode": "100644", "type": "blob", "content": "New example.\n"}],
    }


class EnrollmentTest(unittest.TestCase):
    def test_defaults_are_independent_of_review_generation_enrollment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "repositories.json"
            path.write_text('{"repositories":{"example":{}}}', encoding="utf-8")
            self.assertEqual(hosted.settings("example", path),
                             {"publish_audit_fixes": False, "max_open_audit_prs": 5})
            with self.assertRaisesRegex(ValueError, "not enrolled"):
                hosted.settings("not-enrolled", path)
            for entry in ({"publish_audit_fixes": "false"}, {"max_open_audit_prs": True},
                          {"max_open_audit_prs": 0}, {"model": "other"}):
                path.write_text(json.dumps({"repositories": {"example": entry}}), encoding="utf-8")
                with self.subTest(entry=entry), self.assertRaisesRegex(ValueError, "Invalid"):
                    hosted.settings("example", path)

    @patch.dict(os.environ, {"GITHUB_REPOSITORY": hosted.CENTRAL, "TARGET_REPOSITORY": "../example"})
    def test_dispatch_cannot_escape_the_enrollment(self):
        with self.assertRaises(ValueError):
            hosted.target()


@patch.object(audit, "summary")
@patch.object(audit, "output")
class StateTest(unittest.TestCase):
    @patch.object(hosted, "read_state")
    @patch.object(audit, "api")
    def test_state_writes_are_per_target_non_force_updates_in_central_repo(self, api, read, _output, _summary):
        read.return_value = ("a" * 40, {})
        api.side_effect = [{"sha": "b" * 40}, {"sha": "c" * 40}, {}]
        state = {"anchor": RULE, "sha": "d" * 40, "offset": 1}
        self.assertEqual(hosted.write_state(REPO, "a" * 40, state), "c" * 40)
        self.assertTrue(all(call.args[0] == hosted.CENTRAL for call in api.call_args_list))
        self.assertEqual(api.call_args_list[1].kwargs["body"]["parents"], ["a" * 40])
        self.assertEqual(api.call_args.args[1],
                         "/git/refs/heads/otelbot/instruction-audit-state-opentelemetry-java-instrumentation")
        self.assertEqual(api.call_args.kwargs["body"], {"sha": "c" * 40, "force": False})
        api.reset_mock()
        with self.assertRaisesRegex(ValueError, "changed"):
            hosted.write_state(REPO, "e" * 40, state)
        api.assert_not_called()

    @patch.object(audit, "api")
    def test_reads_pinned_pass_from_central_state_with_wrapped_base64(self, api, _output, _summary):
        state = {"anchor": RULE, "sha": "a" * 40, "offset": 2}
        content = base64.encodebytes(json.dumps(state).encode()).decode()
        api.side_effect = [
            {"object": {"sha": "b" * 40}},
            {"type": "file", "encoding": "base64", "size": len(content), "content": content},
        ]
        self.assertEqual(hosted.read_state(REPO), ("b" * 40, state))
        self.assertTrue(all(call.args[0] == hosted.CENTRAL for call in api.call_args_list))

    @patch.object(hosted, "write_state")
    @patch.object(hosted, "read_state")
    @patch.object(audit, "api")
    @patch.object(audit, "snapshot")
    @patch.object(audit, "read_blob", return_value="- First rule.\n- Second rule.\n")
    def test_failed_or_interrupted_runs_resume_same_snapshot_and_offset(
        self, _blob, snapshot, api, read, write, _output, _summary
    ):
        api.return_value = {"full_name": REPO, "visibility": "public", "archived": False, "disabled": False}
        snapshot.return_value = ("main", "a" * 40, "b" * 40, {
            RULE["path"]: ("100644", "blob"),
        })
        read.return_value = (None, {"anchor": None, "sha": None, "offset": 0})
        write.return_value = "c" * 40
        with tempfile.TemporaryDirectory() as directory:
            plan_path, prompt_path = Path(directory) / "plan.json", Path(directory) / "prompt.txt"
            with patch.object(audit, "PLAN", plan_path), patch.object(audit, "PROMPT", prompt_path):
                hosted.prepare(REPO)
                plan = json.loads(plan_path.read_bytes())
                self.assertEqual(plan["state"], {"anchor": None, "sha": "a" * 40, "offset": 0})
                read.return_value = ("c" * 40, plan["state"])
                hosted.prepare(REPO)
                snapshot.assert_called_with(REPO, unittest.mock.ANY, "a" * 40)
                self.assertEqual(write.call_count, 1)
                active = plan["state"] | {"offset": 1}
                read.return_value = ("d" * 40, active)
                hosted.prepare(REPO)
                resumed = json.loads(plan_path.read_bytes())
                self.assertEqual(resumed["rule"]["text"], "- Second rule.")
                self.assertEqual(resumed["result"]["completed"]["text"], "- First rule.")
                self.assertEqual(write.call_count, 1)

    @patch.object(hosted, "write_state")
    def test_saves_continuation_before_dispatch_and_only_advances_published_finding(
        self, write, output, _summary
    ):
        for status, published, expected in (
            ("continue", False, {"anchor": None, "sha": "a" * 40, "offset": 2}),
            ("finding", False, {"anchor": None, "sha": None, "offset": 0}),
            ("finding", True, {"anchor": RULE, "sha": None, "offset": 0}),
            ("complete", False, {"anchor": None, "sha": None, "offset": 0}),
        ):
            with self.subTest(status=status, published=published):
                plan = {"state_head": "b" * 40, "state": {"anchor": None, "sha": "a" * 40, "offset": 1},
                        "resumable": True}
                result = finding() | {"status": status, "completed": None}
                hosted.save(REPO, result, plan, published=published)
                write.assert_called_with(REPO, "b" * 40, expected)
                output.assert_called_with("continue", str(status == "continue").lower())
        write.side_effect = ValueError("state write failed")
        output.reset_mock()
        with self.assertRaises(ValueError):
            hosted.save(REPO, finding() | {"status": "continue"}, plan)
        output.assert_not_called()

    @patch.object(hosted, "write_state")
    @patch.object(audit, "snapshot")
    @patch.object(audit, "read_blob", return_value=RULE["text"] + "\n- Duplicate rule.\n- Duplicate rule.\n- Later rule.\n")
    def test_published_duplicates_resume_by_pinned_offset_without_skipping_later_rules(
        self, _blob, snapshot, write, output, _summary
    ):
        snapshot.return_value = ("main", "a" * 40, "b" * 40, {
            RULE["path"]: ("100644", "blob"),
        })
        state = {"anchor": RULE, "sha": "a" * 40, "offset": 0}
        for offset in range(2):
            with self.subTest(offset=offset):
                continuation = f"{state['sha']}:none:{state['offset']}" if state["offset"] else ""
                plan, _ = audit.prepare(REPO, "snapshot", publish_fixes=True, pinned=state["sha"],
                                        cursor_record=(None, None, state["anchor"]), continuation=continuation)
                self.assertEqual(plan["rule"]["text"], "- Duplicate rule.")
                self.assertFalse(plan["resumable"])
                plan.update(state_head="c" * 40, state=state)
                result = finding() | {"rule": plan["rule"], "status": "finding", "completed": None}
                hosted.save(REPO, result, plan, published=True)
                state = write.call_args.args[2]
                self.assertEqual(state, {"anchor": RULE, "sha": "a" * 40, "offset": offset + 1})
                output.assert_called_with("continue", "false")
        plan, _ = audit.prepare(REPO, "snapshot", pinned=state["sha"],
                                cursor_record=(None, None, state["anchor"]),
                                continuation=f"{state['sha']}:none:{state['offset']}")
        self.assertEqual(plan["rule"]["text"], "- Later rule.")
        self.assertTrue(plan["resumable"])
        snapshot.assert_called_with(REPO, "snapshot", "a" * 40)

    @patch.object(hosted, "write_state")
    def test_publishing_final_duplicate_finishes_the_pass_without_saving_ambiguous_anchor(
        self, write, output, _summary
    ):
        plan = {"state_head": "b" * 40, "state": {"anchor": RULE, "sha": "a" * 40, "offset": 1},
                "resumable": False, "next": ""}
        hosted.save(REPO, finding() | {"status": "finding", "completed": RULE}, plan, published=True)
        write.assert_called_with(REPO, "b" * 40, {"anchor": None, "sha": None, "offset": 0})
        output.assert_called_with("continue", "false")


@patch.object(audit, "summary")
@patch.object(audit, "output")
class PublicationTest(unittest.TestCase):
    @patch.object(audit, "api")
    def test_cap_counts_pilot_and_bot_fork_across_pages_before_any_write(self, api, _output, _summary):
        def pull(repo, ref):
            return {"head": {"repo": {"full_name": repo}, "ref": ref}}

        pilot = pull(REPO, audit.FIX_PREFIX + "pilot")
        bot = pull(FORK, audit.FIX_PREFIX + "bot")
        unrelated = pull("someone/opentelemetry-java-instrumentation", audit.FIX_PREFIX + "other")
        api.side_effect = [[pilot] * 4 + [unrelated] * 96, [bot]]
        with self.assertRaisesRegex(ValueError, "limit reached"):
            hosted.check_cap(REPO, 5)
        self.assertEqual(api.call_count, 2)
        self.assertTrue(all("method" not in call.kwargs for call in api.call_args_list))

    @patch.object(hosted, "ensure_fork", return_value=FORK)
    @patch.object(hosted, "settings", return_value={"publish_audit_fixes": True, "max_open_audit_prs": 5})
    @patch.object(audit, "pull_request_body", return_value="Fix the example.")
    @patch.object(hosted, "check_cap")
    @patch.object(hosted, "pulls", return_value=iter([]))
    @patch.object(audit, "api")
    def test_late_cap_failure_leaves_no_branch_and_retry_publishes_to_bot_fork(
        self, api, _pulls, cap, _body, _settings, _fork, output, _summary
    ):
        result = finding()
        events = []

        def respond(repo, path="", **kwargs):
            events.append(path)
            if not path:
                return {"visibility": "public", "archived": False, "disabled": False, "default_branch": "main"}
            if path.startswith("/git/ref/"):
                return None
            if path == "/git/trees":
                self.assertEqual(kwargs["body"]["base_tree"], result["tree"])
                return {"sha": "c" * 40}
            if path == "/git/commits":
                self.assertEqual(kwargs["body"]["parents"], [result["sha"]])
                return {"sha": "d" * 40}
            if path == "/pulls":
                self.assertEqual(repo, REPO)
                self.assertEqual(events[-3:], ["cap", "/git/refs", "/pulls"])
                self.assertEqual(kwargs["body"]["head"], f"{hosted.BOT}:{hosted.fix_branch(result)}")
                self.assertFalse(kwargs["body"]["draft"])
                self.assertIn("d" * 40, kwargs["body"]["body"])
                return {"draft": False, "html_url": "https://github.com/" + REPO + "/pull/1"}
            self.assertEqual(repo, FORK)
            return {}

        api.side_effect = respond
        cap.side_effect = [None, ValueError("Open audit PR limit reached")]
        with self.assertRaisesRegex(ValueError, "Open audit PR limit reached"):
            hosted.publish(REPO, result)
        self.assertIn("/git/commits", events)
        self.assertNotIn("/git/refs", events)
        self.assertNotIn("/pulls", events)
        output.assert_not_called()
        events.clear()
        cap.side_effect = lambda *_: events.append("cap")
        hosted.publish(REPO, result)
        for call in api.call_args_list:
            if call.kwargs.get("method") == "POST" and call.args[1].startswith("/git/"):
                self.assertEqual(call.args[0], FORK)
        output.assert_called_once_with("handled", "true")

    @patch.object(hosted, "ensure_fork", return_value=FORK)
    @patch.object(hosted, "settings", return_value={"publish_audit_fixes": True, "max_open_audit_prs": 5})
    @patch.object(audit, "pull_request_body", return_value="Fix the example.")
    @patch.object(hosted, "check_cap")
    @patch.object(hosted, "pulls")
    @patch.object(audit, "api")
    def test_retry_recovers_published_pr_without_duplication_but_rejects_manual_edits(
        self, api, pulls, cap, _body, _settings, _fork, output, _summary
    ):
        result = finding()
        branch = hosted.fix_branch(result)
        pull = {
            "user": {"login": hosted.BOT}, "base": {"ref": "main"},
            "head": {"sha": "c" * 40, "repo": {"full_name": FORK}},
            "body": f"<!-- shared-workflows:instruction-audit {branch} {'c' * 40} -->",
            "html_url": "https://github.com/" + REPO + "/pull/1",
        }
        pulls.side_effect = lambda *args, **kwargs: iter([pull])
        api.return_value = {"object": {"sha": "c" * 40}}
        hosted.publish(REPO, result)
        output.assert_called_once_with("handled", "true")
        cap.assert_not_called()
        api.return_value = {"object": {"sha": "d" * 40}}
        with self.assertRaisesRegex(ValueError, "unmanaged or changed"):
            hosted.publish(REPO, result)
        pull["head"]["sha"] = "d" * 40
        with self.assertRaisesRegex(ValueError, "unmanaged or changed"):
            hosted.publish(REPO, result)

    @patch.object(hosted, "ensure_fork", return_value=FORK)
    @patch.object(hosted, "settings", return_value={"publish_audit_fixes": True, "max_open_audit_prs": 5})
    @patch.object(audit, "pull_request_body", return_value="Fix the example.")
    @patch.object(hosted, "check_cap")
    @patch.object(hosted, "pulls")
    @patch.object(audit, "api")
    def test_identical_rules_at_distinct_lines_publish_separately_and_retries_reuse_each_pr(
        self, api, pulls, cap, _body, _settings, _fork, output, _summary
    ):
        first = finding()
        second = finding() | {
            "first_line": 8, "last_line": 8,
            "fix": [{"path": "internal/README.md", "mode": "100644", "type": "blob",
                     "content": "Updated internal example.\n"}],
        }
        first_branch = hosted.fix_branch(first)
        first_pull = {
            "user": {"login": hosted.BOT}, "base": {"ref": "main"},
            "head": {"sha": "c" * 40, "repo": {"full_name": FORK}},
            "body": f"<!-- shared-workflows:instruction-audit {first_branch} {'c' * 40} -->",
            "html_url": f"https://github.com/{REPO}/pull/1",
        }
        published = {f"{hosted.BOT}:{first_branch}": first_pull}
        pulls.side_effect = lambda _repo, **params: iter(
            [published[params["head"]]] if params["head"] in published else []
        )
        api.return_value = {"object": {"sha": "c" * 40}}
        hosted.publish(REPO, first)
        cap.assert_not_called()
        api.reset_mock()
        api.side_effect = [
            {"visibility": "public", "archived": False, "disabled": False, "default_branch": "main"},
            None, {"sha": "e" * 40}, {"sha": "d" * 40}, {},
            {"draft": False, "html_url": f"https://github.com/{REPO}/pull/2"},
        ]
        hosted.publish(REPO, second)
        second_head = api.call_args.kwargs["body"]["head"]
        self.assertNotEqual(second_head, f"{hosted.BOT}:{first_branch}")
        self.assertEqual(api.call_args_list[2].kwargs["body"]["tree"], second["fix"])
        published[second_head] = {
            "user": {"login": hosted.BOT}, "base": {"ref": "main"},
            "head": {"sha": "d" * 40, "repo": {"full_name": FORK}},
            "body": api.call_args.kwargs["body"]["body"],
            "html_url": f"https://github.com/{REPO}/pull/2",
        }
        api.reset_mock(side_effect=True)
        cap.reset_mock()
        output.reset_mock()
        for result, sha in ((first, "c" * 40), (second, "d" * 40)):
            api.return_value = {"object": {"sha": sha}}
            hosted.publish(REPO, json.loads(json.dumps(result)))
        self.assertTrue(all("method" not in call.kwargs for call in api.call_args_list))
        cap.assert_not_called()
        self.assertEqual(output.call_args_list, [
            unittest.mock.call("handled", "true"), unittest.mock.call("handled", "true"),
        ])

    @patch.object(hosted, "settings", return_value={"publish_audit_fixes": False, "max_open_audit_prs": 5})
    @patch.object(hosted, "ensure_fork")
    def test_disabled_publication_never_uses_bot_fork(self, fork, _settings, output, _summary):
        with self.assertRaisesRegex(ValueError, "authorized"):
            hosted.publish(REPO, finding())
        fork.assert_not_called()
        output.assert_not_called()

    @patch.dict(os.environ, {"GITHUB_RUN_ID": "42", "AUDIT_ORCHESTRATOR": hosted.CENTRAL})
    def test_pr_links_to_central_run_and_target_rule(self, _output, _summary):
        body = audit.pull_request_body(finding())
        self.assertIn("https://github.com/open-telemetry/shared-workflows/actions/runs/42", body)
        self.assertIn(f"https://github.com/{REPO}/blob/{'a' * 40}/.github/copilot-instructions.md", body)


if __name__ == "__main__":
    unittest.main()
