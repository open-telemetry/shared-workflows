"""Validate findings and publish a GitHub review."""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Any

from common import gh_json, progress, run
from prepare import DiffScope


def small_suggestion(
    checkout: Path, head: str, path: str, start: int, end: int, replacement: str
) -> bool:
    if "```" in replacement:
        return False
    # Read the committed blob without following working-tree symlinks.
    blob = run(["git", "cat-file", "blob", f"{head}:{path}"], cwd=checkout, check=False, timeout=120)
    if blob.returncode:
        return False
    lines = blob.stdout.splitlines()
    if end > len(lines):
        return False
    original = lines[start - 1 : end]
    proposed = replacement.rstrip("\n").splitlines()
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
            or raw.get("kind") not in ("review", "simplification")
            or not isinstance(raw.get("path"), str)
            or not isinstance(raw.get("body"), str)
            or not raw["body"].strip()
            or re.search(r"(?im)^\s*(?:`{3,}|~{3,})\s*suggestion\b", raw["body"])
            or type(raw.get("line")) is not int
            or ("start_line" in raw and type(raw["start_line"]) is not int)
            or ("suggestion" in raw and not isinstance(raw["suggestion"], str))
        ):
            raise ValueError("Invalid final review comment")
        path, line, body = raw["path"], raw["line"], raw["body"].strip()
        start = raw.get("start_line", line)
        scope = diff_scope.get(path)
        if scope is None or line not in scope["changed_lines"] or not any(
            first <= start <= line <= last for first, last in scope["hunks"]
        ):
            raise ValueError(f"Invalid diff anchor for {path!r}:{start}-{line}")
        if raw["kind"] == "simplification" and not body.startswith("[Simplification] "):
            body = f"[Simplification] {body}"
        if "suggestion" in raw:
            suggestion = raw["suggestion"]
            if small_suggestion(checkout, head, path, start, line, suggestion):
                body += "\n\n```suggestion\n" + suggestion.rstrip("\n") + "\n```"
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


def post_review(repo: str, pr: int, payload_path: Path) -> str:
    review = gh_json(
        ["api", f"repos/{repo}/pulls/{pr}/reviews", "--method", "POST", "--input", str(payload_path)]
    )
    return review["html_url"]


def load_json(path: Path, label: str) -> dict[str, Any]:
    if not path.exists():
        raise RuntimeError(f"missing {label}: {path}")
    return json.loads(path.read_text(encoding="utf-8"))
