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
  per-repository workflow for each target. The per-repository workflow separates
  read-only calculation from a serialized per-repository publisher, so one
  repository's update failure does not block delivery for repositories whose
  updates succeeded.
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
- Submitted reviews can coalesce with generic PR refreshes because the live PR
  status comment is rendered from current accepted dashboard state rather than
  a review-specific event. Manual runs remain separate because they can refresh
  large repositories that webhook-driven runs intentionally skip.
- Concurrency bounds pending jobs per target; it does not debounce webhook
  delivery or workflow dispatch. Different repositories and PRs can still run
  independently, and every accepted webhook still creates a workflow run.
- Publishers use one concurrency group per target repository. GitHub preserves
  the running publisher but may replace an older pending publisher with a newer
  one even when `cancel-in-progress` is false. Accepted work lives on the state
  branch: a targeted publisher limits status-comment and Slack delivery to its
  triggering PR. Webhook runs can arrive concurrently for many PRs, so allowing
  each publisher to fan out into repository-wide delivery would create long
  jobs and put pressure on the GitHub Actions job queue, especially when a new
  status-comment revision queues every open PR. The hourly untargeted publisher
  is the bounded repository-wide rollout and recovery path.
- The top-level hourly health check treats a replaced pending publisher as
  successful. Matrix failures take precedence over cancellation, so genuine
  update or delivery failures still open the failure issue.

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
- A missing repository state branch is bootstrapped by non-PR backfills. The
  dashboard state records when every open non-draft PR has been populated at
  least once. Targeted PR runs, dashboard publishing, status comments, and
  Slack notifications skip until that initial backfill is complete, so no
  partial dashboard is exposed.
- Targeted PR runs compute the triggered PR and merge that one PR slot with the
  latest accepted state on each state-branch compare-and-swap retry.

## Backfill

- Non-PR dashboard runs are backfills, not repository-wide refreshes. They are
  capped so one run cannot exhaust the dashboard GitHub App's hourly API quota.
- Each backfill lists open PRs, prunes cached PRs that are no longer open
  non-draft, then refreshes at most 50 open non-draft PRs.
- Status-comment rendering rollouts use separate versioned state and a durable
  queue. Incrementing the implementation revision snapshots all open PRs, then
  hourly runs update at most 50 queued comments until the rollout completes.
  Dashboard refreshes atomically queue comments only when their persisted result
  changes. A targeted publisher updates only its triggering PR when that PR is
  queued and cannot initialize or drain the repository-wide rollout. Untargeted
  publishers drain up to 50 queued comments. This confines rollout fan-out to
  the hourly path instead of multiplying it across concurrent webhook runs, and
  also delivers work left by a pending publisher that GitHub replaced.
- Selected PRs are processed one at a time through the same single-PR merge path
  as targeted refreshes. Each accepted PR update pushes structured state before
  the next selected PR is processed.
- The one-PR transaction size keeps state-branch compare-and-swap retries cheap:
  a rejected push retries one PR instead of refreshing a whole large repository
  and spending the same GitHub GraphQL rate-limit budget again. Backfill retries
  refetch the selected PR; targeted PR retries reuse the already computed PR
  result and only redo the latest-state merge and state save.
- Backfill progress is stored separately from dashboard state in
  `backfill-state.json`. The cursor is the last attempted PR number, and the
  next run continues after it in sorted PR-number order, wrapping when needed.
  Failed PR numbers are stored beside the cursor and are removed after a later
  successful refresh.
- Initial-backfill completion is stored in dashboard state and becomes true in
  the same accepted state commit that attempts the final missing open non-draft
  PR. Failed PR data is not accepted into dashboard state, but a recorded failed
  attempt cannot block initial publication. Once set, completion remains true.
  New PRs do not reset bootstrap; they appear after their first successful
  targeted refresh or backfill.
- A selected PR failure is recorded outside dashboard state, advances the
  cursor, and does not stop later selected PRs. The backfill still exits nonzero
  while any open PR is still recorded as having failed processing, keeping
  scheduled failure reporting active. The publisher consumes only accepted
  state, so untrusted PR content cannot deny service to the rest of the
  repository.
- The cursor deliberately does not rely on PR `updatedAt`; prior testing showed
  `updatedAt` is not a safe freshness key for every comment, review-comment, or
  thread event the dashboard needs.

## GraphQL Cost

- Review threads are still fetched from GraphQL because the dashboard needs
  thread-level fields such as `isResolved`, `isOutdated`, and canonical thread
  grouping.
- Top-level issue comments are fetched entirely through GraphQL because
  `lastEditedAt` isolates content edits while REST `updated_at` also changes for
  non-content activity. This avoids a second REST request and metadata join; the
  dashboard falls back to `createdAt` when a comment has never been edited.
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
- Cache snapshots are saved even when the update job fails, preserving valid
  classifications produced before or alongside an isolated failed item.
- Failed classifications are not cached or retried in the same run. A later run
  restores valid sibling classifications and sends only the still-uncached
  items to the model. The original run remains failed so the item is visible
  for operational triage.

## Required Status Checks

- Reported CI facts come from the PR's GraphQL status-check rollup, filtered by
  each context's `isRequired` result, so optional check failures do not make the
  dashboard report a failing PR or change its route. Paginated effective
  rulesets for the PR's base branch supply configured required contexts; a
  context that has not reported yet is shown as pending rather than passing.
- Classic branch-protection required status checks are not discovered when they
  have not reported. This is an accepted limitation because configured
  OpenTelemetry repositories use rulesets for required status checks.
- A failing required status check routes a human-authored PR to the author
  before discussion and approval routing. The live PR status comment names the
  CI failure, including when review feedback also needs author action.
  Repository-configured `non_blocking_check_patterns` identify failed optional
  checks in a note alongside this action, without changing required-check facts
  or routing.
