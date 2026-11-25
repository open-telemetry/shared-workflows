import json
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from types import SimpleNamespace
from unittest.mock import Mock, patch

from archive import Archive, encode
from collect_hosted import capture_days, collect_batch
from config import target_repository
from github_data import BudgetExhausted, DataError
from publish_instructions import BOT, BRANCH, pr_body, publish, publishing_plan
from scorecard import REPOS
from shared_data import SharedData
from test_scorecard import git_at, shared_remote


class HostedCollectionTests(unittest.TestCase):
    def test_assessment_budget_pause_stops_the_batch_without_marking_it_failed(self):
        day = date(2026, 10, 4)
        pulls = [{"repo": REPOS[0], "number": number} for number in (1, 2, 3)]
        archive = Mock()
        args = SimpleNamespace(max_requests=10, max_backfill_days=0, max_assessments=5,
                               work=Path("work"))
        with (patch("collect_hosted.capture_days", return_value=[]),
              patch("collect_hosted.eligible_pulls", return_value=pulls),
              patch("collect_hosted.Copilot") as model,
              patch("collect_hosted.assess_cached", side_effect=[
                  (None, False), ([], True), (None, False), BudgetExhausted("expired"),
              ]) as assess):
            result = collect_batch(archive, args, day, day, args.work / "git", monotonic() + 10)
        self.assertEqual(result["assessment_attempts"], 2)
        self.assertEqual(result["failures"], 0)
        self.assertEqual([call.args[3]["number"] for call in assess.call_args_list], [1, 1, 2, 2])
        model.return_value.close.assert_called_once()

    def test_live_windows_precede_bounded_backfill_for_every_repository(self):
        start, today = date(2026, 10, 1), date(2026, 10, 13)
        with TemporaryDirectory() as work, closing(Archive(Path(work) / "data.sqlite3")) as archive:
            with archive.db:
                archive.db.execute("INSERT INTO complete_days VALUES (?, ?)", (REPOS[0], str(start)))
            tasks = list(capture_days(archive, start, today, 1))
        self.assertEqual(tasks[:len(REPOS)], [(repo, date(2026, 10, 12), today) for repo in REPOS])
        self.assertEqual(tasks[len(REPOS):], [
            (REPOS[0], date(2026, 10, 2), date(2026, 10, 2)),
            *((repo, start, start) for repo in REPOS[1:]),
        ])

    def test_snapshot_keeps_evidence_for_opted_out_repo_and_imports_authors(self):
        active, inactive = REPOS[:2]
        with TemporaryDirectory() as work:
            root = Path(work)
            checkout, remote = shared_remote(root)
            with closing(Archive(root / "old.sqlite3")) as archive:
                old = SharedData(archive, [active, inactive], remote=str(remote), cwd=checkout)
                old.load()
                with archive.db:
                    for repo in (active, inactive):
                        when = "2026-10-04T00:00:00Z"
                        pull = {"repo": repo, "number": 1, "merged": when, "threads": []}
                        archive.db.execute("INSERT INTO pulls VALUES (?, ?, ?, ?, ?)",
                                           (repo, 1, "2026-10-04", when, encode(pull)))
                previous = old.publish()
            with closing(Archive(root / "new.sqlite3")) as archive:
                current = SharedData(archive, [active], remote=str(remote), cwd=checkout, snapshot=True)
                current.load(previous)
                self.assertEqual(archive.db.execute("SELECT repo FROM pulls").fetchall(), [(active,)])
                with archive.db:
                    archive.db.execute("INSERT INTO pull_authors VALUES (?, 1, 'opentelemetrybot', 'User')",
                                       (active,))
                current.prepare_commit("refs/heads/candidate", include_assessments=True)
                self.assertEqual(git_at(checkout, "ls-tree", "--name-only", "candidate").splitlines(),
                                 ["open-telemetry", "schema.json"])
                tree = git_at(checkout, "ls-tree", "-r", "--name-only", "candidate")
                self.assertIn(f"{inactive}/2026-10/1.json", tree)
                self.assertIn(f"{active}/pr-authors.json", tree)
            git_at(checkout, "push", str(remote), "refs/heads/candidate:refs/heads/data")
            with closing(Archive(root / "next.sqlite3")) as archive:
                SharedData(archive, [active], remote=str(remote), cwd=checkout, snapshot=True).load()
                self.assertEqual(archive.pull_authors(active, date(2026, 10, 4), date(2026, 10, 4)),
                                 {1: ("opentelemetrybot", "User")})


