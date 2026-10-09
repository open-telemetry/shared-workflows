from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

from queue_worker_client import QueueWorkerClient

SCRIPT_DIR = Path(__file__).resolve().parent
TRIGGERS = {
    "schedule", "workflow_dispatch", "check_suite", "status", "pull_request",
    "issue_comment", "pull_request_review", "pull_request_review_comment",
    "pull_request_review_thread",
}


def requests(
    config: Path, repository: str, pr_number: str, head_sha: str,
    trigger_event: str, *, reminders: bool = False,
) -> list[dict[str, Any]]:
    configured = {entry["name"] for entry in json.loads(config.read_text(encoding="utf-8"))}
    if repository and repository not in configured:
        raise ValueError(f"no configured repository matched: {repository}")
    if (pr_number or head_sha) and not repository:
        raise ValueError("repository is required for a PR or head SHA")
    if pr_number and (head_sha or re.fullmatch(r"[1-9][0-9]{0,6}", pr_number) is None):
        raise ValueError("provide one valid PR number or head SHA")
    if head_sha and re.fullmatch(r"[0-9a-f]{40}", head_sha) is None:
        raise ValueError("head SHA must be 40 lowercase hexadecimal characters")
    if trigger_event not in TRIGGERS:
        raise ValueError(f"bad trigger event: {trigger_event}")
    if reminders and (pr_number or head_sha):
        raise ValueError("reminder sweeps must target repositories")
    kind = "reminders" if reminders else "refresh" if pr_number or head_sha else "backfill"
    return [
        {
            "repository": name, "kind": kind,
            "prNumber": int(pr_number) if pr_number else None,
            "headSha": head_sha, "triggerEvent": trigger_event,
        }
        for name in sorted({repository} if repository else configured)
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Accept dashboard work or inspect an accepted request.")
    parser.add_argument("--config", type=Path, default=SCRIPT_DIR / "repositories.json")
    parser.add_argument("--repository", default=os.environ.get("TARGET_REPOSITORY", ""))
    parser.add_argument("--pr-number", default=os.environ.get("TRIGGER_PR_NUMBER", ""))
    parser.add_argument("--head-sha", default=os.environ.get("HEAD_SHA", ""))
    parser.add_argument("--trigger-event", default=os.environ.get("TRIGGER_EVENT") or "workflow_dispatch")
    parser.add_argument("--reminders", action="store_true")
    parser.add_argument("--status-item-key", default=os.environ.get("REQUEST_ITEM_KEY", ""))
    parser.add_argument("--status-generation", default=os.environ.get("REQUEST_GENERATION", ""))
    parser.add_argument("--status-request-id", default=os.environ.get("REQUEST_ID", ""))
    parser.add_argument("--endpoint", default=os.environ.get("PR_DASHBOARD_QUEUE_ENDPOINT", ""))
    args = parser.parse_args()
    status_values = (args.status_item_key, args.status_generation, args.status_request_id)
    if any(status_values) and not all(status_values):
        parser.error("request item key, generation, and request ID must be provided together")
    if args.status_item_key:
        if re.fullmatch(r"[1-9][0-9]*", args.status_generation) is None:
            parser.error("request generation must be a positive integer")
        lane = "maintenance" if args.status_item_key.endswith(("#backfill", "#reminders")) else "live"
        results = [QueueWorkerClient(args.endpoint, lane=lane).call(
            "status", itemKey=args.status_item_key, generation=int(args.status_generation),
            requestId=args.status_request_id,
        )]
    else:
        work = requests(
            args.config, args.repository, args.pr_number, args.head_sha,
            args.trigger_event, reminders=args.reminders,
        )
        results = []
        for item in work:
            lane = "live" if item["kind"] == "refresh" else "maintenance"
            client = QueueWorkerClient(args.endpoint, lane=lane)
            result = client.call("enqueue", **item)
            if (
                result.get("accepted") is not True or result.get("completed") is not False
                or not isinstance(result.get("requestId"), str) or not result["requestId"]
            ):
                raise RuntimeError("queue did not confirm acceptance of the request")
            results.append(result)
    print(json.dumps(results, sort_keys=True))
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as summary:
            if not args.status_item_key:
                summary.write("Work accepted, not completed. The queue drain owns execution.\n\n")
            summary.write("| Item | Generation | Request ID | Status |\n| --- | --- | --- | --- |\n")
            for result in results:
                summary.write(
                    f"| `{result['itemKey']}` | {result['generation']} | "
                    f"`{result['requestId']}` | {result['status']} |\n"
                )
            summary.write(
                "\nTo follow an item, run Pull request dashboard on main with "
                "`request_item_key`, `request_generation`, and `request_id` from this table. "
                "Status receipts are bounded; `unknown` does not mean success.\n"
            )


if __name__ == "__main__":
    main()
