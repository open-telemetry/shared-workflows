# Pull Request Dashboard Webhook Setup

## 1. Netlify webhook bridge

Create a Netlify project for the webhook bridge:

- Repository: `open-telemetry/shared-workflows`
- Project name: `otel-pull-request-dashboard`
- Base directory: `.github/scripts/pull-request-dashboard`

The Netlify project only receives GitHub App webhooks and dispatches the central
GitHub Actions workflow. It does not run full dashboard rebuilds and does not own
dashboard state.

Save the Netlify project ID as a GitHub Actions variable named
`NETLIFY_PR_DASHBOARD_PROJECT_ID` in the `shared-workflows` repository.

Save a Netlify personal access token as a GitHub Actions secret named
`NETLIFY_AUTH_TOKEN` in the `shared-workflows` repository.

Disable Deploy Previews. PR preview deploys are unused and only add noise to
PRs. In Netlify, go to **Project configuration** -> **Build & deploy** ->
**Continuous Deployment** -> **Branches and deploy contexts**, select
**Configure**, and disable Deploy Previews.

## 2. GitHub Apps

Use two GitHub Apps to keep permissions narrow:

- a target repository app that receives target repository webhooks and grants
  dashboard data access
- a shared-workflows dispatcher app that can dispatch the central workflow

### Target repository app

Create a GitHub App:

- Name: `OpenTelemetry PR Dashboard`
- Homepage URL: `https://opentelemetry.io`
- Webhook URL: `https://otel-pull-request-dashboard.netlify.app/.netlify/functions/github-webhook`

Generate and save a webhook secret:

```bash
openssl rand -hex 32
```

Repository permissions:

- Checks: read-only
- Contents: read-only
- Issues: read and write
- Metadata: read-only
- Pull requests: read-only

Organization permissions:

- Members: read-only

Permission rationale:

| Permission | Access | Why it is needed |
| ---------- | ------ | ---------------- |
| Checks | Read | Required to subscribe to check-suite events and to read check data for dashboard rows. |
| Contents | Read | Reads PR commits and repository metadata needed by pull/commit APIs. |
| Issues | Read and write | Finds/creates/updates the dashboard issue and posts review-guidance comments on PRs. |
| Metadata | Read | Required by GitHub for GitHub App repository access. |
| Pull requests | Read | Required to subscribe to PR review/comment/thread events and to read PR details, reviews, review comments, commits, and GraphQL review threads. |
| Members | Read | Reads approver-team membership configured in `repositories.json`. |

PR conversation comments are covered by `Issues: read and write`. The dashboard
does not create inline review comments, submit reviews, or resolve review
threads, so it does not need `Pull requests: write`.

Subscribe to events:

- Check suite
- Pull request
- Issue comment
- Pull request review
- Pull request review comment
- Pull request review thread

Event rationale:

| Event | Why it is needed |
| ----- | ---------------- |
| Check suite | Refreshes CI status when checks are requested, rerequested, or completed. |
| Pull request | Refreshes dashboard rows when PR state, draft status, labels, assignees, branches, or metadata change. |
| Issue comment | Refreshes PR conversation state when PR issue comments are created, edited, or deleted. |
| Pull request review | Refreshes approval/change-request state and posts guidance for submitted reviews with review comments. |
| Pull request review comment | Refreshes inline review-comment discussion state. |
| Pull request review thread | Refreshes when inline review threads are resolved or unresolved. |

Create the app, update the logo, and generate a private key.

Save the app credentials in the `shared-workflows` repository:

- GitHub Actions variable `PR_DASHBOARD_APP_ID` - target repository app ID
- GitHub Actions secret `PR_DASHBOARD_PRIVATE_KEY` - private key PEM for the
  target repository app

### Shared-workflows dispatcher app

Use the [repo-specific otelbot app](https://github.com/open-telemetry/community/blob/main/assets.md#otelbot-sig-specific) for `open-telemetry/shared-workflows` to
dispatch the central workflow.

Repository permissions:

- Actions: read and write
- Metadata: read-only

This app does not need to subscribe to target repository events. It only needs
access to `open-telemetry/shared-workflows` so the webhook bridge can call the
workflow dispatch API.

## 3. Install the app

Install the target repository app on every repository listed in
`repositories.json`.

## 4. Netlify environment variables

Add this environment variable to the Netlify project for the Production deploy
context.

Secrets:

- `GITHUB_WEBHOOK_SECRET` - same webhook secret as the target repository app

The deploy workflow syncs these GitHub Actions values into the Netlify
Production function environment before deployment:

- GitHub Actions variable `OTELBOT_SHARED_WORKFLOWS_APP_ID` - repo-specific
  otelbot app ID
- GitHub Actions secret `OTELBOT_SHARED_WORKFLOWS_PRIVATE_KEY` - private key PEM
  for the repo-specific otelbot app that dispatches the central workflow; the
  deploy workflow base64-encodes this secret before storing it in Netlify as
  `OTELBOT_SHARED_WORKFLOWS_PRIVATE_KEY_BASE64`

The webhook function also supports `OTELBOT_SHARED_WORKFLOWS_PRIVATE_KEY` if the
deployment environment can store a multiline PEM value directly.

Deploy contexts:

- Production

## 5. Workflow dispatch contract

The webhook bridge should dispatch `pull-request-dashboard.yml` in
`open-telemetry/shared-workflows` with these inputs:

```json
{
  "repository": "opentelemetry-java-instrumentation",
  "pr_number": "12345",
  "trigger_event": "pull_request_review_comment",
  "trigger_action": "created",
  "trigger_review_id": "67890"
}
```

Notes:

- `repository` is the short repository name under `open-telemetry`.
- Omit `pr_number` or set it to an empty string for a full rebuild.
- `trigger_review_id` is only required for `pull_request_review` `submitted`
  events when review guidance may need to be posted.
- The central workflow validates these inputs before using them.
