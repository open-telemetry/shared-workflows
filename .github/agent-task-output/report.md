# PR 419 simplification report

Request: `prs419-28eb2d20176d4c2b85a95fb043d403ea`

The live pull request was verified before inspection as open at
`open-telemetry/shared-workflows:trask-durable-dashboard-publishing`, commit
`3e586e6983c57ca19a9b854582c12b18cbdcc317`. The actual GitHub pull request
diff was reviewed.

## Inventory

1. **Combine delivery, dashboard issue publication, and receipt recording into
   one invocation — rejected.** This could remove one subprocess boundary, but
   it would couple delivery to issue publication or move receipt ownership into
   the publisher. The receipt must be written only after both full delivery and
   dashboard issue publication succeed. The current orchestration makes that
   ordering explicit in both the direct workflow and queue worker, so the
   proposed consolidation does not establish behavior preservation and is not
   a qualifying simplification.
2. **Store needed and delivered generations together or on one branch —
   rejected.** The accepted-state branch is written by backfills and owns the
   obligation, while the delivery branch is written by publishers and owns the
   receipt. Combining them would create cross-writer lost-update risk or require
   additional synchronization, weakening the existing CAS model rather than
   simplifying it.
3. **Replace the monotonic generation and receipt with a boolean marker —
   rejected.** A boolean cannot distinguish publication of an older generation
   from publication of the latest generation when a backfill commits between
   delivery and acknowledgement. Generation ordering is required for durable
   coalescing recovery.
4. **Reuse backfill state or delivery-version state for the generation —
   rejected.** Backfill state is disposable cursor/failure bookkeeping, and
   delivery versions are compatibility claims on the publisher-owned branch.
   Reusing either would conflate ownership and compatibility semantics and
   would not preserve the independent durable obligation.
5. **Acknowledge inside `deliver_with_state()` or before dashboard issue
   publication — rejected.** Delivery success alone is insufficient. An early
   acknowledgement could hide a failed issue publication and violate the
   full-dashboard guarantee.
6. **Remove the compatibility claim when acknowledging — rejected.** The claim
   prevents an older publisher from acknowledging state whose receipt protocol
   it does not understand.
7. **Replace the hourly health check with publisher outputs or matrix status —
   rejected.** Outputs can disappear with a replaced pending job, and matrix
   cancellation cannot prove that durable work was drained. Reading both
   branches is required to detect an outstanding generation.
8. **Replace the health check's direct `gh api` read with the repository
   `github_cli.gh_api` wrapper — rejected as minor.** This would remove a small
   amount of subprocess handling but would not eliminate a substantial state
   or control-flow branch. It also requires preserving the special missing-file
   behavior. It does not meet the task's threshold for a major simplification.
9. **Suppress the final queue publication after a recovered full publication —
   rejected.** Later items in the same batch can update accepted state after
   the recovered publication, so the final publication preserves current
   dashboard behavior.

## Result

No candidate established both a major material reduction in implementation
complexity and preservation of the required delivery, ordering, compatibility,
cancellation, CAS, failure, and dashboard behavior. The source implementation
was therefore left unchanged.

Code commits, in order: none.

## Hosted validation

- `python3 -m unittest test_state.py test_dashboard_async.py test_delivery.py test_process_queue_batch.py test_check_full_publish_health.py test_rollout.py`
  passed all 110 focused tests after installing the repository's pinned Python
  requirements.
- `git diff --check 571e843a7fac66c62df6e20b759d220601cc15b6..HEAD`
  passed with no whitespace errors in the source PR diff.
