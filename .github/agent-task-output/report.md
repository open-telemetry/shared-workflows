# PR #420 simplification report

The live pull request was confirmed open at the required
`open-telemetry/shared-workflows:trask-recover-stalled-dashboard-runs` head and
exact SHA `431de8a411087da1b893b3cde172b36c1750f807`. The inventory used the live
GitHub pull request diff.

## Outcome

No candidate met the required bar for a major, behavior-preserving
simplification, so there are no code commits.

## Candidate inventory

- **Unify normal- and force-cancellation request paths — rejected.** The shared
  request/409/confirmation skeleton is small, while the receipt replacement
  versus mutation rules, timestamps, counters, retry limits, stored details,
  result arrays, and eligibility gates are stage-specific security and
  concurrency invariants. A common path would retain nearly all branching or
  hide it behind callbacks, trading explicit state-machine behavior for
  indirection without materially reducing complexity.
- **Reuse the dashboard queue mutation facility for watchdog receipts —
  rejected.** The relevant compare-and-swap implementation is a private
  `DashboardQueue` method with queue-specific retry behavior. Exposing or
  generalizing it would broaden directly affected code and change the
  watchdog's deliberate fail-on-concurrent-write guarantee.
- **Extract rotation, matching, lookup, or receipt helpers further —
  rejected.** Existing helpers already centralize workflow matching,
  newer-run selection, and 404 handling. Additional extraction would be minor
  deduplication or stylistic rearrangement rather than a major simplification.
- **Remove receipt states, post-request confirmation, guarded revalidation,
  conflict retry limits, or backlog rotation — rejected.** Each would change
  the PR's documented cancellation semantics, error reporting, safety checks,
  concurrency guarantees, or starvation bounds.
- **Consolidate the GitHub run lookup and force-cancel client methods —
  rejected.** They are thin wrappers over distinct HTTP methods and endpoints;
  combining them would add parameterized indirection without removing
  substantial complexity.

## Validation

- `node --test test_workflow_watchdog.mjs test_github_dispatch.mjs` — passed
  all 47 focused tests in the hosted environment at the verified source head.

