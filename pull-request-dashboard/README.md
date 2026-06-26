# Pull Request Dashboard

A centrally-executed shared workflow that builds and publishes a per-repository pull request triage dashboard for opted-in OpenTelemetry repositories. The workflow scans open PRs in each target repository, classifies who has the next action, publishes the dashboard as a GitHub issue in that repository, and (optionally) sends Slack notifications.

The workflow runs from `open-telemetry/shared-workflows` and targets the repositories listed in [`repositories.json`](../.github/scripts/pull-request-dashboard/repositories.json). Target repositories do not need to host any workflow files.

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
- **Age** — **Time the party currently expected to act has been waiting — _not_ the calendar age of the PR.** A six-month-old PR that received a reviewer comment yesterday shows `1d` (the author has been holding the ball for one day), not six months. Computed in this priority order:
  1. Time since the oldest pending review thread directed at the routed party (e.g. the oldest unresolved thread the author owes a reply on, when the PR is *Waiting on authors*).
  2. Time since the most recent activity from the _opposite_ party — for *Waiting on authors*, the latest approver/reviewer activity; for *Waiting on reviewers* / *Waiting on maintainers*, the latest author activity; for *Waiting on external*, the latest external activity.
  3. Time since the most recent activity timestamp overall.
  4. PR creation time (last-resort fallback).

  Format: `<1m`, `Xm` (under an hour), `Xh` (under a day), or `Xd` (days).

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

Ask a maintainer or admin to add the repo to the list of repositories under [Repository access](https://github.com/organizations/open-telemetry/settings/installations/133550497).

Once the PR is merged, the dashboard will pick up your repository on its next scheduled run. To manually trigger it sooner, see [Manual runs](#manual-runs) below. The dashboard issue is discovered dynamically in your repository by the `dashboard` label and `Pull Request Dashboard` title — if it does not exist, the publish step creates the label and issue.

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
target repository API reads/writes and approver team membership reads.

Slack notifications use the shared `SLACK_WEBHOOK_URL` secret. Each repository
can route notifications to its own `slack_channel` and map GitHub logins to
Slack user IDs via `slack_user_mapping`. Repositories without `slack_channel`
configured skip Slack notification processing.

## Prerequisites

The target repository GitHub App must be installed on your repository. The workflow creates repository-scoped app installation tokens with `PR_DASHBOARD_CLIENT_ID` and `PR_DASHBOARD_PRIVATE_KEY`. See [`WEBHOOK_SETUP.md`](../.github/scripts/pull-request-dashboard/WEBHOOK_SETUP.md) for the GitHub App configuration this repo uses.

## State

Dashboard state is stored on the shared state branch configured by
`DASHBOARD_STATE_BRANCH`. State files are namespaced by target repository, for
example:

```text
semantic-conventions-genai/dashboard-state.json
opentelemetry-java-instrumentation/dashboard-state.json
```

This allows one central workflow to manage multiple target repositories without
state collisions.

## Implementation

The workflow YAML and scripts that run this workflow live in this repo at:

- [`.github/workflows/pull-request-dashboard.yml`](../.github/workflows/pull-request-dashboard.yml) — top-level orchestrator
- [`.github/workflows/pull-request-dashboard-repo.yml`](../.github/workflows/pull-request-dashboard-repo.yml) — per-repository job
- [`.github/workflows/pull-request-dashboard-deploy-webhook.yml`](../.github/workflows/pull-request-dashboard-deploy-webhook.yml) — webhook bridge deploy
- [`.github/scripts/pull-request-dashboard/`](../.github/scripts/pull-request-dashboard/) — Python scripts, state handling, rendering

See [`RATIONALE.md`](../.github/scripts/pull-request-dashboard/RATIONALE.md) for the architecture and tradeoffs behind the design.
See [`WEBHOOK_SETUP.md`](../.github/scripts/pull-request-dashboard/WEBHOOK_SETUP.md) for GitHub App webhook permissions and dispatch setup.

## Manual runs

Manual run for one repository:

```text
workflow_dispatch:
  repository: opentelemetry-java-instrumentation
```

Scheduled runs process every configured repository.

## Notes

- Full rebuilds run in GitHub Actions, not Netlify background functions.
- GraphQL review threads are paged in groups of 10 to reduce GraphQL point cost
  while preserving the existing thread/comment data shape.
- Slack notification state remains git-backed and repository-namespaced.