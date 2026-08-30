# Pull request dashboard context

## Evaluation, acceptance, and persistence

Pull request evaluation is the dashboard's data plane. Given repository policy,
a pull request number, and the previous result, `pull_request_evaluation.py`
reads a canonical `PullRequestSource` and produces either `EvaluationSuccess` or
`EvaluationFailure`. A success carries typed `DashboardFacts`, the final route,
diagnostics, pending actions, and top-level history. A failure carries a failure
route and error, so failure routes cannot be combined with successful results.
Evaluation owns effective-author resolution, activity, discussions,
classification, lifecycle and routing decisions, reviewer projection, command
acknowledgements, author-action links, nudge episodes, and PR-specific failure
shaping. It has no accepted-state or state-branch side effects.

`pull_request_source.py` is the sole aggregate GitHub source boundary. It
schedules the concurrent `gh pr view`, REST, and GraphQL reads, settles required
checks against branch rules, rejects a stale check rollup, and normalizes the
results into frozen records for pull metadata, actors, commits, comments,
reviews, review requests, review threads, checks, and non-blocking failures.
`github_cli.py` remains the transport layer; its response dictionaries do not
cross into evaluation or domain modules.

`dashboard_contracts.py` defines the immutable in-memory boundary. A
`StoredDashboardResult` projects an evaluation success down to the fields that
survive refreshes. `DashboardState` holds those projections and the initial
backfill marker. It also tracks draft PR numbers separately so drafts participate
in concurrent update detection without becoming routed results. Evaluation
diagnostics keep typed classification results and freeze only the source
discussion records.

`state.py` owns the JSON boundary. Its dashboard facts, stored-result, and state
codecs translate the immutable contracts to the version 13
`dashboard-state.json` shape. Malformed pull request entries are discarded
individually, so one bad entry does not prevent valid entries from loading.

`dashboard_state_update.py` owns the acceptance transaction for one pull request
slot. It prepares the cached starting value, reconciles an evaluation with the
latest accepted state, protects a slot changed by another writer, rejects failed
evaluations, and returns the accepted state plus an explicit effect plan for
persistence, status comments, observations, and backfill-failure clearing. The
transaction is pure and does not fetch GitHub data or write state files.

`dashboard.py` is the operational control plane. It selects targeted and
backfill work, calls evaluation and the shared acceptance transaction, applies
the planned effects, advances backfill scheduling state, and reports diagnostics
and CLI status.

`state_branch.py` owns the Git checkout, commit, push, and retry mechanics around
those updates. The state branch remains the durable compare-and-swap boundary;
the acceptance transaction only decides which dashboard state a retry may
persist.

## Activity timeline

The activity timeline projects canonical commits, issue comments, review
comments, and reviews into ordered activity events. Each event uses a normalized
actor, role, and activity timestamp while preserving the source fields needed by
reviewer state and the discussion lifecycle. Comment and review events also
preserve their creation time for ordering.

### Substantive activity

Substantive activity is participant activity that can advance an activity clock.
It excludes activity in the bot role and non-author merge commits. A bot that
opened the pull request takes the author role instead, so its own activity still
counts. Non-comment review states are substantive without body text; other
activity events require non-whitespace text. The timeline records separate
latest participant, author, and approver activity clocks.

## Discussion lifecycle

The discussion lifecycle turns current pull request discussion into the pending
actions that drive routing. It covers the three discussion kinds below and
tracks top-level feedback across refreshes.

## Classification policy

`classification_policy.py` is the pure classification boundary. It defines
immutable discussion identities, decisions, feedback outcomes, successful
results, failures, deferrals, diagnostics, model requests, and raw model
responses. It also owns prompt inputs and rendering, deterministic review-thread
shortcuts, prompt batching plans, response validation, verdict-to-action
mapping, result projection, and cache-key computation.

Policy preparation turns typed discussions into model requests or deterministic
results. Policy resolution consumes a typed raw model response and returns typed
classification results. Neither phase reads files, changes the environment, or
starts a process.

