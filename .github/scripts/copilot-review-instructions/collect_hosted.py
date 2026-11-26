import argparse
import json
from contextlib import closing
from datetime import datetime, timedelta, timezone
from itertools import zip_longest
from pathlib import Path
from time import monotonic

from archive import Archive
from assessment import RETRIEVAL_VERSION
from config import load_config
from copilot_model import AssessmentError, AssessmentSetupError, Copilot
from github_data import BudgetExhausted, DataError, GitHub, RateLimitError, run_command, time_remaining
from git_evidence import object_id
from prepared import Evidence, json_bytes, prepare
from review_instructions import git
from scorecard import COMMAND_ERRORS, REPOS, assess_cached, is_bot_pr_author, log
from shared_data import SHARED_REMOTE, SNAPSHOT_REF, RecordRejected, SharedData, check_record


SEED_REMOTE = "https://github.com/trask/otel-pr-review-metrics.git"
SEED_SHA = "a1e882d35cd3ef2a8c0dd9ec1a085b51363bf7c2"
BUNDLE_REF = "refs/heads/copilot-review-instructions-snapshot"
STAGING_REF = "refs/copilot-review-instructions/staged-data"
ASSESSOR = "gpt-6.2-sol"
LIVE_DAYS = 2


def remote_tip(remote=SHARED_REMOTE):
    result = run_command(["git", "--no-pager", "ls-remote", "--exit-code", remote, SNAPSHOT_REF])
    if result.returncode == 2:
        return ""
    if result.returncode or not object_id(result.stdout.partition("\t")[0]):
        raise DataError(f"Could not read the review evidence data ref: {result.stderr.strip()}")
    return result.stdout.partition("\t")[0]


def open_snapshot(archive, expected, git_root):
    shared = SharedData(archive, REPOS, ref=SNAPSHOT_REF, snapshot=True, retrieval_git_root=git_root,
                        remote=SHARED_REMOTE if expected else SEED_REMOTE)
    shared.load(expected or SEED_SHA)
    return shared


def capture_days(archive, start, today, max_backfill):
    live_start = max(start, today - timedelta(days=LIVE_DAYS - 1))
    for repo in REPOS:
        yield repo, live_start, today
    historical_end = live_start - timedelta(days=1)
    deferred = archive.collection_retries("backfill")

    def priority(repo, day):
        attempted_at = deferred.get((repo, day.isoformat()))
        return attempted_at is not None, attempted_at or 0, -day.toordinal()

    pending = {repo: sorted(archive.pending_days(repo, start, historical_end),
                            key=lambda day: priority(repo, day)) for repo in REPOS}
    for offset in range(max_backfill):
        for repo, days in pending.items():
            if offset < len(days):
                day = days[offset]
                yield repo, day, day


def eligible_pulls(archive, start, today):
    deferred = archive.collection_retries("assessments")
    retries = []
    recent = max(start, today - timedelta(days=LIVE_DAYS - 1))
    for first, last in ((recent, today), (start, recent - timedelta(days=1))):
        for cohort in zip_longest(*(archive.pulls(repo, first, last, newest_first=True) for repo in REPOS)):
            for pull in cohort:
                if pull is None:
                    continue
                author = archive.db.execute(
                    "SELECT login, kind FROM pull_authors WHERE repo = ? AND number = ?",
                    (pull["repo"], pull["number"]),
                ).fetchone()
                if author is not None and pull["threads"] and not is_bot_pr_author(*author):
                    key = pull["repo"], str(pull["number"])
                    if key in deferred:
                        retries.append((deferred[key], *key, pull))
                    else:
                        yield pull
    for _, _, _, pull in sorted(retries):
        yield pull


