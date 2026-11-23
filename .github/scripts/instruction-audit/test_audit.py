import base64
import json
import os
import unittest
from unittest.mock import patch

import audit
import model
from rules import blocks, remaining_rules


ROOT = ".github/copilot-instructions.md"
NESTED = ".github/instructions/nested/later.instructions.md"
FIRST = "- Check the new setting only if present.\n  Skip internal settings."


class RulesTest(unittest.TestCase):
    def test_selects_exact_blocks_in_order(self):
        source = f"# Scope\n\n{FIRST}\n\n- Check existing docs.\n- Check existing docs.\n"
        nested = """---
applyTo: "**/*.md"
---
# Documentation
Keep public examples current.

```shell
- Not a rule
```
"""
        files = {NESTED: nested, ROOT: source}
        (first, resumable), *rest = remaining_rules(files, None)
        self.assertEqual(first.anchor, {"path": ROOT, "text": FIRST})
        self.assertEqual((first.first_line, first.last_line), (3, 4))
        self.assertTrue(resumable)
        self.assertEqual(list(remaining_rules(files, first.anchor)), rest)
        self.assertEqual([(rule.text, resumable) for rule, resumable in rest[:-1]],
                         [("- Check existing docs.", False)] * 2)
        final, _ = rest[-1]
        self.assertEqual(final.anchor, {"path": NESTED, "text": "Keep public examples current."})
        self.assertEqual(list(remaining_rules(files, final.anchor)), [])

    def test_missing_rewritten_or_ambiguous_anchor_skips_only_its_file(self):
        cursor = {"path": ROOT, "text": FIRST}
        for sources in ({}, {ROOT: FIRST.replace("new", "old")}, {ROOT: f"{FIRST}\n{FIRST}\n"}):
            with self.subTest(sources=sources):
                self.assertEqual([rule.path for rule, _ in remaining_rules(
                    {**sources, NESTED: "- Later rule.\n"}, cursor)], [NESTED])

    def test_preserves_line_endings_and_indented_exceptions(self):
        source = "- Check the setting.\r\n\r\n  - Except for tests.\r\n- Check docs.\r\n"
        self.assertEqual([rule.text for rule in blocks(ROOT, source)],
                         ["- Check the setting.\r\n\r\n  - Except for tests.", "- Check docs."])


