from __future__ import annotations

import argparse
import gzip
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterator


def migrate_collections(jobs_dir: Path) -> dict[str, int]:
    files = sorted(jobs_dir.rglob("*.jsonl.gz")) if jobs_dir.exists() else []
    records_scanned = 0
    seen_job_ids: set[int] = set()
    negative_counts: dict[Path, int] = defaultdict(int)
    runnerless_counts: dict[Path, int] = defaultdict(int)

    for path in files:
        for _, record in _iter_records(path):
            records_scanned += 1
            job_id = record.get("job_id")
            if isinstance(job_id, bool) or not isinstance(job_id, int):
                raise ValueError(
                    f"{path} has a record without an integer job_id"
                )
            if job_id in seen_job_ids:
                raise ValueError(f"duplicate job_id in collections: {job_id}")
            seen_job_ids.add(job_id)
            if not isinstance(record.get("runner_assigned"), bool):
                raise ValueError(
                    f"{path} has a record without boolean runner_assigned"
                )
            if not record["runner_assigned"]:
                runnerless_counts[path] += 1
            elif _is_negative_queue(record.get("queue_seconds")):
                negative_counts[path] += 1

    for path in files:
        if negative_counts[path] or runnerless_counts[path]:
            _rewrite_collection(path)

    return {
        "files_changed": sum(
            bool(negative_counts[path] or runnerless_counts[path])
            for path in files
        ),
        "files_scanned": len(files),
        "negative_queue_records_removed": sum(negative_counts.values()),
        "records_removed": sum(negative_counts.values())
        + sum(runnerless_counts.values()),
        "records_scanned": records_scanned,
        "runnerless_records_removed": sum(runnerless_counts.values()),
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


def _rewrite_collection(path: Path) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    try:
        with temporary.open("wb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
                for line, record in _iter_records(path):
                    if record["runner_assigned"] and not _is_negative_queue(
                        record.get("queue_seconds")
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
