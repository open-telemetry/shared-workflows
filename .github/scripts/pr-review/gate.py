"""Resolve an opted-in GitHub PR URL before creating target repository tokens."""

import json
import os
import re
from pathlib import Path


def review_target(pr_url: str) -> tuple[str, int]:
    match = re.fullmatch(
        r"https://github\.com/(open-telemetry)/([A-Za-z0-9_.-]+)/pull/([1-9][0-9]*)/?",
        pr_url,
        flags=re.IGNORECASE,
    )
    if not match:
        raise ValueError("Expected a GitHub PR URL: https://github.com/open-telemetry/REPO/pull/NUMBER")
    repositories = json.loads(
        (Path(__file__).parent.parent / "pull-request-dashboard" / "repositories.json").read_text(encoding="utf-8")
    )
    names = {item["name"].lower(): item["name"] for item in repositories}
    name = names.get(match[2].lower())
    if name is None:
        raise ValueError("The target repository is not in the dashboard opt-in list")
    return f"open-telemetry/{name}", int(match[3])


def main() -> None:
    repo, pr = review_target(os.environ["PR_URL"])
    owner, name = repo.split("/")
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        output.write(f"repository={repo}\nowner={owner}\nrepo_name={name}\npr_number={pr}\n")


if __name__ == "__main__":
    main()
