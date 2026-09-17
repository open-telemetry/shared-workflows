from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError
from urllib.parse import parse_qs, urlparse

from collect import (
    API_ROOT,
    ApiError,
    ApiResponse,
    BackfillState,
    CollectionBudgetExhausted,
    CollectorState,
    GitHubClient,
    QueueCollector,
    RateLimitExhausted,
    RateSnapshot,
    _job_record,
    _urlopen_transport,
    collect_live_and_backfill,
    load_backfill_state,
    load_state,
    write_records,
    write_state,
)


RATE_HEADERS = {
    "x-ratelimit-limit": "5000",
    "x-ratelimit-remaining": "4999",
    "x-ratelimit-reset": "2000000000",
}


def response(body, *, remaining=4999):
    headers = dict(RATE_HEADERS)
    headers["x-ratelimit-remaining"] = str(remaining)
    return ApiResponse(200, headers, json.dumps(body).encode())


class FakeTransport:
    def __init__(self, handler):
        self.handler = handler
        self.urls = []

    def __call__(self, url, _headers):
        self.urls.append(url)
        if url == f"{API_ROOT}/rate_limit":
            return response(
                {
                    "resources": {
                        "core": {
                            "limit": 5000,
                            "remaining": 4999,
                            "reset": 2000000000,
                        }
                    }
                }
            )
        return self.handler(url)


