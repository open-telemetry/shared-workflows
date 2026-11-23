"""Report usage from the firewall proxy's token log."""

import json
import os
from collections import Counter
from decimal import Decimal
from pathlib import Path

from common import progress


PHASES = {
    "review": "Review",
    "simplification": "Simplification",
    "verification": "Verification",
    "verification-retry": "Verification retry",
}
TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")


def proxy_logs_dir(phase: str) -> Path:
    return Path(os.environ["RUNNER_TEMP"]) / "pr-review-usage" / phase


def read_usage(path: Path, label: str) -> tuple[Counter[str], Decimal | None]:
    tokens: Counter[str] = Counter()
    credits = Decimal(0)
    complete = True
    seen = set()
    with path.open(encoding="utf-8") as log:
        for line_no, line in enumerate(log, 1):
            try:
                record = json.loads(line, parse_float=Decimal)
            except json.JSONDecodeError:
                progress(f"{label}: invalid token usage JSON on line {line_no}")
                complete = False
                continue
            if not isinstance(record, dict) or record.get("event") != "token_usage":
                continue
            request = record.get("request_id")
            counts = {key: record.get(key) for key in TOKEN_FIELDS}
            if not isinstance(request, str) or not request or any(
                type(value) is not int or value < 0 for value in counts.values()
            ):
                progress(f"{label}: invalid token usage fields on line {line_no}")
                complete = False
                continue
            if request in seen:
                continue
            seen.add(request)
            tokens.update(counts)
            credit = record.get("ai_credits_this_response")
            if type(credit) is int:
                credit = Decimal(credit)
            if isinstance(credit, Decimal) and credit.is_finite() and credit >= 0:
                credits += credit
                estimate = f", {credit:,.6f} estimated AI credits"
            else:
                complete = False
                estimate = ""
            duration = record.get("duration_ms")
            timing = f" in {duration / 1000:.1f}s" if type(duration) is int and 0 <= duration <= 3_600_000 else ""
            progress(
                f"{label} request #{len(seen)}{timing}: {counts['input_tokens']:,} input, "
                f"{counts['output_tokens']:,} output, {counts['cache_read_tokens']:,} cache read, "
                f"{counts['cache_write_tokens']:,} cache write{estimate}"
            )
    return tokens, credits if seen and complete else None


def usage_row(label: str, tokens: Counter[str], credits: Decimal | None) -> str:
    columns = [f"{tokens[key]:,}" if tokens else "Unavailable" for key in TOKEN_FIELDS]
    columns.append(f"{credits:,.6f}" if credits is not None else "Unavailable")
    return "| " + " | ".join([label, *columns]) + " |"


def summarize() -> str:
    lines = ["### Copilot token usage", ""]
    rows = []
    total_tokens: Counter[str] = Counter()
    total_credits: Decimal | None = Decimal(0)
    for phase, label in PHASES.items():
        directory = proxy_logs_dir(phase)
        if not directory.is_dir():
            continue
        try:
            tokens, credits = read_usage(directory / "api-proxy-logs" / "token-usage.jsonl", label)
        except (OSError, UnicodeError) as error:
            progress(f"{label}: could not read the firewall token log: {error}")
            tokens, credits = Counter(), None
        rows.append(usage_row(label, tokens, credits))
        total_tokens.update(tokens)
        total_credits = total_credits + credits if total_credits is not None and credits is not None else None
    if not rows:
        return "\n".join(lines + ["No model passes started.", ""])
    return "\n".join(lines + [
        "| Pass | Input | Output | Cache read | Cache write | AI credits (estimate) |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
        *rows, usage_row("Recorded total", total_tokens, total_credits), "",
        "Input is reported by the provider and can already include cache tokens. "
        "Credit estimates come from the firewall, not the GitHub bill.",
        "",
    ])


def main() -> None:
    summary = summarize()
    print(summary, end="")
    with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as output:
        output.write(summary)


if __name__ == "__main__":
    main()
