import unittest
from contextlib import closing
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

from archive import Archive, encode
from collect_hosted import aggregate_report, capture_days
from config import target_repository
from github_data import DataError
from publish_instructions import BOT, BRANCH, pr_body, publishing_plan
from scorecard import REPOS
from shared_data import SharedData
from test_scorecard import git_at, shared_remote


class HostedCollectionTests(unittest.TestCase):
    def test_pages_report_shows_confirmed_capture_without_contacting_the_model(self):
        start, today = date(2026, 10, 1), date(2026, 10, 3)
        with TemporaryDirectory() as work, closing(Archive(Path(work) / "data.sqlite3")) as archive:
            with archive.db:
                archive.db.execute("INSERT INTO complete_days VALUES (?, ?)", (REPOS[0], str(start)))
                pull = {"repo": REPOS[0], "number": 1, "missing_roots": 0,
                        "threads": [{"id": "T1", "author": {"login": "person", "__typename": "User"}}]}
                archive.db.execute("INSERT INTO pulls VALUES (?, 1, ?, '2026-10-01T12:00:00Z', ?)",
                                   (REPOS[0], str(start), encode(pull)))
                archive.db.execute("INSERT INTO pull_authors VALUES (?, 1, 'person', 'User')", (REPOS[0],))
            report = aggregate_report(archive, start, today)
        self.assertIn("Collection progress", report)
        self.assertIn(f"<td>{REPOS[0]}</td><td>1</td><td>2026-10-01</td><td>0 of 1</td>", report)
        self.assertIn(f"<td>{REPOS[1]}</td><td>0</td><td>None</td>", report)

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
                current.prepare_commit("refs/heads/candidate", include_assessments=True, report="<main>Stats</main>")
                tree = git_at(checkout, "ls-tree", "-r", "--name-only", "candidate")
                self.assertIn(f"{inactive}/2026-10/1.json", tree)
                self.assertIn(f"{active}/pr-authors.json", tree)
                self.assertEqual(git_at(checkout, "show", "candidate:report.html"), "<main>Stats</main>")
            git_at(checkout, "push", str(remote), "refs/heads/candidate:refs/heads/data")
            with closing(Archive(root / "next.sqlite3")) as archive:
                SharedData(archive, [active], remote=str(remote), cwd=checkout, snapshot=True).load()
                self.assertEqual(archive.pull_authors(active, date(2026, 10, 4), date(2026, 10, 4)),
                                 {1: ("opentelemetrybot", "User")})


class InstructionPublishingTests(unittest.TestCase):
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
