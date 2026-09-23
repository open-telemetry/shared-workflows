from __future__ import annotations

import gzip
import json
import tempfile
import tracemalloc
import unittest
from pathlib import Path

from migrate_queue_records import migrate_collections


def record(
    job_id: int,
    queue_seconds: float | None,
    *,
    runner_assigned: bool = True,
) -> dict:
    return {
        "job_id": job_id,
        "queue_seconds": queue_seconds,
        "runner_assigned": runner_assigned,
    }


def write_collection(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
            for item in records:
                compressed.write(
                    json.dumps(item, separators=(",", ":"), sort_keys=True).encode()
                )
                compressed.write(b"\n")


class QueueRecordMigrationTest(unittest.TestCase):
    def test_migrates_deterministically_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = (
                Path(directory)
                / "jobs"
                / "date=2026-09-18"
                / "collection-1.jsonl.gz"
            )
            original = record(1, 2)
            write_collection(
                path,
                [original, record(2, -2152), record(3, None, runner_assigned=False)],
            )

            summary = migrate_collections(Path(directory) / "jobs")
            first_bytes = path.read_bytes()
            second_summary = migrate_collections(Path(directory) / "jobs")

            with gzip.open(path, "rt", encoding="utf-8") as source:
                migrated = [json.loads(line) for line in source]
            self.assertEqual([original], migrated)
            self.assertEqual(1, summary["files_changed"])
            self.assertEqual(2, summary["records_removed"])
            self.assertEqual(1, summary["negative_queue_records_removed"])
            self.assertEqual(1, summary["runnerless_records_removed"])
            self.assertEqual(0, second_summary["files_changed"])
            self.assertEqual(0, second_summary["records_removed"])
            self.assertEqual(first_bytes, path.read_bytes())
            self.assertEqual(b"\0\0\0\0", first_bytes[4:8])

    def test_removes_negative_records_without_matching_originals(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs = Path(directory) / "jobs"
            first = jobs / "date=2026-09-18" / "collection-1.jsonl.gz"
            second = jobs / "date=2026-09-18" / "collection-2.jsonl.gz"
            original = record(1, 2)
            write_collection(first, [original])
            write_collection(second, [record(2, -1), record(3, -2)])

            summary = migrate_collections(jobs)

            with gzip.open(first, "rt", encoding="utf-8") as source:
                self.assertEqual([original], [json.loads(line) for line in source])
            with gzip.open(second, "rt", encoding="utf-8") as source:
                self.assertEqual([], list(source))
            self.assertEqual(2, summary["negative_queue_records_removed"])
            self.assertEqual(1, summary["files_changed"])

    def test_removes_negative_record_even_if_timestamps_disagree(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs = Path(directory) / "jobs"
            path = jobs / "date=2026-09-18" / "collection-1.jsonl.gz"
            write_collection(
                path,
                [
                    record(1, 0),
                    dict(
                        record(2, -1),
                        job_created_at="2026-09-18T11:24:57Z",
                        job_started_at="2026-09-18T11:24:59Z",
                    ),
                ],
            )

            summary = migrate_collections(jobs)

            with gzip.open(path, "rt", encoding="utf-8") as source:
                self.assertEqual([record(1, 0)], [json.loads(line) for line in source])
            self.assertEqual(1, summary["negative_queue_records_removed"])

    def test_preflights_all_files_before_rewriting(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs = Path(directory) / "jobs"
            first = jobs / "date=2026-09-18" / "collection-1.jsonl.gz"
            second = jobs / "date=2026-09-18" / "collection-2.jsonl.gz"
            write_collection(first, [record(1, -1)])
            write_collection(second, [dict(record(2, 0), runner_assigned="invalid")])
            original_bytes = first.read_bytes()

            with self.assertRaisesRegex(ValueError, "boolean runner_assigned"):
                migrate_collections(jobs)

            self.assertEqual(original_bytes, first.read_bytes())

    def test_rejects_duplicate_job_ids_before_rewriting(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs = Path(directory) / "jobs"
            first = jobs / "date=2026-09-18" / "collection-1.jsonl.gz"
            second = jobs / "date=2026-09-18" / "collection-2.jsonl.gz"
            write_collection(first, [record(1, -1)])
            write_collection(second, [record(1, 0)])
            original_bytes = first.read_bytes()

            with self.assertRaisesRegex(ValueError, "duplicate job_id"):
                migrate_collections(jobs)

            self.assertEqual(original_bytes, first.read_bytes())

    def test_large_collection_does_not_load_all_records(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs = Path(directory) / "jobs"
            path = jobs / "date=2026-09-18" / "collection-1.jsonl.gz"
            template = dict(record(1, None, runner_assigned=False), extra="x" * 4096)
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

        self.assertIn("migrations/remove-unmeasurable-jobs-v1.json", workflow)
        self.assertIn(
            "MIGRATION_PERFORMED: ${{ steps.migration.outputs.performed }}",
            workflow,
        )
        self.assertIn('rm -f "$DATA_DIRECTORY/report-state.json.gz"', workflow)
        self.assertIn('max_runtime_seconds=1800', workflow)
        self.assertIn('--max-runtime-seconds "$max_runtime_seconds"', workflow)
        self.assertIn('rm -rf "$DATA_DIRECTORY/report-data"', workflow)


if __name__ == "__main__":
    unittest.main()
