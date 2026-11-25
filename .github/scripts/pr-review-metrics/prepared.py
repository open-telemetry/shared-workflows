import json
import re
from functools import lru_cache
from hashlib import sha256
from pathlib import PurePosixPath

from github_data import blob_url, instant
from git_evidence import GitEvidence, GitUnavailable, object_id


PREPARED_VERSION = 3
SUPPORTED_PREPARED_VERSIONS = (1, 2, PREPARED_VERSION)
MAX_PATHS = 4
PATCH_CANDIDATES = 24
HISTORY_BYTES = 100_000
UNREADABLE = (GitUnavailable, UnicodeError)


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(value):
    return sha256(json_bytes(value)).hexdigest()


def bounded(text, limit):
    if text is None or len(text) <= limit:
        return text
    return text[:limit] + "\n[truncated]"


def pack_changes(candidates, byte_limit):
    remaining = byte_limit
    for record in candidates:
        size = len(json_bytes(record))
        if size <= remaining:
            yield record
            remaining -= size


class Evidence:
    def __init__(self, git_root, pull):
        self.pull = pull
        self.repo = pull["repo"]
        self.merge = object_id(pull["merge"])
        self.head = object_id(pull["head"])
        self.git = GitEvidence(git_root, self.repo, pull["number"])
        self.commit = lru_cache(maxsize=None)(self._commit)
        self.compare = lru_cache(maxsize=24)(self._compare)
        self.file = lru_cache(maxsize=128)(self._file)
        self.after_review = lru_cache(maxsize=64)(self.git.after_review)
        self.pr_history = lru_cache(maxsize=1)(self._pr_history)

    def _commit(self, revision):
        if not revision:
            return None
        try:
            return self.git.commit(revision)
        except UNREADABLE:
            return None

    def _compare(self, start, end):
        try:
            return self.git.compare(start, end)
        except UNREADABLE:
            return None

    def _file(self, revision, path):
        try:
            content = self.git.file(revision, path)
        except UNREADABLE:
            return {"state": "unavailable"}
        if content is None:
            return {"state": "absent"}
        return self.file_state(content)

    @staticmethod
    def file_state(content):
        try:
            text = content.decode("utf-8")
        except UnicodeError:
            return {"state": "unavailable"}
        if "\0" in text:
            return {"state": "unavailable"}
        return {"state": "text" if len(text) <= 14_000 else "truncated", "text": text}

    def snapshot(self, revision, path, thread):
        state = self.file(revision, path)
        if state["state"] != "truncated":
            return state
        text = state["text"]
        terms = identifiers(thread["body"])
        if not terms:
            return dict(state, text=bounded(text, 14_000))
        lines = text.splitlines()
        hits = [(len(terms & identifiers(line)), i) for i, line in enumerate(lines)]
        hits = sorted(((count, i) for count, i in hits if count), key=lambda hit: (-hit[0], -hit[1]))
        ranges = []
        for _, i in hits:
            if any(start <= i < end for start, end in ranges):
                continue
            ranges.append((max(0, i - 12), min(len(lines), i + 13)))
            if len(ranges) == 6:
                break
        if not ranges:
            return dict(state, text=bounded(text, 14_000))
        combined = []
        for first, last in sorted(ranges):
            if combined and first <= combined[-1][1]:
                combined[-1] = (combined[-1][0], max(last, combined[-1][1]))
            else:
                combined.append((first, last))
        excerpts = [f"[lines {first + 1}-{last} of {len(lines)}]\n" + "\n".join(lines[first:last])
                    for first, last in combined]
        return dict(state, text=bounded(text[:2200] + "\n[excerpts]\n" + "\n".join(excerpts), 14_000))

    def mentioned_paths(self, thread, changed, reviewed):
        candidates = list(dict.fromkeys(re.findall(
            r"(?<![\w./:-])(?:[\w.-]+/)+[\w.-]+\.[\w-]+(?![\w/-])", thread["body"],
        )))
        return {
            path for path in candidates[:12]
            if "." not in path.split("/") and ".." not in path.split("/")
            and (path in changed or any(self.file(revision, path)["state"] in {"text", "truncated"}
                                        for revision in (self.merge, reviewed) if revision))
        }

    def scoped_patches(self, item, paths):
        def patch(path):
            try:
                return bounded(self.git.patch(*item["git_range"], path), 10_000)
            except UNREADABLE:
                return None

        return [
            {"path": file["path"], "previous_path": None, "status": file["status"],
             "patch": patch(file["path"])}
            for file in item["files"] if file["path"] in paths
        ]

    def _pr_history(self):
        self.git.prepare({self.head, self.merge})
        if self.head and self.head in self.git.checked:
            try:
                return self.git.pr_commits(self.head, self.merge)
            except UNREADABLE:
                pass
        return None

    def changed_paths(self):
        revisions = set(filter(None, (object_id(t["reviewed"]) for t in self.pull["threads"])))
        self.git.prepare({*revisions, self.head, self.merge})
        files = list((self.commit(self.merge) or {}).get("files", []))
        for sha, _ in self.pr_history() or ():
            files += (self.commit(sha) or {}).get("files", [])
        return {item["path"] for item in files}

    def ranked_patches(self, thread, reviewed, compared, changed):
        history = self.pr_history()
        if history is None or not compared or compared["status"] not in {"ahead", "identical"}:
            return []
        opened, merged = instant(thread["opened"]), instant(self.pull["merged"])
        after_review = self.after_review(reviewed, self.head)
        score = path_score(thread)
        terms = identifiers(thread["body"])
        candidates = []
        for sha, at in history:
            if sha not in after_review or instant(at) > merged:
                continue
            commit = self.commit(sha)
            if not commit:
                continue
            paths = [item["path"] for item in commit["files"] if item["path"] in changed]
            if not paths:
                continue
            path_pool = sorted(paths, key=lambda path: (-score(path), path))[:MAX_PATHS]
            if score(path_pool[0]) == 0:
                continue
            distance = abs((instant(at) - opened).total_seconds())
            candidates.append((score(path_pool[0]), distance, sha, at, path_pool))
        candidates.sort(key=lambda candidate: (-candidate[0], candidate[1], candidate[2]))
        candidates = candidates[:PATCH_CANDIDATES]
        self.git.prefetch_patches((self.commit(sha), paths) for _, _, sha, _, paths in candidates)
        for weight, distance, sha, at, paths in candidates:
            patches = self.scoped_patches(self.commit(sha), paths)
            matched = set()
            for patch in patches:
                text = patch["patch"] or ""
                changed_lines = " ".join(line[1:] for line in text.splitlines()
                                         if line.startswith(("+", "-")) and not line.startswith(("+++", "---")))
                matched.update(terms & identifiers(changed_lines))
            yield_rank = (len(matched), weight, -distance, sha)
            yield yield_rank, sha, at, patches

    def for_thread(self, thread, changed):
        reviewed = object_id(thread["reviewed"])
        records = {}
        compared = self.compare(reviewed, self.head) if reviewed and self.head else None
        ranked = sorted(self.ranked_patches(thread, reviewed, compared, changed), reverse=True)
        votes = {}
        for rank, _, _, patches in ranked:
            for patch in patches:
                path = patch["path"]
                if patch["patch"]:
                    votes[path] = max(votes.get(path, ()), rank[:3])
        paths = evidence_paths(thread, changed | self.mentioned_paths(thread, changed, reviewed), votes)
        candidates = []
        opened = instant(thread["opened"])
        for _, sha, at, patches in ranked:
            relevant = [p for p in patches if p["path"] in paths and p["patch"]]
            if relevant:
                candidates.append(change_record(self.repo, sha, at, opened, relevant))
        retained = list(pack_changes(candidates, HISTORY_BYTES))
        merge = self.commit(self.merge)
        self.git.prefetch_patches(((compared, paths), (merge, paths)))
        if compared:
            complete = compared["status"] in {"ahead", "identical"}
            scoped = self.scoped_patches(compared, paths)
            if any(not item["patch"] for item in scoped):
                complete = False
            records["comparison"] = {
                "url": f"https://github.com/{self.repo}/compare/{reviewed}...{self.head}",
                "status": compared["status"],
                "patches": scoped,
                "files_in_comparison": len(compared["files"]), "complete": complete,
                "relevant_patches_omitted_from_ranked_set": len(candidates) - len(retained),
                "pr_history_available": self.pr_history() is not None and compared["status"] in {"ahead", "identical"},
            }
        for name, revision, verified in (("reviewed", reviewed, compared or self.commit(reviewed)),
                                         ("merged", self.merge, merge)):
            if revision and verified:
                for i, path in enumerate(paths):
                    records[f"{name}-{i}"] = {
                        "url": blob_url(self.repo, revision, path), "path": path,
                        **self.snapshot(revision, path, thread),
                    }
        if merge:
            records["merge-commit"] = {
                "url": f"https://github.com/{self.repo}/commit/{self.merge}",
                "patches": self.scoped_patches(merge, paths),
            }
        for i, record in enumerate(retained):
            records[f"change-{i}"] = record
        return paths, records


