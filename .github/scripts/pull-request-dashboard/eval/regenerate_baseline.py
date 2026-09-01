"""Re-measure the recorded baseline in reviewer_feedback_cases.json.

Drives from the cases already in the file rather than re-collecting them from
GitHub. Re-collecting would silently change the set, because the original
collection kept only pull requests that were open on the day it ran.

Raw responses are cached in .cache/baseline/ beside the dashboard scripts, so a
failed or interrupted run resumes without paying for the calls it already made,
and a change to how answers are read costs nothing to apply.

Run manually; it makes several hundred model calls.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from classification_execution import (  # noqa: E402
    CopilotCliModelRunner,
    ModelRunRequest,
    ModelRunner,
)
import classification_policy as policy  # noqa: E402
from score_reviewer_feedback import CASES, batch_cases  # noqa: E402

CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "baseline"
PROMPT = "REVIEWER_FEEDBACK_PROMPT_TEMPLATE"
# The shipped binary answers in the recorded labels themselves, so this maps one
# onto the other unchanged. It stays because the file records the mapping it was
# built with, and a prompt answering in its own vocabulary would need one.
ACTION_LABELS = {"author_action": "author_action", "no_author_action": "no_author_action"}

_printed = Lock()


def batch_prompt(batch: list[dict]) -> str:
    """The prompt the dashboard would send for one batch."""
    items = [
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
        for c in batch
    ]
    # The cases already hold the joined comment body the pipeline would build,
    # so they are their own prompt input. Copies, because rendering truncates in
    # place when a batch runs long.
    return policy.render_prompt_inputs(
        [dict(item) for item in items],
        policy.REVIEWER_FEEDBACK_PROMPT_TEMPLATE,
    )


def cache_key(prompt: str, model: str, salt: str) -> str:
    """Key on the prompt text itself, so a change to how it renders misses."""
    payload = json.dumps(
        {"prompt": prompt, "model": model, "salt": salt}, sort_keys=True
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def run_batch(
    batch: list[dict],
    model: str,
    salt: str,
    runner: ModelRunner | None = None,
) -> dict:
    """Return the raw Copilot result for one batch, from cache when present."""
    prompt = batch_prompt(batch)
    path = CACHE_DIR / f"{cache_key(prompt, model, salt)}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))

    try:
        response = (runner or CopilotCliModelRunner()).run(
            ModelRunRequest(prompt, model)
        )
        raw = {
            "returncode": response.returncode,
            "stdout": response.stdout,
        }
    except Exception as e:  # noqa: BLE001 - one bad batch must not end the run
        raw = {"returncode": -1, "stdout": "", "error": f"{type(e).__name__}: {e}"}
    # A failure is not a result: caching it would make every later run replay it
    # instead of retrying the call. Via a temporary name, so an interrupt cannot
    # leave a half-written entry for the next run to read back.
    if raw["returncode"] == 0:
        tmp = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
        tmp.write_text(json.dumps(raw), encoding="utf-8")
        tmp.replace(path)
    return raw


def answers(raw: dict, batch: list[dict]) -> dict[str, str]:
    """Verdicts keyed by case id, keeping only ids that were asked for.

    A repeated id is dropped rather than resolved, matching production, which
    fails a discussion whose id came back more than once.
    """
    if raw.get("returncode") != 0:
        return {}
    parsed = policy.extract_json_object(raw.get("stdout") or "") or {}
    items = parsed.get("items")
    if not isinstance(items, list):
        return {}
    requested = {c["id"] for c in batch}
    out: dict[str, str] = {}
    seen: set[str] = set()
    for entry in items:
        if not isinstance(entry, dict):
            continue
        case_id = entry.get("discussion_id")
        if not isinstance(case_id, str) or case_id not in requested:
            continue
        if case_id in seen:
            out.pop(case_id, None)
            continue
        seen.add(case_id)
        verdict = str(entry.get("verdict") or "").strip().lower()
        if verdict in ACTION_LABELS:
            out[case_id] = verdict
    return out


def measure(
    cases: list[dict],
    model: str,
    runs: int,
    workers: int,
    runner: ModelRunner | None = None,
) -> list[dict[str, str]]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    runner_lock = Lock() if runner is not None else None
    runner = runner or CopilotCliModelRunner()
    batches = batch_cases(cases)
    # A separate salt per run keeps the cache from replaying one trial as all of
    # them, which would report perfect stability no matter how the model behaves.
    tasks = [(batch, f"baseline-{n}") for n in range(runs) for batch in batches]
    print(f"{len(batches)} batches x {runs} runs = {len(tasks)} calls", flush=True)
    done = 0

    def work(task: tuple[list[dict], str]) -> tuple[str, dict[str, str]]:
        nonlocal done
        batch, salt = task
        if runner_lock is None:
            result = answers(run_batch(batch, model, salt, runner), batch)
        else:
            with runner_lock:
                result = answers(run_batch(batch, model, salt, runner), batch)
        with _printed:
            done += 1
            if done % 20 == 0 or done == len(tasks):
                print(f"  {done}/{len(tasks)}", flush=True)
        return salt, result

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(work, tasks))

    trials: list[dict[str, str]] = [{} for _ in range(runs)]
    for salt, result in results:
        trials[int(salt.rsplit("-", 1)[1])].update(result)
    return trials


def rebuild(payload: dict, trials: list[dict[str, str]], model: str) -> dict:
    cases = []
    for case in payload["cases"]:
        raw = [trial.get(case["id"]) for trial in trials]
        observed = [ACTION_LABELS[a] for a in raw if a is not None]
        # An unanswered run is not an observation; a substitute label would make
        # the file claim evidence it does not have.
        if any(a is None for a in raw):
            role, stability, recorded = "context", None, None
        elif len(set(observed)) == 1:
            role, stability, recorded = "scored", "stable", observed[0]
        else:
            role, stability, recorded = "scored", "flaky", None
        cases.append({
            **{k: case[k] for k in
               ("id", "repo", "pull_request", "requester", "pr_author", "review_state",
                "root_timestamp", "body")},
            "role": role,
            "stability": stability,
            "recorded_label": recorded,
            "adjudicated_label": case["adjudicated_label"],
            "run_actions": raw,
            "run_labels": observed,
        })
    # The order the dashboard sends a pull request's items in, which decides
    # where the ten-item batch boundaries fall and so what each prompt contains.
    cases.sort(key=lambda c: (c["repo"], c["pull_request"], c["root_timestamp"]))
    roles = Counter(c["role"] for c in cases)
    stabilities = Counter(c["stability"] for c in cases)
    return {
        **payload,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%d"),
        "baseline_configuration": {
            **payload["baseline_configuration"],
            "model": model,
            "prompt": PROMPT,
            "runs": len(trials),
        },
        "action_labels": ACTION_LABELS,
        "counts": {
            "cases": len(cases),
            "scored": roles["scored"],
            "context": roles["context"],
            "stable": stabilities["stable"],
            "flaky": stabilities["flaky"],
            "adjudicated": sum(1 for c in cases if c["adjudicated_label"]),
        },
        "cases": cases,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="gpt-5.4-mini")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    args = parser.parse_args()
    # Zero runs would observe nothing and rewrite every case as flaky.
    if args.runs < 1:
        parser.error("--runs must be at least 1")
    if args.workers < 1:
        parser.error("--workers must be at least 1")

    payload = json.loads(CASES.read_text(encoding="utf-8"))
    before = payload["counts"]
    trials = measure(payload["cases"], args.model, args.runs, args.workers)
    rebuilt = rebuild(payload, trials, args.model)
    after = rebuilt["counts"]

    print(f"\n            {'was':>6} {'now':>6}")
    for key in ("cases", "scored", "context", "stable", "flaky", "adjudicated"):
        print(f"{key:<11} {before.get(key, 0):>6} {after[key]:>6}")

    by_id = {c["id"]: c for c in payload["cases"]}
    changed = [
        c for c in rebuilt["cases"]
        if c["stability"] == "stable" == by_id[c["id"]]["stability"]
        and c["recorded_label"] != by_id[c["id"]]["recorded_label"]
    ]
    print(f"\nlabel changed on {len(changed)} cases stable in both recordings")
    print(
        "label balance "
        f"{dict(Counter(c['recorded_label'] for c in rebuilt['cases'] if c['recorded_label']))}"
    )

    if args.dry_run:
        print("\ndry run; nothing written")
        return
    CASES.write_text(json.dumps(rebuilt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nwritten to {CASES}")


if __name__ == "__main__":
    main()
