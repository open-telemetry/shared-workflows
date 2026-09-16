from __future__ import annotations

import argparse
import gzip
import io
import json
import math
import os
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


API_ROOT = "https://api.github.com"
API_VERSION = "2022-11-28"
PAGE_SIZE = 100
PENDING_RETRY_BATCH_SIZE = 25
STATE_VERSION = 1
USER_AGENT = "open-telemetry-github-actions-queue-collector"
COLLECTION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


class ApiError(RuntimeError):
    pass


class RateLimitExhausted(RuntimeError):
    pass


@dataclass(frozen=True)
class ApiResponse:
    status: int
    headers: dict[str, str]
    body: bytes


@dataclass(frozen=True)
class RateSnapshot:
    limit: int
    remaining: int
    reset: int

    def to_dict(self) -> dict[str, int]:
        return {
            "limit": self.limit,
            "remaining": self.remaining,
            "reset": self.reset,
        }


Transport = Callable[[str, dict[str, str]], ApiResponse]


def _urlopen_transport(url: str, headers: dict[str, str]) -> ApiResponse:
    request = Request(url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=30) as response:
            return ApiResponse(
                status=response.status,
                headers={key.lower(): value for key, value in response.headers.items()},
                body=response.read(),
            )
    except HTTPError as error:
        return ApiResponse(
            status=error.code,
            headers={key.lower(): value for key, value in error.headers.items()},
            body=error.read(),
        )
    except URLError as error:
        raise ApiError(f"GitHub API request failed: {error}") from error