class GitHubClientTest(unittest.TestCase):
    def test_preserves_wrapped_connection_errors_for_retry(self):
        with patch(
            "collect.urlopen",
            side_effect=URLError(ConnectionResetError("connection reset")),
        ):
            with self.assertRaisesRegex(ConnectionResetError, "connection reset"):
                _urlopen_transport(f"{API_ROOT}/user", {})

    def test_lists_only_active_public_repositories(self):
        pages = {
            "1": [
                {"name": f"repo-{index}", "private": False, "archived": False}
                for index in range(100)
            ],
            "2": [
                {"name": "active", "private": False, "archived": False},
                {"name": "archived", "private": False, "archived": True},
                {"name": "private", "private": True, "archived": False},
                {
                    "name": "disabled",
                    "private": False,
                    "archived": False,
                    "disabled": True,
                },
            ],
        }

        def handler(url):
            query = parse_qs(urlparse(url).query)
            return response(pages[query["page"][0]])

        transport = FakeTransport(handler)
        client = GitHubClient("token", transport=transport)

        repositories = client.resolve_repositories("open-telemetry")

        self.assertEqual(101, len(repositories))
        self.assertIn("active", repositories)
        self.assertNotIn("archived", repositories)
        self.assertNotIn("private", repositories)
        self.assertNotIn("disabled", repositories)

    def test_paginates_all_jobs_and_requests_all_attempts(self):
        def handler(url):
            query = parse_qs(urlparse(url).query)
            page = int(query["page"][0])
            start = (page - 1) * 100
            count = 100 if page < 3 else 50
            return response(
                {
                    "total_count": 250,
                    "jobs": [{"id": index} for index in range(start, start + count)],
                }
            )

        transport = FakeTransport(handler)
        client = GitHubClient("token", transport=transport)

        jobs = client.list_jobs("open-telemetry", "example", 7)

        self.assertEqual(250, len(jobs))
        job_urls = [url for url in transport.urls if "/jobs?" in url]
        self.assertEqual(3, len(job_urls))
        self.assertTrue(all("filter=all" in url for url in job_urls))

    def test_splits_run_searches_above_one_thousand(self):
        def handler(url):
            query = parse_qs(urlparse(url).query)
            created = query["created"][0]
            if created.startswith("2026-09-15T00:00:00Z") and created.endswith(
                "2026-09-15T00:59:59Z"
            ):
                return response({"total_count": 1001, "workflow_runs": []})
            run_id = 1 if "00:29:59Z" in created else 2
            return response(
                {
                    "total_count": 1,
                    "workflow_runs": [
                        {
                            "id": run_id,
                            "created_at": f"2026-09-15T00:{run_id:02d}:00Z",
                        }
                    ],
                }
            )

        transport = FakeTransport(handler)
        client = GitHubClient("token", transport=transport)

        runs = client.list_workflow_runs(
            "open-telemetry",
            "example",
            datetime(2026, 9, 15, tzinfo=UTC),
            datetime(2026, 9, 15, 1, tzinfo=UTC),
        )

        self.assertEqual([1, 2], [run["id"] for run in runs])
        self.assertEqual(
            3,
            len([url for url in transport.urls if "/actions/runs?" in url]),
        )

    def test_stops_before_requesting_when_limit_is_exhausted(self):
        transport = FakeTransport(lambda _url: response({}))
        client = GitHubClient("token", transport=transport)
        client._rate = RateSnapshot(limit=5000, remaining=0, reset=2000000000)

        with self.assertRaisesRegex(RateLimitExhausted, "exhausted"):
            client.get_json("/user")

        self.assertEqual([], transport.urls)

    def test_does_not_retry_permission_error_as_secondary_limit(self):
        def handler(_url):
            return ApiResponse(
                403,
                RATE_HEADERS,
                b'{"message":"Resource not accessible by integration"}',
            )

        transport = FakeTransport(handler)
        client = GitHubClient(
            "token",
            transport=transport,
            sleep=lambda _seconds: self.fail("permission error was retried"),
        )

        with self.assertRaisesRegex(ApiError, "Resource not accessible"):
            client.get_json("/user")

        self.assertEqual(2, len(transport.urls))

    def test_retries_truncated_and_disconnected_responses(self):
        failures = (
            http.client.IncompleteRead(b"", 10),
            http.client.RemoteDisconnected("connection closed"),
        )

        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                attempts = 0
                sleeps = []

                def handler(_url):
                    nonlocal attempts
                    attempts += 1
                    if attempts == 1:
                        raise failure
                    return response({"login": "octocat"})

                transport = FakeTransport(handler)
                client = GitHubClient(
                    "token",
                    transport=transport,
                    sleep=sleeps.append,
                )

                self.assertEqual({"login": "octocat"}, client.get_json("/user"))
                self.assertEqual(2, attempts)
                self.assertEqual([1], sleeps)
                self.assertEqual(3, client.request_count)

    def test_transport_retry_exhaustion_raises_api_error(self):
        attempts = 0
        sleeps = []

        def handler(_url):
            nonlocal attempts
            attempts += 1
            raise http.client.IncompleteRead(b"", 10)

        transport = FakeTransport(handler)
        client = GitHubClient(
            "token",
            transport=transport,
            sleep=sleeps.append,
            max_retries=2,
        )

        with self.assertRaisesRegex(
            ApiError,
            r"transport failed after 3 attempts .*\/user",
        ):
            client.get_json("/user")

        self.assertEqual(3, attempts)
        self.assertEqual([1, 2], sleeps)
        self.assertEqual(4, client.request_count)

    def test_initial_rate_uses_first_data_response_headers(self):
        transport = FakeTransport(
            lambda _url: response({"login": "octocat"}, remaining=4200)
        )
        client = GitHubClient("token", transport=transport)

        client.get_json("/user")

        self.assertEqual(4201, client.initial_rate.remaining)
        self.assertEqual(4200, client.rate.remaining)

    def test_tracks_lowest_observed_remaining_value(self):
        remaining = iter((2600, 4000))
        transport = FakeTransport(lambda _url: response({}, remaining=next(remaining)))
        client = GitHubClient("token", transport=transport)

        client.get_json("/first")
        client.get_json("/second")
        self.assertEqual(2600, client.minimum_rate.remaining)
        self.assertEqual(2600, client.rate.remaining)

    def test_stops_at_request_budget(self):
        transport = FakeTransport(lambda _url: response({"login": "octocat"}))
        client = GitHubClient(
            "token",
            transport=transport,
            max_requests=2,
        )

        client.get_json("/first")
        with self.assertRaisesRegex(
            CollectionBudgetExhausted,
            "request_limit",
        ):
            client.get_json("/second")

        self.assertEqual(2, client.request_count)
        self.assertEqual(2, len(transport.urls))

    def test_counts_parallel_requests(self):
        transport = FakeTransport(lambda _url: response({}))
        client = GitHubClient("token", transport=transport)
        client.load_rate_limit()

        with ThreadPoolExecutor(max_workers=4) as executor:
            list(
                executor.map(
                    client.get_json,
                    [f"/item/{index}" for index in range(20)],
                )
            )

        self.assertEqual(21, client.request_count)

    def test_reserves_reported_quota_for_in_flight_request(self):
        request_started = threading.Event()
        release_request = threading.Event()

        def handler(_url):
            request_started.set()
            release_request.wait(timeout=1)
            return response({}, remaining=0)

        transport = FakeTransport(handler)
        client = GitHubClient("token", transport=transport)
        client._rate = RateSnapshot(limit=5000, remaining=1, reset=2000000000)

        with ThreadPoolExecutor(max_workers=1) as executor:
            request = executor.submit(client.get_json, "/first")
            self.assertTrue(request_started.wait(timeout=1))
            try:
                with self.assertRaisesRegex(RateLimitExhausted, "exhausted"):
                    client.get_json("/second")
            finally:
                release_request.set()
            request.result()

        self.assertEqual([f"{API_ROOT}/first"], transport.urls)

    def test_does_not_wait_past_runtime_deadline(self):
        transport = FakeTransport(
            lambda _url: ApiResponse(
                429,
                {**RATE_HEADERS, "retry-after": "10"},
                b'{"message":"secondary rate limit"}',
            )
        )
        client = GitHubClient(
            "token",
            transport=transport,
            monotonic=lambda: 0,
            sleep=lambda _seconds: self.fail("slept past deadline"),
            deadline=5,
        )

        with self.assertRaisesRegex(
            CollectionBudgetExhausted,
            "runtime_limit",
        ):
            client.get_json("/limited")


