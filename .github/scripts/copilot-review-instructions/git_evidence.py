import os
import re
from functools import lru_cache

from github_data import DataError, run_command


def object_id(value):
    return value if isinstance(value, str) and re.fullmatch("[0-9a-f]{40}", value) else None


class GitUnavailable(DataError):
    pass


class GitEvidence:
    def __init__(self, root, repo, number, *, deadline=None):
        self.path = root.joinpath(*repo.split("/")).with_suffix(".git")
        self.url = f"https://github.com/{repo}.git"
        self.number = number
        self.deadline = deadline
        self.checked = set()
        self.unavailable = set()
        self.ready = False
        self.parent = lru_cache(maxsize=64)(self._parent)
        self.patch = lru_cache(maxsize=128)(self.patch)

    def git(self, *args, binary=False, lazy=True, input=None):
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
        if not lazy:
            env["GIT_NO_LAZY_FETCH"] = "1"
        command = ["git", "--no-pager", "--literal-pathspecs", "-c", "core.quotePath=false",
                   "--git-dir", str(self.path), *args]
        result = run_command(command, binary=binary, deadline=self.deadline, env=env, input=input)
        if result.returncode:
            error = result.stderr.decode("utf-8", errors="replace") if binary else result.stderr
            raise GitUnavailable(f"Git {args[0]} failed: {error.strip()}")
        return result.stdout

    def prepare(self, revisions):
        needed = set(filter(object_id, revisions)) - self.checked - self.unavailable
        if not needed:
            return
        if not self.ready:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if not (self.path / "HEAD").exists():
                self.git("init", "--bare", "--quiet")
            self.git("config", "remote.origin.url", self.url)
            self.git("config", "remote.origin.promisor", "true")
            self.git("config", "remote.origin.partialclonefilter", "blob:none")
            self.ready = True
        missing = []
        for revision in sorted(needed):
            try:
                self.git("rev-parse", "--verify", f"{revision}^{{commit}}", lazy=False)
                self.checked.add(revision)
            except GitUnavailable:
                missing.append(revision)
        if not missing:
            return

        def fetch(refs):
            self.git("fetch", "--quiet", "--no-tags", "--no-write-fetch-head", "--filter=blob:none",
                     "origin", *refs)

        try:
            fetch([f"+{revision}:refs/scorecard/commits/{revision}" for revision in missing])
        except GitUnavailable:
            try:
                fetch([f"+refs/pull/{self.number}/head:refs/scorecard/pulls/{self.number}"])
            except GitUnavailable:
                pass
            for revision in missing:
                try:
                    self.git("rev-parse", "--verify", f"{revision}^{{commit}}", lazy=False)
                except GitUnavailable:
                    try:
                        fetch([f"+{revision}:refs/scorecard/commits/{revision}"])
                    except GitUnavailable as error:
                        # GitHub reports a commit it no longer has this way; anything else is a fetch failure.
                        if "not our ref" not in str(error):
                            raise DataError(f"Could not fetch evidence from {self.url}: {error}") from error
                        self.unavailable.add(revision)
        self.checked.update(set(missing) - self.unavailable)

    def require(self, revision):
        self.prepare([revision])
        if revision not in self.checked:
            raise GitUnavailable("The archived commit is not available through Git")

    def _parent(self, revision):
        self.require(revision)
        parents = self.git("show", "--no-patch", "--format=%P", revision).split()
        return parents[0] if parents else None

    def files(self, start, end):
        if not start:
            raise GitUnavailable("No first parent for the requested commit")
        changes = self.git("diff", "--no-ext-diff", "--no-renames", "--raw", "--no-abbrev", "-z",
                           start, end, "--").rstrip("\0").split("\0")
        statuses = {"A": "added", "D": "removed", "M": "modified", "T": "modified"}
        files = []
        for metadata, path in zip(changes[::2], changes[1::2]):
            old_mode, new_mode, old_oid, new_oid, kind = metadata.removeprefix(":").split()
            blobs = [oid for mode, oid in ((old_mode, old_oid), (new_mode, new_oid)) if mode.startswith("100")]
            files.append({"path": path, "status": statuses.get(kind, "modified"), "blobs": blobs})
        return {"git_range": (start, end), "files": files}

    def prefetch_patches(self, requests):
        blobs = set()
        for commit, paths in requests:
            if commit:
                blobs.update(oid for item in commit["files"] if item["path"] in paths for oid in item["blobs"])
        if not blobs:
            return
        info = self.git("cat-file", "--batch-check", lazy=False, input="\n".join(sorted(blobs)) + "\n")
        missing = [line.split()[0] for line in info.splitlines() if line.endswith(" missing")]
        if missing:
            self.git("-c", "fetch.negotiationAlgorithm=noop", "fetch", "--quiet", "--no-tags",
                     "--no-write-fetch-head", "--filter=blob:none", "origin", "--stdin",
                     input=("\n".join(missing) + "\n").encode("ascii"), binary=True)

    def commit(self, revision):
        return self.files(self.parent(revision), revision)

    def compare(self, start, end):
        self.require(start)
        self.require(end)
        base = self.git("merge-base", start, end).strip()
        status = ("identical" if start == end else "ahead" if base == start
                  else "behind" if base == end else "diverged")
        item = self.files(base, end)
        return dict(item, status=status)

    def pr_commits(self, head, merge):
        self.require(head)
        if merge:
            before_merge = self.parent(merge)
            # The recorded head can be the final commit of a fast-forwarded PR.
            # Its parent is not a reliable upstream boundary in that case.
            if before_merge and head != merge:
                lines = self.git("log", "--reverse", "--topo-order", "--no-merges", "--format=%H %cI",
                                 head, "--not", before_merge).splitlines()
                return [tuple(line.split(" ", 1)) for line in lines]
        return None

    def after_review(self, reviewed, head):
        self.require(reviewed)
        self.require(head)
        lines = self.git("rev-list", "--no-merges", f"{reviewed}..{head}").splitlines()
        return set(lines)

    def patch(self, start, end, path):
        return self.git("diff", "--no-color", "--no-ext-diff", "--no-textconv", "--no-renames",
                        start, end, "--", path)

    def file(self, revision, path):
        self.require(revision)
        item = self.git("ls-tree", "-z", revision, "--", path)
        if not item:
            return None
        metadata, _, name = item.rstrip("\0").partition("\t")
        mode, kind, oid = metadata.split(" ")
        if name != path or kind != "blob" or mode == "120000":
            return b"\0"
        if int(self.git("cat-file", "-s", oid)) > 1_000_000:
            return b"\0"
        return self.git("cat-file", "blob", oid, binary=True)
