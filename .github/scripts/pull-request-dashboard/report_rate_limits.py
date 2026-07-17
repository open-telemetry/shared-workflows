from __future__ import annotations

import argparse
import json
from typing import Any

from github_cli import gh_api


def remaining_percent(rate_limit: dict[str, Any]) -> float | None:
    limit = rate_limit.get("limit")
    remaining = rate_limit.get("remaining")
    if not isinstance(limit, int) or not isinstance(remaining, int) or limit <= 0:
        return None
    return remaining * 100 / limit


def report_rate_limit(name: str, rate_limit: dict[str, Any], warning_threshold: float) -> None:
    print(f"{name}: {json.dumps(rate_limit, sort_keys=True)}")
    percent = remaining_percent(rate_limit)
    if percent is None:
        print(f"::warning title={name} rate limit::Unable to calculate remaining headroom")
    elif percent <= warning_threshold:
        print(
            f"::warning title={name} rate limit::"
            f"{rate_limit['remaining']} of {rate_limit['limit']} requests remain "
            f"({percent:.1f}% headroom)"
        )
    else:
        print(
            f"{name} headroom: {rate_limit['remaining']} of {rate_limit['limit']} "
            f"({percent:.1f}% remaining)"
        )


def report_rate_limits(warning_threshold: float) -> None:
    try:
        rate_limit_data = gh_api("/rate_limit")
    except Exception as e:
        print(f"::warning title=REST rate-limit query failed::{e}")
        raise

    resources = rate_limit_data.get("resources") or {}
    report_rate_limit("REST /rate_limit core", resources.get("core") or {}, warning_threshold)
    report_rate_limit(
        "REST /rate_limit graphql", resources.get("graphql") or {}, warning_threshold
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Report GitHub App API rate-limit headroom")
    parser.add_argument(
        "--warning-threshold-percent",
        type=float,
        default=20,
        help="emit a workflow warning at or below this remaining percentage",
    )
    args = parser.parse_args()
    report_rate_limits(args.warning_threshold_percent)


if __name__ == "__main__":
    main()