class JobRecordTest(unittest.TestCase):
    def setUp(self):
        self.run = {
            "id": 11,
            "name": "CI",
            "workflow_id": 12,
            "run_attempt": 1,
            "created_at": "2026-09-15T00:00:00Z",
            "event": "pull_request",
            "head_branch": "feature",
            "head_sha": "abc",
            "head_repository": {"full_name": "someone/fork"},
        }

    def test_calculates_queue_time_for_assigned_job(self):
        record = _job_record(
            "open-telemetry",
            "example",
            self.run,
            {
                "id": 21,
                "name": "test (3.13, ubuntu)",
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-09-15T00:00:10Z",
                "started_at": "2026-09-15T00:02:40Z",
                "completed_at": "2026-09-15T00:03:00Z",
                "runner_name": "GitHub Actions 1",
                "labels": ["ubuntu-latest"],
                "html_url": "https://github.com/open-telemetry/example/actions/runs/11/job/21",
            },
            "2026-09-15T01:00:00Z",
        )

        self.assertEqual(150.0, record["queue_seconds"])
        self.assertEqual(1, record["schema_version"])
        self.assertEqual("open-telemetry", record["organization"])
        self.assertTrue(record["runner_assigned"])
        self.assertTrue(record["from_fork"])
        self.assertEqual("test (3.13, ubuntu)", record["job_name"])

    def test_never_assigned_job_does_not_look_like_zero_wait(self):
        record = _job_record(
            "open-telemetry",
            "example",
            self.run,
            {
                "id": 22,
                "status": "completed",
                "created_at": "2026-09-15T00:00:10Z",
                "started_at": "2026-09-15T00:00:10Z",
                "runner_name": None,
            },
            "2026-09-15T01:00:00Z",
        )

        self.assertIsNone(record["queue_seconds"])
        self.assertFalse(record["runner_assigned"])

    def test_unknown_head_repository_preserves_unknown_fork_origin(self):
        self.run["head_repository"] = None

        record = _job_record(
            "open-telemetry",
            "example",
            self.run,
            {
                "id": 22,
                "status": "completed",
            },
            "2026-09-15T01:00:00Z",
        )

        self.assertIsNone(record["from_fork"])


