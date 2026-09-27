# Pull request #420 simplification review

## Outcome

Passed. The live pull request was open and still matched
`open-telemetry/shared-workflows:trask-recover-stalled-dashboard-runs` at
`431de8a411087da1b893b3cde172b36c1750f807` before inspection. No candidate
met the required threshold for a major, behavior-preserving simplification, so
there are no code commits.

The review covered the complete PR diff, title, description, review thread, all
six changed files, the directly affected Netlify Blob store adapter, and the
dashboard test workflow. No applicable `AGENTS.md` or
`copilot-instructions.md` files exist in the repository.

## Candidate inventory

- **Reuse `DashboardQueue.#mutate` for receipts — rejected.** The only existing
  compare-and-swap facility is private and queue-specific. It retries conflicts,
  while the watchdog intentionally fails a conditional write that loses its
  strong-read ETag. Exposing and reusing it would expand the API and change the
  concurrency guarantee rather than simplify equivalent behavior.
- **Combine all per-workflow receipts into one blob — rejected.** This would
  change persisted keys, increase unrelated-writer contention, and require
  compatibility handling. It removes no required state transition.
- **Replace accepted-normal and normal-conflict receipt fields with one generic
  stage schema — rejected.** A migration or dual-schema parser would offset the
  apparent reduction and risk the invariant that a rejected normal cancel can
  never authorize force-cancel.
- **Extract one normal/force cancellation request helper — rejected.** The API
  calls look similar, but their 409 handling, receipt mutation, retry counters,
  result categories, and eligibility are intentionally different. Extraction
  would be minor indirection, not a substantial control-flow reduction.
- **Extract a shared rotation helper — rejected.** The three rotations have
  different thresholds and limits. The small local duplication does not meet
  the major-simplification threshold.
- **Replace `findNewerRun` sorting or deduplicate queued-job timestamp parsing —
  rejected.** These are micro-optimizations or minor local cleanup without a
  material complexity reduction.
- **Remove the missing-receipt pass, `getRunIfFound`, `getJobsIfFound`, or
  post-request confirmation — rejected.** These paths provide required
  missing-run reporting, final-state confirmation, and deletion-race handling.
- **Split recorded and unrecorded candidates into separate loops — rejected.**
  This merely moves the same state transitions and duplicates their shared
  normal-cancel path.
- **Replace the receipt state machine with an established repository facility —
  rejected.** No equivalent public facility exists. The queue state machine has
  different schema, retry, lease, and conflict semantics.

An independent codebase review and a separate adversarial review reached the
same conclusion: the remaining apparent duplication is either minor or encodes
one of the required safety and concurrency distinctions.

## Code commits

None.

## Successful hosted validation

- `cd /home/runner/work/shared-workflows/shared-workflows/.github/scripts/pull-request-dashboard && node --test test_github_dispatch.mjs test_workflow_watchdog.mjs`
  — passed all 47 focused tests.
- Deterministic source review — verified the live PR identity before inspection,
  read all 1,580 lines of the GitHub PR diff, and checked every candidate against
  the required cancellation, receipt, revalidation, retry, confirmation,
  rotation, and API-limit invariants.