class GitHubClient:
    def __init__(
        self,
        token: str,
        *,
        transport: Transport = _urlopen_transport,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = 3,
    ) -> None:
        if not token:
            raise ValueError("a GitHub token is required")
        self._headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": API_VERSION,
        }
        self._transport = transport
        self._sleep = sleep
        self._max_retries = max_retries
        self._rate: RateSnapshot | None = None
        self._initial_rate: RateSnapshot | None = None
        self._minimum_rate: RateSnapshot | None = None
        self.request_count = 0

    @property
    def rate(self) -> RateSnapshot:
        if self._rate is None:
            raise RuntimeError("rate limit has not been loaded")
        return self._rate

    @property
    def initial_rate(self) -> RateSnapshot:
        return self._initial_rate or self.rate

    @property
    def minimum_rate(self) -> RateSnapshot:
        return self._minimum_rate or self.rate

    def load_rate_limit(self) -> RateSnapshot:
        response = self._send(f"{API_ROOT}/rate_limit", check_rate_limit=False)
        data = self._decode_json(response)
        core = (data.get("resources") or {}).get("core") or {}
        self._rate = _parse_rate_snapshot(core)
        self._ensure_rate_available()
        return self._rate

    def get_json(self, path: str, query: dict[str, str] | None = None) -> Any:
        if self._rate is None:
            self.load_rate_limit()
        url = f"{API_ROOT}{path}"
        if query:
            url = f"{url}?{urlencode(query)}"
        response = self._send(url, check_rate_limit=True)
        return self._decode_json(response)

    def _send(self, url: str, *, check_rate_limit: bool) -> ApiResponse:
        for attempt in range(self._max_retries + 1):
            if check_rate_limit:
                self._ensure_rate_available()

            response = self._transport(url, self._headers)
            self.request_count += 1
            self._update_rate(
                response.headers,
                capture_initial=check_rate_limit,
            )

            if 200 <= response.status < 300:
                return response

            if response.status in (403, 429):
                if self._rate is not None and self._rate_exhausted():
                    raise RateLimitExhausted(self._rate_limit_message())
                if _is_secondary_rate_limit(response) and attempt < self._max_retries:
                    self._sleep(_retry_delay(response.headers, attempt))
                    continue

            if response.status >= 500 and attempt < self._max_retries:
                self._sleep(2**attempt)
                continue

            message = response.body.decode("utf-8", errors="replace")
            raise ApiError(
                f"GitHub API returned {response.status} for {url}: {message[:500]}"
            )

        raise AssertionError("unreachable")

    @staticmethod
    def _decode_json(response: ApiResponse) -> Any:
        try:
            return json.loads(response.body)
        except json.JSONDecodeError as error:
            raise ApiError(
                f"GitHub API returned invalid JSON with status {response.status}"
            ) from error

    def _update_rate(
        self,
        headers: dict[str, str],
        *,
        capture_initial: bool,
    ) -> None:
        limit = headers.get("x-ratelimit-limit")
        remaining = headers.get("x-ratelimit-remaining")
        reset = headers.get("x-ratelimit-reset")
        if limit is None or remaining is None or reset is None:
            return
        self._rate = RateSnapshot(
            limit=int(limit),
            remaining=int(remaining),
            reset=int(reset),
        )
        if capture_initial and self._initial_rate is None:
            self._initial_rate = RateSnapshot(
                limit=self._rate.limit,
                remaining=min(self._rate.limit, self._rate.remaining + 1),
                reset=self._rate.reset,
            )
        if capture_initial and (
            self._minimum_rate is None
            or self._rate.remaining / self._rate.limit
            < self._minimum_rate.remaining / self._minimum_rate.limit
        ):
            self._minimum_rate = self._rate

    def _ensure_rate_available(self) -> None:
        if self._rate is not None and self._rate_exhausted():
            raise RateLimitExhausted(self._rate_limit_message())

    def _rate_exhausted(self) -> bool:
        return self.rate.remaining == 0

    def _rate_limit_message(self) -> str:
        rate = self.rate
        return (
            f"REST rate limit exhausted: {rate.remaining} remaining of "
            f"{rate.limit}, reset {rate.reset}"
        )

    def resolve_repositories(
        self,
        org: str,
        selected: list[str] | None = None,
    ) -> list[str]:
        if selected:
            repositories = []
            for name in sorted(set(selected)):
                repository = self.get_json(
                    f"/repos/{quote(org)}/{quote(name)}"
                )
                if repository.get("private") is not False:
                    raise ApiError(f"{org}/{name} is not public")
                if repository.get("archived") is True:
                    raise ApiError(f"{org}/{name} is archived")
                if repository.get("disabled") is True:
                    raise ApiError(f"{org}/{name} is disabled")
                if (repository.get("owner") or {}).get("login", "").lower() != org.lower():
                    raise ApiError(f"{org}/{name} is not owned by {org}")
                repositories.append(name)
            return repositories

        repositories: list[str] = []
        page = 1
        while True:
            batch = self.get_json(
                f"/orgs/{quote(org)}/repos",
                {
                    "type": "public",
                    "sort": "full_name",
                    "direction": "asc",
                    "per_page": str(PAGE_SIZE),
                    "page": str(page),
                },
            )
            if not isinstance(batch, list):
                raise ApiError("organization repositories response is not an array")
            for repository in batch:
                if (
                    repository.get("private") is False
                    and repository.get("archived") is not True
                    and repository.get("disabled") is not True
                ):
                    repositories.append(repository["name"])
            if len(batch) < PAGE_SIZE:
                break
            page += 1
        return sorted(set(repositories))

    def list_workflow_runs(
        self,
        org: str,
        repository: str,
        start: datetime,
        end: datetime,
    ) -> list[dict[str, Any]]:
        if start >= end:
            return []
        return self._list_workflow_runs_range(org, repository, start, end)

    def _list_workflow_runs_range(
        self,
        org: str,
        repository: str,
        start: datetime,
        end: datetime,
    ) -> list[dict[str, Any]]:
        first = self._workflow_runs_page(org, repository, start, end, 1)
        total = first.get("total_count")
        runs = first.get("workflow_runs")
        if not isinstance(total, int) or not isinstance(runs, list):
            raise ApiError("workflow runs response has an invalid shape")

        if total > 1000:
            if end - start <= timedelta(seconds=1):
                raise ApiError(
                    f"{org}/{repository} has more than 1000 runs in one second"
                )
            midpoint = start + (end - start) / 2
            midpoint = midpoint.replace(microsecond=0)
            if midpoint <= start:
                midpoint = start + timedelta(seconds=1)
            combined = [
                *self._list_workflow_runs_range(
                    org, repository, start, midpoint
                ),
                *self._list_workflow_runs_range(
                    org, repository, midpoint, end
                ),
            ]
            return _deduplicate_runs(combined)

        pages = math.ceil(total / PAGE_SIZE)
        for page in range(2, pages + 1):
            response = self._workflow_runs_page(
                org, repository, start, end, page
            )
            page_runs = response.get("workflow_runs")
            if not isinstance(page_runs, list):
                raise ApiError("workflow runs page has an invalid shape")
            runs.extend(page_runs)
        return _deduplicate_runs(runs)

    def _workflow_runs_page(
        self,
        org: str,
        repository: str,
        start: datetime,
        end: datetime,
        page: int,
    ) -> dict[str, Any]:
        inclusive_end = end - timedelta(seconds=1)
        created = f"{_format_instant(start)}..{_format_instant(inclusive_end)}"
        return self.get_json(
            f"/repos/{quote(org)}/{quote(repository)}/actions/runs",
            {
                "created": created,
                "exclude_pull_requests": "true",
                "per_page": str(PAGE_SIZE),
                "page": str(page),
            },
        )

    def get_workflow_run(
        self,
        org: str,
        repository: str,
        run_id: int,
    ) -> dict[str, Any]:
        response = self.get_json(
            f"/repos/{quote(org)}/{quote(repository)}/actions/runs/{run_id}"
        )
        if not isinstance(response, dict):
            raise ApiError("workflow run response is not an object")
        return response

    def list_jobs(
        self,
        org: str,
        repository: str,
        run_id: int,
    ) -> list[dict[str, Any]]:
        first = self._jobs_page(org, repository, run_id, 1)
        total = first.get("total_count")
        jobs = first.get("jobs")
        if not isinstance(total, int) or not isinstance(jobs, list):
            raise ApiError("workflow jobs response has an invalid shape")

        pages = math.ceil(total / PAGE_SIZE)
        for page in range(2, pages + 1):
            response = self._jobs_page(org, repository, run_id, page)
            page_jobs = response.get("jobs")
            if not isinstance(page_jobs, list):
                raise ApiError("workflow jobs page has an invalid shape")
            jobs.extend(page_jobs)

        deduplicated = {job["id"]: job for job in jobs}
        if len(deduplicated) != total:
            raise ApiError(
                f"{org}/{repository} run {run_id} returned "
                f"{len(deduplicated)} of {total} jobs"
            )
        return list(deduplicated.values())

    def _jobs_page(
        self,
        org: str,
        repository: str,
        run_id: int,
        page: int,
    ) -> dict[str, Any]:
        return self.get_json(
            f"/repos/{quote(org)}/{quote(repository)}/actions/runs/{run_id}/jobs",
            {
                "filter": "all",
                "per_page": str(PAGE_SIZE),
                "page": str(page),
            },
        )


