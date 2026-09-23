from __future__ import annotations

import gzip
import json
import tempfile
import tracemalloc
import unittest
from pathlib import Path

from migrate_queue_records import migrate_collections
from rerun_carry_forwards import CarryForwardError, filter_rerun_carry_forwards


def job(
    job_id: int,
    attempt: int,
    created_at: str,
    started_at: str,
    *,
    completed_at: str = "2026-09-18T11:28:16Z",
) -> dict:
    return {
        "id": job_id,
        "name": "setup-environment",
        "run_attempt": attempt,
        "created_at": created_at,
        "started_at": started_at,
        "completed_at": completed_at,
        "conclusion": "success",
        "runner_name": "GitHub Actions 1",
        "runner_group_name": "GitHub Actions",
        "labels": ["ubuntu-24.04"],
    }


def record(item: dict) -> dict:
    return {
        "schema_version": 1,
        "organization": "open-telemetry",
        "repository": "example",
        "run_id": 10,
        "run_attempt": item["run_attempt"],
        "job_id": item["id"],
        "job_name": item["name"],
        "job_created_at": item["created_at"],
        "job_started_at": item["started_at"],
        "job_completed_at": item["completed_at"],
        "job_conclusion": item["conclusion"],
        "queue_seconds": (
            _seconds(item["started_at"]) - _seconds(item["created_at"])
        ),
        "runner_assigned": True,
        "runner_name": item["runner_name"],
        "runner_group_name": item["runner_group_name"],
        "runner_labels": item["labels"],
    }


def runnerless_record(job_id: int) -> dict:
    return {
        "schema_version": 1,
        "organization": "open-telemetry",
        "repository": "example",
        "run_id": 10,
        "run_attempt": 1,
        "job_id": job_id,
        "job_name": "skipped",
        "job_created_at": "2026-09-18T11:24:57Z",
        "job_started_at": "2026-09-18T11:24:57Z",
        "job_completed_at": "2026-09-18T11:24:57Z",
        "job_conclusion": "skipped",
        "queue_seconds": None,
        "runner_assigned": False,
        "runner_name": None,
        "runner_group_name": None,
        "runner_labels": ["ubuntu-24.04"],
    }


def _seconds(value: str) -> int:
    hours, minutes, seconds = value[11:19].split(":")
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds)


