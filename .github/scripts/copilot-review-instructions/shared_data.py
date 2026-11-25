import gzip
import json
import re
from datetime import date, timezone
from hashlib import sha256
from io import BytesIO
from uuid import uuid4

from archive import decode, encode
from assessment import (CHANGE_TIMINGS, KINDS, LOCAL_ASSESSMENT_VERSIONS, OUTCOMES, REASONING_EFFORTS,
                        RETRIEVAL_VERSION)
from github_data import DataError, instant, line_counts, merge_timing, run_command
from git_evidence import object_id
from outcome_evidence import LOOKUP_ROUNDS, OutcomeEvidence, valid_request
from prepared import SUPPORTED_PREPARED_VERSIONS, digest, json_bytes


SHARED_REMOTE = "https://github.com/open-telemetry/shared-workflows.git"
DATA_REF = "refs/heads/data"
SNAPSHOT_REF = "refs/heads/otelbot/copilot-review-instructions-data"
MANIFEST = "schema.json"
COLLECTION_RETRIES = "collection-retries.json"
FORMAT = {"format": 1}
HASH = r"[0-9a-f]{64}"
ASSESSMENT_FILE = rf"\.assessment\.({HASH})\.json"
EVIDENCE_FILE = r"\.evidence(?:\.v([2-9]|[1-9]\d+))?\.json\.gz"
PR_FILE = re.compile(rf"(\d{{4}}-\d{{2}})/([1-9]\d*)(\.json|{ASSESSMENT_FILE}|{EVIDENCE_FILE})")
MAX_RECORD_BYTES = 16_000_000
PROBABLE_SECRET = re.compile(
    rb"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----[\s+>]*[A-Za-z0-9+/]{24,}"
    rb"|\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{70,}|"
    rb"(?:AKIA|ASIA)[A-Z0-9]{16}|xox[baprs]-[0-9A-Za-z-]{16,}|AIza[0-9A-Za-z_-]{35})\b"
    rb"|(?i:\bbearer )[A-Za-z0-9._~-]{32,}"
)
URL_AUTH = re.compile(rb"""https?://[^\s:/"\\@]+:([^\s/"\\@]+)@""")
PLACEHOLDER = re.compile(rb"(?i)(?:pass(?:word)?|token|secret|xxx+|\$\{?[\w.-]+\}?|\*+)")


def safe_payload(value):
    def strings(value):
        if isinstance(value, str):
            yield value.encode("utf-8")
        elif isinstance(value, dict):
            for child in value.values():
                yield from strings(child)
        elif isinstance(value, list):
            for child in value:
                yield from strings(child)

    def safe(text):
        return not PROBABLE_SECRET.search(text) and all(
            PLACEHOLDER.fullmatch(match.group(1)) for match in URL_AUTH.finditer(text)
        )

    return all(map(safe, strings(value)))


class RecordRejected(DataError):
    pass


def check_record(path, blob, value):
    if len(blob) > MAX_RECORD_BYTES:
        raise RecordRejected(f"Shared record is too large: {path}")
    if value is not None and not safe_payload(value):
        raise RecordRejected(f"Shared publication withheld: {path} did not pass the public-data check.")


def judgment_key(model, inputs, reasoning_effort):
    return sha256(json.dumps([model, reasoning_effort, inputs], sort_keys=True).encode()).hexdigest()


def _replay_citations(thread, row, lookup):
    first = row.get("first_pass")
    lookups = row.get("lookups")
    if (not isinstance(first, dict) or first.get("status") != "unclear"
            or not isinstance(lookups, list) or not 1 <= len(lookups) <= LOOKUP_ROUNDS):
        return set()
    urls = set()
    for i, entry in enumerate(lookups):
        if not isinstance(entry, dict):
            return set()
        ids = entry.get("records")
        if not isinstance(ids, list) or any(not isinstance(ident, str) for ident in ids):
            return set()
        if not ids:
            continue
        request = entry.get("request")
        if not valid_request(request):
            return set()
        records, _ = lookup(thread, request, str(i))
        if not set(ids) <= records.keys():
            return set()
        urls.update(records[ident]["url"] for ident in ids)
    return urls


