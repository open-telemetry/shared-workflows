import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import common
import gate
import post
import prepare
import review as runner
import sandbox
import usage
from common import write_json


class ReviewTest(unittest.TestCase):
    def test_target_url_resolves_without_repository_enrollment(self):
        self.assertEqual(
            gate.review_target("https://github.com/OPEN-TELEMETRY/Any-Repository/pull/123/"),
            ("any-repository", 123),
        )

    def test_target_url_rejects_untrusted_hosts_paths_and_repositories(self):
        for url in (
            "http://github.com/open-telemetry/shared-workflows/pull/123",
            "https://github.com.example.test/open-telemetry/shared-workflows/pull/123",
            "https://github.com@evil.test/open-telemetry/shared-workflows/pull/123",
            "https://github.com/trask/shared-workflows/pull/123",
            "https://github.com/open-telemetry/shared-workflows/pull/0",
            "https://github.com/open-telemetry/shared-workflows/pull/123?repo=other",
            "https://github.com/open-telemetry/shared-workflows/pull/123#discussion",
            "https://github.com/open-telemetry/shared-workflows/issues/123",
            "https://github.com/open-telemetry/./pull/123",
            "https://github.com/open-telemetry/../pull/123",
            "https://github.com/open-telemetry/shared-workflows/pull/123\nrepo=other",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                gate.review_target(url)

    def test_prepare_runs_the_three_passes_and_writes_the_checked_result(self):
        metadata = {
            "number": 123, "html_url": "https://github.com/org/repo/pull/123",
            "base": {"sha": "base", "ref": "release/1.x"}, "head": {"sha": "head"},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "review-result.json"
            with (
                mock.patch.object(runner, "build_bundle", return_value=(metadata, {})) as build,
                mock.patch.object(runner, "checkout_target_branch") as guidance,
                mock.patch.object(runner, "checkout_pr") as checkout,
                mock.patch.object(runner, "closer_look_context", return_value=", including Needs a closer look https://example.test"),
                mock.patch.object(runner, "model_pass", side_effect=["review", "simplifications", '{"comments":[]}']) as model,
            ):
                runner.prepare_result("org/repo", 123, path)
            first, second, final = [call.args for call in model.call_args_list]
            self.assertEqual([call.kwargs["phase"] for call in model.call_args_list], [
                "review", "simplification", "verification",
            ])
            build.assert_called_once_with("org/repo", 123, first[4])
            target = first[3].parent / "target"
            guidance.assert_called_once_with("org/repo", "release/1.x", target)
            for call in model.call_args_list:
                self.assertEqual(call.kwargs["target"], target)
                self.assertIn("Follow relevant local links", call.args[2])
            self.assertEqual([first[:2], second[:2], final[:2]], [(runner.SOL, "max"), (runner.SOL, "max"), (runner.OPUS, "high")])
            self.assertTrue(first[2].startswith(f"review {metadata['html_url']}, including Needs a closer look"))
            self.assertTrue(second[2].startswith(f"are there any simplifications worth doing in {metadata['html_url']}?"))
            self.assertIn("review.txt", final[2])
            self.assertIn("simplifications.txt", final[2])
            checkout.assert_called_once_with("org/repo", 123, "head", first[3])
            self.assertEqual(second[3:], first[3:])
            self.assertEqual(final[3:], first[3:])
            result = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(result["repository"], "org/repo")
            self.assertEqual(result["review"], {
                "body": "No findings.", "comments": [], "commit_id": "head",
            })

    def test_verifier_preserves_a_trailing_no_findings_verdict_without_retry(self):
        response = 'I checked both candidate files. None of the candidates is worth posting.\n\n{"comments":[]}'
        metadata = {"number": 123, "base": {"sha": "base"}, "head": {"sha": "head"}}
        with mock.patch.object(runner, "model_pass", return_value=response) as model:
            result = runner.verify_findings(
                "Check both passes.", Path("source"), Path("input"), {}, metadata, target=Path("target"),
            )
        self.assertEqual(result["review"], {"commit_id": "head", "body": "No findings.", "comments": []})
        model.assert_called_once()

    def test_verifier_output_does_not_guess_at_ambiguous_or_nonterminal_json(self):
        for answer in (
            '{"comments":[]}\n{"comments":[]}',
            '{"comments":[]}\nMore prose.',
            '[\n{"comments":[]}\n]',
            '```json\n{"comments":[]}',
            '{"comments":[]}\n```',
        ):
            with self.subTest(answer=answer), self.assertRaisesRegex(ValueError, "unambiguous JSON object"):
                runner.parse_verifier_output(answer)

    def test_verifier_requests_format_only_repair_for_ambiguous_output(self):
        checkout, bundle = Path("source"), Path("input")
        target = Path("target")
        metadata = {"number": 123, "base": {"sha": "base"}, "head": {"sha": "head"}}
        answer = 'Draft:\n{"comments":[]}\nFinal verdict:\n{"comments":[]}'
        with mock.patch.object(
            runner, "model_pass", side_effect=[answer, '```json\n{\n  "comments": []\n}\n```']
        ) as model:
            result = runner.verify_findings("Check both passes.", checkout, bundle, {}, metadata, target=target)

        self.assertEqual(result, {
            "pr": 123,
            "base_sha": "base",
            "review": {"commit_id": "head", "body": "No findings.", "comments": []},
        })
        first, retry = [call.args for call in model.call_args_list]
        self.assertEqual(first, (runner.OPUS, "high", "Check both passes.", checkout, bundle))
        self.assertEqual(retry[:2], first[:2])
        self.assertNotIn(first[2], retry[2])
        self.assertIn("Do not review the PR again", retry[2])
        self.assertIn(json.dumps(answer), retry[2])
        self.assertEqual(retry[3:], first[3:])
        self.assertEqual([call.kwargs["target"] for call in model.call_args_list], [target, target])
        self.assertIs(model.call_args.kwargs["allow_tools"], False)
        self.assertEqual([call.kwargs["phase"] for call in model.call_args_list], [
            "verification", "verification-retry",
        ])

    def test_verifier_repairs_an_unchanged_end_line_without_carrying_over_the_patch(self):
        comment = {
            "kind": "review", "path": "Example.java", "start_line": 23, "line": 28,
            "finding": "The inserted main method separates run from its Javadoc.", "why": "Javadoc binds to main.",
            "suggestion": "replacement",
        }
        answers = [
            json.dumps({"body": "Review summary", "comments": [comment]}),
            '{"anchors":[{"index":0,"line":24}]}',
        ]
        metadata = {"number": 123, "base": {"sha": "base"}, "head": {"sha": "head"}}
        diff = (
            'diff --git a/Example.java b/Example.java\n'
            '--- a/Example.java\n+++ "b/Example.java"\n'
            '@@ -21,6 +21,10 @@\n'
            ' /**\n  * Runs.\n  */\n'
            '+public static void main(String[] args) {\n+    run();\n+}\n+\n'
            ' public void run() {\n }\n \n'
            '@@ -50 +54,2 @@\n // Other behavior.\n+int count = 1;\n'
        )
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.object(runner, "model_pass", side_effect=answers) as model,
        ):
            bundle = Path(directory)
            (bundle / "pr.diff").write_text(
                diff + 'diff --git a/Other.java b/Other.java\n'
                '--- a/Other.java\n+++ b/Other.java\n@@ -1 +1 @@\n-before\n+unrelated change\n',
                encoding="utf-8",
            )
            result = runner.verify_findings(
                "Check both passes.", Path("source"), bundle, prepare.parse_diff(diff), metadata, target=Path("target"),
            )

        self.assertEqual(model.call_count, 2)
        self.assertEqual(model.call_args.kwargs["phase"], "anchor-repair")
        self.assertIs(model.call_args.kwargs["allow_tools"], False)
        self.assertIn('"changed_lines": [24, 25, 26, 27, 55]', model.call_args.args[2])
        self.assertIn(json.dumps(comment["why"]), model.call_args.args[2])
        self.assertIn(json.dumps(diff), model.call_args.args[2])
        self.assertNotIn("unrelated change", model.call_args.args[2])
        self.assertNotIn('"suggestion": "replacement"', model.call_args.args[2])
        self.assertEqual(result["review"]["comments"], [
            {"path": "Example.java", "line": 24, "side": "RIGHT", "body": (
                "**Finding:** The inserted main method separates run from its Javadoc.\n\n**Why:** Javadoc binds to main."
            )},
        ])

    def test_verifier_keeps_valid_comments_when_other_anchors_cannot_be_repaired(self):
        comment = {"kind": "review", "path": "example.txt", "line": 2, "finding": "Keep this finding.", "why": "Evidence."}
        answers = [
            json.dumps({"body": "Do not repeat the dropped finding.", "comments": [
                comment, {**comment, "line": 1, "finding": "Drop this finding."},
            ]}),
            '{"anchors":[{"index":0,"line":3},{"index":1,"line":1}]}',
        ]
        metadata = {"number": 123, "base": {"sha": "base"}, "head": {"sha": "head"}}
        scope = {"example.txt": {"hunks": [(1, 3)], "changed_lines": [2, 3]}}
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.object(runner, "model_pass", side_effect=answers),
        ):
            bundle = Path(directory)
            (bundle / "pr.diff").write_text(
                "diff --git a/example.txt b/example.txt\n"
                "--- a/example.txt\n+++ b/example.txt\n@@ -1 +1,3 @@\n context\n+first\n+second\n",
                encoding="utf-8",
            )
            result = runner.verify_findings(
                "Check both passes.", Path("source"), bundle, scope, metadata, target=Path("target"),
            )
        self.assertEqual(result["review"]["comments"], [
            {"path": "example.txt", "line": 2, "side": "RIGHT", "body": "**Finding:** Keep this finding.\n\n**Why:** Evidence."},
        ])
        self.assertNotIn("Do not repeat", result["review"]["body"])

    def test_verifier_fails_if_every_finding_has_an_unrepairable_anchor(self):
        comment = {"kind": "review", "path": "example.txt", "line": 1, "finding": "A finding", "why": "Evidence"}
        answers = [json.dumps({"body": "Review summary", "comments": [comment]}), '{"anchors":[]}']
        metadata = {"number": 123, "base": {"sha": "base"}, "head": {"sha": "head"}}
        scope = {"example.txt": {"hunks": [(1, 2)], "changed_lines": [2]}}
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.object(runner, "model_pass", side_effect=answers),
        ):
            bundle = Path(directory)
            (bundle / "pr.diff").write_text(
                "diff --git a/example.txt b/example.txt\n"
                "--- a/example.txt\n+++ b/example.txt\n@@ -1 +1,2 @@\n context\n+added\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "Review validation failed: none of the findings could be anchored"):
                runner.verify_findings(
                    "Check both passes.", Path("source"), bundle, scope, metadata, target=Path("target"),
                )

    def test_verifier_fails_closed_after_two_invalid_answers(self):
        comment = {"kind": "review", "path": "example.txt", "line": 2, "finding": "A finding", "why": "Evidence"}
        answers = ["No findings.", json.dumps({"comments": [comment]})]
        metadata = {"number": 123, "base": {"sha": "base"}, "head": {"sha": "head"}}
        scope = {"example.txt": {"hunks": [(1, 2)], "changed_lines": [2]}}
        with (
            mock.patch.object(runner, "model_pass", side_effect=answers) as model,
            self.assertRaisesRegex(RuntimeError, "after two attempts: .*must include a review summary"),
        ):
            runner.verify_findings(
                "Check both passes.", Path("source"), Path("input"), scope, metadata, target=Path("target"),
            )
        self.assertEqual(model.call_count, 2)

    def test_model_pass_uses_the_final_answer_not_progress_or_tool_output(self):
        response = subprocess.CompletedProcess([], 0, stdout=(
            'AWF startup log\n'
            '{"type":"assistant.message","data":{"content":"I will check the code."}}\n'
            '{"type":"tool.execution_complete","data":{"result":{"content":"File contents"}}}\n'
            '{"type":"assistant.message","data":{"content":"{\\"comments\\":[]}"}}\n'
            '{"type":"result","data":{}}\n'
        ))
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.object(runner, "run_in_sandbox", return_value=response) as launch,
        ):
            bundle = Path(directory)
            self.assertEqual(
                runner.model_pass("model", "high", "prompt", bundle / "checkout", bundle, target=bundle / "target", phase="review"),
                '{"comments":[]}',
            )
            self.assertEqual((bundle / "prompt.txt").read_text(encoding="utf-8"), "prompt")
            self.assertIn("shell", launch.call_args.args[2])
            self.assertEqual(launch.call_args.kwargs["phase"], "review")

    def test_model_pass_allows_urls_only_when_tools_are_enabled(self):
        response = subprocess.CompletedProcess(
            [], 0, stdout='{"type":"assistant.message","data":{"content":"Done."}}\n',
        )
        for allow_tools in (True, False):
            with (
                self.subTest(allow_tools=allow_tools),
                tempfile.TemporaryDirectory() as directory,
                mock.patch.object(runner, "run_in_sandbox", return_value=response) as launch,
                mock.patch.object(runner, "progress"),
            ):
                bundle = Path(directory)
                runner.model_pass(
                    "model", "high", "prompt", bundle / "checkout", bundle,
                    target=bundle / "target", phase="review", allow_tools=allow_tools,
                )
                args = launch.call_args.args[2]
                self.assertEqual("--allow-all-urls" in args, allow_tools)
                self.assertEqual("--allow-tool" in args, allow_tools)
                self.assertEqual("--deny-tool" in args, not allow_tools)
                self.assertNotIn("--allow-all-tools", args)

    def test_model_pass_logs_tool_activity_in_order_without_publishing_drafts(self):
        answer = (
            "A finding to verify.\n\n"
            "::warning::fixture-token\r##[error]\n"
            "-----BEGIN PRIVATE KEY-----\n"
            "fixture-key-data\n"
            "-----END PRIVATE KEY-----"
        )
        events = [
            {"type": "assistant.message", "data": {"toolRequests": [{
                "toolCallId": "shell", "name": "shell",
                "arguments": {"command": "./gradlew :api:test --tests 'com.example.*Test' --no-daemon"},
            }, {"toolCallId": "denied", "name": "write", "arguments": {"content": "private-write"}}]}},
            {"type": "assistant.usage", "data": {"duration": 4500}},
            {"type": "tool.execution_start", "timestamp": "2026-11-23T01:00:00Z", "data": {
                "toolCallId": "shell", "toolName": "shell",
            }},
            {"type": "tool.execution_start", "timestamp": "2026-11-23T01:00:01Z", "data": {
                "toolCallId": "view", "toolName": "view",
                "arguments": {"path": "api/build.gradle.kts", "view_range": [1, 80]},
            }},
            {"type": "tool.execution_complete", "timestamp": "2026-11-23T01:00:02Z", "data": {
                "toolCallId": "view", "success": True,
            }},
            {"type": "tool.execution_complete", "timestamp": "2026-11-23T01:00:42Z", "data": {
                "toolCallId": "shell", "success": False, "result": {
                    "content": "secret output \u00e9",
                    "contents": [{"type": "shell_exit", "exitCode": 2}],
                },
            }},
            {"type": "tool.execution_complete", "data": {
                "toolCallId": "denied", "success": False, "error": {"message": "Tool denied by policy"},
            }},
            {"type": "assistant.message", "data": {"content": "private-draft"}},
            {"type": "assistant.message", "data": {"content": answer}},
            {"type": "result"},
            {"type": "private-unknown-event"},
        ]
        response = subprocess.CompletedProcess([], 0, stdout="\n".join(map(json.dumps, events)))
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.object(runner, "run_in_sandbox", return_value=response),
            mock.patch.object(runner.time, "monotonic", side_effect=[100, 150]),
            mock.patch.dict(os.environ, {"GH_TOKEN": "fixture-token"}),
            mock.patch.object(runner, "progress") as log,
        ):
            bundle = Path(directory)
            self.assertEqual(
                runner.model_pass("model", "max", "prompt", bundle, bundle, target=bundle / "target", phase="review"),
                answer,
            )
        lines = [call.args[0] for call in log.call_args_list]
        self.assertIn(
            "review: 2026-11-23T01:00:00.000+00:00 tool #1 shell started "
            "./gradlew :api:test --tests 'com.example.*Test' --no-daemon",
            lines,
        )
        self.assertIn("review: model call finished in 4.5s", lines)
        self.assertIn("review: 2026-11-23T01:00:42.000+00:00 tool #1 shell failed in 42.0s (exit 2), result 16 B", lines)
        self.assertIn("review: 2026-11-23T01:00:01.000+00:00 tool #2 view started api/build.gradle.kts lines 1-80", lines)
        self.assertLess(
            next(i for i, line in enumerate(lines) if "tool #1 shell started" in line),
            next(i for i, line in enumerate(lines) if "tool #2 view finished" in line),
        )
        self.assertIn("review: tool #3 unknown failed, permission denied", lines)
        self.assertEqual(lines[-6:], [
            "review: completed after 50.0s. Last recorded event: result.",
            "review: final answer",
            "review: | A finding to verify.",
            "review: | ",
            r"review: | \u003a\u003awarning\u003a\u003a[redacted]\r##\u005berror]",
            "review: | [redacted private key]",
        ])
        self.assertFalse(any("private-" in line or "secret output" in line or "Tool denied by policy" in line for line in lines))

    def test_unsafe_invocations_are_omitted(self):
        for command in (
            "TOKEN=fixture-secret ./gradlew test",
            "./gradlew test -PrepoToken=fixture-secret",
            "./gradlew test --tests fixture-secret; echo fixture-secret",
            "cd /tmp && ./gradlew :fixture-secret:test",
            "cd . && ./gradlew test && echo fixture-secret",
            "./gradlew test\nfixture-secret",
            "bash -c 'fixture-secret'",
        ):
            with self.subTest(command=command):
                self.assertEqual(runner.shell_preview(command, Path("/tmp/run")), "")
        self.assertEqual(runner.shell_preview("go test ./...", Path("/tmp/run")), "go test ./...")
        self.assertEqual(
            runner.shell_preview(
                "cd /tmp/run/pass-abc/checkout/java && ./gradlew :api:compileJava --no-daemon", Path("/tmp/run"),
            ),
            "cd java && ./gradlew :api:compileJava --no-daemon",
        )
        self.assertEqual(
            runner.tool_preview("grep", {
                "path": "/tmp/run/pass-abc/checkout/api", "glob": "**/*.java", "pattern": "fixture-secret",
            }, Path("/tmp/run")),
            "api '**/*.java'",
        )
        self.assertEqual(runner.tool_preview("view", {"path": "/tmp/fixture-secret"}, Path("/tmp/run")), "")

    def test_preview_limits_do_not_drop_tool_events(self):
        events = [
            event
            for call in range(3)
            for event in (
                {"type": "tool.execution_start", "data": {
                    "toolCallId": str(call), "toolName": "shell",
                    "arguments": {"command": "pytest -q"},
                }},
                {"type": "tool.execution_complete", "data": {
                    "toolCallId": str(call), "success": True, "result": {"content": "abcdefgh"},
                }},
            )
        ]
        with (
            mock.patch.object(runner, "PASS_PREVIEW_BYTES", 7),
            mock.patch.object(runner, "progress") as log,
        ):
            runner.report_activity("review", 3, "completed", events, Path("run"))
        lines = [call.args[0] for call in log.call_args_list]
        self.assertEqual(lines[:4], [
            "review: tool #1 shell started pytest  [truncated]",
            "review: tool #1 shell finished, result 8 B",
            "review: tool #2 shell started [omitted: pass limit]",
            "review: tool #2 shell finished, result 8 B",
        ])
        self.assertIn("review: tool #3 shell finished, result 8 B", lines)

    def test_model_pass_reports_the_cli_error_from_json(self):
        response = subprocess.CompletedProcess(
            [], 1, stdout='{"type":"session.error","data":{"message":"Model not available."}}\n', stderr=""
        )
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.object(runner, "run_in_sandbox", return_value=response),
            self.assertRaisesRegex(RuntimeError, r"Model not available\."),
        ):
            bundle = Path(directory)
            runner.model_pass("model", "max", "prompt", bundle / "checkout", bundle, target=bundle / "target", phase="review")

    def test_model_pass_redacts_the_token_in_stderr_diagnostics(self):
        response = subprocess.CompletedProcess([], 1, stdout="", stderr="AWF failed with fixture-token\n")
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.dict(os.environ, {"COPILOT_GITHUB_TOKEN": "fixture-token"}),
            mock.patch.object(runner, "run_in_sandbox", return_value=response),
            self.assertRaisesRegex(RuntimeError, r"AWF failed with \[redacted\]"),
        ):
            bundle = Path(directory)
            runner.model_pass("model", "max", "prompt", bundle / "checkout", bundle, target=bundle / "target", phase="review")

    def test_model_pass_rejects_partial_output_on_timeout(self):
        response = subprocess.CompletedProcess(
            [], 124, stdout=(
                '{"type":"assistant.message","data":{"content":"{\\"comments\\":[]}"}}\n'
                '{"type":"tool.execution_start","data":{"toolCallId":"x","toolName":"private-name"}}\n'
                '{"type":"tool.execution_start","data":{"toolCallId":"y","toolName":"shell"}}\n'
                '{"type":"tool.execution_start","data":{"toolCallId":"z","toolName":"view"}}\n'
                '{"type":"tool.execution_complete","data":{"toolCallId":"z"}}\n'
            ),
        )
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.object(runner, "run_in_sandbox", return_value=response),
            mock.patch.object(runner.time, "monotonic", side_effect=[0, 3600]),
            mock.patch.object(runner, "progress") as log,
            self.assertRaisesRegex(RuntimeError, "exceeded its .* sandbox time limit"),
        ):
            bundle = Path(directory)
            runner.model_pass("model", "max", "prompt", bundle / "checkout", bundle, target=bundle / "target", phase="review")
        self.assertEqual(log.call_args_list[-3:], [
            mock.call("review: sandbox timeout after 3600.0s. Last recorded event: tool.execution_complete."),
            mock.call("review: tool #1 unknown has no recorded finish."),
            mock.call("review: tool #2 shell has no recorded finish."),
        ])
        self.assertNotIn(mock.call("review: final answer"), log.call_args_list)

    def test_runner_timeout_reports_partial_activity(self):
        timeout = subprocess.TimeoutExpired([], 3720, output=(
            b'{"type":"tool.execution_start","data":{"toolCallId":"x","toolName":"shell",'
            b'"arguments":{"command":"./gradlew :api:test --offline"}}}\n{"unfinished"'
        ))
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.object(runner, "run_in_sandbox", side_effect=timeout),
            mock.patch.object(runner.time, "monotonic", side_effect=[0, 3720]),
            mock.patch.object(runner, "progress") as log,
            self.assertRaises(subprocess.TimeoutExpired),
        ):
            bundle = Path(directory)
            runner.model_pass("model", "max", "prompt", bundle, bundle, target=bundle / "target", phase="review")
        log.assert_any_call("review: tool #1 shell started ./gradlew :api:test --offline")
        log.assert_any_call("review: tool #1 shell has no recorded finish.")

    def test_checkout_rejects_a_moved_pr_head(self):
        response = subprocess.CompletedProcess([], 0, stdout="e" * 40 + "\n")
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(sandbox, "run", return_value=response) as git:
            with self.assertRaisesRegex(RuntimeError, "PR head changed"):
                sandbox.checkout_pr("org/repo", 123, "d" * 40, Path(directory) / "checkout")
            self.assertEqual(git.call_count, 3)
            self.assertEqual(git.call_args.args[0][-2:], ["rev-parse", "FETCH_HEAD"])

    def test_target_checkout_pins_the_tip_of_the_named_target_branch(self):
        response = subprocess.CompletedProcess([], 0, stdout="e" * 40 + "\n")
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(sandbox, "run", return_value=response) as git:
            target = Path(directory) / "target"
            sandbox.checkout_target_branch("org/repo", "release/1.x", target)
            self.assertEqual(
                git.call_args_list[1].args[0][-2:], ["https://github.com/org/repo.git", "refs/heads/release/1.x"],
            )
            self.assertEqual(git.call_args.args[0][-4:], ["checkout", "--quiet", "--detach", "e" * 40])

    def test_sandbox_cannot_change_seed_checkout_or_receive_repository_tokens(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            seed = root / "seed"
            seed.mkdir()
            (seed / ".git").mkdir()
            (seed / ".git" / "HEAD").write_text("d" * 40 + "\n", encoding="utf-8")
            (seed / "code").write_text("PR contents", encoding="utf-8")
            target = root / "target"
            (target / ".git").mkdir(parents=True)
            (target / ".git" / "HEAD").write_text("e" * 40 + "\n", encoding="utf-8")
            inputs = root / "input"
            inputs.mkdir()
            cli = root / "npm" / ".bin" / "copilot"
            awf = root / "awf-bin" / "awf"

            def launch(args, *, cwd, env, **kwargs):
                self.assertEqual(args[:2], ["/usr/bin/sudo", "--non-interactive"])
                self.assertEqual(args[3], str(awf))
                preserved = set(args[2].removeprefix("--preserve-env=").split(","))
                self.assertEqual(preserved, env.keys())
                self.assertIn("COPILOT_GITHUB_TOKEN", preserved)
                self.assertIn(f"{inputs}:{inputs}:ro", args)
                self.assertIn(f"{target}:{target}:ro", args)
                self.assertIn(str(target), args[args.index("--") + 1:])
                logs = Path(args[args.index("--proxy-logs-dir") + 1])
                self.assertEqual(logs.parent, root / "pr-review-usage")
                self.assertTrue(logs.is_dir())
                self.assertNotIn("--proxy-logs-dir", args[args.index("--") + 1:])
                self.assertEqual(env["GITHUB_WORKSPACE"], str(cwd))
                self.assertEqual(Path(env["HOME"]).parent, cwd.parent)
                self.assertNotIn("PATH", env)
                self.assertNotIn("GH_TOKEN", env)
                self.assertNotIn("ACTIONS_RUNTIME_TOKEN", env)
                self.assertNotIn("GITHUB_TOKEN", env)
                config = json.loads(Path(args[args.index("--config") + 1]).read_text(encoding="utf-8"))
                self.assertTrue(config["network"]["isolation"])
                self.assertTrue({
                    "github.com", "release-assets.githubusercontent.com", "opentelemetry.io",
                    "services.gradle.org", "repo.maven.apache.org", "api.foojay.io", "cdn.azul.com",
                    "bcr.bazel.build", "pub.dev", "api.nuget.org", "repo.hex.pm", "proxy.golang.org",
                    "maven.pkg.jetbrains.space", "registry.npmjs.org", "repo.packagist.org",
                    "pypi.org", "files.pythonhosted.org", "rubygems.org", "static.crates.io", "swift.org",
                }.issubset(config["network"]["allowDomains"]))
                self.assertNotIn("*", config["network"]["allowDomains"])
                self.assertFalse(any("://" in domain for domain in config["network"]["allowDomains"]))
                self.assertEqual((cwd / "code").read_text(encoding="utf-8"), "PR contents")
                (cwd / "code").write_text("scratch edit", encoding="utf-8")

            with (
                mock.patch.dict(os.environ, {
                    "GH_TOKEN": "fixture",
                    "GITHUB_TOKEN": "fixture",
                    "COPILOT_GITHUB_TOKEN": "fixture",
                    "ACTIONS_RUNTIME_TOKEN": "fixture",
                    "AWF_IMAGE_TAG": "fixture",
                    "RUNNER_TEMP": directory,
                }),
                mock.patch.object(sandbox.shutil, "which", side_effect={"copilot": str(cli), "awf": str(awf)}.get),
                mock.patch.object(sandbox, "run", side_effect=launch) as run,
            ):
                sandbox.run_in_sandbox(seed, inputs, ["--help"], target=target, phase="review")
                sandbox.run_in_sandbox(seed, inputs, ["--help"], target=target, phase="simplification")
                self.assertNotEqual(run.call_args_list[0].kwargs["cwd"], run.call_args_list[1].kwargs["cwd"])
            self.assertEqual((seed / "code").read_text(encoding="utf-8"), "PR contents")

    def test_usage_summary_counts_distinct_requests_and_includes_the_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            summary = Path(directory) / "summary.md"
            with (
                mock.patch.dict(os.environ, {"RUNNER_TEMP": directory, "GITHUB_STEP_SUMMARY": str(summary)}),
                mock.patch("builtins.print") as log,
                mock.patch.object(usage, "progress") as requests,
            ):
                entry = {
                    "event": "token_usage", "request_id": "first-request", "input_tokens": 1250,
                    "output_tokens": 100, "cache_read_tokens": 40, "cache_write_tokens": 20,
                    "ai_credits_this_response": 0.25, "ai_credits_total": 0.25, "path": "not for publication",
                    "duration_ms": 4500,
                }
                for phase, records in (
                    ("review", [entry, entry, {**entry, "request_id": "second-request", "ai_credits_total": 0.5}]),
                    ("verification-retry", [entry]),
                ):
                    logs = usage.proxy_logs_dir(phase) / "api-proxy-logs"
                    logs.mkdir(parents=True)
                    (logs / "token-usage.jsonl").write_text(
                        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8",
                    )
                usage.main()
            result = summary.read_text(encoding="utf-8")
            log.assert_called_once_with(result, end="")
            self.assertIn("| Review | 2,500 | 200 | 80 | 40 | 0.500000 |", result)
            self.assertIn("| Verification retry | 1,250 | 100 | 40 | 20 | 0.250000 |", result)
            self.assertIn("| Recorded total | 3,750 | 300 | 120 | 60 | 0.750000 |", result)
            self.assertNotIn("not for publication", result)
            self.assertEqual(requests.call_count, 3)
            requests.assert_any_call(
                "Review request #2 in 4.5s: 1,250 input, 100 output, 40 cache read, "
                "20 cache write, 0.250000 estimated AI credits"
            )
            self.assertNotIn("not for publication", str(requests.call_args_list))

    def test_usage_summary_does_not_report_missing_logs_or_estimates_as_zero(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.object(usage, "progress") as diagnostic,
        ):
            with mock.patch.dict(os.environ, {"RUNNER_TEMP": directory}):
                usage.proxy_logs_dir("review").mkdir(parents=True)
                logs = usage.proxy_logs_dir("verification") / "api-proxy-logs"
                logs.mkdir(parents=True)
                (logs / "token-usage.jsonl").write_text(
                    '{"event":"token_usage","request_id":"r","input_tokens":8,"output_tokens":2,'
                    '"cache_read_tokens":0,"cache_write_tokens":0}\n{"incomplete"\n', encoding="utf-8",
                )
                result = usage.summarize()
        self.assertIn("| Review | Unavailable | Unavailable | Unavailable | Unavailable | Unavailable |", result)
        self.assertIn("| Verification | 8 | 2 | 0 | 0 | Unavailable |", result)
        self.assertIn("| Recorded total | 8 | 2 | 0 | 0 | Unavailable |", result)
        diagnostic.assert_any_call("Verification request #1: 8 input, 2 output, 0 cache read, 0 cache write")
        self.assertEqual(diagnostic.call_count, 3)

    def test_only_the_latest_submitted_copilot_review_can_supply_a_hint(self):
        reviews = [[{
            "id": 1, "user": {"login": "copilot-pull-request-reviewer[bot]"}, "submitted_at": "2026-01-01",
            "body": "Needs a closer look", "html_url": "old",
        }], [{
            "id": 2, "user": {"login": " Copilot "}, "submitted_at": "2026-01-02",
            "body": "Approval recommended", "html_url": "latest",
        }, {
            "id": 3, "user": {"login": "contributor"}, "submitted_at": "2026-01-03",
            "body": "Needs a closer look", "html_url": "human",
        }, {
            "id": 4, "user": {"login": "copilot[bot]"}, "submitted_at": None,
            "body": "Needs a closer look", "html_url": "pending",
        }]]
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory)
            with mock.patch.object(runner, "gh_json", side_effect=[reviews, [[]]]) as api:
                self.assertEqual(runner.closer_look_context("org/repo", 123, bundle), "")
                self.assertIn("/reviews/2/comments?", api.call_args.args[0][-1])
            with mock.patch.object(runner, "gh_json", side_effect=[reviews, [[{
                "body": "Needs a closer look", "html_url": "inline",
            }]]]):
                self.assertEqual(
                    runner.closer_look_context("org/repo", 123, bundle),
                    ", including Needs a closer look inline",
                )

    def test_all_copilot_reviewer_logins_are_recognized(self):
        for login in ("Copilot", "copilot[bot]", "copilot-pull-request-reviewer", "copilot-pull-request-reviewer[bot]"):
            with self.subTest(login=login):
                review = {"user": {"login": login}, "submitted_at": "2026-01-01"}
                self.assertEqual(runner.latest_copilot_review([[review]]), review)

    def test_suggestion_limit_counts_green_lines_against_the_committed_blob(self):
        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory)
            env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}

            def git(*args: str) -> str:
                return common.run(["git", *args], cwd=checkout, env=env).stdout.strip()

            git("init", "--quiet", "--template=")
            source = checkout / "example.txt"
            source.write_bytes(b"unchanged\rcontinuation\nbefore\n")
            git("add", "example.txt")
            git("-c", "user.name=Test", "-c", "user.email=test@example.test", "commit", "--quiet", "-m", "Fixture")
            head = git("rev-parse", "HEAD")
            source.write_text("scratch edit\nbefore\n", encoding="utf-8")

            scope = {"example.txt": {"hunks": [(1, 2)], "changed_lines": [2]}}
            metadata = {"number": 123, "base": {"sha": "base"}, "head": {"sha": head}}
            for added in (14, 15):
                with self.subTest(added=added):
                    replacement = "unchanged\rcontinuation\n" + "new\n" * added
                    result = prepare.prepare_review({
                        "body": "Review summary",
                        "comments": [{
                            "kind": "simplification", "path": "example.txt",
                            "start_line": 1, "line": 2, "finding": "Simpler code. Update the import.",
                            "why": "One branch is enough.", "suggestion": replacement,
                        }],
                    }, checkout, scope, metadata)
                    review = result["review"]
                    self.assertNotIn("event", review)
                    self.assertEqual(review["commit_id"], head)
                    body = "**Simplification:** Simpler code. Update the import.\n\n**Why:** One branch is enough."
                    if added == 14:
                        body += "\n\n```suggestion\n" + replacement + "```"
                    self.assertEqual(review["comments"][0]["body"], body)

    def test_plain_comment_does_not_read_source(self):
        with mock.patch.object(prepare, "run") as git:
            result = prepare.prepare_review(
                {"body": "Review summary", "comments": [{
                    "kind": "review", "path": "example.txt", "line": 2, "finding": "A finding", "why": "Evidence",
                }]},
                Path("source"),
                {"example.txt": {"hunks": [(1, 2)], "changed_lines": [2]}},
                {"number": 123, "base": {"sha": "base"}, "head": {"sha": "head"}},
            )
        git.assert_not_called()
        self.assertEqual(result["review"]["comments"], [
            {"path": "example.txt", "line": 2, "side": "RIGHT", "body": "**Finding:** A finding\n\n**Why:** Evidence"},
        ])

    def test_findings_publish_as_a_comment_review_with_only_the_notice(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "review-result.json"
            review = {
                "commit_id": "head",
                "body": "One finding.",
                "comments": [{"path": "example.txt", "line": 2, "side": "RIGHT", "body": "A finding"}],
            }
            write_json(path, {"repository": "org/repo", "pr": 123, "base_sha": "base", "review": review})

            def submit(args):
                self.assertEqual(args[:5], ["api", "repos/org/repo/pulls/123/reviews", "--method", "POST", "--input"])
                self.assertEqual(json.loads(Path(args[5]).read_text(encoding="utf-8")), {
                    **review, "body": post.EXPERIMENT_NOTICE, "event": "COMMENT",
                })
                return {"html_url": "https://example.test/review"}

            with (
                mock.patch.object(post, "check_snapshot") as check,
                mock.patch.object(post, "gh_json", side_effect=submit) as api,
                mock.patch.object(post, "progress") as log,
            ):
                post.publish_result("org/repo", 123, path)
            check.assert_called_once_with("org/repo", 123, "base", "head")
            api.assert_called_once()
            log.assert_called_once_with("Published review https://example.test/review")

    def test_empty_review_posts_a_top_level_comment_without_a_user_token(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "review-result.json"
            write_json(path, {
                "repository": "org/repo", "pr": 123, "base_sha": "base",
                "review": {"commit_id": "head", "comments": []},
            })
            with (
                mock.patch.object(post, "check_snapshot") as check,
                mock.patch.object(post, "gh_json", return_value={"html_url": "https://example.test"}) as api,
            ):
                post.publish_result("org/repo", 123, path)
            check.assert_called_once_with("org/repo", 123, "base", "head")
            api.assert_called_once_with(
                ["api", "-X", "POST", "repos/org/repo/issues/123/comments", "-f",
                 f"body={post.EXPERIMENT_NOTICE}\n\nNo findings."]
            )

    def test_publisher_rejects_result_for_another_repository_or_pr(self):
        for repo, pr in (("other/repo", 123), ("org/repo", 456)):
            with self.subTest(repo=repo, pr=pr), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "result.json"
                write_json(path, {"repository": repo, "pr": pr})
                with (
                    mock.patch.object(post, "gh_json") as api,
                    self.assertRaisesRegex(RuntimeError, "different PR"),
                ):
                    post.publish_result("org/repo", 123, path)
                api.assert_not_called()

    def test_snapshot_rejects_closed_or_changed_prs_in_the_target_repository(self):
        for state, base, head in (("closed", "base", "head"), ("open", "new", "head"), ("open", "base", "new")):
            with (
                self.subTest(state=state, base=base, head=head),
                mock.patch.object(common, "pr_metadata", return_value={
                    "state": state, "base": {"sha": base}, "head": {"sha": head},
                }) as metadata,
                self.assertRaisesRegex(RuntimeError, "changed or closed"),
            ):
                common.check_snapshot("org/repo", 123, "base", "head")
                metadata.assert_called_once_with("org/repo", 123)

    def test_windows_subprocess_uses_no_window(self):
        with (
            mock.patch.object(common.subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True),
            mock.patch.object(common.subprocess, "run") as run,
        ):
            common.run(["copilot", "--version"])
        self.assertEqual(run.call_args.kwargs["creationflags"], 0x08000000)


if __name__ == "__main__":
    unittest.main()
