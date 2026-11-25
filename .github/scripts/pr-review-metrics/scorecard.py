import argparse
import json
import sqlite3
import sys
from collections import Counter
from contextlib import closing
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from html import escape
from pathlib import Path
from statistics import median, quantiles
from string import Template
from subprocess import TimeoutExpired
from tempfile import TemporaryDirectory

from archive import DEFAULT_ARCHIVE, Archive
from config import OWNER, load_config
from assessment import (ASSESSMENT_VERSION, KINDS, REASONING_EFFORTS, RETRIEVAL_VERSION, assess,
                        needs_classification_batches)
from copilot_model import DEFAULT_REASONING_EFFORT, AssessmentSetupError, Copilot
from github_data import DataError, GitHub, merge_timing, timing_instant
from outcome_evidence import OutcomeEvidence
from prepared import Evidence, SUPPORTED_PREPARED_VERSIONS, digest, prepare
from review_instructions import Snapshot, archived_examples, draft_rules, render_rules
from shared_data import SNAPSHOT_REF, SharedData, judgment_key


REPOS = tuple(f"{OWNER}/{name}" for name in load_config()[1])
BASELINE = (date(2026, 10, 1), date(2026, 11, 15))
COPILOT = {"copilot-pull-request-reviewer", "copilot-pull-request-reviewer[bot]"}
BOT_PR_AUTHORS = {"opentelemetrybot"}
AUTOMATED = ("copilot", "custom")
FINDING_GROUPS = (("Automated", AUTOMATED), ("Human", ("human",)))
NO_DENOMINATOR = object()
TEMPLATE = Path(__file__).with_name("scorecard.html")
COMMAND_ERRORS = (DataError, OSError, TimeoutExpired, sqlite3.Error, ValueError)


def identity(author, custom, outside):
    if not author:
        return "outside"
    login = author["login"].lower()
    if login in COPILOT:
        return "copilot"
    if login in custom:
        return "custom"
    if (login in outside or author["__typename"] != "User" or login.endswith("[bot]")):
        return "outside"
    return "human"


def is_bot_pr_author(login, kind):
    login = (login or "").lower()
    return kind == "Bot" or login.endswith("[bot]") or login in BOT_PR_AUTHORS


@dataclass(frozen=True)
class Rate:
    numerator: int
    denominator: int
    outcome: str = ""

    @property
    def value(self):
        return self.numerator / self.denominator


@dataclass
class Stats:
    prs: int = 0
    missing: bool = False
    unknown_roots: int = 0
    threads: Counter = field(default_factory=Counter)
    pending: Counter = field(default_factory=Counter)
    findings: Counter = field(default_factory=Counter)
    excluded_bot_prs: int = 0
    merge_seconds: list[float] = field(default_factory=list)

    def collect(self, pull, rows, custom, other_bots):
        self.prs += 1
        self.unknown_roots += pull["missing_roots"]
        by_id = {row["id"]: row for row in rows} if rows is not None else {}
        for thread in pull["threads"]:
            group = identity(thread["author"], custom, other_bots)
            self.threads[group] += 1
            row = by_id.get(thread["id"])
            if row is None:
                self.pending[group] += 1
                continue
            if row["kind"]:
                self.findings[group, row["kind"], row["status"]] += 1

    def __add__(self, other):
        return Stats(
            self.prs + other.prs, self.missing or other.missing,
            self.unknown_roots + other.unknown_roots,
            self.threads + other.threads, self.pending + other.pending,
            self.findings + other.findings,
            self.excluded_bot_prs + other.excluded_bot_prs,
            self.merge_seconds + other.merge_seconds,
        )

    @property
    def pr_count(self):
        return None if self.missing and not self.prs else self.prs

    @property
    def unassessed(self):
        return sum(self.pending[group] for _, groups in FINDING_GROUPS for group in groups)

    def finding(self, groups, status=None, kind=None):
        if self.pr_count is not None and not self.unknown_roots and not any(self.pending[g] for g in groups):
            return sum(
                n for (group, finding_kind, outcome), n in self.findings.items()
                if group in groups and status in (None, outcome) and kind in (None, finding_kind)
            )
        return None

    def findings_per_pr(self, groups, kind=None):
        findings = self.finding(groups, kind=kind)
        if findings is None:
            return None
        return Rate(findings, self.prs) if self.prs else NO_DENOMINATOR

    def outcome_pct(self, groups, status, kind=None):
        findings = self.finding(groups, kind=kind)
        if findings is None:
            return None
        return Rate(self.finding(groups, status, kind), findings, status) if findings else NO_DENOMINATOR