def public_assessment(pull, result, key, *, bundle=None, lookup=None):
    def invalid():
        raise DataError(f"Invalid shared assessment for {pull['repo']}#{pull['number']}")

    version, evidence, model = (result.get(k) for k in ("assessment_version", "evidence_digest", "model"))
    if not all(isinstance(value, str) and re.fullmatch(HASH, value) for value in (version, evidence)):
        invalid()
    if not isinstance(model, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}", model):
        invalid()
    effort = result.get("reasoning_effort")
    if effort not in REASONING_EFFORTS:
        invalid()
    if key != judgment_key(model, [version, pull, evidence], effort):
        invalid()

    threads, rows = pull["threads"], result.get("threads")
    if not isinstance(rows, list) or not threads or len(rows) != len(threads):
        invalid()
    originals, decisions = {}, []
    base = f"https://github.com/{pull['repo']}/"
    for thread, row in zip(threads, rows):
        if not isinstance(row, dict) or row.get("id") != thread["id"]:
            invalid()
        status, kind = row.get("status"), row.get("kind")
        if not (kind in KINDS and status in OUTCOMES[:3] or kind is None and status in OUTCOMES[3:]):
            invalid()
        decision = {"id": thread["id"], "kind": kind, "status": status}
        if status == "duplicate":
            original = row.get("original")
            if (not isinstance(original, str) or original not in originals
                    or instant(originals[original]) >= instant(thread["opened"])):
                invalid()
            decision["original"] = original
        elif kind:
            originals[thread["id"]] = thread["opened"]
            if "change_timing" in row or "regressed" in row:
                timing, regressed = row.get("change_timing"), row.get("regressed")
                if (timing not in CHANGE_TIMINGS or type(regressed) is not bool
                        or (regressed and (status != "unaddressed" or timing == "unknown"))
                        or (timing != "unknown" and status != "addressed" and not regressed)):
                    invalid()
                decision.update(change_timing=timing, regressed=regressed)
            links = row.get("evidence")
            if not isinstance(links, list) or any(
                not isinstance(link, str)
                or not link.startswith((base + "pull/", base + "blob/", base + "commit/", base + "compare/"))
                or any(ch.isspace() for ch in link) or "?" in link
                for link in links
            ):
                invalid()
            if bundle is not None:
                refs = bundle["threads"][thread["id"]]["records"]
                allowed = {bundle["records"][index]["url"] for index in refs.values()}
                extra = set(links) - allowed
                if extra and (version != RETRIEVAL_VERSION or lookup is None
                              or not extra <= _replay_citations(thread, row, lookup)):
                    invalid()
            decision["evidence"] = links
        decisions.append(decision)
    return {"input_digest": key, "assessment_version": version, "evidence_digest": evidence,
            "model": model, "reasoning_effort": effort, "threads": decisions}


