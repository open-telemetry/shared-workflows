"""Resolve an OpenTelemetry PR URL before creating target repository tokens."""

import os
import re
from pathlib import Path


def review_target(pr_url: str) -> tuple[str, int]:
    match = re.fullmatch(
        r"https://github\.com/open-telemetry/([A-Za-z0-9_.-]+)/pull/([1-9][0-9]*)/?",
        pr_url,
        flags=re.IGNORECASE | re.ASCII,
    )
    if not match or match[1] in (".", ".."):
        raise ValueError("Expected a GitHub PR URL: https://github.com/open-telemetry/REPO/pull/NUMBER")
    return match[1].lower(), int(match[2])


def main() -> None:
    name, pr = review_target(os.environ["PR_URL"])
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        output.write(f"repo_name={name}\npr_number={pr}\n")


if __name__ == "__main__":
    main()
