import os
import re
import sys
import unittest
from collections import Counter
from contextlib import closing
from datetime import date
from pathlib import Path
from subprocess import TimeoutExpired
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from archive import Archive, encode
from assessment import (ASSESSMENT_VERSION, CLASSIFY, CLASSIFY_BATCH, DEDUPLICATE, JUDGE, LOOKUP_JUDGE,
                        LOCAL_ASSESSMENT_VERSIONS, RETRIEVAL_VERSION, assess, classify, judge)
from copilot_model import AssessmentError, Copilot, input_size
from github_data import BudgetExhausted, DataError, GitHub, RateLimitError, run_command
from git_evidence import GitEvidence
from outcome_evidence import OutcomeEvidence
from prepared import PREPARED_VERSION, Evidence, PreparedEvidence, digest, pack_changes, prepare
from public_data import redact_code_urls
from review_instructions import (AUTHORING_INSTRUCTIONS, DISCOVER, EDIT_DRAFT, FINALIZE, JUDGE_EDITS, NAME_FILES,
                                 Snapshot, archived_examples, ask_rules, consolidation_input, draft_rules, name_files,
                                 refine_rules, render_rules, validate_rules)
from scorecard import AUTOMATED, BASELINE, REPOS, Cohort, Stats, assess_cached, assessment_order, identity, main, render
from shared_data import RecordRejected, SharedData, check_record, judgment_key, public_assessment


def git_at(path, *args, **kwargs):
    result = run_command(["git", "--no-pager", *args], cwd=path, **kwargs)
    if result.returncode:
        raise RuntimeError(result.stderr)
    return result.stdout.strip()


def shared_remote(root):
    repo_path, remote = root / "work", root / "remote.git"
    repo_path.mkdir()
    git_at(repo_path, "init", "--quiet")
    git_at(repo_path, "config", "user.name", "Scorecard fixture")
    git_at(repo_path, "config", "user.email", "fixture@example.test")
    git_at(repo_path, "init", "--quiet", "--bare", str(remote))
    (repo_path / "schema.json").write_text('{"format": 1}\n', encoding="utf-8")
    git_at(repo_path, "add", "schema.json")
    git_at(repo_path, "commit", "--quiet", "-m", "Initialize shared data")
    git_at(repo_path, "push", "--quiet", str(remote), "HEAD:refs/heads/data")
    return repo_path, remote


def pilot_cases(root):
    git_at(root, "init", "--quiet")
    git_at(root, "config", "user.name", "Scorecard fixture")
    git_at(root, "config", "user.email", "fixture@example.test")

    def commit(minute, text):
        (root / "adult.py").write_text(f"def is_adult(age):\n    return age {text} 18\n", encoding="utf-8")
        git_at(root, "add", "--", "adult.py")
        time = f"2026-10-01T13:{minute:02}:00Z"
        git_at(root, "commit", "--quiet", "-m", "Update boundary check",
               env=dict(os.environ, GIT_AUTHOR_DATE=time, GIT_COMMITTER_DATE=time))
        return git_at(root, "rev-parse", "HEAD")

    reviewed, fixed, reverted = commit(0, ">"), commit(20, ">="), commit(40, ">")
    for label, opened, head, expected in (
        ("delayed draft", "2026-10-01T13:30:00Z", fixed, "unclear"),
        ("reverted fix", "2026-10-01T13:10:00Z", reverted, "unaddressed"),
    ):
        thread = {"id": "thread", "url": "https://example.test/thread", "path": "adult.py",
                  "created": "2026-10-01T13:01:00Z", "opened": opened,
                  "reviewed": reviewed, "replies": [],
                  "author": {"login": "person", "__typename": "User"},
                  "body": "This rejects 18-year-olds. The minimum age is inclusive.",
                  "hunk": "+    return age > 18"}
        merge = git_at(root, "commit-tree", head + "^{tree}", "-p", reviewed, "-m", "Squash PR")
        pull = {"repo": REPOS[0], "number": 1, "head": head, "merge": merge,
                "merged": "2026-10-01T13:50:00Z", "threads": [thread], "missing_roots": 0}
        evidence = Evidence(root, pull)
        evidence.git.path = root / ".git"
        evidence.git.ready = True
        yield label, pull, thread, evidence, expected


class FakeModel:
    def __init__(self, result):
        self.result = result

    def ask(self, prompt, evidence):
        return self.result


def connection(nodes, next_cursor=None):
    return {"nodes": nodes, "pageInfo": {
        "hasNextPage": next_cursor is not None, "endCursor": next_cursor,
    }}


def timeline(nodes, count=None, next_cursor=None):
    return dict(connection(nodes, next_cursor), filteredCount=len(nodes) if count is None else count)


