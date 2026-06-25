# Pull Request Dashboard Rationale

This dashboard is a maintainer aid, not a transactional notification system.
Some rare timing and notification edge cases are intentionally accepted to keep
the implementation understandable and operationally cheap.

## Central Workflow

- Full rebuilds run from `open-telemetry/shared-workflows` instead of each
  target repository hosting its own workflow.
- Target repositories only need GitHub App access and an entry in
  `repositories.json`.
- The top-level workflow resolves target repositories, then calls a reusable
  per-repository workflow for each target. The per-repository workflow runs the
  update, notification, and publishing jobs top to bottom for one repository, so
  one repository's update failure does not block publishing or notifications for
  repositories whose updates succeeded.
- The top-level repository matrix uses limited parallelism to reduce contention
  on the shared state branch while still allowing more than one repository to
  run at a time.
- State for all target repositories lives on one shared state branch, namespaced
  by repository name.
- The dashboard issue is discovered dynamically by title and label, so target
  repositories do not need to store issue numbers in config.

## GitHub Actions Instead Of Netlify For Full Rebuilds

- Full rebuilds are batch jobs: they read many PRs, call REST and GraphQL many
  times, run Copilot classification, update git-backed state, and publish an
  issue body.
- GitHub Actions provides clearer logs, concurrency controls, artifacts, and
  normal retry/cancel behavior for that workload.
- Netlify remains appropriate for small webhook-sized work, but it was a poor
  fit for long full-rebuild workers.

## State Branch

- Dashboard and notification state are stored on a git branch rather than in the
  live dashboard issue body.
- Updates use `git push --force-with-lease`, so git refs provide the durable
  compare-and-swap boundary for concurrent runs.
- Full rebuilds write the complete current state; targeted PR runs merge one PR
  slot with the latest accepted state.

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
- Cache keys are scoped by target repository and by either PR number or full
  rebuild.
- Cache entries are immutable, so rolling keys plus restore prefixes pick up the
  latest usable snapshot without concurrent writers overwriting each other.

## Slack Notifications

- Slack notification state is PR-granular. It does not track notification
  history separately for each assignee.
- When notification state is first created, existing approver-routed PRs may
  receive initial notifications on the next run. Avoiding that bootstrap case
  would require storing separate seen-but-not-notified state.
- When a mapped assignee is added after a PR was already notified during the
  same waiting period, that assignee may wait until the next follow-up cadence
  instead of receiving an immediate initial notification.
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
  slot, so older jobs do not intentionally publish stale markdown over newer
  accepted state.
- If another update advances the state branch while a publish job is already
  editing the issue, the live issue can briefly lag until the next publish job.
