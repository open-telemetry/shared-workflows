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
- The state branch stores structured dashboard, notification, and author
  follow-up state. The publishing job renders markdown from accepted dashboard
  state and the target repository's current open PR list.
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
- Submitted reviews can coalesce with generic PR refreshes because the live PR
  status comment is rendered from current accepted dashboard state rather than
  a review-specific event. Manual runs remain separate because they can refresh
  large repositories that webhook-driven runs intentionally skip.
- Concurrency bounds pending jobs per target; it does not debounce webhook
  delivery or workflow dispatch. Different repositories and PRs can still run
  independently, and every accepted webhook still creates a workflow run.

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

- Dashboard, notification, and author follow-up state are stored on a git branch
  rather than in the live dashboard issue body.
- State files are namespaced by target repository.
- Each target repository uses a separate state branch so unrelated repositories
  do not contend on the same git ref during scheduled and webhook-driven runs.
- Updates use `git push --force-with-lease`, so git refs provide the durable
  compare-and-swap boundary for concurrent same-repository runs.
- A missing repository state branch is bootstrapped by non-PR backfills. The
  dashboard state records when every open non-draft PR has been populated at
  least once. Targeted PR runs, dashboard publishing, status comments, and
  Slack notifications skip until that initial backfill is complete, so no
  partial dashboard is exposed.
- Targeted PR runs compute the triggered PR and merge that one PR slot with the
  latest accepted state on each state-branch compare-and-swap retry.

## Author Follow-Up

- The handoff nudge is due one day after the first substantive author action
  without a later human response during the current author-routing period.
  Later author activity without a successful transition out of the author route
  does not postpone it. This prevents repeated activity from indefinitely
  delaying a nudge when the dashboard still expects the author to act. When a
  handoff and general nudge would otherwise both be due, the handoff is
  delivered first.
- The general nudge is due one week after a backfill follow-up job first
  observes the PR in the author route, or one week after a delivered handoff
  nudge when that occurs. This guarantees a full week between the two comments.
- Both nudges apply to every configured repository. Stale labeling and closure
  require the repository-level `stale_waiting_on_author` option.
- The general nudge clock is unaffected by activity while the PR remains routed
  to the author. After that nudge, author pushes and human comments and reviews
  restart the current one-week quiet stage. Activity after stale labeling
  removes a dashboard-owned `Stale` label before restarting the stale wait.
  Leaving the author route clears the lifecycle.
- Substantive human activity consists of author commits, human issue or review
  comments, and submitted human reviews. Bot and dashboard activity, reactions,
  label or assignment changes, checks, and edits to existing comments do not
  reset a quiet stage.
- A non-PR run evaluates lifecycle actions only for PR results refreshed and
  accepted during that run. Older cached results remain available for dashboard
  rendering but cannot trigger nudges, stale labeling, or closure.
- Escalation state is stored separately from dashboard routing and Slack
  notification state. Hidden per-cycle comment markers make retries idempotent
  if a GitHub mutation succeeds before a state-branch push is rejected. For
  stale labels, the latest matching GitHub label event is the durable ownership
  receipt, allowing a retry to recover ownership when the label mutation
  succeeded before lifecycle state was saved.
- Stage clocks begin only after the preceding GitHub mutation succeeds. Closure
  requires a current open PR with at least one still-unresolved author-action
  thread, the dashboard-owned `Stale` label, and no subsequent substantive human
  activity. This live recheck protects against a thread being resolved after
  the dashboard refresh without producing another activity event.
- The workflow records whether it added `Stale` and removes only labels it owns
  when activity, manual label removal, or a route change resets escalation. It
  also removes an owned label after closure; if that cleanup fails, retained
  lifecycle state lets a later run remove it from the closed PR.

## Backfill

- Non-PR dashboard runs are backfills, not repository-wide refreshes. They are
  capped so one run cannot exhaust the dashboard GitHub App's hourly API quota.
- Each backfill lists open PRs, prunes cached PRs that are no longer open
  non-draft, then refreshes at most 50 open non-draft PRs.
- Selected PRs are processed one at a time through the same single-PR merge path
  as targeted refreshes. Each accepted PR update pushes structured state before
  the next selected PR is processed.
- The update job exposes the exact selected PR numbers to downstream jobs.
  Scheduled Slack follow-ups and author lifecycle mutations are restricted to
  that fresh set; unselected cached rows can still render but cannot cause an
  external side effect.
- The one-PR transaction size keeps state-branch compare-and-swap retries cheap:
  a rejected push retries one PR instead of refreshing a whole large repository
  and spending the same GitHub GraphQL rate-limit budget again. Backfill retries
  refetch the selected PR; targeted PR retries reuse the already computed PR
  result and only redo the latest-state merge and state save.
