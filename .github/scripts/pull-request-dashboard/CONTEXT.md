# Pull request dashboard context

## Discussion lifecycle

The discussion lifecycle turns current pull request discussion into the pending
actions that drive routing. It covers the three discussion kinds below and
tracks top-level feedback across refreshes.

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
tracks the first missing report on the current head, pauses during conflicts,
and expires after four hours. Releasing a hold starts a fresh reviewer wait.

### Reviewer handoff

An acknowledged dashboard override binds a reviewer handoff to one head SHA.
While that head remains current, the handoff routes directly to approvers and
bypasses discussions, approvals, conflicts, required checks, and the Copilot
gate. A push ends the handoff.

## Routing snapshot

The routing snapshot is the shared live view used when the dashboard computes
facts and when prepared author reminders or Copilot review requests are
delivered. It carries the pull request state, draft state, node ID, head SHA,
required checks, review requests, review threads, and both routing
fingerprints.

### Routing fingerprint

The dashboard routing fingerprint covers every input that can change routing,
including required checks. Prepared author reminders compare it with the live
snapshot before delivery.

### Copilot request fingerprint

The Copilot request fingerprint covers the same routing inputs except required
checks. A request can therefore be delivered while checks move from pending to
passing. Component digests identify which covered input changed when delivery
rejects a stale request.
