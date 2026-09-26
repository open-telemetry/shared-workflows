#!/usr/bin/env python3
"""Check whether backfill delivery still needs a repository-wide publisher."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import subprocess
import sys

from state import FULL_PUBLISH_DELIVERED_FILE, FULL_PUBLISH_NEEDED_FILE, full_publish_generation


def remote_generation(repository: str, branch_prefix: str, filename: str) -> int:
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
            return 0
        raise RuntimeError(f"cannot read {branch}/{filename}: {result.stderr.strip()}")
    payload = json.loads(result.stdout)
    if payload.get("encoding") != "base64" or not isinstance(payload.get("content"), str):
        raise RuntimeError(f"unexpected GitHub response for {branch}/{filename}")
    data = json.loads(base64.b64decode("".join(payload["content"].split()), validate=True))
    return full_publish_generation(data, f"{branch}/{filename}")


def check_health(repositories: list[str], canary: set[str], canceled: set[str]) -> bool:
    healthy = True
    for repository in repositories:
        channel = "canary" if repository in canary else "stable"
        needed = remote_generation(
            repository, "otelbot/pull-request-dashboard-state", FULL_PUBLISH_NEEDED_FILE
        )
        if needed == 0:
            if channel in canceled:
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