`classification_execution.py` owns operational classification. Its service
accepts one immutable request containing typed policy discussions and returns
typed `DiscussionClassifications`. The service coordinates deterministic
review-thread results, cache hits, model requests, batching, per-pull-request
limits, failures, and deferrals. It attributes each model call to the first
result produced by that call.

The model runner accepts an immutable rendered prompt and returns a typed
`RawModelResponse`. The production runner owns the Copilot CLI command, timeout,
model argument, telemetry environment and temporary file, and telemetry
diagnostics. It has no cache dependency. The cache store owns the existing
per-pull-request JSON files, validation, replacement, and pruning. It has no
classification policy.

Production evaluation builds the typed request directly, and the dashboard
prunes classification caches through the default cache store.

### Review thread

An unresolved, non-outdated inline review conversation. Its latest meaningful
comment determines whether the author or reviewer has the pending action.

### Top-level feedback

Actionable feedback in a pull request comment or review summary that is not
attached to an inline review thread. GitHub has no resolved state for it, so the
dashboard tracks its outcome.

### Author reply

A later top-level comment from the pull request author that addresses one or
more top-level feedback items. A completed reply closes the matching pending
action. A reply that commits to more work keeps it with the author.

### Pending action

One discussion waiting on either the author or a reviewer. Pull request routing
combines these actions with checks, approvals, conflicts, overrides, and
Copilot review state.

### Top-level history

The durable evidence that a specific author reply closed a top-level feedback
item. It lets later refreshes preserve that outcome when the reply does not
need classification again.

## Routing decision

The routing decision is one transition from current facts and pending actions,
plus the previous route and durable facts, to a final route and enriched facts.
It owns route progression, required-check and Copilot coordination, and every
clock that affects routing.

### Gate hold

An unsettled required check or Copilot review can stop a pull request from
advancing, but cannot stop it from moving back toward its author. The hold
starts its clock when an unreported gate blocks progress to a reviewer route.
A missing report does not start the clock while the pull request remains on an
existing reviewer route. Conflicts clear the clock and restart it when they
resolve. The hold expires after four hours. Releasing a hold that kept the pull
request with its author starts a fresh reviewer wait. A release between
reviewer routes keeps the existing wait.

### Reviewer handoff

An acknowledged dashboard override binds a reviewer handoff to one head SHA.
While that head remains current, the handoff routes directly to approvers and
bypasses discussions, approvals, conflicts, required checks, and the Copilot
gate. A push or newer actionable human reviewer feedback ends the handoff.

## Routing snapshot

The routing snapshot is the shared live view used when the dashboard computes
facts and when prepared author reminders or Copilot review requests are
delivered. It carries the pull request state, draft state, node ID, head SHA,
required checks, review requests, review threads, and both routing
fingerprints.

The source boundary also carries a frozen fingerprint projection with the
historical JSON field spellings. Routing hashes that projection rather than the
domain records, preserving existing fingerprints while keeping transport shapes
out of routing logic.

### Routing fingerprint

The dashboard routing fingerprint covers every input that can change routing,
including required checks. Prepared author reminders compare it with the live
snapshot before delivery.

### Copilot request fingerprint

The Copilot request fingerprint covers the same routing inputs except required
checks. A request can therefore be delivered while checks move from pending to
passing. Component digests identify which covered input changed when delivery
rejects a stale request.

## Reviewer state

Reviewer state is prepared once from normalized pull request events, current
review requests, and assignee actors. It determines active approver-team
approvals, normalized assignees, and pending human re-reviews before routing.

### Pending re-review

A pending re-review is an individual review request for someone who has already
submitted a review. It invalidates that reviewer's approval but keeps a
CHANGES_REQUESTED state active. Team requests and first-time review requests do
not create pending re-review rows.

### Reviewer summary

A reviewer summary is the final dashboard row produced after discussion actions
are resolved. It combines approval state, pending re-review, changes requested,
inline discussion ownership, top-level feedback ownership, approver
participation, and assignee visibility.