def collect_batch(archive, args, start, today, git_root, deadline):
    github = GitHub(max_rate_wait=0, max_requests=args.max_requests, deadline=deadline)
    failures, prepared, assessed = 0, 0, 0

    def accept_pull(pull, day):
        nonlocal failures
        path = SharedData.pull_file(pull["repo"], day.isoformat(), pull["number"])
        try:
            check_record(path, SharedData.text_record(pull), pull)
        except RecordRejected as error:
            log(f"Skipping PR record: {error}")
            failures += 1
            return False
        return True

    def capture_for(repo, label, operation, *params, **kwargs):
        nonlocal failures
        try:
            return operation(github, repo, *params, **kwargs)
        except (BudgetExhausted, RateLimitError):
            raise
        except COMMAND_ERRORS as error:
            log(f"{repo}: {label} failed: {error}")
            failures += 1
            return None

    try:
        for repo, first, last in capture_days(archive, start, today, args.max_backfill_days):
            added = capture_for(repo, f"capture for {first} to {last}", archive.capture,
                                first, last, today, accept_pull=accept_pull)
            if added is not None:
                log(f"{repo}: {first} to {last}, {added} new merged PRs")
        for repo in REPOS:
            capture_for(repo, "author capture", archive.capture_authors, start, today)
    except (BudgetExhausted, RateLimitError) as error:
        log(f"Capture paused: {error}")

    model = Copilot(ASSESSOR, deadline=deadline)
    try:
        for pull in eligible_pulls(archive, start, today):
            if assessed >= args.max_assessments:
                break
            repo, number = pull["repo"], pull["number"]
            attempting = False
            try:
                time_remaining(deadline)
                bundle = archive.get_prepared(pull)
                saved, _ = assess_cached(archive, bundle, model, pull, None, False, RETRIEVAL_VERSION)
                if saved is not None:
                    continue
                if bundle is None:
                    if prepared >= args.max_evidence:
                        continue
                    prepared += 1
                    attempting = True
                    bundle = prepare(pull, Evidence(git_root, pull, deadline=deadline))
                    check_record(f"{repo}#{number} prepared evidence", json_bytes(bundle), bundle)
                    archive.put_prepared(repo, number, bundle)
                assessed += 1
                attempting = True
                assess_cached(archive, bundle, model, pull, args.work / "audit", True,
                              RETRIEVAL_VERSION, git_root=git_root, deadline=deadline)
                archive.clear_retry(repo, "assessments", number)
            except BudgetExhausted as error:
                if attempting:
                    archive.defer_retry(repo, "assessments", number)
                log(f"Assessment paused: {error}")
                break
            except AssessmentSetupError as error:
                log(f"Assessment setup failed: {error}")
                failures += 1
                break
            except COMMAND_ERRORS as error:
                if attempting:
                    archive.defer_retry(repo, "assessments", number)
                log(f"{repo}#{number}: evidence or assessment failed: {error}")
                failures += 1
    finally:
        try:
            model.close()
        except AssessmentError as error:
            log(f"Assessment shutdown failed: {error}")
            failures += 1
    return {"api_requests": github.requests, "prepared_attempts": prepared,
            "assessment_attempts": assessed, "failures": failures}


def export_snapshot(args):
    deadline = monotonic() + args.work_seconds
    start, _ = load_config()
    today = datetime.now(timezone.utc).date()
    expected = remote_tip()
    args.work.mkdir(parents=True, exist_ok=True)
    args.artifact.mkdir(parents=True, exist_ok=True)
    git_root = args.work / "git"
    with closing(Archive(args.work / "archive.sqlite3")) as archive:
        shared = open_snapshot(archive, expected, git_root)
        details = collect_batch(archive, args, start, today, git_root, deadline)

        def reject_repository(repo, error):
            log(f"Keeping the previous snapshot for {repo}: {error}")
            details["failures"] += 1

        shared.prepare_commit(STAGING_REF, include_assessments=True, on_rejected_repository=reject_repository)
        tree = git("rev-parse", f"{STAGING_REF}^{{tree}}").strip()
        if expected and tree == git("rev-parse", f"{expected}^{{tree}}").strip():
            print(json.dumps({**details, "updated": False}))
            return int(bool(details["failures"]))
        commit = git("commit-tree", tree, "-m", "Snapshot public PR review evidence").strip()
        git("update-ref", BUNDLE_REF, commit)
        git("bundle", "create", str(args.artifact / "data.bundle"), BUNDLE_REF)
        (args.artifact / "snapshot.json").write_text(
            json.dumps({"expected": expected, "revision": commit}) + "\n", encoding="utf-8")
    print(json.dumps({**details, "updated": True}))
    return int(bool(details["failures"]))


