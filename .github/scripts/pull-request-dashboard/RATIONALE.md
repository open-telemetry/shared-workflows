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
- Refresh events that carry no pull request number report the head commit
  instead, and the workflow resolves it to an open pull request. GitHub omits
  the pull request association from check and status events whose head branch
  lives in a fork, which is where nearly every contribution comes from, so
  without this the CI columns of those PRs would only refresh on the hourly
  backfill. The webhook bridge cannot resolve the commit itself, because its
  GitHub App is installed only on `shared-workflows`.

## Staged Rollout

- Dashboard changes reach repositories in two stages. Canary repositories run
  the workflow and its scripts from the commit that triggered the run; every
  other repository runs them from a promoted release commit, hash pinned with
  the release tag as a comment. A regression therefore shows up on a small set
  of repositories before it reaches the whole fleet.
- Staging the code rather than gating individual changes behind feature flags
  also covers the changes nobody thought were risky, which is where regressions
  actually come from. Feature flags remain available for a single genuinely
  risky behavior.
- `jobs.<id>.uses` cannot take an expression, so the channel cannot come from a
  matrix value. Each entry path instead has one job per channel. A job-level
  `if` cannot read `env` either, so the targeted canary job repeats the canary
  list inline and the targeted stable job reads that job's skip rather than
  repeating it again; `test_rollout.py` keeps the copy in sync.
- The reusable workflow loads the dashboard scripts through GitHub's
  self-repository action syntax. The action resolves from the same commit as
  the reusable workflow, so pinned stable jobs cannot mix released workflow
  YAML with scripts from the commit that triggered the run.
- Stable callers temporarily keep passing `code_ref` because the currently
  promoted workflow still checks it out. The first release containing the
  self-repository action accepts but ignores that compatibility input; it can
  be removed from callers after that release is promoted.
- Repository configuration is deliberately not staged. `repositories.json` is
  always read from the commit that triggered the run, so opting a repository in
  or changing its settings takes effect immediately in both channels. The cost
  is that configuration changes must be additive for one promotion cycle,
  because pinned code reads live configuration.
- Promotion is a pull request that pins the ref, rather than a moving tag or
  branch. A manually dispatched workflow prepares that pull request only after
  an operator names the release that has soaked on canaries. The version that
  each repository runs is then visible in the workflow file, a rollback is a
  revert, and the reference stays hash pinned the way every other action
  reference in this repository is.
- A rollback stops a bad change rather than restoring delivery. Downgraded code
  reads newer delivery state as empty, so delivering from it would repeat
  reminders and re-review requests already sent; the delivery version check
  makes it skip delivery instead. Rolling forward is the way out, and a paused
  dashboard is the cheaper failure.

## Queue and Workflow Concurrency

- In `canary` and `all` queue modes, the Netlify bridge coalesces webhook
  refreshes by target repository and PR before it starts GitHub Actions. One
  singleton drain claims a bounded batch. An event that arrives during
  processing marks the item dirty and schedules one follow-up pass.
- Direct refreshes in `off` mode, and non-canary refreshes in `canary` mode, use
  GitHub Actions concurrency groups by target repository and PR. GitHub Actions
  keeps at most one running and one pending run in each group; a newer pending
  run replaces the older pending run without canceling the run already in
  progress.
- Coalescing is safe because each refresh loads current PR state from GitHub.
  Intermediate states can go unobserved, but the surviving run reflects the
  state that exists when it executes.
- Submitted reviews can coalesce with generic PR refreshes because the live PR
  status comment is rendered from current accepted dashboard state rather than
  a review-specific event. Manual runs remain separate because they can refresh
  large repositories that webhook-driven runs intentionally skip.
- Actions concurrency bounds pending direct jobs per target; it does not
  debounce direct workflow dispatch. Every accepted direct webhook still
  creates a workflow run. Queue modes avoid that dispatch pressure for queued
  repositories by coalescing events in Netlify first.