def change_record(repo, revision, timestamp, opened, patches):
    return {
        "url": f"https://github.com/{repo}/commit/{revision}",
        "committed_at": timestamp, "dated_after_publication": instant(timestamp) > opened, "patches": patches,
    }


def identifiers(value):
    return {token.lower() for token in re.findall(r"\b[A-Za-z_]\w{3,}\b", value)
            if any(c.isupper() for c in token[1:]) or "_" in token}


def path_score(thread):
    path = thread["path"]
    original = PurePosixPath(path)
    text = thread["body"].lower()

    @lru_cache(maxsize=None)
    def score(candidate):
        if candidate == path:
            return 12
        target = PurePosixPath(candidate)
        shared = 0
        for a, b in zip(original.parent.parts, target.parent.parts):
            if a != b:
                break
            shared += 1
        stem = original.stem.lower()
        similar = len(stem) >= 3 and stem in target.stem.lower()
        target_stem = target.stem.lower()
        mentioned = len(target_stem) >= 5 and re.search(rf"\b{re.escape(target_stem)}\b", text)
        same_directory = target.parent == original.parent and shared > 0
        suffix = original.suffixes
        same_suffix = len(suffix) > 1 and target.suffixes[-2:] == suffix[-2:]
        return (10 * bool(mentioned or candidate.lower() in text) + 4 * similar
                + min(shared, 5) * (shared >= 2) + 2 * same_directory + 4 * (same_directory and same_suffix))

    return score