class CollectionTests(unittest.TestCase):
    def test_shared_data_round_trip_uses_only_public_tables(self):
        with TemporaryDirectory() as work:
            root = Path(work)
            repo_path, remote = shared_remote(root)
            with closing(Archive(root / "writer.sqlite3")) as writer:
                shared = SharedData(writer, REPOS, remote=str(remote), cwd=repo_path)
                shared.load()
                self.assertIsNotNone(shared.publish())
                shared.load()
                self.assertIsNone(shared.publish())
                pull = {"repo": REPOS[0], "number": 1, "merged": "2026-10-01T10:00:00Z",
                        "threads": [], "missing_roots": 0}
                bundle = {"version": PREPARED_VERSION, "input": digest(pull), "threads": {}, "records": []}
                legacy = dict(bundle, version=1)
                with writer.db:
                    writer.db.execute("INSERT INTO complete_days VALUES (?, ?)", (REPOS[0], "2026-10-01"))
                    writer.db.execute("INSERT INTO pulls VALUES (?, ?, ?, ?, ?)",
                                      (REPOS[0], 1, "2026-10-01", pull["merged"], encode(pull)))
                    writer.db.execute("INSERT INTO pull_line_counts VALUES (?, ?, ?, ?)", (REPOS[0], 1, 0, 12))
                writer.put_prepared(REPOS[0], 1, legacy)
                writer.put_prepared(REPOS[0], 1, bundle)
                writer.put_judgment(REPOS[0], 1, "judge", "test", {"status": "addressed"})
                updated = shared.publish()
                files = git_at(repo_path, "ls-tree", "-r", "--name-only", updated).splitlines()
                self.assertIn(f"{REPOS[0]}/coverage.json", files)
                self.assertIn(f"{REPOS[0]}/2026-10/1.json", files)
                self.assertIn(f"{REPOS[0]}/2026-10/1.evidence.json.gz", files)
                self.assertIn(f"{REPOS[0]}/2026-10/1.evidence.v{PREPARED_VERSION}.json.gz", files)
                self.assertIn(f"{REPOS[0]}/line-changes.json", files)
                self.assertNotIn("archive.sqlite3", files)
            with closing(Archive(root / "reader.sqlite3")) as reader:
                SharedData(reader, REPOS, remote=str(remote), cwd=repo_path).load()
                self.assertEqual(reader.pending_days(REPOS[0], BASELINE[0], BASELINE[0]), [])
                self.assertEqual(reader.get_prepared(pull), bundle)
                self.assertEqual(reader.get_prepared(pull, 1), legacy)
                self.assertEqual(list(reader.db.execute("SELECT * FROM pull_line_counts")), [(REPOS[0], 1, 0, 12)])
                self.assertIsNone(reader.get_judgment(REPOS[0], 1, "judge", "test"))

    def test_published_results_are_versioned_and_reusable_without_a_model(self):
        repo = REPOS[0]
        day = "2026-10-01"
        link = f"https://github.com/{repo}/commit/{'c' * 40}"
        pull = {"repo": repo, "number": 1, "merged": f"{day}T12:00:00Z", "missing_roots": 0,
                "threads": [
                    {"id": "first", "opened": f"{day}T09:00:00Z",
                     "author": {"login": "copilot-pull-request-reviewer[bot]", "__typename": "Bot"}},
                    {"id": "duplicate", "opened": f"{day}T09:30:00Z",
                     "author": {"login": "person", "__typename": "User"}},
                ]}
        bundle = {"version": PREPARED_VERSION, "input": digest(pull), "records": [{"url": link}],
                  "threads": {"first": {"records": {"merge-commit": 0}}, "duplicate": {"records": {}}}}
        published_version = ASSESSMENT_VERSION
        model = "gpt-6.2-sol"
        with TemporaryDirectory() as work:
            root = Path(work)
            repo_path, remote = shared_remote(root)

            def shared(archive, repos, **options):
                options.pop("ref", None)
                options.pop("snapshot", None)
                return SharedData(archive, repos, remote=str(remote), cwd=repo_path, **options)

            local, fresh, html = (root / name for name in ("local.sqlite3", "fresh.sqlite3", "scorecard.html"))
            with closing(Archive(local)) as writer:
                publisher = shared(writer, REPOS)
                publisher.load()
                with writer.db:
                    writer.db.executemany("INSERT INTO complete_days VALUES (?, ?)", ((r, day) for r in REPOS))
                    writer.db.execute("INSERT INTO pulls VALUES (?, ?, ?, ?, ?)",
                                      (repo, 1, day, pull["merged"], encode(pull)))
                    writer.db.execute("INSERT INTO pull_authors VALUES (?, ?, ?, ?)", (repo, 1, "person", "User"))
                writer.put_prepared(repo, 1, bundle)
                for effort, status in (("medium", "unclear"), ("high", "addressed")):
                    key = judgment_key(model, [published_version, pull, digest(bundle)], effort)
                    result = {"assessment_version": published_version, "model": model,
                              "reasoning_effort": effort, "evidence_digest": digest(bundle),
                              "threads": [
                                  {"id": "first", "kind": "bug", "status": status, "evidence": [link],
                                   "reason": "Do not publish this model explanation"},
                                  {"id": "duplicate", "kind": None, "status": "duplicate", "original": "first"},
                              ]}
                    writer.put_judgment(repo, 1, "complete", key, result)
                writer.put_judgment(repo, 1, "judge", "partial", {"outcome_reason": "Private intermediate output"})
                before = publisher.publish()
                self.assertNotIn(".assessment.", git_at(repo_path, "ls-tree", "-r", "--name-only", before))
                with writer.db:
                    writer.db.execute("INSERT INTO pull_merge_timings VALUES (?, ?, ?, ?)",
                                      (repo, 1, f"{day}T08:00:00Z", f"{day}T08:30:00Z"))

            options = ["--window", day, day, "--model", model, "--output", str(html)]
            with (patch("scorecard.SharedData", side_effect=shared),
                  patch.object(Copilot, "ask", side_effect=AssertionError("no model calls"))):
                self.assertEqual(main(["assess", "--archive", str(local), "--publish-data", "--max-prs", "0",
                                       *options]), 0)
                self.assertEqual(main(["load-data", "--archive", str(fresh)]), 0)
                with patch("scorecard.GitHub") as github:
                    github.return_value.pull_authors.side_effect = lambda repo, pending: [
                        (n, {"login": "person", "__typename": "User"}) for n in pending
                    ]
                    self.assertEqual(main(["capture-authors", "--archive", str(fresh), "--through", day]), 0)
                for effort, unclear in (("high", "0.0%"), ("medium", "100.0%")):
                    self.assertEqual(main(["report", "--archive", str(fresh), "--reasoning-effort", effort,
                                           "--assessment-version", published_version, *options]), 0)
                    text = html.read_text(encoding="utf-8")
                    row = re.search(r'<th scope="row">semantic-conventions-genai</th><td>1</td>(.*?)</tr>', text)
                    self.assertEqual(re.findall(r'class="rate-value">([^<]+)', row.group(1)),
                                     ["1.00", "0.0%", unclear, "0.00"])
                    self.assertIn(f"{model} ({effort} reasoning effort)", text)

            with closing(Archive(fresh)) as reader:
                publisher = shared(reader, REPOS)
                publisher.load()
                self.assertIsNone(publisher.publish())
                self.assertEqual(list(reader.db.execute("SELECT stage, count(*) FROM judgments GROUP BY stage")),
                                 [("published", 2)])
                self.assertEqual(list(reader.db.execute("SELECT * FROM pull_merge_timings")), [
                    (repo, 1, f"{day}T08:00:00Z", f"{day}T08:30:00Z"),
                ])
                self.assertEqual(reader.get_judgment(repo, 1, "published", key)["reasoning_effort"], "high")
                self.assertEqual(reader.get_judgment(repo, 1, "published", key)["threads"], [
                    {"id": "first", "kind": "bug", "status": "addressed", "evidence": [link]},
                    {"id": "duplicate", "kind": None, "status": "duplicate", "original": "first"},
                ])
                retrieval_key = judgment_key(model, [RETRIEVAL_VERSION, pull, digest(bundle)], "high")
                retrieval = dict(result, assessment_version=RETRIEVAL_VERSION, input_digest=retrieval_key,
                                 threads=[dict(result["threads"][0], status="unaddressed"), result["threads"][1]])

            with closing(Archive(local)) as writer:
                writer.put_judgment(repo, 1, "complete", retrieval_key, retrieval)
            with (patch("scorecard.SharedData", side_effect=shared),
                  patch.object(Copilot, "ask", side_effect=AssertionError("no model calls"))):
                self.assertEqual(main(["assess", "--archive", str(local), "--retrieve-evidence",
                                       "--publish-data", "--max-prs", "0", *options]), 0)
                self.assertEqual(main(["load-data", "--archive", str(fresh)]), 0)
                self.assertEqual(main(["report", "--archive", str(fresh), "--output", str(html)]), 0)
            text = html.read_text(encoding="utf-8")
            row = re.search(r'<th scope="row">semantic-conventions-genai.*?</tr>', text)[0]
            self.assertEqual(re.findall(r'class="rate-value">([^<]+)', row), ["1.00", "100.0%", "0.0%", "0.00"])
            self.assertNotIn("Assessment version", text)
            result["threads"][0]["evidence"] = [f"https://github.com/{repo}/commit/{'d' * 40}"]
            with self.assertRaises(DataError):
                public_assessment(pull, result, key, bundle=bundle)

    def test_capture_resumes_after_limit_and_reads_reports_from_disk(self):
        repo = REPOS[0]
        first, empty, recent = date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3)
        merged = "2026-10-01T12:00:00Z"
        created = "2026-10-01T01:00:00Z"
        pull = {"repo": repo, "number": 1, "merged": merged, "missing_roots": 0, "threads": []}
        github = Mock()
        github.merged.return_value = {1: merged, 2: merged, 3: merged}

        def interrupted(repo, pending):
            yield pull, (4, 0), (created, created)
            raise RateLimitError("Try later")

        github.pulls.side_effect = interrupted
        with TemporaryDirectory() as work:
            path = Path(work) / "archive.sqlite3"
            with closing(Archive(path)) as archive:
                with self.assertRaises(RateLimitError):
                    archive.capture(github, repo, first, empty, recent)
                self.assertEqual(archive.pending_days(repo, first, empty), [first, empty])
            github.pulls.side_effect = None
            github.pulls.return_value = iter([
                (dict(pull, number=2), (0, 9), (created, None)),
                (dict(pull, number=3), (1, 1), None),
            ])
            with closing(Archive(path)) as archive:
                self.assertEqual(archive.capture(github, repo, first, empty, recent), 2)
                github.pulls.assert_called_with(repo, {2: merged, 3: merged})
                github.merged.return_value = {}
                github.pulls.return_value = iter([])
                archive.capture(github, repo, first, empty, date(2026, 10, 4))
                github.merged.assert_called_with(repo, empty, empty)
                github.reset_mock()
                archive.capture(github, repo, first, empty, date(2026, 10, 4))
                github.merged.assert_not_called()
                github.pull_authors.return_value = [(1, None), (2, None), (3, None)]
                archive.capture_authors(github, repo, first, empty)
            with closing(Archive(path, readonly=True)) as archive:
                self.assertEqual(list(archive.pulls(repo, first, empty)), [dict(pull, number=n) for n in (1, 2, 3)])
                self.assertEqual(list(archive.db.execute(
                    "SELECT additions, deletions FROM pull_line_counts ORDER BY number"
                )), [(4, 0), (0, 9), (1, 1)])
                self.assertEqual(list(archive.db.execute(
                    "SELECT created_at, ready_at FROM pull_merge_timings ORDER BY number"
                )), [(created, created), (created, None)])
                self.assertEqual(archive.pending_days(repo, first, empty), [])
            output = Path(work) / "scorecard.html"
            with patch("scorecard.GitHub", side_effect=AssertionError("report must not contact GitHub")):
                self.assertEqual(main(["report", "--archive", str(path), "--output", str(output),
                                       "--window", first.isoformat(), empty.isoformat()]), 0)
            self.assertIn('<th scope="row">semantic-conventions-genai</th><td>3</td>',
                          output.read_text(encoding="utf-8"))

    def test_author_capture_resumes_after_missing_pr_but_keeps_successful_null_author(self):
        repo, day = REPOS[0], BASELINE[0]
        merged = "2026-10-01T12:00:00Z"
        github = GitHub()
        with TemporaryDirectory() as work, closing(Archive(Path(work) / "archive.sqlite3")) as archive:
            with archive.db:
                archive.db.executemany("INSERT INTO pulls VALUES (?, ?, ?, ?, ?)",
                                       ((repo, n, day.isoformat(), merged, encode({})) for n in (1, 2)))
            with patch.object(github, "query", return_value={"repository": {
                "visibility": "PUBLIC", "p1": {"number": 1, "mergedAt": merged, "author": None},
                "p2": None,
            }}):
                with self.assertRaisesRegex(DataError, "Merge timestamp mismatch"):
                    archive.capture_authors(github, repo, day, day)
            with self.assertRaisesRegex(DataError, "have not been checked for 1 PRs"):
                archive.pull_authors(repo, day, day)
            with patch.object(github, "query", return_value={"repository": {
                "visibility": "PUBLIC", "p2": {"number": 2, "mergedAt": merged,
                                              "author": {"login": "dependabot", "__typename": "Bot"}},
            }}) as query:
                self.assertEqual(archive.capture_authors(github, repo, day, day), 1)
                self.assertNotIn("p1:pullRequest", query.call_args.args[0])
                self.assertNotIn("reviewThreads", query.call_args.args[0])
            self.assertEqual(archive.pull_authors(repo, day, day), {1: (None, None), 2: ("dependabot", "Bot")})

    def test_rate_limit_with_retry_after_stops_when_wait_is_too_long(self):
        result = SimpleNamespace(returncode=1, stderr="gh: secondary rate limit (HTTP 403)",
                                 stdout="HTTP/2 403 Forbidden\nRetry-After: 180\n\n{}")
        with patch("github_data.run_command", return_value=result), patch("github_data.sleep") as sleep:
            with self.assertRaisesRegex(RateLimitError, "180s"):
                GitHub(max_rate_wait=60).get("search/issues")
            sleep.assert_not_called()

    def test_search_splits_and_collects_every_page_by_merge_date(self):
        def results(path, q, page, **kwargs):
            def item(n, day):
                return {"number": n, "created_at": "2026-09-01T00:00:00Z",
                        "pull_request": {"merged_at": f"2026-10-{day:02}T00:00:00Z"}}

            if "2026-10-01..2026-10-02" in q:
                return {"total_count": 1001, "incomplete_results": False, "items": []}
            if "2026-10-01..2026-10-01" in q:
                return {
                    "total_count": 101, "incomplete_results": False,
                    "items": [item(n, 1) for n in (range(1, 101) if page == 1 else [101])],
                }
            return {"total_count": 1, "incomplete_results": False, "items": [item(102, 2)]}

        github = GitHub()
        with patch.object(github, "get", side_effect=results):
            cohort = github._merged(REPOS[0], date(2026, 10, 1), date(2026, 10, 2))
        self.assertEqual(list(cohort), list(range(1, 103)))
        self.assertEqual(cohort[102], "2026-10-02T00:00:00Z")

    def test_collects_more_thread_and_reply_pages_without_counting_replies(self):
        def comment(id_, published, reply=False):
            return {
                "id": id_, "url": f"https://example.test/{id_}", "createdAt": "2026-09-01T00:00:00Z",
                "publishedAt": published, "replyTo": {"id": "root"} if reply else None,
                "author": {"login": "person", "__typename": "User"}, "body": "", "path": "app.py",
                "diffHunk": "", "originalCommit": {"oid": "reviewed"}, "pullRequestReview": None,
            }

        before, merged, after = ("2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z", "2026-10-03T00:00:00Z")
        pr = {
            "number": 1, "mergedAt": merged, "headRefOid": "head", "mergeCommit": {"oid": "merge"},
            "additions": 23, "deletions": 11,
            "createdAt": before, "isDraft": False,
            "timelineItems": timeline([{"__typename": "MergedEvent", "id": "m", "createdAt": merged}]),
            "reviewThreads": connection([{
                "id": "t1", "comments": connection([comment("root", before)], "replies"),
            }], "threads"),
        }
        github = GitHub()
        with patch.object(github, "query", side_effect=[
            {"repository": {"visibility": "PUBLIC", "p1": pr}},
            {"node": {"comments": connection([comment("reply", before, reply=True)])}},
            {"repository": {"visibility": "PUBLIC", "pullRequest": {"reviewThreads": connection([
                {"id": "t2", "comments": connection([comment("at-merge", merged)])},
                {"id": "t3", "comments": connection([comment("post-merge", after)])},
                {"id": "t4", "comments": connection([comment("pending", None)])},
            ])}}},
        ]) as query:
            (pull, counts, timing), = github.pulls(REPOS[0], {1: merged})
            self.assertIn("additions deletions", query.call_args_list[0].args[0])
        self.assertEqual(counts, (23, 11))
        self.assertEqual(timing, (before, before))
        self.assertNotIn("additions", pull)
        self.assertEqual([thread["id"] for thread in pull["threads"]], ["t1"])
        self.assertEqual(pull["threads"][0]["replies"][0]["url"], "https://example.test/reply")
        self.assertEqual(pull["threads"][0]["reviewed"], "reviewed")

    def test_capture_redacts_fixture_credentials_in_a_review_hunk(self):
        merged = "2026-10-02T00:00:00Z"
        opened = "2026-10-01T00:00:00Z"
        hunk = '+    var url = "http://fixture:made-up@" + server.getHost();'
        comment = {
            "id": "root", "url": "https://example.test/review", "createdAt": opened,
            "publishedAt": opened, "replyTo": None, "author": None, "body": "Check the expected URL.",
            "path": "ExampleTest.java", "diffHunk": hunk, "originalCommit": None, "pullRequestReview": None,
        }
        node = {
            "number": 1, "mergedAt": merged, "headRefOid": "head", "mergeCommit": None,
            "reviewThreads": connection([{"id": "t1", "comments": connection([comment])}]),
        }
        repo = REPOS[0]
        pull = GitHub()._pull(repo, *repo.split("/"), node)
        self.assertEqual(pull["threads"][0]["hunk"], '+    var url = "http://omitted:password@" + server.getHost();')
        check_record("pull.json", SharedData.text_record(pull), pull)

    def test_merge_timing_uses_final_ready_event_before_merge_across_pages(self):
        created, earlier, merged, after = (f"2026-10-01T{hour:02}:00:00Z" for hour in (1, 2, 3, 4))

        def event(kind, id_, at):
            return {"__typename": kind, "id": id_, "createdAt": at}

        pr = {"number": 1, "createdAt": created, "mergedAt": merged, "isDraft": False}
        first = dict(pr, timelineItems=timeline([
            event("ReadyForReviewEvent", "r1", earlier), event("ConvertToDraftEvent", "d1", earlier),
        ], count=6, next_cursor="continue"))
        last = dict(pr, timelineItems=timeline([
            event("ReadyForReviewEvent", "r2", merged), event("MergedEvent", "m", merged),
            event("ConvertToDraftEvent", "d2", after), event("ReadyForReviewEvent", "r3", after),
        ]))
        github = GitHub()
        with patch.object(github, "query", side_effect=[
            {"repository": {"visibility": "PUBLIC", "p1": first}},
            {"repository": {"visibility": "PUBLIC", "pullRequest": last}},
        ]) as query:
            self.assertEqual(list(github.pull_merge_timings(REPOS[0], {1: merged})), [(1, (created, merged))])
        self.assertEqual(query.call_args.kwargs["cursor"], "continue")
        self.assertNotIn("reviewThreads", query.call_args_list[0].args[0])

    def test_merge_timing_does_not_use_creation_when_final_readiness_is_unverified(self):
        created, merged = "2026-10-01T01:00:00Z", "2026-10-01T03:00:00Z"
        merge = {"__typename": "MergedEvent", "id": "m", "createdAt": merged}
        draft = {"__typename": "ConvertToDraftEvent", "id": "d", "createdAt": created}
        pr = {"number": 1, "createdAt": created, "mergedAt": merged, "isDraft": False}
        github = GitHub()
        for events in ([], [draft, merge]):
            with self.subTest(events=events):
                self.assertEqual(github._merge_timing(REPOS[0], dict(pr, timelineItems=timeline(events))),
                                 (created, None))
        with self.assertRaisesRegex(DataError, "Incomplete readiness timeline"):
            github._merge_timing(REPOS[0], dict(pr, timelineItems=timeline([merge], count=2)))
        with self.assertLogs("github_data", level="WARNING") as logs:
            self.assertIsNone(github._merge_timing(REPOS[0], dict(pr, createdAt="2026-10-01T04:00:00Z")))
        self.assertIn("GitHub creation time follows merge", logs.output[0])

    def test_merge_timing_uses_merge_timestamp_when_merge_event_is_delayed(self):
        created, ready, merged, recorded = (f"2026-10-01T01:00:0{s}Z" for s in range(4))
        merge = {"__typename": "MergedEvent", "id": "m", "createdAt": recorded}
        event = {"__typename": "ReadyForReviewEvent", "id": "r", "createdAt": ready}
        pr = {"number": 1, "createdAt": created, "mergedAt": merged, "isDraft": False}
        github = GitHub()
        for events, expected in (([merge], created), ([event, merge], ready),
                                 ([dict(event, createdAt=merged), merge], None)):
            with self.subTest(events=events):
                self.assertEqual(github._merge_timing(REPOS[0], dict(pr, timelineItems=timeline(events))),
                                 (created, expected))