def write_collection(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
            for item in records:
                compressed.write(
                    json.dumps(item, separators=(",", ":"), sort_keys=True).encode()
                )
                compressed.write(b"\n")


class CarryForwardFilterTest(unittest.TestCase):
    def setUp(self):
        self.original = job(
            1,
            1,
            "2026-09-18T11:24:57Z",
            "2026-09-18T11:24:59Z",
        )
        self.clone = job(
            2,
            2,
            "2026-09-18T12:00:51Z",
            "2026-09-18T11:24:59Z",
        )

    def test_removes_verified_carry_forward(self):
        kept, removed = filter_rerun_carry_forwards(
            [self.original, self.clone]
        )

        self.assertEqual([1], [item["id"] for item in kept])
        self.assertEqual([2], [item["id"] for item in removed])

    def test_keeps_job_that_actually_reran(self):
        rerun = job(
            3,
            2,
            "2026-09-18T12:00:51Z",
            "2026-09-18T12:01:10Z",
            completed_at="2026-09-18T12:02:00Z",
        )

        kept, removed = filter_rerun_carry_forwards(
            [self.original, self.clone, rerun]
        )

        self.assertEqual([1, 3], [item["id"] for item in kept])
        self.assertEqual([2], [item["id"] for item in removed])

    def test_removes_clone_chain_using_only_original_execution(self):
        later_clone = dict(
            self.clone,
            id=4,
            run_attempt=3,
            created_at="2026-09-18T12:30:00Z",
        )

        kept, removed = filter_rerun_carry_forwards(
            [self.original, self.clone, later_clone]
        )

        self.assertEqual([1], [item["id"] for item in kept])
        self.assertEqual([2, 4], [item["id"] for item in removed])

    def test_rejects_unmatched_negative_job(self):
        with self.assertRaisesRegex(
            CarryForwardError,
            "0 matching earlier executions",
        ):
            filter_rerun_carry_forwards([self.clone])

    def test_rejects_ambiguous_original_execution(self):
        duplicate_original = dict(self.original, id=5)

        with self.assertRaisesRegex(
            CarryForwardError,
            "2 matching earlier executions",
        ):
            filter_rerun_carry_forwards(
                [self.original, duplicate_original, self.clone]
            )

    def test_accepts_one_second_completion_drift_for_unique_original(self):
        clone = dict(self.clone, completed_at="2026-09-18T11:28:17Z")

        kept, removed = filter_rerun_carry_forwards([self.original, clone])

        self.assertEqual([1], [item["id"] for item in kept])
        self.assertEqual([2], [item["id"] for item in removed])

    def test_rejects_drift_without_unique_original(self):
        clone = dict(self.clone, completed_at="2026-09-18T11:28:17Z")
        duplicate = dict(self.original, id=5)
        with self.assertRaisesRegex(CarryForwardError, "2 matching earlier"):
            filter_rerun_carry_forwards([self.original, duplicate, clone])

        clone["completed_at"] = "2026-09-18T11:28:18Z"
        with self.assertRaisesRegex(CarryForwardError, "0 matching earlier"):
            filter_rerun_carry_forwards([self.original, clone])


class CarryForwardMigrationTest(unittest.TestCase):
    def setUp(self):
        self.original = job(
            1,
            1,
            "2026-09-18T11:24:57Z",
            "2026-09-18T11:24:59Z",
        )
        self.clone = job(
            2,
            2,
            "2026-09-18T12:00:51Z",
            "2026-09-18T11:24:59Z",
        )

    def test_migrates_deterministically_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = (
                Path(directory)
                / "jobs"
                / "date=2026-09-18"
                / "collection-1.jsonl.gz"
            )
            original_record = record(self.original)
            write_collection(
                path,
                [original_record, record(self.clone), runnerless_record(3)],
            )

            summary = migrate_collections(Path(directory) / "jobs")
            first_bytes = path.read_bytes()
            second_summary = migrate_collections(Path(directory) / "jobs")

            with gzip.open(path, "rt", encoding="utf-8") as source:
                migrated = [json.loads(line) for line in source]
            self.assertEqual([original_record], migrated)
            self.assertEqual(1, summary["files_changed"])
            self.assertEqual(2, summary["records_removed"])
            self.assertEqual(1, summary["carry_forward_records_removed"])
            self.assertEqual(1, summary["runnerless_records_removed"])
            self.assertEqual(0, second_summary["files_changed"])
            self.assertEqual(0, second_summary["records_removed"])
            self.assertEqual(first_bytes, path.read_bytes())
            self.assertEqual(b"\0\0\0\0", first_bytes[4:8])

    def test_preflights_all_files_before_rewriting(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs = Path(directory) / "jobs"
            valid_path = jobs / "date=2026-09-18" / "collection-1.jsonl.gz"
            invalid_path = jobs / "date=2026-09-18" / "collection-2.jsonl.gz"
            write_collection(
                valid_path,
                [record(self.original), record(self.clone)],
            )
            unmatched_clone = dict(
                self.clone,
                id=3,
                started_at="2026-09-18T11:24:58Z",
            )
            write_collection(invalid_path, [record(unmatched_clone)])
            original_bytes = valid_path.read_bytes()

            with self.assertRaisesRegex(
                CarryForwardError,
                "0 matching earlier executions",
            ):
                migrate_collections(jobs)

            self.assertEqual(original_bytes, valid_path.read_bytes())

    def test_matches_carry_forward_across_collection_files(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs = Path(directory) / "jobs"
            original_path = (
                jobs / "date=2026-09-18" / "collection-1.jsonl.gz"
            )
            clone_path = jobs / "date=2026-09-18" / "collection-2.jsonl.gz"
            original_record = record(self.original)
            write_collection(original_path, [original_record])
            write_collection(clone_path, [record(self.clone)])

            summary = migrate_collections(jobs)

            with gzip.open(original_path, "rt", encoding="utf-8") as source:
                self.assertEqual(
                    [original_record],
                    [json.loads(line) for line in source],
                )
            with gzip.open(clone_path, "rt", encoding="utf-8") as source:
                self.assertEqual([], list(source))
            self.assertEqual(1, summary["carry_forward_records_removed"])
            self.assertEqual(1, summary["files_changed"])

    def test_migrates_clone_with_one_second_completion_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs = Path(directory) / "jobs"
            path = jobs / "date=2026-09-18" / "collection-1.jsonl.gz"
            clone = dict(self.clone, completed_at="2026-09-18T11:28:17Z")
            write_collection(path, [record(self.original), record(clone)])

            summary = migrate_collections(jobs)

            with gzip.open(path, "rt", encoding="utf-8") as source:
                self.assertEqual(
                    [record(self.original)],
                    [json.loads(line) for line in source],
                )
            self.assertEqual(1, summary["carry_forward_records_removed"])

    def test_rejects_duplicate_job_ids_before_rewriting(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs = Path(directory) / "jobs"
            first = jobs / "date=2026-09-18" / "collection-1.jsonl.gz"
            second = jobs / "date=2026-09-18" / "collection-2.jsonl.gz"
            write_collection(first, [record(self.original)])
            write_collection(second, [record(self.original)])

            with self.assertRaisesRegex(
                CarryForwardError,
                "duplicate job_id in collections: 1",
            ):
                migrate_collections(jobs)

    def test_rejects_negative_queue_value_without_negative_interval(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs = Path(directory) / "jobs"
            first = jobs / "date=2026-09-18" / "collection-1.jsonl.gz"
            second = jobs / "date=2026-09-18" / "collection-2.jsonl.gz"
            write_collection(first, [runnerless_record(3)])
            inconsistent = dict(record(self.original), job_id=4, queue_seconds=-1)
            write_collection(second, [inconsistent])
            original_bytes = first.read_bytes()

            with self.assertRaisesRegex(
                CarryForwardError,
                "negative records that are not verified carry-forwards",
            ):
                migrate_collections(jobs)

            self.assertEqual(original_bytes, first.read_bytes())

    def test_matches_only_originals_in_the_same_run(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs = Path(directory) / "jobs"
            first = jobs / "date=2026-09-18" / "collection-1.jsonl.gz"
            second = jobs / "date=2026-09-18" / "collection-2.jsonl.gz"
            write_collection(first, [dict(record(self.original), run_id=11)])
            write_collection(second, [record(self.clone)])

            with self.assertRaisesRegex(
                CarryForwardError,
                "0 matching earlier executions",
            ):
                migrate_collections(jobs)

    def test_large_collection_does_not_load_all_records(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs = Path(directory) / "jobs"
            path = jobs / "date=2026-09-18" / "collection-1.jsonl.gz"
            template = runnerless_record(1)
            template["extra"] = "x" * 4096
            write_collection(
                path,
                [dict(template, job_id=job_id) for job_id in range(1, 5001)],
            )

            tracemalloc.start()
            try:
                summary = migrate_collections(jobs)
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()

            self.assertEqual(5000, summary["records_scanned"])
            self.assertEqual(5000, summary["runnerless_records_removed"])
            self.assertLess(peak, 8 * 1024 * 1024)


class MigrationWorkflowContractTest(unittest.TestCase):
    def test_migration_is_marked_and_rebuilds_report_state(self):
        workflow = (
            Path(__file__).parents[2]
            / "workflows"
            / "github-actions-queue-collector.yml"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "migrations/remove-unmeasurable-jobs-v1.json",
            workflow,
        )
        self.assertIn(
            "MIGRATION_PERFORMED: ${{ steps.migration.outputs.performed }}",
            workflow,
        )
        self.assertIn(
            'rm -f "$DATA_DIRECTORY/report-state.json.gz"',
            workflow,
        )
        self.assertIn(
            'max_runtime_seconds=1800',
            workflow,
        )
        self.assertIn(
            '--max-runtime-seconds "$max_runtime_seconds"',
            workflow,
        )
        self.assertIn(
            'rm -rf "$DATA_DIRECTORY/report-data"',
            workflow,
        )


if __name__ == "__main__":
    unittest.main()
