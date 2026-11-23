"""Shared helpers for the PR review workflow."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any


# Force UTF-8 on parent stdout/stderr so unicode characters in subprocess
# output don't crash the default cp1252 codec on Windows.
for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        _reconfigure(encoding="utf-8", errors="replace")


def progress(message: str) -> None:
    print(f"[review] {message}", flush=True)


def run(
    cmd: list[str],
    *,
    check: bool = True,
    timeout: int | None = None,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
        timeout=timeout,
        cwd=cwd,
        env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def gh(args: list[str]) -> subprocess.CompletedProcess[str]:
    cmd = ["gh", *args]
    progress("Running: " + shlex.join(cmd))
    return run(cmd)


def gh_json(args: list[str]) -> Any:
    result = gh(args)
    return json.loads(result.stdout or "null")


def pr_metadata(repo: str, pr: int) -> dict[str, Any]:
    metadata = gh_json(["api", f"repos/{repo}/pulls/{pr}"])
    if metadata["html_url"] != f"https://github.com/{repo}/pull/{pr}" or metadata["number"] != pr:
        raise RuntimeError("GitHub resolved a different PR target")
    return metadata


def check_snapshot(repo: str, pr: int, base: str, head: str) -> None:
    live = pr_metadata(repo, pr)
    if live["state"] != "open" or (live["base"]["sha"], live["head"]["sha"]) != (base, head):
        raise RuntimeError("The PR changed or closed during review; start a new run")


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
