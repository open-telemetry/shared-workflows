import re
from subprocess import TimeoutExpired

from github_data import DataError, blob_url, instant
from git_evidence import object_id
from prepared import Evidence, bounded, change_record, json_bytes, pack_changes, path_score


LOOKUP_ROUNDS = 2
LOOKUP_BYTES = 16_000
SEARCH_FILES = 20
HISTORY_SCAN = 16


def valid_request(value):
    if not isinstance(value, dict) or set(value) != {"action", "path", "query"}:
        return False
    action, path, query = value["action"], value["path"], value["query"]
    if not isinstance(query, str) or len(query) > 100 or not all(c.isprintable() for c in query):
        return False
    if action == "search":
        return path is None and bool(query.strip())
    return (action in ("read", "history") and isinstance(path, str) and len(path) <= 400
            and all(c.isprintable() for c in path) and not re.search(r"[:\\]", path)
            and all(part not in ("", ".", "..") for part in path.split("/")))


def excerpt(state, query):
    text = state.get("text", "")
    if len(text) <= 2600:
        return state
    matches = list(re.finditer(re.escape(query), text, flags=re.IGNORECASE)) if query else []
    if matches:
        pos = matches[0].start()
        start = text.rfind("\n", max(0, pos - 900), pos)
        if start < 0:
            start = max(0, pos - 900)
        text = f"[excerpt near line {text.count(chr(10), 0, pos) + 1}]\n" + text[start:start + 2600]
    return {"state": "truncated", "text": bounded(text, 2700)}


class OutcomeEvidence:
    def __init__(self, git_root, pull):
        self.source = Evidence(git_root, pull)
        self.paths = None

    def snapshots(self, thread, path, query, suffix):
        records = {}
        for label, rev in (("reviewed", object_id(thread["reviewed"])), ("merged", self.source.merge)):
            if rev:
                state = self.source.file(rev, path)
                state = excerpt(state, query) if query else excerpt(self.source.snapshot(rev, path, thread), "")
                records[f"{label}-lookup-{suffix}"] = {
                    "url": blob_url(self.source.repo, rev, path), "path": path, **state,
                }
        return records

    def search(self, thread, query, suffix):
        if self.paths is None:
            self.paths = self.source.changed_paths()
        score = path_score(thread)
        paths = sorted(self.paths | {thread["path"]}, key=lambda p: (-score(p), p))
        query_lower = query.lower()
        hits = [path for path in paths if query_lower in path.lower()][:2]
        if not hits:
            for path in paths[:SEARCH_FILES]:
                for rev in (self.source.merge, object_id(thread["reviewed"])):
                    if rev and query_lower in self.source.file(rev, path).get("text", "").lower():
                        hits.append(path)
                        break
                if len(hits) == 2:
                    break
        records = {}
        for i, path in enumerate(hits):
            records.update(self.snapshots(thread, path, query, f"{suffix}-{i}"))
        return records, ("Matched PR-changed or commented paths; search is bounded."
                         if records else "No match in the bounded scan of PR-changed or commented paths.")

    def history(self, thread, path, query, suffix):
        source = self.source
        reviewed = object_id(thread["reviewed"])
        compared = source.compare(reviewed, source.head) if reviewed and source.head else None
        if not compared or compared["status"] not in {"ahead", "identical"} or source.pr_history() is None:
            return {}, "PR-only chronology is unavailable for this reviewed revision."
        after = source.after_review(reviewed, source.head)
        opened, merged = instant(thread["opened"]), instant(source.pull["merged"])
        candidates = sorted(
            ((sha, at, commit) for sha, at in source.pr_history()
             if sha in after and instant(at) <= merged and (commit := source.commit(sha))
             and any(item["path"] == path for item in commit["files"])),
            key=lambda item: (abs((instant(item[1]) - opened).total_seconds()), item[0]),
        )[:HISTORY_SCAN]
        source.git.prefetch_patches((commit, [path]) for _, _, commit in candidates)
        changes = []
        for sha, at, commit in candidates:
            for diff in source.scoped_patches(commit, [path]):
                text = diff["patch"]
                if text and (not query or query.lower() in text.lower()):
                    patch = excerpt({"text": text}, query)["text"]
                    changes.append(change_record(source.repo, sha, at, opened, [dict(diff, patch=patch)]))
        records = {f"change-lookup-{suffix}-{i}": value
                   for i, value in enumerate(pack_changes(changes, LOOKUP_BYTES // 2))}
        return records, ("Returned a bounded set of PR-only diffs for this path." if records
                         else "No matching diff in the bounded PR-only history scan.")

    def lookup(self, thread, request, suffix):
        if not valid_request(request):
            return {}, "Invalid lookup. Use a relative repository path or a short literal search term."
        action, path, query = (request[key] for key in ("action", "path", "query"))
        try:
            if action == "search":
                records, note = self.search(thread, query, suffix)
            elif action == "history":
                records, note = self.history(thread, path, query, suffix)
            else:
                records = self.snapshots(thread, path, query, suffix)
                note = "Read the requested file in the archived reviewed and merged revisions."
        except (DataError, OSError, TimeoutExpired, UnicodeError):
            return {}, "Git could not complete this lookup."
        if len(json_bytes(records)) > LOOKUP_BYTES:
            return {}, "Lookup result exceeded the output budget. Ask for a narrower search."
        return records, note