@dataclass
class CollectorState:
    cursor: str
    window_repositories: list[str] = field(default_factory=list)
    completed_repositories: list[str] = field(default_factory=list)
    pending_runs: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": STATE_VERSION,
            "cursor": self.cursor,
            "window_repositories": self.window_repositories,
            "completed_repositories": self.completed_repositories,
            "pending_runs": self.pending_runs,
        }


@dataclass(frozen=True)
class CollectionResult:
    records: list[dict[str, Any]]
    paused: bool
    completed_windows: int
    completed_repositories: int


class QueueCollector:
    def __init__(
        self,
        client: GitHubClient,
        *,
        org: str,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._client = client
        self._org = org
        self._now = now

    def collect(
        self,
        state: CollectorState,
        *,
        selected_repositories: list[str] | None = None,
        max_windows: int = 1,
    ) -> CollectionResult:
        if max_windows < 1:
            raise ValueError("max_windows must be positive")

        records: list[dict[str, Any]] = []
        completed_windows = 0
        completed_repositories = 0

        try:
            self._client.load_rate_limit()
            repositories = self._client.resolve_repositories(
                self._org, selected_repositories
            )
            current_repositories = set(repositories)
            resuming_window = bool(state.window_repositories)
            state.window_repositories = [
                repository
                for repository in state.window_repositories
                if repository in current_repositories
            ]
            state.completed_repositories = [
                repository
                for repository in state.completed_repositories
                if repository in current_repositories
            ]
            state.pending_runs = [
                item
                for item in state.pending_runs
                if item["repository"] in current_repositories
            ]
            pending_to_retry = sorted(
                state.pending_runs,
                key=lambda item: (
                    item.get("last_attempt_at") or item.get("created_at") or "",
                    item["repository"],
                    item["run_id"],
                ),
            )[:PENDING_RETRY_BATCH_SIZE]
            self._collect_pending_runs(state, pending_to_retry, records)

            available_until = _floor_hour(self._now())
            while (
                _parse_instant(state.cursor) < available_until
                and completed_windows < max_windows
            ):
                if not resuming_window:
                    state.window_repositories = repositories
                    state.completed_repositories = []
                    resuming_window = True

                completed = set(state.completed_repositories)
                for repository in state.window_repositories:
                    if repository in completed:
                        continue
                    repository_records, pending = self._collect_repository_window(
                        repository,
                        _parse_instant(state.cursor),
                        _parse_instant(state.cursor) + timedelta(hours=1),
                    )
                    records.extend(repository_records)
                    _merge_pending_runs(state, pending)
                    state.completed_repositories.append(repository)
                    completed_repositories += 1

                state.cursor = _format_instant(
                    _parse_instant(state.cursor) + timedelta(hours=1)
                )
                state.window_repositories = []
                state.completed_repositories = []
                resuming_window = False
                completed_windows += 1
        except RateLimitExhausted:
            return CollectionResult(
                records=records,
                paused=True,
                completed_windows=completed_windows,
                completed_repositories=completed_repositories,
            )

        return CollectionResult(
            records=records,
            paused=False,
            completed_windows=completed_windows,
            completed_repositories=completed_repositories,
        )

    def _collect_pending_runs(
        self,
        state: CollectorState,
        pending: list[dict[str, Any]],
        records: list[dict[str, Any]],
    ) -> None:
        for item in pending:
            try:
                run = self._client.get_workflow_run(
                    self._org, item["repository"], item["run_id"]
                )
                if run.get("status") != "completed":
                    _merge_pending_runs(
                        state,
                        [_pending_run_attempt(item, self._now())],
                    )
                    continue
                run_records, terminal = self._collect_completed_run(
                    item["repository"], run
                )
                if terminal:
                    records.extend(run_records)
                    _remove_pending_run(state, item)
                else:
                    _merge_pending_runs(
                        state,
                        [_pending_run_attempt(item, self._now())],
                    )
            except RateLimitExhausted:
                raise
            except ApiError as error:
                _merge_pending_runs(
                    state,
                    [_pending_run_failure(item, error, self._now())],
                )

    def _collect_repository_window(
        self,
        repository: str,
        start: datetime,
        end: datetime,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        records: list[dict[str, Any]] = []
        pending: list[dict[str, Any]] = []
        runs = self._client.list_workflow_runs(
            self._org, repository, start, end
        )
        for run in runs:
            if run.get("status") != "completed":
                pending.append(_pending_run(repository, run))
                continue
            try:
                run_records, terminal = self._collect_completed_run(repository, run)
            except ApiError as error:
                pending.append(
                    _pending_run_failure(
                        _pending_run(repository, run),
                        error,
                        self._now(),
                    )
                )
                continue
            if terminal:
                records.extend(run_records)
            else:
                pending.append(_pending_run(repository, run))
        return records, pending

    def _collect_completed_run(
        self,
        repository: str,
        run: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], bool]:
        jobs = self._client.list_jobs(self._org, repository, run["id"])
        if any(job.get("status") != "completed" for job in jobs):
            return [], False
        collected_at = _format_instant(self._now())
        return [
            _job_record(self._org, repository, run, job, collected_at)
            for job in jobs
        ], True


def _pending_run(
    repository: str,
    run: dict[str, Any],
) -> dict[str, Any]:
    return {
        "repository": repository,
        "run_id": run["id"],
        "created_at": run.get("created_at"),
    }


def _pending_run_failure(
    item: dict[str, Any],
    error: ApiError,
    attempted_at: datetime,
) -> dict[str, Any]:
    return {
        **item,
        "failures": int(item.get("failures") or 0) + 1,
        "last_error": str(error)[:1000],
        "last_attempt_at": _format_instant(attempted_at),
    }


def _pending_run_attempt(
    item: dict[str, Any],
    attempted_at: datetime,
) -> dict[str, Any]:
    return {
        **item,
        "last_attempt_at": _format_instant(attempted_at),
    }


def _merge_pending_runs(
    state: CollectorState,
    additions: list[dict[str, Any]],
) -> None:
    merged = {
        (item["repository"], item["run_id"]): item
        for item in [*state.pending_runs, *additions]
    }
    state.pending_runs = sorted(
        merged.values(),
        key=lambda item: (item["repository"], item["run_id"]),
    )


def _remove_pending_run(
    state: CollectorState,
    completed: dict[str, Any],
) -> None:
    key = (completed["repository"], completed["run_id"])
    state.pending_runs = [
        item
        for item in state.pending_runs
        if (item["repository"], item["run_id"]) != key
    ]


def _job_record(
    org: str,
    repository: str,
    run: dict[str, Any],
    job: dict[str, Any],
    collected_at: str,
) -> dict[str, Any]:
    runner_assigned = bool(job.get("runner_name"))
    queue_seconds = None
    if runner_assigned and job.get("created_at") and job.get("started_at"):
        queue_seconds = (
            _parse_instant(job["started_at"])
            - _parse_instant(job["created_at"])
        ).total_seconds()

    head_repository = run.get("head_repository") or {}
    head_full_name = head_repository.get("full_name")
    from_fork = (
        head_full_name.lower() != f"{org}/{repository}".lower()
        if head_full_name
        else None
    )
    return {
        "schema_version": 1,
        "organization": org,
        "repository": repository,
        "repository_id": (run.get("repository") or {}).get("id"),
        "workflow_name": run.get("name") or job.get("workflow_name"),
        "workflow_id": run.get("workflow_id"),
        "run_id": run["id"],
        "run_attempt": job.get("run_attempt") or run.get("run_attempt"),
        "run_created_at": run.get("created_at"),
        "event": run.get("event"),
        "from_fork": from_fork,
        "head_branch": job.get("head_branch") or run.get("head_branch"),
        "head_sha": job.get("head_sha") or run.get("head_sha"),
        "job_id": job["id"],
        "job_name": job.get("name"),
        "job_status": job.get("status"),
        "job_conclusion": job.get("conclusion"),
        "job_created_at": job.get("created_at"),
        "job_started_at": job.get("started_at"),
        "job_completed_at": job.get("completed_at"),
        "queue_seconds": queue_seconds,
        "runner_assigned": runner_assigned,
        "runner_labels": job.get("labels") or [],
        "runner_name": job.get("runner_name"),
        "runner_group_name": job.get("runner_group_name"),
        "html_url": job.get("html_url"),
        "collected_at": collected_at,
    }


def load_state(path: Path, initial_start: datetime) -> CollectorState:
    if not path.exists():
        return CollectorState(cursor=_format_instant(_floor_hour(initial_start)))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"unable to read collector state from {path}: {error}") from error
    if data.get("version") != STATE_VERSION:
        raise ValueError(f"unsupported collector state version: {data.get('version')}")
    state = CollectorState(
        cursor=data["cursor"],
        window_repositories=list(data.get("window_repositories") or []),
        completed_repositories=list(data.get("completed_repositories") or []),
        pending_runs=list(data.get("pending_runs") or []),
    )
    _parse_instant(state.cursor)
    if not set(state.completed_repositories).issubset(state.window_repositories):
        raise ValueError("completed repositories are not in the saved window")
    return state