def publish_snapshot(artifact):
    metadata = json.loads((artifact / "snapshot.json").read_text(encoding="utf-8"))
    expected, revision = metadata["expected"], metadata["revision"]
    if (expected != "" and not object_id(expected)) or not object_id(revision):
        raise DataError("Invalid review evidence snapshot identity")
    bundle = str(artifact / "data.bundle")
    git("bundle", "verify", bundle)
    git("fetch", "--quiet", "--no-tags", bundle, f"{BUNDLE_REF}:{STAGING_REF}")
    if git("rev-list", "--parents", "-1", STAGING_REF).split() != [revision]:
        raise DataError("The metrics artifact does not contain its expected parentless snapshot")
    git("cat-file", "-e", f"{revision}:schema.json")
    git("-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential",
        "push", f"--force-with-lease={SNAPSHOT_REF}:{expected}", SHARED_REMOTE, f"{revision}:{SNAPSHOT_REF}")
    if remote_tip() != revision:
        raise DataError("The published metrics snapshot could not be confirmed")
    print(revision)


def report_progress(work):
    start, _ = load_config()
    today = datetime.now(timezone.utc).date()
    stable_through = today - timedelta(days=LIVE_DAYS)
    revision = remote_tip()
    if not revision:
        raise DataError("The staging checkpoint has not been published")

    repositories = {}
    with closing(Archive(work / "progress.sqlite3")) as archive:
        open_snapshot(archive, revision, None)
        for repo in REPOS:
            missing = archive.pending_days(repo, start, stable_through)
            captured, authors = archive.db.execute("""
                SELECT count(*), count(a.number) FROM pulls p
                LEFT JOIN pull_authors a USING (repo, number)
                WHERE p.repo = ? AND p.merged_day BETWEEN ? AND ?
            """, (repo, start.isoformat(), today.isoformat())).fetchone()
            repositories[repo] = {
                "covered_days": max(0, (stable_through - start).days + 1) - len(missing),
                "missing_days": [day.isoformat() for day in missing],
                "captured_pulls": captured,
                "resolved_authors": authors,
                "missing_authors": captured - authors,
                "prepared_pulls": 0,
                "assessed_pulls": 0,
                "pending_assessments": 0,
            }

        model = Copilot(ASSESSOR)
        for pull in eligible_pulls(archive, start, today):
            row = repositories[pull["repo"]]
            bundle = archive.get_prepared(pull)
            saved, _ = assess_cached(archive, bundle, model, pull, None, False, RETRIEVAL_VERSION)
            row["prepared_pulls"] += int(bundle is not None)
            row["assessed_pulls" if saved is not None else "pending_assessments"] += 1

    counts = ("covered_days", "captured_pulls", "resolved_authors", "prepared_pulls", "assessed_pulls")
    remaining = ("missing_days", "missing_authors", "pending_assessments")
    print(json.dumps({
        "revision": revision,
        "coverage_target": stable_through.isoformat(),
        "score": sum(row[key] for row in repositories.values() for key in counts),
        "done": not any(row[key] for row in repositories.values() for key in remaining),
        "repositories": repositories,
    }))


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    snapshot = commands.add_parser("collect")
    snapshot.add_argument("--work", type=Path, required=True)
    snapshot.add_argument("--artifact", type=Path, required=True)
    snapshot.add_argument("--max-requests", type=int, default=180)
    snapshot.add_argument("--max-backfill-days", type=int, default=3)
    snapshot.add_argument("--max-evidence", type=int, default=10)
    snapshot.add_argument("--max-assessments", type=int, default=6)
    snapshot.add_argument("--work-seconds", type=int, default=1500)
    publisher = commands.add_parser("publish")
    publisher.add_argument("--artifact", type=Path, required=True)
    progress = commands.add_parser("progress")
    progress.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "collect" and (
        min(args.max_backfill_days, args.max_evidence, args.max_assessments) < 0
        or args.max_requests < 1 or args.work_seconds < 1
    ):
        parser.error("Collection budgets must be nonnegative, with positive request and time limits")
    try:
        if args.command == "collect":
            return export_snapshot(args)
        if args.command == "publish":
            publish_snapshot(args.artifact)
        else:
            report_progress(args.work)
    except COMMAND_ERRORS as error:
        log(f"Review evidence {args.command} stopped: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