- Publishers use one concurrency group per target repository. GitHub preserves
  the running publisher but may replace an older pending publisher with a newer
  one even when `cancel-in-progress` is false. Direct and queued publishers also
  acquire a lease stored on the repository's state branch. This shared lock
  prevents the drain from overlapping the direct concurrency group while either
  path performs external delivery or publishes the issue. Accepted work lives
  on the state branch: a targeted publisher limits status-comment and Slack
  delivery to its triggering PR. Webhook runs can arrive concurrently for many
  PRs, so allowing each publisher to fan out into repository-wide delivery
  would create long jobs and put pressure on the GitHub Actions job queue,
  especially when a new status-comment revision queues every open PR. The
  hourly untargeted publisher is the bounded repository-wide rollout and
  recovery path.
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
- The rotation is not reordered to favor PRs that are waiting on something. A
  wait ends with nothing changing on the PR — a check completes, a review is
  filed — so a missed event is only noticed when the rotation comes round again.
  But a full sweep takes two passes on most repositories and four on the largest,
  which is no longer than the gate hold that bounds the wait anyway, so
  refreshing waiting PRs first would buy about an hour on one repository and
  nothing on the rest.
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
- The rollup is read from the PR's last commit, which can still be the previous
  head just after a push. That commit's checks are already complete, so they
  would read as a settled result for code that is no longer proposed. The
  rollup therefore carries its commit oid and is discarded when it does not
  match the PR head, leaving check facts unavailable until the rollup catches
  up.
- A required context is only pending while the app that owns it may still
  report. Check suites for the head commit are consulted, and a context is
  dropped from the pending set once every suite its app created has completed,
  because the app has then reported everything it is going to. Without this a
  ruleset context that no workflow produces — an obsolete or conditionally
  skipped job — would be reported as permanently pending. Such a PR cannot
  merge either way; the difference is that the dashboard stops treating it as
  "still running." The suites are read only when an app-owned required context
  has not reported, so the refreshes that cannot use the answer do not pay for
  it.
- A `code_scanning` ruleset rule holds the merge on a check that the code
  scanning app publishes per configured tool, named after that tool, which
  GitHub never marks as required. Those checks are matched by app and by the
  tool names in the rule, then treated as required. Their `NEUTRAL` conclusion
  means the alerts introduced by the PR could not be determined, which also
  holds the merge, so it is reported as failing rather than skipped. Tools with
  no such check are not reported, because GitHub expects results only from the
  tool configurations that actually ran.
- That `NEUTRAL` is only reported as failing once every check at the head has
  finished, including optional ones. The code scanning app publishes the tool
  check as `NEUTRAL` before the analysis is uploaded and then replaces it in
  place, so an analysis still running is reported as pending instead of pinning
  the PR to its author. The replacement leaves the enclosing check suite
  untouched, and the dashboard does not subscribe to check runs, so no webhook
  reports it. That only delays the dashboard when the code scanning analysis is
  the last thing to finish at the head, because any check suite completing after
  it triggers the refresh that observes the final result; when it does, the
  transition waits for the hourly refresh. Subscribing to check runs would close
  that window at roughly ten times the webhook volume of every other event
  combined, because GitHub emits one check run per job.
- Required checks reported as commit statuses, such as EasyCLA, never belong to
  a check suite, so commit status events are subscribed as well.
- A failing required status check routes a human-authored PR to the author
  before discussion and approval routing. The live PR status comment names the
  CI failure, including when review feedback also needs author action.
  Repository-configured `non_blocking_check_patterns` identify failed optional
  checks in a note alongside this action, without changing required-check facts
  or routing.
- A merge conflict does not decide who should act. Discussion, CI, and approval
  routing still identify the owner, while the conflict remains visible as a
  separate merge blocker. This lets maintainers handle routine conflicts, such
  as a changelog conflict, without first sending the PR back to its author.
- Required-check and Copilot review gates still hold a handoff while a conflict
  exists, but their missing-result clock is paused. GitHub may not start either
  automation on an unmergeable head, so a missing result is expected rather than
  a stalled handoff. Entering the conflicted state clears any
  same-head hold clock. A fresh four-hour clock starts if a gate is still missing
  after the conflict is resolved.
- A PR does not advance toward merge while the required checks are unsettled:
  an author waiting on CI keeps the PR, and a PR already with approvers is not
  handed to maintainers to merge. Clearing the checks is the author's job, so
  an outstanding one is not yet a reason to spend anyone else's attention, and a
  push clears the failing count before the replacement checks produce a result,
  so the PR would otherwise move forward on evidence that does not exist yet and
  move back minutes later when the same check fails. Moving back toward the
  author is never held, because a failing check or new author-owned discussion
  is evidence the gates cannot undo. Unavailable check results hold the handoff
  for the same reason a pending one does, and resolve on a later run.
