"""Fetch a PR diff and track the lines available for review comments."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, TypedDict

from common import gh, gh_json, progress


HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


class FileScope(TypedDict):
    changed_lines: list[int]
    hunks: list[tuple[int, int]]


DiffScope = dict[str, FileScope]


def pr_metadata(repo: str, pr: int) -> dict[str, Any]:
    metadata = gh_json(["api", f"repos/{repo}/pulls/{pr}"])
    if metadata["html_url"] != f"https://github.com/{repo}/pull/{pr}" or metadata["number"] != pr:
        raise RuntimeError("GitHub resolved a different PR target")
    return metadata


def parse_diff(diff: str) -> DiffScope:
    files: DiffScope = {}
    current: FileScope | None = None
    right_line = 0
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            current = None
        elif current is None and line.startswith("+++ b/"):
            current = files.setdefault(line[6:], {"changed_lines": [], "hunks": []})
        elif current is not None:
            match = HUNK_RE.match(line)
            if match:
                right_line = int(match[1])
                current["hunks"].append((right_line, right_line + int(match[2] or "1") - 1))
            elif current["hunks"]:
                if line.startswith("+"):
                    current["changed_lines"].append(right_line)
                    right_line += 1
                elif line.startswith(" "):
                    right_line += 1
    return files


def build_bundle(repo: str, pr: int, directory: Path) -> tuple[dict[str, Any], DiffScope]:
    metadata = pr_metadata(repo, pr)
    if metadata["state"] != "open":
        raise RuntimeError("The PR is not open")
    progress(f"Preparing review bundle in {directory}")
    diff = gh(["api", "-H", "Accept: application/vnd.github.diff", f"repos/{repo}/pulls/{pr}"]).stdout
    (directory / "pr.diff").write_text(diff, encoding="utf-8")
    return metadata, parse_diff(diff)
