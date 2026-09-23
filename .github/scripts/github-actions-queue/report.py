from __future__ import annotations

import argparse
import fnmatch
import gzip
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ALL = "*"
GITHUB_HOSTED = "github-hosted"
SELF_HOSTED = "self-hosted"
SELF_HOSTED_LABEL_PATTERNS = (
    "self-hosted",
    "cncf-*",
    "oracle-*",
    "*-s390x",
)
STATE_VERSION = 1
KEY_SEPARATOR = "\x1f"
PERCENTILES = (0.5, 0.9, 0.95, 0.99)


def classify_runner(labels: list[str]) -> str:
    normalized = [label.casefold() for label in labels]
    if any(
        fnmatch.fnmatchcase(label, pattern)
        for label in normalized
        for pattern in SELF_HOSTED_LABEL_PATTERNS
    ):
        return SELF_HOSTED
    return GITHUB_HOSTED


def load_report_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _new_report_state()
    try:
        with gzip.open(path, "rt", encoding="utf-8") as source:
            state = json.load(source)
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"unable to read report state from {path}: {error}") from error
    if state.get("version") != STATE_VERSION:
        raise ValueError(f"unsupported report state version: {state.get('version')}")
    if state.get("self_hosted_label_patterns") != list(
        SELF_HOSTED_LABEL_PATTERNS
    ):
        return _new_report_state()
    return state


def _new_report_state() -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "self_hosted_label_patterns": list(SELF_HOSTED_LABEL_PATTERNS),
        "processed_files": [],
        "buckets": {},
        "labels": {
            GITHUB_HOSTED: [],
            SELF_HOSTED: [],
        },
        "repositories": {
            GITHUB_HOSTED: [],
            SELF_HOSTED: [],
        },
        "records": 0,
        "valid_records": 0,
        "updated_at": None,
    }


