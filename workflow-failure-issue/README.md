# Workflow failure issue

Reusable GitHub Actions workflow that tracks the pass/fail state of another
workflow (typically a scheduled one) by opening, commenting on, and closing a
GitHub issue in the calling repository.

This is useful because GitHub only sends scheduled-workflow failure
notifications to the user who initially created the workflow. By opening a
tracking issue instead, the whole team is notified.

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
permissions:
  contents: read

jobs:
  # ... your jobs (e.g. build) ...

  workflow-failure-issue:
    permissions:
      contents: read
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