def missing_reason(value):
    if value is None:
        return "Not measured"
    if value is NO_DENOMINATOR:
        return "No denominator for this rate"
    return None


def rate_content(rate):
    text = f"{rate.value:.1%}" if rate.outcome else f"{rate.value:,.2f}"
    bar = f' style="--rate: {text}"' if rate.outcome == "unaddressed" else ""
    css = f' {rate.outcome}' if rate.outcome else ""
    return (f'<span class="rate{css}"{bar}><span class="rate-value">{text}</span>'
            f'<small class="rate-count">{rate.numerator:,} / {rate.denominator:,}</small></span>')


def cell(value):
    if reason := missing_reason(value):
        return f'<td class="missing" aria-label="{reason}" title="{reason}">&mdash;</td>'
    if isinstance(value, Rate):
        return f"<td>{rate_content(value)}</td>"
    text = f"{value:,}" if isinstance(value, int) else value
    return f"<td>{escape(text)}</td>"


def row(label, values, css="", note=""):
    annotation = f'<small class="row-note">{escape(note)}</small>' if note else ""
    return (f'<tr class="{css}"><th scope="row">{escape(label)}{annotation}</th>'
            + "".join(cell(value) for value in values) + "</tr>")


def table(caption, head, body):
    return (f'<div class="table-wrap" tabindex="0" role="region" aria-label="{escape(caption)}">'
            f"<table><caption>{escape(caption)}</caption><thead>{head}</thead><tbody>{body}</tbody>"
            "</table></div>")


def headers(labels):
    return "".join(f'<th scope="col">{escape(label)}</th>' for label in labels)


def findings_header(lead):
    return (
        "<tr>" + "".join(f'<th scope="col" rowspan="2">{escape(label)}</th>' for label in lead)
        + '<th scope="colgroup" colspan="3" class="automated">Automated findings</th>'
        + '<th scope="colgroup" colspan="3">Human findings</th></tr><tr>'
        + headers(["Findings per merged PR", "% unaddressed", "% unclear"] * 2) + "</tr>"
    )


def finding_rates(stats, kind=None):
    return [
        value for _, groups in FINDING_GROUPS
        for value in (stats.findings_per_pr(groups, kind), stats.outcome_pct(groups, "unaddressed", kind),
                      stats.outcome_pct(groups, "unclear", kind))
    ]


def main_table(label, repos, total):
    def values(stats):
        return [stats.pr_count, *finding_rates(stats)]

    def note(stats):
        parts = []
        if stats.missing:
            parts.append("Capture incomplete")
        if pending := stats.unassessed:
            parts.append(f"{pending:,} unassessed")
        if stats.unknown_roots:
            parts.append(f"{stats.unknown_roots:,} missing thread openers")
        return "; ".join(parts)

    body = "".join(row(repo.split("/")[1], values(stats), note=note(stats)) for repo, stats in repos.items())
    head = findings_header(["Repository", "Merged PRs"])
    return table(label, head, body + row("All repositories", values(total), "total", note(total)))


def types_table(label, total):
    body = "".join(row(kind.capitalize(), finding_rates(total, kind)) for kind in KINDS)
    return table(label, findings_header(["Finding type"]), body)


