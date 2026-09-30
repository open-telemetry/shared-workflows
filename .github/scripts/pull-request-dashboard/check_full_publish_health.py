#!/usr/bin/env python3
"""Check whether backfill delivery still needs a repository-wide publisher."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import subprocess
import sys

from state import (
    DASHBOARD_STATE_VERSION,
    FULL_PUBLISH_DELIVERED_FILE,
    FULL_PUBLISH_NEEDED_FILE,
    INITIAL_BACKFILL_COMPLETE_KEY,
    full_publish_generation,
)


def remote_state_file(repository: str, branch_prefix: str, filename: str) -> dict | None:
    branch = f"{branch_prefix}/{repository}"
    endpoint = f"repos/open-telemetry/shared-workflows/contents/{repository}/{filename}"
    result = subprocess.run(
        ["gh", "api", "--method", "GET", endpoint, "-f", f"ref={branch}"],
        capture_output=True,
        text=True,
        check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    if result.returncode != 0:
        if "HTTP 404" in result.stderr:
            return None
        raise RuntimeError(f"cannot read {branch}/{filename}: {result.stderr.strip()}")
    payload = json.loads(result.stdout)
    if payload.get("encoding") != "base64" or not isinstance(payload.get("content"), str):
        raise RuntimeError(f"unexpected GitHub response for {branch}/{filename}")
    data = json.loads(base64.b64decode("".join(payload["content"].split()), validate=True))
    if not isinstance(data, dict):
        raise RuntimeError(f"unexpected GitHub state for {branch}/{filename}")
    return data


def remote_generation(repository: str, branch_prefix: str, filename: str) -> int:
    data = remote_state_file(repository, branch_prefix, filename)
    if data is None:
        return 0
    branch = f"{branch_prefix}/{repository}"
    return full_publish_generation(data, f"{branch}/{filename}")


def needed_generation(repository: str) -> tuple[int, bool | None]:
    branch = "otelbot/pull-request-dashboard-state"
    data = remote_state_file(repository, branch, FULL_PUBLISH_NEEDED_FILE)
    if data is None:
        return 0, None
    generation = full_publish_generation(
        data, f"{branch}/{repository}/{FULL_PUBLISH_NEEDED_FILE}"
    )
    complete = data.get(INITIAL_BACKFILL_COMPLETE_KEY)
    if INITIAL_BACKFILL_COMPLETE_KEY in data and not isinstance(complete, bool):
        raise RuntimeError(f"incompatible full publish state {branch}/{repository}/{FULL_PUBLISH_NEEDED_FILE}")
    return generation, complete


def initial_backfill_complete(repository: str) -> bool:
    branch = "otelbot/pull-request-dashboard-state"
    data = remote_state_file(repository, branch, "dashboard-state.json")
    if data is None:
        return False
    if (
        data.get("version") != DASHBOARD_STATE_VERSION
        or not isinstance(data.get(INITIAL_BACKFILL_COMPLETE_KEY), bool)
    ):
        raise RuntimeError(f"incompatible dashboard state {branch}/{repository}/dashboard-state.json")
    return data[INITIAL_BACKFILL_COMPLETE_KEY]


def check_health(repositories: list[str], canary: set[str], canceled: set[str]) -> bool:
    healthy = True
    for repository in repositories:
        channel = "canary" if repository in canary else "stable"
        needed, ready = needed_generation(repository)
        if needed == 0:
            if channel == "canary" and channel in canceled:
                print(
                    f"{repository}: canceled {channel} matrix with no full publish receipt protocol",
                    file=sys.stderr,
                )
                healthy = False
            continue
        delivered = remote_generation(
            repository, "otelbot/pull-request-dashboard-delivery", FULL_PUBLISH_DELIVERED_FILE
        )
        if delivered < needed:
            if channel not in canceled:
                if ready is None:
                    ready = initial_backfill_complete(repository)
                if not ready:
                    continue
            print(
                f"{repository}: full publication pending ({delivered}/{needed})",
                file=sys.stderr,
            )
            healthy = False
    return healthy


def main() -> int:
    config = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    repositories = [entry["name"] for entry in config]
    canary = set(json.loads(os.environ["CANARY_REPOSITORIES"]))
    canceled = {
        channel
        for channel in ("canary", "stable")
        if os.environ[f"{channel.upper()}_RESULT"] == "cancelled"
    }
    healthy = check_health(repositories, canary, canceled)
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        output.write(f"healthy={'true' if healthy else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