class AssessmentTests(unittest.TestCase):
    def test_prepared_and_lookup_evidence_preserve_already_redacted_urls(self):
        path = "ExampleTest.java"
        raw = '    var url = "https://REDACTED:REDACTED@example.test/path";'
        normalized = '    var url = "https://redacted:password@example.test/path";'
        thread = {"id": "t1", "path": path, "body": "Check the expected URL.", "reviewed": None,
                  "opened": "2026-10-01T00:00:00Z"}
        pull = {"repo": REPOS[0], "number": 1, "head": None, "merge": "a" * 40, "threads": [thread]}
        retrieval = OutcomeEvidence(Path("."), pull)
        evidence = retrieval.source
        evidence.git = Mock()
        evidence.git.file.return_value = raw.encode()
        evidence.git.patch.return_value = "+" + raw
        evidence.git.commit.return_value = {
            "git_range": ("b" * 40, "a" * 40), "files": [{"path": path, "status": "modified"}],
        }
        bundle = prepare(pull, evidence)
        records = PreparedEvidence(bundle).for_thread(thread, [path])
        self.assertEqual(records["merged-0"]["text"], normalized)
        self.assertEqual(records["merge-commit"]["patches"][0]["patch"], "+" + normalized)
        check_record("evidence.json", SharedData.text_record(bundle), bundle)
        records, _ = retrieval.lookup(thread, {"action": "read", "path": path, "query": ""}, "0")
        self.assertEqual(records["merged-lookup-0"]["text"], normalized)

    def test_publication_check_still_rejects_unredacted_or_ambiguous_values(self):
        values = (
            "https://fixture:made-up@example.test",
            redact_code_urls("https://fixture:made-up@extra@example.test"),
            redact_code_urls("Authorization: Bearer " + "A" * 40),
        )
        for value in values:
            with self.assertRaises(RecordRejected):
                check_record("evidence.json", value.encode(), value)
        self.assertEqual(redact_code_urls("https://user:password@example.test"), "https://user:password@example.test")

    def test_evidence_lookup_stops_at_collection_deadline_instead_of_returning_missing_git(self):
        pull = {"repo": REPOS[0], "number": 1, "head": None, "merge": "a" * 40}
        thread = {"reviewed": None}
        with (TemporaryDirectory() as work,
              patch("github_data.monotonic", return_value=10),
              patch("github_data.subprocess.run", side_effect=TimeoutExpired("git", 2)) as command):
            retrieval = OutcomeEvidence(Path(work), pull, deadline=12)
            with self.assertRaisesRegex(BudgetExhausted, "Collection time budget exhausted"):
                retrieval.lookup(thread, {"action": "read", "path": "README.md", "query": ""}, "0")
            self.assertEqual(command.call_args.kwargs["timeout"], 2)

    def test_assess_runs_from_prepared_data_without_git_or_github(self):
        with TemporaryDirectory() as work:
            root = Path(work)
            git = root / "git"
            git.mkdir()
            _, pull, _, evidence, _ = next(pilot_cases(git))
            bundle = prepare(pull, evidence)
            db = root / "archive.sqlite3"
            with closing(Archive(db)) as archive:
                with archive.db:
                    archive.db.execute("INSERT INTO pulls VALUES (?, ?, ?, ?, ?)",
                                       (pull["repo"], 1, "2026-10-01", pull["merged"], encode(pull)))
                    archive.db.execute("INSERT INTO pull_authors VALUES (?, ?, ?, ?)",
                                       (pull["repo"], 1, "person", "User"))
                archive.put_prepared(pull["repo"], 1, bundle)
            model = Mock(model="test-model", reasoning_effort="high")
            model.ask.side_effect = [
                [{"id": "thread", "kind": "bug", "reason": "Rejects adults."}],
                {"status": "unclear", "evidence": ["comparison"], "reason": "Timing is unclear."},
            ]
            with patch("scorecard.GitHub", side_effect=AssertionError("assessment must not call REST")), \
                    patch("prepared.GitEvidence", side_effect=AssertionError("assessment must not fetch Git")), \
                    patch("scorecard.Copilot", return_value=model):
                self.assertEqual(main(["assess", "--archive", str(db),
                                       "--output", str(root / "scorecard.html")]), 0)
            self.assertEqual(model.ask.call_count, 2)
            audit, = (root / "audit").glob("**/*.json")
            self.assertIn('"status": "unclear"', audit.read_text(encoding="utf-8"))

    def test_git_evidence_separates_a_draft_cutoff_and_a_reverted_fix(self):
        with TemporaryDirectory() as work:
            for label, pull, thread, evidence, _ in pilot_cases(Path(work)):
                _, records = evidence.for_thread(thread, evidence.changed_paths())
                self.assertIn("return age > 18", records["reviewed-0"]["text"])
                if label == "delayed draft":
                    self.assertFalse(records["change-0"]["dated_after_publication"])
                    self.assertIn("return age >= 18", records["merged-0"]["text"])
                else:
                    self.assertTrue(records["change-0"]["dated_after_publication"])
                    self.assertTrue(records["change-1"]["dated_after_publication"])
                    self.assertIn("return age > 18", records["merged-0"]["text"])

    def test_ranks_a_related_pr_fix_before_later_upstream_commits(self):
        with TemporaryDirectory() as work:
            root = Path(work)
            git_at(root, "init", "--quiet")
            git_at(root, "config", "user.name", "Scorecard fixture")
            git_at(root, "config", "user.email", "fixture@example.test")

            def commit(minute, path, text):
                (root / path).write_text(text, encoding="utf-8")
                git_at(root, "add", "--", path)
                stamp = f"2026-10-01T13:{minute:02d}:00Z"
                git_at(root, "commit", "-qm", path,
                       env=dict(os.environ, GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp))
                return git_at(root, "rev-parse", "HEAD")

            base = commit(0, "caller.py", "limit=18\n")
            git_at(root, "branch", "upstream")
            reviewed = commit(1, "helper.py", "def allowsAge(age): return age > 18\n")
            fix = commit(3, "helper.py", "def allowsAge(age): return age >= 18\n")
            branch = git_at(root, "branch", "--show-current")
            git_at(root, "switch", "upstream")
            upstream = [commit(i, "helper.py", f"# upstream {i}\n") for i in range(4, 19)]
            # Keep the branches' files disjoint at the merge, while leaving upstream
            # history with changes to the same path.
            unmerged = upstream[-1]
            (root / "helper.py").unlink()
            git_at(root, "add", "-u")
            git_at(root, "commit", "-qm", "Drop upstream file")
            upstream_tip = git_at(root, "rev-parse", "HEAD")
            git_at(root, "switch", branch)
            head = git_at(root, "commit-tree", fix + "^{tree}", "-p", fix, "-p", upstream_tip,
                          "-m", "Merge upstream")
            merge = git_at(root, "commit-tree", head + "^{tree}", "-p", upstream_tip, "-m", "Squash")
            thread = {"id": "thread", "path": "caller.py", "body": "helper.py allowsAge must accept age 18.",
                      "opened": "2026-10-01T13:02:00Z", "reviewed": reviewed, "replies": []}
            pull = {"repo": REPOS[0], "number": 1, "head": head, "merge": merge,
                    "merged": "2026-10-01T13:50:00Z", "threads": [thread]}
            evidence = Evidence(root, pull)
            evidence.git.path = root / ".git"
            evidence.git.ready = True
            bundle = prepare(pull, evidence)
            prepared = PreparedEvidence(bundle)
            paths = prepared.paths()["thread"]
            self.assertIn("helper.py", paths)
            records = prepared.for_thread(thread, paths)
            links = [item["url"].rsplit("/", 1)[-1] for k, item in records.items() if k.startswith("change-")]
            self.assertEqual(links, [fix])
            self.assertNotIn(unmerged, links)

    def test_large_snapshot_shows_matching_code_after_the_prefix(self):
        evidence = Evidence(Path("."), {"repo": REPOS[0], "number": 1, "head": None, "merge": None})
        text = "# filler " + "x" * 100 + "\n"
        state = Evidence.file_state(
            (text * 180 + "if finish_reason is None:\n" + "\n" * 9 + '    return "stop"\n').encode()
        )
        with patch.object(evidence, "file", return_value=state):
            result = evidence.snapshot("revision", "code.py", {"body": "Do not emit `finish_reason` as null."})
        self.assertEqual(result["state"], "truncated")
        self.assertIn('return "stop"', result["text"])
        self.assertLessEqual(len(result["text"]), 14_000)

    def test_named_schema_can_be_outside_the_pr_diff(self):
        thread = {"id": "thread", "path": "code.py",
                  "body": "Check `model/message.json` and `model/missing.json`.",
                  "opened": "2026-10-01T13:02:00Z", "reviewed": "1" * 40}
        pull = {"repo": REPOS[0], "number": 1, "head": None, "merge": "2" * 40,
                "merged": "2026-10-01T13:50:00Z"}
        evidence = Evidence(Path("."), pull)
        with patch.object(evidence, "commit", return_value={"files": []}), \
                patch.object(evidence, "pr_history", return_value=None), \
                patch.object(evidence.git, "prefetch_patches"), \
                patch.object(evidence, "file", side_effect=lambda _, path:
                             {"state": "absent"} if path.endswith("/missing.json")
                             else {"state": "text", "text": path}):
            paths, records = evidence.for_thread(thread, {"code.py"})
        self.assertEqual(paths, ["code.py", "model/message.json"])
        self.assertEqual(records["reviewed-1"]["text"], "model/message.json")
        self.assertEqual(records["merged-1"]["text"], "model/message.json")

    def test_missing_commit_is_unavailable_but_a_failed_fetch_stops(self):
        with TemporaryDirectory() as work:
            root = Path(work)
            source = root / "source"
            source.mkdir()
            _, pull, *_ = next(pilot_cases(source))
            missing = "1" * 40
            git = GitEvidence(root / "cache", REPOS[0], 1)
            git.url = str(source)
            git.prepare([pull["head"], missing])
            self.assertEqual((git.checked, git.unavailable), ({pull["head"]}, {missing}))
            git = GitEvidence(root / "cache", REPOS[0], 1)
            git.url = str(root / "no-such-remote")
            with self.assertRaisesRegex(DataError, "Could not fetch evidence"):
                git.prepare([missing])

    def test_changing_outcome_version_keeps_classification_and_old_result(self):
        pull = {"repo": REPOS[0], "number": 1, "merge": "revision"}
        model = Copilot("model")
        classify_once, judge_v1, judge_v2 = Mock(return_value="bug"), Mock(return_value="unclear"), Mock(
            return_value="addressed"
        )

        def run(judge_version, evaluator):
            def fake_assess(github, model, pull, checkpoint):
                return [checkpoint("classify", ["stable-prompt"], classify_once),
                        checkpoint("judge", [judge_version], evaluator)]
            return fake_assess

        with TemporaryDirectory() as work:
            root = Path(work)
            with closing(Archive(root / "archive.sqlite3")) as archive:
                with patch("scorecard.assess", side_effect=run("old-prompt", judge_v1)):
                    self.assertEqual(assess_cached(archive, {}, model, pull, root, True, "old")[0],
                                     ["bug", "unclear"])
                with patch("scorecard.assess", side_effect=run("new-prompt", judge_v2)):
                    self.assertEqual(assess_cached(archive, {}, model, pull, root, True, "new")[0],
                                     ["bug", "addressed"])
                self.assertEqual(assess_cached(archive, {}, model, pull, None, False, "old"),
                                 (["bug", "unclear"], False))
                medium = Copilot("model", "medium")
                self.assertEqual(assess_cached(archive, {}, medium, pull, None, False, "new"), (None, False))
                with patch("scorecard.assess", side_effect=run("new-prompt", judge_v2)):
                    self.assertEqual(assess_cached(archive, {}, medium, pull, root, True, "new")[0],
                                     ["bug", "addressed"])
            self.assertEqual(classify_once.call_count, 2)
            judge_v1.assert_called_once()
            self.assertEqual(judge_v2.call_count, 2)

    def test_duplicate_only_links_earlier_original_and_is_not_a_finding(self):
        threads = [
            {"id": "a", "url": "original-url", "path": "test.py",
             "opened": "2026-10-01T00:00:00Z", "created": "2026-10-01T00:00:00Z",
             "body": "Add a test.", "hunk": "old hunk", "replies": ["Not sent"]},
            {"id": "b", "url": "duplicate-url", "path": "test.py",
             "opened": "2026-10-01T01:00:00Z", "created": "2026-10-01T01:00:00Z",
             "body": "This still needs a test."},
        ]
        answers = [
            {"id": "a", "kind": "test coverage gap", "paths": ["guessed.py"], "reason": "Missing test."},
            {"id": "b", "kind": "duplicate", "original": "a", "reason": "Same missing test."},
        ]
        model = Mock()
        model.ask.return_value = answers
        rows = classify(model, {"threads": threads})
        prompt, data = model.ask.call_args.args
        self.assertEqual(prompt, CLASSIFY)
        self.assertEqual(set(data["threads"][0]), {"id", "opened", "path", "body"})
        self.assertNotIn("paths", rows[0])
        self.assertEqual((rows[1]["kind"], rows[1]["status"], rows[1]["original_url"]),
                         (None, "duplicate", "original-url"))
        threads[1]["opened"] = threads[0]["opened"]
        with self.assertRaises(AssessmentError):
            classify(FakeModel(answers), {"threads": threads})

    def test_split_classification_resumes_batches_and_links_across_them(self):
        threads = [
            {"id": ident, "url": f"url-{ident}", "path": "test.py",
             "opened": f"2026-10-01T0{i}:00:00Z", "body": "\u00e9" * 900}
            for i, ident in enumerate("abc")
        ]
        saved, calls = {}, []
        links = [{"id": "c", "original": "a", "reason": "Same missing test."}]

        def checkpoint(stage, inputs, evaluate):
            key = stage, digest(inputs)
            if key not in saved:
                saved[key] = evaluate()
            return saved[key]

        def ask(prompt, data):
            self.assertLessEqual(input_size(prompt, data), 3500)
            calls.append(prompt)
            if prompt == DEDUPLICATE:
                self.assertEqual([row["id"] for row in data["findings"]], ["a", "b", "c"])
                return links
            return [{"id": item["id"], "kind": "test coverage gap", "reason": "Missing test."}
                    for item in data["threads"]]

        model = SimpleNamespace(ask=ask)
        pull = {"threads": threads}
        with patch("assessment.MAX_INPUT", 3500):
            rows = classify(model, pull, checkpoint)
            self.assertEqual(calls, [CLASSIFY_BATCH, CLASSIFY_BATCH, CLASSIFY_BATCH, DEDUPLICATE])
            self.assertEqual((rows[2]["original"], rows[2]["original_url"]), ("a", "url-a"))
            links.append({"id": "b", "original": "c", "reason": "Invalid chain."})
            calls.clear()
            with self.assertRaisesRegex(AssessmentError, "earlier original"):
                classify(model, pull, checkpoint)
            self.assertEqual(calls, [DEDUPLICATE])

    def test_prioritizes_split_prs_ahead_of_earlier_repositories(self):
        pulls = {REPOS[0]: [{"repo": REPOS[0], "number": 1, "threads": []}],
                 REPOS[-1]: [{"repo": REPOS[-1], "number": 2, "threads": [
                     {"id": "t", "body": "x" * 180_000, "path": "a", "opened": "time"},
                 ]}]}
        bundle = {"threads": {"t": {"paths": ["a"]}}}
        archive = Mock()
        archive.pulls.side_effect = lambda repo, *_: iter(pulls.get(repo, []))
        archive.get_prepared.return_value = bundle
        self.assertEqual([(pull["number"], evidence) for pull, evidence in assessment_order(archive, BASELINE, True)],
                         [(2, bundle), (1, None)])

    def test_addressed_is_independent_of_fix_timing(self):
        records = {
            "reviewed-0": {"url": "reviewed-url", "state": "text", "path": "code.py"},
            "merged-0": {"url": "merged-url", "state": "text", "path": "code.py"},
            "change-0": {"url": "later-url", "dated_after_publication": True},
        }
        reply = {"status": "addressed", "reason": "Matching code is kept.",
                 "evidence": ["reviewed-0", "merged-0"]}
        args = ({"merged": "time"}, {}, {"paths": ["code.py"]}, records)
        self.assertEqual(judge(FakeModel(reply), *args)["status"], "addressed")
        self.assertEqual(judge(FakeModel(reply), *args)["change_timing"], "unknown")
        reply["fix"] = "change-0"
        self.assertEqual(judge(FakeModel(reply), *args)["change_timing"], "dated_after_publication")
        reply["status"], reply["regressed"] = "unaddressed", True
        self.assertTrue(judge(FakeModel(reply), *args)["regressed"])
        reply["evidence"] = ["merged-url", "not-supplied"]
        self.assertEqual(judge(FakeModel(reply), *args)["status"], "unclear")

    def test_change_budget_can_retain_more_than_twelve_small_diffs(self):
        changes = [{"patch": "x" * 1000}, *({"patch": str(i)} for i in range(15))]
        self.assertEqual(list(pack_changes(changes, 250)), changes[1:])

    def test_git_lookup_uses_archived_revisions_and_rejects_non_repo_paths(self):
        with TemporaryDirectory() as work:
            root = Path(work)
            _, pull, thread, evidence, _ = next(pilot_cases(root))
            retrieval = OutcomeEvidence(root, pull)
            retrieval.source = evidence
            denied, _ = retrieval.lookup(thread, {"action": "read", "path": "../local", "query": ""}, "0")
            self.assertEqual(denied, {})
            snapshots, _ = retrieval.lookup(thread, {"action": "search", "path": None, "query": "is_adult"}, "1")
            self.assertIn("age > 18", snapshots["reviewed-lookup-1-0"]["text"])
            self.assertIn("age >= 18", snapshots["merged-lookup-1-0"]["text"])
            changes, _ = retrieval.lookup(
                thread, {"action": "history", "path": "adult.py", "query": "age >= 18"}, "2")
            self.assertFalse(changes["change-lookup-2-0"]["dated_after_publication"])
            self.assertIn("+    return age >= 18", changes["change-lookup-2-0"]["patches"][0]["patch"])

    def test_judge_validates_citations_against_retrieved_records_and_limits_requests(self):
        requested = {"lookup": {"action": "read", "path": "helper.py", "query": ""}}
        added = {f"{side}-lookup-0": {"url": side, "state": "text"} for side in ("reviewed", "merged")}
        retrieval = Mock()
        retrieval.lookup.side_effect = [(added, "Read"), ({}, "No more evidence")]
        model = Mock()
        model.ask.side_effect = [requested, requested, {
            "status": "addressed", "reason": "The check is gone.", "evidence": list(added),
        }]
        result = judge(model, {"merged": "time"}, {}, {}, {}, retrieval=retrieval)
        self.assertEqual((result["status"], result["evidence"]), ("addressed", ["reviewed", "merged"]))
        self.assertEqual([call.args[0] for call in model.ask.call_args_list], [LOOKUP_JUDGE, LOOKUP_JUDGE, JUDGE])
        self.assertEqual(retrieval.lookup.call_count, 2)

    def test_retrieval_reuses_static_judgments_and_only_retries_unclear_findings(self):
        threads = [
            {"id": ident, "url": ident, "body": "Rejects adults.", "path": "adult.py", "opened": "time"}
            for ident in ("addressed", "unaddressed", "unclear", "other")
        ]
        pull = {"repo": REPOS[0], "head": "head", "merge": "merge", "merged": "time", "threads": threads}
        records = [{"url": side, "state": "text", "path": "adult.py"} for side in ("reviewed", "merged")]
        bundle = {"records": records, "threads": {
            t["id"]: {"paths": ["adult.py"], "omitted_related_paths": 0,
                      "records": {"reviewed-0": 0, "merged-0": 1}}
            for t in threads
        }}
        rows = [{"id": t["id"], "kind": "other" if t["id"] == "other" else "bug", "reason": "Age check."}
                for t in threads]
        citations = ["reviewed-0", "merged-0"]
        added = {f"{side}-lookup-0": {"url": f"{side}-helper", "state": "text"}
                 for side in ("reviewed", "merged")}
        request = {"action": "read", "path": "helper.py", "query": ""}
        retrieval = Mock()
        retrieval.lookup.return_value = added, "Read"
        model = Mock()
        model.ask.side_effect = [
            rows, *({"status": t["id"], "reason": t["id"], "evidence": citations} for t in threads[:3]),
            {"lookup": request}, {"status": "addressed", "reason": "Helper fixed it.", "evidence": list(added)},
        ]
        saved = {}

        def checkpoint(stage, inputs, evaluate):
            key = stage, digest(inputs)
            if key not in saved:
                saved[key] = evaluate()
            return saved[key]

        first = assess(bundle, model, pull, checkpoint)
        retried = assess(bundle, model, pull, checkpoint, retrieval=retrieval)
        self.assertEqual([first[i] for i in (0, 1, 3)], [retried[i] for i in (0, 1, 3)])
        self.assertEqual(retried[2]["first_pass"]["status"], "unclear")
        self.assertEqual((retried[2]["status"], retried[2]["evidence"]),
                         ("addressed", ["reviewed-helper", "merged-helper"]))
        retrieval.lookup.assert_called_once_with(threads[2], request, "0")
        self.assertEqual(retried, assess(bundle, model, pull, checkpoint, retrieval=retrieval))
        self.assertEqual([call.args[0] for call in model.ask.call_args_list],
                         [CLASSIFY, JUDGE, JUDGE, JUDGE, LOOKUP_JUDGE, LOOKUP_JUDGE])

    def test_retrieval_result_is_not_selected_for_default_publication(self):
        pull = {"repo": REPOS[0], "number": 1}
        bundle = {"records": []}
        with TemporaryDirectory() as work, closing(Archive(Path(work) / "archive.sqlite3")) as archive:
            for version in LOCAL_ASSESSMENT_VERSIONS:
                archive.put_judgment(pull["repo"], 1, "complete", version, {
                    "assessment_version": version, "evidence_digest": digest(bundle),
                })
            shared = SharedData(archive, REPOS)
            shared.imported = {}
            self.assertEqual(list(shared.assessment_records(pull["repo"], 1, "1.json", pull, bundle)), [])

    def test_retrieval_publication_replays_citations_without_publishing_transcript(self):
        with TemporaryDirectory() as work:
            root = Path(work)
            _, pull, thread, evidence, _ = next(pilot_cases(root))
            retrieval = OutcomeEvidence(root, pull)
            retrieval.source = evidence
            request = {"action": "read", "path": "adult.py", "query": "is_adult"}
            link = f"https://github.com/{pull['repo']}/blob/{pull['merge']}/adult.py"
            bundle = {"records": [], "threads": {thread["id"]: {"records": {}}}}
            model, effort = "test-model", "high"
            key = judgment_key(model, [RETRIEVAL_VERSION, pull, digest(bundle)], effort)
            row = {
                "id": thread["id"], "kind": "bug", "status": "addressed", "evidence": [link],
                "first_pass": {"status": "unclear"}, "outcome_reason": "Local explanation",
                "lookups": [{"request": request, "records": ["reviewed-lookup-0", "merged-lookup-0"]}],
            }
            result = {"assessment_version": RETRIEVAL_VERSION, "model": model, "reasoning_effort": effort,
                      "evidence_digest": digest(bundle), "threads": [row]}
            with closing(Archive(root / "archive.sqlite3")) as archive:
                shared = SharedData(archive, REPOS, retrieval_git_root=root)
                archive.put_judgment(pull["repo"], 1, "complete", key, result)
                with patch("shared_data.OutcomeEvidence", return_value=retrieval):
                    [(_, _, record)] = shared.assessment_records(pull["repo"], 1, "1.json", pull, bundle)
                self.assertEqual(record["threads"], [
                    {"id": thread["id"], "kind": "bug", "status": "addressed", "evidence": [link]},
                ])
                for records in (["reviewed-lookup-0"], ["merged-lookup-1"]):
                    row["lookups"][0]["records"] = records
                    with self.assertRaises(DataError):
                        public_assessment(pull, result, key, bundle=bundle, lookup=retrieval.lookup)

    @unittest.skipIf(sys.version_info < (3, 11), "SDK requires Python 3.11")
    def test_sdk_reuses_client_but_replaces_prompt_in_each_tool_free_session(self):
        response = SimpleNamespace(data=SimpleNamespace(content='```json\n{"ok":true}\n```'))
        sessions = [AsyncMock(), AsyncMock()]
        for session in sessions:
            session.send_and_wait.return_value = response
        client = Mock(create_session=AsyncMock(side_effect=sessions), stop=AsyncMock())
        sdk = SimpleNamespace(CopilotClient=Mock(return_value=client), JsonRpcError=RuntimeError)
        with patch.dict("sys.modules", {"copilot": sdk}), patch.dict(os.environ, {"COPILOT_GITHUB_TOKEN": "test"}):
            with closing(Copilot("model", "medium")) as model:
                self.assertEqual(model.ask(CLASSIFY, {}), {"ok": True})
                self.assertEqual(model.ask(JUDGE, {}), {"ok": True})
        sdk.CopilotClient.assert_called_once()
        self.assertEqual(sdk.CopilotClient.call_args.kwargs["mode"], "empty")
        for call, session, prompt in zip(client.create_session.await_args_list, sessions, (CLASSIFY, JUDGE), strict=True):
            self.assertEqual(call.kwargs, {"model": "model", "reasoning_effort": "medium", "available_tools": [],
                                           "system_message": {"mode": "replace", "content": prompt.strip()}})
            session.send_and_wait.assert_awaited_once()
            session.disconnect.assert_awaited_once()
        client.stop.assert_awaited_once()

    @unittest.skipIf(sys.version_info < (3, 11), "SDK requires Python 3.11")
    def test_sdk_gives_max_reasoning_more_time_and_reports_timeouts(self):
        session = AsyncMock()
        session.send_and_wait.side_effect = TimeoutError
        client = Mock(create_session=AsyncMock(return_value=session), stop=AsyncMock())
        sdk = SimpleNamespace(CopilotClient=Mock(return_value=client), JsonRpcError=RuntimeError)
        with patch.dict("sys.modules", {"copilot": sdk}), patch.dict(os.environ, {"COPILOT_GITHUB_TOKEN": "test"}):
            with closing(Copilot("model", "max")) as model:
                with self.assertRaisesRegex(AssessmentError, r"model \(max\) timed out after"):
                    model.ask(CLASSIFY, {})
        self.assertEqual(client.create_session.await_args.kwargs["reasoning_effort"], "max")
        self.assertGreater(session.send_and_wait.await_args.kwargs["timeout"], 180)
        session.abort.assert_awaited_once()
        session.disconnect.assert_awaited_once()

    def test_sdk_request_respects_collection_deadline(self):
        session = AsyncMock()
        session.send_and_wait.side_effect = TimeoutError
        client = Mock(create_session=AsyncMock(return_value=session), stop=AsyncMock())
        sdk = SimpleNamespace(CopilotClient=Mock(return_value=client), JsonRpcError=RuntimeError)
        with (patch.dict("sys.modules", {"copilot": sdk}),
              patch.dict(os.environ, {"COPILOT_GITHUB_TOKEN": "test"}),
              patch("github_data.monotonic", return_value=10) as clock,
              closing(Copilot("model", deadline=12)) as model):
            with self.assertRaises(BudgetExhausted):
                model.ask(CLASSIFY, {})
            self.assertEqual(session.send_and_wait.await_args.kwargs["timeout"], 2)
            session.abort.assert_awaited_once()
            clock.return_value = 12
            with self.assertRaises(BudgetExhausted):
                model.ask(JUDGE, {})
        client.create_session.assert_awaited_once()

    def test_judge_preserves_core_evidence_when_dropping_optional_context(self):
        records = {
            "reviewed-0": {"url": "reviewed", "state": "text", "path": "a"},
            "merged-0": {"url": "merged", "state": "text", "path": "a"},
            "comparison": {"url": "comparison", "complete": True},
            "change-0": {"url": "later", "dated_after_publication": True},
            "merge-commit": {"url": "merge-commit", "text": "x" * 180_000},
            "reply-0": {"url": "reply"},
        }
        model = Mock()
        model.ask.return_value = {"status": "addressed", "reason": "The fix survived.",
                                  "evidence": ["reviewed-0", "merged-0"], "fix": "change-0"}
        thread = {"hunk": "hunk", "replies": [{"body": "reply"}]}
        args = (model, {"merged": "time"}, thread, {"paths": ["a"]}, records)
        self.assertEqual(judge(*args)["status"], "addressed")
        self.assertEqual(set(model.ask.call_args.args[1]["evidence"]),
                         {"reviewed-0", "merged-0", "comparison", "change-0"})
        model.ask.reset_mock()
        records["change-1"] = {"url": "oversized later", "text": "x" * 180_000}
        self.assertEqual(judge(*args)["status"], "addressed")
        sent = model.ask.call_args.args[1]["evidence"]
        self.assertNotIn("change-1", sent)
        model.ask.reset_mock()
        records["reviewed-0"]["text"] = "x" * 180_000
        self.assertEqual(judge(*args)["status"], "unclear")
        model.ask.assert_not_called()

    def test_merged_snapshot_can_establish_unaddressed_without_complete_history(self):
        records = {
            "reviewed-0": {"url": "reviewed-url", "state": "text", "path": "code.py"},
            "merged-0": {"url": "merged-url", "state": "text", "path": "code.py"},
            "comparison": {"url": "compare-url", "complete": False},
        }
        reply = {"status": "unaddressed", "reason": "No matching change.",
                 "evidence": list(records)}
        args = ({"merged": "time"}, {}, {"paths": ["code.py"]}, records)
        self.assertEqual(judge(FakeModel(reply), *args)["status"], "unaddressed")
        records["comparison"]["complete"] = True
        self.assertEqual(judge(FakeModel(reply), *args)["status"], "unaddressed")


