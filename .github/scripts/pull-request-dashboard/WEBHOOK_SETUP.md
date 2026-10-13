# Pull Request Dashboard Webhook Setup

## 1. Netlify webhook bridge

Create a Netlify project for the webhook bridge:

- Repository: `open-telemetry/shared-workflows`
- Project name: `otel-pull-request-dashboard`
- Base directory: `.github/scripts/pull-request-dashboard`

The Netlify project receives GitHub App webhooks and persists work in a
site-wide Netlify Blobs store. It dispatches a drain but does not calculate
dashboard state or publish GitHub comments itself.

Queue-owned execution includes draft-open events. The combined processor creates
the managed status comment with the target repository GitHub App identity.
In legacy mode, draft-open events still use direct targeted dispatch.

Save the Netlify project ID as a GitHub Actions variable named
`NETLIFY_PR_DASHBOARD_PROJECT_ID` in the `shared-workflows` repository.

Save a Netlify personal access token as a GitHub Actions secret named
`NETLIFY_AUTH_TOKEN` in the `shared-workflows` repository.

`PR_DASHBOARD_EXECUTION_MODE` is an operator-controlled repository variable with
three values: `legacy`, `paused`, and `owned`. The default is `legacy`, which
retains the existing rollout. `paused` accepts work but starts no new execution.
`owned` routes normal entry points through independent live and maintenance
drains using the same processor. The deploy workflow syncs this variable to
Netlify, and its optional `execution_mode` input
allows the bridge to pause before changing GitHub's mode.

In owned mode, canaries use the drain's immutable triggering commit on main.
Stable repositories use the existing promoted commit pins, with their own
scripts and Python environments. Both channels load current repository policy
from the worker checkout. Switching execution ownership does not promote a
release or move stable code onto main.

The `shared-workflows` Actions token needs `contents: write` so workers can push
`otelbot/pull-request-dashboard-state/<repository>` and publishers can push
`otelbot/pull-request-dashboard-delivery/<repository>`. Target-repository app
permissions do not change.

The queue uses the site-wide `pr-dashboard-queue` store with strong reads and
ETag-conditional writes. Live refreshes use the `dispatcher` lease; repository
backfills and reminder sweeps use `maintenance-dispatcher`. Existing item keys,
generations, and request IDs stay in the same shards. Netlify creates the store
on its first write. The drain workflow authenticates claim, heartbeat,
acknowledgment, and finish calls with
a short-lived GitHub OIDC token restricted to:

- `open-telemetry/shared-workflows`
- `.github/workflows/pull-request-dashboard-drain.yml`
- `refs/heads/main`
- the `protected` environment
- audience `otel-pr-dashboard-queue`

The main dashboard workflow and author-reminder sweep can authenticate enqueue,
status, and stats requests with the same main-branch, protected-environment
restrictions. They cannot activate, claim, heartbeat, acknowledge, or finish a
dispatcher or acquire/release repository delivery ownership. No Netlify runtime
token is shared with these workflows. The existing `NETLIFY_AUTH_TOKEN` remains
limited to deployment and environment
configuration.

The `dashboard-queue-recover` scheduled function reclaims expired worker and
dispatcher leases independently for each lane. Events normally request their
lane's drain immediately. Scheduled recovery runs every five minutes as a
failure backstop and also
requests a drain once a retry backoff (`notBefore`) has elapsed. An item whose lease expires
repeatedly without an acknowledgment is moved to the shard's dead letters
instead of being requeued forever. Recovery logs include queued and inflight
counts, retry and dead-letter counts, pending backfills and reminder sweeps,
oldest unfinished work, and the dispatcher lease deadline, labeled by lane.
Recovery of one lane is attempted even when the other fails.
In owned mode, recovery also redispatches a requested generation that has not
activated after 15 minutes. This does not renew its lease or grant ownership.
The first run to receive a runner must still win activation; the others are
harmless duplicates.

The drain has no GitHub concurrency group. It acquires active ownership only
after receiving a runner, and its lease remains live through code preparation,
calculation, state persistence, delivery, publication, and receipt updates.
Success acknowledges that combined path, not a workflow dispatch. Retries repeat
the item with existing accepted state and delivery receipts. An event during
processing requests a dirty follow-up generation.

Calculation and accepted-state persistence can overlap across lanes. Delivery,
publication, receipt updates, and reminder writes acquire a shared per-repository
lease after calculation and read current accepted state under that lease.
Heartbeats renew this ownership alongside dispatcher and item leases. Its expiry
and generation fence stale releases; waiting for it still observes the worker's
lease and processing deadlines. No GitHub concurrency group blocks a replacement.

