# Pull Request Dashboard

A centralized shared workflow that builds and publishes a per-repository pull request triage dashboard for opted-in OpenTelemetry repositories. For each target repository, the workflow scans open PRs, classifies who has the next action, publishes the dashboard as a GitHub issue, and optionally sends Slack notifications.

The workflow runs from `open-telemetry/shared-workflows` and targets the repositories listed in [`repositories.json`](../.github/scripts/pull-request-dashboard/repositories.json). Target repositories do not need to host any workflow files.

Webhook-triggered incremental runs keep active dashboards close to real time. Hourly runs provide a backstop for missed or failed targeted refreshes.

The classification cache reuses prior results for unchanged review threads, minimizing Copilot token usage.

## Dashboard columns

The dashboard groups open non-draft pull requests by who is expected to act next (e.g. *Waiting on reviewers*, *Waiting on authors*, *Waiting on maintainers*, *Waiting on external*). Draft PRs are listed separately at the bottom unless `large_repo` rendering is enabled. Within each group, rows are sorted longest-waiting first. Every row has these six columns:

- **PR** — Pull request number and title. The number autolinks to the PR on GitHub.
- **Author** — GitHub login of the PR author.
- **Reviewers** — Reviewers who have engaged with the PR, each annotated with one or more icons:
  - ✅ approved
  - ✔️ approved (non-code-owner — does **not** count toward merge requirements)
  - 💬 has an open (unresolved) review thread on the PR
  - 📌 has tracked top-level feedback that still needs author action
  - 🔴 requested changes
  - Icons combine when multiple states apply. For example, 💬📌 means the reviewer has both an unresolved inline thread and tracked top-level feedback; ✅ may accompany either or both.
- **CI** — Aggregate check status across the PR:
  - ✅ all checks passing
  - ⏳ at least one check pending, none failing
  - ❌ at least one check failing
  - `?` check data could not be fetched
- **Conflicts** — Whether the PR has merge conflicts against its base branch:
  - ✅ no conflicts
  - ❌ has conflicts
  - `?` could not be determined
- **Age** — How long the PR has been waiting on the current next-action owner,
  not the calendar age of the PR. For example, a six-month-old PR that received a
  reviewer comment yesterday shows `1d` when it is waiting on the author. When
  possible, this is based on the oldest pending thread for the routed party;
  otherwise it falls back to the latest activity from the opposite party, then
  to recent PR activity or PR creation time. Format: `<1m`, `Xm`, `Xh`, or
  `Xd`.

## How to opt in

Open a pull request that adds your repository to [`.github/scripts/pull-request-dashboard/repositories.json`](../.github/scripts/pull-request-dashboard/repositories.json):

```json
[
  {
    "name": "example-repo",
    "approver_teams": ["example-approvers"],
    "required_approvals": 1,
    "slack_channel": "#example-maintainers",
    "slack_user_mapping": {
      "octocat": "U0123456789"
    }
  }
]
```

Fields:

| Field | Required | Description |
| ----- | -------- | ----------- |
| `name` | yes | Name of the repository under `open-telemetry`. |
| `approver_teams` | yes | GitHub team slugs whose members count as approvers. |
| `required_approvals` | yes | Number of approvals required for an open PR to be marked ready to merge. |
| `slack_channel` | no | Slack channel for notifications. Omit to skip Slack processing for this repository. |
| `slack_user_mapping` | no | Map of GitHub login to Slack user ID for at-mentions. |
| `large_repo` | no | If `true`, apply rendering presets that keep the dashboard body under GitHub's 65,536-character issue-body limit: cap each section (each *Waiting on …* table, the *Draft pull requests* table, and the *Diagnostics* block) at 100 rows, and omit the *Draft pull requests* section entirely. Truncated sections get a `_More X PRs not shown_` footer. Defaults to `false` (no cap, drafts shown). Enable this for very large repos with hundreds of PRs. |
| `stale_waiting_on_author` | no | If `true`, apply `Stale` after one quiet week following the general nudge and close after another quiet week. Subsequent human activity restarts the current escalation stage. Nudges apply to every repository. Defaults to `false`. |