def timing_rows(label, repos, total):
    def timing_row(name, stats, css=""):
        durations = stats.merge_seconds
        notes = []
        if stats.missing:
            notes.append("Capture incomplete")
        if len(durations) < stats.prs:
            notes.append("Timing incomplete")
        if len(durations) > 1:
            cuts = quantiles(durations, n=100, method="inclusive")
            percentiles = [days_display(cuts[p - 1]) for p in (50, 75, 90)]
        elif durations:
            percentiles = [days_display(durations[0])] * 3
        else:
            percentiles = [None] * 3
        return row(name, [stats.pr_count, *percentiles], css, "; ".join(notes))

    head = "<tr>" + headers(["Repository", "Merged PRs", "Median (p50)", "p75", "p90"]) + "</tr>"
    body = "".join(timing_row(repo.split("/")[1], stats) for repo, stats in repos.items())
    return table(label, head, body + timing_row("All repositories", total, "total"))


def days_display(seconds):
    return f"{seconds / 86_400:,.1f} days"


def timing_headline(stats):
    durations = stats.merge_seconds
    value = days_display(median(durations)) if durations else "Not measured"
    count = stats.pr_count
    merged_prs = ("Merged PR count not measured" if count is None
                  else f"{count:,} merged {'PR' if count == 1 else 'PRs'}")
    return (f'<p class="timing-headline"><strong>Median time to merge</strong> <span>{value}</span> '
            f'<small>{merged_prs}'
            f'{"; capture incomplete" if stats.missing else ""}'
            f'{"; timing incomplete" if len(durations) < stats.prs else ""}</small></p>')


@dataclass
class Cohort:
    name: str
    dates: tuple[date, date]
    repos: dict

    @property
    def total(self):
        return sum(self.repos.values(), start=Stats())

    @property
    def label(self):
        return f"{self.name} | {self.dates[0]} to {self.dates[1]} UTC"


def headline(stats):
    def metric(label, value):
        reason = missing_reason(value)
        content = f'<span class="missing">{reason}</span>' if reason else rate_content(value)
        return f"<div><dt>{label}</dt><dd>{content}</dd></div>"

    groups = "".join(
        f'<div class="headline-group"><h3>{name} findings</h3><dl>'
        + metric("Findings per merged PR", stats.findings_per_pr(group))
        + metric("% unaddressed", stats.outcome_pct(group, "unaddressed"))
        + "</dl></div>"
        for name, group in FINDING_GROUPS
    )
    return f'<div class="headline">{groups}</div>'


def findings_summary(window, total):
    if any(total.findings_per_pr(groups) is not None for _, groups in FINDING_GROUPS):
        return total, 0
    measured = [stats for stats in window.repos.values() if stats.prs and
                all(stats.findings_per_pr(groups) is not None for _, groups in FINDING_GROUPS)]
    if not measured:
        return total, 0
    return sum(measured, start=Stats()), len(measured)