The `dashboard-workflow-watchdog` scheduled function runs every 15 minutes. It
force-cancels an automated dashboard run after 30 minutes when a newer run is
queued behind it in the same concurrency group. It rechecks both runs and all
jobs before requesting cancellation and checks the resulting run state. Reruns
use their current attempt's start time rather than the original run's age. If no
jobs have completed, none may have received a runner or started a step. For
partially completed runs, at least one job must still be waiting or queued,
and every unfinished job must have waited without a runner or started step
for at least 30 minutes. In legacy and paused modes the watchdog covers queue drains,
hourly dashboard backfills, targeted dashboard dispatches, and webhook
deployments. Targeted dispatch run names expose their concurrency group so
the watchdog does not pair unrelated repository or pull request updates.
Queued publishers also match by
repository using their job names and the targeted run's repository. A
repository-wide publisher or backfill can replace a stalled publisher for any
pull request, but a targeted publisher only replaces a stalled publisher for the
same pull request, because it would not deliver the other pull request. Publisher enqueue times determine which
request is newer, even when its backfill started earlier or is still processing
other repositories. A queued failure notification does not affect this matching. A newer manual backfill can unblock an automated publisher,
but the watchdog does not cancel manual runs. Each invocation lists jobs for at most 16 runs per publisher workflow: the stale runs
rotate fairly, half of the rest goes to the newest runs, and the other half rotates
through older runs, including manual backfills. The older-run rotation advances only
after a complete sweep of the stale runs, so every stale run is eventually checked
against every older run. Publisher jobs are fetched again
before cancellation to check for a newly assigned runner or started step.
In owned mode it watches only webhook deployment; queue expiry and unfinished
work replace publisher concurrency as the dashboard health signals.

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

On the first deploy, the workflow creates the dispatcher client ID, encoded
private key, `PR_DASHBOARD_QUEUE_MODE`, and `PR_DASHBOARD_EXECUTION_MODE` in the
Production Functions environment. Later deploys update only their Production
values, preserving
the existing scope and the private key's secret setting. If an older Netlify
project still has the deprecated `OTELBOT_SHARED_WORKFLOWS_APP_ID`, remove it
once in Netlify's environment settings after confirming the client ID is set.
The deploy workflow does not remove unrelated or deprecated variables.

Deploy contexts:

- Production

## 5. Workflow dispatch contract

Owned execution always queues webhooks, regardless of the legacy queue mode.
Webhooks and targeted PR/head runs use the live lane.
Manual and scheduled dashboard runs enqueue repository backfills when both
`pr_number` and `head_sha` are empty, in the maintenance lane. A repository
backfill invokes the existing bounded cursor-based CLI, not a queue entry for
every open PR. It retains
closed-PR cleanup, initial population, full-publication generations, and large
repository rendering. Initial population can continue through multiple claims;
later backfills keep the existing bounded round-robin behavior.

Only scheduled backfills prepare due author nudges. A scheduled request that
coalesces with manual work preserves that scheduling intent. The reminder
workflow queues its write sweeps in maintenance and keeps dry-run sweeps read-only.

Drain dispatches include `queue_lane`, either `live` or `maintenance`, along with
`dispatcher_generation`. Omitted lanes default to `live` for legacy refresh
dispatches. Enqueue and status entry points derive the lane from the item kind
or key; progress queries do not need a new input.

An enqueue run's summary says "Work accepted, not completed" and reports an item
key, generation, and request ID. To inspect progress, use all three values
from the enqueue summary in a dashboard dispatch on main:

```sh
gh workflow run pull-request-dashboard.yml \
  --repo open-telemetry/shared-workflows --ref main \
  -f request_item_key='ecosystem-explorer#backfill' \
  -f request_generation=12 \
  -f request_id='<request ID from the enqueue summary>'
```

Status can be `queued`, `inflight`, `completed`, `dead`, or `unknown`. Queued
status includes retry attempts and the latest error when available. Completion
receipts are bounded; `unknown` means the receipt is unavailable, not success.
Worker run logs and scheduled recovery logs provide the corresponding queue
health and execution code refs.

### Legacy dispatch during migration

In legacy execution, the queue mode selects one of two dispatch contracts.

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
  a fork report no pull request association.
- The central workflow validates repository and target inputs before use.
  Owned enqueue also validates the trigger event and rejects owner-prefixed
  repository names, conflicting PR/head inputs, and malformed targets.

## 6. Deployment and rollback

These operations change production behavior and require operator approval.
Do not promote a release or cancel unrelated runs as part of this migration.
The stable rollout pins remain unchanged throughout.