def write_state(path: Path, state: CollectorState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(state.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def write_records(
    output_dir: Path,
    collection_id: str,
    records: list[dict[str, Any]],
    collected_at: datetime,
) -> Path | None:
    if not records:
        return None
    if not COLLECTION_ID_PATTERN.fullmatch(collection_id):
        raise ValueError(
            "collection_id may contain only letters, digits, dots, underscores, and hyphens"
        )
    directory = output_dir / f"date={collected_at.astimezone(UTC):%Y-%m-%d}"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"collection-{collection_id}.jsonl.gz"
    if path.exists():
        raise FileExistsError(f"collection output already exists: {path}")

    with path.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
            with io.TextIOWrapper(compressed, encoding="utf-8", newline="\n") as text:
                for record in records:
                    text.write(
                        json.dumps(
                            record,
                            separators=(",", ":"),
                            sort_keys=True,
                        )
                    )
                    text.write("\n")
    return path


def _parse_rate_snapshot(data: dict[str, Any]) -> RateSnapshot:
    try:
        snapshot = RateSnapshot(
            limit=int(data["limit"]),
            remaining=int(data["remaining"]),
            reset=int(data["reset"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ApiError("REST rate-limit response has an invalid shape") from error
    if snapshot.limit <= 0 or snapshot.remaining < 0:
        raise ApiError("REST rate-limit response has invalid values")
    return snapshot


def _retry_delay(headers: dict[str, str], attempt: int) -> float:
    retry_after = headers.get("retry-after")
    if retry_after is not None:
        return max(0, int(retry_after))
    reset = headers.get("x-ratelimit-reset")
    remaining = headers.get("x-ratelimit-remaining")
    if reset is not None and remaining == "0":
        return max(0, int(reset) - int(time.time()))
    return min(15 * 60, 60 * 2**attempt)


def _is_secondary_rate_limit(response: ApiResponse) -> bool:
    if response.status == 429 or "retry-after" in response.headers:
        return True
    message = response.body.decode("utf-8", errors="replace").lower()
    return "secondary rate limit" in message or "abuse detection" in message


def _deduplicate_runs(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduplicated = {run["id"]: run for run in runs}
    return sorted(
        deduplicated.values(),
        key=lambda run: (run.get("created_at") or "", run["id"]),
    )


def _parse_instant(value: str) -> datetime:
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        raise ValueError(f"timestamp must include a time zone: {value}")
    return moment.astimezone(UTC)


def _format_instant(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _floor_hour(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def _token_from_environment() -> str:
    for name in ("GH_TOKEN", "GITHUB_AUTH_TOKEN", "GITHUB_TOKEN"):
        value = os.getenv(name)
        if value:
            return value
    raise ValueError("set GH_TOKEN, GITHUB_AUTH_TOKEN, or GITHUB_TOKEN")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect per-job GitHub Actions queue timing data."
    )
    parser.add_argument("--org", default="open-telemetry")
    parser.add_argument("--repository", action="append")
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--collection-id", required=True)
    parser.add_argument(
        "--start",
        type=_parse_instant,
        help="initial UTC cursor when the state file does not exist",
    )
    parser.add_argument("--max-windows", type=int, default=1)
    args = parser.parse_args()

    now = datetime.now(UTC)
    initial_start = args.start or (_floor_hour(now) - timedelta(hours=1))
    state = load_state(args.state, initial_start)
    client = GitHubClient(_token_from_environment())
    collector = QueueCollector(client, org=args.org)
    result = collector.collect(
        state,
        selected_repositories=args.repository,
        max_windows=args.max_windows,
    )
    output = write_records(
        args.output_dir,
        args.collection_id,
        result.records,
        now,
    )
    write_state(args.state, state)
    summary = {
        "completed_repositories": result.completed_repositories,
        "completed_windows": result.completed_windows,
        "cursor": state.cursor,
        "output": str(output) if output else None,
        "paused": result.paused,
        "pending_run_failures": sum(
            1 for item in state.pending_runs if item.get("last_error")
        ),
        "pending_runs": len(state.pending_runs),
        "rate_end": client.rate.to_dict(),
        "rate_low_watermark": client.minimum_rate.to_dict(),
        "rate_start": client.initial_rate.to_dict(),
        "records": len(result.records),
        "requests": client.request_count,
    }
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
