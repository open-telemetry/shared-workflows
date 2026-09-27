# PR #420 simplification review

Request: `pr-simplify-420-2b78971d3080476a94ff0e45c956a8cf`

## Source verification

Before repository inspection, the live pull request was verified as open with
head repository `open-telemetry/shared-workflows`, head ref
`trask-recover-stalled-dashboard-runs`, and head SHA
`431de8a411087da1b893b3cde172b36c1750f807`. Its description, full GitHub diff,
discussion, and applicable repository instructions were reviewed.

## Candidate inventory

1. **Unify normal-cancel and force-cancel request handling — rejected.**
   The two paths share a request/409/re-read pattern, but have materially
   different authorization, receipt transitions, retry limits, timestamps,
   counters, and result collections. A common helper would require
   stage-specific callbacks or field-name parameters and would move rather than
   materially reduce the state-machine complexity. This is minor deduplication,
   not a qualifying major simplification.
2. **Replace watchdog persistence with the queue mutation facility — rejected.**
   `DashboardQueue.#mutate` is private and retries compare-and-swap conflicts,
   while the watchdog intentionally fails when its strongly read receipt changes
   during processing. Reusing it would expand scope and alter concurrency/error
   behavior rather than preserve it.
3. **Consolidate backlog rotations into a helper — rejected.**
   Workflow, missing-receipt, and candidate rotations use a similar slice idiom,
   but each enforces a distinct fairness or API-budget boundary. Extracting the
   few shared lines is only minor deduplication; removing any rotation can starve
   work.
4. **Precompute or collapse newer-run lookup — rejected.**
   `findNewerRun` is used both to qualify candidates and to retain the selected
   earliest newer same-group run. Replacing it with maps or a custom scan would
   not materially simplify control flow and could change ordering or group
   matching.
5. **Remove fresh run/job reads or merge missing-run helpers — rejected.**
   `getRunIfFound`, `getJobsIfFound`, and the fresh reads before escalation
   distinguish deleted runs from other API failures and prevent force-cancelling
   runs whose jobs, state, or newer blocker changed. They directly implement
   required safety and error behavior.
6. **Collapse receipt fields or validation branches — rejected.**
   Accepted normal cancellation, rejected normal attempts, force attempts, and
   their independent grace periods are distinct persisted states. Their
   validation prevents malformed state from authorizing force cancellation.
   Removing fields or branches would weaken safety or change error behavior.
7. **Simplify job eligibility predicates — rejected.**
   The completed/unfinished split and queued-job timestamp handling preserve
   protections for recent, assigned, started, active, and unknown jobs.
   Factoring repeated timestamp parsing would be a small line-count cleanup, not
   a major simplification.
8. **Remove confirmation reads or receipt retirement — rejected.**
   Post-request reads and `finishIfStopped` ensure GitHub's final state controls
   confirmation, deleted runs are reported unconfirmed, and terminal receipts
   are removed. Removing them changes public results and leaves stale state.
9. **Simplify API/store wiring, documentation, or tests — rejected.**
   The new GitHub client methods and scheduled-function store wiring are already
   thin. Documentation describes the behavior, and the expanded tests cover
   safety-critical state transitions. Any available edits would be stylistic,
   test-only reduction, or lost coverage.

## Disposition

No candidate met both the material-complexity and behavior-preservation gates.
No simplification was applied.

Code commits, in order: none (`[]`).

## Hosted validation

- Focused watchdog and GitHub-dispatch tests passed: 47 tests.
- Existing dashboard dependencies installed successfully with `npm ci`; the
  audit reported zero vulnerabilities.
- The complete JavaScript suite passed after dependency installation: 107 tests.
- The output JSON was parsed and its exact key sets, source identity, completion
  flag, passed outcomes, and empty code-commit list were checked locally.

