import json
import logging
import re
import subprocess
from datetime import datetime, timedelta, timezone
from time import monotonic, sleep, time
from urllib.parse import quote, urlencode


class DataError(RuntimeError):
    pass


class GitHubError(DataError):
    pass


class RateLimitError(GitHubError):
    pass


class BudgetExhausted(DataError):
    pass


def run_command(args, *, binary=False, **kwargs):
    return subprocess.run(
        args, encoding=None if binary else "utf-8", capture_output=True, timeout=180,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), **kwargs,
    )


def instant(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def line_counts(record, ref):
    counts = record.get("additions"), record.get("deletions")
    if any(type(count) is not int or count < 0 for count in counts):
        raise DataError(f"Invalid line counts for {ref}")
    return counts


def timing_instant(value, ref):
    try:
        at = instant(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise DataError(f"Invalid PR timestamp for {ref}") from exc
    if at.tzinfo is None:
        raise DataError(f"Missing PR timestamp timezone for {ref}")
    return at


def merge_timing(record, merged, ref):
    if not isinstance(record, dict) or record.keys() != {"created_at", "ready_at"}:
        raise DataError(f"Invalid merge timing for {ref}")
    created, ready = record["created_at"], record["ready_at"]
    start, end = timing_instant(created, ref), timing_instant(merged, ref)
    if start > end or (ready is not None and not start <= timing_instant(ready, ref) <= end):
        raise DataError(f"PR timestamps out of order for {ref}")
    return created, ready


def pages(fetch, first):
    seen = set()
    connection = first
    while True:
        yield from connection["nodes"]
        info = connection["pageInfo"]
        if not info["hasNextPage"]:
            return
        cursor = info["endCursor"]
        if not cursor or cursor in seen:
            raise DataError("GitHub pagination did not advance")
        seen.add(cursor)
        connection = fetch(cursor)


COMMENT = """
    id url body createdAt publishedAt path diffHunk
    author { login __typename } replyTo { id }
    originalCommit { oid } pullRequestReview { commit { oid } }
"""
PAGE = "pageInfo { hasNextPage endCursor }"
THREAD = f"id comments(first:8) {{ {PAGE} nodes {{ {COMMENT} }} }}"
TIMELINE_TYPES = "itemTypes:[READY_FOR_REVIEW_EVENT,CONVERT_TO_DRAFT_EVENT,MERGED_EVENT]"
TIMELINE_FIELDS = f"""
    filteredCount {PAGE}
    nodes {{
        __typename
        ... on ReadyForReviewEvent {{ id createdAt }}
        ... on ConvertToDraftEvent {{ id createdAt }}
        ... on MergedEvent {{ id createdAt }}
    }}
"""
MERGE_TIMING = f"createdAt isDraft timelineItems(first:10, {TIMELINE_TYPES}) {{ {TIMELINE_FIELDS} }}"
PULL = (
    f"headRefOid mergeCommit {{ oid }} additions deletions "
    f"reviewThreads(first:25) {{ {PAGE} nodes {{ {THREAD} }} }} {MERGE_TIMING}"
)


class GitHub:
    def __init__(self, max_rate_wait=60, *, max_requests=None, deadline=None):
        self.max_rate_wait = max_rate_wait
        self.max_requests = max_requests
        self.deadline = deadline
        self.requests = 0

    def _call(self, method, path, payload=None, *, attempts=3):
        args = ["gh", "api", "--hostname", "github.com", "--method", method, "--include", path]
        if payload is not None:
            args.extend(["--input", "-"])
        for attempt in range(attempts):
            if self.max_requests is not None and self.requests >= self.max_requests:
                raise BudgetExhausted("GitHub request budget exhausted")
            if self.deadline is not None and monotonic() >= self.deadline:
                raise BudgetExhausted("Collection time budget exhausted")
            self.requests += 1
            result = run_command(args, input=json.dumps(payload) if payload is not None else None)
            header, _, body = result.stdout.partition("\n\n")
            headers = dict(
                (key.lower(), value.strip()) for line in header.splitlines()[1:] if ":" in line
                for key, value in [line.split(":", 1)]
            )
            response = json.loads(body) if not result.returncode else None
            errors = response.get("errors", []) if method == "POST" and isinstance(response, dict) else []
            message = result.stderr.strip()
            limited = (
                "rate limit" in message.lower()
                or "HTTP 429" in message
                or any(error.get("type") == "RATE_LIMITED" for error in errors)
            )
            if limited:
                delay = max(1, int(headers.get("retry-after", "60")))
                if headers.get("x-ratelimit-remaining") == "0" and "x-ratelimit-reset" in headers:
                    delay = max(int(headers["x-ratelimit-reset"]) - int(time()) + 1, 1)
                if attempt == attempts - 1 or delay > self.max_rate_wait:
                    raise RateLimitError(f"GitHub rate limit reached. Retry in {delay}s; progress is saved.")
                sleep(delay)
                continue
            if not result.returncode:
                return response
            if attempt == attempts - 1 or not re.search(
                r"HTTP 5\d\d|REFUSED_STREAM|connection reset|timeout|unexpected EOF", message, re.I
            ):
                raise GitHubError(f"{path}: {message}")
            sleep(2 ** attempt)

    def get(self, path, **params):
        return self._call("GET", f"{path}?{urlencode(params)}" if params else path)

    def write(self, method, path, payload):
        return self._call(method, path, payload, attempts=1)

    def query(self, query, **variables):
        result = self._call("POST", "graphql", {"query": query, "variables": variables})
        if result.get("errors"):
            raise GitHubError("; ".join(error["message"] for error in result["errors"]))
        return result["data"]

    def public(self, repo):
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo, re.ASCII):
            raise DataError(f"Invalid repository: {repo}")
        info = self.get(f"repos/{repo}")
        if info["visibility"] != "public" or info["full_name"].lower() != repo.lower():
            raise DataError(f"Not a canonical public github.com repository: {repo}")
        return info

    def rest_pages(self, path, **params):
        for page in range(1, 1_000_000):
            items = self.get(path, **params, page=page, per_page=100)
            yield from items
            if len(items) < 100:
                return
        raise DataError(f"Pagination did not complete for {path}")

    def merged(self, repo, start, end):
        self.public(repo)
        return self._merged(repo, start, end)

    def _merged(self, repo, start, end):
        query = f"repo:{repo} is:pr is:merged merged:{start}..{end}"
        kwargs = dict(q=query, sort="created", order="asc", per_page=100)
        response = self.get("search/issues", **kwargs, page=1)
        total = response["total_count"]
        if total > 1000 or response["incomplete_results"]:
            if start == end:
                items = self.rest_pages(f"repos/{repo}/pulls", state="closed")
                return self._merged_dates(items, start, end, search=False)
            middle = start + (end - start) // 2
            left = self._merged(repo, start, middle)
            right = self._merged(repo, middle + timedelta(days=1), end)
            if left.keys() & right.keys():
                raise DataError(f"Overlapping search results for {repo}")
            return left | right
        items = response["items"]
        for page in range(2, (total + 99) // 100 + 1):
            response = self.get("search/issues", **kwargs, page=page)
            if response["incomplete_results"] or response["total_count"] != total:
                raise DataError(f"GitHub search changed while paging {repo}; rerun the collector")
            items.extend(response["items"])
        result = self._merged_dates(items, start, end, search=True)
        if len(result) != total or len(items) != total:
            raise DataError(f"GitHub search count did not match the UTC merge cohort for {repo}")
        return result

    @staticmethod
    def _merged_dates(items, start, end, *, search):
        result = {}
        for pr in items:
            value = pr["pull_request"]["merged_at"] if search else pr["merged_at"]
            if value and start <= instant(value).astimezone(timezone.utc).date() <= end:
                if pr["number"] in result:
                    raise DataError("GitHub returned a duplicate pull request")
                result[pr["number"]] = value
        return result

    def _pull_nodes(self, repo, expected, fields, batch_size):
        owner, name = repo.split("/")
        numbers = sorted(expected)
        for offset in range(0, len(numbers), batch_size):
            batch = numbers[offset:offset + batch_size]
            selections = " ".join(
                f"p{n}:pullRequest(number:{n}) {{ number mergedAt {fields} }}" for n in batch
            )
            result = self.query(
                "query($owner:String!, $name:String!) { repository(owner:$owner,name:$name) "
                f"{{ visibility {selections} }} }}", owner=owner, name=name,
            )["repository"]
            if not result or result.get("visibility") != "PUBLIC":
                raise DataError(f"{repo} is not public")
            for n in batch:
                node = result.get(f"p{n}")
                if (not node or node.get("number") != n or not node.get("mergedAt")
                        or instant(node["mergedAt"]) != instant(expected[n])):
                    raise DataError(f"Merge timestamp mismatch for {repo}#{n}")
                yield node

    def pulls(self, repo, expected):
        owner, name = repo.split("/")
        for node in self._pull_nodes(repo, expected, PULL, 10):
            counts = line_counts(node, f"{repo}#{node['number']}")
            timing = self._merge_timing(repo, node)
            yield self._pull(repo, owner, name, node), counts, timing

    def pull_merge_timings(self, repo, expected):
        for node in self._pull_nodes(repo, expected, MERGE_TIMING, 25):
            yield node["number"], self._merge_timing(repo, node)

    def _merge_timing(self, repo, pr):
        owner, name = repo.split("/")
        number = pr["number"]
        ref = f"{repo}#{number}"
        created = pr.get("createdAt")
        start, end = timing_instant(created, ref), timing_instant(pr["mergedAt"], ref)
        draft = pr.get("isDraft")
        if start > end:
            logging.getLogger(__name__).warning("Merge timing unavailable for %s: GitHub creation time follows merge", ref)
            return None
        if type(draft) is not bool:
            raise DataError(f"Invalid PR draft state for {ref}")

        def check_page(page):
            if (not isinstance(page, dict) or not isinstance(page.get("nodes"), list)
                    or type(page.get("filteredCount")) is not int
                    or not isinstance(page.get("pageInfo"), dict)
                    or type(page["pageInfo"].get("hasNextPage")) is not bool
                    or (page["pageInfo"]["hasNextPage"] and not page["pageInfo"].get("endCursor"))):
                raise DataError(f"Invalid readiness timeline page for {ref}")
            return page

        def more_events(cursor):
            result = self.query(
                "query($owner:String!, $name:String!, $n:Int!, $cursor:String!) "
                "{ repository(owner:$owner,name:$name) { visibility pullRequest(number:$n) "
                f"{{ number mergedAt timelineItems(first:100, after:$cursor, {TIMELINE_TYPES}) "
                f"{{ {TIMELINE_FIELDS} }} }} }} }}",
                owner=owner, name=name, n=number, cursor=cursor,
            ).get("repository")
            node = result.get("pullRequest") if result and result.get("visibility") == "PUBLIC" else None
            if not node or node.get("number") != number or timing_instant(node.get("mergedAt"), ref) != end:
                raise DataError(f"Readiness timeline pull request changed for {ref}")
            return check_page(node.get("timelineItems"))

        first = check_page(pr.get("timelineItems"))
        seen, events, previous = set(), [], start
        for event in pages(more_events, first):
            kind = event.get("__typename") if isinstance(event, dict) else None
            if kind not in ("ReadyForReviewEvent", "ConvertToDraftEvent", "MergedEvent"):
                raise DataError(f"Unexpected readiness timeline event for {ref}")
            event_id, stamp = event.get("id"), event.get("createdAt")
            at = timing_instant(stamp, ref)
            if not isinstance(event_id, str) or not event_id or event_id in seen or at < previous:
                raise DataError(f"Inconsistent readiness timeline for {ref}")
            seen.add(event_id)
            events.append((kind, stamp, at))
            previous = at
        if len(seen) != first["filteredCount"]:
            raise DataError(f"Incomplete readiness timeline for {ref}")

        # Timeline order distinguishes readiness and merge events in the same second.
        merges = [i for i, (kind, _, at) in enumerate(events) if kind == "MergedEvent" and at == end]
        changes = [(i, kind, stamp, at) for i, (kind, stamp, at) in enumerate(events) if kind != "MergedEvent"]
        if len(merges) != 1 and any(at == end for _, _, _, at in changes):
            return created, None
        before = [(kind, stamp) for i, kind, stamp, at in changes
                  if at < end or (at == end and i < merges[0])]
        if before:
            return created, next((stamp for kind, stamp in reversed(before) if kind == "ReadyForReviewEvent"), None)
        if any(kind == "MergedEvent" for kind, _, _ in events):
            if (changes and changes[0][1] == "ConvertToDraftEvent") or (not changes and not draft):
                return created, created
        return created, None

    def pull_line_counts(self, repo, expected):
        for node in self._pull_nodes(repo, expected, "additions deletions", 50):
            number = node["number"]
            yield number, line_counts(node, f"{repo}#{number}")

    def pull_authors(self, repo, expected):
        for node in self._pull_nodes(repo, expected, "author { login __typename }", 50):
            number = node["number"]
            if "author" not in node:
                raise DataError(f"GitHub did not return the author field for {repo}#{number}")
            author = node["author"]
            if author is not None and (not isinstance(author, dict) or any(
                not isinstance(author.get(field), str) or not author[field] for field in ("login", "__typename")
            )):
                raise DataError(f"Invalid author for {repo}#{number}")
            yield number, author

    def _pull(self, repo, owner, name, pr):
        def more_threads(cursor):
            result = self.query(
                "query($owner:String!, $name:String!, $n:Int!, $cursor:String!) "
                "{ repository(owner:$owner,name:$name) { visibility pullRequest(number:$n) "
                f"{{ reviewThreads(first:25, after:$cursor) {{ {PAGE} nodes {{ {THREAD} }} }} }} }} }}",
                owner=owner, name=name, n=pr["number"], cursor=cursor,
            )["repository"]
            if not result or result["visibility"] != "PUBLIC":
                raise DataError(f"{repo} is not public")
            return result["pullRequest"]["reviewThreads"]

        merged = instant(pr["mergedAt"])
        threads, missing, seen = [], 0, set()
        for thread in pages(more_threads, pr["reviewThreads"]):
            if thread["id"] in seen:
                raise DataError(f"Duplicate review thread on {repo}#{pr['number']}")
            seen.add(thread["id"])
            first = thread["comments"]["nodes"]
            if not first or first[0]["replyTo"]:
                missing += 1
                continue
            root = first[0]
            if not root["publishedAt"] or instant(root["publishedAt"]) >= merged:
                continue

            def more_comments(cursor):
                return self.query(
                    "query($id:ID!, $cursor:String!) { node(id:$id) { ... on PullRequestReviewThread "
                    f"{{ comments(first:100, after:$cursor) {{ {PAGE} nodes {{ {COMMENT} }} }} }} }} }}",
                    id=thread["id"], cursor=cursor,
                )["node"]["comments"]

            comments = [
                {"url": comment["url"], "body": comment["body"], "at": comment["publishedAt"]}
                for comment in pages(more_comments, thread["comments"])
                if comment["replyTo"] and comment["publishedAt"]
                and instant(comment["publishedAt"]) < merged
            ]
            revision = (
                (root["originalCommit"] or {}).get("oid")
                or ((root["pullRequestReview"] or {}).get("commit") or {}).get("oid")
            )
            threads.append({
                "id": thread["id"], "url": root["url"], "opened": root["publishedAt"],
                "created": root["createdAt"], "path": root["path"], "hunk": root["diffHunk"],
                "body": root["body"], "author": root["author"], "reviewed": revision,
                "replies": comments,
            })
        threads.sort(key=lambda t: (instant(t["opened"]), instant(t["created"]), t["id"]))
        return {
            "repo": repo, "number": pr["number"], "merged": pr["mergedAt"],
            "merge": (pr["mergeCommit"] or {}).get("oid"), "head": pr["headRefOid"],
            "threads": threads, "missing_roots": missing,
        }


def blob_url(repo, revision, path):
    return f"https://github.com/{repo}/blob/{revision}/{quote(path)}"
