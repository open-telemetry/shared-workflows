# Workflow failure issue

Reusable GitHub Actions workflow that tracks the pass/fail state of another
workflow (typically a scheduled one) by opening, commenting on, and closing a
GitHub issue in the calling repository.

This is useful because GitHub notifies only a single user of scheduled-workflow
failures. Per the [GitHub docs on scheduled workflows](https://docs.github.com/en/actions/using-workflows/events-that-trigger-workflows#actor-for-scheduled-workflows):

> Notifications for scheduled workflows are sent to the user who last modified
> the cron syntax in the workflow file.

By opening a tracking issue instead, the failure is visible to the whole team.

Behavior:

- On failure, if no tracking issue is open, a new issue titled
  `Workflow failed: <workflow name> (#<run number>)` is created.
- On a subsequent failure while an issue is already open, a comment linking to
  the failing run is added.
- On success, any open tracking issue is closed.

## How to use

Add a final job to the workflow you want to monitor. It must run after the jobs
you care about, use `if: always()` so it also runs when they fail, and grant
`issues: write`:

```yaml
jobs:
  # ... your jobs (e.g. build) ...

  workflow-failure-issue:
    permissions:
      issues: write
    needs:
      - build
    if: always()
    uses: open-telemetry/shared-workflows/.github/workflows/workflow-failure-issue.yml@<sha-or-tag>
    with:
      success: ${{ needs.build.result == 'success' }}
```

Pin `<sha-or-tag>` to a commit SHA or release tag in this repository.

### Inputs

| Name | Type | Required | Description |
| ---- | ---- | -------- | ----------- |
| `success` | boolean | yes | Whether the monitored jobs succeeded. Pass `true` to close any open tracking issue, `false` to open or comment on one. |