class InstructionDraftTests(unittest.TestCase):
    def test_examples_require_an_addressed_human_comment_and_cited_snapshot_pair(self):
        repo = REPOS[0]
        base = f"https://github.com/{repo}"
        day = BASELINE[0].isoformat()
        threads = [
            {"id": ident, "opened": f"{day}T08:00:00Z", "url": f"{base}/pull/1#{ident}", "path": "runner.py",
             "author": {"login": kind.lower(), "__typename": kind}, "body": body}
            for ident, kind, body in (
                ("human", "User", "The runner hides the rejected value."),
                ("bot", "Bot", "The runner hides the rejected value."),
                ("delegate", "User", "@copilot implement the review feedback."),
                ("unaddressed", "User", "The runner hides the rejected value."),
                ("unpaired", "User", "The runner hides the rejected value."),
            )
        ]
        pull = {"repo": repo, "number": 1, "merged": f"{day}T12:00:00Z", "threads": threads}
        before, after = (f"{base}/blob/{rev * 40}/runner.py" for rev in "ab")
        bundle = {
            "version": PREPARED_VERSION, "input": digest(pull),
            "threads": {t["id"]: {"records": {"reviewed-0": 0, "merged-0": 1}} for t in threads},
            "records": [
                {"path": "runner.py", "url": before, "text": "shown = None"},
                {"path": "runner.py", "url": after, "text": "shown = rejected"},
            ],
        }
        inputs = [RETRIEVAL_VERSION, pull, digest(bundle)]
        key = judgment_key("model", inputs, "high")
        result = {
            "assessment_version": RETRIEVAL_VERSION, "evidence_digest": digest(bundle),
            "model": "model", "reasoning_effort": "high",
            "threads": [
                {"id": t["id"], "kind": "bug",
                 "status": "unaddressed" if t["id"] == "unaddressed" else "addressed",
                 "evidence": [before] if t["id"] == "unpaired" else [before, after]}
                for t in threads
            ],
        }
        with TemporaryDirectory() as work, closing(Archive(Path(work) / "archive.sqlite3")) as archive:
            with archive.db:
                archive.db.execute("INSERT INTO pulls VALUES (?, 1, ?, ?, ?)",
                                   (repo, day, pull["merged"], encode(pull)))
                archive.db.execute("INSERT INTO pull_authors VALUES (?, 1, 'user', 'User')", (repo,))
            archive.put_prepared(repo, 1, bundle)
            archive.put_judgment(repo, 1, "published", key, result)
            examples, addressed = archived_examples(
                archive, repo, BASELINE, human=lambda author: identity(author, set(), set()) == "human",
                bot_pr=lambda login, kind: kind == "Bot",
            )
        self.assertEqual(addressed, 3)
        self.assertEqual(len(examples), 1)
        self.assertEqual(examples[0]["urls"], [threads[0]["url"], before, after])
        self.assertEqual(examples[0]["change"], "-shown = None\n+shown = rejected")

    def test_discovery_reads_guides_and_prior_instructions_but_replaces_only_copilot_files(self):
        with TemporaryDirectory() as work:
            root = Path(work)
            repo = root / "repo"
            repo.mkdir()
            git_at(repo, "init", "--quiet")
            git_at(repo, "config", "user.name", "Draft fixture")
            git_at(repo, "config", "user.email", "fixture@example.test")
            originals = {
                ".github/copilot-instructions.md": "Old global instructions\n",
                ".github/instructions/old/nested.instructions.md": "Old scoped instructions\n",
                AUTHORING_INSTRUCTIONS: "Old authoring instructions\n",
                ".github/instructions/notes.md": "Shared note\n",
                "AGENTS.md": "Shared agent guide\n" * 250 + "Record observations without declaring expectations.\n",
                "README.md": "The runner distinguishes rejected attributes from missing ones.\n",
                "tools/runner/runner.py": "def run(): pass\n",
            }
            for path, text in originals.items():
                file = repo / path
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text(text, encoding="utf-8")
            git_at(repo, "add", ".")
            git_at(repo, "commit", "--quiet", "-m", "Create draft fixture")
            rule = {"scopes": ["tools/runner"], "rule": "Record observations without declaring expectations.",
                    "sources": ["D1"]}

            def answer(prompt, data):
                if prompt == EDIT_DRAFT:
                    return []
                self.assertEqual({row["text"].strip() for row in data["sources"].values() if row["kind"] == "prior"},
                                 {"Old global instructions", "Old scoped instructions"})
                shared = next(row for row in data["sources"].values() if row["path"] == "AGENTS.md")
                self.assertIn("without declaring expectations", shared["text"])
                candidate = dict(rule, sources=["I1"], marker=None, impact="behavior",
                                 audit_example="A workload embeds assertions and fails before recording a sample.")
                if prompt == DISCOVER:
                    return [candidate]
                self.assertTrue(prompt.startswith(FINALIZE))
                self.assertEqual(data["candidates"], [dict(candidate, marker_comparisons=[])])
                if prompt == FINALIZE:
                    return [rule]
                self.assertIn("audit_example", prompt)
                promoted = dict(rule, audit_example="A workload embeds assertions and fails before recording a sample.",
                                contract={"source": "D1", "quote": originals["AGENTS.md"].splitlines()[-1]})
                return [promoted, promoted]

            model = Mock()
            model.ask.side_effect = answer
            snapshot = Snapshot(repo / ".git")
            rules, sources = draft_rules(model, snapshot, REPOS[0], [])
            files, evidence = render_rules(rules, sources)
            patch_bytes, removed = snapshot.replacement(files, root / "draft.index")
            self.assertEqual(git_at(repo, "status", "--short"), "")
            self.assertEqual(removed, [
                ".github/copilot-instructions.md",
                ".github/instructions/old/nested.instructions.md",
            ])
            self.assertEqual(len(evidence), 1)
            self.assertEqual(evidence[0]["contract"]["quote"], originals["AGENTS.md"].splitlines()[-1])
            patch_file = root / "draft.patch"
            patch_file.write_bytes(patch_bytes)
            git_at(repo, "apply", str(patch_file))
            for path in removed:
                self.assertFalse((repo / path).exists())
            self.assertIn('applyTo: "tools/runner/**"',
                          (repo / ".github/instructions/tools-runner.instructions.md").read_text(encoding="utf-8"))
            authoring = (repo / AUTHORING_INSTRUCTIONS).read_text(encoding="utf-8")
            self.assertEqual(authoring, files[AUTHORING_INSTRUCTIONS])
            self.assertTrue(authoring.startswith(
                '---\napplyTo: ".github/copilot-instructions.md,.github/instructions/**/*.instructions.md"\n'
                '---\n\n# Writing repository instructions\n',
            ))
            self.assertNotIn("Old authoring instructions", authoring)
            for path in ("AGENTS.md", ".github/instructions/notes.md", "README.md", "tools/runner/runner.py"):
                self.assertEqual((repo / path).read_text(encoding="utf-8"), originals[path])
            self.assertEqual(model.ask.call_count, 4)
            git_at(repo, "add", ".")
            git_at(repo, "commit", "--quiet", "-m", "Apply replacement")
            self.assertEqual(Snapshot(repo / ".git").replacement(files, root / "draft.index"), (b"", []))

    def test_repository_wide_instructions_contain_only_code_rules(self):
        files, evidence = render_rules([{
            "scopes": [], "rule": "Shared workloads do not initialize telemetry.",
            "sources": ["D1"], "contract": None,
        }], {"D1": {"kind": "guide", "path": "README.md", "urls": ["https://example.test/README.md"]}})
        self.assertEqual(set(files), {".github/copilot-instructions.md", AUTHORING_INSTRUCTIONS})
        self.assertEqual([row["file"] for row in evidence], [".github/copilot-instructions.md"])
        self.assertEqual(
            files[".github/copilot-instructions.md"],
            "# Repository code rules\n\n"
            "- Shared workloads do not initialize telemetry.\n",
        )

    def test_multiline_rule_keeps_examples_inside_one_bullet_without_overwriting_authoring_guide(self):
        sources = {"D1": {
            "kind": "guide", "path": "README.md", "text": "A script that writes results keeps stdout machine-readable.",
            "urls": ["https://example.test/README.md"],
        }}
        rule = {
            "scopes": ["instruction/authoring"],
            "rule": "A script that writes results keeps stdout machine-readable.\n\n"
                    "For example, this command puts diagnostics on stderr:\n\n"
                    "```sh\nif ! validate; then\n  echo 'invalid  input' >&2\nfi\n```",
            "sources": ["D1"],
            "contract": {"source": "D1", "quote": sources["D1"]["text"]},
            "audit_example": "A validation script prints a warning to stdout and breaks its JSON consumer.",
        }
        self.assertEqual(validate_rules([rule], rule["scopes"], sources, final=True), [rule])
        files, evidence = render_rules([rule], sources)
        code_path = evidence[0]["file"]
        self.assertNotEqual(code_path, AUTHORING_INSTRUCTIONS)
        self.assertEqual(set(files), {AUTHORING_INSTRUCTIONS, code_path})
        self.assertEqual(files[code_path], (
            '---\napplyTo: "instruction/authoring/**"\n---\n\n'
            "# Code rules for instruction/authoring\n\n"
            "- A script that writes results keeps stdout machine-readable.\n\n"
            "  For example, this command puts diagnostics on stderr:\n\n"
            "  ```sh\n  if ! validate; then\n    echo 'invalid  input' >&2\n  fi\n  ```\n"
        ))
        with self.assertRaisesRegex(DataError, "must start with prose"):
            validate_rules([dict(rule, rule="- " + rule["rule"])], rule["scopes"], sources, final=True)

    def test_model_names_scoped_files_and_sees_existing_names(self):
        with TemporaryDirectory() as work:
            repo = Path(work)
            git_at(repo, "init", "--quiet")
            git_at(repo, "config", "user.name", "Draft fixture")
            git_at(repo, "config", "user.email", "fixture@example.test")
            existing = repo / ".github/instructions/database-contracts.instructions.md"
            existing.parent.mkdir(parents=True)
            existing.write_text('---\napplyTo: "scenarios/database/contracts/**"\n---\n\n# Database contracts\n\n'
                                "- Old rule.\n", encoding="utf-8")
            (repo / AUTHORING_INSTRUCTIONS).write_text("Bundled guide\n", encoding="utf-8")
            git_at(repo, "add", ".")
            git_at(repo, "commit", "--quiet", "-m", "Create naming fixture")
            rules = [
                {"scopes": ["scenarios/database/contracts"], "rule": "Contracts list every column.",
                 "sources": ["D1"], "contract": None},
                {"scopes": ["scenarios/browser/js", "scenarios/http/js"], "rule": "Scenarios pin dependencies.",
                 "sources": ["D1"], "contract": None},
                {"scopes": [], "rule": "Workloads do not initialize telemetry.", "sources": ["D1"], "contract": None},
            ]
            model = Mock()
            model.ask.return_value = {
                "G1": {"slug": "database-contracts", "title": "Database contracts"},
                "G2": {"slug": "js-scenario-dependencies", "title": "JavaScript scenario dependencies"},
            }
            names = name_files(model, Snapshot(repo / ".git"), rules)
        prompt, data = model.ask.call_args.args
        self.assertEqual(prompt, NAME_FILES)
        self.assertEqual(data["existing"], [{
            "path": ".github/instructions/database-contracts.instructions.md",
            "applyTo": "scenarios/database/contracts/**", "title": "Database contracts",
        }])
        self.assertEqual(data["groups"], {
            "G1": {"scopes": ["scenarios/database/contracts"], "rules": ["Contracts list every column."]},
            "G2": {"scopes": ["scenarios/browser/js", "scenarios/http/js"], "rules": ["Scenarios pin dependencies."]},
        })
        files, evidence = render_rules(rules, {"D1": {"kind": "guide", "path": "README.md", "urls": ["u"]}}, names)
        self.assertEqual([row["file"] for row in evidence], [
            ".github/instructions/database-contracts.instructions.md",
            ".github/instructions/js-scenario-dependencies.instructions.md",
            ".github/copilot-instructions.md",
        ])
        self.assertEqual(files[".github/instructions/js-scenario-dependencies.instructions.md"], (
            '---\napplyTo: "scenarios/browser/js/**,scenarios/http/js/**"\n---\n\n'
            "# JavaScript scenario dependencies\n\n- Scenarios pin dependencies.\n"
        ))
        self.assertEqual(files[".github/copilot-instructions.md"],
                         "# Repository code rules\n\n- Workloads do not initialize telemetry.\n")

    def test_invalid_or_conflicting_model_file_names_fall_back_to_paths(self):
        snapshot = SimpleNamespace(entries={}, read=lambda paths: {})
        rules = [{"scopes": [scope], "rule": f"Rule for {scope}.", "sources": ["D1"], "contract": None}
                 for scope in ("tools/report", "tools/runner", "tools/mock", "docs/a", "docs/b", "docs/c", "docs/d")]
        model = FakeModel({
            "G1": {"slug": "Coverage_Scoring", "title": "Coverage scoring"},
            "G2": {"slug": "instruction-authoring", "title": "Runner rules"},
            "G3": {"slug": "mock-server", "title": "# Mock server"},
            "G4": {"slug": "shared-docs", "title": "Shared docs"},
            "G5": {"slug": "shared-docs", "title": "Shared docs"},
            "G6": {"slug": "tools-report", "title": "Report tools"},
            "G7": {"slug": "doc-index", "title": "Documentation index"},
        })
        names = name_files(model, snapshot, rules)
        self.assertEqual(names, {("docs/d",): ("doc-index", "Documentation index")})
        files, _ = render_rules(rules, {"D1": {"kind": "guide", "path": "README.md", "urls": ["u"]}}, names)
        self.assertEqual(sorted(files), sorted([AUTHORING_INSTRUCTIONS, *(
            f".github/instructions/{name}.instructions.md"
            for name in ("tools-report", "tools-runner", "tools-mock", "docs-a", "docs-b", "docs-c", "doc-index"))]))
        self.assertIn("# Code rules for tools/report\n",
                      files[".github/instructions/tools-report.instructions.md"])
        self.assertEqual(name_files(FakeModel(["not", "an", "object"]), snapshot, rules), {})
        failing = Mock()
        failing.ask.side_effect = AssessmentError("Copilot did not return valid JSON")
        self.assertEqual(name_files(failing, snapshot, rules), {})

    def test_unlisted_scopes_do_not_discard_other_rules_after_correction(self):
        valid = {"scopes": ["tools"], "rule": "Runners keep diagnostics separate.",
                 "marker": None, "sources": ["D1"], "impact": "behavior",
                 "audit_example": "A runner writes diagnostics to stdout and breaks its JSON consumer."}
        invalid = dict(valid, scopes=["tools/*"])
        model = Mock()
        model.ask.side_effect = [[invalid, valid], [invalid, valid]]
        with self.assertLogs(level="WARNING") as logs:
            self.assertEqual(ask_rules(model, DISCOVER, {"scopes": ["tools"], "sources": {"D1": {}}}), [valid])
        self.assertIn("tools/*", logs.output[0])
        self.assertIn("tools/*", model.ask.call_args.args[0])

    def test_invalid_marker_does_not_discard_candidate(self):
        rule = {"scopes": ["tools"], "rule": "Runners keep diagnostics separate.",
                "sources": ["D1"], "marker": {"kind": "path", "value": "tools/runner"}, "impact": "behavior",
                "audit_example": "A runner writes diagnostics to stdout and breaks its JSON consumer."}
        with self.assertLogs(level="WARNING"):
            self.assertEqual(validate_rules([rule], ["tools"], {"D1": {}}), [dict(rule, marker=None)])

    def test_tight_budget_preserves_supported_rules_that_catch_failures(self):
        sources = {
            "D1": {"kind": "guide", "text": "Migrations are safe to retry. Migration filenames use lowercase."},
            "R1": {"kind": "review", "pr": 42, "path": "db/init.sql", "review": "Keep a backup before deleting."},
        }
        failure = {"scopes": ["db"], "rule": "Migrations tolerate retries.", "sources": ["D1"], "marker": None,
                   "impact": "behavior", "audit_example": "A retried backfill inserts duplicate account rows."}
        convention = dict(failure, rule="Migration filenames use lowercase.", impact="convention",
                          audit_example="A mixed-case filename differs from its neighbors.")
        unsupported = dict(failure, rule="Migrations back up dropped tables.", sources=["R1"],
                           audit_example="A migration drops a table without a backup and rollback loses the data.")
        data = consolidation_input(["db"], [unsupported, convention, failure], sources,
                                   input_size(FINALIZE, {"scopes": ["db"], "candidates": [failure],
                                                         "sources": {"D1": sources["D1"]}}) + 1_000)
        self.assertEqual(data["candidates"], [failure])
        self.assertEqual(set(data["sources"]), {"D1"})

    def test_promotion_requires_a_current_contract_or_more_than_one_pr(self):
        sources = {
            "D1": {"kind": "guide", "text": "Shared scenarios have no\n instrumentation setup."},
            "I1": {"kind": "prior", "text": "Shared scenarios have no instrumentation setup."},
            "R1": {"kind": "review", "pr": 42, "path": "scenarios/http/java/Server.java"},
            "R2": {"kind": "review", "pr": 42, "path": "scenarios/http/js/server.js"},
            "R3": {"kind": "review", "pr": 65, "path": "scenarios/http/js/server.js"},
            "C1": {"kind": "comparison", "path": "scenarios/database/java/Server.java"},
        }
        rule = {"scopes": ["scenarios"], "rule": "Shared scenarios are instrumented externally.",
                "audit_example": "A Redis workload starts a telemetry provider and reports its own spans."}
        guide = dict(rule, sources=["D1"], contract={"source": "D1", "quote":
                     "Shared scenarios have no instrumentation setup."})
        independent = dict(rule, sources=["R1", "R3"], contract=None, scopes=["scenarios/http"])
        wider = dict(independent, scopes=["scenarios"])
        compared = dict(wider, sources=["C1", "R1", "R3"])
        with self.assertLogs(level="WARNING") as logs:
            promoted = validate_rules([
                guide,
                dict(guide, contract={"source": "D1", "quote": "Invented contract."}),
                dict(guide, sources=["I1"], contract={"source": "I1", "quote": guide["contract"]["quote"]}),
                dict(guide, sources=["D1", "I1"]),
                dict(rule, sources=["D1", "R1", "R2"], contract=None),
                independent,
                wider,
                compared,
                dict(independent, audit_example=""),
            ], ["scenarios", "scenarios/http"], sources, final=True)
        self.assertEqual(promoted, [guide, independent, compared])
        self.assertEqual(len(logs.output), 6)

    def test_discovery_sees_current_neighbors_and_sibling_components_before_choosing_scope(self):
        path = "scenarios/http/js/fetch/native/conformance.yaml"
        files = {
            path: "runner: http\nrun: launch fetch",
            "scenarios/http/js/xhr/native/conformance.yaml": "runner: http\nrun: launch xhr",
            "scenarios/http/java/client/native/conformance.yaml": "runner: http\nrun: launch gradle",
            "scenarios/database/java/client/native/conformance.yaml": "runner: database\nsetup: prepare backend",
            "scenarios/database/java/unrelated/README.md": "Unrelated guide.",
        }
        snapshot = SimpleNamespace(
            entries={p: ("100644", "a" * 40, len(text)) for p, text in files.items()}, revision="b" * 40,
            read=lambda paths: {p: files[p] for p in paths},
        )
        reviews = [dict(path=path, review="Keep HTTP instrumentation out of the workload.",
                        change="-runner: internal\n+runner: http", urls=[], pr=number)
                   for number in (42, 65)]

        def answer(prompt, data):
            if prompt == EDIT_DRAFT:
                return []
            sources = data["sources"]
            peer = "C1"
            candidate = {"scopes": ["scenarios/http/js"], "rule": "Declare the HTTP runner in the package.",
                         "sources": ["R1", "R2", peer]}
            discovery = dict(candidate, marker=None, impact="behavior",
                             audit_example="An XHR package picks a database runner and checks the wrong policy.")
            if prompt == DISCOVER:
                review = sources["R1"]
                peers = {sources[ref]["path"]: sources[ref]["text"] for ref in review["comparisons"]}
                self.assertEqual(peers, {p: text for p, text in files.items()
                                         if p != path and p.endswith("/conformance.yaml")})
                self.assertEqual(review["comparisons"][0], peer)
                self.assertEqual(review["current"], files[path])
                self.assertIn("scenarios/http/js/fetch/native", data["scopes"])
                return [discovery]
            self.assertEqual(prompt, FINALIZE)
            self.assertEqual(sources[peer]["path"], "scenarios/http/js/xhr/native/conformance.yaml")
            self.assertEqual(data["candidates"], [
                dict(discovery, sources=sorted(candidate["sources"]), marker_comparisons=[]),
            ])
            return [dict(candidate, audit_example="An XHR package picks a database runner and checks the wrong policy.",
                         contract=None)]

        model = Mock()
        model.ask.side_effect = answer
        rules, sources = draft_rules(model, snapshot, REPOS[0], reviews)
        _, evidence = render_rules(rules, sources)
        self.assertEqual([row["scopes"] for row in evidence], [["scenarios/http/js"]])
        self.assertIn("scenarios/http/js/xhr/native/conformance.yaml",
                      {source["path"] for source in evidence[0]["sources"]})

    def test_literal_markers_find_current_code_across_directories(self):
        with TemporaryDirectory() as work:
            repo = Path(work)
            git_at(repo, "init", "--quiet")
            git_at(repo, "config", "user.name", "Draft fixture")
            git_at(repo, "config", "user.email", "fixture@example.test")
            files = {
                "README.md": ("Background on the runner.\n\n" * 170
                              + "Each external runner uses a separate collector.\n"),
                "scenarios/go/obi/scenario.yaml": "collector: separate\n",
                "scenarios/java/obi/setup.json": '{"collector": "separate"}\n',
                "scenarios/go/mobi/scenario.yaml": "collector: shared\n",
                "tools/python/settings.py": "# padding\n" * 100 + "COLLECTOR_MODE = 'separate'\n",
                "tools/ruby/runner.rb": "collector_mode = 'shared'\n",
            }
            for path, text in files.items():
                file = repo / path
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text(text, encoding="utf-8")
            git_at(repo, "add", ".")
            git_at(repo, "commit", "--quiet", "-m", "Create scope fixture")
            rule = {"scopes": ["scenarios"], "rule": "External runners use a separate collector.", "sources": ["D1"]}

            def answer(prompt, data):
                if prompt == EDIT_DRAFT:
                    return []
                if prompt == DISCOVER:
                    candidate = dict(rule, impact="behavior",
                                     audit_example="An OBI scenario records another runner's spans in a shared collector.")
                    return [
                        dict(candidate, marker={"kind": "path", "value": "obi"}),
                        dict(candidate, marker={"kind": "text", "value": "collector_mode"}),
                    ]
                self.assertEqual(prompt, FINALIZE)
                peers = {row["marker"]["kind"]: {data["sources"][ref]["path"]: data["sources"][ref]["text"]
                                                for ref in row["marker_comparisons"]}
                         for row in data["candidates"]}
                self.assertEqual(set(peers["path"]), {"scenarios/go/obi/scenario.yaml", "scenarios/java/obi/setup.json"})
                self.assertEqual(set(peers["text"]), {"tools/python/settings.py", "tools/ruby/runner.rb"})
                self.assertIn("COLLECTOR_MODE", peers["text"]["tools/python/settings.py"])
                self.assertIn("scenarios/java/obi", data["scopes"])
                return [dict(rule, scopes=["scenarios/go/obi", "scenarios/java/obi"],
                             audit_example="An OBI scenario records another runner's spans in a shared collector.",
                             contract={"source": "D1", "quote": files["README.md"].splitlines()[-1]})]

            model = Mock()
            model.ask.side_effect = answer
            rules, _ = draft_rules(model, Snapshot(repo / ".git"), REPOS[0], [])
            self.assertEqual(rules[0]["scopes"], ["scenarios/go/obi", "scenarios/java/obi"])

    def test_post_draft_edits_reopen_guides_and_keep_untouched_rules(self):
        line = "The worker defers a retry by the delay in `Retry-After` when the server sends it."
        addition_line = "A malformed delay falls back to the configured backoff instead of dropping the job."
        guide = "Workers rate-limit their requests.\n\n" + "Background on scheduling.\n\n" * 170
        snapshot = SimpleNamespace(read=lambda paths: {
            path: guide + line + "\n\n" + addition_line for path in paths
        })
        base = {"scopes": ["workers"], "rule": "Workers respect retry delays.", "sources": ["D1"],
                "audit_example": "An immediate retry exhausts the remaining request quota.",
                "contract": {"source": "D1", "quote": "Workers rate-limit their requests."}}
        untouched = dict(base, rule="Workers rate-limit their requests.")
        refined = dict(base, rule=line, contract={"source": "D1", "quote": line})
        added = dict(base, rule=addition_line, contract={"source": "D1", "quote": addition_line})
        unsupported = dict(base, rule="Requests retry forever.", contract=None)
        data = {"scopes": ["workers"], "sources": {
            "D1": {"kind": "guide", "path": "workers/README.md", "text": guide[:100]},
        }}

        def answer(prompt, payload):
            self.assertEqual(prompt, EDIT_DRAFT)
            self.assertIn(line, payload["sources"]["D1"]["text"])
            self.assertEqual(payload["draft"], {"F1": base, "F2": untouched})
            return [
                {"target": "F1", "replacement": refined},
                {"target": "F2", "replacement": dict(refined, rule="All requests defer retries.", scopes=[])},
                {"target": None, "replacement": unsupported},
                {"target": None, "replacement": added},
            ]

        with self.assertLogs(level="WARNING") as logs:
            rules = refine_rules(Mock(ask=answer), snapshot, [base, untouched], data)
        self.assertEqual(rules, [refined, untouched, added])
        self.assertIn("changes rule scope", logs.output[0])
        self.assertIn("at least two PRs", logs.output[1])

    def test_second_model_proposals_are_judged_before_merging(self):
        quote = "Retry delays use Retry-After when provided."
        fallback = "Workers use configured backoff when the delay cannot be parsed."
        base = {"scopes": ["workers"], "rule": "Workers respect retry delays.", "sources": ["D1"],
                "audit_example": "An early retry exhausts the remaining request quota.",
                "contract": {"source": "D1", "quote": quote}}
        extra = dict(base, rule=fallback, contract={"source": "D1", "quote": fallback})
        proposed = dict(base, rule="Every worker retry uses Retry-After.")
        corrected = dict(base, rule=quote)
        data = {"scopes": ["workers"], "sources": {
            "D1": {"kind": "guide", "path": "workers/README.md", "text": quote + "\n\n" + fallback},
        }}
        snapshot = SimpleNamespace(read=lambda paths: {})
        editor = Mock()
        editor.ask.return_value = [{"target": "F1", "replacement": proposed},
                                   {"target": None, "replacement": extra}]

        def judge(prompt, payload):
            self.assertEqual(prompt, JUDGE_EDITS)
            self.assertEqual(payload["proposals"]["P1"]["replacement"], proposed)
            return [{"proposal": "P1", "replacement": corrected},
                    {"proposal": "P2", "replacement": dict(extra, scopes=[])}]

        with self.assertLogs(level="WARNING") as logs:
            result = refine_rules(Mock(ask=judge), snapshot, [base], data, editor=editor)
        self.assertEqual(result, [corrected])
        self.assertEqual(editor.ask.call_args.args[0], EDIT_DRAFT)
        self.assertIn("widens a proposed addition", logs.output[0])