- A held PR is presented as waiting on its author rather than on the robot it
  is waiting for, so a separate route would add a section that nobody is
  expected to act on. What it waits for is named in the columns instead: the CI
  column already shows running checks, and a Copilot review that is actually in
  flight is listed in the reviewers column with the pending icon. Copilot
  otherwise joins that column only once it has reviewed, so without this the
  Copilot gate would hold a PR with nothing on the row to explain why. The live
  status comment tells the author the handoff happens once both are clean.
- A held route also holds its wait age. Recomputing it would read the push as
  the end of the CI failure and fall back to the last approver activity, which
  is usually far older, so a PR the author had just pushed to would sort to the
  top of the waiting-on-authors section as the stalest item on the board.
- A held PR remains in the author reminder flow. Most holds clear quickly, but a
  hold can also mean that automation never started or that the author is stuck.
  After the standard seven-day wait, the reminder links to the live status and
  offers the reviewer-routing command as a break-glass handoff. Reviewer Slack
  notification still waits until the route actually reaches reviewers.
- While a PR stays on a route where someone other than the author owes it a
  response, its wait age only moves back, never forward. The fallback for those
  routes is the last author activity, so a push would otherwise restart the
  clock and present a review nobody has done in a week as brand new. A handoff
  from the author route does start a fresh wait, because that push is what put
  the PR in front of reviewers.
- A handoff the gates held starts its wait when the gates release it, not at the
  push. The push and the handoff were the same moment before the gates existed,
  which is why the fallback dates a reviewer's wait from the last author
  activity. Now the gates sit between the two, so a PR whose checks took an hour
  would reach reviewers already an hour old, and one released after a stalled
  gate would arrive older still. Either way the age blames reviewers for a wait
  they could not have answered, and sorts the PR above ones they really have
  been sitting on.
- Only the handoff from the author restarts the wait. A held PR that was already
  with reviewers and is released to maintainers keeps its wait, because it never
  left the people who owe it a response, and restarting there would present an
  approval a week old as a merge request that just arrived.
- Maintenance-bot PRs retain maintainer-oriented routing because the bot cannot
  respond to a dashboard action. Pending required checks affect the CI column
  but never route one of these PRs to its author: a bot PR whose handoff is
  held waits on reviewers instead. Merge conflicts remain visible without
  overriding that routing.
- A hold has a time limit, and past it the PR routes anyway. Every gate waits on
  something outside the dashboard, and each one has been seen never to arrive: a
  required check with no check run on the head, a Copilot review GitHub never
  started, a review request GitHub accepted and dropped. The dashboard does not
  decide whether a PR may merge, branch protection does, so a hold that never
  ends protects nobody. It only keeps the PR away from the people who could look
  at the missing gate, and it hides the failure, because a held PR looks exactly
  like a PR that is waiting normally.
- The limit is four hours: longer than a slow check suite or a queued Copilot
  review, short enough that a gate which is never going to report costs the PR
  part of a day rather than the rest of its life.
- The clock starts when a gate first holds the PR back, and then runs on its own
  for as long as the same head still has an outstanding gate. Carrying it that
  way is what lets the hold give up without the stall looking resolved a moment
  later, and it keeps the status comment able to say which gate the dashboard
  stopped waiting for. Starting it only on a real hold is what keeps a slow
  check suite on a PR that was already with its reviewers from looking like a
  stalled handoff. A push clears the clock, because new code means new checks
  and a review that has to run again.
- Only a gate that has reported nothing on the current head runs the clock. A
  Copilot review that covers the head but left open findings holds the PR, yet
  it is not missing: it answered, and the threads it left are the author's to
  clear. Because the clock is carried across a trip back to the author, counting
  those findings would make every author who takes more than four hours over
  review comments look like a gate GitHub lost, and the report would say a
  review never arrived when it is sitting on the PR. A review that is missing or
  that only covers older code still counts, because Copilot has said nothing
  about the code being reviewed.