- Backfill progress is stored separately from dashboard state in
  `backfill-state.json`. The cursor is the last successfully refreshed PR
  number, and the next run continues after it in sorted PR-number order,
  wrapping when needed.
- Initial-backfill completion is stored in dashboard state and becomes true in
  the same accepted state commit that populates the final missing open
  non-draft PR. Once set, it remains true. New PRs do not reset bootstrap; they
  appear after their first targeted refresh or backfill. Existing cached rows
  remain visible, while reminders resume when each PR is next selected for a
  fresh backfill result.
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
- Unchanged review threads and top-level feedback items reuse cached
  classifications and avoid new Copilot calls.
- Cache keys are scoped by target repository and by either PR number or
  backfill.
- Targeted PR runs restore their PR-specific cache first, then fall back to the
  latest backfill cache for that repository. They still save under a PR-specific
  key, so targeted runs do not overwrite the backfill cache namespace.
- Cache entries are immutable, so rolling keys plus restore prefixes pick up the
  latest usable snapshot without concurrent writers overwriting each other.

## Top-Level Feedback

- GitHub gives inline review threads explicit replies and a resolved state, but
  top-level feedback has no equivalent completion signal. Top-level feedback
  means a standalone PR comment or submitted review summary that is not
  attached to an inline review thread.
- Each top-level feedback item is therefore classified independently with a
  stable GitHub-derived id. The LLM decides only whether the source is
  actionable and which observable evidence kinds could address it: a commit,
  PR title edit, PR description edit, explicit reply, or a combination of those
  signals.
- Each refresh reconstructs these independent items with a linear scan of the
  comments and reviews already fetched from GitHub. They are not threaded, and
  the reconstructed list is not stored as a second ledger. This keeps edited or
  deleted source comments authoritative without additional reconciliation.
  Cached classifications avoid repeated LLM calls, while dashboard state
  retains the evidence already observed for each item.
- Evidence requirements are an all-of list for compound feedback. For example,
  a request to update code and the PR description waits for both a commit and a
  description edit. A later explicit author reply always addresses the item,
  regardless of the predicted kinds, because it can communicate pushback,
  clarification, or another valid outcome that automation cannot infer.
- Title edits use GitHub's `RenamedTitleEvent` pull request timeline items,
  including the event actor and creation time. They remain separate from
  description edits so compound requests can require either or both. The
  timeline is queried only when a classified title requirement has no
  previously recorded qualifying title edit.
- Each model call classifies up to ten uncached top-level feedback items
  independently, while retaining a separate cache entry for every item. A
  refresh processes at most 200 such items per PR; excess items remain visible
  as non-failing unclear actions and are classified by later refreshes. This
  bounds both call count and prompt size without allowing one long-lived PR to
  monopolize the workflow or model quota.
- Lifecycle transitions are deterministic. An ordinary new item waits on the
  author with 📌 visible. Once all expected evidence is observed, or the author
  explicitly replies, the item is addressed and the pin disappears. Normal
  approval-based routing then decides whether the PR waits on reviewers or
  maintainers; ordinary items do not have a separate requester-confirmation
  phase.
- Review summaries are classified like other top-level feedback, independently
  of review state. A `CHANGES_REQUESTED` state affects only the reviewer's
  badge; it does not affect dashboard actions or routing. Empty review summaries
  are ignored; their inline comments, if any, define independent actions.
- Description edits use the pull request's GraphQL `lastEditedAt` and `editor`
  fields instead of the general `updatedAt`, which also changes for unrelated
  PR activity.
- The earliest timestamp for every observed evidence kind is retained in the
  cached PR result. Evidence is reused only when it remains newer than the
  item's root, so later metadata edits cannot regress the handoff and an edited
  request cannot inherit evidence that predates its new text. Ordinary
  requester-confirmation timestamps are not persisted.
- Matching evidence can address an item before the request is fully satisfied.
  That recoverable early handoff is preferable to leaving a PR indefinitely
  assigned to an author who already pushed, edited the description, or replied.
- Reviewers should prefer inline comments when feedback needs explicit closure.
  Blocking PR-wide feedback should use GitHub's **Request changes** review state;
  ordinary top-level feedback remains a softer coordination mechanism.

## Slack Notifications

- Slack notification state is PR-granular. It does not track notification
  history separately for each assignee.
- When notification state is first created, existing approver-routed PRs may
  receive initial notifications on a later targeted refresh. Avoiding that
  bootstrap case would require storing separate seen-but-not-notified state.
- When a mapped assignee is added after a PR was already notified during the
  same waiting period, that assignee may wait until the next follow-up cadence
  instead of receiving an immediate initial notification.
- Scheduled runs send due follow-up reminders only for PRs refreshed during the
  current backfill. Targeted PR refreshes send only the triggering PR's initial
  notification. This prevents stale cached routing from producing a reminder
  while keeping webhook-driven refreshes from sweeping unrelated PRs.
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
