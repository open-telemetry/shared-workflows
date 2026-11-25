import argparse
import json
from contextlib import closing
from datetime import datetime, timedelta, timezone
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
BUNDLE_REF = "refs/heads/pr-review-metrics-snapshot"
STAGING_REF = "refs/pr-review-metrics/staged-data"
ASSESSOR = "gpt-6.2-sol"
LIVE_DAYS = 2


def remote_tip(remote=SHARED_REMOTE):
    result = run_command(["git", "--no-pager", "ls-remote", "--exit-code", remote, SNAPSHOT_REF])
    if result.returncode == 2:
        return ""
    if result.returncode or not object_id(result.stdout.partition("\t")[0]):
        raise DataError(f"Could not read the review metrics data ref: {result.stderr.strip()}")
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
    pending = {repo: sorted(archive.pending_days(repo, start, historical_end)) for repo in REPOS}
    for offset in range(max_backfill):
        for repo, days in pending.items():
            if offset < len(days):
                day = days[offset]
                yield repo, day, day


def eligible_pulls(archive, start, today):
    recent = max(start, today - timedelta(days=LIVE_DAYS - 1))
    for first, last in ((recent, today), (start, recent - timedelta(days=1))):
        cohorts = [iter(archive.pulls(repo, first, last)) for repo in REPOS]
        while cohorts:
            for cohort in tuple(cohorts):
                pull = next(cohort, None)
                if pull is None:
                    cohorts.remove(cohort)
                    continue
                author = archive.db.execute(
                    "SELECT login, kind FROM pull_authors WHERE repo = ? AND number = ?",
                    (pull["repo"], pull["number"]),
                ).fetchone()
                if author is not None and pull["threads"] and not is_bot_pr_author(*author):
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

    try:
        for repo, first, last in capture_days(archive, start, today, args.max_backfill_days):
            before = archive.capture(github, repo, first, last, today, accept_pull=accept_pull)
            log(f"{repo}: {first} to {last}, {before} new merged PRs")
        for repo in REPOS:
            archive.capture_authors(github, repo, start, today)
    except (BudgetExhausted, RateLimitError) as error:
        log(f"Capture paused: {error}")
    except COMMAND_ERRORS as error:
        log(f"Capture failed: {error}")
        failures += 1

    model = Copilot(ASSESSOR, deadline=deadline)
    try:
        for pull in eligible_pulls(archive, start, today):
            if assessed >= args.max_assessments:
                break
            repo, number = pull["repo"], pull["number"]
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
                    bundle = prepare(pull, Evidence(git_root, pull, deadline=deadline))
                    check_record(f"{repo}#{number} prepared evidence", json_bytes(bundle), bundle)
                    archive.put_prepared(repo, number, bundle)
                assessed += 1
                assess_cached(archive, bundle, model, pull, args.work / "audit", True,
                              RETRIEVAL_VERSION, git_root=git_root, deadline=deadline)
            except BudgetExhausted as error:
                log(f"Assessment paused: {error}")
                break
            except AssessmentSetupError as error:
                log(f"Assessment setup failed: {error}")
                failures += 1
                break
            except COMMAND_ERRORS as error:
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
        commit = git("commit-tree", tree, "-m", "Snapshot public PR review metrics").strip()
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
        raise DataError("Invalid review metrics snapshot identity")
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
    args = parser.parse_args()
    if args.command == "collect" and (
        min(args.max_backfill_days, args.max_evidence, args.max_assessments) < 0
        or args.max_requests < 1 or args.work_seconds < 1
    ):
        parser.error("Collection budgets must be nonnegative, with positive request and time limits")
    try:
        if args.command == "collect":
            return export_snapshot(args)
        publish_snapshot(args.artifact)
    except COMMAND_ERRORS as error:
        log(f"Review metrics {args.command} stopped: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