class FakeClient:
    def __init__(self):
        self.rate = RateSnapshot(5000, 4990, 2000000000)
        self.request_count = 0
        self.pause_on_repo = None
        self.fail_jobs_for_run = None
        self.completed_runs = {}

    def load_rate_limit(self):
        return self.rate

    def resolve_repositories(self, _org, selected):
        return selected or ["a", "b"]

    def get_workflow_run(self, _org, repository, run_id):
        return self.completed_runs[(repository, run_id)]

    def list_workflow_runs(self, _org, repository, _start, _end):
        if repository == self.pause_on_repo:
            raise RateLimitExhausted("limit")
        return [
            {
                "id": 100 if repository == "a" else 200,
                "name": "CI",
                "workflow_id": 1,
                "run_attempt": 1,
                "created_at": "2026-09-15T00:10:00Z",
                "status": "completed",
                "event": "push",
                "head_repository": {"full_name": f"open-telemetry/{repository}"},
            }
        ]

    def list_jobs(self, _org, repository, run_id):
        if run_id == self.fail_jobs_for_run:
            raise ApiError("GitHub API returned 502")
        return [
            {
                "id": run_id + 1,
                "name": "test",
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-09-15T00:10:00Z",
                "started_at": "2026-09-15T00:11:00Z",
                "completed_at": "2026-09-15T00:12:00Z",
                "runner_name": "runner",
                "labels": ["ubuntu-latest"],
                "html_url": f"https://example/{repository}/{run_id}",
            }
        ]