- An expired hold routes the pull request and says so in its status comment,
  which is the whole of what the dashboard can do about it. The gates raise no
  alarm of their own. Each way a gate can go missing has its own cause and none
  of them can be told apart from the dashboard's side, and the pull request is
  already in front of the people who can look at the missing gate. Failing the
  repository's run instead would repeat every hour until the head moved, long
  after the handoff it was reporting had happened, and it would keep firing once
  a trip back to the author had stopped the dashboard re-requesting the review it
  was naming. A conflicted PR cannot expire its hold because its missing
  automation is expected.

## Copilot Review Gate

- `require_clean_copilot_review_branches` is a final safety net applied only when a PR
  would otherwise route to reviewers or maintainers — that is, after the author
  has addressed the actionable discussions. It is not a routing input while the
  author still owns actions.
- The setting lists the base branches to gate rather than a single on/off
  switch, because automatic Copilot review is itself configured per branch
  (often only the default branch). Gating a branch with no automatic review
  would hold every ready PR with its author waiting for a review that never
  runs, so only branches with automatic review are listed and PRs targeting
  other branches route normally.
- Copilot findings normally return a PR to the author through ordinary
  discussion routing: an inline finding is an unresolved review thread, and an
  actionable one routes the PR to "waiting on author." In that common path the
  gate never fires and no re-review is requested.
- Findings are counted from unresolved, non-outdated review threads Copilot
  started, not from the comment count on its review. A review's comment count
  never shrinks, so it keeps counting feedback the author has since addressed
  and holds the PR on work that is already done.
- The gate's re-request path covers two states. A stale review means the author
  pushed, which is the one change a re-review can respond to. Findings on the
  current head sit on unchanged code, so asking Copilot to look at it again
  would reach the same verdict and be requested again on the next pass; those
  threads clear when the author resolves them or pushes a fix, which is a
  re-request in its own right.
- The other state is a first review that never arrived. The gate otherwise
  relies entirely on automatic Copilot code review to produce it, so when GitHub
  silently never starts one, the pull request waits on its author forever for a
  review nobody has asked for, and only manual intervention recovers it.
- That wait is timed from `copilot_first_review_missing_since`, set when the
  gate first observes a non-draft pull request with no Copilot review and
  carried forward across passes. Becoming a draft resets it, because GitHub
  starts the automatic review when a pull request becomes ready rather than when
  it is opened. A push deliberately does not reset it: GitHub does not
  automatically review a pull request it has never reviewed, so restarting the
  wait on every push would leave an actively developed pull request waiting
  forever — exactly the case the recovery exists for.
- One hour is the grace period. Observed first reviews normally land within
  twenty minutes and have been seen as late as forty, so an hour clears the
  normal spread without waiting through another full review cycle. Nothing
  signals the expiry itself: a pull request stalled on a missing review produces
  no activity, so no webhook fires and the hourly backfill is what notices.
  Recording and delivery share a run, so recovery lands within roughly one to
  two hours of the pull request becoming ready, against a failure that is
  otherwise unbounded.
- A first-review request is reachable only where a re-request already is — the
  pull request would otherwise route to reviewers or maintainers, and Copilot is
  not already a pending requested reviewer.
- Running checks do not hold either request back, so the review and the checks
  run at once rather than in sequence. Checks that fail still block it, because
  they route the pull request to its author and only a reviewer route reaches
  here, so a review is never spent on code CI is about to reject. Waiting for
  pending checks only delayed the request by however long the suite took, on top
  of the hour the missing review had already cost.
- For the same reason the recorded request is fingerprinted without the checks.
  The fingerprint exists to discard a request whose routing has since changed,
  and a request made while CI runs would otherwise be discarded by the next
  check to finish, one pass at a time, for as long as the suite lasted.
- Delivery re-validates the review state against live data and discards the
  request only when a review already covers the current head, which is what
  happens when one lands between the observation and the delivery. A review that
  is still missing is a reason to request, not to discard.
- A request counts as delivered only once GitHub shows Copilot as a pending
  reviewer. The mutation answers with success even when GitHub records nothing:
  on one pull request it accepted the same request every hour for nineteen
  hours and created no review request at all, and the pull request stayed held
  on its author until a person requested the review by hand. Stamping the
  request from the mutation's own answer hides that completely, because success
  is silent and only discards are logged.
- The read back is retried a few times, because GitHub takes a moment to record
  a request it did accept. A Copilot review of the current head is accepted as
  the same proof, since a short review can finish and take Copilot out of the
  pending requests again before the read.