Ask a maintainer or admin to add the repository under [Repository access](https://github.com/organizations/open-telemetry/settings/installations/133550497).

Once the PR is merged, the dashboard will pick up your repository on its next hourly run. To run it sooner, see [Manual dashboard run](#manual-dashboard-run). The dashboard issue is discovered dynamically in your repository by the `dashboard` label and `Pull Request Dashboard` title; if it does not exist, the publish step creates the label and issue.

## Review feedback lifecycle

Inline review threads and top-level feedback have different lifecycles on
GitHub. Top-level feedback means a standalone PR comment or submitted review
summary that is not attached to an inline review thread.

- An inline thread remains on the dashboard until someone marks the conversation
  resolved on GitHub or GitHub marks its code anchor outdated. An author reply
  can hand the dashboard action back to reviewers, but it does not close the
  thread. After addressing it, authors should reply with the outcome and, when
  appropriate, resolve the conversation.
- Top-level feedback has no resolved state. The dashboard therefore tracks each
  actionable top-level feedback item independently. 📌 means that one or more
  of those items are waiting on the author.
- Reviewer badges reflect GitHub's latest review state. A `CHANGES_REQUESTED`
  state affects only the reviewer's badge; it does not affect dashboard
  classification or routing. 🔴 remains until a later approval or dismissal
  clears that state. Empty review summaries are ignored; any inline comments
  are tracked through their own threads.

### Evidence for top-level feedback

For each actionable top-level feedback item, the dashboard identifies one or
more kinds of observable author evidence:

| Evidence | Used when the request asks for | Observable signal |
| -------- | ------------------------------ | ----------------- |
| `commit` | Code, tests, documentation files, or other repository changes | A later commit attributed to the PR author |
| `description` | A PR description update | A later description edit by the PR author |
| `title` | A PR title update | A later title rename by the PR author |
| `reply` | An answer, decision, clarification, or action without another tracked signal | A later standalone PR comment by the author |

A compound feedback item can require multiple evidence kinds. For example, a
request to change the implementation and update the PR description requires
both a later commit and a later description edit. One commit is sufficient
observable evidence for a request containing several code changes; the
dashboard does not try to prove that every requested edit appears in that
commit.

An explicit author reply always addresses the item, even when another evidence
kind was expected. This lets authors explain why a suggestion was not applied,
ask a clarifying question, or otherwise close the dashboard action.
The dashboard intentionally treats evidence as a handoff signal, not proof that
the reviewer agrees with the outcome.

Title and description edits are tracked separately, so a compound request can
require either or both.

### Routing after evidence

| Item | Before accepted evidence | After accepted evidence | When it clears |
| ---- | ------------------------ | ----------------------- | -------------- |
| Top-level author action | Waiting on the author; 📌 is visible | Addressed; 📌 disappears and normal approval-based routing resumes | Immediately after all expected evidence is observed, or after an explicit author reply |

GitHub remains responsible for enforcing blocking review states when a
maintainer attempts to merge.

This can hand a PR back to reviewers before every requested change is actually
complete, such as when an unrelated commit matches the expected evidence kind.
That handoff is deliberate: reviewers can see the activity and respond, while
the dashboard avoids leaving an active author indefinitely marked as blocked.

## Live PR status comment

After the first full dashboard run has populated repository state, each targeted
PR update creates or updates one dashboard-managed status comment on that PR.
The comment shows who has the next action and what the PR is waiting on. When the
author has the next action, it also links separately to unresolved inline review
threads and top-level feedback when possible. Draft PRs show that they are
waiting for the author to mark them ready for review.

A hidden marker lets the workflow update the comment in place. Existing one-time
guidance comments are upgraded rather than duplicated. The comment also asks
authors to give each review feedback item a clear outcome, which keeps stale or
ambiguous feedback from being routed to the wrong person.

Reviewers should prefer inline comments for feedback requiring explicit
resolution. See
[`RATIONALE.md`](../.github/scripts/pull-request-dashboard/RATIONALE.md#top-level-feedback)
for the tradeoffs behind this behavior.

Targeted updates received before the first full dashboard run are ignored.

## Author follow-up lifecycle

Hourly runs process two independent reminder schedules. Manually triggered runs
without a PR number do the same. Each run evaluates follow-up actions only for
PRs refreshed during that run. Since a repository refresh is capped at 50 PRs,
a due action in a larger repository may wait for a later round-robin run, but it
is delivered as soon as the PR is next refreshed.

- **Handoff nudge:** If the author acts, nobody else responds, and the PR remains
  in *Waiting on authors*, the dashboard posts a nudge one day after the first
  action. This catches cases where the author may be waiting for review while
  the dashboard still expects them to act. Later author activity does not reset
  the timer to ensure repeated author activity without a successful transition
  out of *Waiting on authors* cannot postpone the nudge. If the
  general nudge is due within one day, the handoff nudge is skipped to avoid two
  closely spaced comments.
- **General nudge:** Every PR still routed to the author receives a nudge one
  week after it entered that route, even if it already received the handoff
  nudge.

Both nudges link to the dashboard-managed status comment and apply to every
configured repository.

Repositories with `stale_waiting_on_author` enabled apply `Stale` after one
quiet week following the general nudge and close after another quiet week.
Author pushes and author or reviewer comments and reviews restart the current
escalation stage. Activity after stale labeling removes a dashboard-managed
`Stale` label before starting a fresh one-week stale wait. Manually removing a
dashboard-managed `Stale` label has the same effect. Bot activity, reactions,
label or assignment changes, checks, and edits to existing comments do not
count as human activity.

All follow-up actions apply only while the PR remains in *Waiting on authors*.
Leaving that route clears the cycle and removes a dashboard-managed `Stale`
label; a label that already existed before dashboard escalation is not removed.

At most one handoff nudge and one general nudge are posted during an
uninterrupted *Waiting on authors* period. Repositories that do not enable stale
handling receive only these reminders.

## Configuration

The target repository GitHub App is installed on each configured repository.
The workflow creates repository-scoped app installation tokens with
`PR_DASHBOARD_CLIENT_ID` and `PR_DASHBOARD_PRIVATE_KEY`, then uses those tokens for
API reads/writes and approver team membership reads in the target repository.

Slack notifications use the shared `SLACK_WEBHOOK_URL` secret. Each repository
can route notifications to its own `slack_channel` and map GitHub logins to
Slack user IDs via `slack_user_mapping`. Repositories without `slack_channel`
configured skip Slack notification processing. Scheduled Slack follow-ups are
also limited to PRs refreshed during the current hourly run, so a stale cached
route cannot produce a reminder.

## Prerequisites

The target repository GitHub App must be installed on your repository. See [`WEBHOOK_SETUP.md`](../.github/scripts/pull-request-dashboard/WEBHOOK_SETUP.md) for the GitHub App configuration this repo uses.

## State

Dashboard state is stored on the shared state branch configured by
`DASHBOARD_STATE_BRANCH`. State files are namespaced by target repository, for
example:

```text
semantic-conventions-genai/dashboard-state.json
opentelemetry-java-instrumentation/dashboard-state.json
```

This lets one central workflow manage multiple target repositories without
state collisions.

## Implementation

The workflow YAML and supporting scripts live in this repo:

- [`.github/workflows/pull-request-dashboard.yml`](../.github/workflows/pull-request-dashboard.yml) — top-level orchestrator
- [`.github/workflows/pull-request-dashboard-repo.yml`](../.github/workflows/pull-request-dashboard-repo.yml) — per-repository job
- [`.github/workflows/pull-request-dashboard-deploy-webhook.yml`](../.github/workflows/pull-request-dashboard-deploy-webhook.yml) — webhook bridge deploy
- [`.github/scripts/pull-request-dashboard/`](../.github/scripts/pull-request-dashboard/) — Python scripts, state handling, rendering

See [`RATIONALE.md`](../.github/scripts/pull-request-dashboard/RATIONALE.md) for the architecture and tradeoffs behind the design.
See [`WEBHOOK_SETUP.md`](../.github/scripts/pull-request-dashboard/WEBHOOK_SETUP.md) for GitHub App webhook permissions and dispatch setup.

## Manual dashboard run

To run the dashboard manually, open the
[Pull request dashboard workflow](https://github.com/open-telemetry/shared-workflows/actions/workflows/pull-request-dashboard.yml),
choose **Run workflow**, and populate the `repository` field with the target
repository name under `open-telemetry`, for example
`opentelemetry-java-instrumentation`. Leave `repository` empty to update every
configured repository. Each repository is subject to the PR cap so large repos
cannot consume the dashboard App's hourly API quota in a single run.
