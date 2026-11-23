import json
import os
import unittest
from unittest.mock import patch

import audit
import model
from rules import blocks, select


ROOT = ".github/copilot-instructions.md"
NESTED = ".github/instructions/nested/later.instructions.md"
FIRST = "- Check the new setting only if present.\n  Skip internal settings."


class RulesTest(unittest.TestCase):
    def test_selects_exact_blocks_in_order(self):
        source = f"# Scope\n\n{FIRST}\n\n- Check existing docs.\n"
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
        first = select(files, None)
        self.assertEqual(first.anchor, {"path": ROOT, "text": FIRST})
        self.assertEqual((first.first_line, first.last_line), (3, 4))
        second = select(files, first.anchor)
        self.assertEqual(second.text, "- Check existing docs.")
        final = select(files, second.anchor)
        self.assertEqual(final.anchor, {"path": NESTED, "text": "Keep public examples current."})
        self.assertIsNone(select(files, final.anchor))

    def test_missing_rewritten_or_ambiguous_anchor_skips_only_its_file(self):
        cursor = {"path": ROOT, "text": FIRST}
        for sources in ({}, {ROOT: FIRST.replace("new", "old")}, {ROOT: f"{FIRST}\n{FIRST}\n"}):
            with self.subTest(sources=sources):
                self.assertEqual(select({**sources, NESTED: "- Later rule.\n"}, cursor).path, NESTED)

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


class StateTest(unittest.TestCase):
    @patch.dict(os.environ, {"GITHUB_REPOSITORY": "open-telemetry/example"})
    @patch.object(audit, "summary")
    @patch.object(audit, "api")
    def test_end_of_pass_removes_cursor_and_detects_concurrent_changes(self, api, _summary):
        result = {"status": "complete", "repo": audit.target(), "head": "b" * 40, "cursor": "c" * 40}
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

    @patch.object(audit, "output")
    @patch.object(audit, "summary")
    @patch.object(audit, "api")
    def test_existing_draft_blocks_all_publication_writes(self, api, _summary, output):
        repo = "open-telemetry/example"
        api.return_value = [{"number": 123, "head": {"repo": {"full_name": repo}, "ref": audit.FIX_PREFIX + "12"}}]
        audit.create_branch({"repo": repo})
        self.assertEqual(api.call_count, 1)
        output.assert_not_called()

    @patch.dict(os.environ, {"GITHUB_RUN_ID": "42"})
    @patch.object(audit, "output")
    @patch.object(audit, "summary")
    @patch.object(audit, "api")
    def test_branch_and_draft_requests_are_separate(self, api, _summary, output):
        repo = "open-telemetry/example"
        result = {
            "repo": repo, "tree": "a" * 40, "sha": "b" * 40, "base": "main",
            "rule": {"path": ROOT},
            "fix": {"edit": {"path": "README.md", "line": 1, "before": "Five seconds.", "after": "Ten seconds."},
                    "original": "Five seconds.\n",
                    "evidence": "Correct the timeout in `README.md`.\nIt is ten seconds.\n\nThe default stays the same."},
        }
        api.side_effect = [[], {"sha": "c" * 40}, {"sha": "d" * 40}, {}]
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
                         "Correct the timeout in `README.md`. It is ten seconds.\n\nThe default stays the same.\n\n"
                         f"[Repository guidance](https://github.com/{repo}/blob/{result['sha']}/{ROOT}) | "
                         f"[Audit run](https://github.com/{repo}/actions/runs/42)")
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

    @patch.dict(os.environ, {"GITHUB_REPOSITORY": "other/example"})
    def test_rejects_callers_outside_open_telemetry(self):
        with self.assertRaisesRegex(ValueError, "Caller must be an OpenTelemetry repository"):
            audit.target()
