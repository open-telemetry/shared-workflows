from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any


class CarryForwardError(ValueError):
    pass


def filter_rerun_carry_forwards(
    jobs: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    originals: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for job in jobs:
        if _has_non_negative_queue(job):
            originals[_execution_fingerprint(job)].append(job)

    removed = []
    for job in jobs:
        if not _has_negative_queue(job):
            continue
        attempt = job.get("run_attempt")
        candidates = [
            original
            for original in originals[_execution_fingerprint(job)]
            if _attempt(original) < _attempt_value(attempt)
        ]
        if len(candidates) != 1:
            raise CarryForwardError(
                f"job {job.get('id')} has negative queue time and "
                f"{len(candidates)} matching earlier executions"
            )
        removed.append(job)

    removed_ids = {id(job) for job in removed}
    return [job for job in jobs if id(job) not in removed_ids], removed


def _execution_fingerprint(job: dict[str, Any]) -> tuple[Any, ...]:
    labels = job.get("labels") or []
    if not isinstance(labels, list) or not all(
        isinstance(label, str) and label for label in labels
    ):
        raise CarryForwardError("job labels must contain non-empty strings")
    return (
        job.get("name"),
        job.get("started_at"),
        job.get("completed_at"),
        job.get("conclusion"),
        job.get("runner_name"),
        job.get("runner_group_name"),
        tuple(sorted(set(labels))),
    )


def _has_negative_queue(job: dict[str, Any]) -> bool:
    interval = _queue_interval(job)
    return interval is not None and interval < 0


def _has_non_negative_queue(job: dict[str, Any]) -> bool:
    interval = _queue_interval(job)
    return interval is not None and interval >= 0


def _queue_interval(job: dict[str, Any]) -> float | None:
    if not job.get("runner_name"):
        return None
    created_at = job.get("created_at")
    started_at = job.get("started_at")
    if not created_at or not started_at:
        return None
    return (_parse_instant(started_at) - _parse_instant(created_at)).total_seconds()


def _attempt(job: dict[str, Any]) -> int:
    return _attempt_value(job.get("run_attempt"))


def _attempt_value(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise CarryForwardError("negative queue jobs require a positive run_attempt")
    return value


def _parse_instant(value: Any) -> datetime:
    if not isinstance(value, str):
        raise CarryForwardError("job timestamps must be strings")
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise CarryForwardError(f"invalid job timestamp: {value}") from error
    if moment.tzinfo is None:
        raise CarryForwardError(f"job timestamp must include a time zone: {value}")
    return moment.astimezone(UTC)