class ReportTests(unittest.TestCase):
    def test_report_excludes_known_bot_prs_but_keeps_unknown_prs_and_bot_reviews(self):
        repo, day = REPOS[0], BASELINE[0].isoformat()
        with TemporaryDirectory() as work:
            path, output = (Path(work) / name for name in ("archive.sqlite3", "scorecard.html"))
            with closing(Archive(path)) as archive, archive.db:
                archive.db.executemany("INSERT INTO complete_days VALUES (?, ?)", ((r, day) for r in REPOS))
                for number, (login, kind, excluded) in enumerate((
                    ("machine", "Bot", True),
                    ("service[BOT]", "User", True),
                    ("OpenTelemetryBot", "User", True),
                    ("dependabot", "Bot", True),
                    ("renovate[bot]", "User", True),
                    (None, None, False),
                    ("person", "User", False),
                ), start=1):
                    pull = {"repo": repo, "number": number, "merged": f"{day}T12:00:00Z",
                            "missing_roots": int(excluded),
                            "threads": [{"id": "t", "author": {"login": "copilot-pull-request-reviewer[bot]",
                                                              "__typename": "Bot"}}]}
                    archive.db.execute("INSERT INTO pulls VALUES (?, ?, ?, ?, ?)",
                                       (repo, number, day, pull["merged"], encode(pull)))
                    archive.db.execute("INSERT INTO pull_authors VALUES (?, ?, ?, ?)", (repo, number, login, kind))
                    opened = f"{day}T{'00' if excluded else '10'}:00:00Z"
                    archive.db.execute("INSERT INTO pull_merge_timings VALUES (?, ?, ?, ?)",
                                       (repo, number, opened, opened))
            self.assertEqual(main(["report", "--archive", str(path), "--output", str(output),
                                   "--window", day, day]), 0)
            html = output.read_text(encoding="utf-8")
            self.assertIn("0 of 2 visible thread openers assessed. Excluded 5 bot-authored PRs.", html)
            self.assertIn("<strong>Median time to merge</strong> <span>0.1 days</span> "
                          "<small>2 merged PRs</small>", html)
            self.assertRegex(html, r'<th scope="row">semantic-conventions-genai'
                                   r'<small class="row-note">2 unassessed</small></th><td>2</td>'
                                   r'(?:<td class="missing" aria-label="Not measured" title="Not measured">'
                                   r'&mdash;</td>){3}<td><span class="rate"><span class="rate-value">0\.00</span>'
                                   r'<small class="rate-count">0 / 2</small>')
            self.assertNotIn("missing thread openers</small>", html)

    def test_report_pools_ready_to_merge_durations_and_keeps_missing_timings_in_cohort(self):
        day, later = "2026-11-15", "2026-11-16"
        first, second = REPOS[:2]
        with TemporaryDirectory() as work:
            path, output = (Path(work) / name for name in ("archive.sqlite3", "scorecard.html"))
            with closing(Archive(path)) as archive, archive.db:
                for number, (repo, merged_day, merged_hour, ready) in enumerate((
                    (first, day, 1, "2026-11-14T01:00:00Z"),
                    (second, day, 9, "2026-11-06T09:00:00Z"),
                    (second, day, 10, "2026-11-05T10:00:00Z"),
                    (first, day, 3, f"{day}T04:00:00Z"),
                    (second, day, 4, None),
                    (second, day, 4, "missing row"),
                    (first, later, 1, f"{later}T01:00:00Z"),
                ), start=1):
                    merged = f"{merged_day}T{merged_hour:02d}:00:00Z"
                    pull = {"repo": repo, "number": number, "merged": merged, "missing_roots": 0, "threads": []}
                    archive.db.execute("INSERT INTO pulls VALUES (?, ?, ?, ?, ?)",
                                       (repo, number, merged_day, merged, encode(pull)))
                    archive.db.execute("INSERT INTO pull_authors VALUES (?, ?, ?, ?)",
                                       (repo, number, "person", "User"))
                    if ready != "missing row":
                        archive.db.execute("INSERT INTO pull_merge_timings VALUES (?, ?, ?, ?)",
                                           (repo, number, "2026-10-01T00:00:00Z", ready))
            with patch("scorecard.log") as log:
                self.assertEqual(main(["report", "--archive", str(path), "--output", str(output),
                                       "--follow-up", later, later, "--custom-reviewer", "custom",
                                       "--repo", first, "--repo", second]), 0)
                self.assertTrue(any("merge timing unavailable" in call.args[0] for call in log.call_args_list))
            html = output.read_text(encoding="utf-8")
            self.assertIn("<strong>Median time to merge</strong> <span>9.0 days</span> "
                          "<small>6 merged PRs; capture incomplete; timing incomplete</small>", html)
            self.assertIn("<strong>Median time to merge</strong> <span>0.0 days</span> "
                          "<small>1 merged PR; capture incomplete</small>", html)
            self.assertIn('<small class="row-note">Capture incomplete; Timing incomplete</small></th>'
                          '<td>6</td><td>9.0 days</td><td>9.5 days</td><td>9.8 days</td>', html)
            self.assertIn('<td>4</td><td>9.5 days</td><td>9.8 days</td><td>9.9 days</td>', html)
            self.assertIn('<td>1</td><td>0.0 days</td><td>0.0 days</td><td>0.0 days</td>', html)
            self.assertNotIn("Mean time to merge", html)

    def test_selected_repositories_limit_report_scope(self):
        day = BASELINE[0].isoformat()
        with TemporaryDirectory() as work:
            path, output = (Path(work) / name for name in ("archive.sqlite3", "scorecard.html"))
            with closing(Archive(path)) as archive, archive.db:
                archive.db.executemany("INSERT INTO complete_days VALUES (?, ?)",
                                       ((repo, day) for repo in REPOS[:2]))
                excluded = REPOS[2]
                merged = f"{day}T12:00:00Z"
                archive.db.execute("INSERT INTO pulls VALUES (?, ?, ?, ?, ?)",
                                   (excluded, 1, day, merged, encode({"repo": excluded, "number": 1})))
                archive.db.execute("DROP TABLE pull_merge_timings")
            self.assertEqual(main(["report", "--archive", str(path), "--output", str(output),
                                   "--window", day, day, "--repo", REPOS[0], "--repo", REPOS[1]]), 0)
            html = output.read_text(encoding="utf-8")
            self.assertIn("across 2 public repositories.", html)
            for repo in REPOS[:2]:
                self.assertIn(f'<th scope="row">{repo.split("/")[1]}</th>', html)
            self.assertNotIn(excluded.split("/")[1], html)
            self.assertNotIn("Capture is incomplete", html)
            self.assertIn("<strong>Median time to merge</strong> <span>Not measured</span> "
                          "<small>0 merged PRs</small>", html)
            timing = re.search(r'<section aria-labelledby="timing-by-repository">.*?</section>', html, re.S)[0]
            self.assertIn('<td>0</td>' + '<td class="missing" aria-label="Not measured" title="Not measured">'
                          '&mdash;</td>' * 3, timing)

    def test_account_groups_include_bots_and_configured_users_without_merging_unknowns(self):
        self.assertEqual(identity({"login": "copilot-pull-request-reviewer", "__typename": "Bot"},
                                  set(), set()), "copilot")
        self.assertEqual(identity({"login": "LooksHuman", "__typename": "User"},
                                  {"lookshuman"}, set()), "custom")
        self.assertEqual(identity({"login": "LooksHuman", "__typename": "User"},
                                  set(), {"lookshuman"}), "outside")
        bot = {"login": "OpenTelemetryBot", "__typename": "User"}
        self.assertEqual(identity(bot, set(), set()), "outside")
        self.assertEqual(identity(bot, {"opentelemetrybot"}, set()), "custom")
        self.assertEqual(identity(None, set(), set()), "outside")

    def test_summed_rates_by_repo_and_type_preserve_missing_followup(self):
        stats = Stats()
        authors = [
            {"login": "copilot-pull-request-reviewer", "__typename": "Bot"},
            {"login": "person", "__typename": "User"},
            {"login": "custom", "__typename": "Bot"},
        ]
        stats.collect(
            {"missing_roots": 0, "threads": [{"id": str(i), "author": a} for i, a in enumerate(authors)]},
            [{"id": "0", "status": "addressed", "kind": "bug"},
             {"id": "1", "status": "duplicate", "kind": None},
             {"id": "2", "status": "unclear", "kind": "improvement"}],
            {"custom"}, set(),
        )
        second_repo = Stats(prs=2)
        second_repo.collect(
            {"missing_roots": 0, "threads": [{"id": "0", "author": authors[0]},
                                             {"id": "1", "author": authors[1]}]},
            [{"id": "0", "status": "unaddressed", "kind": "bug"},
             {"id": "1", "status": "addressed", "kind": "improvement"}],
            {"custom"}, set(),
        )
        baseline = Cohort("Baseline", BASELINE, {REPOS[0]: stats, REPOS[1]: second_repo})
        total = baseline.total
        self.assertEqual(total.finding(AUTOMATED, "addressed"), 1)
        self.assertEqual(total.finding(AUTOMATED, "unaddressed"), 1)
        follow_up = Cohort("Follow-up", (date(2026, 11, 16), date(2026, 11, 17)),
                           {REPOS[0]: Stats(missing=True)})
        html = render([baseline, follow_up], {"custom"}, "model")
        self.assertEqual(html.count("<table>"), 6)
        self.assertLess(html.index("Automated findings"), html.index("Human findings"))
        self.assertEqual(html.count('<th scope="col">Findings per merged PR</th><th scope="col">% unaddressed</th>'
                                    '<th scope="col">% unclear</th>'), 8)
        self.assertNotIn('<th scope="col" rowspan="2">Findings per merged PR</th>', html)
        total_row = re.search(r'<th scope="row">All repositories</th><td>4</td>(.*?)</tr>', html).group(1)
        self.assertEqual(re.findall(r'class="rate-value">([^<]+)', total_row),
                         ["0.75", "33.3%", "33.3%", "0.25", "0.0%", "0.0%"])
        self.assertIn('<span class="rate unaddressed" style="--rate: 33.3%"><span class="rate-value">33.3%</span>'
                      '<small class="rate-count">1 / 3</small>', total_row)
        self.assertIn('<span class="rate unclear">', total_row)
        bug_row = re.search(r'<th scope="row">Bug</th>(.*?)</tr>', html).group(1)
        self.assertIn('<small class="rate-count">2 / 4</small>', bug_row)
        self.assertIn('title="No denominator for this rate">&mdash;</td>', bug_row)
        headline = html.split('<div class="headline">', 1)[1].split("</article>", 1)[0]
        self.assertEqual(re.findall(r'class="rate-value">([^<]+)', headline), ["0.75", "33.3%", "0.25", "0.0%"])
        empty_types = html.rsplit('<th scope="row">Bug</th>', 1)[1].split("</tr>", 1)[0]
        self.assertEqual(empty_types.count('title="Not measured">&mdash;</td>'), 6)
        self.assertNotIn("N/M", html)
        self.assertNotIn("N/A", html)
        self.assertNotIn("All inline review threads", html)
        self.assertIn("5 of 5 visible thread openers assessed.", html)

    def test_incomplete_capture_shows_provisional_rates_except_for_unassessed_groups(self):
        available = Stats(prs=2, missing=True,
                          findings=Counter({("copilot", "bug", "unaddressed"): 1}))
        unassessed = Stats(prs=3, pending=Counter(human=1))
        html = render([Cohort("Baseline", BASELINE, {REPOS[0]: available, REPOS[1]: unassessed})], set(), "model")
        self.assertIn("Counts and rates are provisional and use archived PRs only.", html)
        self.assertIn("Rates that depend on unassessed or missing thread openers are not shown.", html)
        self.assertRegex(html, r'semantic-conventions-genai<small class="row-note">Capture incomplete</small>'
                               r'</th><td>2</td>.*?class="rate-value">0\.50</span>')
        total_row = re.search(r'<th scope="row">All repositories(.*?)</tr>', html).group(1)
        self.assertIn('<small class="row-note">Capture incomplete; 1 unassessed</small></th><td>5</td>', total_row)
        self.assertEqual(re.findall(r'class="rate-value">([^<]+)', total_row), ["0.20", "100.0%", "0.0%"])
        self.assertEqual(total_row.count('title="Not measured"'), 3)

        unassessed.pending["copilot"] = 1
        html = render([Cohort("Baseline", BASELINE, {REPOS[0]: available, REPOS[1]: unassessed})], set(), "model")
        self.assertIn("Headline rates cover 1 of 2 repositories,", html)
        headline = html.split('<div class="headline">', 1)[1].split("</article>", 1)[0]
        self.assertEqual(re.findall(r'class="rate-value">([^<]+)', headline), ["0.50", "100.0%", "0.00"])
        total_row = re.search(r'<th scope="row">All repositories(.*?)</tr>', html).group(1)
        self.assertEqual(total_row.count('title="Not measured"'), 6)
        types = html.split('id="by-type"', 1)[1].split("</section>", 1)[0]
        self.assertIn("2 merged PRs across 1 of 2 repositories with complete assessments</caption>", types)
        bug_row = re.search(r'<th scope="row">Bug</th>(.*?)</tr>', types).group(1)
        self.assertIn('<small class="rate-count">1 / 2</small>', bug_row)
        self.assertIn('<small class="rate-count">0 / 2</small>', bug_row)

    def test_scorecard_terms_group_and_define_the_selected_outcomes(self):
        html = render([], {"custom-reviewer"}, "model")
        self.assertRegex(html, r"<details>\s*<summary>Scorecard terms and methodology</summary>")
        self.assertNotIn("<dt>Human</dt>", html)
        self.assertRegex(html, r"<dt>Bot</dt>\s*<dd>[^<]*opentelemetrybot automation account\.</dd>")
        self.assertRegex(html, r"<dt>Automated Finding</dt>\s*<dd>[^<]*GitHub Copilot[^<]*custom-reviewer\.</dd>")
        self.assertRegex(
            html,
            r'<section class="term-group" aria-labelledby="finding-outcomes">\s*'
            r'<h3[^>]*>Finding outcomes</h3>\s*<dl[^>]*>\s*'
            r'<dt>Addressed</dt>\s*<dd>The issue is gone at merge\.</dd>\s*'
            r'<dt>Unaddressed</dt>\s*<dd>The issue is still present at merge\.</dd>\s*<dt>Unclear</dt>',
        )
        legacy = render([], set(), "model", digest("compact-sdk-v1"))
        self.assertRegex(legacy, r"<dt>Addressed</dt>\s*<dd>A matching change was made after the review opened")
        self.assertRegex(legacy, r"<dt>Unaddressed</dt>\s*<dd>No matching change was found\.</dd>")


if __name__ == "__main__":
    unittest.main()
