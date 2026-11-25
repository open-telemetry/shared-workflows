import json
import unittest
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from types import SimpleNamespace
from unittest.mock import Mock, patch

from archive import Archive, encode
from collect_hosted import capture_days, collect_batch, eligible_pulls
from config import target_repository
from github_data import BudgetExhausted, DataError, GitHubError
from publish_instructions import BOT, BRANCH, PENDING, apply_draft, pr_body, publish, publishing_plan
from review_instructions import git
from scorecard import REPOS
from shared_data import COLLECTION_RETRIES, SharedData
from test_scorecard import git_at, shared_remote


class HostedCollectionTests(unittest.TestCase):
    def test_rejected_pr_and_evidence_do_not_block_other_records(self):
        day = date(2026, 10, 4)
        stamp = f"{day}T08:00:00Z"
        repo, other = REPOS[:2]
        pulls = [
            {"repo": repo, "number": 1, "merged": stamp, "threads": ["blocked review"]},
            {"repo": repo, "number": 2, "merged": stamp, "threads": ["accepted review"]},
            {"repo": other, "number": 3, "merged": stamp, "threads": []},
        ]
        args = SimpleNamespace(max_requests=20, max_backfill_days=1, max_assessments=5,
                               max_evidence=5, work=Path("work"))
        with (TemporaryDirectory() as work, closing(Archive(Path(work) / "data.sqlite3")) as archive,
              patch("collect_hosted.capture_days", return_value=[(repo, day, day), (other, day, day)]),
              patch("collect_hosted.GitHub") as github_type, patch("collect_hosted.Copilot"),
              patch("collect_hosted.assess_cached", return_value=(None, False)) as assess,
              patch("collect_hosted.Evidence"),
              patch("collect_hosted.prepare", return_value={"text": "x" * 1001}),
              patch("shared_data.MAX_RECORD_BYTES", 1000),
              patch("shared_data.safe_payload", side_effect=lambda value: value.get("number") != 1)):
            github = github_type.return_value
            github.merged.side_effect = lambda name, *_: {p["number"]: stamp for p in pulls if p["repo"] == name}
            github.pulls.side_effect = lambda _, pending: [(p, (1, 0), None)
                                                          for p in pulls if p["number"] in pending]
            github.pull_authors.side_effect = lambda _, pending: [
                (n, {"login": "person", "__typename": "User"}) for n in pending
            ]
            result = collect_batch(archive, args, day, day + timedelta(days=2),
                                   args.work / "git", monotonic() + 10)
            self.assertEqual(result["failures"], 2)
            self.assertEqual(list(archive.pulls(repo, day, day)), [pulls[1]])
            self.assertEqual(list(archive.pulls(other, day, day)), [pulls[2]])
            self.assertEqual(archive.pending_days(repo, day, day), [day])
            self.assertEqual(archive.pending_days(other, day, day), [])
            self.assertIsNone(archive.get_prepared(pulls[1]))
            self.assertEqual(list(archive.pull_authors(repo, day, day)), [2])
            self.assertEqual(assess.call_count, 1)

    def test_rejected_repository_update_keeps_previous_tree_and_publishes_other_repos(self):
        repo, other = sorted(REPOS[:2])
        rejected = Mock()
        with TemporaryDirectory() as work, closing(Archive(Path(work) / "data.sqlite3")) as archive:
            checkout, remote = shared_remote(Path(work))
            shared = SharedData(archive, [repo, other], remote=str(remote), cwd=checkout, snapshot=True)
            shared.load()
            with archive.db:
                archive.db.executemany("INSERT INTO complete_days VALUES (?, '2026-10-04')", [(repo,), (other,)])
            old = shared.publish()
            with archive.db:
                archive.db.executemany("INSERT INTO complete_days VALUES (?, '2026-10-05')", [(repo,), (other,)])
                archive.db.execute("INSERT INTO pull_line_counts VALUES (?, 1, 5, 0)", (repo,))
            with patch("shared_data.MAX_RECORD_BYTES", 40):
                shared.prepare_commit("refs/heads/candidate", on_rejected_repository=rejected)
            self.assertEqual(git_at(checkout, "show", f"candidate:{repo}/coverage.json"),
                             git_at(checkout, "show", f"{old}:{repo}/coverage.json"))
            self.assertIn("2026-10-05", git_at(checkout, "show", f"candidate:{other}/coverage.json"))
            rejected.assert_called_once()
            self.assertEqual(rejected.call_args.args[0], repo)

    def test_capture_and_author_errors_do_not_skip_other_repositories(self):
        day = date(2026, 10, 4)
        repos = REPOS[:3]
        archive = Mock()
        archive.capture.side_effect = [1, DataError("repository unavailable"), 2]
        archive.capture_authors.side_effect = [DataError("authors unavailable"), 0, 2]
        args = SimpleNamespace(max_requests=10, max_backfill_days=0, max_assessments=5)
        with (patch("collect_hosted.REPOS", repos),
              patch("collect_hosted.capture_days", return_value=[(repo, day, day) for repo in repos]),
              patch("collect_hosted.eligible_pulls", return_value=[]),
              patch("collect_hosted.Copilot")):
            result = collect_batch(archive, args, day, day, Path("git"), monotonic() + 10)
        self.assertEqual(result["failures"], 2)
        self.assertEqual([call.args[1] for call in archive.capture.call_args_list], list(repos))
        self.assertEqual([call.args[1] for call in archive.capture_authors.call_args_list], list(repos))

    def test_capture_budget_exhaustion_still_pauses_capture_and_author_lookups(self):
        day = date(2026, 10, 4)
        archive = Mock()
        archive.capture.side_effect = BudgetExhausted("request budget reached")
        args = SimpleNamespace(max_requests=10, max_backfill_days=0, max_assessments=5)
        with (patch("collect_hosted.capture_days", return_value=[(repo, day, day) for repo in REPOS]),
              patch("collect_hosted.eligible_pulls", return_value=[]),
              patch("collect_hosted.Copilot")):
            result = collect_batch(archive, args, day, day, Path("git"), monotonic() + 10)
        self.assertEqual(result["failures"], 0)
        archive.capture.assert_called_once()
        archive.capture_authors.assert_not_called()

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
        archive.defer_retry.assert_called_once_with(REPOS[0], "assessments", 2)
        model.return_value.close.assert_called_once()

    def test_timed_out_pull_moves_behind_other_work_after_snapshot_reload(self):
        repo = REPOS[0]
        day = date(2026, 10, 4)
        stamp = f"{day}T08:00:00Z"
        args = SimpleNamespace(max_requests=10, max_backfill_days=0, max_assessments=5,
                               work=Path("work"))
        with TemporaryDirectory() as work:
            root = Path(work)
            checkout, remote = shared_remote(root)
            with closing(Archive(root / "first.sqlite3")) as archive:
                shared = SharedData(archive, [repo], remote=str(remote), cwd=checkout, snapshot=True)
                shared.load()
                with archive.db:
                    for number in (1, 2):
                        pull = {"repo": repo, "number": number, "merged": stamp, "threads": ["review"]}
                        archive.db.execute("INSERT INTO pulls VALUES (?, ?, ?, ?, ?)",
                                           (repo, number, str(day), stamp, encode(pull)))
                        archive.db.execute("INSERT INTO pull_authors VALUES (?, ?, 'person', 'User')",
                                           (repo, number))
                archive.defer_retry(repo, "assessments", 1)
                first = shared.publish()
            with closing(Archive(root / "next.sqlite3")) as archive:
                shared = SharedData(archive, [repo], remote=str(remote), cwd=checkout, snapshot=True)
                shared.load(first)
                self.assertEqual([pull["number"] for pull in eligible_pulls(archive, day, day)], [2, 1])
                with (patch("collect_hosted.capture_days", return_value=[]),
                      patch.object(archive, "capture_authors"),
                      patch.object(archive, "get_prepared", return_value={}),
                      patch("collect_hosted.Copilot"),
                      patch("collect_hosted.assess_cached", side_effect=[(None, False), ([], True)] * 2)):
                    collect_batch(archive, args, day, day, args.work / "git", monotonic() + 10)
                second = shared.publish()
                self.assertEqual(json.loads(git_at(checkout, "show", f"{second}:{repo}/{COLLECTION_RETRIES}")), {})

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
    def test_draft_rejects_renames_from_outside_instruction_paths(self):
        repo = REPOS[0]
        url = f"https://github.com/{repo}.git"
        with TemporaryDirectory() as work:
            root = Path(work)
            checkout, remote = shared_remote(root)
            revision = git_at(checkout, "rev-parse", "HEAD")
            instruction = ".github/copilot-instructions.md"
            (checkout / instruction).parent.mkdir()
            (checkout / instruction).write_text("Review rules\n", encoding="utf-8")
            git_at(checkout, "add", "--", instruction)

            def local_git(*args, **kwargs):
                return git(*(remote.as_uri() if arg == url else arg for arg in args), **kwargs)

            with patch("publish_instructions.git", side_effect=local_git):
                artifact = git("diff", "--cached", "--binary", git_dir=checkout / ".git", binary=True)
                git_dir, commit = apply_draft(repo, "data", artifact, revision, None, None, root)
                self.assertEqual(git("show", f"{commit}:{instruction}", git_dir=git_dir), "Review rules\n")

                scoped = ".github/instructions/schema.instructions.md"
                (checkout / scoped).parent.mkdir()
                git_at(checkout, "mv", "--", "schema.json", scoped)
                artifact = git("diff", "--cached", "--binary", "--find-renames",
                               git_dir=checkout / ".git", binary=True)
                with self.assertRaisesRegex(DataError, r"non-instruction path: 'schema\.json'"):
                    apply_draft(repo, "data", artifact, revision, None, None, root)

    def test_replacement_draft_uses_current_base_but_keeps_existing_fork_lease(self):
        repo = REPOS[0]
        base, previous, commit = "a" * 40, "b" * 40, "c" * 40
        fork = f"https://github.com/{BOT}/{repo.split('/')[1]}.git"
        with (TemporaryDirectory() as work,
              patch("publish_instructions.target_repository", return_value=(repo, True)),
              patch("publish_instructions.GitHub") as github_type,
              patch("publish_instructions.pending_publication", return_value=None),
              patch("publish_instructions.publishing_plan", return_value=(None, previous)),
              patch("publish_instructions.push_fork", return_value=fork),
              patch("publish_instructions.fork_head", side_effect=[previous, commit, commit]),
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
        self.assertIn(f"--force-with-lease=refs/heads/{BRANCH}:{previous}", git.call_args_list[0].args)
        self.assertEqual(github.write.call_args.args[:2], ("POST", f"repos/{repo}/pulls"))
        self.assertTrue(github.write.call_args.args[2]["draft"])

    def test_retry_finishes_interrupted_pr_writes_without_pushing_another_draft(self):
        repo = REPOS[0]
        name = repo.split("/")[1]
        for existing in (False, True):
            with (self.subTest(existing=existing), TemporaryDirectory() as work,
                  patch("publish_instructions.target_repository", return_value=(repo, True)),
                  patch("publish_instructions.GitHub") as github_type,
                  patch("publish_instructions.request_review")):
                root = Path(work)
                checkout, remote = shared_remote(root)
                base = git_at(checkout, "rev-parse", "HEAD")
                if existing:
                    git_at(checkout, "push", str(remote), f"HEAD:refs/heads/{BRANCH}")
                git_at(checkout, "commit", "--quiet", "--allow-empty", "-m", "Bot draft")
                commit = git_at(checkout, "rev-parse", "HEAD")
                (root / "copilot-instructions.patch").write_bytes(b"new instructions")
                (root / "copilot-instructions.evidence.json").write_text(
                    json.dumps({"repo": repo, "revision": base}), encoding="utf-8")
                github = github_type.return_value
                github.public.return_value = {"archived": False, "disabled": False, "default_branch": "main"}
                pull_path = f"repos/{repo}/pulls"
                pull = {"number": 1, "html_url": f"https://github.com/{repo}/pull/1",
                        "body": "Manual notes\n\n" + pr_body(base), "user": {"login": BOT},
                        "state": "open", "base": {"ref": "main"}}

                def get(path):
                    if "/matching-refs/" in path:
                        refs = git_at(checkout, "ls-remote", "--refs", str(remote), f"{PENDING}/*")
                        return [{"ref": ref, "object": {"sha": sha}}
                                for line in refs.splitlines() for sha, ref in [line.split("\t")]]
                    if path == f"{pull_path}/1":
                        return pull
                    self.assertEqual(path, f"repos/{repo}/git/ref/heads/main")
                    return {"object": {"sha": base}}

                def pages(path, *, state, **_):
                    self.assertEqual(path, pull_path)
                    if existing and state == "open":
                        sha = git_at(checkout, "ls-remote", "--refs", str(remote), f"refs/heads/{BRANCH}")
                        return [dict(pull, head={"ref": BRANCH, "sha": sha.split("\t")[0],
                                                 "repo": {"full_name": f"{BOT}/{name}"}})]
                    return []

                github.get.side_effect = get
                github.rest_pages.side_effect = pages
                github.write.side_effect = [GitHubError("transient error"), pull]
                with (patch("publish_instructions.push_fork", return_value=str(remote)),
                      patch("publish_instructions.apply_draft",
                            return_value=(checkout / ".git", commit)) as apply):
                    with self.assertRaisesRegex(GitHubError, "transient"):
                        publish(name, root, dry_run=False)
                    self.assertTrue(get(f"repos/{BOT}/{name}/git/matching-refs/"))
                    if existing:
                        old_body = pull["body"]
                        pull["body"] = pr_body("f" * 40)
                        with self.assertRaisesRegex(DataError, "manually changed"):
                            publish(name, root, dry_run=False)
                        pull["body"] = old_body
                    publish(name, root, dry_run=False)
                    apply.assert_called_once()
                method, _, data = github.write.call_args.args
                self.assertEqual(method, "PATCH" if existing else "POST")
                self.assertEqual(data["body"], ("Manual notes\n\n" if existing else "") + pr_body(commit))
                self.assertFalse(get(f"repos/{BOT}/{name}/git/matching-refs/"))

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
