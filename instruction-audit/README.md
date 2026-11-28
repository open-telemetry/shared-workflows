# Central instruction audit

The audit runs from `open-telemetry/shared-workflows`. It checks a public OpenTelemetry repository against its own Copilot instructions, one rule per run, and can open one ready-for-review fix from `opentelemetrybot`'s fork. Target repositories do not install a workflow or grant the bot write access.

Audit enrollment is independent of the review-evidence collector and instruction generator. Configure [the audit's own `repositories.json`](../.github/scripts/instruction-audit/repositories.json):

```json
{
  "repositories": {
    "opentelemetry-java-instrumentation": {
      "publish_audit_fixes": false,
      "max_open_audit_prs": 5
    }
  }
}
```

Both settings are optional and default to the values above. Repository names must be lowercase, without the owner. There are no per-repository schedule, model, or path settings. Java is enrolled for audit-only operation; publication is disabled.

After rollout to `main`, dispatch:

```shell
gh workflow run instruction-audit.yml --repo open-telemetry/shared-workflows \
  --ref main -f repository=opentelemetry-java-instrumentation
```

## Passes and recovery

The central workflow serializes runs per target. It pins a pass to the target's default-branch commit and persists that commit before starting the worker. Instructions come from `.github/copilot-instructions.md` and nested `.github/instructions/**/*.instructions.md` files. Rules retain their conditions, exceptions, and document context. Rules requiring an active edit or review are not retroactive audit criteria.

The worker checks the selected rule across its applicable scope. With publication enabled, it proposes fixes for all verified violations it can safely address together, formats the edits, and runs relevant checks. Unsupported patches and unfixable findings are reported in the run summary and skipped, as in the pilot. With publication disabled, the pass stops at the first finding.

Each target has a state branch in **shared-workflows**, named `otelbot/instruction-audit-state-<repository>`. This does not collide with the pilot's existing `otelbot/instruction-audit-state` ref. Its `cursor.json` contains:

```json
{
  "anchor": {
    "path": ".github/instructions/example.instructions.md",
    "text": "- The last completed rule."
  },
  "sha": null,
  "offset": 0
}
```

`anchor` is the saved position between passes. `sha` and `offset` identify an active pinned pass. Continuations save the next offset before dispatching another run from central `main`. A complete pass resets the anchor. A reported finding stops automatic continuation, retaining the preceding completed rule unless publication succeeds.

Publishing a unique rule saves it as the anchor for the next pass. Duplicate rule text cannot identify a safe anchor. After publishing a duplicate, the workflow retains the pinned pass and advances its offset; the next manual dispatch checks the following rule instead of skipping the file or retrying the same duplicate. Publishing a duplicate at the end of the pass resets the cursor.

Worker, validation, publication, or cursor-write failures retain the active pass. Dispatch again to resume it, rather than rerunning a job, which is intentionally disabled. If automatic dispatch fails or a queued run is replaced, the saved state still resumes from the same commit and rule. To abandon a pass, stop its central runs first, then set `sha` to `null` and `offset` to `0`, retaining the anchor.

Publication uses a deterministic branch per snapshot and rule occurrence, identified by its path, text, and source line range. Identical rules at different locations get separate PRs; retries of the same occurrence reuse its PR. Cursor anchors remain `{path, text}`.

An ownership marker contains the published commit. If PR creation succeeded but saving progress failed, the next dispatch recognizes the existing PR instead of duplicating it. A manually changed PR or an orphaned branch without a confirmed PR stops publication for operator inspection. Do not delete or overwrite such branches until you establish whether GitHub accepted the preceding creation request. Closed PRs are not recreated.

## Publication boundary

Preparation, cursor writes, and continuation dispatch use narrowly scoped central `GITHUB_TOKEN` permissions. The GHAW worker receives no bot credential and has no GitHub tools. It runs with read-only repository access and `copilot-requests: write`; only the worker's local checkout can change. A separate job validates its JSON and applies the proposed patch to a fresh copy of the pinned snapshot.

Only the `protected` publication job receives `OPENTELEMETRYBOT_GITHUB_TOKEN`, following the instruction generator's identity and fork-parent checks. Git trees, commits, and branches are written to the bot fork, never the target. Only opening the PR writes to the target. The bot account needs access to its own forks and permission to propose public PRs, not membership or repository write access.

The publisher checks the cap after preparing the Git objects, immediately before creating the fork branch and PR. During migration, it counts both same-repository pilot heads and bot-fork heads whose branch starts with `otelbot/instruction-audit-`. Other forks and unrelated PRs do not count. A cap failure leaves no new audit branch and retains the active pass for a later dispatch. Central runs are serialized, but a concurrently publishing pilot is outside that lock; pause the pilot before cutover.

Generated fixes remain ready for review, matching the pilot. The workflow does not approve, merge, or post review comments. Run summaries hold coverage and validation details; generated PRs quote the selected rule and explain the fix.

## Java cutover gates

This PR does not change Java's pilot workflow, saved cursor, refs, runs, or existing PRs. Do not enable central publication or retire the pilot until these steps are complete:

1. Merge the parent instruction-generator PR and this layer. Configure the protected environment and bot secret. Confirm central read-only auditing and continuation on `main`; the operational entry point rejects feature-branch runs.
2. Stop new pilot dispatches and wait for every pilot continuation and publication to finish. Record the pilot's state-branch commit and saved `<repository>.json` cursor. Copy its exact `{path, text}` anchor to central `cursor.json` as `anchor`, with `sha: null` and `offset: 0`. Keep the pilot state branch intact for rollback. Do not migrate a running continuation's ordinal into a new snapshot.
3. Before central dispatch, read back and compare the adopted anchor byte-for-byte, and confirm the next selected rule matches the intended position. Missing, rewritten, or ambiguous anchor text skips the remainder of that instruction file, as in the pilot.
4. With maintainer agreement, enable publication and inspect the first real ready-for-review PR from `opentelemetrybot`'s fork. Verify required checks start, whether GitHub requests maintainer approval, and whether secret-dependent or fork-gated jobs are skipped. Approve a run only after reviewing its code. Do not switch to `pull_request_target` or expose secrets to fork code to bypass this gate.

On November 25, 2026, Java's Actions approval policy reported `first_time_contributors_new_to_github`, and its default workflow permission was `read`. Its main PR build subscribes to `pull_request` opened, synchronize, and reopened events without excluding forks. These settings alone do not prove the bot's first fork PR will run all required checks. That needs a real fork PR after protected-main rollout; this layer does not create one to test it.

## Development

The audit logic, rule parser, and regression cases come from the Java pilot at `c103570bb4aee3a3833294aaa905f33308bcb626`. Central orchestration and fork publication have separate tests.

```shell
python -m unittest discover -s .github/scripts/instruction-audit -p 'test_*.py'
```

Edit `instruction-audit-worker.md`, then compile with `gh aw compile instruction-audit-worker` using **gh-aw v1.2.2**. Commit the generated lock workflow and action lock. The compiler warns about target checkout before host setup. The only post-checkout host steps are compiler setup and the fixed artifact-download step; they must never execute target scripts. Keep target code execution inside the worker sandbox. Actionlint exceptions apply only to generated syntax it does not yet recognize.