class SharedData:
    def __init__(self, archive, repositories, *, remote=SHARED_REMOTE, cwd=None, retrieval_git_root=None,
                 ref=DATA_REF, snapshot=False):
        self.archive = archive
        self.repositories = set(repositories)
        self.remote = remote
        self.cwd = cwd
        self.retrieval_git_root = retrieval_git_root
        self.ref = ref
        self.snapshot = snapshot
        self.tip = None
        self.imported = {}

    def git(self, *args, binary=False, input=None):
        result = run_command(["git", "--no-pager", *args], binary=binary, input=input, cwd=self.cwd)
        if result.returncode:
            error = result.stderr.decode("utf-8", errors="replace") if binary else result.stderr
            raise DataError(f"Shared data branch: git {args[0]} failed: {error.strip()}")
        return result.stdout if binary else result.stdout.strip()

    def blobs(self, entries):
        for offset in range(0, len(entries), 32):
            batch = entries[offset:offset + 32]
            request = "".join(f"{oid}\n" for _, oid in batch).encode()
            output = self.git("cat-file", "--batch", binary=True, input=request)
            position = 0
            for path, oid in batch:
                boundary = output.find(b"\n", position)
                header = output[position:boundary].decode().split()
                if boundary < 0 or len(header) != 3 or header[:2] != [oid, "blob"]:
                    raise DataError(f"Unexpected Git blob for {path}")
                size = int(header[2])
                position = boundary + 1
                end = position + size
                if size > MAX_RECORD_BYTES or output[end:end + 1] != b"\n":
                    raise DataError(f"Invalid shared record size for {path}")
                yield path, output[position:end]
                position = end + 1
            if position != len(output):
                raise DataError("Unexpected extra data from Git")

    def load(self, tip=None):
        self.imported = {}
        if tip is None:
            tip = self.git("ls-remote", "--exit-code", self.remote, self.ref).partition("\t")[0]
        if not object_id(tip):
            raise DataError("Shared data branch did not return a commit ID")
        depth = ["--depth=1"] if self.snapshot else []
        self.git("fetch", "--quiet", "--no-tags", "--no-write-fetch-head", *depth, self.remote, tip)
        listing = self.git("ls-tree", "-rlz", tip, binary=True)
        entries = {}
        for line in listing.rstrip(b"\0").split(b"\0"):
            attributes, _, path = line.partition(b"\t")
            fields = attributes.decode().split()
            if len(fields) != 4 or fields[:2] != ["100644", "blob"] or not object_id(fields[2]):
                raise DataError("Shared data branch contains an unexpected Git entry")
            entries[path.decode()] = (fields[2], int(fields[3]))
        if MANIFEST not in entries or any(size > MAX_RECORD_BYTES for _, size in entries.values()):
            raise DataError("Shared data branch is missing a manifest or contains an oversized file")
        ordered = sorted(entries, key=lambda path: (
            path != MANIFEST, ".assessment." in path, path.endswith(".gz"), path))
        with self.archive.db:
            for path, blob in self.blobs([(path, entries[path][0]) for path in ordered]):
                if self.snapshot and self.inactive_record(path):
                    continue
                self.load_record(path, blob)
                self.imported[path] = sha256(blob).digest()
        self.tip = tip

    def inactive_record(self, path):
        owner, _, rest = path.partition("/")
        name, _, record = rest.partition("/")
        return (owner == "open-telemetry" and re.fullmatch(r"[a-z0-9][a-z0-9._-]*", name)
                and f"{owner}/{name}" not in self.repositories
                and (record in {"coverage.json", "line-changes.json", "merge-timing.json",
                                "pr-authors.json", COLLECTION_RETRIES}
                     or PR_FILE.fullmatch(record)))

    @staticmethod
    def pull_file(repo, day, number):
        return f"{repo}/{day[:7]}/{number}.json"

    @staticmethod
    def text_record(value):
        return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")

    def load_record(self, path, blob):
        if path == MANIFEST:
            if json.loads(blob) != FORMAT:
                raise DataError("Unsupported shared data format")
            return
        parts = path.split("/", 2)
        if len(parts) != 3:
            raise DataError(f"Unexpected shared data file: {path}")
        owner, name, relative = parts
        repo = f"{owner}/{name}"
        if repo not in self.repositories:
            raise DataError(f"Unexpected repository in shared data: {path}")
        if relative == "coverage.json":
            value = json.loads(blob)
            if not isinstance(value, list) or any(date.fromisoformat(day).isoformat() != day for day in value):
                raise DataError(f"Invalid date coverage: {path}")
            self.archive.db.executemany("INSERT OR IGNORE INTO complete_days VALUES (?, ?)",
                                       ((repo, day) for day in value))
            return
        if relative == "line-changes.json":
            records = json.loads(blob)
            numbers = {str(number): number for (number,) in self.archive.db.execute(
                "SELECT number FROM pulls WHERE repo = ?", (repo,),
            )}
            if not isinstance(records, dict) or any(
                key not in numbers or not isinstance(value, dict) for key, value in records.items()
            ):
                raise DataError(f"Invalid shared line counts: {path}")
            self.archive.db.executemany("INSERT OR REPLACE INTO pull_line_counts VALUES (?, ?, ?, ?)", [
                (repo, numbers[key], *line_counts(value, f"{repo}#{key}")) for key, value in records.items()
            ])
            return
        if relative == "merge-timing.json":
            records = json.loads(blob)
            pulls = {str(number): (number, merged) for number, merged in self.archive.db.execute(
                "SELECT number, merged FROM pulls WHERE repo = ?", (repo,),
            )}
            if not isinstance(records, dict) or not records.keys() <= pulls.keys():
                raise DataError(f"Invalid shared merge timings: {path}")
            self.archive.db.executemany("INSERT OR REPLACE INTO pull_merge_timings VALUES (?, ?, ?, ?)", [
                (repo, pulls[key][0], *merge_timing(value, pulls[key][1], f"{repo}#{key}"))
                for key, value in records.items()
            ])
            return
        if self.snapshot and relative == "pr-authors.json":
            records = json.loads(blob)
            numbers = {str(number): number for (number,) in self.archive.db.execute(
                "SELECT number FROM pulls WHERE repo = ?", (repo,),
            )}
            if not isinstance(records, dict) or not records.keys() <= numbers.keys() or any(
                author is not None and (
                    not isinstance(author, dict) or author.keys() != {"login", "kind"}
                    or not all(isinstance(value, str) and re.fullmatch(r"[\w\[\]-]+", value, re.ASCII)
                               for value in author.values())
                ) for author in records.values()
            ):
                raise DataError(f"Invalid shared PR authors: {path}")
            self.archive.db.executemany("INSERT OR REPLACE INTO pull_authors VALUES (?, ?, ?, ?)", [
                (repo, numbers[number], author["login"] if author else None, author["kind"] if author else None)
                for number, author in records.items()
            ])
            return
        if self.snapshot and relative == COLLECTION_RETRIES:
            records = json.loads(blob)
            if not isinstance(records, dict) or not records.keys() <= {"assessments"}:
                raise DataError(f"Invalid collection retry queues: {path}")
            numbers = {str(number) for (number,) in self.archive.db.execute(
                "SELECT number FROM pulls WHERE repo = ?", (repo,),
            )}
            rows = []
            for queue, attempts in records.items():
                if not isinstance(attempts, dict) or not attempts.keys() <= numbers or any(
                    type(at) is not int or not 0 < at < 2 ** 63 for at in attempts.values()
                ):
                    raise DataError(f"Invalid collection retry records: {path}")
                rows.extend((repo, queue, item, at) for item, at in attempts.items())
            self.archive.db.execute("DELETE FROM collection_retries WHERE repo = ?", (repo,))
            self.archive.db.executemany("INSERT INTO collection_retries VALUES (?, ?, ?, ?)", rows)
            return
        parsed = PR_FILE.fullmatch(relative)
        if not parsed:
            raise DataError(f"Unexpected shared data file: {path}")
        month, number, suffix, key, version = parsed.groups()
        number = int(number)
        if suffix == ".json":
            pull = json.loads(blob)
            merged = instant(pull["merged"]).astimezone(timezone.utc).date().isoformat()
            if pull["repo"] != repo or pull["number"] != number or not merged.startswith(month + "-"):
                raise DataError(f"Inconsistent shared pull request: {path}")
            self.archive.db.execute("INSERT OR REPLACE INTO pulls VALUES (?, ?, ?, ?, ?)",
                                    (repo, number, merged, pull["merged"], encode(pull)))
            return
        row = self.archive.db.execute("SELECT merged_day, payload FROM pulls WHERE repo = ? AND number = ?",
                                     (repo, number)).fetchone()
        if key:
            result = json.loads(blob)
            if (not row or not row[0].startswith(month + "-") or not isinstance(result, dict)
                    or result != public_assessment(decode(row[1]), result, key)):
                raise DataError(f"Invalid shared assessment: {path}")
            self.archive.db.execute("INSERT OR REPLACE INTO judgments VALUES (?, ?, ?, ?, ?)",
                                    (repo, number, "published", key, encode(result)))
            return
        with gzip.GzipFile(fileobj=BytesIO(blob)) as contents:
            payload = contents.read(MAX_RECORD_BYTES + 1)
        if len(payload) > MAX_RECORD_BYTES:
            raise DataError(f"Oversized prepared evidence: {path}")
        bundle = json.loads(payload)
        if (bundle["version"] not in SUPPORTED_PREPARED_VERSIONS
                or bundle["version"] != int(version or "1")):
            raise DataError(f"Unsupported prepared evidence version: {path}")
        if not row or not row[0].startswith(month + "-") or digest(decode(row[1])) != bundle["input"]:
            raise DataError(f"Prepared evidence doesn't match its pull request: {path}")
        self.archive.db.execute("INSERT OR REPLACE INTO prepared_evidence VALUES (?, ?, ?, ?, ?)",
                                (repo, number, bundle["version"], bundle["input"], encode(bundle)))

    def local_records(self, include_assessments, repositories):
        for repo in repositories:
            complete = self.archive.db.execute("SELECT day FROM complete_days WHERE repo = ? ORDER BY day",
                                              (repo,))
            yield f"{repo}/coverage.json", self.text_record([day for (day,) in complete]), None
            sizes = self.archive.db.execute(
                "SELECT number, additions, deletions FROM pull_line_counts WHERE repo = ?", (repo,),
            )
            counts = {str(n): {"additions": added, "deletions": deleted} for n, added, deleted in sizes}
            if counts:
                yield f"{repo}/line-changes.json", self.text_record(counts), counts
            timings = self.archive.db.execute(
                "SELECT number, created_at, ready_at FROM pull_merge_timings WHERE repo = ?", (repo,),
            )
            timing = {str(n): {"created_at": created, "ready_at": ready} for n, created, ready in timings}
            if timing:
                yield f"{repo}/merge-timing.json", self.text_record(timing), timing
            if self.snapshot:
                authors = {str(n): {"login": login, "kind": kind} if login is not None else None
                           for n, login, kind in self.archive.db.execute(
                               "SELECT number, login, kind FROM pull_authors WHERE repo = ?", (repo,))}
                if authors:
                    yield f"{repo}/pr-authors.json", self.text_record(authors), authors
                retries = {}
                for queue, item, at in self.archive.db.execute(
                    "SELECT queue, item, attempted_at FROM collection_retries WHERE repo = ?", (repo,),
                ):
                    retries.setdefault(queue, {})[item] = at
                path = f"{repo}/{COLLECTION_RETRIES}"
                if retries or path in self.imported:
                    yield path, self.text_record(retries), retries
            rows = self.archive.db.execute("""
                SELECT number, merged_day, payload FROM pulls
                WHERE repo = ? ORDER BY merged_day, number
            """, (repo,))
            for number, day, raw in rows:
                pull = decode(raw)
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) or number < 1:
                    raise DataError("Invalid local PR identity")
                name = self.pull_file(repo, day, number)
                yield name, self.text_record(pull), pull
                for version in SUPPORTED_PREPARED_VERSIONS:
                    evidence = self.archive.get_prepared(pull, version)
                    if evidence is None:
                        continue
                    data = json_bytes(evidence)
                    if len(data) > MAX_RECORD_BYTES:
                        raise RecordRejected(f"Prepared evidence is too large for {repo}#{number}")
                    suffix = "" if version == 1 else f".v{version}"
                    yield (name.removesuffix(".json") + f".evidence{suffix}.json.gz",
                           gzip.compress(data, mtime=0), evidence)
                    if include_assessments and pull["threads"]:
                        yield from self.assessment_records(repo, number, name, pull, evidence)

    def assessment_records(self, repo, number, name, pull, evidence):
        evidence_digest = digest(evidence)
        retriever = None

        def replay(thread, request, suffix):
            nonlocal retriever
            if retriever is None:
                retriever = OutcomeEvidence(self.retrieval_git_root, pull)
            return retriever.lookup(thread, request, suffix)

        for key, payload in self.archive.db.execute("""
            SELECT fingerprint, payload FROM judgments
            WHERE repo = ? AND number = ? AND stage = 'complete' ORDER BY fingerprint
        """, (repo, number)):
            path = name.removesuffix(".json") + f".assessment.{key}.json"
            if path in self.imported:
                continue
            result = decode(payload)
            if result.get("evidence_digest") != evidence_digest:
                continue
            version = result.get("assessment_version")
            lookup = None
            if version in LOCAL_ASSESSMENT_VERSIONS:
                if version != RETRIEVAL_VERSION or self.retrieval_git_root is None:
                    continue
                lookup = replay
            record = public_assessment(pull, result, key, bundle=evidence, lookup=lookup)
            yield path, self.text_record(record), record

    def prepare_commit(self, ref, *, include_assessments=False, on_rejected_repository=None):
        if self.tip is None:
            raise DataError("Load the current data branch before publishing")
        identity = self.git("var", "GIT_COMMITTER_IDENT")
        message = (b"Update public PR review data\n\n"
                   b"Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>\n")
        stream = bytearray(f"commit {ref}\ncommitter {identity}\ndata {len(message)}\n".encode())
        stream.extend(message)
        stream.extend(f"\nfrom {self.tip}\n".encode())
        seen = {path: value for path, value in self.imported.items()
                if re.search(ASSESSMENT_FILE + "$", path)}

        def append_records(records):
            start = len(stream)
            hashes = {}
            try:
                for path, blob, value in records:
                    hashes[path] = sha256(blob).digest()
                    if hashes[path] == self.imported.get(path):
                        continue
                    check_record(path, blob, value)
                    stream.extend(f"M 100644 inline {path}\ndata {len(blob)}\n".encode())
                    stream.extend(blob)
                    stream.extend(b"\n")
            except RecordRejected:
                del stream[start:]
                raise
            seen.update(hashes)

        append_records([(MANIFEST, self.text_record(FORMAT), None)])
        for repo in sorted(self.repositories):
            try:
                append_records(self.local_records(include_assessments, [repo]))
            except RecordRejected as error:
                if not self.snapshot or on_rejected_repository is None:
                    raise
                on_rejected_repository(repo, error)
        if not self.snapshot:
            if seen == self.imported:
                return None
            for path in sorted(self.imported.keys() - seen.keys()):
                stream.extend(f"D {path}\n".encode())
        stream.extend(b"\ndone\n")
        self.git("fast-import", "--quiet", "--done", binary=True, input=bytes(stream))
        return seen

    def publish(self, *, include_assessments=False):
        ref = f"refs/scorecard/data-{uuid4().hex}"
        try:
            seen = self.prepare_commit(ref, include_assessments=include_assessments)
            if seen is None:
                return None
            commit = self.git("rev-parse", ref)
            self.git("push", "--quiet", self.remote, f"{commit}:{self.ref}")
            self.tip, self.imported = commit, seen
            return commit
        finally:
            self.git("update-ref", "-d", ref)
