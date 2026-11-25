import gzip
import json
import sqlite3
from datetime import timedelta, timezone
from pathlib import Path

from github_data import DataError, instant
from prepared import PREPARED_VERSION, digest


DEFAULT_ARCHIVE = Path.home() / ".otel-pr-review-metrics" / "archive.sqlite3"
DAY = timedelta(days=1)


def days(start, end):
    return (start + i * DAY for i in range((end - start).days + 1))


def windows(dates):
    start = previous = None
    for day in dates:
        if previous is None:
            start = day
        elif day != previous + DAY or (day - start).days == 46:
            yield start, previous
            start = day
        previous = day
    if previous is not None:
        yield start, previous


def encode(value):
    return gzip.compress(json.dumps(value, ensure_ascii=False).encode(), compresslevel=3)


def decode(value):
    return json.loads(gzip.decompress(value))


class Archive:
    def __init__(self, path, readonly=False):
        if readonly:
            self.db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS pulls (
                repo TEXT, number INTEGER, merged_day TEXT, merged TEXT, payload BLOB,
                PRIMARY KEY (repo, number)
            );
            CREATE INDEX IF NOT EXISTS pulls_by_day ON pulls(repo, merged_day, number);
            CREATE TABLE IF NOT EXISTS pull_line_counts (
                repo TEXT, number INTEGER, additions INTEGER NOT NULL, deletions INTEGER NOT NULL,
                PRIMARY KEY (repo, number)
            );
            CREATE TABLE IF NOT EXISTS pull_merge_timings (
                repo TEXT, number INTEGER, created_at TEXT NOT NULL, ready_at TEXT,
                PRIMARY KEY (repo, number)
            );
            CREATE TABLE IF NOT EXISTS pull_authors (
                repo TEXT, number INTEGER, login TEXT, kind TEXT,
                PRIMARY KEY (repo, number)
            );
            CREATE TABLE IF NOT EXISTS complete_days (
                repo TEXT, day TEXT, PRIMARY KEY (repo, day)
            );
            CREATE TABLE IF NOT EXISTS prepared_evidence (
                repo TEXT, number INTEGER, version INTEGER, input_digest TEXT, payload BLOB NOT NULL,
                PRIMARY KEY (repo, number, version)
            );
            CREATE TABLE IF NOT EXISTS judgments (
                repo TEXT, number INTEGER, stage TEXT, fingerprint TEXT, payload BLOB,
                PRIMARY KEY (repo, number, stage, fingerprint)
            );
        """)

    def close(self):
        self.db.close()

    def pending_days(self, repo, start, end):
        complete = {row[0] for row in self.db.execute(
            "SELECT day FROM complete_days WHERE repo = ? AND day BETWEEN ? AND ?",
            (repo, start.isoformat(), end.isoformat()),
        )}
        return [day for day in days(start, end) if day.isoformat() not in complete]

    def pulls(self, repo, start, end):
        for (payload,) in self.db.execute(
            "SELECT payload FROM pulls WHERE repo = ? AND merged_day BETWEEN ? AND ? ORDER BY number",
            (repo, start.isoformat(), end.isoformat()),
        ):
            yield decode(payload)

    def pull_merge_timings(self, repo, start, end):
        if not self.db.execute("SELECT 1 FROM sqlite_master WHERE name = 'pull_merge_timings'").fetchone():
            return {}
        return {
            number: (created, ready)
            for number, created, ready in self.db.execute("""
                SELECT p.number, t.created_at, t.ready_at
                FROM pulls p JOIN pull_merge_timings t USING (repo, number)
                WHERE p.repo = ? AND p.merged_day BETWEEN ? AND ?
            """, (repo, start.isoformat(), end.isoformat()))
        }

    def pull_authors(self, repo, start, end):
        hint = "Run `python scorecard.py capture-authors` for this report window."
        if not self.db.execute("SELECT 1 FROM sqlite_master WHERE name = 'pull_authors'").fetchone():
            raise DataError(f"The archive has no PR author metadata. {hint}")
        authors = {}
        missing = 0
        for number, known_number, login, kind in self.db.execute("""
            SELECT p.number, a.number, a.login, a.kind
            FROM pulls p LEFT JOIN pull_authors a USING (repo, number)
            WHERE p.repo = ? AND p.merged_day BETWEEN ? AND ?
        """, (repo, start.isoformat(), end.isoformat())):
            if known_number is None:
                missing += 1
            else:
                authors[number] = (login, kind)
        if missing:
            raise DataError(f"{repo}: PR authors have not been checked for {missing:,} PRs. {hint}")
        return authors

    def capture_authors(self, github, repo, start, end):
        pending = dict(self.db.execute("""
            SELECT p.number, p.merged FROM pulls p
            LEFT JOIN pull_authors a USING (repo, number)
            WHERE p.repo = ? AND p.merged_day BETWEEN ? AND ? AND a.number IS NULL
        """, (repo, start.isoformat(), end.isoformat())))
        remaining = set(pending)
        for number, author in github.pull_authors(repo, pending):
            if number not in remaining:
                raise DataError(f"Unexpected PR author record for {repo}#{number}")
            with self.db:
                self.db.execute("INSERT OR IGNORE INTO pull_authors VALUES (?, ?, ?, ?)",
                                (repo, number, author["login"] if author else None,
                                 author["__typename"] if author else None))
            remaining.remove(number)
        if remaining:
            raise DataError(f"{repo}: GitHub did not return authors for {len(remaining):,} PRs")
        return len(pending)

    def capture(self, github, repo, start, end, today, *, accept_pull=None):
        added = 0
        for first, last in windows(self.pending_days(repo, start, end)):
            expected = github.merged(repo, first, last)
            saved = dict(self.db.execute(
                "SELECT number, merged FROM pulls WHERE repo = ? AND merged_day BETWEEN ? AND ?",
                (repo, first.isoformat(), last.isoformat()),
            ))
            if any(n not in expected or instant(merged) != instant(expected[n])
                   for n, merged in saved.items()):
                raise DataError(f"Search disagrees with archived merges in {repo} for {first} to {last}; retry later")
            pending = {n: merged for n, merged in expected.items() if n not in saved}
            remaining = set(pending)
            incomplete = set()
            for pull, (additions, deletions), timing in github.pulls(repo, pending):
                n, merged = pull["number"], pull["merged"]
                if n not in remaining or instant(merged) != instant(pending[n]):
                    raise DataError(f"Unexpected archive record for {repo}#{n}")
                remaining.remove(n)
                day = instant(merged).astimezone(timezone.utc).date()
                if accept_pull is not None and not accept_pull(pull, day):
                    incomplete.add(day)
                    continue
                with self.db:
                    self.db.execute("INSERT OR IGNORE INTO pulls VALUES (?, ?, ?, ?, ?)",
                                    (repo, n, day.isoformat(), merged, encode(pull)))
                    self.db.execute("INSERT OR IGNORE INTO pull_line_counts VALUES (?, ?, ?, ?)",
                                    (repo, n, additions, deletions))
                    if timing is not None:
                        self.db.execute("INSERT OR IGNORE INTO pull_merge_timings VALUES (?, ?, ?, ?)",
                                        (repo, n, *timing))
                added += 1
            if remaining:
                raise DataError(f"Missing pull request records in {repo} for {first} to {last}")
            # Search indexing can lag. Recheck today and yesterday on the next capture.
            with self.db:
                self.db.executemany(
                    "INSERT OR IGNORE INTO complete_days VALUES (?, ?)",
                    ((repo, day.isoformat()) for day in days(first, min(last, today - 2 * DAY))
                     if day not in incomplete),
                )
        return added

    def get_prepared(self, pull, version=PREPARED_VERSION):
        row = self.db.execute(
            "SELECT input_digest, payload FROM prepared_evidence WHERE repo = ? AND number = ? AND version = ?",
            (pull["repo"], pull["number"], version),
        ).fetchone()
        return decode(row[1]) if row and row[0] == digest(pull) else None

    def put_prepared(self, repo, number, bundle):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO prepared_evidence VALUES (?, ?, ?, ?, ?)",
                            (repo, number, bundle["version"], bundle["input"], encode(bundle)))

    def get_judgment(self, repo, number, stage, fingerprint):
        row = self.db.execute(
            "SELECT payload FROM judgments WHERE repo = ? AND number = ? AND stage = ? AND fingerprint = ?",
            (repo, number, stage, fingerprint),
        ).fetchone()
        return decode(row[0]) if row else None

    def put_judgment(self, repo, number, stage, fingerprint, value):
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO judgments VALUES (?, ?, ?, ?, ?)",
                            (repo, number, stage, fingerprint, encode(value)))
