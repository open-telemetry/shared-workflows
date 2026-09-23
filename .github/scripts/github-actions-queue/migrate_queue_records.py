from __future__ import annotations

import argparse
import gzip
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from rerun_carry_forwards import CarryForwardError, filter_rerun_carry_forwards


def migrate_collections(jobs_dir: Path) -> dict[str, int]:
    plans: list[tuple[Path, list[bytes], int, int]] = []
    files = sorted(jobs_dir.rglob("*.jsonl.gz")) if jobs_dir.exists() else []
    collections: list[tuple[Path, list[bytes], list[dict[str, Any]]]] = []
    records_scanned = 0
    seen_job_ids: set[int] = set()

    for path in files:
        lines = _read_lines(path)
        records = [_read_record(path, line) for line in lines]
        collections.append((path, lines, records))
        records_scanned += len(records)
        for record in records:
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

    all_records = [
        record for _, _, records in collections for record in records
    ]
    carry_forward_ids = _carry_forward_ids(all_records)
    negative_ids = {
        record["job_id"]
        for record in all_records
        if _is_negative_queue(record.get("queue_seconds"))
    }
    if carry_forward_ids != negative_ids:
        raise CarryForwardError(
            "collections have negative records that are not verified carry-forwards"
        )

    for path, lines, records in collections:
        file_carry_forward_ids = {
            record["job_id"]
            for record in records
            if record["job_id"] in carry_forward_ids
        }
        runnerless_ids = {
            record["job_id"]
            for record in records
            if not record["runner_assigned"]
        }
        removed_ids = file_carry_forward_ids | runnerless_ids
        if removed_ids:
            kept_lines = [
                line
                for line, record in zip(lines, records, strict=True)
                if record.get("job_id") not in removed_ids
            ]
            plans.append(
                (
                    path,
                    kept_lines,
                    len(file_carry_forward_ids),
                    len(runnerless_ids),
                )
            )

    for path, lines, _, _ in plans:
        _write_lines(path, lines)

    return {
        "carry_forward_records_removed": sum(plan[2] for plan in plans),
        "files_changed": len(plans),
        "files_scanned": len(files),
        "records_removed": sum(plan[2] + plan[3] for plan in plans),
        "records_scanned": records_scanned,
        "runnerless_records_removed": sum(plan[3] for plan in plans),
    }


def _carry_forward_ids(
    records: list[dict[str, Any]],
) -> set[int]:
    runs: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        runs[
            (
                record.get("organization"),
                record.get("repository"),
                record.get("run_id"),
            )
        ].append(_as_api_job(record))

    removed_ids = set()
    for jobs in runs.values():
        _, removed = filter_rerun_carry_forwards(jobs)
        removed_ids.update(job["id"] for job in removed)
    return removed_ids


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


def _read_lines(path: Path) -> list[bytes]:
    try:
        with gzip.open(path, "rb") as source:
            return source.read().splitlines()
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


def _write_lines(path: Path, lines: list[bytes]) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    with temporary.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
            for line in lines:
                compressed.write(line)
                compressed.write(b"\n")
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Remove records without measurable runner queue time."
    )
    parser.add_argument("--jobs-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(migrate_collections(args.jobs_dir), sort_keys=True))


if __name__ == "__main__":
    main()