class InstructionPublishingTests(unittest.TestCase):
    def test_replacement_draft_uses_current_base_but_keeps_existing_fork_lease(self):
        repo = REPOS[0]
        base, previous, commit = "a" * 40, "b" * 40, "c" * 40
        fork = f"https://github.com/{BOT}/{repo.split('/')[1]}.git"
        with (TemporaryDirectory() as work,
              patch("publish_instructions.target_repository", return_value=(repo, True)),
              patch("publish_instructions.GitHub") as github_type,
              patch("publish_instructions.publishing_plan", return_value=(None, previous)),
              patch("publish_instructions.push_fork", return_value=fork),
              patch("publish_instructions.fork_head", side_effect=[previous, commit]),
              patch("publish_instructions.apply_draft", return_value=(Path("draft.git"), commit)) as apply,
              patch("publish_instructions.git") as git,
              patch("publish_instructions.request_review")):
            path = Path(work)
            (path / "copilot-instructions.patch").write_bytes(b"nonempty patch")
            (path / "copilot-instructions.evidence.json").write_text(
                json.dumps({"repo": repo, "revision": base}), encoding="utf-8")
            github = github_type.return_value
            github.public.return_value = {"archived": False, "disabled": False, "default_branch": "main"}
            github.get.return_value = {"object": {"sha": base}}
            github.write.return_value = {"number": 1, "html_url": f"https://github.com/{repo}/pull/1"}
            publish(repo.split("/")[1], path, dry_run=False)
        self.assertEqual(apply.call_args.args[3:6], (base, fork, None))
        self.assertIn(f"--force-with-lease=refs/heads/{BRANCH}:{previous}", git.call_args.args)
        self.assertEqual(github.write.call_args.args[:2], ("POST", f"repos/{repo}/pulls"))
        self.assertTrue(github.write.call_args.args[2]["draft"])

    def test_dispatch_accepts_only_opted_in_repository_names(self):
        self.assertEqual(target_repository("SEMANTIC-CONVENTIONS-CONFORMANCE"),
                         ("open-telemetry/semantic-conventions-conformance", False))
        for name in ("other/semantic-conventions-conformance", "not-opted-in"):
            with self.subTest(name=name), self.assertRaises(DataError):
                target_repository(name)

    def test_publisher_stops_for_a_competing_instruction_pr(self):
        pull = {"number": 515, "html_url": "https://example.test/pull/515",
                "head": {"ref": "manual", "repo": {"full_name": REPOS[0]}}}
        github = Mock()
        github.rest_pages.side_effect = [[pull], [
            {"filename": "docs.md", "previous_filename": ".github/instructions/old.instructions.md"},
        ]]
        with self.assertRaisesRegex(DataError, "Another open PR changes Copilot instructions"):
            publishing_plan(github, REPOS[0], "main")
        github.write.assert_not_called()

    def test_only_the_verified_last_bot_commit_can_be_reused(self):
        sha = "c" * 40
        name = REPOS[0].split("/")[1]
        pull = {"number": 1, "html_url": "https://example.test/pull/1",
                "head": {"ref": BRANCH, "sha": sha, "repo": {"full_name": f"{BOT}/{name}"}},
                "body": pr_body(sha), "user": {"login": BOT}}
        github = Mock()
        github.rest_pages.return_value = [pull]
        self.assertEqual(publishing_plan(github, REPOS[0], "main"), (pull, sha))
        pull["head"]["sha"] = "d" * 40
        with self.assertRaisesRegex(DataError, "manually changed bot PR"):
            publishing_plan(github, REPOS[0], "main")


if __name__ == "__main__":
    unittest.main()
