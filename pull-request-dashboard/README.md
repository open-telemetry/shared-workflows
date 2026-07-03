# Pull Request Dashboard

A centralized shared workflow that builds and publishes a per-repository pull request triage dashboard for opted-in OpenTelemetry repositories. For each target repository, the workflow scans open PRs, classifies who has the next action, publishes the dashboard as a GitHub issue, and optionally sends Slack notifications.

The workflow runs from `open-telemetry/shared-workflows` and targets the repositories listed in [`repositories.json`](../.github/scripts/pull-request-dashboard/repositories.json). Target repositories do not need to host any workflow files.

Webhook-triggered incremental runs keep active dashboards close to real time. Hourly full runs provide a backstop for missed or delayed events.

The classification cache reuses prior results for unchanged review threads, minimizing Copilot token usage.

## Dashboard columns

The dashboard groups open non-draft pull requests by who is expected to act next (e.g. *Waiting on reviewers*, *Waiting on authors*, *Waiting on maintainers*, *Waiting on external*). Draft PRs are listed separately at the bottom. Within each group, rows are sorted longest-waiting first. Every row has these six columns:

- **PR** — Pull request title and number, linked to the PR on GitHub.
- **Author** — GitHub login of the PR author.
- **Reviewers** — Reviewers who have engaged with the PR, each annotated with one or more icons:
  - ✅ approved
  - ✔️ approved (non-code-owner — does **not** count toward merge requirements)
  - 💬 has an open (unresolved) review thread on the PR
  - 🔴 requested changes
  - Combinations such as 💬✅ mean approved but with an open thread still outstanding.
- **CI** — Aggregate check status across the PR:
  - ✅ all checks passing
  - ⏳ at least one check pending, none failing
  - ❌ at least one check failing
  - `?` check data could not be fetched
- **Conflicts** — Whether the PR has merge conflicts against its base branch:
  - ✅ no conflicts
  - ❌ has conflicts
  - `?` could not be determined
- **Age** — How long the PR has been waiting on the current next-action owner,
  not the calendar age of the PR. For example, a six-month-old PR that received a
  reviewer comment yesterday shows `1d` when it is waiting on the author. When
  possible, this is based on the oldest pending thread for the routed party;
  otherwise it falls back to the latest activity from the opposite party, then
  to recent PR activity or PR creation time. Format: `<1m`, `Xm`, `Xh`, or
  `Xd`.

## How to opt in

Open a pull request that adds your repository to [`.github/scripts/pull-request-dashboard/repositories.json`](../.github/scripts/pull-request-dashboard/repositories.json):

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
| `name` | yes | Name of the repository under `open-telemetry`. |
| `approver_teams` | yes | GitHub team slugs whose members count as approvers. |
| `required_approvals` | yes | Number of approvals required for an open PR to be marked ready to merge. |
| `slack_channel` | no | Slack channel for notifications. Omit to skip Slack processing for this repository. |
| `slack_user_mapping` | no | Map of GitHub login to Slack user ID for at-mentions. |
| `large_repo` | no | If `true`, apply rendering presets that keep the dashboard body under GitHub's 65,536-character issue-body limit: cap each section (each *Waiting on …* table, the *Draft pull requests* table, and the *Diagnostics* block) at 50 rows, and omit the *Draft pull requests* section entirely. Truncated sections get a `_More X PRs not shown_` footer. Defaults to `false` (no cap, drafts shown). Enable this for very large repos with hundreds of PRs. **Also affects triggering**: webhook-driven runs are skipped for `large_repo` targets to avoid exhausting the App's hourly API quota. Hourly scheduled runs and manual `Run workflow` clicks still process them. |

Ask a maintainer or admin to add the repository under [Repository access](https://github.com/organizations/open-telemetry/settings/installations/133550497).

Once the PR is merged, the dashboard will pick up your repository on its next hourly full run. To run it sooner, see [Manual full run](#manual-full-run). The dashboard issue is discovered dynamically in your repository by the `dashboard` label and `Pull Request Dashboard` title; if it does not exist, the publish step creates the label and issue.

## One-time PR guidance comment

After the dashboard issue exists, the workflow adds one guidance comment to a
PR the first time a submitted review includes inline comments. A hidden marker
prevents repeat comments on later reviews or workflow runs.

The comment asks authors to give each review thread a clear outcome. This keeps
the dashboard from treating stale or ambiguous threads as the wrong next action.

## Configuration

The target repository GitHub App is installed on each configured repository.
The workflow creates repository-scoped app installation tokens with
`PR_DASHBOARD_CLIENT_ID` and `PR_DASHBOARD_PRIVATE_KEY`, then uses those tokens for
API reads/writes and approver team membership reads in the target repository.

Slack notifications use the shared `SLACK_WEBHOOK_URL` secret. Each repository
can route notifications to its own `slack_channel` and map GitHub logins to
Slack user IDs via `slack_user_mapping`. Repositories without `slack_channel`
configured skip Slack notification processing.

## Prerequisites

The target repository GitHub App must be installed on your repository. See [`WEBHOOK_SETUP.md`](../.github/scripts/pull-request-dashboard/WEBHOOK_SETUP.md) for the GitHub App configuration this repo uses.

## State

Dashboard state is stored on the shared state branch configured by
`DASHBOARD_STATE_BRANCH`. State files are namespaced by target repository, for
example:

```text
semantic-conventions-genai/dashboard-state.json
opentelemetry-java-instrumentation/dashboard-state.json
```

This lets one central workflow manage multiple target repositories without
state collisions.

## Implementation

The workflow YAML and supporting scripts live in this repo:

- [`.github/workflows/pull-request-dashboard.yml`](../.github/workflows/pull-request-dashboard.yml) — top-level orchestrator
- [`.github/workflows/pull-request-dashboard-repo.yml`](../.github/workflows/pull-request-dashboard-repo.yml) — per-repository job
- [`.github/workflows/pull-request-dashboard-deploy-webhook.yml`](../.github/workflows/pull-request-dashboard-deploy-webhook.yml) — webhook bridge deploy
- [`.github/scripts/pull-request-dashboard/`](../.github/scripts/pull-request-dashboard/) — Python scripts, state handling, rendering

See [`RATIONALE.md`](../.github/scripts/pull-request-dashboard/RATIONALE.md) for the architecture and tradeoffs behind the design.
See [`WEBHOOK_SETUP.md`](../.github/scripts/pull-request-dashboard/WEBHOOK_SETUP.md) for GitHub App webhook permissions and dispatch setup.

## Manual full run

To manually run a full dashboard rebuild, open the
[Pull request dashboard workflow](https://github.com/open-telemetry/shared-workflows/actions/workflows/pull-request-dashboard.yml),
choose **Run workflow**, and populate the `repository` field with the target
repository name under `open-telemetry`, for example
`opentelemetry-java-instrumentation`. Leave `repository` empty to process every
configured repository.
