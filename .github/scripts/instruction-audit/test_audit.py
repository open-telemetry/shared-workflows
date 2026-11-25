import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import audit
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
## Examples
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

    def test_opening_scope_is_not_selected_but_still_anchors_a_saved_cursor(self):
        scope = "Check only public examples.\r\nInternal examples are out of scope."
        source = f'---\r\napplyTo: "**/*.md"\r\n---\r\n\r\n# Docs\r\n\r\n{scope}\r\n\r\n{FIRST}\r\n'
        documents = {ROOT: source, NESTED: "- Check later docs.\n"}
        cursor = {"path": ROOT, "text": scope}
        expected = [(ROOT, FIRST, 10), (NESTED, "- Check later docs.", 1)]
        self.assertEqual([(rule.text, rule.first_line) for rule in blocks(ROOT, source)], [(FIRST, 10)])
        for anchor in (None, cursor):
            with self.subTest(anchor=anchor):
                self.assertEqual([(rule.path, rule.text, rule.first_line)
                                  for rule, _ in remaining_rules(documents, anchor)], expected)

    def test_legacy_openers_and_later_prose_are_still_rules(self):
        for source, expected in (
            ("Check docs.\n\n# Rules\n\nCheck links.\n", ["Check docs.", "Check links."]),
            ("## Rules\n\nCheck docs.\n", ["Check docs."]),
            ("# Rules\n\n## Docs\n\nCheck docs.\n", ["Check docs."]),
            ("# Rules\n\n- Check docs.\n\nCheck links.\n", ["- Check docs.", "Check links."]),
            ("# Rules\n\n> Check docs.\n\nCheck links.\n", ["> Check docs.", "Check links."]),
            ("# Rules\n\n    Example\n\nCheck docs.\n", ["    Example", "Check docs."]),
            ("# Rules\n\nName | Value\n--- | ---\n", ["Name | Value\n--- | ---"]),
            ("# Rules\n\n```text\nExample\n```\n\nCheck docs.\n", ["Check docs."]),
            ("# Rules\n\n  Scope goes here.\n\nCheck docs.\n", ["Check docs."]),
        ):
            with self.subTest(source=source):
                self.assertEqual([rule.text for rule in blocks(ROOT, source)], expected)


