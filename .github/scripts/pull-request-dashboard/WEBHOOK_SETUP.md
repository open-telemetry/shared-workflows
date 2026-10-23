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

Save the Netlify project ID as a GitHub Actions variable named
`NETLIFY_PR_DASHBOARD_PROJECT_ID` in the `shared-workflows` repository.

Save a Netlify personal access token as a GitHub Actions secret named
`NETLIFY_AUTH_TOKEN` in the `shared-workflows` repository.

`PR_DASHBOARD_EXECUTION_MODE` is an operator-controlled repository variable that
must be explicitly set to `paused` or `owned`. Missing and invalid Netlify
values fail rather than select an execution default.
`paused` accepts work but starts no new execution.
`owned` routes normal entry points through independent live and maintenance
drains using the same processor. The deploy workflow syncs this variable to
Netlify, and its optional `execution_mode` input
allows the bridge to pause before changing GitHub's mode.

In owned mode, canaries use the drain's immutable triggering commit on main.
Stable repositories use the SHA and release in `stable-release.json`, with their own
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
only watches webhook deployment, in both owned and paused modes. Queue expiry
and unfinished work provide dashboard health signals.
It force-cancels a stalled deployment run after 30 minutes when a newer run is
queued behind it in the same concurrency group. It rechecks both runs and all
jobs before requesting cancellation and checks the resulting run state. Reruns
use their current attempt's start time rather than the original run's age. If no
jobs have completed, none may have received a runner or started a step. For
partially completed runs, at least one job must still be waiting or queued,
and every unfinished job must have waited without a runner or started step
for at least 30 minutes. It fetches jobs again before cancellation to check for
a newly assigned runner or started step.

Disable Deploy Previews. PR preview deploys are unused and only add noise to
PRs. In Netlify, go to **Project configuration** -> **Build & deploy** ->
**Continuous Deployment** -> **Branches and deploy contexts**, select
**Configure**, and disable Deploy Previews.

## 2. GitHub Apps

Use two GitHub Apps:

- a target repository app that receives target repository webhooks and grants
  dashboard data access
- a shared-workflows dispatcher app that can dispatch queue drains
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
| Members | Read | Reads approver-, maintainer-, and triager-team membership configured in `repositories.json`. |

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
dispatch the lane's queue drain workflow.

Repository permissions:

- Actions: read and write
- Contents: read and write
- Metadata: read-only
- Pull requests: read and write

This app does not need to subscribe to target repository events. It only needs
access to `open-telemetry/shared-workflows`. Actions permission lets the webhook
bridge dispatch the queue drain workflow. Contents permission lets
the promotion workflow push its validated rollout update under
`.github/scripts/pull-request-dashboard/stable-release.json`, and Pull requests
permission lets it open the promotion pull request.

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
  for the repo-specific otelbot app that dispatches queue drains; the
  deploy workflow base64-encodes this secret before storing it in Netlify as
  `OTELBOT_SHARED_WORKFLOWS_PRIVATE_KEY_BASE64`

The webhook function also supports `OTELBOT_SHARED_WORKFLOWS_PRIVATE_KEY` if the
deployment environment can store a multiline PEM value directly.

On the first deploy, the workflow creates the dispatcher client ID, encoded
private key and `PR_DASHBOARD_EXECUTION_MODE` in the
Production Functions environment. Later deploys update only their Production
values, preserving
the existing scope and the private key's secret setting. If an older Netlify
project still has the deprecated `OTELBOT_SHARED_WORKFLOWS_APP_ID`, remove it
once in Netlify's environment settings after confirming the client ID is set.
The deploy workflow does not remove unrelated or deprecated variables.
After deploying this owned-only bridge, remove the obsolete
`PR_DASHBOARD_QUEUE_MODE` Netlify variable from every context where it is set.
The bridge ignores it and the deploy workflow no longer installs it.

Deploy contexts:

- Production

## 5. Workflow dispatch contract

All webhook refreshes use queue-owned execution.
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
`dispatcher_generation`. Omitted lanes default to `live` for ordinary refresh
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

Manual targeted refreshes enqueue through `pull-request-dashboard.yml` with
these inputs:

```json
{
  "repository": "opentelemetry-java-instrumentation",
  "pr_number": "12345",
  "head_sha": "",
  "trigger_event": "pull_request_review_comment"
}
```

The bridge writes webhook PR or head-SHA work directly to the Blob queue.
Only a request that acquires the lane's dispatcher lease dispatches
`pull-request-dashboard-drain.yml`, with these inputs:

```json
{
  "dispatcher_generation": "12",
  "queue_lane": "live"
}
```

The generation identifies the dispatcher lease that the drain must activate.
The drain claims repository and PR or head-SHA work from Netlify, so those
values are not workflow inputs.

Target input rules:

- `repository` is the short repository name under `open-telemetry`, and must
  match a `repositories.json` entry exactly. An owner-prefixed name is rejected.
- Omit `pr_number` or set it to an empty string for a backfill.
- Send `head_sha` instead of `pr_number` when the event carries no pull request
  number. Check and status events for a pull request whose head branch lives in
  a fork report no pull request association.
- The central workflow validates repository and target inputs before use.
  Enqueue also validates the trigger event and rejects owner-prefixed
  repository names, conflicting PR/head inputs, and malformed targets.

## 6. Deployment and maintenance

