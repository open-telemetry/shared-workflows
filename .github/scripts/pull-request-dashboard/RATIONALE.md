# Pull Request Dashboard Rationale

This dashboard is a maintainer aid, not a transactional notification system.
Some rare timing and notification edge cases are intentionally accepted to keep
the implementation understandable and operationally cheap.

## Central Workflow

- Scheduled backfills run from `open-telemetry/shared-workflows` instead of
  each target repository hosting its own workflow.
- Target repositories only need GitHub App access and an entry in
  `repositories.json`.
- The top-level workflow resolves target repositories, then calls a reusable
  per-repository workflow for each target. The per-repository workflow runs the
  update, notification, and publishing jobs top to bottom for one repository, so
  one repository's update failure does not block publishing or notifications for
  repositories whose updates succeeded.
- The top-level repository matrix runs one repository at a time. Backfills do
  not benefit enough from cross-repository parallelism to justify extra
  aggregate API and LLM demand.
- State for each target repository lives on its own state branch under
  `otelbot/pull-request-dashboard-state/<repository>`, with files still
  namespaced by repository name inside the branch.
- The state branch stores structured dashboard and notification state. The
  publishing job renders markdown from accepted dashboard state and the target
  repository's current open PR list.
- The dashboard issue is discovered dynamically by title and label, so target
  repositories do not need to store issue numbers in config.

## Workflow Concurrency

- Webhook refreshes are grouped by target repository and PR before the first
  job starts. GitHub Actions keeps at most one running and one pending run in
  each group; a newer pending run replaces the older pending run without
  canceling the run already in progress.
- Coalescing is safe because each refresh loads current PR state from GitHub.
  Intermediate states can go unobserved, but the surviving run reflects the
  state that exists when it executes.
- Submitted reviews use the review id as a distinct concurrency identity so a
  pending review-guidance event cannot be replaced by a generic PR refresh.
  Manual runs are also separate because they can refresh large repositories
  that webhook-driven runs intentionally skip.
- Concurrency bounds pending jobs per target; it does not debounce webhook
  delivery or workflow dispatch. Different repositories, PRs, and submitted
  review ids can still run independently, and every accepted webhook still
  creates a workflow run.

## GitHub Actions Instead Of Netlify For Scheduled Backfills

- Scheduled backfills are batch jobs: they read repository PR lists, call REST
  and GraphQL, run Copilot classification, and update git-backed state. A
  follow-up publishing job renders and publishes the issue body from accepted
  state.
- GitHub Actions provides clearer logs, concurrency controls, artifacts, and
  normal retry/cancel behavior for that workload.
- Netlify remains appropriate for small webhook-sized work, but it was a poor
  fit for long backfill workers.

## State Branch

- Dashboard and notification state are stored on a git branch rather than in the
  live dashboard issue body.
- Dashboard and notification state files are namespaced by target repository.
- Each target repository uses a separate state branch so unrelated repositories
  do not contend on the same git ref during scheduled and webhook-driven runs.
- Updates use `git push --force-with-lease`, so git refs provide the durable
  compare-and-swap boundary for concurrent same-repository runs.
- A missing repository state branch is bootstrapped by the next non-PR backfill;
  targeted PR runs skip until initial dashboard state exists.
- Targeted PR runs compute the triggered PR and merge that one PR slot with the
  latest accepted state on each state-branch compare-and-swap retry.

## Backfill

- Non-PR dashboard runs are backfills, not repository-wide refreshes. They are
  capped so one run cannot exhaust the dashboard GitHub App's hourly API quota.
- Each backfill lists open PRs, prunes cached PRs that are no longer open
  non-draft, then refreshes at most 50 open non-draft PRs.
- Selected PRs are processed one at a time through the same single-PR merge path
  as targeted refreshes. Each accepted PR update pushes structured state before
  the next selected PR is processed.
- The one-PR transaction size keeps state-branch compare-and-swap retries cheap:
  a rejected push retries one PR instead of refreshing a whole large repository
  and spending the same GitHub GraphQL rate-limit budget again. Backfill retries
  refetch the selected PR; targeted PR retries reuse the already computed PR
  result and only redo the latest-state merge and state save.
- Backfill progress is stored separately from dashboard state in
  `backfill-state.json`. The cursor is the last successfully refreshed PR
  number, and the next run continues after it in sorted PR-number order,
  wrapping when needed.
- A selected PR failure stops the run without advancing the cursor. This can
  temporarily block later PRs, but it keeps the scheduled workflow failure issue
  open and pointing at the blocked backfill until the failure is fixed.
- The cursor deliberately does not rely on PR `updatedAt`; prior testing showed
  `updatedAt` is not a safe freshness key for every comment, review-comment, or
  thread event the dashboard needs.

## GraphQL Cost

- Review threads are still fetched from GraphQL because the dashboard needs
  thread-level fields such as `isResolved`, `isOutdated`, and canonical thread
  grouping.
- `reviewThreads(first: 10)` is intentionally small. The nested
  `comments(first: 100)` connection makes GitHub GraphQL rate-limit cost scale
  with the review-thread page size.
- Pagination still fetches every review thread; the smaller page size reduces
  rate-limit spikes without dropping data.

## Classification Cache

- LLM classification cache is stored with `actions/cache`.
- Unchanged review threads reuse cached classifications and avoid new Copilot
  calls.
- Cache keys are scoped by target repository and by either PR number or
  backfill.
- Targeted PR runs restore their PR-specific cache first, then fall back to the
  latest backfill cache for that repository. They still save under a PR-specific
  key, so targeted runs do not overwrite the backfill cache namespace.
- Cache entries are immutable, so rolling keys plus restore prefixes pick up the
  latest usable snapshot without concurrent writers overwriting each other.

## Slack Notifications

- Slack notification state is PR-granular. It does not track notification
  history separately for each assignee.
- When notification state is first created, existing approver-routed PRs may
  receive initial notifications on a later targeted refresh. Avoiding that
  bootstrap case would require storing separate seen-but-not-notified state.
- When a mapped assignee is added after a PR was already notified during the
  same waiting period, that assignee may wait until the next follow-up cadence
  instead of receiving an immediate initial notification.
- Scheduled runs send only due follow-up reminders. Targeted PR refreshes send
  only the triggering PR's initial notification. This keeps webhook-driven
  refreshes from sweeping unrelated PR reminders while preserving the hourly
  reminder pass.
- Slack notifications are sent only for dashboard state that has already been
  accepted on the state branch. A newer dashboard update can land after the
  notification job checks out state, so a notification can be slightly late
  relative to the newest state.
- The notification job preserves just-written notification state across normal
  state-branch CAS retries. If Slack delivery succeeds and every state-branch
  push attempt is rejected, a later run can send the same notification again.
  Recording state before sending Slack would avoid that duplicate window, but
  could instead record notifications that were never delivered.

## Publishing

- Dashboard publishing is serialized per target repository.
- Each publish job fetches the accepted state branch while holding the publish
  slot, lists the target repository's current open PRs, renders markdown from
  `dashboard-state.json`, and publishes the issue body.
- Publish jobs are superseded by newer publish jobs for the same repository;
  only the newest queued publish needs to render the latest accepted state.
- If another update advances the state branch while a publish job is already
  editing the issue, the live issue can briefly lag until the next publish job.