class ValidationTest(unittest.TestCase):
    def test_git_patch_changes_separate_hunks_and_files_without_touching_checkout(self):
        with tempfile.TemporaryDirectory() as folder:
            checkout = Path(folder)
            original = b"first = old\nfirst context\nmiddle\nlast context\nlast = old\n"
            (checkout / "settings").write_bytes(original)
            (checkout / "obsolete").write_bytes(b"old\n")
            audit.git(checkout, "init", "--quiet", "--template=")
            audit.git(checkout, "-c", "core.autocrlf=false", "add", "--", "settings", "obsolete")
            tree = audit.git(checkout, "write-tree").decode().strip()
            proposal = """diff --git a/settings b/settings
--- a/settings
+++ b/settings
@@ -1,2 +1,2 @@
-first = old
+first = new
 first context
@@ -4,2 +4,2 @@
 last context
-last = old
+last = new
diff --git a/test_settings.py b/test_settings.py
new file mode 100644
--- /dev/null
+++ b/test_settings.py
@@ -0,0 +1 @@
+assert first == last
diff --git a/obsolete b/obsolete
deleted file mode 100644
--- a/obsolete
+++ /dev/null
@@ -1 +0,0 @@
-old
"""
            with self.assertRaisesRegex(audit.UnsupportedPatch, "could not apply"):
                audit.patch_changes(checkout, tree, proposal.replace("-last = old", "-last = stale"))
            self.assertEqual(audit.patch_changes(checkout, tree, proposal), [
                {"path": "obsolete", "mode": "100644", "type": "blob", "sha": None},
                {"path": "settings", "mode": "100644", "type": "blob",
                 "content": original.decode().replace("old", "new")},
                {"path": "test_settings.py", "mode": "100644", "type": "blob", "content": "assert first == last\n"},
            ])
            self.assertEqual((checkout / "settings").read_bytes(), original)
            self.assertEqual((checkout / "obsolete").read_bytes(), b"old\n")
            self.assertFalse((checkout / "test_settings.py").exists())
            self.assertEqual(audit.git(checkout, "write-tree").decode().strip(), tree)

    @patch.dict(os.environ, {"GITHUB_REPOSITORY": "Other-org/Private", "GH_TOKEN": "read-token"})
    @patch.object(audit, "git")
    @patch.object(audit, "api", return_value={
        "full_name": "Other-org/Private", "default_branch": "main", "visibility": "private",
    })
    def test_private_snapshot_uses_caller_read_token_only_for_fetch(self, api, git):
        repo = audit.target()
        git.side_effect = [b"", b"", f"{'a' * 40} {'b' * 40}".encode(), b"", b""]
        audit.snapshot(repo, "snapshot")
        api.assert_called_once_with("other-org/private")
        self.assertEqual([call.kwargs for call in git.call_args_list],
                         [{}, {"http_token": "read-token"}, {}, {}, {}])
        fetch = git.call_args_list[1]
        self.assertIn("http.followRedirects=false", fetch.args)
        self.assertIn("https://github.com/other-org/private.git", fetch.args)
        self.assertNotIn("read-token", " ".join(fetch.args))

    @patch.dict(os.environ, {"GH_TOKEN": "read-token", "GIT_TRACE_CURL": "1"})
    @patch.object(audit.subprocess, "run", return_value=CompletedProcess([], 0, b"", b""))
    def test_fetch_credentials_stay_in_process_local_git_config(self, run):
        audit.git("snapshot", "fetch", "https://github.com/other-org/private.git", http_token="read-token")
        fetch_env = run.call_args.kwargs["env"]
        self.assertEqual(fetch_env["GIT_CONFIG_KEY_0"], "http.https://github.com/.extraHeader")
        prefix, _, encoded = fetch_env["GIT_CONFIG_VALUE_0"].rpartition(" ")
        self.assertEqual(prefix, "Authorization: Basic")
        self.assertEqual(base64.b64decode(encoded), b"x-access-token:read-token")
        self.assertNotIn("GH_TOKEN", fetch_env)
        self.assertNotIn("GIT_TRACE_CURL", fetch_env)
        audit.git("snapshot", "checkout", "--detach", "a" * 40)
        self.assertNotIn("GIT_CONFIG_VALUE_0", run.call_args.kwargs["env"])


