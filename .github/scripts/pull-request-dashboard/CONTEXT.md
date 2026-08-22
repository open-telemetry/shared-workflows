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
