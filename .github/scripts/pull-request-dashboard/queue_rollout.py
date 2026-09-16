#!/usr/bin/env python3
"""Resolve whether queue workers can safely process stable repositories."""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

VERSION_SUFFIXES = ("_STATE_VERSION", "_REVISION")


class QueueRolloutError(ValueError):
    """Dashboard delivery versions could not be read from source."""


def delivery_versions_from_source(path: Path) -> dict[str, int]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as error:
        raise QueueRolloutError(f"could not parse {path}: {error}") from error

    versions: dict[str, int] = {}
    for node in tree.body:
        assignments: list[tuple[str, ast.expr | None]] = []
        if isinstance(node, ast.Assign):
            assignments.extend(
                (target.id, node.value)
                for target in node.targets
                if isinstance(target, ast.Name)
            )
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            assignments.append((node.target.id, node.value))

        for name, value_node in assignments:
            if not name.endswith(VERSION_SUFFIXES):
                continue
            if name in versions:
                raise QueueRolloutError(f"{path} assigns {name} more than once")
            value = value_node.value if isinstance(value_node, ast.Constant) else None
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise QueueRolloutError(
                    f"{path} must assign {name} a non-negative integer literal"
                )
            versions[name] = value

    if not versions:
        raise QueueRolloutError(f"{path} defines no dashboard delivery versions")
    return dict(sorted(versions.items()))


def queue_mode(current_state: Path, stable_state: Path) -> str:
    current = delivery_versions_from_source(current_state)
    stable = delivery_versions_from_source(stable_state)
    if current == stable:
        return "all"
    print(
        "dashboard delivery versions differ; using canary queue mode: "
        f"current={json.dumps(current, sort_keys=True)} "
        f"stable={json.dumps(stable, sort_keys=True)}",
        file=sys.stderr,
    )
    return "canary"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current-state", type=Path, required=True)
    parser.add_argument("--stable-state", type=Path, required=True)
    args = parser.parse_args()
    try:
        mode = queue_mode(args.current_state, args.stable_state)
    except QueueRolloutError as error:
        parser.error(str(error))
    print(mode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