class QueueCollectorTest(unittest.TestCase):
    def test_cleanly_pauses_when_initial_rate_limit_is_exhausted(self):
        class ExhaustedClient(FakeClient):
            def load_rate_limit(self):
                raise RateLimitExhausted("limit")

        collector = QueueCollector(
            ExhaustedClient(),
            org="open-telemetry",
            now=lambda: datetime(2026, 9, 15, 2, tzinfo=UTC),
        )
        state = CollectorState(cursor="2026-09-15T00:00:00Z")

        result = collector.collect(state)

        self.assertTrue(result.paused)
        self.assertEqual([], result.records)
        self.assertEqual("2026-09-15T00:00:00Z", state.cursor)

    def test_checkpoints_completed_repositories_when_rate_limit_is_exhausted(self):
        client = FakeClient()
        client.pause_on_repo = "b"
        collector = QueueCollector(
            client,
            org="open-telemetry",
            now=lambda: datetime(2026, 9, 15, 2, tzinfo=UTC),
        )
        state = CollectorState(cursor="2026-09-15T00:00:00Z")

        result = collector.collect(state)

        self.assertTrue(result.paused)
        self.assertEqual(["a"], state.completed_repositories)
        self.assertEqual("2026-09-15T00:00:00Z", state.cursor)
        self.assertEqual([101], [record["job_id"] for record in result.records])

        client.pause_on_repo = None
        resumed = collector.collect(state)

        self.assertFalse(resumed.paused)
        self.assertEqual("2026-09-15T01:00:00Z", state.cursor)
        self.assertEqual([], state.completed_repositories)
        self.assertEqual([201], [record["job_id"] for record in resumed.records])

    def test_reconciles_resumed_state_with_current_repositories(self):
        client = FakeClient()
        client.pause_on_repo = "a"
        collector = QueueCollector(
            client,
            org="open-telemetry",
            now=lambda: datetime(2026, 9, 15, 1, tzinfo=UTC),
        )
        state = CollectorState(
            cursor="2026-09-15T00:00:00Z",
            window_repositories=["a", "removed"],
            completed_repositories=["removed"],
            pending_runs=[
                {
                    "repository": "removed",
                    "run_id": 9,
                    "created_at": "2026-09-14T23:00:00Z",
                }
            ],
        )

        result = collector.collect(state, selected_repositories=["a"])

        self.assertTrue(result.paused)
        self.assertEqual(["a"], state.window_repositories)
        self.assertEqual([], state.completed_repositories)
        self.assertEqual([], state.pending_runs)

    def test_does_not_reinitialize_empty_reconciled_window(self):
        collector = QueueCollector(
            FakeClient(),
            org="open-telemetry",
            now=lambda: datetime(2026, 9, 15, 1, tzinfo=UTC),
        )
        state = CollectorState(
            cursor="2026-09-15T00:00:00Z",
            window_repositories=["removed"],
        )

        result = collector.collect(state, selected_repositories=["a"])

        self.assertFalse(result.paused)
        self.assertEqual([], result.records)
        self.assertEqual("2026-09-15T01:00:00Z", state.cursor)

    def test_revisits_pending_run_until_it_is_terminal(self):
        client = FakeClient()
        client.completed_runs[("a", 9)] = {
            "id": 9,
            "name": "CI",
            "workflow_id": 1,
            "run_attempt": 1,
            "created_at": "2026-09-14T23:00:00Z",
            "status": "completed",
            "event": "push",
            "head_repository": {"full_name": "open-telemetry/a"},
        }
        collector = QueueCollector(
            client,
            org="open-telemetry",
            now=lambda: datetime(2026, 9, 15, 1, tzinfo=UTC),
        )
        state = CollectorState(
            cursor="2026-09-15T01:00:00Z",
            pending_runs=[
                {
                    "repository": "a",
                    "run_id": 9,
                    "created_at": "2026-09-14T23:00:00Z",
                }
            ],
        )

        result = collector.collect(state, selected_repositories=["a"])

        self.assertEqual([10], [record["job_id"] for record in result.records])
        self.assertEqual([], state.pending_runs)

    def test_retries_pending_runs_before_window_collection_can_pause(self):
        client = FakeClient()
        client.pause_on_repo = "a"
        client.completed_runs[("a", 9)] = {
            "id": 9,
            "name": "CI",
            "workflow_id": 1,
            "run_attempt": 1,
            "created_at": "2026-09-14T23:00:00Z",
            "status": "completed",
            "event": "push",
            "head_repository": {"full_name": "open-telemetry/a"},
        }
        collector = QueueCollector(
            client,
            org="open-telemetry",
            now=lambda: datetime(2026, 9, 15, 1, tzinfo=UTC),
        )
        state = CollectorState(
            cursor="2026-09-15T00:00:00Z",
            pending_runs=[
                {
                    "repository": "a",
                    "run_id": 9,
                    "created_at": "2026-09-14T23:00:00Z",
                }
            ],
        )

        result = collector.collect(state, selected_repositories=["a"])

        self.assertTrue(result.paused)
        self.assertEqual([10], [record["job_id"] for record in result.records])
        self.assertEqual([], state.pending_runs)

    def test_retries_old_attempted_run_before_new_unattempted_runs(self):
        class TrackingClient(FakeClient):
            def __init__(self):
                super().__init__()
                self.retried = []

            def get_workflow_run(self, org, repository, run_id):
                self.retried.append(run_id)
                return {
                    "id": run_id,
                    "status": "in_progress",
                }

        client = TrackingClient()
        collector = QueueCollector(
            client,
            org="open-telemetry",
            now=lambda: datetime(2026, 9, 15, 1, tzinfo=UTC),
        )
        state = CollectorState(
            cursor="2026-09-15T01:00:00Z",
            pending_runs=[
                {
                    "repository": "a",
                    "run_id": 1,
                    "created_at": "2026-09-14T22:00:00Z",
                    "last_attempt_at": "2026-09-15T00:30:00Z",
                },
                *[
                    {
                        "repository": "a",
                        "run_id": run_id,
                        "created_at": "2026-09-15T00:45:00Z",
                    }
                    for run_id in range(2, 27)
                ],
            ],
        )

        collector.collect(state, selected_repositories=["a"])

        self.assertEqual([1, *range(2, 26)], client.retried)
        self.assertNotIn(
            "last_attempt_at",
            next(item for item in state.pending_runs if item["run_id"] == 26),
        )

    def test_checkpoints_pending_runs_before_rate_limit_pause(self):
        class PausingClient(FakeClient):
            def get_workflow_run(self, org, repository, run_id):
                if run_id == 10:
                    raise RateLimitExhausted("limit")
                return super().get_workflow_run(org, repository, run_id)

        client = PausingClient()
        client.completed_runs[("a", 9)] = {
            "id": 9,
            "name": "CI",
            "workflow_id": 1,
            "run_attempt": 1,
            "created_at": "2026-09-14T23:00:00Z",
            "status": "completed",
            "event": "push",
            "head_repository": {"full_name": "open-telemetry/a"},
        }
        collector = QueueCollector(
            client,
            org="open-telemetry",
            now=lambda: datetime(2026, 9, 15, 1, tzinfo=UTC),
        )
        state = CollectorState(
            cursor="2026-09-15T01:00:00Z",
            pending_runs=[
                {
                    "repository": "a",
                    "run_id": 9,
                    "created_at": "2026-09-14T23:00:00Z",
                },
                {
                    "repository": "a",
                    "run_id": 10,
                    "created_at": "2026-09-14T23:30:00Z",
                },
            ],
        )

        result = collector.collect(state, selected_repositories=["a"])

        self.assertTrue(result.paused)
        self.assertEqual([10], [record["job_id"] for record in result.records])
        self.assertEqual([10], [item["run_id"] for item in state.pending_runs])

    def test_job_api_failure_does_not_block_window_progress(self):
        client = FakeClient()
        client.fail_jobs_for_run = 100
        collector = QueueCollector(
            client,
            org="open-telemetry",
            now=lambda: datetime(2026, 9, 15, 1, tzinfo=UTC),
        )
        state = CollectorState(cursor="2026-09-15T00:00:00Z")

        result = collector.collect(
            state,
            selected_repositories=["a"],
        )

        self.assertFalse(result.paused)
        self.assertEqual("2026-09-15T01:00:00Z", state.cursor)
        self.assertEqual([], result.records)
        self.assertEqual(1, len(state.pending_runs))
        self.assertEqual(1, state.pending_runs[0]["failures"])
        self.assertIn("502", state.pending_runs[0]["last_error"])

    def test_backfills_newest_windows_first(self):
        class WindowClient(FakeClient):
            def __init__(self):
                super().__init__()
                self.windows = []

            def list_workflow_runs(self, org, repository, start, end):
                self.windows.append((repository, start, end))
                return super().list_workflow_runs(
                    org,
                    repository,
                    start,
                    end,
                )

        client = WindowClient()
        collector = QueueCollector(
            client,
            org="open-telemetry",
            now=lambda: datetime(2026, 9, 15, 2, tzinfo=UTC),
        )
        state = BackfillState(
            initial_cursor="2026-09-15T02:00:00Z",
            target="2026-09-15T00:00:00Z",
            cursor="2026-09-15T02:00:00Z",
        )

        result = collector.collect_backfill(
            state,
            selected_repositories=["a"],
            max_windows=2,
        )

        self.assertFalse(result.paused)
        self.assertEqual(2, result.completed_windows)
        self.assertEqual("2026-09-15T00:00:00Z", state.cursor)
        self.assertEqual(
            [
                (
                    "a",
                    datetime(2026, 9, 15, 1, tzinfo=UTC),
                    datetime(2026, 9, 15, 2, tzinfo=UTC),
                ),
                (
                    "a",
                    datetime(2026, 9, 15, 0, tzinfo=UTC),
                    datetime(2026, 9, 15, 1, tzinfo=UTC),
                ),
            ],
            client.windows,
        )

    def test_backfill_resumes_partial_window(self):
        client = FakeClient()
        client.pause_on_repo = "b"
        collector = QueueCollector(
            client,
            org="open-telemetry",
            now=lambda: datetime(2026, 9, 15, 2, tzinfo=UTC),
        )
        state = BackfillState(
            initial_cursor="2026-09-15T02:00:00Z",
            target="2026-09-15T00:00:00Z",
            cursor="2026-09-15T02:00:00Z",
        )

        first = collector.collect_backfill(
            state,
            selected_repositories=["a", "b"],
        )

        self.assertTrue(first.paused)
        self.assertEqual("rate_limit", first.pause_reason)
        self.assertEqual(["a"], state.completed_repositories)
        self.assertEqual("2026-09-15T02:00:00Z", state.cursor)

        client.pause_on_repo = None
        resumed = collector.collect_backfill(
            state,
            selected_repositories=["a", "b"],
        )

        self.assertFalse(resumed.paused)
        self.assertEqual("2026-09-15T01:00:00Z", state.cursor)
        self.assertEqual([], state.completed_repositories)

    def test_parallelizes_repository_collection(self):
        class ParallelClient(FakeClient):
            def __init__(self):
                super().__init__()
                self.barrier = threading.Barrier(2)

            def list_workflow_runs(self, org, repository, start, end):
                self.barrier.wait(timeout=1)
                return super().list_workflow_runs(
                    org,
                    repository,
                    start,
                    end,
                )

        collector = QueueCollector(
            ParallelClient(),
            org="open-telemetry",
            now=lambda: datetime(2026, 9, 15, 1, tzinfo=UTC),
        )
        state = CollectorState(cursor="2026-09-15T00:00:00Z")

        result = collector.collect(
            state,
            selected_repositories=["a", "b"],
            max_workers=2,
        )

        self.assertFalse(result.paused)
        self.assertEqual([101, 201], [record["job_id"] for record in result.records])

    def test_collects_live_window_before_backfill(self):
        class WindowClient(FakeClient):
            def __init__(self):
                super().__init__()
                self.windows = []

            def list_workflow_runs(self, org, repository, start, end):
                self.windows.append((start, end))
                return super().list_workflow_runs(
                    org,
                    repository,
                    start,
                    end,
                )

        client = WindowClient()
        collector = QueueCollector(
            client,
            org="open-telemetry",
            now=lambda: datetime(2026, 9, 15, 2, tzinfo=UTC),
        )
        live_state = CollectorState(cursor="2026-09-15T01:00:00Z")
        backfill_state = BackfillState(
            initial_cursor="2026-09-15T01:00:00Z",
            target="2026-09-14T23:00:00Z",
            cursor="2026-09-15T01:00:00Z",
        )

        result = collect_live_and_backfill(
            collector,
            live_state,
            backfill_state,
            now=datetime(2026, 9, 15, 2, tzinfo=UTC),
            selected_repositories=["a"],
            max_windows=2,
        )

        self.assertEqual(1, result.live.completed_windows)
        self.assertEqual(1, result.backfill.completed_windows)
        self.assertEqual(
            [
                (
                    datetime(2026, 9, 15, 1, tzinfo=UTC),
                    datetime(2026, 9, 15, 2, tzinfo=UTC),
                ),
                (
                    datetime(2026, 9, 15, 0, tzinfo=UTC),
                    datetime(2026, 9, 15, 1, tzinfo=UTC),
                ),
            ],
            client.windows,
        )

    def test_live_backlog_uses_all_windows_before_backfill(self):
        class WindowClient(FakeClient):
            def __init__(self):
                super().__init__()
                self.windows = []

            def list_workflow_runs(self, org, repository, start, end):
                self.windows.append((start, end))
                return super().list_workflow_runs(
                    org,
                    repository,
                    start,
                    end,
                )

        client = WindowClient()
        collector = QueueCollector(
            client,
            org="open-telemetry",
            now=lambda: datetime(2026, 9, 15, 2, tzinfo=UTC),
        )
        live_state = CollectorState(cursor="2026-09-15T00:00:00Z")
        backfill_state = BackfillState(
            initial_cursor="2026-09-15T00:00:00Z",
            target="2026-09-14T23:00:00Z",
            cursor="2026-09-15T00:00:00Z",
        )

        result = collect_live_and_backfill(
            collector,
            live_state,
            backfill_state,
            now=datetime(2026, 9, 15, 2, tzinfo=UTC),
            selected_repositories=["a"],
            max_windows=2,
        )

        self.assertEqual(2, result.live.completed_windows)
        self.assertIsNone(result.backfill)
        self.assertEqual(
            [
                (
                    datetime(2026, 9, 15, 0, tzinfo=UTC),
                    datetime(2026, 9, 15, 1, tzinfo=UTC),
                ),
                (
                    datetime(2026, 9, 15, 1, tzinfo=UTC),
                    datetime(2026, 9, 15, 2, tzinfo=UTC),
                ),
            ],
            client.windows,
        )

    def test_skips_completed_backfill(self):
        class WindowClient(FakeClient):
            def __init__(self):
                super().__init__()
                self.windows = []

            def list_workflow_runs(self, org, repository, start, end):
                self.windows.append((start, end))
                return super().list_workflow_runs(
                    org,
                    repository,
                    start,
                    end,
                )

        client = WindowClient()
        collector = QueueCollector(
            client,
            org="open-telemetry",
            now=lambda: datetime(2026, 9, 15, 2, tzinfo=UTC),
        )
        live_state = CollectorState(cursor="2026-09-15T02:00:00Z")
        backfill_state = BackfillState(
            initial_cursor="2026-09-15T01:00:00Z",
            target="2026-09-15T00:00:00Z",
            cursor="2026-09-15T00:00:00Z",
        )

        result = collect_live_and_backfill(
            collector,
            live_state,
            backfill_state,
            now=datetime(2026, 9, 15, 2, tzinfo=UTC),
            selected_repositories=["a"],
            max_windows=2,
        )

        self.assertIsNone(result.backfill)
        self.assertEqual([], client.windows)

    def test_checkpoints_repository_progress_at_request_budget(self):
        class BudgetClient(FakeClient):
            def list_workflow_runs(self, org, repository, start, end):
                if repository == "b":
                    raise CollectionBudgetExhausted("request_limit")
                return super().list_workflow_runs(
                    org,
                    repository,
                    start,
                    end,
                )

        collector = QueueCollector(
            BudgetClient(),
            org="open-telemetry",
            now=lambda: datetime(2026, 9, 15, 1, tzinfo=UTC),
        )
        state = CollectorState(cursor="2026-09-15T00:00:00Z")

        result = collector.collect(
            state,
            selected_repositories=["a", "b"],
        )

        self.assertTrue(result.paused)
        self.assertEqual("request_limit", result.pause_reason)
        self.assertEqual(["a"], state.completed_repositories)
        self.assertEqual("2026-09-15T00:00:00Z", state.cursor)


