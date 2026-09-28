# Pull request dashboard webhook setup

## 1. Create the Netlify project

Create a Netlify project with:

- Repository: `open-telemetry/shared-workflows`
- Project name: `otel-pull-request-dashboard`
- Base directory: `.github/scripts/pull-request-dashboard`

In Netlify, go to **Project configuration** -> **Build & deploy** ->
**Continuous Deployment** -> **Branches and deploy contexts** -> **Configure**
and disable Deploy Previews.

## 2. Configure the target repository GitHub App

Create a GitHub App with:

- Name: `OpenTelemetry PR Dashboard`
- Homepage URL: `https://opentelemetry.io`
- Webhook URL: `https://otel-pull-request-dashboard.netlify.app/.netlify/functions/github-webhook`

Generate a webhook secret and enter it in the app's webhook settings:

```bash
openssl rand -hex 32
```

Repository permissions:

- Checks: read-only
- Commit statuses: read-only
- Contents: read-only
- Issues: read and write
- Metadata: read-only
- Pull requests: read and write

Organization permissions:

- Members: read-only

Subscribe to events:

- Check suite
- Pull request
- Issue comment
- Pull request review
- Pull request review comment
- Pull request review thread
- Status

Do not subscribe to **Check run**. Create the app, generate a private key,
and install it on every repository listed in `repositories.json`.

## 3. Configure the dispatcher GitHub App

Use the [repo-specific otelbot app](https://github.com/open-telemetry/community/blob/main/assets.md#otelbot-sig-specific)
for `open-telemetry/shared-workflows`. Install it only on that repository.

Repository permissions:

- Actions: read and write
- Contents: read and write
- Metadata: read-only
- Pull requests: read and write
- Workflows: read and write

## 4. Set credentials and deploy

In `open-telemetry/shared-workflows`, set these GitHub Actions values:

| Type | Name | Value |
| ---- | ---- | ----- |
| Variable | `NETLIFY_PR_DASHBOARD_PROJECT_ID` | Netlify project ID |
| Secret | `NETLIFY_AUTH_TOKEN` | Netlify personal access token |
| Variable | `PR_DASHBOARD_CLIENT_ID` | Target repository app client ID |
| Secret | `PR_DASHBOARD_PRIVATE_KEY` | Target repository app private key PEM |
| Variable | `OTELBOT_SHARED_WORKFLOWS_CLIENT_ID` | Dispatcher app client ID |
| Secret | `OTELBOT_SHARED_WORKFLOWS_PRIVATE_KEY` | Dispatcher app private key PEM |

In Netlify, set `GITHUB_WEBHOOK_SECRET` to the target repository app's webhook
secret for the Production Functions context.

Run the `Deploy pull request dashboard webhook` workflow on `main` once. It
sets the dispatcher credentials and queue mode in Netlify before deploying.
