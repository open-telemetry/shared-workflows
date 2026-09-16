# Self-review report

Reviewed pull request #377 at immutable head
`8f66336f18bbb637f105548ec82e1de7a4f611a0` against `main`.

## Result

The pull request is cleared. No substantive fix was required, so there are no
fix commits.

One candidate was dropped after verification. Repository visibility is
snapshotted before collection, leaving a theoretical transition window if an
administrator makes a repository private while the collector is running.
Repeated checks cannot eliminate that time-of-check/time-of-use window, and the
implemented contract intentionally collects the set of active public
repositories resolved at the start of a run. There is no repository requirement
or directly applicable precedent requiring stronger synchronization.

The live title and description accurately describe the final diff; no
replacement is proposed.

## Validation

- `python3 -m unittest discover -p 'test_*.py'` from
  `.github/scripts/github-actions-queue`: 20 tests passed.
- `git diff --check ad5b9918d6eca8cc999d7034757aee727b2631ea HEAD`:
  passed.
- Current GitHub checks at review time: collector tests, CodeQL, and both zizmor
  checks passed.

```json
{
  "findings": [
    {
      "body": "Repository visibility is resolved once before collection, so a repository made private during the same run could remain accessible to the already-issued installation token. This candidate was dropped because the workflow's documented contract snapshots active public repositories at run start, repeated checks cannot eliminate the underlying time-of-check/time-of-use window, and no repository rule or directly applicable precedent requires stronger synchronization.",
      "location": {
        "path": ".github/scripts/github-actions-queue/collect.py",
        "line": 455
      },
      "status": "dropped",
      "commit": null
    }
  ],
  "fix_commits": [],
  "repository": {
    "owner": "open-telemetry",
    "name": "shared-workflows"
  },
  "pull_request": {
    "number": 377,
    "base_branch": "main",
    "head_branch": "trask-actions-queue-events",
    "head_sha": "8f66336f18bbb637f105548ec82e1de7a4f611a0"
  },
  "iteration": {
    "completed": 1,
    "maximum": 1,
    "result": "clean"
  },
  "metadata": {
    "title": {
      "current": "Collect organization-wide GitHub Actions queue data",
      "proposed": null
    },
    "body": {
      "current": "Collect per-job GitHub Actions timing data hourly across active public OpenTelemetry repositories and store immutable gzip-compressed JSON Lines on the orphan `otelbot/github-actions-queue-data` branch.\n\n- Use the read-only `OpenTelemetry Actions Telemetry` GitHub App and keep the built-in workflow token limited to writing the data branch.\n- Checkpoint repository progress, revisit unfinished runs, and retain failed job lookups for retry.\n- Preserve matrix jobs, attempts, fork runs, runner metadata, and direct job links in the raw dataset.\n- Split searches around GitHub's 1,000-run API limit and checkpoint cleanly if the App exhausts its REST quota.",
      "proposed": null
    }
  }
}
```
