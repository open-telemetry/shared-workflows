# Survey on merged PR (non-member)

Reusable GitHub Actions workflow that posts a survey link to a pull request when it is merged, if the author is a new contributor. Used to gather feedback from new contributors about their OpenTelemetry contribution experience.

The survey link automatically includes the caller repository as a query parameter (`entry.1540511742=<owner/repo>`), so responses collected in the shared OpenTelemetry Google Form are tagged with the source repo.

## Prerequisites

**Confirm that your repository is one of the options in the survey's first question** — a select/dropdown that identifies which repository the response is about. Open the [survey form](https://docs.google.com/forms/d/e/1FAIpQLSf2FfCsW-DimeWzdQgfl0KDzT2UEAqu69_f7F2BVPSxVae1cQ/viewform) and check whether your repository appears in that list.

If your repository is **not** listed, request its addition by posting a message in the [#contributor-experience channel on CNCF Slack](https://cloud-native.slack.com/archives/C06TMJ2R0SK). A maintainer will add your repository to the form's options. Wait until your repository appears in the dropdown before enabling this workflow.

## How to use

Add a caller workflow to your repository, for example `.github/workflows/survey-on-merged-pr.yml`:

```yaml
name: Survey on merged PR

on:
  pull_request_target: # zizmor: ignore[dangerous-triggers] — this workflow only calls the reusable shared workflow; no PR code is checked out or executed.
    types: [closed]
    branches: [main]

permissions: {}

jobs:
  survey:
    permissions:
      pull-requests: write # required by the shared workflow to post the survey comment
    uses: open-telemetry/shared-workflows/.github/workflows/survey-on-merged-pr.yml@<sha-or-tag>
```

Pin `<sha-or-tag>` to a commit SHA or release tag in this repository. No inputs, no secrets — the workflow uses the built-in `GITHUB_TOKEN` and posts as `github-actions[bot]`.

## Required permissions

The caller's job-level `permissions:` block **must** grant `pull-requests: write`. The shared workflow needs it to post the survey comment via `gh pr comment`.

A [reusable workflow cannot elevate its own permissions beyond what the caller grants](https://docs.github.com/en/actions/using-workflows/reusing-workflows), so a caller job without `pull-requests: write` will fail with a permission error when the comment step runs.

The `zizmor: ignore[dangerous-triggers]` inline comment on the `pull_request_target:` line is only relevant if your repo runs [zizmor](https://github.com/zizmorcore/zizmor); it silences the false-positive finding since this caller doesn't check out any PR-controlled code — it just invokes the shared workflow which itself operates entirely through the GitHub API.

## Behavior notes

- **Not idempotent per author**: the workflow posts a survey comment on **every** merged PR by a non-member, not just the first. If the same external contributor gets several PRs merged, they receive the survey comment on each one.