def render(cohorts, custom, model, version=ASSESSMENT_VERSION, reasoning_effort=DEFAULT_REASONING_EFFORT):
    overview, main, types, timings = [], [], [], []
    for window in cohorts:
        stats = window.total
        counted = sum(stats.threads.values())
        assessed = counted - sum(stats.pending.values())
        note = f"{assessed:,} of {counted:,} visible thread openers assessed."
        if stats.excluded_bot_prs:
            note += f" Excluded {stats.excluded_bot_prs:,} bot-authored PRs."
        coverage = []
        if stats.missing:
            coverage.append("Capture is incomplete. Counts and rates are provisional and use archived PRs only.")
        if stats.unknown_roots or stats.unassessed:
            coverage.append("Rates that depend on unassessed or missing thread openers are not shown.")
        coverage_note = f'<p class="coverage">{" ".join(coverage)}</p>' if coverage else ""
        findings, assessed_repos = findings_summary(window, stats)
        headline_scope, types_caption = "", window.label
        if assessed_repos:
            scope = f"{assessed_repos} of {len(window.repos)} repositories"
            headline_scope = (f'<p class="headline-scope">Headline rates cover {scope}, '
                              "using only those with archived PRs and complete assessments.</p>")
            types_caption += f" | {findings.prs:,} merged PRs across {scope} with complete assessments"
        overview.append(f'<article class="window"><h2>{escape(window.name)}</h2>'
                        f'<p class="dates">{window.dates[0]} to {window.dates[1]} UTC</p>'
                        f"{coverage_note}{headline_scope}{headline(findings)}{timing_headline(stats)}"
                        f"<p>{escape(note)}</p></article>")
        main.append(main_table(window.label, window.repos, stats))
        types.append(types_table(types_caption, findings))
        timings.append(timing_rows(window.label, window.repos, stats))
    custom_reviewers = f" Configured: {', '.join(sorted(custom))}." if custom else ""
    legacy = version == digest("compact-sdk-v1") or version == (
        "83e7535cd200dbab733f135ce12a8fa9b38789b2f77e6530066135a137b19313")
    repo_count = len({repo for window in cohorts for repo in window.repos})
    return Template(TEMPLATE.read_text(encoding="utf-8")).substitute(
        overview="".join(overview), main="".join(main), types="".join(types), timings="".join(timings),
        repositories=f"{repo_count} public {'repository' if repo_count == 1 else 'repositories'}",
        custom_reviewers=escape(custom_reviewers),
        addressed_definition=("A matching change was made after the review opened and survived at merge."
                              if legacy else "The issue is gone at merge."),
        unaddressed_definition=("No matching change was found." if legacy else "The issue is still present at merge."),
        model=escape(f"{model} ({reasoning_effort} reasoning effort)"),
    )