def write_report_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    with temporary.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
            payload = json.dumps(
                state,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
            compressed.write(payload)
    temporary.replace(path)


def build_report(
    jobs_dir: Path,
    state_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    state = load_report_state(state_path)
    processed_files = set(state["processed_files"])
    dirty_dates: set[str] = set()
    labels = {
        host: set(state["labels"].get(host, []))
        for host in (GITHUB_HOSTED, SELF_HOSTED)
    }
    repositories = {
        host: set(state["repositories"].get(host, []))
        for host in (GITHUB_HOSTED, SELF_HOSTED)
    }

    files = sorted(jobs_dir.rglob("*.jsonl.gz")) if jobs_dir.exists() else []
    new_files = [
        path
        for path in files
        if path.relative_to(jobs_dir).as_posix() not in processed_files
    ]
    for path in new_files:
        relative_path = path.relative_to(jobs_dir).as_posix()
        _process_file(path, state, dirty_dates, labels, repositories)
        processed_files.add(relative_path)

    state["processed_files"] = sorted(processed_files)
    state["labels"] = {host: sorted(labels[host]) for host in labels}
    state["repositories"] = {
        host: sorted(repositories[host]) for host in repositories
    }

    all_dates = sorted({key.split(KEY_SEPARATOR, 1)[0][:10] for key in state["buckets"]})
    output_dir.mkdir(parents=True, exist_ok=True)
    for date in all_dates:
        if not (output_dir / f"date={date}.json").exists():
            dirty_dates.add(date)
    for date in sorted(dirty_dates):
        _write_daily_report(output_dir, date, state)
    _write_manifest(output_dir, all_dates, state)
    write_report_state(state_path, state)

    return {
        "dates": len(all_dates),
        "new_files": len(new_files),
        "processed_files": len(processed_files),
        "records": state["records"],
        "valid_records": state["valid_records"],
        "updated_at": state["updated_at"],
    }


def _process_file(
    path: Path,
    state: dict[str, Any],
    dirty_dates: set[str],
    labels: dict[str, set[str]],
    repositories: dict[str, set[str]],
) -> None:
    try:
        with gzip.open(path, "rt", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                try:
                    record = json.loads(line)
                    _add_record(state, record, dirty_dates, labels, repositories)
                except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                    raise ValueError(
                        f"invalid queue record in {path} at line {line_number}: {error}"
                    ) from error
    except OSError as error:
        raise ValueError(f"unable to read queue records from {path}: {error}") from error


def _add_record(
    state: dict[str, Any],
    record: dict[str, Any],
    dirty_dates: set[str],
    labels: dict[str, set[str]],
    repositories: dict[str, set[str]],
) -> None:
    state["records"] += 1
    collected_at = record.get("collected_at")
    if collected_at and (
        state["updated_at"] is None or collected_at > state["updated_at"]
    ):
        _parse_instant(collected_at)
        state["updated_at"] = collected_at

    queue_seconds = record.get("queue_seconds")
    if queue_seconds is None:
        raise ValueError("queue_seconds must not be null")
    if isinstance(queue_seconds, bool) or not isinstance(queue_seconds, (int, float)):
        raise TypeError("queue_seconds must be a number")
    if not math.isfinite(queue_seconds):
        raise ValueError("queue_seconds must be finite")
    if queue_seconds < 0:
        raise ValueError("queue_seconds must not be negative")

    repository = record["repository"]
    if not isinstance(repository, str) or not repository:
        raise ValueError("repository must be a non-empty string")
    runner_labels = record.get("runner_labels") or []
    if not isinstance(runner_labels, list) or not all(
        isinstance(label, str) and label for label in runner_labels
    ):
        raise ValueError("runner_labels must contain non-empty strings")
    runner_labels = sorted(set(runner_labels))
    host = classify_runner(runner_labels)
    hour = _format_hour(_parse_instant(record["job_created_at"]))
    dirty_dates.add(hour[:10])
    labels[host].update(runner_labels)
    repositories[host].add(repository)
    state["valid_records"] += 1

    queue_key = _format_queue_value(float(queue_seconds))
    for repository_key in (ALL, repository):
        for label_key in (ALL, *runner_labels):
            bucket_key = KEY_SEPARATOR.join(
                (hour, host, repository_key, label_key)
            )
            histogram = state["buckets"].setdefault(bucket_key, {})
            histogram[queue_key] = histogram.get(queue_key, 0) + 1


def _write_daily_report(
    output_dir: Path,
    date: str,
    state: dict[str, Any],
) -> None:
    rows = []
    summary_buckets: dict[tuple[str, str, str], dict[str, int]] = {}
    for key, histogram in state["buckets"].items():
        hour, host, repository, label = key.split(KEY_SEPARATOR)
        if not hour.startswith(date):
            continue
        summary = summary_buckets.setdefault((host, repository, label), {})
        for value, value_count in histogram.items():
            summary[value] = summary.get(value, 0) + value_count
        values = sorted(
            ((float(value), count) for value, count in histogram.items()),
            key=lambda item: item[0],
        )
        count = sum(item[1] for item in values)
        rows.append(
            {
                "hour": hour,
                "host": host,
                "repository": repository,
                "label": label,
                "count": count,
                "p50": _percentile(values, count, PERCENTILES[0]),
                "p90": _percentile(values, count, PERCENTILES[1]),
                "p95": _percentile(values, count, PERCENTILES[2]),
                "p99": _percentile(values, count, PERCENTILES[3]),
            }
        )
    summaries = []
    for (host, repository, label), histogram in sorted(summary_buckets.items()):
        values = sorted(
            ((float(value), count) for value, count in histogram.items()),
            key=lambda item: item[0],
        )
        summaries.append(
            {
                "host": host,
                "repository": repository,
                "label": label,
                "histogram": values,
            }
        )
    rows.sort(
        key=lambda row: (
            row["hour"],
            row["host"],
            row["repository"],
            row["label"],
        )
    )
    _atomic_write_json(
        output_dir / f"date={date}.json",
        {
            "version": STATE_VERSION,
            "date": date,
            "series": rows,
            "summaries": summaries,
        },
    )


def _write_manifest(
    output_dir: Path,
    dates: list[str],
    state: dict[str, Any],
) -> None:
    latest_hour = max(
        (
            key.split(KEY_SEPARATOR, 1)[0]
            for key in state["buckets"]
        ),
        default=None,
    )
    _atomic_write_json(
        output_dir / "manifest.json",
        {
            "version": STATE_VERSION,
            "updated_at": state["updated_at"],
            "latest_hour": latest_hour,
            "dates": dates,
            "labels": state["labels"],
            "repositories": state["repositories"],
            "records": state["records"],
            "valid_records": state["valid_records"],
            "self_hosted_label_patterns": list(SELF_HOSTED_LABEL_PATTERNS),
        },
    )


def _percentile(
    values: list[tuple[float, int]],
    count: int,
    fraction: float,
) -> float:
    position = (count - 1) * fraction
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    lower = _value_at_index(values, lower_index)
    upper = _value_at_index(values, upper_index)
    return round(lower + (upper - lower) * (position - lower_index), 3)


def _value_at_index(values: list[tuple[float, int]], index: int) -> float:
    seen = 0
    for value, count in values:
        seen += count
        if index < seen:
            return value
    raise ValueError(f"percentile index {index} exceeds histogram size {seen}")


def _parse_instant(value: str) -> datetime:
    if not isinstance(value, str):
        raise TypeError("timestamp must be a string")
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        raise ValueError(f"timestamp must include a time zone: {value}")
    return moment.astimezone(UTC)


def _format_hour(value: datetime) -> str:
    return value.astimezone(UTC).replace(
        minute=0,
        second=0,
        microsecond=0,
    ).isoformat(timespec="seconds").replace("+00:00", "Z")


def _format_queue_value(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return repr(value)


def _atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(value, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build incremental hourly GitHub Actions queue reports."
    )
    parser.add_argument("--jobs-dir", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            build_report(args.jobs_dir, args.state, args.output_dir),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