These operations change production behavior and require operator approval.
Do not promote a release or cancel unrelated runs as part of execution retirement.
Execution retirement does not change the stable release pin.
It removes the direct-dispatch and return-to-legacy execution paths, not stored
data compatibility. Existing GitHub comments, nudge episodes, delivery receipts,
and accepted-state JSON remain intact. Schema 1 PR/head refresh records remain
readable and claimable by owned workers.

### Deploy the legacy execution cleanup

Production already uses owned execution, and legacy writers have retired.
This cleanup does not require another pause-and-retire window. Current owned
workers already send dispatcher generations and use the same ownership
contract. In-flight owned workers can finish during deployment.

The main-branch merge automatically deploys the bridge using the repository's
execution mode. After deployment, remove obsolete `PR_DASHBOARD_QUEUE_MODE`
Netlify values. Keep existing queued
items, request IDs, generations, control items, and completion receipts in their
shards. No migration, deletion, release, or promotion is needed.

### Pause for incompatible ownership changes or manual maintenance

Use this procedure before an incompatible change to execution lanes, delivery
ownership, or acknowledgment fencing, or before a standalone command writes
without queue ownership:

1. Pause the bridge first, independently of GitHub's current mode:

   ```sh
   gh workflow run pull-request-dashboard-deploy-webhook.yml \
     --repo open-telemetry/shared-workflows --ref main \
     -f execution_mode=paused
   ```

   Wait for that deployment to finish. Webhooks now persist refreshes without
   requesting a dispatcher.
2. Set GitHub's mode to paused:

   ```sh
   gh variable set PR_DASHBOARD_EXECUTION_MODE \
     --repo open-telemetry/shared-workflows --body paused
   ```

   Already claimed work can finish with its existing heartbeat and receipts;
   paused finish releases ownership without dispatching a successor. New
   scheduled/manual backfills and sweeps still enqueue.
3. Let every pre-upgrade drain in both lanes finish, including runs waiting for
   a runner and any old direct writers or reminder sweeps. Inspect all jobs,
   not only enqueue completion. Retiring a specific old run requires separate
   cancellation approval. Do not run workers with different ownership rules
   concurrently or rerun retired workflow attempts.
4. Deploy the incompatible change only after those writers have retired.
   A main-branch merge that changes bridge files automatically deploys the bridge
   using the paused repository setting. Confirm that deployment completed in
   paused mode; if a separate deployment is needed, use the `execution_mode=paused`
   override. For manual maintenance, keep execution paused until the command exits.
   Keep existing queued items, request IDs, generations, control items, and
   completion receipts in their shards. No migration or deletion is needed.
5. Restore GitHub's repository variable to `owned`, then deploy the bridge with
   `execution_mode=owned`. The paused bridge accepts work during that interval
   but grants no new claims. A new enqueue or scheduled recovery starts each
   lane independently.
6. Follow live and maintenance work in both release channels through completion.
   Confirm immutable code refs, delivery receipts, full-publication progress,
   and queue age. Do not release or promote dashboard code for this retirement.

There is no supported `legacy` mode after retirement. Recover execution problems
while paused or roll forward. Any separate processor-code rollback must respect
the accepted-state and delivery-version guards. Do not discard receipts or
restore old Git history to make older code publish. Data storage remains
permanently snapshot-only.

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

## 7. Data branch snapshots

The snapshot cutover is complete. Writers always replace
`otelbot/github-actions-queue-data`, `otelbot/pull-request-dashboard-state/*`,
and `otelbot/pull-request-dashboard-delivery/*` with parentless commits
containing their complete current trees. Historical CI records, collection
checkpoints, dashboard state, and delivery acknowledgements remain in the files.
The cutover preserved the current trees; it did not migrate stored JSON schemas
or remove payload compatibility.

Snapshot commits have a monotonically increasing `Data-snapshot-generation`
trailer. Dashboard fetches use it to reject stale snapshots, and every push
uses the exact SHA loaded before processing as its force-with-lease expectation.
A rejected dashboard update refetches and reapplies its changes. A rejected
collector push fails the job; the next run resumes from the accepted checkpoint.
Unchanged snapshots do not create commits. The first snapshot compacts a legacy
branch even when its files have not changed.

Readers compare snapshot generations, reject conflicting generations, and keep
a newer local ref when a fetch returns a stale head. They reject ordinary
history appended after a snapshot, including in a fresh checkout. The collector
fetches full history for heads without snapshot metadata so the writer can
detect appended history even when its initial fetch was shallow.

### Retired rollout flag

The unconditional snapshot helper is released and promoted, and older writers
have retired. Workflows and processors no longer require
`DATA_BRANCH_SNAPSHOTS`. After the workflow environment cleanup is merged,
the obsolete repository variable can be deleted without weakening snapshot
safety. Do not rerun older workflow attempts or roll back to processors that
require the flag.

This cleanup does not change stable/canary selection or release pins. It needs
no pause, release, promotion, compaction, or data migration.

### Recovery and clone size

Snapshots are permanent; changing an environment variable does not restore
the ancestry-based protocol. Restoring an old backup can discard CI checkpoints
or repeat notifications. Recover the latest accepted complete tree instead,
with a snapshot generation higher than the current head and an exact lease
against that head. Any recovery write must follow the pause-and-retire procedure
in section 6. Keep historical backups outside this repository rather than
advertising refs that retain old data history.

Compaction removes old history from the generated branch refs, not from
existing clones. A fresh clone avoids that history once no advertised branch
or tag retains it. Existing clones also retain local refs, reflogs, and packed
objects; a fresh clone is the safest way to reclaim that space. GitHub may
retain unreachable objects and pull request refs separately.