class PersistenceTest(unittest.TestCase):
    def test_round_trips_state_and_writes_gzip_json_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "state.json"
            state = CollectorState(
                cursor="2026-09-15T00:00:00Z",
                window_repositories=["a"],
                completed_repositories=["a"],
            )
            write_state(state_path, state)
            loaded = load_state(state_path, datetime(2026, 9, 1, tzinfo=UTC))
            self.assertEqual(state.to_dict(), loaded.to_dict())

            output = write_records(
                root / "jobs",
                "run-1",
                [{"job_id": 1}, {"job_id": 2}],
                datetime(2026, 9, 15, tzinfo=UTC),
            )
            self.assertIsNotNone(output)
            import gzip

            with gzip.open(output, "rt", encoding="utf-8") as source:
                self.assertEqual(
                    [{"job_id": 1}, {"job_id": 2}],
                    [json.loads(line) for line in source],
                )

    def test_round_trips_backfill_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "backfill-state.json"
            state = load_backfill_state(
                path,
                datetime(2026, 9, 17, 2, tzinfo=UTC),
                datetime(2026, 1, 1, tzinfo=UTC),
            )
            state.cursor = "2026-09-17T01:00:00Z"
            state.window_repositories = ["a"]
            state.completed_repositories = ["a"]
            write_state(path, state)

            loaded = load_backfill_state(
                path,
                datetime(2026, 9, 17, 2, tzinfo=UTC),
                datetime(2026, 1, 1, tzinfo=UTC),
            )

            self.assertEqual(state.to_dict(), loaded.to_dict())

    def test_rejects_backfill_cursor_outside_fixed_range(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "backfill-state.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "initial_cursor": "2026-09-17T02:00:00Z",
                        "target": "2026-01-01T00:00:00Z",
                        "cursor": "2025-12-31T23:00:00Z",
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "outside"):
                load_backfill_state(
                    path,
                    datetime(2026, 9, 17, 2, tzinfo=UTC),
                    datetime(2026, 1, 1, tzinfo=UTC),
                )


if __name__ == "__main__":
    unittest.main()