- A request GitHub dropped is left undelivered, so the next pass sends it again
  and logs the miss. It needs no alarm of its own: the pull request stays held
  while the review is missing, and the hold expiring is what reports it.
- The reviewers column marks Copilot pending only where the gate applies and a
  review is genuinely in flight — a requested re-review, or the automatic first
  review on a PR the Copilot gate is holding because Copilot has never reviewed
  it. The gate scope is part of the meaning, not just a hold: a requested human
  reviewer who has not responded is left off the row entirely, so Copilot earns
  a place only where its review is what the PR is waiting on. A hold alone is
  not enough either, because unsettled required checks hold a route too. Marking
  every outstanding gate instead puts the icon on nearly every row, because a
  stale review is the ordinary state between a push and the next re-review, and
  an icon that is always present says nothing about which PRs are actually
  waiting.
- An effective reviewer-routing override is a break-glass handoff for the
  current head. It forces the reviewer route and bypasses required checks,
  Copilot review, merge conflicts, discussion actions, and approval routing. The
  author may be stuck or may need a person to explain a basic problem, so no
  automated blocker can prevent the handoff. A later push restores normal
  routing and gates.
- The dashboard binds a command to the head it sees when it first reads that
  command, and records that head in the acknowledgement comment. The handoff is
  then a comparison of two strings: the recorded head and the current one. The
  earlier design instead ordered the command against the push by comparing the
  comment timestamp with the head push time from `GET /repos/{repo}/activity`.
  Do not reintroduce that. Both timestamps have one-second resolution and come
  from different APIs, so the ordering is sometimes unknowable, which forces a
  third "cannot tell" state that every later pass has to carry forward and every
  failure path has to preserve by hand. It also fails unsafely in the case the
  handoff exists for: when the activity lookup returns nothing, the command
  hangs unacknowledged. Binding to the observed head removes the extra API call
  and keeps the answer in GitHub rather than in `dashboard-state.json`, so a
  failed pass or a dropped cache cannot corrupt it. The cost is that a push
  between the command and the pass that reads it belongs to the handoff instead
  of ending it, which is the safer direction: the author asked for help, and the
  worst case is one extra handoff the author can end with another push.
- The gate does not wait for the required checks before requesting the review,
  so the two run at once. A route computed while checks are still running is
  provisional, but the only outcome that matters here is a failure, and a
  failure routes the PR to its author, which is not a route the request is
  reachable from. Waiting cost the review however long the suite took and
  recovered nothing.
- Delivery still re-validates the required checks against live data rather than
  relying on the fingerprint alone. The fingerprint only detects change, so
  checks that were already failing when the request was recorded would otherwise
  pass through unnoticed.
- "Clean" means no inline comments on the current head, counted from the
  review, not from the classifier's actionability judgment. Accepted
  limitation: if Copilot leaves comments the classifier treats as
  non-actionable while they stay unresolved, routing sits at reviewers but the
  handoff stays held with the author and re-requests until Copilot returns a
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
- `last_activity_at` is derived from substantive participant events rather than
  the PR's `updatedAt`. Updating the status comment bumps `updatedAt`, so an
  `updatedAt`-derived fact made every refresh look like new activity, which
  queued another comment update, which bumped `updatedAt` again. Besides
  rewriting the comment indefinitely, that loop reset the inactivity clock
  `actions/stale` reads, so no PR in a dashboard repository could go stale.
  The dashboard app is never a PR's author, so `role_for` always classifies its
  comments as `bot` and they never count.
- An inline review thread's wait age and list position come from its last
  comment's `createdAt`, never its edit time. Wait age is what makes a neglected
  thread visible, so a reviewer fixing a typo in their own comment must not make
  a weeks-old thread look freshly raised. Top-level feedback items date from
  their creation time for the same reason, so editing a comment cannot reorder
  the list or reset how long an item has been waiting.

## Top-Level Feedback

- GitHub gives inline review threads explicit replies and a resolved state, but
  top-level feedback has no equivalent completion signal. Top-level feedback
  means a standalone PR comment or submitted review summary that is not
  attached to an inline review thread.
- Each top-level feedback item is therefore classified independently with a
  stable GitHub-derived id. The LLM decides only whether the source is
  actionable.
