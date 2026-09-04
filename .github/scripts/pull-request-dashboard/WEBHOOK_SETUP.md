# Pull Request Dashboard Webhook Setup

## 1. Netlify webhook bridge

Create a Netlify project for the webhook bridge:

- Repository: `open-telemetry/shared-workflows`
- Project name: `otel-pull-request-dashboard`
- Base directory: `.github/scripts/pull-request-dashboard`

The Netlify project receives GitHub App webhooks and either dispatches a direct
targeted refresh or coalesces the refresh in a site-wide Netlify Blobs store for
the queue drain workflow. It does not run dashboard backfills or own dashboard
state.

An `opened` event for a draft pull request always uses direct targeted dispatch,
including in `canary` and `all` queue modes. The central workflow still creates
the managed status comment with the target repository GitHub App identity.

Save the Netlify project ID as a GitHub Actions variable named
`NETLIFY_PR_DASHBOARD_PROJECT_ID` in the `shared-workflows` repository.

Save a Netlify personal access token as a GitHub Actions secret named
`NETLIFY_AUTH_TOKEN` in the `shared-workflows` repository.

Queue rollout requires no queue-mode variable or manual Netlify deployment. The
deployment workflow selects `canary` until the stable pinned repository workflow
contains the shared publisher lock and its timeout. In that mode, only
`opentelemetry-java-instrumentation` and `shared-workflows` use the queue.

Merging the promotion pull request updates the stable workflow pin and triggers
another Netlify deployment. Once that pin contains the queue-compatible
publisher behavior, the deployment automatically selects `all` and queues every
accepted targeted webhook refresh.

The drain workflow runs the dashboard scripts from the commit it was dispatched
at. Before promotion, queued canary repositories therefore exercise the merged
code while every other repository continues to use direct targeted refreshes and
the promoted stable workflow.

The queue uses the site-wide `pr-dashboard-queue` store with strong reads and
ETag-conditional writes. Netlify creates the store on its first write. The drain
workflow authenticates claim, heartbeat, acknowledgment, and finish calls with
a short-lived GitHub OIDC token restricted to:

- `open-telemetry/shared-workflows`
- `.github/workflows/pull-request-dashboard-drain.yml`
- `refs/heads/main`
- the `protected` environment
- audience `otel-pr-dashboard-queue`

No Netlify runtime token is shared with the drain workflow. The existing
`NETLIFY_AUTH_TOKEN` remains limited to deployment and environment
configuration.

The `dashboard-queue-recover` scheduled function runs every five minutes. It
reclaims expired worker and dispatcher leases, and it starts a drain for claims
whose retry delay has elapsed. A new event normally starts the singleton drain
immediately, and a later event can also pick up a delayed claim once its delay
passes. An item whose lease expires repeatedly without an acknowledgment is
moved to the shard's dead letters instead of being requeued forever.

Disable Deploy Previews. PR preview deploys are unused and only add noise to
PRs. In Netlify, go to **Project configuration** -> **Build & deploy** ->
**Continuous Deployment** -> **Branches and deploy contexts**, select
**Configure**, and disable Deploy Previews.

## 2. GitHub Apps

Use two GitHub Apps:

- a target repository app that receives target repository webhooks and grants
  dashboard data access
- a shared-workflows dispatcher app that can dispatch the central workflow
  and push validated rollout updates

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
- Commit statuses: read-only
- Contents: read-only
- Issues: read and write
- Metadata: read-only
- Pull requests: read and write

Organization permissions:

- Members: read-only

Permission rationale:

| Permission | Access | Why it is needed |
| ---------- | ------ | ---------------- |
| Checks | Read | Required to subscribe to check-suite events and to read check data for dashboard rows. |
| Commit statuses | Read | Required to subscribe to commit status events, which are the only notification for checks reported as statuses instead of check runs, and to read those status contexts in the check rollup. |
| Contents | Read | Reads PR commits and repository metadata needed by pull/commit APIs. |
| Issues | Read and write | Finds, creates, and updates the dashboard issue. |
| Metadata | Read | Required by GitHub for GitHub App repository access. |
| Pull requests | Read and write | Required to subscribe to PR review/comment/thread events; read PR details, reviews, review comments, commits, and GraphQL review threads; and create the dashboard-managed PR status comment, which is a pull request conversation comment. |
| Members | Read | Reads approver-team membership configured in `repositories.json`. |

The dashboard does not create inline review comments, submit reviews, or resolve
review threads. It manages one PR conversation comment (create, update, and
duplicate cleanup) through the issue-comments API. Because that comment lives on
a pull request, GitHub governs writing it with the `Pull requests` permission;
`Issues: read and write` covers only the separate dashboard issue.

Subscribe to events:

- Check suite
- Pull request
- Issue comment
- Pull request review
- Pull request review comment
- Pull request review thread
- Status

