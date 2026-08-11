#!/usr/bin/env python3
"""Advance every stable pull request dashboard job to one release."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_WORKFLOW = SCRIPT_DIR.parents[1] / "workflows" / "pull-request-dashboard.yml"

STABLE_JOBS = (
    "run-repo-dashboard-stable",
    "run-targeted-dashboard-stable",
    "run-head-sha-dashboard-stable",
)
WORKFLOW_PATH = ".github/workflows/pull-request-dashboard-repo.yml"
JOB_HEADER = re.compile(r"^  ([A-Za-z0-9_-]+):\s*$")
USES_LINE = re.compile(
    r"^(    uses: open-telemetry/shared-workflows/"
    + re.escape(WORKFLOW_PATH)
    + r"@)([0-9a-f]{40})( # )(v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))(\r?\n?)$"
)
CODE_REF_LINE = re.compile(
    r"^(      code_ref: )([0-9a-f]{40})( # )"
    r"(v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))(\r?\n?)$"
)
VERSION = re.compile(r"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
SHA = re.compile(r"^[0-9a-f]{40}$")


class PromotionError(ValueError):
    """The requested promotion or current rollout wiring is invalid."""


def version_tuple(version: str) -> tuple[int, int, int]:
    match = VERSION.fullmatch(version)
    if match is None:
        raise PromotionError(f"release must be a stable semantic version such as v0.6.0: {version}")
    return tuple(int(part) for part in match.groups())


def job_ranges(lines: list[str]) -> dict[str, range]:
    headers = [(index, match.group(1)) for index, line in enumerate(lines) if (match := JOB_HEADER.match(line))]
    return {
        name: range(index + 1, headers[position + 1][0] if position + 1 < len(headers) else len(lines))
        for position, (index, name) in enumerate(headers)
    }


def matching_line(lines: list[str], indexes: range, pattern: re.Pattern[str], label: str) -> tuple[int, re.Match[str]]:
    matches = [(index, match) for index in indexes if (match := pattern.fullmatch(lines[index]))]
    if len(matches) != 1:
        raise PromotionError(f"{label} must contain exactly one matching pin; found {len(matches)}")
    return matches[0]


def promoted_text(text: str, release: str, target_sha: str) -> str:
    target_version = version_tuple(release)
    if SHA.fullmatch(target_sha) is None:
        raise PromotionError(f"release commit must be a lowercase 40-character SHA: {target_sha}")

    lines = text.splitlines(keepends=True)
    ranges = job_ranges(lines)
    pins: list[tuple[str, str]] = []
    replacements: list[tuple[int, re.Match[str]]] = []

    for job in STABLE_JOBS:
        indexes = ranges.get(job)
        if indexes is None:
            raise PromotionError(f"stable rollout job is missing: {job}")
        uses_index, uses_match = matching_line(lines, indexes, USES_LINE, job)
        code_index, code_match = matching_line(lines, indexes, CODE_REF_LINE, job)
        uses_pin = (uses_match.group(2), uses_match.group(4))
        code_pin = (code_match.group(2), code_match.group(4))
        if uses_pin != code_pin:
            raise PromotionError(f"{job} uses and code_ref pins disagree: {uses_pin} != {code_pin}")
        pins.append(uses_pin)
        replacements.extend(((uses_index, uses_match), (code_index, code_match)))

    unique_pins = set(pins)
    if len(unique_pins) != 1:
        raise PromotionError(f"stable jobs disagree on the current rollout pin: {sorted(unique_pins)}")
    _, current_release = unique_pins.pop()
    if target_version <= version_tuple(current_release):
        raise PromotionError(f"release {release} must be newer than the current stable release {current_release}")

    for index, match in replacements:
        lines[index] = f"{match.group(1)}{target_sha}{match.group(3)}{release}{match.group(5)}"
    return "".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release", help="published release tag to promote, for example v0.6.0")
    parser.add_argument("sha", help="40-character commit SHA resolved from the release tag")
    parser.add_argument("--workflow", type=Path, default=DEFAULT_WORKFLOW)
    args = parser.parse_args()

    current = args.workflow.read_text(encoding="utf-8")
    promoted = promoted_text(current, args.release, args.sha)
    args.workflow.write_text(promoted, encoding="utf-8")
    print(f"promoted the stable pull request dashboard to {args.release} ({args.sha})")


if __name__ == "__main__":
    main()
