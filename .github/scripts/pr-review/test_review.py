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
            "base": {"sha": "base"}, "head": {"sha": "head"},
        }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "review-result.json"
            with (
                mock.patch.object(runner, "build_bundle", return_value=(metadata, {})) as build,
                mock.patch.object(runner, "checkout_pr") as checkout,
                mock.patch.object(runner, "closer_look_context", return_value=", including Needs a closer look https://example.test"),
                mock.patch.object(runner, "model_pass", side_effect=["review", "simplifications", '{"comments":[]}']) as model,
            ):
                runner.prepare_result("org/repo", 123, path)
            first, second, final = [call.args for call in model.call_args_list]
            build.assert_called_once_with("org/repo", 123, first[4])
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

    def test_verifier_retries_a_plain_text_answer(self):
        checkout, bundle = Path("source"), Path("input")
        metadata = {"number": 123, "base": {"sha": "base"}, "head": {"sha": "head"}}
        with mock.patch.object(
            runner, "model_pass", side_effect=["No findings.", '```json\n{"comments":[]}\n```']
        ) as model:
            result = runner.verify_findings("Check both passes.", checkout, bundle, {}, metadata)

        self.assertEqual(result, {
            "pr": 123,
            "base_sha": "base",
            "review": {"commit_id": "head", "body": "No findings.", "comments": []},
        })
        first, retry = [call.args for call in model.call_args_list]
        self.assertEqual(first, (runner.OPUS, "high", "Check both passes.", checkout, bundle))
        self.assertEqual(retry[:2], first[:2])
        self.assertTrue(retry[2].startswith(first[2]))
        self.assertIn("did not pass validation", retry[2])
        self.assertEqual(retry[3:], first[3:])

    def test_verifier_retries_when_json_anchors_a_comment_to_an_unchanged_line(self):
        comment = {"kind": "review", "path": "example.txt", "line": 1, "body": "A finding"}
        answers = [
            json.dumps({"body": "Review summary", "comments": [{**comment, "line": line}]})
            for line in (1, 2)
        ]
        metadata = {"number": 123, "base": {"sha": "base"}, "head": {"sha": "head"}}
        scope = {"example.txt": {"hunks": [(1, 2)], "changed_lines": [2]}}
        with mock.patch.object(runner, "model_pass", side_effect=answers) as model:
            result = runner.verify_findings("Check both passes.", Path("source"), Path("input"), scope, metadata)

        self.assertEqual(model.call_count, 2)
        self.assertIn("Invalid diff anchor for 'example.txt':1-1", model.call_args.args[2])
        self.assertEqual(result["review"]["comments"], [
            {"path": "example.txt", "line": 2, "side": "RIGHT", "body": "A finding"},
        ])

    def test_verifier_fails_closed_after_two_invalid_answers(self):
        comment = {"kind": "review", "path": "example.txt", "line": 2, "body": "A finding"}
        answers = ["No findings.", json.dumps({"comments": [comment]})]
        metadata = {"number": 123, "base": {"sha": "base"}, "head": {"sha": "head"}}
        scope = {"example.txt": {"hunks": [(1, 2)], "changed_lines": [2]}}
        with (
            mock.patch.object(runner, "model_pass", side_effect=answers) as model,
            self.assertRaisesRegex(RuntimeError, "after two attempts: .*must include a review summary"),
        ):
            runner.verify_findings("Check both passes.", Path("source"), Path("input"), scope, metadata)
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
            self.assertEqual(runner.model_pass("model", "high", "prompt", bundle / "checkout", bundle), '{"comments":[]}')
            self.assertEqual((bundle / "prompt.txt").read_text(encoding="utf-8"), "prompt")
            self.assertIn("shell", launch.call_args.args[2])

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
            runner.model_pass("model", "max", "prompt", bundle / "checkout", bundle)

    def test_model_pass_redacts_the_token_in_stderr_diagnostics(self):
        response = subprocess.CompletedProcess([], 1, stdout="", stderr="AWF failed with fixture-token\n")
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.dict(os.environ, {"COPILOT_GITHUB_TOKEN": "fixture-token"}),
            mock.patch.object(runner, "run_in_sandbox", return_value=response),
            self.assertRaisesRegex(RuntimeError, r"AWF failed with \[redacted\]"),
        ):
            bundle = Path(directory)
            runner.model_pass("model", "max", "prompt", bundle / "checkout", bundle)

    def test_model_pass_rejects_partial_output_on_timeout(self):
        response = subprocess.CompletedProcess(
            [], 124, stdout='{"type":"assistant.message","data":{"content":"{\\"comments\\":[]}"}}\n',
        )
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.object(runner, "run_in_sandbox", return_value=response),
            self.assertRaisesRegex(RuntimeError, "exceeded its .* sandbox time limit"),
        ):
            bundle = Path(directory)
            runner.model_pass("model", "max", "prompt", bundle / "checkout", bundle)

    def test_checkout_rejects_a_moved_pr_head(self):
        response = subprocess.CompletedProcess([], 0, stdout="e" * 40 + "\n")
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(sandbox, "run", return_value=response) as git:
            with self.assertRaisesRegex(RuntimeError, "PR head changed"):
                sandbox.checkout_pr("org/repo", 123, "d" * 40, Path(directory) / "checkout")
            self.assertEqual(git.call_count, 3)
            self.assertEqual(git.call_args.args[0][-2:], ["rev-parse", "FETCH_HEAD"])

    def test_sandbox_cannot_change_seed_checkout_or_receive_repository_tokens(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            seed = root / "seed"
            seed.mkdir()
            (seed / ".git").mkdir()
            (seed / ".git" / "HEAD").write_text("d" * 40 + "\n", encoding="utf-8")
            (seed / "code").write_text("PR contents", encoding="utf-8")
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
                self.assertEqual(env["GITHUB_WORKSPACE"], str(cwd))
                self.assertEqual(Path(env["HOME"]).parent, cwd.parent)
                self.assertNotIn("PATH", env)
                self.assertNotIn("GH_TOKEN", env)
                self.assertNotIn("ACTIONS_RUNTIME_TOKEN", env)
                self.assertNotIn("GITHUB_TOKEN", env)
                config = json.loads(Path(args[args.index("--config") + 1]).read_text(encoding="utf-8"))
                self.assertEqual(config["network"]["allowDomains"], [
                    "github.com", "release-assets.githubusercontent.com",
                ])
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
                sandbox.run_in_sandbox(seed, inputs, ["--help"])
                sandbox.run_in_sandbox(seed, inputs, ["--help"])
                self.assertNotEqual(run.call_args_list[0].kwargs["cwd"], run.call_args_list[1].kwargs["cwd"])
            self.assertEqual((seed / "code").read_text(encoding="utf-8"), "PR contents")

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
            source.write_text("unchanged\nbefore\n", encoding="utf-8")
            git("add", "example.txt")
            git("-c", "user.name=Test", "-c", "user.email=test@example.test", "commit", "--quiet", "-m", "Fixture")
            head = git("rev-parse", "HEAD")
            source.write_text("scratch edit\nbefore\n", encoding="utf-8")

            scope = {"example.txt": {"hunks": [(1, 2)], "changed_lines": [2]}}
            metadata = {"number": 123, "base": {"sha": "base"}, "head": {"sha": head}}
            for added in (14, 15):
                with self.subTest(added=added):
                    replacement = "unchanged\n" + "new\n" * added
                    result = prepare.prepare_review({
                        "body": "Review summary",
                        "comments": [{
                            "kind": "simplification", "path": "example.txt",
                            "start_line": 1, "line": 2, "body": "Simpler code. Update the import.",
                            "suggestion": replacement,
                        }],
                    }, checkout, scope, metadata)
                    review = result["review"]
                    self.assertNotIn("event", review)
                    self.assertEqual(review["commit_id"], head)
                    body = "[Simplification] Simpler code. Update the import."
                    if added == 14:
                        body += "\n\n```suggestion\n" + replacement + "```"
                    self.assertEqual(review["comments"][0]["body"], body)

    def test_plain_comment_does_not_read_source(self):
        with mock.patch.object(prepare, "run") as git:
            result = prepare.prepare_review(
                {"body": "Review summary", "comments": [{
                    "kind": "review", "path": "example.txt", "line": 2, "body": "A finding",
                }]},
                Path("source"),
                {"example.txt": {"hunks": [(1, 2)], "changed_lines": [2]}},
                {"number": 123, "base": {"sha": "base"}, "head": {"sha": "head"}},
            )
        git.assert_not_called()
        self.assertEqual(result["review"]["comments"], [
            {"path": "example.txt", "line": 2, "side": "RIGHT", "body": "A finding"},
        ])

    def test_findings_publish_as_a_comment_review(self):
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
                self.assertEqual(json.loads(Path(args[5]).read_text(encoding="utf-8")), {**review, "event": "COMMENT"})
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
                ["api", "-X", "POST", "repos/org/repo/issues/123/comments", "-f", "body=No findings."]
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