- Each refresh reconstructs these independent items with a linear scan of the
  comments and reviews already fetched from GitHub. They are not threaded, and
  the reconstructed list is not stored as a second ledger. This keeps edited or
  deleted source comments authoritative without additional reconciliation.
  Cached classifications avoid repeated LLM calls, while dashboard state
  retains the author reply already observed for each item.
- An explicit author reply is the only thing that closes a top-level item.
  Commits, PR title edits, and PR description edits are not tied to the item
  they would close, so any push after the feedback arrived would close every
  open item at once and hide feedback nobody had answered. The status comment
  lists the exact open discussions and explains how to give each one an outcome,
  which makes an explicit reply both cheap and unambiguous. An author's explicit
  commitment to future work in the current PR is a self-deferral, not a
  completed reply, so the item continues waiting on the author.
- Each model call classifies up to ten uncached top-level feedback items
  independently, while retaining a separate cache entry for every item. A
  refresh processes at most 200 such items per PR. Exceeding that cap means the
  items went unread, which is reported as a classification failure so the
  refresh is not published and the next one retries them, rather than the items
  being given an invented action. This bounds both call count and prompt size
  without allowing one long-lived PR to monopolize the workflow or model quota.
- Candidate author replies use a separate classifier with the same batch size,
  per-PR cap, and immutable cache behavior. That classifier also has a
  model-call budget that is expected to bind, so exceeding it defers the items
  instead of failing; every consumer already treats a deferred author reply as
  not yet classified, which leaves the earlier handoff in place. Its result
  distinguishes completed replies from author self-deferrals independently for
  each earlier feedback item the comment addresses. Timestamp
  ordering determines which items are candidates, but never applies a comment to
  every earlier item by itself. Candidate sets are split and model-call batches
  are greedily packed against the fully serialized prompt, so every Copilot CLI
  argument remains within the configured character limit. Partial results are
  merged into one cache entry per author comment. Completed reply evidence
  retains the source comment id as well as its timestamp, so comments created in
  the same second cannot be confused.
- "Unclear" remains classifier vocabulary but is not a route. It collapses onto
  the author when a pending action is built: when the classifier cannot tell
  what a discussion needs, the author is the one who can clarify it. There is no
  separate label for feedback blocked on a dependency, decision, or event
  outside this repository, because the author still has to drive it. A route for
  that case would name nobody, could not be nudged, and would outrank approvals,
  leaving blocked PRs unowned.
- Lifecycle transitions are deterministic after feedback and author-reply
  classification. An ordinary new item waits on the
  author with 📌 visible. Once the author gives a completed reply, the item is
  addressed and the pin disappears. Normal
  approval-based routing then decides whether the PR waits on reviewers or
  maintainers; ordinary items do not have a separate requester-confirmation
  phase.
- Review summaries are classified like other top-level feedback, independently
  of review state. A `CHANGES_REQUESTED` state affects only the reviewer's
  badge; it does not affect dashboard actions or routing. Empty review summaries
  are ignored; their inline comments, if any, define independent actions.
- Who a comment opens by addressing is extracted in code and passed to the
  classifier as `addressed_to`, rather than left for the model to find in the
  body. A reviewer routinely names other people, pull requests, and prior work
  while still asking the author for something, so only an opening run of
  mentions is treated as an addressee. Naming it explicitly is what lets the
  prompt distinguish a design question put to a maintainer, whose next move is
  that maintainer's, from feedback that merely mentions one. The item still
  waits on the author whenever it also requests, suggests, or directs a change,
  so the addressee never overrides real feedback.
- A review summary that only introduces the review — where its comments came
  from, how much weight to give them, or that the author is free to disagree
  with them — needs nothing from the author. Those comments are already
  independent items, so treating the preamble as feedback asks the author to
  answer a note about comments each tracked on their own, and its invitation to
  push back would outlive every one of them. The classifier's ambiguity
  fail-safe otherwise sends these to the author whenever the wording reads as a
  request, so the prompt names them; a preamble that also asks for something is
  ordinary feedback.
- The author reply that closed an item is retained in the cached PR result. It
  is reused only when it is newer than the item's creation time, which an edit
  never moves. Accepted tradeoff: a substantively rewritten request keeps the
  reply that answered its earlier text, so a reviewer who needs the new text
  answered should post it as a new comment. Ordinary requester-confirmation
  timestamps are not persisted.
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