Do not subscribe to **Check run**. GitHub emits one check run per job, so on
`opentelemetry-collector-contrib` a single push produces roughly 137 of them
against 17 check suites, and every one is delivered to the webhook whether the
dashboard uses it or not. Subscribing to it costs an order of magnitude more
webhook traffic than every other event combined.

Event rationale:

| Event | Why it is needed |
| ----- | ---------------- |
| Check suite | Refreshes CI status when checks complete. Check suites on the default branch are ignored. |
| Pull request | Refreshes dashboard rows when PR state, draft status, labels, assignees, branches, or metadata change. |
| Issue comment | Refreshes PR conversation state when PR issue comments are created, edited, or deleted. Events generated by the dashboard App changing its own comments are ignored. |
| Pull request review | Refreshes approval/change-request state and the live PR status comment. |
| Pull request review comment | Refreshes inline review-comment discussion state. |
| Pull request review thread | Refreshes when inline review threads are resolved or unresolved. |
| Status | Refreshes CI status for required checks reported as commit statuses, such as EasyCLA, which are never part of a check suite. Statuses on the default branch are ignored. |

Create the app, update the logo, and generate a private key.

Save the app credentials in the `shared-workflows` repository:

- GitHub Actions variable `PR_DASHBOARD_CLIENT_ID` - target repository client ID
- GitHub Actions secret `PR_DASHBOARD_PRIVATE_KEY` - private key PEM for the
  target repository app

### Shared-workflows dispatcher app

Use the [repo-specific otelbot app](https://github.com/open-telemetry/community/blob/main/assets.md#otelbot-sig-specific) for `open-telemetry/shared-workflows` to
dispatch the central workflow.

Repository permissions:

- Actions: read and write
- Contents: read and write
- Metadata: read-only
- Pull requests: read and write
- Workflows: read and write

This app does not need to subscribe to target repository events. It only needs
access to `open-telemetry/shared-workflows`. Actions permission lets the webhook
bridge dispatch the central workflow. Contents and Workflows permissions let
the promotion workflow push its validated rollout update under
`.github/workflows/`, and Pull requests permission lets it open the promotion
pull request.

## 3. Install the app

Install the target repository app on every repository listed in
`repositories.json`. Install the dispatcher app only on
`open-telemetry/shared-workflows`.

## 4. Netlify environment variables

Add this environment variable to the Netlify project for the Production deploy
context.

Secrets:

- `GITHUB_WEBHOOK_SECRET` - same webhook secret as the target repository app

The deploy workflow syncs these GitHub Actions values into the Netlify
Production function environment before deployment:

- GitHub Actions variable `OTELBOT_SHARED_WORKFLOWS_CLIENT_ID` - repo-specific
  otelbot client ID
- GitHub Actions secret `OTELBOT_SHARED_WORKFLOWS_PRIVATE_KEY` - private key PEM
  for the repo-specific otelbot app that dispatches the central workflow; the
  deploy workflow base64-encodes this secret before storing it in Netlify as
  `OTELBOT_SHARED_WORKFLOWS_PRIVATE_KEY_BASE64`

The webhook function also supports `OTELBOT_SHARED_WORKFLOWS_PRIVATE_KEY` if the
deployment environment can store a multiline PEM value directly.

Deploy contexts:

- Production

## 5. Workflow dispatch contract

The queue mode selects one of two dispatch contracts.

In `off` mode, and for non-canary repositories in `canary` mode, the webhook
bridge dispatches `pull-request-dashboard.yml` in
`open-telemetry/shared-workflows` with these inputs:

```json
{
  "repository": "opentelemetry-java-instrumentation",
  "pr_number": "12345",
  "head_sha": "",
  "trigger_event": "pull_request_review_comment"
}
```

In `all` mode, and for canary repositories in `canary` mode, the bridge writes
the repository and PR or head SHA to the Blob queue. Only the webhook request
that acquires the singleton dispatcher lease dispatches
`pull-request-dashboard-drain.yml`, with this input:

```json
{
  "dispatcher_generation": "12"
}
```

The generation identifies the dispatcher lease that the drain must activate.
The drain claims repository and PR or head-SHA work from Netlify, so those
values are not workflow inputs.

Direct dispatch notes:

- `repository` is the short repository name under `open-telemetry`, and must
  match a `repositories.json` entry exactly. An owner-prefixed name is rejected.
- Omit `pr_number` or set it to an empty string for a backfill.
- Send `head_sha` instead of `pr_number` when the event carries no pull request
  number. Check and status events for a pull request whose head branch lives in
  a fork report no pull request association, so the central workflow resolves
  the head commit to an open pull request and skips the refresh when there is
  none.
- The central workflow validates `repository`, `pr_number` and `head_sha` before
  using them. `trigger_event` only selects a concurrency group, so it is
  validated on the backfill path only; the bridge is what restricts it to known
  event names.