@patch.dict(os.environ, {"GITHUB_REPOSITORY": "open-telemetry/example"})
@patch.object(audit, "summary")
@patch.object(audit, "read_blob")
@patch.object(audit, "read_cursor")
@patch.object(audit, "snapshot")
class PassTest(unittest.TestCase):
    def test_chain_stays_on_pinned_snapshot_and_stops_at_reported_finding(
        self, snapshot, read_cursor, read_blob, _summary
    ):
        repo = audit.target()
        snapshot.return_value = ("main", "a" * 40, "b" * 40, {
            ROOT: ("100644", "root"), NESTED: ("100644", "nested"),
        })
        read_cursor.return_value = ("c" * 40, "d" * 40, {"path": ROOT, "text": FIRST})
        read_blob.side_effect = lambda _checkout, oid: (
            f"# Rules\n\nApply only to public settings.\n\n{FIRST}\n- Clean rule.\n" if oid == "root"
            else "- Finding rule.\n- Final rule.\n"
        )
        plan, prompt = audit.prepare(repo, "snapshot")
        self.assertEqual(plan["rule"]["text"], "- Clean rule.")
        self.assertIn("Apply only to public settings.", prompt)
        self.assertNotIn('"patch"', prompt)
        self.assertEqual(audit.assess(plan, {"status": "clean", "reason": "Looks good."}, "snapshot")["status"],
                         "continue")
        plan, _ = audit.prepare(repo, "snapshot", continuation=plan["next"])
        snapshot.assert_called_with(repo, "snapshot", "a" * 40)
        result = audit.assess(plan, {"status": "finding", "reason": "Use the new value."}, "snapshot")
        self.assertEqual(result["status"], "finding")
        self.assertEqual(result["rule"], {"path": NESTED, "text": "- Finding rule."})
        self.assertEqual(result["completed"], {"path": ROOT, "text": "- Clean rule."})

    def test_stale_continuation_stops_before_checkout(self, snapshot, read_cursor, _read_blob, _summary):
        read_cursor.return_value = ("c" * 40, "d" * 40, None)
        with self.assertRaisesRegex(ValueError, "Cursor changed"):
            audit.prepare(audit.target(), "snapshot", continuation=f"{'a' * 40}:{'b' * 40}:1")
        snapshot.assert_not_called()

    def test_last_rule_completes_the_pass_without_dispatching_again(self, snapshot, read_cursor, read_blob, _summary):
        repo = audit.target()
        snapshot.return_value = ("main", "a" * 40, "b" * 40, {ROOT: ("100644", "root")})
        read_cursor.return_value = (None, None, None)
        read_blob.return_value = "- Only rule.\n"
        plan, _ = audit.prepare(repo, "snapshot")
        self.assertEqual(plan["next"], "")
        with self.assertRaisesRegex(ValueError, "Unexpected JSON fields"):
            audit.assess(plan, {}, "snapshot")
        result = audit.assess(plan, {"status": "not_auditable", "reason": "Only applies to changes."}, "snapshot")
        self.assertEqual(result["status"], "complete")

    @patch.object(audit, "patch_changes")
    def test_publish_continues_past_unfixable_finding_and_stops_at_valid_patch(
        self, patch_changes, snapshot, read_cursor, read_blob, _summary
    ):
        read_cursor.return_value = (None, None, None)
        snapshot.return_value = ("main", "a" * 40, "b" * 40, {ROOT: ("100644", "root")})
        read_blob.return_value = "- First rule.\n- Second rule.\n- Third rule.\n"
        patch_changes.return_value = [{"path": "src/Settings.java", "mode": "100644",
                                       "type": "blob", "content": "newValue();\n"}]
        plan, _ = audit.prepare(audit.target(), "snapshot", publish_fixes=True)
        result = audit.assess(plan, {"status": "finding", "reason": "First finding.", "patch": None, "title": None,
                                    "explanation": None}, "snapshot")
        self.assertEqual(result["status"], "continue")
        plan, _ = audit.prepare(audit.target(), "snapshot", publish_fixes=True, continuation=plan["next"])
        title = "Default the timeout at the configuration read"
        explanation = (
            "Explicit timeouts were ignored when the setting came from an environment variable. "
            "Apply the default only when neither the setting nor the variable is present.\n\n"
            "An explicitly configured zero still disables the timeout. Older deployments that only set the "
            "environment variable keep their configured value; deployments that set neither now use the "
            "documented default."
        )
        judgment = {"status": "finding", "reason": "Checked Java sources only.", "patch": "diff", "title": title,
                    "explanation": explanation}
        for invalid in (None, "", "\u00e9" * 51, "Fix timeout\nNew line", "Fix timeout for @someone",
                        "Fix timeout in #42", "Fix timeout in GH-42", "Fix timeout at https://example.com"):
            with self.subTest(title=invalid), self.assertRaisesRegex(ValueError, "plain-text PR title"):
                audit.assess(plan, judgment | {"title": invalid}, "snapshot")
        for invalid in (None, " \n", "Fix\0 the timeout."):
            with self.subTest(explanation=invalid), self.assertRaisesRegex(ValueError, "non-empty PR explanation"):
                audit.assess(plan, judgment | {"explanation": invalid}, "snapshot")
        result = audit.assess(plan, judgment, "snapshot")
        self.assertEqual(result["status"], "finding")
        self.assertEqual(result["rule"]["text"], "- Second rule.")
        self.assertEqual(result["completed"]["text"], "- First rule.")
        self.assertEqual(result["fix"], patch_changes.return_value)
        self.assertEqual(result["title"], title)
        self.assertEqual(result["reason"], "Checked Java sources only.")
        self.assertEqual(result["explanation"], explanation)
        patch_changes.assert_called_once_with("snapshot", "a" * 40, "diff")


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

    @patch.dict(os.environ, {"PR_TOKEN": "pull-token"})
    @patch.object(audit, "api")
    def test_five_open_audit_prs_across_pages_block_all_publication_writes(self, api):
        repo = "open-telemetry/example"
        audit_prs = [{"head": {"repo": {"full_name": repo}, "ref": audit.FIX_PREFIX + str(run)}}
                     for run in range(5)]
        other_pr = {"head": {"repo": {"full_name": repo}, "ref": "unrelated"}}
        api.side_effect = [audit_prs[:4] + [other_pr] * 96, audit_prs[4:]]
        with self.assertRaisesRegex(ValueError, "Open audit PR limit reached"):
            audit.create_branch({"repo": repo})
        self.assertEqual([request.args for request in api.call_args_list], [
            (repo, "/pulls?state=open&per_page=100&page=1"),
            (repo, "/pulls?state=open&per_page=100&page=2"),
        ])

    @patch.dict(os.environ, {"GITHUB_RUN_ID": "42", "PR_TOKEN": "pull-token"})
    @patch.object(audit, "output")
    @patch.object(audit, "summary")
    @patch.object(audit, "api")
    def test_branch_and_ready_for_review_requests_are_separate(self, api, _summary, output):
        repo = "open-telemetry/example"
        result = {
            "repo": repo, "tree": "a" * 40, "sha": "b" * 40, "base": "main",
            "rule": {"path": ROOT, "text": "Document the `timeout` setting.\n\n"
                                          "Include the compatibility exception.\n"
                                          + "Keep this guidance. " * 30 + "End of rule."},
            "reason": "Searched configuration and tests.\n\nCoverage is limited to the defaults.",
            "title": "Document the ten-second timeout",
            "explanation": "The docs disagree with the default. Record the ten-second value in `README.md`.\n\n"
                           "Existing timeout settings still work.",
            "fix": [
                {"path": "gradlew", "mode": "100755", "type": "blob", "content": "Ten seconds.\n"},
                {"path": "README.md", "mode": "100644", "type": "blob", "content": "Timeout: ten seconds.\n"},
            ],
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
        self.assertEqual(api.call_args_list[0].kwargs, {"token": "pull-token"})
        self.assertNotIn("token", api.call_args_list[1].kwargs)
        self.assertEqual(api.call_args_list[1].kwargs["body"]["tree"], result["fix"])
        self.assertEqual(api.call_args_list[2].kwargs["body"]["parents"], [result["sha"]])
        self.assertEqual(api.call_args.kwargs["body"]["ref"], "refs/heads/otelbot/instruction-audit-42")
        api.reset_mock(side_effect=True)
        api.return_value = {"number": 123, "draft": False}
        audit.publish(result)
        self.assertEqual(api.call_count, 1)
        self.assertEqual(api.call_args.args, (repo, "/pulls"))
        body = api.call_args.kwargs["body"]
        self.assertEqual(body["title"], "Instruction audit: Document the ten-second timeout")
        self.assertEqual((body["base"], body["head"], body["draft"]), ("main", "otelbot/instruction-audit-42", False))
        self.assertEqual(body["body"],
                         f"This PR fixes a finding from an [instruction audit run](https://github.com/{repo}/actions/runs/42) "
                         f"that checked the following [repository rule](https://github.com/{repo}/blob/{result['sha']}/{ROOT}):\n\n"
                         "> Document the `timeout` setting.\n>\n> Include the compatibility exception.\n> "
                         + "Keep this guidance. " * 30 + "End of rule.\n\n"
                         "### Explanation\n\nThe docs disagree with the default. "
                         "Record the ten&#45;second value in `README.md`.\n\n"
                         "Existing timeout settings still work.")
        output.assert_called_once_with("handled", "true")

    @patch.dict(os.environ, {"GITHUB_RUN_ID": "42"})
    @patch.object(audit, "pull_request_body", return_value="Fix the finding.")
    @patch.object(audit, "output")
    @patch.object(audit, "api", return_value={"number": 123, "draft": True})
    def test_unexpected_draft_does_not_confirm_publication(self, _api, output, _body):
        with self.assertRaisesRegex(ValueError, "ready-for-review pull request"):
            audit.publish({"repo": "open-telemetry/example", "base": "main", "title": "Fix the finding"})
        output.assert_not_called()

    def test_inline_markdown_does_not_render_model_supplied_links_mentions_or_html(self):
        self.assertEqual(
            audit.inline_markdown(
                "# Review: fixes #42 GH-42 @team at www.example.com or https://example.com. "
                "![click](https://example.com) <img> `builder(...).build()` `@CanIgnoreReturnValue`"
            ),
            "&#35;&#8203; Review&#58; fixes &#35;&#8203;42 GH&#45;&#8203;42 &#64;&#8203;team "
            "at www&#46;example.com or https&#58;//example.com. "
            "&#33;&#91;click&#93;&#40;https&#58;//example.com&#41; &#60;img&#62; "
            "`builder(...).build()` `@CanIgnoreReturnValue`",
        )

    def test_rule_quote_keeps_continuations_inside_the_quote(self):
        self.assertEqual(
            audit.rule_quote("- Use `timeout`.\r\n    Except @team.\r\n\r\n> Keep the exception."),
            "> Use `timeout`.\n> &#32;&#32;Except &#64;&#8203;team.\n>\n"
            "> &#62; Keep the exception.",
        )

    def test_rule_quote_renders_table_without_activating_cell_markup(self):
        self.assertEqual(
            audit.rule_quote(
                "- Use these fields:\n\n"
                "  Field | Meaning\n"
                "  :--- | ---:\n"
                r"  `a\|b` | Literal \|, @team, [docs](https://example.com), <img>" "\n\n"
                "  A | B"
            ),
            "> Use these fields&#58;\n>\n"
            "> | Field | Meaning |\n"
            "> | :--- | ---: |\n"
            r"> | `a\|b` | Literal \|, &#64;&#8203;team, &#91;docs&#93;&#40;https&#58;//example.com&#41;, "
            "&#60;img&#62; |\n>\n"
            "> A &#124; B",
        )

    def test_rule_quote_leaves_malformed_table_inert(self):
        self.assertEqual(
            audit.rule_quote("| A | B |\n| --- |\n| C | D |"),
            "> &#124; A &#124; B &#124;\n> &#124; &#45;&#45;&#45; &#124;\n> &#124; C &#124; D &#124;",
        )

    def test_rule_quote_renders_fenced_example_without_activating_surrounding_markup(self):
        self.assertEqual(
            audit.rule_quote(
                "- Use `create(OpenTelemetry)`.\n\n"
                "    ```java\n"
                "      @Example\n"
                "      ```\n"
                "      return builder(openTelemetry).build();\n"
                "    ```\n\n"
                "  See [docs](https://example.com) and @team."
            ),
            "> Use `create(OpenTelemetry)`.\n>\n"
            ">   ````java\n"
            ">     @Example\n"
            ">     ```\n"
            ">     return builder(openTelemetry).build();\n"
            "> ````\n>\n"
            "> See &#91;docs&#93;&#40;https&#58;//example.com&#41; and &#64;&#8203;team.",
        )

    def test_rule_quote_leaves_unclosed_fence_inert(self):
        self.assertEqual(
            audit.rule_quote("- Example:\n\n  ```java\n  @Example"),
            "> Example&#58;\n>\n> &#96;&#96;&#96;java\n> &#64;&#8203;Example",
        )

    @patch.dict(os.environ, {"GITHUB_REPOSITORY": "other/../example"})
    def test_rejects_malformed_caller_repository(self):
        with self.assertRaisesRegex(ValueError, "Invalid caller repository"):
            audit.target()
