"""Fetch the PR diff and validate findings against it."""

from __future__ import annotations

import codecs
import difflib
import re
from pathlib import Path
from typing import Any, TypedDict

from common import gh, pr_metadata, progress, run


HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


class FileScope(TypedDict):
    changed_lines: list[int]
    hunks: list[tuple[int, int]]


DiffScope = dict[str, FileScope]


def right_side_path(header: str) -> str | None:
    path = header.split("\t", 1)[0]
    if path.startswith('"') and path.endswith('"'):
        path = codecs.escape_decode(path[1:-1].encode("utf-8"))[0].decode("utf-8", errors="surrogateescape")
    return path[2:] if path.startswith("b/") else None


def parse_diff(diff: str) -> DiffScope:
    files: DiffScope = {}
    current: FileScope | None = None
    right_line = 0
    for line in diff.split("\n"):
        if line.startswith("diff --git "):
            current = None
        elif current is None and line.startswith("+++ "):
            if (path := right_side_path(line[4:])) is not None:
                current = files.setdefault(path, {"changed_lines": [], "hunks": []})
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
    if metadata["base"]["repo"]["private"] is not False:
        raise RuntimeError("PR review is only available for public repositories")
    progress(f"Preparing review bundle in {directory}")
    diff = gh(
        ["api", "-H", "Accept: application/vnd.github.diff", f"repos/{repo}/pulls/{pr}"],
    ).stdout
    (directory / "pr.diff").write_text(diff, encoding="utf-8", newline="")
    return metadata, parse_diff(diff)


def small_suggestion(
    checkout: Path, head: str, path: str, start: int, end: int, replacement: str
) -> bool:
    if "```" in replacement:
        return False
    # Read the committed blob without following working-tree symlinks.
    blob = run(
        ["git", "cat-file", "blob", f"{head}:{path}"],
        cwd=checkout, check=False, timeout=120,
    )
    if blob.returncode:
        return False
    lines = blob.stdout.removesuffix("\n").split("\n") if blob.stdout else []
    if end > len(lines):
        return False
    original = lines[start - 1 : end]
    text = replacement.removesuffix("\n")
    proposed = text.split("\n") if replacement else []
    ops = difflib.SequenceMatcher(None, original, proposed, autojunk=False).get_opcodes()
    return original != proposed and sum(j2 - j1 for op, _, _, j1, j2 in ops if op != "equal") < 15


def prepare_review(
    findings: object, checkout: Path, diff_scope: DiffScope, metadata: dict[str, Any]
) -> dict[str, Any]:
    if not isinstance(findings, dict) or not isinstance(findings.get("comments"), list):
        raise ValueError("Final findings must contain a comments array")
    head = metadata["head"]["sha"]
    comments: list[dict[str, Any]] = []
    for raw in findings["comments"]:
        if (
            not isinstance(raw, dict)
            or raw.get("kind") not in ("review", "simplification", "question")
            or not isinstance(raw.get("path"), str)
            or not isinstance(raw.get("body"), str)
            or not raw["body"].strip()
            or re.search(r"(?im)^\s*(?:`{3,}|~{3,})\s*suggestion\b", raw["body"])
            or type(raw.get("line")) is not int
            or ("start_line" in raw and type(raw["start_line"]) is not int)
            or ("suggestion" in raw and not isinstance(raw["suggestion"], str))
        ):
            raise ValueError("Invalid final review comment")
        if raw["kind"] == "question" and "suggestion" in raw:
            raise ValueError("Review questions must not include a suggestion")
        path, line, body = raw["path"], raw["line"], raw["body"].strip()
        start = raw.get("start_line", line)
        scope = diff_scope.get(path)
        if scope is None or line not in scope["changed_lines"] or not any(
            first <= start <= line <= last for first, last in scope["hunks"]
        ):
            raise ValueError(f"Invalid diff anchor for {path!r}:{start}-{line}")
        if raw["kind"] == "simplification" and not body.startswith("[Simplification] "):
            body = f"[Simplification] {body}"
        if raw["kind"] == "question" and not body.startswith("[Question] "):
            body = f"[Question] {body}"
        if "suggestion" in raw:
            suggestion = raw["suggestion"]
            if small_suggestion(checkout, head, path, start, line, suggestion):
                replacement = suggestion.removesuffix("\n") + "\n" if suggestion else ""
                body += f"\n\n```suggestion\n{replacement}```"
            else:
                progress(f"Kept comment without suggestion for {path!r}:{line}")
        comment = {"path": path, "line": line, "side": "RIGHT", "body": body}
        if start != line:
            comment.update(start_line=start, start_side="RIGHT")
        comments.append(comment)
    body = findings.get("body")
    if comments and (not isinstance(body, str) or not body.strip()):
        raise ValueError("Final findings with comments must include a review summary")
    return {
        "pr": metadata["number"],
        "base_sha": metadata["base"]["sha"],
        "review": {"commit_id": head, "body": body if comments else "No findings.", "comments": comments},
    }
