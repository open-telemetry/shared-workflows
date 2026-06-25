# Pull Request Dashboard

A centrally-executed shared workflow that builds and publishes a per-repository pull request triage dashboard for opted-in OpenTelemetry repositories. The workflow scans open PRs in each target repository, classifies who has the next action, publishes the dashboard as a GitHub issue in that repository, and (optionally) sends Slack notifications.

The workflow runs from `open-telemetry/shared-workflows` and targets the repositories listed in [`repositories.json`](../.github/scripts/pull-request-dashboard/repositories.json). Target repositories do not need to host any workflow files.

## How to opt in

Open a pull request adding an entry for your repository to [`.github/scripts/pull-request-dashboard/repositories.json`](../.github/scripts/pull-request-dashboard/repositories.json):

```json
[
  {
    "name": "example-repo",
    "approver_teams": ["example-approvers"],
    "required_approvals": 1,
    "slack_channel": "#example-maintainers",
    "slack_user_mapping": {
      "octocat": "U0123456789"
    }
  }
]
```

Fields:

| Field | Required | Description |
| ----- | -------- | ----------- |
| `name` | yes | Repository name under `open-telemetry`. |
| `approver_teams` | yes | GitHub team slugs whose members count as approvers. |
| `required_approvals` | yes | Number of approvals required for an open PR to be marked ready to merge. |
| `slack_channel` | no | Slack channel for notifications. Omit to skip Slack processing for this repository. |
| `slack_user_mapping` | no | Map of GitHub login to Slack user ID for at-mentions. |

Once the PR is merged, the dashboard will pick up your repository on its next scheduled run. The dashboard issue is discovered dynamically in your repository by the `dashboard` label and `Pull Request Dashboard` title — if it does not exist, the publish step creates the label and issue.

## Prerequisites

The target repository GitHub App must be installed on your repository. See [`WEBHOOK_SETUP.md`](../.github/scripts/pull-request-dashboard/WEBHOOK_SETUP.md) for the GitHub App configuration this repo uses.

## Implementation

The workflow YAML and scripts that run this workflow live in this repo at:

- [`.github/workflows/pull-request-dashboard.yml`](../.github/workflows/pull-request-dashboard.yml) — top-level orchestrator
- [`.github/workflows/pull-request-dashboard-repo.yml`](../.github/workflows/pull-request-dashboard-repo.yml) — per-repository job
- [`.github/workflows/pull-request-dashboard-deploy-webhook.yml`](../.github/workflows/pull-request-dashboard-deploy-webhook.yml) — webhook bridge deploy
- [`.github/scripts/pull-request-dashboard/`](../.github/scripts/pull-request-dashboard/) — Python scripts, state handling, rendering

See [`RATIONALE.md`](../.github/scripts/pull-request-dashboard/RATIONALE.md) for the architecture and tradeoffs behind the design.

## Manual runs

Manual run for one repository:

```text
workflow_dispatch:
  repository: opentelemetry-java-instrumentation
```

Manual targeted PR refresh:

```text
workflow_dispatch:
  repository: opentelemetry-java-instrumentation
  pr_number: "12345"
```

Scheduled runs process every configured repository.
