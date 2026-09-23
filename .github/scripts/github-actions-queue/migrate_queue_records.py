from __future__ import annotations

import argparse
import gzip
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterator

from rerun_carry_forwards import (
    CarryForwardError,
    _execution_fingerprint,
    _queue_interval,
    _verified_original,
)


def migrate_collections(jobs_dir: Path) -> dict[str, int]:
    files = sorted(jobs_dir.rglob("*.jsonl.gz")) if jobs_dir.exists() else []
    records_scanned = 0
    seen_job_ids: set[int] = set()
    negative_ids: set[int] = set()
    negative_jobs: list[tuple[Path, tuple[Any, ...], dict[str, Any]]] = []
    runnerless_counts: dict[Path, int] = defaultdict(int)

    for path in files:
        for _, record in _iter_records(path):
            records_scanned += 1
            job_id = record.get("job_id")
            if isinstance(job_id, bool) or not isinstance(job_id, int):
                raise CarryForwardError(
                    f"{path} has a record without an integer job_id"
                )
            if job_id in seen_job_ids:
                raise CarryForwardError(f"duplicate job_id in collections: {job_id}")
            seen_job_ids.add(job_id)
            if not isinstance(record.get("runner_assigned"), bool):
                raise CarryForwardError(
                    f"{path} has a record without boolean runner_assigned"
                )
            if not record["runner_assigned"]:
                runnerless_counts[path] += 1
            if _is_negative_queue(record.get("queue_seconds")):
                negative_ids.add(job_id)
            job = _as_api_job(record)
            if (interval := _queue_interval(job)) is not None and interval < 0:
                negative_jobs.append((path, _run_key(record), job))

    del seen_job_ids
    negative_by_fingerprint: dict[
        tuple[Any, ...], list[tuple[Path, dict[str, Any]]]
    ] = defaultdict(list)
    for path, run_key, job in negative_jobs:
        negative_by_fingerprint[run_key + _execution_fingerprint(job)].append(
            (path, job)
        )
    del negative_jobs

    originals: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for path in files:
        for _, record in _iter_records(path):
            job = _as_api_job(record)
            interval = _queue_interval(job)
            if interval is not None and interval >= 0:
                fingerprint = _run_key(record) + _execution_fingerprint(job)
                if fingerprint in negative_by_fingerprint:
                    originals[fingerprint].append(job)

    carry_forward_ids: set[int] = set()
    carry_forward_counts: dict[Path, int] = defaultdict(int)
    for fingerprint, negatives in negative_by_fingerprint.items():
        for path, job in negatives:
            _verified_original(job, originals.get(fingerprint, []))
            carry_forward_ids.add(job["id"])
            carry_forward_counts[path] += 1
    if carry_forward_ids != negative_ids:
        raise CarryForwardError(
            "collections have negative records that are not verified carry-forwards"
        )

    for path in files:
        if carry_forward_counts[path] or runnerless_counts[path]:
            _rewrite_collection(path, carry_forward_ids)

    return {
        "carry_forward_records_removed": sum(carry_forward_counts.values()),
        "files_changed": sum(
            bool(carry_forward_counts[path] or runnerless_counts[path])
            for path in files
        ),
        "files_scanned": len(files),
        "records_removed": sum(carry_forward_counts.values())
        + sum(runnerless_counts.values()),
        "records_scanned": records_scanned,
        "runnerless_records_removed": sum(runnerless_counts.values()),
    }


def _run_key(record: dict[str, Any]) -> tuple[Any, ...]:
    return (
        record.get("organization"),
        record.get("repository"),
        record.get("run_id"),
    )


def _as_api_job(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record.get("job_id"),
        "name": record.get("job_name"),
        "run_attempt": record.get("run_attempt"),
        "created_at": record.get("job_created_at"),
        "started_at": record.get("job_started_at"),
        "completed_at": record.get("job_completed_at"),
        "conclusion": record.get("job_conclusion"),
        "runner_name": record.get("runner_name"),
        "runner_group_name": record.get("runner_group_name"),
        "labels": record.get("runner_labels"),
    }


def _is_negative_queue(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and value < 0
    )


def _iter_records(path: Path) -> Iterator[tuple[bytes, dict[str, Any]]]:
    try:
        with gzip.open(path, "rb") as source:
            for line in source:
                line = line.rstrip(b"\r\n")
                yield line, _read_record(path, line)
    except OSError as error:
        raise ValueError(f"unable to read collection {path}: {error}") from error


def _read_record(path: Path, line: bytes) -> dict[str, Any]:
    try:
        record = json.loads(line)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"unable to parse collection {path}: {error}") from error
    if not isinstance(record, dict):
        raise ValueError(f"collection {path} contains a non-object record")
    return record


def _rewrite_collection(path: Path, removed_ids: set[int]) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    try:
        with temporary.open("wb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
                for line, record in _iter_records(path):
                    if (
                        record["runner_assigned"]
                        and record["job_id"] not in removed_ids
                    ):
                        compressed.write(line)
                        compressed.write(b"\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Remove records without measurable runner queue time."
    )
    parser.add_argument("--jobs-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(migrate_collections(args.jobs_dir), sort_keys=True))


if __name__ == "__main__":
    main()