- Maintenance-bot PRs retain maintainer-oriented routing because the bot cannot
  respond to a dashboard action. Pending required checks affect the CI column
  but do not change who owns the next action.

## Copilot Review Gate

- `require_clean_copilot_review_branches` is a final safety net applied only when a PR
  would otherwise route to reviewers or maintainers — that is, after the author
  has addressed the actionable discussions. It is not a routing input while the
  author still owns actions.
- The setting lists the base branches to gate rather than a single on/off
  switch, because automatic Copilot review is itself configured per branch
  (often only the default branch). Gating a branch with no automatic review
  would park every ready PR on the copilot route waiting for a review that never
  runs, so only branches with automatic review are listed and PRs targeting
  other branches route normally.
- Copilot findings normally return a PR to the author through ordinary
  discussion routing: an inline finding is an unresolved review thread, and an
  actionable one routes the PR to "waiting on author." In that common path the
  gate never fires and no re-review is requested.
- The gate's re-request path is deliberately narrow: it triggers when the
  current head has no Copilot review yet (a push made the prior review stale) or
  the author resolved Copilot's threads without a code change. Re-requesting the
  same head is intentional — it asks Copilot to re-review after the author
  responded, mirroring a human review cycle. Copilot's answer either clears the
  gate or produces fresh actionable threads that route the PR back to the
  author, so re-requesting an unchanged commit is self-correcting rather than a
  re-request loop.
- "Clean" means no inline comments on the current head, counted from the
  review, not from the classifier's actionability judgment. Accepted
  limitation: if Copilot leaves comments the classifier treats as
  non-actionable while they stay unresolved, routing sits at reviewers but the
  gate holds the PR on the copilot route and re-requests until Copilot returns a
  comment-free review or the author pushes. The strict count is intentional —
  the gate is a conservative "Copilot had nothing to say about this exact code"
  check, and folding in classifier judgment could let a real-but-non-actionable
  comment slip a PR to humans.

## Live PR Status Comments

- Feedback totals in the live comment count the canonical author-action links
  stored in dashboard state, not a separately persisted total of pending-action
  records. This keeps every counted item tied to an action the comment can
  present to the author.
- GitHub provides non-null ids for review threads and non-null canonical URLs
  for review comments and submitted reviews; issue comments likewise have
  id-specific URLs. Distinct author-action items should therefore produce
  distinct links. Missing or colliding URLs indicate malformed upstream data,
  not a supported state that needs a second count.
- Feedback links are deduplicated in dashboard state (when the canonical
  author-action links are collected) and capped at 20 in the comment. If the
  URL invariants need stronger enforcement, fail the affected PR refresh rather
  than advertise a larger item count with fewer actionable links.

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
  description edit. A later completed author reply addresses the item regardless
  of the predicted kinds because it can communicate pushback, clarification, or
  another valid outcome that automation cannot infer. An author's explicit
  commitment to future work in the current PR is a self-deferral, not a
  completed reply, so the item continues waiting on the author.
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
- Candidate author replies use a separate classifier with the same batch size,
  per-PR cap, and immutable cache behavior. Its result distinguishes completed
  replies, author self-deferrals, and external blockers independently for each
  earlier feedback item the comment addresses. Timestamp ordering determines
  which items are candidates, but never applies a comment to every earlier item
  by itself. Candidate sets are split and model-call batches are greedily packed
  against the fully serialized prompt, so every Copilot CLI argument remains
  within the configured character limit. Partial results are merged into one
  cache entry per author comment. Completed reply evidence retains the source
  comment id as well as its timestamp, so comments created in the same second
  cannot be confused. An external author reply moves only its associated
  feedback to external routing.
- Lifecycle transitions are deterministic after feedback and author-reply
  classification. An ordinary new item waits on the
  author with 📌 visible. Once all expected evidence is observed, or the author
  gives a completed reply, the item is addressed and the pin disappears. Normal
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
  receive initial notifications from a later publisher. Avoiding that bootstrap
  case would require storing separate seen-but-not-notified state.
- When a mapped assignee is added after a PR was already notified during the
  same waiting period, that assignee may wait until the next follow-up cadence
  instead of receiving an immediate initial notification.
- A targeted publisher evaluates only its triggering PR and preserves unrelated
  entries in the sent-notification ledger. An untargeted publisher evaluates all
  accepted repository state for eligible initial and follow-up notifications,
  providing the recovery path for Slack work whose pending publisher GitHub
  replaced. The ledger and weekday 24-hour follow-up cadence bound delivery.
- Slack notifications are sent only for dashboard state that has already been
  accepted on the state branch. A newer dashboard update can land after the
  publisher checks out state, so a notification can be slightly late
  relative to the newest state.
- The publisher preserves just-written notification state across normal
  state-branch CAS retries. If Slack delivery succeeds and every state-branch
  push attempt is rejected, a later run can send the same notification again.
  Recording state before sending Slack would avoid that duplicate window, but
  could instead record notifications that were never delivered.

## Publishing

- Dashboard publishing is serialized per target repository. The publisher owns
  target-repository writes for status comments, author reminders, Copilot
  re-review requests, Slack notifications, and the dashboard issue.
- Each publisher fetches accepted state while holding the publish slot. A
  targeted publisher limits status-comment and Slack delivery to its triggering
  PR; an untargeted publisher drains repository-wide work, with status comments
  bounded to 50 per run. Author reminders and Copilot requests use explicit
  durable ledgers; Slack eligibility is reconstructed from accepted dashboard
  and notification state.
- The dashboard issue is rendered from `dashboard-state.json` and the target
  repository's current open PR list after delivery. If another update advances
  the state branch while a publisher is already working, external views can
  briefly lag until the next publisher.
