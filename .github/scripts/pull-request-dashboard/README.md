# Pull request dashboard

This directory contains the central pull request dashboard implementation used by
`.github/workflows/pull-request-dashboard.yml` in this repository.

The workflow runs from `open-telemetry/shared-workflows` and targets repositories
listed in `repositories.json`. Target repositories do not need to host dashboard
workflow files.

See `RATIONALE.md` for the architecture and tradeoffs behind this design.
See `WEBHOOK_SETUP.md` for GitHub App webhook permissions and dispatch setup.

## Configuration

Add target repositories to `repositories.json`:

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

The dashboard issue is discovered dynamically in the target repository by the
`dashboard` label and `Pull Request Dashboard` title. If it does not exist, the
publish step creates the label and issue.

The target repository GitHub App is installed on each configured repository.
The workflow creates repository-scoped app installation tokens with
`PR_DASHBOARD_CLIENT_ID` and `PR_DASHBOARD_PRIVATE_KEY`, then uses those tokens for
target repository API reads/writes and approver team membership reads.

Slack notifications use the shared `SLACK_WEBHOOK_URL` secret. Each repository
can route notifications to its own `slack_channel` and map GitHub logins to
Slack user IDs via `slack_user_mapping`. Repositories without `slack_channel`
configured skip Slack notification processing.

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

## Running

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

## Notes

- Full rebuilds run in GitHub Actions, not Netlify background functions.
- GraphQL review threads are paged in groups of 10 to reduce GraphQL point cost
  while preserving the existing thread/comment data shape.
- Slack notification state remains git-backed and repository-namespaced.