class ValidationTest(unittest.TestCase):
    def test_path_scope(self):
        document = '---\napplyTo: "docs/**/*.md, README.md"\n---\nNo stale docs.'
        for path in ("README.md", "docs/top.md", "docs/sub/page.md"):
            self.assertTrue(audit.applies_to(NESTED, document, path))
        for path in ("sub/README.md", "example/docs/sub/page.md", "docs/file.java"):
            self.assertFalse(audit.applies_to(NESTED, document, path))

    def test_edit_is_one_existing_literal_prose_line(self):
        original = "Overview\n\nThe timeout is ten seconds.\n"
        edit = {"path": "docs/overview.md", "line": 3, "before": "The timeout is ten seconds.",
                "after": "The timeout is five seconds."}
        self.assertEqual(audit.edited_content(edit, original), "Overview\n\nThe timeout is five seconds.\n")
        for invalid in (
            {"path": ".github/workflows/test.md"}, {"path": "docs/../README.md"},
            {"line": True}, {"line": 1}, {"before": "Other text."}, {"after": "---"},
            {"after": "See <https://example.com>."}, {"after": "The timeout is five seconds.\n"},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                audit.edited_content(edit | invalid, original)
        with self.assertRaises(ValueError):
            audit.edited_content(edit, original + "\n```sh\ncode\n```\n")

    def test_removes_only_a_complete_standalone_yaml_example(self):
        before = "The equivalent YAML is:\n\n```yaml\nsetting: true\n```"
        original = "Keep this example.\n\n```yaml\n      <KEY>: value\n```\n\n" + before + "\n\n## Next\n"
        edit = {"path": "docs/settings.md", "line": 7, "before": before, "after": None}
        self.assertEqual(audit.edited_content(edit, original),
                         "Keep this example.\n\n```yaml\n      <KEY>: value\n```\n\n## Next\n")
        for invalid in (
            edit | {"before": before.rsplit("\n", 1)[0]},
            edit | {"before": before + "\n\n## Next"},
            edit | {"line": 9, "before": "\n".join(before.split("\n")[2:])},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                audit.edited_content(invalid, original)
        with self.assertRaisesRegex(ValueError, "inside a fenced block"):
            audit.edited_content(edit | {"line": 9}, "````markdown\n\n" + original + "````\n")

    def test_model_error_does_not_accept_preceding_answer(self):
        message = json.dumps({"type": "assistant.message", "data": {"content": '{"status":"clean"}'}})
        self.assertEqual(model.response(message, 0), {"status": "clean"})
        with self.assertRaises(ValueError):
            model.response(message + '\n{"type":"session.error"}', 0)

    def test_model_failure_reports_stage_without_echoing_diagnostics(self):
        unknown = "arbitrary runner or model output"
        with self.assertRaisesRegex(ValueError, r"startup was not confirmed \(firewall exit 2\)"):
            model.response("", 2, unknown)
        with self.assertRaisesRegex(ValueError, "Copilot was not started: Copilot proxy token not isolated"):
            model.response("", 1, "Sandbox check failed: Copilot proxy token not isolated\n" + unknown)
        with self.assertRaisesRegex(ValueError, r"did not complete successfully \(firewall exit 2\)"):
            model.response("", 2, model.STARTED + "\n" + unknown)


class PassTest(unittest.TestCase):
    @patch.dict(os.environ, {"GITHUB_REPOSITORY": "open-telemetry/example"})
    @patch("builtins.print")
    @patch.object(audit, "summary")
    @patch.object(audit, "read_blob")
    @patch.object(audit, "read_cursor")
    @patch.object(audit, "snapshot")
    @patch.object(model, "ask")
    def test_pass_resumes_on_one_snapshot_and_stops_at_finding_or_end(
        self, ask, snapshot, read_cursor, read_blob, summary, _print
    ):
        for outcome in ("finding", "complete"):
            with self.subTest(outcome=outcome):
                repo = audit.target()
                snapshot.reset_mock()
                snapshot.return_value = ("main", "a" * 40, "b" * 40, {ROOT: "root", NESTED: "nested"})
                read_cursor.return_value = ("c" * 40, "d" * 40, {"path": ROOT, "text": FIRST})
                read_blob.side_effect = [
                    f"{FIRST}\n- Clean rule.\n",
                    "- Changed code only.\n- Finding rule.\n- Changed code only.\n- Final rule.\n",
                ]
                ask.reset_mock()
                ask.side_effect = [
                    {"status": status, "reason": "Audit reason.", "edit": None}
                    for status in (["clean", "not_auditable", "finding"] if outcome == "finding"
                                   else ["clean", "not_auditable", "clean", "not_auditable", "clean"])
                ]
                summary.reset_mock()
                result = audit.prepare(repo, "snapshot")
                snapshot.assert_called_once_with(repo, "snapshot")
                self.assertEqual(result["status"], outcome)
                self.assertEqual(
                    [json.loads(call.args[1].split("encoded as data:\n")[1].splitlines()[0])["selected_rule"]
                     for call in ask.call_args_list],
                    ["- Clean rule.", "- Changed code only.", "- Finding rule."]
                    + (["- Changed code only.", "- Final rule."] if outcome == "complete" else []),
                )
                if outcome == "finding":
                    self.assertEqual(result["rule"], {"path": NESTED, "text": "- Finding rule."})
                    self.assertEqual(result["completed"], {"path": ROOT, "text": "- Clean rule."})
                    summary.assert_called_once_with("Rules checked before stopping: 1 clean, 1 not auditable.")
                else:
                    self.assertIsNone(result["rule"])
                    self.assertEqual(result["completed"], {"path": NESTED, "text": "- Final rule."})


class StateTest(unittest.TestCase):
    @patch.dict(os.environ, {"GITHUB_REPOSITORY": "open-telemetry/example"})
    @patch.object(audit, "summary")
    @patch.object(audit, "api")
    def test_end_of_pass_removes_cursor_and_detects_concurrent_changes(self, api, _summary):
        result = {"status": "complete", "repo": audit.target(), "head": "b" * 40, "cursor": "c" * 40,
                  "rule": None, "completed": {"path": ROOT, "text": FIRST}}
        api.return_value = {"object": {"sha": "a" * 40}}
        with self.assertRaises(ValueError):
            audit.save_state(result)
        self.assertEqual(api.call_count, 1)
        api.return_value = {"object": {"sha": result["head"]}}
        audit.save_state(result)
        api.assert_called_with(
            "open-telemetry/example", "/contents/example.json", method="DELETE",
            body={"message": "Advance instruction audit for open-telemetry/example",
                  "branch": audit.STATE, "sha": result["cursor"]},
        )

    @patch.object(audit, "summary")
    @patch.object(audit, "api")
    def test_saves_preceding_rule_until_finding_is_published(self, api, _summary):
        repo = "open-telemetry/example"
        completed = {"path": ROOT, "text": FIRST}
        finding = {"path": NESTED, "text": "- Finding rule."}
        result = {"status": "finding", "repo": repo, "head": "a" * 40, "cursor": "b" * 40,
                  "rule": finding, "completed": completed}
        api.return_value = {"object": {"sha": result["head"]}}
        audit.save_state(result | {"completed": None})
        api.assert_not_called()
        for published, expected in ((False, completed), (True, finding)):
            with self.subTest(published=published):
                audit.save_state(result, published=published)
                self.assertEqual(api.call_args.args, (repo, "/contents/example.json"))
                body = api.call_args.kwargs["body"]
                self.assertEqual(body["sha"], result["cursor"])
                self.assertEqual(json.loads(base64.b64decode(body["content"])), expected)

    @patch.object(audit, "output")
    @patch.object(audit, "summary")
    @patch.object(audit, "api")
    def test_five_open_audit_prs_across_pages_block_all_publication_writes(self, api, summary, output):
        repo = "open-telemetry/example"
        audit_prs = [{"head": {"repo": {"full_name": repo}, "ref": audit.FIX_PREFIX + str(run)}}
                     for run in range(5)]
        other_pr = {"head": {"repo": {"full_name": repo}, "ref": "unrelated"}}
        api.side_effect = [audit_prs[:4] + [other_pr] * 96, audit_prs[4:]]
        audit.create_branch({"repo": repo})
        self.assertEqual([request.args for request in api.call_args_list], [
            (repo, "/pulls?state=open&per_page=100&page=1"),
            (repo, "/pulls?state=open&per_page=100&page=2"),
        ])
        summary.assert_called_once_with("Open audit PR limit reached (5). No draft opened.")
        output.assert_not_called()

    @patch.dict(os.environ, {"GITHUB_RUN_ID": "42"})
    @patch.object(audit, "output")
    @patch.object(audit, "summary")
    @patch.object(audit, "api")
    def test_branch_and_draft_requests_are_separate(self, api, _summary, output):
        repo = "open-telemetry/example"
        result = {
            "repo": repo, "tree": "a" * 40, "sha": "b" * 40, "base": "main",
            "rule": {"path": ROOT, "text": "Document the `timeout` setting.\n\n"
                                          "Include the compatibility exception.\n"
                                          + "Keep this guidance. " * 30 + "End of rule."},
            "fix": {"edit": {"path": "README.md", "line": 1, "before": "Five seconds.", "after": "Ten seconds."},
                    "original": "Five seconds.\n",
                    "evidence": "Correct the timeout in `README.md`.\nIt is ten seconds.\n\nThe default stays the same."},
        }
        open_prs = [{"head": {"repo": {"full_name": repo}, "ref": audit.FIX_PREFIX + str(run)}}
                    for run in range(4)]
        open_prs.append({"head": {"repo": {"full_name": "someone/example"}, "ref": audit.FIX_PREFIX + "4"}})
        api.side_effect = [open_prs, {"sha": "c" * 40}, {"sha": "d" * 40}, {}]
        audit.create_branch(result)
        self.assertEqual([request.args for request in api.call_args_list], [
            (repo, "/pulls?state=open&per_page=100&page=1"),
            (repo, "/git/trees"), (repo, "/git/commits"), (repo, "/git/refs"),
        ])
        self.assertEqual(api.call_args_list[2].kwargs["body"]["parents"], [result["sha"]])
        self.assertEqual(api.call_args.kwargs["body"]["ref"], "refs/heads/otelbot/instruction-audit-42")
        output.assert_called_once_with("created", "true")
        api.reset_mock(side_effect=True)
        api.return_value = {"number": 123, "draft": True}
        output.reset_mock()
        audit.publish(result)
        self.assertEqual(api.call_count, 1)
        self.assertEqual(api.call_args.args, (repo, "/pulls"))
        body = api.call_args.kwargs["body"]
        self.assertEqual((body["base"], body["head"], body["draft"]), ("main", "otelbot/instruction-audit-42", True))
        self.assertEqual(body["body"],
                         f"This PR fixes a finding from an [instruction audit run](https://github.com/{repo}/actions/runs/42) "
                         f"that checked the following [repository rule](https://github.com/{repo}/blob/{result['sha']}/{ROOT}):\n\n"
                         "> Document the `timeout` setting.\n>\n> Include the compatibility exception.\n> "
                         + "Keep this guidance. " * 30 + "End of rule.\n\n"
                         "### Explanation\n\nCorrect the timeout in `README.md`. It is ten seconds.\n\n"
                         "The default stays the same.")
        output.assert_called_once_with("handled", "true")

    def test_evidence_does_not_render_model_supplied_links_mentions_or_html(self):
        self.assertEqual(
            audit.evidence_markdown(
                "# Review\n\nFixes #42 GH-42 @team at www.example.com or https://example.com.\n"
                "![click](https://example.com) <img> `setting.name`"
            ),
            "&#35;&#8203; Review\n\nFixes &#35;&#8203;42 GH&#45;&#8203;42 &#64;&#8203;team "
            "at www&#46;example.com or https&#58;//example.com. "
            "&#33;&#91;click&#93;&#40;https&#58;//example.com&#41; &#60;img&#62; `setting.name`",
        )

    def test_rule_quote_keeps_continuations_inside_the_quote(self):
        self.assertEqual(
            audit.rule_quote("- Use `timeout`.\r\n    Except @team.\r\n\r\n> Keep the exception."),
            "> &#45; Use `timeout`.\n> &#32;&#32;&#32;&#32;Except &#64;&#8203;team.\n>\n"
            "> &#62; Keep the exception.",
        )

    @patch.dict(os.environ, {"GITHUB_REPOSITORY": "other/example"})
    def test_rejects_callers_outside_open_telemetry(self):
        with self.assertRaisesRegex(ValueError, "Caller must be an OpenTelemetry repository"):
            audit.target()