### Enable queue-owned execution

1. Deploy this change with `PR_DASHBOARD_EXECUTION_MODE` unset or `legacy`.
   Check that the bridge still accepts legacy refreshes and that existing queue
   records remain readable. This deployment alone must not enable owned writers.
2. Pause the bridge first, while GitHub still uses legacy mode:

   ```sh
   gh workflow run pull-request-dashboard-deploy-webhook.yml \
     --repo open-telemetry/shared-workflows --ref main \
     -f execution_mode=paused
   ```

   Wait for that deployment to finish. Webhooks now persist refreshes without
   dispatching either path.
3. Set GitHub's mode to paused:

   ```sh
   gh variable set PR_DASHBOARD_EXECUTION_MODE \
     --repo open-telemetry/shared-workflows --body paused
   ```

   New scheduled/manual backfills and sweeps now enqueue rather than write.
   Let every pre-cutover targeted, backfill, reminder, and drain run finish.
   Inspect all jobs, including runnerless publisher jobs. Retiring a specific
   old run requires separate cancellation approval; a completed enqueue run is
   not evidence that its older publisher finished. Keep the watchdog enabled
   for these legacy runs.
4. After no legacy writer remains, set the repository variable to `owned`, then
   deploy the bridge with `execution_mode=owned`. The interval between those
   operations is safe because the paused bridge accepts work but grants no
   new claims. A new enqueue or scheduled recovery starts the drain.
5. Follow a canary request and a stable request through completion. Confirm the
   logged immutable code refs, delivery receipts, full-publication progress,
   and queue age. No fleet-wide release promotion is needed.

### Upgrade an owned installation

Git checkout isolation is supplied by the current drain worker, including for
pinned stable scripts. It needs no release promotion or queue migration. For
that worker upgrade, use the same pause-and-retire sequence below; all
pre-upgrade drains in both lanes must retire before resuming with isolated
checkouts.

Before merging or deploying a change to execution lanes or delivery ownership,
pause the bridge and GitHub with the commands above, then let every pre-upgrade
drain finish, including runs waiting for a runner. Do not run workers with
different ownership rules concurrently.

Deploy the bridge with both lanes while paused. Queued items, request IDs, generations, and
completion receipts remain in their existing shards; no item migration is
needed. Resume GitHub and the bridge in owned mode only after all pre-upgrade drains
have retired. Scheduled recovery can then start each lane independently.

### Return to legacy execution

Keep this queue implementation and worker available until control items have
finished. Schema 1 refresh records remain compatible with the old queue, but an
older worker cannot process active `backfill` or `reminders` items.

1. Stop scheduled/manual control producers by disabling the central dashboard
   and reminder workflows. While still owned, let pending backfills and reminder
   sweeps finish. Check that recovery health reports `backfills=0` and
   `reminderSweeps=0`. Do not delete control records to make those counts zero.
2. Deploy the bridge paused, then set the repository variable to paused using
   the commands above. Already claimed work can finish; no new claims start.
   Wait until no active writer or inflight item remains. If a worker died,
   let lease recovery return its item to the queue before proceeding.
3. Verify that only ordinary PR/head refreshes remain queued. Set GitHub's mode
   to `legacy`, then deploy the bridge with `execution_mode=legacy`. Re-enable
   the producer workflows. The paused interval preserves incoming refreshes.
4. Only after active control items are absent may an older bridge/worker
   deployment replace this one. Leave accepted-state and delivery branches
   intact so receipts continue suppressing duplicates.

The execution change does not alter accepted-state or delivery formats, so this
rollback does not require releasing or promoting dashboard code. Existing
version guards still apply to any separate code rollback. Do not bypass them
or discard delivery receipts to make older code publish.

### Manual maintenance

Any standalone command that writes dashboard state, GitHub, or Slack must use
the same pause-and-retire procedure. This includes direct draft-status updates
and reminder write sweeps. Keep the bridge paused for the whole maintenance
command and resume only after it exits. Dry-run reminder sweeps remain safe
without acquiring ownership.

### Lease-loss and delivery limits

The heartbeat monitor stops execution before lease expiry and terminates the
subprocess tree on lease loss. Expired or superseded workers cannot activate,
claim, heartbeat, or acknowledge newer work. Failed work retries the combined
processing path with bounded backoff; repeated abandonment eventually becomes a
dead letter rather than an endless redispatch.

Delivery is not exactly once. A GitHub or Slack call can succeed immediately
before worker death or receipt persistence failure. Existing comment markers,
live-state reconciliation, and durable delivery receipts suppress ordinary
duplicates, but that external-success/receipt-loss window remains.