def write_atomic(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(text, encoding="utf-8")
    temp.replace(path)


def assess_cached(archive, bundle, model, pull, audit, allow_model, version, *, git_root=None):
    repo, number = pull["repo"], pull["number"]

    def fingerprint(inputs):
        return judgment_key(model.model, inputs, model.reasoning_effort)

    bundles = [bundle]
    if not allow_model and version != ASSESSMENT_VERSION:
        bundles.extend(archive.get_prepared(pull, v) for v in reversed(SUPPORTED_PREPARED_VERSIONS[:-1]))
    for saved_bundle in bundles:
        key = fingerprint([version, pull, digest(saved_bundle)])
        saved = (archive.get_judgment(repo, number, "published", key)
                 or archive.get_judgment(repo, number, "complete", key))
        if saved is not None:
            return saved["threads"], False
    if not allow_model or bundle is None:
        return None, False
    key = fingerprint([version, pull, digest(bundle)])

    def checkpoint(stage, inputs, evaluate):
        stage_key = fingerprint(inputs)
        result = archive.get_judgment(repo, number, stage, stage_key)
        if result is None:
            result = evaluate()
            archive.put_judgment(repo, number, stage, stage_key, result)
        return result

    if git_root is None:
        rows = assess(bundle, model, pull, checkpoint)
    else:
        rows = assess(bundle, model, pull, checkpoint, retrieval=OutcomeEvidence(git_root, pull))
    result = {
        "input_digest": key, "assessment_version": version,
        "model": model.model, "reasoning_effort": model.reasoning_effort,
        "merge": pull["merge"], "evidence_digest": digest(bundle), "threads": rows,
    }
    write_atomic(audit / repo / str(number) / f"{key}.json", json.dumps(result, indent=2) + "\n")
    archive.put_judgment(repo, number, "complete", key, result)
    return rows, True


def log(message):
    print(message, file=sys.stderr, flush=True)


def load_shared(archive, *, retrieval_git_root=None):
    shared = SharedData(archive, REPOS, retrieval_git_root=retrieval_git_root, ref=SNAPSHOT_REF, snapshot=True)
    shared.load()
    log(f"Loaded shared data from {shared.tip[:12]}")
    return shared


def publish_shared(shared, *, include_assessments=False):
    commit = shared.publish(include_assessments=include_assessments)
    log(f"Published shared data as {commit[:12]}" if commit else "Shared data is already up to date")


def assessment_order(archive, dates, prioritize_oversized, repos=REPOS):
    remaining = []
    for repo in repos:
        for pull in archive.pulls(repo, *dates):
            if not prioritize_oversized:
                yield pull, None
                continue
            bundle = archive.get_prepared(pull) if pull["threads"] else None
            if bundle is not None and needs_classification_batches(pull):
                log(f"Prioritizing split classification for {repo}#{pull['number']}")
                yield pull, bundle
            else:
                remaining.append(pull)
    for pull in remaining:
        yield pull, None


def collect(archive, args, model, windows, repo_names, shared=None):
    cohorts, failures, runs = [], 0, 0
    completed = 0
    authors_by_window = [
        {repo: archive.pull_authors(repo, *dates) for repo in repo_names} for _, dates in windows
    ]
    for (label, dates), authors in zip(windows, authors_by_window, strict=True):
        repos = {repo: Stats(missing=bool(archive.pending_days(repo, *dates))) for repo in repo_names}
        timings = {repo: archive.pull_merge_timings(repo, *dates) for repo in repo_names}
        for pull, bundle in assessment_order(archive, dates, args.prioritize_oversized, repo_names):
            repo, number = pull["repo"], pull["number"]
            author = authors[repo].get(number)
            if author is None:
                raise DataError(f"{repo}#{number}: PR was added during report generation. "
                                "Run `python scorecard.py capture-authors` and retry.")
            if is_bot_pr_author(*author):
                repos[repo].excluded_bot_prs += 1
                continue
            if timing := timings[repo].get(number):
                created, ready = timing
                ref = f"{repo}#{number}"
                try:
                    merge_timing({"created_at": created, "ready_at": ready}, pull["merged"], ref)
                    if ready is not None:
                        elapsed = timing_instant(pull["merged"], ref) - timing_instant(ready, ref)
                        repos[repo].merge_seconds.append(elapsed.total_seconds())
                except DataError as error:
                    log(f"{ref}: merge timing unavailable: {error}")
            rows = []
            if pull["threads"]:
                allow = args.command == "assess" and (args.max_prs is None or runs < args.max_prs)
                try:
                    rows, ran = assess_cached(
                        archive, bundle if bundle is not None else archive.get_prepared(pull),
                        model, pull, args.audit_dir, allow, args.assessment_version,
                        git_root=args.archive.parent / "git" if args.retrieve_evidence else None,
                    )
                except AssessmentSetupError:
                    raise
                except (DataError, OSError, TimeoutExpired, ValueError) as error:
                    log(f"{repo}#{pull['number']}: assessment failed: {error}")
                    rows = None
                    failures += 1
                    runs += 1
                else:
                    if ran:
                        runs += 1
                        completed += 1
                        if shared is not None and completed % 25 == 0:
                            publish_shared(shared, include_assessments=True)
            repos[repo].collect(pull, rows, args.custom_reviewer, args.other_bot)
        for repo, stats in repos.items():
            log(f"{label} | {repo}: {sum(stats.threads.values())} visible threads, "
                f"{sum(stats.pending.values())} not assessed, {stats.unknown_roots} missing roots, "
                f"{stats.excluded_bot_prs} bot-authored PRs excluded")
        cohorts.append(Cohort(label, dates, repos))
    return cohorts, failures


def load_data(args, _parser):
    try:
        with closing(Archive(args.archive)) as archive:
            load_shared(archive)
    except COMMAND_ERRORS as error:
        log(f"Load stopped: {error}")
        return 1
    return 0


def capture_window(args, parser):
    today = datetime.now(timezone.utc).date()
    if args.max_rate_wait < 0:
        parser.error("--max-rate-wait cannot be negative")
    through = args.through or today
    if not args.start <= through <= today:
        parser.error("capture dates must be in order and no later than today UTC")
    return through, today


def capture_authors_data(args, parser):
    through, _ = capture_window(args, parser)
    github = GitHub(args.max_rate_wait)
    try:
        if not args.archive.is_file():
            raise DataError("Archive does not exist. Run `python scorecard.py load-data` or `capture` first.")
        with closing(Archive(args.archive)) as archive:
            for repo in REPOS:
                checked = archive.capture_authors(github, repo, args.start, through)
                log(f"{repo}: checked authors for {checked:,} archived PRs")
    except COMMAND_ERRORS as error:
        log(f"Author capture stopped: {error}")
        return 1
    return 0


def capture_data(args, parser):
    through, today = capture_window(args, parser)
    if args.max_evidence_prs is not None and (not args.with_evidence or args.max_evidence_prs < 1):
        parser.error("--max-evidence-prs requires --with-evidence and a positive limit")
    github = GitHub(args.max_rate_wait)
    try:
        with closing(Archive(args.archive)) as archive:
            shared = load_shared(archive) if args.publish_data else None
            for repo in REPOS:
                log(f"Capturing {repo}")
                added = archive.capture(github, repo, args.start, through, today)
                log(f"{repo}: {added} new PRs archived")
                checked = archive.capture_authors(github, repo, args.start, through)
                log(f"{repo}: checked authors for {checked:,} archived PRs")
            if args.with_evidence:
                total_prepared = 0
                git_root = args.archive.parent / "git"
                for repo in REPOS:
                    count = 0
                    for pull in archive.pulls(repo, args.start, through):
                        if not pull["threads"] or archive.get_prepared(pull) is not None:
                            continue
                        if args.max_evidence_prs is not None and total_prepared >= args.max_evidence_prs:
                            break
                        bundle = prepare(pull, Evidence(git_root, pull))
                        archive.put_prepared(repo, pull["number"], bundle)
                        count += 1
                        total_prepared += 1
                        log(f"Prepared {repo}#{pull['number']}")
                    log(f"{repo}: {count} new evidence bundles")
            if args.publish_data:
                publish_shared(shared)
    except COMMAND_ERRORS as error:
        log(f"Capture stopped: {error}")
        return 1
    print(args.archive)
    return 0


def build_report(args, parser):
    assessing = args.command == "assess"
    args.custom_reviewer = {login.lower() for login in args.custom_reviewer}
    args.other_bot = {login.lower() for login in args.other_bot}
    if assessing:
        args.audit_dir = args.audit_dir or args.archive.parent / "audit"
        if args.retrieve_evidence:
            if args.publish_data and args.max_prs != 0:
                parser.error("--retrieve-evidence with --publish-data requires --max-prs 0")
            args.assessment_version = RETRIEVAL_VERSION
    if args.custom_reviewer & (args.other_bot | COPILOT):
        parser.error("a custom reviewer login cannot also be another bot or GitHub Copilot")
    if assessing and args.max_prs is not None and args.max_prs < 0:
        parser.error("--max-prs cannot be negative")
    dates = tuple(args.window)
    if dates[0] > dates[1]:
        parser.error("window dates must be in order")
    windows = [("Baseline" if dates == BASELINE else "Selected window", dates)]
    if args.follow_up:
        start, end = args.follow_up
        if start <= dates[1] or start > end:
            parser.error("follow-up must start after the first window and end on or after its start date")
        if not args.custom_reviewer:
            parser.error("--follow-up requires --custom-reviewer LOGIN")
        windows.append(("Follow-up", (start, end)))
    repos = tuple(repo for repo in REPOS if not args.repo or repo in args.repo)
    try:
        publish = assessing and args.publish_data
        if not args.archive.is_file() and not publish:
            raise DataError("Archive does not exist. Run `python scorecard.py load-data` or `capture` first.")
        with closing(Archive(args.archive, readonly=not assessing)) as archive, \
                closing(Copilot(args.model, args.reasoning_effort)) as model:
            git_root = args.archive.parent / "git" if publish and args.retrieve_evidence else None
            shared = load_shared(archive, retrieval_git_root=git_root) if publish else None
            cohorts, failures = collect(archive, args, model, windows, repos, shared)
            if shared is not None:
                publish_shared(shared, include_assessments=True)
    except COMMAND_ERRORS as error:
        log(str(error))
        return 1
    write_atomic(args.output, render(cohorts, args.custom_reviewer, args.model, args.assessment_version,
                                     args.reasoning_effort))
    print(args.output)
    return int(bool(failures))


def draft_instructions(args, parser):
    dates = tuple(args.window)
    if dates[0] > dates[1]:
        parser.error("window dates must be in order")
    if args.editor_reasoning_effort and not args.editor_model:
        parser.error("--editor-reasoning-effort requires --editor-model")
    editor_effort = args.editor_reasoning_effort or DEFAULT_REASONING_EFFORT
    repo = args.repo.lower()
    try:
        if not args.archive.is_file():
            raise DataError("Archive does not exist. Run `python scorecard.py load-data` first.")
        with closing(Archive(args.archive, readonly=True)) as archive:
            logins = BOT_PR_AUTHORS | {login.lower() for login in args.other_bot}
            examples, addressed = archived_examples(
                archive, repo, dates, human=lambda author: identity(author, set(), logins) == "human",
                bot_pr=is_bot_pr_author,
            )
        log(f"{repo}: {len(examples)} paired examples from {addressed} addressed human findings")
        if len(examples) < args.min_examples:
            raise DataError(f"Need at least {args.min_examples} paired review examples; only {len(examples)} available")
        if not examples:
            log("No paired review examples; drafting from repository guides only")
        with TemporaryDirectory() as work:
            root = Path(work)
            snapshot = Snapshot.fetch(repo, root / "repository.git")
            with closing(Copilot(args.model, args.reasoning_effort)) as model:
                if args.editor_model:
                    with closing(Copilot(args.editor_model, editor_effort)) as editor:
                        rules, sources = draft_rules(model, snapshot, repo, examples, editor=editor)
                else:
                    rules, sources = draft_rules(model, snapshot, repo, examples)
            files, evidence = render_rules(rules, sources)
            patch, removed = snapshot.replacement(files, root / "draft.index")
        args.output_dir.mkdir(parents=True, exist_ok=True)
        patch_path = args.output_dir / "copilot-instructions.patch"
        patch_path.write_bytes(patch)
        write_atomic(args.output_dir / "copilot-instructions.evidence.json", json.dumps({
            "repo": repo, "revision": snapshot.revision, "window": [day.isoformat() for day in dates],
            "model": args.model, "reasoning_effort": args.reasoning_effort,
            **({"editor_model": args.editor_model, "editor_reasoning_effort": editor_effort}
               if args.editor_model else {}),
            "paired_examples": len(examples), "addressed_findings": addressed,
            "removed_files": removed, "draft_files": sorted(files), "rules": evidence,
        }, ensure_ascii=False, indent=2) + "\n")
    except COMMAND_ERRORS as error:
        log(f"Instruction draft stopped: {error}")
        return 1
    print(patch_path)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build an aggregate scorecard from public GitHub PRs.")
    sub = parser.add_subparsers(dest="command", required=True)
    capture = sub.add_parser("capture", help="update the local archive without using a model")
    authors = sub.add_parser("capture-authors", help="check authors of archived PRs without recapturing them")
    load = sub.add_parser("load-data", help="download the shared data branch without calling the GitHub API")
    assessment = sub.add_parser("assess", help="assess archived threads and render a report",
                                description="Use --retrieve-evidence to match the default report. Without it, "
                                            "assessments use only prepared evidence.")
    report = sub.add_parser("report", help="render a report offline from the archive")
    draft = sub.add_parser("draft-instructions", help="draft a local replacement for a public repo's Copilot rules")
    for command in (capture, authors, load, assessment, report, draft):
        command.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)

    load.set_defaults(run=load_data)
    capture.set_defaults(run=capture_data)
    authors.set_defaults(run=capture_authors_data)
    for command in (capture, authors):
        command.add_argument("--from", dest="start", type=date.fromisoformat, default=BASELINE[0])
        command.add_argument("--through", type=date.fromisoformat, help="last UTC day to capture (default: today)")
        command.add_argument("--max-rate-wait", type=int, default=60, metavar="SECONDS")
    capture.add_argument("--with-evidence", action="store_true", help="prepare PR evidence for offline assessment")
    capture.add_argument("--max-evidence-prs", type=int, help="cap new PR evidence bundles in this capture")
    capture.add_argument("--publish-data", action="store_true",
                         help="load and publish captured inputs on the public data branch")

    for command in (assessment, report):
        command.set_defaults(run=build_report, audit_dir=None,
                             prioritize_oversized=False, retrieve_evidence=False, repo=None)
        command.add_argument("--window", nargs=2, type=date.fromisoformat, metavar=("START", "END"),
                             default=BASELINE, help="inclusive UTC merge dates (default: baseline)")
        command.add_argument("--follow-up", nargs=2, type=date.fromisoformat, metavar=("START", "END"))
        command.add_argument("--custom-reviewer", action="append", default=[], metavar="LOGIN")
        command.add_argument("--other-bot", action="append", default=[], metavar="LOGIN")
        command.add_argument("--model", default="gpt-6.2-sol", help="GitHub Copilot CLI model ID (default: %(default)s)")
        command.add_argument("--reasoning-effort", choices=REASONING_EFFORTS, default=DEFAULT_REASONING_EFFORT,
                             help="reasoning effort for the assessment (default: %(default)s)")
        command.add_argument("--output", type=Path, default=Path(".scorecard", "scorecard.html"))
    assessment.set_defaults(assessment_version=ASSESSMENT_VERSION)
    assessment.add_argument("--publish-data", action="store_true",
                            help="publish assessments (with --retrieve-evidence, requires --max-prs 0)")
    assessment.add_argument("--audit-dir", type=Path)
    assessment.add_argument("--max-prs", type=int, help="limit new assessments to this many PRs")
    assessment.add_argument("--prioritize-oversized", action="store_true",
                            help="assess PRs that need classification batches first in each window")
    assessment.add_argument("--retrieve-evidence", action="store_true",
                            help="retry unclear outcomes with bounded read-only Git lookups")
    report.add_argument("--assessment-version", default=RETRIEVAL_VERSION, help=argparse.SUPPRESS)
    report.add_argument("--repo", action="append", choices=REPOS, metavar="OWNER/NAME",
                        help="include only this repository (repeat to include more)")

    draft.set_defaults(run=draft_instructions)
    draft.add_argument("--repo", required=True, metavar="OWNER/NAME", help="public github.com repository")
    draft.add_argument("--window", nargs=2, type=date.fromisoformat, metavar=("START", "END"),
                       default=BASELINE, help="inclusive UTC merge dates (default: baseline)")
    draft.add_argument("--model", default="gpt-6.2-sol", help="GitHub Copilot CLI model ID (default: %(default)s)")
    draft.add_argument("--reasoning-effort", choices=(*REASONING_EFFORTS, "max"), default=DEFAULT_REASONING_EFFORT)
    draft.add_argument("--editor-model", help="optional second Copilot model to propose edits for the draft model to judge")
    draft.add_argument("--editor-reasoning-effort", choices=(*REASONING_EFFORTS, "max"),
                       help="reasoning effort for the second model (default: high)")
    draft.add_argument("--other-bot", action="append", default=[], metavar="LOGIN",
                       help="also exclude review comments from this automation account")
    draft.add_argument("--output-dir", type=Path, default=Path(".scorecard", "instructions"))
    draft.add_argument("--min-examples", type=int, default=0, help="minimum paired review examples required")

    args = parser.parse_args(argv)
    return args.run(args, parser)


if __name__ == "__main__":
    sys.exit(main())
