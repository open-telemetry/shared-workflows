#!/usr/bin/env python3
"""Advance the stable pull request dashboard processor to a newer release."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from telemetry import session as telemetry_session

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = SCRIPT_DIR / "stable-release.json"
VERSION = re.compile(r"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
SHA = re.compile(r"^[0-9a-f]{40}$")


class PromotionError(ValueError):
    """The requested promotion or current stable release is invalid."""


def version_tuple(version: str) -> tuple[int, int, int]:
    match = VERSION.fullmatch(version)
    if match is None:
        raise PromotionError(f"release must be a stable semantic version such as v0.6.0: {version}")
    return tuple(int(part) for part in match.groups())


def unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    fields: dict[str, object] = {}
    for name, value in pairs:
        if name in fields:
            raise PromotionError(f"duplicate stable release field: {name}")
        fields[name] = value
    return fields


def stable_release(text: str) -> tuple[str, str]:
    try:
        config = json.loads(text, object_pairs_hook=unique_fields)
    except json.JSONDecodeError as error:
        raise PromotionError("stable release must be a JSON object with sha and release") from error
    if not isinstance(config, dict) or set(config) != {"sha", "release"}:
        raise PromotionError("stable release must contain exactly sha and release")
    sha, release = config["sha"], config["release"]
    if not isinstance(sha, str) or SHA.fullmatch(sha) is None:
        raise PromotionError("stable release commit must be an immutable lowercase 40-character SHA")
    if not isinstance(release, str):
        raise PromotionError("stable release must be a stable semantic version")
    version_tuple(release)
    return sha, release


def promoted_text(text: str, release: str, target_sha: str) -> str:
    target_version = version_tuple(release)
    if SHA.fullmatch(target_sha) is None:
        raise PromotionError(f"release commit must be a lowercase 40-character SHA: {target_sha}")
    _, current_release = stable_release(text)
    if target_version <= version_tuple(current_release):
        raise PromotionError(f"release {release} must be newer than the current stable release {current_release}")
    return json.dumps({"sha": target_sha, "release": release}, indent=2) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release", help="published release tag to promote, for example v0.6.0")
    parser.add_argument("sha", help="40-character commit SHA resolved from the release tag")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()

    current = args.config.read_text(encoding="utf-8")
    promoted = promoted_text(current, args.release, args.sha)
    args.config.write_text(promoted, encoding="utf-8")
    print(f"promoted the stable pull request dashboard to {args.release} ({args.sha})")


if __name__ == "__main__":
    with telemetry_session():
        main()
