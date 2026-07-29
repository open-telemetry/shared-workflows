"""Score a classifier configuration against the recorded reviewer-feedback cases.

Reports three signals, because they answer different questions:

  drift       stable cases whose recorded label changed -- a regression signal
  contested   cases the voters disagreed on -- a stability signal, lower is better
  accuracy    agreement with cases a human has adjudicated -- a quality signal

Run manually; it makes model calls and is deliberately not part of the suite.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import classification  # noqa: E402

CASES = Path(__file__).resolve().parent / "reviewer_feedback_cases.json"
BATCH_SIZE = 10

# Classifiers answer in their own vocabulary; map each onto the recorded labels.
# "substantive" means the author owes something, so every verdict a classifier
# uses to keep an item with the author maps onto it.
VOCABULARIES = {
    "top_level_reviewer_feedback": (
        "TOP_LEVEL_REVIEWER_FEEDBACK_BATCH_PROMPT_TEMPLATE",
        "discussion_action",
        {"author": "substantive", "unclear": "substantive", "none": "noise"},
    ),
    "reviewer_feedback": (
        "REVIEWER_FEEDBACK_PROMPT_TEMPLATE",
        "verdict",
        {"substantive": "substantive", "noise": "noise"},
    ),
    "reviewer_feedback_confirm": (
        "REVIEWER_FEEDBACK_CONFIRM_PROMPT_TEMPLATE",
        "verdict",
        {"other": "substantive", "confirmed": "noise"},
    ),
}


def available_classifiers() -> list[str]:
    return [
        name for name, (template, _f, _m) in VOCABULARIES.items()
        if hasattr(classification, template)
    ]


def classify(cases: list[dict], template: str, field: str, mapping: dict, model: str) -> dict:
    prompt_inputs = [
        {
            "discussion_id": c["id"],
            "requester": c["requester"],
            "pr_author": c["pr_author"],
            "body": c["body"],
        }
        for c in cases
    ]
    batches = [
        prompt_inputs[i:i + BATCH_SIZE] for i in range(0, len(prompt_inputs), BATCH_SIZE)
    ]

    def run(batch: list[dict]) -> dict[str, str]:
        prompt = classification.render_top_level_batch_prompt(
            batch, template, [dict(item) for item in batch]
        )
        proc = classification.run_copilot(prompt, model)
        parsed = classification.extract_json_object(proc.stdout) or {}
        out: dict[str, str] = {}
        for entry in parsed.get("items") or []:
            verdict = str(entry.get(field) or "").strip().lower()
            label = mapping.get(verdict)
            if entry.get("discussion_id") and label:
                out[entry["discussion_id"]] = label
        return out

    observed: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for answers in pool.map(run, batches):
            observed.update(answers)
    return observed


def report(cases: list[dict], observed: dict[str, str]) -> None:
    drift = [
        {**c, "got": observed.get(c["id"])}
        for c in cases
        if c["stability"] == "stable" and observed.get(c["id"]) != c["baseline"]
    ]
    contested = sum(1 for c in cases if c["stability"] == "contested")
    adjudicated = [c for c in cases if c["adjudicated"]]
    correct = sum(1 for c in adjudicated if observed.get(c["id"]) == c["adjudicated"])
    unanswered = sum(1 for c in cases if c["id"] not in observed)

    print(f"cases        {len(cases)}")
    print(f"unanswered   {unanswered}")
    print(f"drift        {len(drift)}  (stable cases whose label changed)")
    print(f"contested    {contested}  (recorded as unstable; lower is better)")
    print(
        f"accuracy     {correct}/{len(adjudicated)} adjudicated"
        if adjudicated
        else "accuracy     no adjudicated cases yet"
    )

    if not drift:
        return
    print(f"\ndrift by new label: {dict(Counter(d['got'] for d in drift))}")
    print(
        "Moving a case to substantive keeps the pull request with its author, which "
        "one /dashboard route:reviewers comment corrects. Moving it to noise does not.\n"
    )
    for d in drift[:40]:
        print(f"  {d['baseline']} -> {d['got']}  {d['repo']}#{d['pull_request']}")
        print(f"      {' '.join(d['body'].split())[:88]}")
    if len(drift) > 40:
        print(f"  ... and {len(drift) - 40} more")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--classifier", choices=sorted(VOCABULARIES), required=True)
    parser.add_argument("--model", default="gpt-5.4-mini")
    args = parser.parse_args()

    template_name, field, mapping = VOCABULARIES[args.classifier]
    template = getattr(classification, template_name, None)
    if template is None:
        raise SystemExit(
            f"{args.classifier} is not available in this checkout; "
            f"available: {', '.join(available_classifiers())}"
        )

    data = json.loads(CASES.read_text(encoding="utf-8"))
    print(f"{args.classifier} / {args.model}   baseline generated {data['generated_at']}\n")
    observed = classify(data["cases"], template, field, mapping, args.model)
    report(data["cases"], observed)


if __name__ == "__main__":
    main()
