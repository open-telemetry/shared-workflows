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
    @patch.dict(os.environ, {"TARGET": "example"})
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
            audit.HOME_REPO, "/contents/example.json", method="DELETE",
            body={"message": "Advance instruction audit for open-telemetry/example",
                  "branch": audit.STATE, "sha": result["cursor"]},
        )

    @patch.object(audit, "output")
    @patch.object(audit, "summary")
    @patch.object(audit, "api")
    def test_existing_draft_blocks_all_publication_writes(self, api, _summary, output):
        repo = "open-telemetry/example"
        api.return_value = [{"number": 123, "head": {"repo": {"full_name": repo}, "ref": audit.FIX_PREFIX + "12"}}]
        audit.publish({"repo": repo})
        self.assertEqual(api.call_count, 1)
        output.assert_called_once_with("handled", "false")