def evidence_paths(thread, changed, votes=None):
    path = thread["path"]
    score = path_score(thread)
    votes = votes or {}
    opener = thread["body"].lower()
    related = sorted(changed - {path}, key=lambda name: (
        name.lower() not in opener, -votes.get(name, (0, 0, 0))[0],
        -score(name), -votes.get(name, (0, 0, 0))[1], name,
    ))
    distinct, repeated = [], []
    seen = {PurePosixPath(path).name}
    for name in related:
        if not score(name) and name not in votes:
            continue
        basename = PurePosixPath(name).name
        if basename in seen:
            repeated.append(name)
        else:
            seen.add(basename)
            distinct.append(name)
    related = sorted(distinct + repeated, key=lambda name: name.lower() not in opener)
    return [path, *related[:MAX_PATHS - 1]]


def prepare(pull, evidence):
    changed = evidence.changed_paths()
    records, indexes, threads = [], {}, {}
    for thread in pull["threads"]:
        refs = {}
        paths, records_for_thread = evidence.for_thread(thread, changed)
        omitted = max(0, len(changed - set(paths)))
        for key, value in records_for_thread.items():
            name = digest(value)
            if name not in indexes:
                indexes[name] = len(records)
                records.append(value)
            refs[key] = indexes[name]
        threads[thread["id"]] = {"paths": paths, "omitted_related_paths": omitted, "records": refs}
    return {"version": PREPARED_VERSION, "input": digest(pull), "threads": threads, "records": records}


class PreparedEvidence:
    def __init__(self, bundle):
        self.bundle = bundle

    def paths(self):
        return {key: item["paths"] for key, item in self.bundle["threads"].items()}

    def for_thread(self, thread, paths):
        item = self.bundle["threads"][thread["id"]]
        records = {}
        for key, index in item["records"].items():
            value = self.bundle["records"][index]
            if "path" in value and value["path"] not in paths:
                continue
            if "patches" in value:
                scoped = [patch for patch in value["patches"]
                          if patch["path"] in paths or patch["previous_path"] in paths]
                if key.startswith(("later-", "change-")) and not scoped:
                    continue
                value = dict(value, patches=scoped)
            records[key] = value
        return records
