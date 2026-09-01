"""Score a classifier configuration against the recorded reviewer-feedback cases.

Reports three signals, because they answer different questions:

  drift          stable cases whose recorded label changed -- a regression signal
  inconsistent   cases this candidate answered differently across trials -- a
                 stability signal, lower is better
  accuracy       agreement with cases a human has adjudicated -- a quality signal

Run manually; it makes model calls and is deliberately not part of the suite.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from classification_execution import (  # noqa: E402
    CopilotCliModelRunner,
    ModelRunRequest,
    ModelRunner,
)
import classification_policy as policy  # noqa: E402

CASES = Path(__file__).resolve().parent / "reviewer_feedback_cases.json"
BATCH_SIZE = 10

# Classifiers answer in their own vocabulary; map each onto the recorded labels.
# "author_action" means the author owes something, so every verdict a classifier
# uses to keep an item with the author maps onto it. Answer fields are tried in
# order, matching the fallbacks the production parser accepts.
VOCABULARIES = {
    "reviewer_feedback": (
        "REVIEWER_FEEDBACK_PROMPT_TEMPLATE",
        ("verdict",),
        {"author_action": "author_action", "no_author_action": "no_author_action"},
    ),
}


def available_classifiers() -> list[str]:
    return [
        name for name, (template, _f, _m) in VOCABULARIES.items()
        if hasattr(policy, template)
    ]


def batch_cases(cases: list[dict]) -> list[list[dict]]:
    """Group like production, which classifies one pull request at a time.

    Batch composition is part of the prompt, so mixing pull requests would score
    the model on context it never sees when deployed.
    """
    by_pr: dict[tuple[str, int], list[dict]] = {}
    for case in cases:
        by_pr.setdefault((case["repo"], case["pull_request"]), []).append(case)
    batches: list[list[dict]] = []
    for group in by_pr.values():
        for start in range(0, len(group), BATCH_SIZE):
            batches.append(group[start:start + BATCH_SIZE])
    return batches


def classify(
    cases: list[dict],
    template: str,
    fields: tuple[str, ...],
    mapping: dict,
    model: str,
    runner: ModelRunner | None = None,
) -> dict:
    runner_lock = Lock() if runner is not None else None
    runner = runner or CopilotCliModelRunner()
    batches = [
        [
            policy.reviewer_feedback_prompt_item(
                c["id"],
                c["requester"],
                c["pr_author"],
                c["body"],
                (
                    "review_summary"
                    if c["id"].startswith("pr-review-")
                    else "top_level_comment"
                ),
            )
            for c in group
        ]
        for group in batch_cases(cases)
    ]

    def run(batch: list[dict]) -> dict[str, str]:
        prompt = policy.render_prompt_inputs(
            [dict(item) for item in batch],
            template,
        )
        # A batch that fails or answers unusably is unanswered, not fatal: one bad
        # response should not discard an evaluation of several hundred calls.
        try:
            if runner_lock is None:
                response = runner.run(ModelRunRequest(prompt, model))
            else:
                with runner_lock:
                    response = runner.run(ModelRunRequest(prompt, model))
        except Exception:  # noqa: BLE001 - production also treats any batch failure as failed
            return {}
        if response.returncode != 0:
            return {}
        parsed = policy.extract_json_object(response.stdout) or {}
        items = parsed.get("items")
        if not isinstance(items, list):
            return {}
        out: dict[str, str] = {}
        requested = {item["discussion_id"] for item in batch}
        seen: set[str] = set()
        for entry in items:
            if not isinstance(entry, dict):
                continue
            discussion_id = entry.get("discussion_id")
            if not isinstance(discussion_id, str) or discussion_id not in requested:
                continue
            # Identity is settled before the verdict is read, so a repeated id
            # fails the case however the repeat is spelled, as production does.
            if discussion_id in seen:
                out.pop(discussion_id, None)
                continue
            seen.add(discussion_id)
            answer = next((entry[name] for name in fields if entry.get(name)), "")
            label = mapping.get(str(answer).strip().lower())
            if label:
                out[discussion_id] = label
        return out

    observed: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for answers in pool.map(run, batches):
            observed.update(answers)
    return observed


def majority(labels: list[str]) -> str | None:
    """The label held by more than half the votes, or None when there is no majority."""
    if not labels:
        return None
    label, count = Counter(labels).most_common(1)[0]
    return label if count * 2 > len(labels) else None


def summarize(cases: list[dict], trials: list[dict[str, str]]) -> dict:
    """Everything the report prints, as values, so it can be tested without a model."""
    trial_count = len(trials)
    # Context cases exist only to reproduce the baseline's batches; they have
    # no recorded behaviour to compare against.
    scored_cases = [c for c in cases if c["role"] == "scored"]
    observed = {
        case["id"]: [t[case["id"]] for t in trials if case["id"] in t]
        for case in scored_cases
    }

    # A case answered in only some trials is not comparable with one answered in
    # all of them, so it is reported rather than settled from the answers present.
    no_answer = [c for c in scored_cases if not observed[c["id"]]]
    partial_answer = [c for c in scored_cases if 0 < len(observed[c["id"]]) < trial_count]
    complete = [c for c in scored_cases if len(observed[c["id"]]) == trial_count]

    settled = {c["id"]: majority(observed[c["id"]]) for c in complete}
    tied = [c for c in complete if settled[c["id"]] is None]
    scorable = [c for c in complete if settled[c["id"]] is not None]

    # Every adjudicated case counts, so failing to answer a hard one cannot
    # improve the score by leaving the denominator.
    adjudicated = [c for c in scored_cases if c["adjudicated_label"]]
    scored = [c for c in adjudicated if settled.get(c["id"]) is not None]
    stable = [c for c in scored_cases if c["stability"] == "stable"]
    return {
        "trial_count": trial_count,
        "scored_cases": scored_cases,
        "context_only": [c for c in cases if c["role"] == "context"],
        "no_answer": no_answer,
        "partial_answer": partial_answer,
        "tied": tied,
        "stable": stable,
        # Drift is only meaningful against the stable cases actually settled, so
        # the denominator is reported and never shrinks silently.
        "stable_settled": [c for c in stable if settled.get(c["id"]) is not None],
        "drift": [
            {**c, "got": settled[c["id"]]}
            for c in scorable
            if c["stability"] == "stable" and settled[c["id"]] != c["recorded_label"]
        ],
        "inconsistent": [c for c in complete if len(set(observed[c["id"]])) > 1],
        "complete": complete,
        "adjudicated": adjudicated,
        "scored": scored,
        "correct": sum(1 for c in scored if settled[c["id"]] == c["adjudicated_label"]),
    }


def report(
    cases: list[dict],
    trials: list[dict[str, str]],
    baseline_flaky: int,
    baseline_runs: int,
) -> None:
    s = summarize(cases, trials)
    trial_count, drift = s["trial_count"], s["drift"]
    inconsistent = s["inconsistent"]
    adjudicated, scored = s["adjudicated"], s["scored"]

    print(f"cases          {len(s['scored_cases'])}  scored"
          f"  (+{len(s['context_only'])} kept only to reproduce baseline batches)")
    print(f"trials         {trial_count}  (a case's label is the majority across trials)")
    print(f"no answer      {len(s['no_answer'])}  (nothing in any trial)")
    print(f"partial answer {len(s['partial_answer'])}  (some trials only; not scored)")
    print(f"tied           {len(s['tied'])}  (every trial answered but no majority; not scored)")
    settled_stable, all_stable = len(s["stable_settled"]), len(s["stable"])
    if settled_stable == all_stable:
        print(f"drift          {len(drift)}  (stable cases whose label changed)")
    else:
        print(
            f"drift          {len(drift)}  over {settled_stable} of {all_stable} stable "
            "cases settled; not comparable"
        )
    # More trials mean more chances to disagree, and cases missing from a trial
    # never count, so the counts only compare when the trial count matches and
    # every case was answered in full.
    uncovered = len(s["no_answer"]) + len(s["partial_answer"])
    if trial_count == baseline_runs and not uncovered:
        print(
            f"inconsistent   {len(inconsistent)}  this candidate; baseline recorded "
            f"{baseline_flaky}  (lower is better)"
        )
    elif trial_count != baseline_runs:
        print(
            f"inconsistent   {len(inconsistent)}  over {trial_count} trials; not comparable "
            f"with the baseline's {baseline_flaky} over {baseline_runs}"
        )
    else:
        print(
            f"inconsistent   {len(inconsistent)}  over {len(s['complete'])} fully answered "
            f"cases; not comparable with the baseline's {baseline_flaky} over all "
            f"{len(s['scored_cases'])}"
        )
    print(
        f"accuracy       {s['correct']}/{len(adjudicated)} adjudicated"
        f"  (scored {len(scored)} of {len(adjudicated)})"
        if adjudicated
        else "accuracy       no adjudicated cases yet"
    )

    if not drift:
        return
    print(f"\ndrift by new label: {dict(Counter(d['got'] for d in drift))}")
    print(
        "Moving a case to author_action keeps the pull request with its author, which "
        "one /dashboard route:reviewers comment corrects. The reverse does not.\n"
    )
    for d in drift[:40]:
        print(f"  {d['recorded_label']} -> {d['got']}  {d['repo']}#{d['pull_request']}")
        print(f"      {' '.join(d['body'].split())[:88]}")
    if len(drift) > 40:
        print(f"  ... and {len(drift) - 40} more")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--classifier", choices=sorted(VOCABULARIES), required=True)
    parser.add_argument("--model", default="gpt-5.4-mini")
    parser.add_argument(
        "--trials",
        type=int,
        default=3,
        help="runs of the candidate; an odd count of at least 3 avoids ties",
    )
    args = parser.parse_args()

    if args.trials < 2:
        parser.error("--trials must be at least 2 to measure stability")

    template_name, fields, mapping = VOCABULARIES[args.classifier]
    template = getattr(policy, template_name, None)
    if template is None:
        raise SystemExit(
            f"{args.classifier} is not available in this checkout; "
            f"available: {', '.join(available_classifiers())}"
        )

    data = json.loads(CASES.read_text(encoding="utf-8"))
    print(
        f"{args.classifier} / {args.model} x{args.trials}   "
        f"baseline generated {data['generated_at']}\n"
    )
    trials = [
        classify(data["cases"], template, fields, mapping, args.model)
        for _ in range(args.trials)
    ]
    report(
        data["cases"],
        trials,
        data["counts"]["flaky"],
        data["baseline_configuration"]["runs"],
    )


if __name__ == "__main__":
    main()
