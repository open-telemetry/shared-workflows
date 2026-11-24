import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import audit
import model
from rules import Rule, blocks, remaining_rules


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

    @patch.object(model.shutil, "which", side_effect=lambda name: f"/opt/tools/bin/{name}")
    @patch.object(model.subprocess, "run")
    def test_shell_tools_use_disposable_workspace_without_checkout_token(self, run, _which):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {
            "RUNNER_TEMP": folder, "AWF_IMAGE_TAG": "test", "GH_TOKEN": "checkout-token",
            "COPILOT_GITHUB_TOKEN": "inference-token",
        }):
            checkout = Path(folder) / "original"
            checkout.mkdir()
            (checkout / "README").write_bytes(b"original")

            def invoke(args, *, cwd, env, **_kwargs):
                self.assertIn("--allow-all-tools", args)
                self.assertNotIn("--available-tools", args)
                self.assertNotIn("GH_TOKEN", env)
                self.assertEqual(env["COPILOT_GITHUB_TOKEN"], "inference-token")
                self.assertEqual(env["GITHUB_WORKSPACE"], str(cwd))
                self.assertNotEqual(cwd, checkout)
                config = json.loads(Path(args[args.index("--config") + 1]).read_text(encoding="utf-8"))
                self.assertEqual(config["network"], {"isolation": True})
                self.assertEqual(config["filesystem"]["allowWrite"], [str(cwd.parent)])
                for option in ("--mount", "--container-workdir"):
                    self.assertTrue(all(not value.startswith(str(checkout))
                                        for key, value in zip(args, args[1:]) if key == option))
                (cwd / "README").write_bytes(b"scratch")
                capture = Path(args[args.index("--capture-command-output") + 1])
                capture.mkdir()
                (capture / "attempt-001.stdout.bin").write_text(
                    '{"type":"assistant.message","data":{"content":"{\\"status\\":\\"clean\\"}"}}\n',
                    encoding="utf-8",
                )
                return CompletedProcess(args, 0, b"", b"")

            run.side_effect = invoke
            self.assertEqual(model.ask(checkout, "Audit this rule."), {"status": "clean"})
            self.assertEqual((checkout / "README").read_bytes(), b"original")
            self.assertFalse(run.call_args.kwargs["cwd"].exists())

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


class PassTest(unittest.TestCase):
    @patch.object(audit, "patch_changes")
    @patch.object(model, "ask")
    def test_report_only_does_not_request_or_validate_a_patch(self, ask, patch_changes):
        reason = "The timeout in `README.md` is wrong."
        ask.return_value = {"status": "finding", "reason": reason, "patch": "unused"}
        result = audit.audit_rule("snapshot", "a" * 40, Rule(ROOT, FIRST, 1, 2), FIRST, publish_fixes=False)
        self.assertEqual(result, ("finding", reason, None))
        ask.assert_called_once()
        self.assertNotIn('"patch"', ask.call_args.args[1])
        patch_changes.assert_not_called()

    @patch.object(audit, "summary")
    @patch.object(audit, "patch_changes", side_effect=audit.UnsupportedPatch("Git could not apply the patch"))
    @patch.object(model, "ask")
    def test_publish_keeps_finding_when_patch_does_not_apply(self, ask, patch_changes, summary):
        reason = "Use the new value."
        ask.return_value = {"status": "finding", "reason": reason, "patch": "bad patch"}
        result = audit.audit_rule("snapshot", "a" * 40, Rule(ROOT, FIRST, 1, 2), FIRST, publish_fixes=True)
        self.assertEqual(result, ("finding", reason, None))
        patch_changes.assert_called_once_with("snapshot", "a" * 40, "bad patch")
        summary.assert_called_once_with("Proposed patch not published: Git could not apply the patch.")

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
                snapshot.return_value = ("main", "a" * 40, "b" * 40, {
                    ROOT: ("100644", "root"), NESTED: ("100644", "nested"),
                })
                read_cursor.return_value = ("c" * 40, "d" * 40, {"path": ROOT, "text": FIRST})
                read_blob.side_effect = [
                    f"{FIRST}\n- Clean rule.\n",
                    "- Changed code only.\n- Finding rule.\n- Changed code only.\n- Final rule.\n",
                ]
                ask.reset_mock()
                ask.side_effect = [
                    {"status": status, "reason": "Audit reason."}
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

    @patch.dict(os.environ, {"GITHUB_REPOSITORY": "open-telemetry/example"})
    @patch("builtins.print")
    @patch.object(audit, "summary")
    @patch.object(audit, "read_blob")
    @patch.object(audit, "read_cursor", return_value=(None, None, None))
    @patch.object(audit, "snapshot")
    @patch.object(audit, "patch_changes")
    @patch.object(model, "ask")
    def test_publish_continues_past_unfixable_findings_until_fix_or_end(
        self, ask, patch_changes, snapshot, _read_cursor, read_blob, summary, _print
    ):
        snapshot.return_value = ("main", "a" * 40, "b" * 40, {ROOT: ("100644", "root")})
        read_blob.return_value = "- First rule.\n- Second rule.\n- Third rule.\n"
        patch_changes.return_value = [{"path": "src/Settings.java", "mode": "100644",
                                       "type": "blob", "content": "newValue();\n"}]
        for candidate in (None, "applicable patch"):
            with self.subTest(candidate=candidate):
                summary.reset_mock()
                ask.reset_mock()
                ask.side_effect = [
                    {"status": "finding", "reason": "First finding.", "patch": None},
                    {"status": "finding", "reason": "Second finding.", "patch": candidate},
                    {"status": "clean", "reason": "Third rule is clean.", "patch": None},
                ]
                result = audit.prepare(audit.target(), "snapshot", publish_fixes=True)
                self.assertIn("First finding.", summary.call_args_list[0].args[0])
                if candidate is None:
                    self.assertEqual(ask.call_count, 3)
                    self.assertEqual(result["status"], "complete")
                    self.assertIsNone(result["fix"])
                else:
                    self.assertEqual(ask.call_count, 2)
                    self.assertEqual(result["status"], "finding")
                    self.assertEqual(result["rule"]["text"], "- Second rule.")
                    self.assertEqual(result["completed"]["text"], "- First rule.")
                    self.assertEqual(result["fix"], patch_changes.return_value)


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
    def test_branch_and_draft_requests_are_separate(self, api, _summary, output):
        repo = "open-telemetry/example"
        result = {
            "repo": repo, "tree": "a" * 40, "sha": "b" * 40, "base": "main",
            "rule": {"path": ROOT, "text": "Document the `timeout` setting.\n\n"
                                          "Include the compatibility exception.\n"
                                          + "Keep this guidance. " * 30 + "End of rule."},
            "reason": "Correct the timeout in `README.md`.\nIt is ten seconds.\n\nThe default stays the same.",
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
        api.return_value = {"number": 123, "draft": True}
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

    @patch.dict(os.environ, {"GITHUB_REPOSITORY": "other/../example"})
    def test_rejects_malformed_caller_repository(self):
        with self.assertRaisesRegex(ValueError, "Invalid caller repository"):
            audit.target()
