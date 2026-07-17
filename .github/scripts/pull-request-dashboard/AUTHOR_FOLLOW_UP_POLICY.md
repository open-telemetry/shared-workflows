# Author follow-up policy

This note defines the implemented author follow-up lifecycle.

Every lifecycle event below applies only while the PR is routed to *Waiting on
authors*. No nudge, stale label, or closure is performed under any other route.

| Lifecycle event | Clock starts from | Effect of subsequent human activity | Action when due |
| --------------- | ----------------- | ----------------------------------- | --------------- |
| **Handoff nudge** | The first substantive author action since the latest reviewer action | Later author activity does not postpone the nudge. Reviewer activity clears the pending handoff; a later author action can start a new one. | Post after one day if the PR is still *Waiting on authors*, unless it is suppressed because the general nudge is imminent. |
| **General nudge** | First backfill observation of entry into *Waiting on authors* | Human activity does not postpone the nudge. Only leaving *Waiting on authors* clears this clock. | Post after one week if the PR is still *Waiting on authors*. |
| **Apply `Stale`** | The later of the general nudge and the latest subsequent substantive human activity | Author or reviewer activity after the general nudge restarts the one-week quiet period. | Apply `Stale` after one quiet week if the PR is still *Waiting on authors*. |
| **Close** | Application of the dashboard-managed `Stale` label | With no activity, close after one week. Human activity removes the dashboard-managed `Stale` label and returns the PR to the preceding stale-wait stage. | Close after one quiet week if the PR is still *Waiting on authors* and still has the dashboard-managed `Stale` label. |

## Handoff nudge suppression

Suppress the handoff nudge if it would become due within 24 hours before the
scheduled general nudge. The general nudge is still posted at its normal time;
its message and delivery record remain independent. This prevents two lifecycle
comments from arriving within one day without changing the meaning of either
reminder.

Suppression consumes that pending handoff. Specifically, discard the pending
handoff when its scheduled deadline falls in the inclusive interval from 24
hours before the scheduled general-nudge deadline through the general-nudge
deadline. Compare scheduled deadlines rather than actual workflow or comment
times so a delayed hourly run does not change the outcome.

Further author activity before the general nudge does not start another handoff
clock after suppression. If the general nudge has already been delivered when
the author acts, the handoff nudge remains independent and is posted one day
after the qualifying author action. Deliver at most one handoff nudge and one
general nudge during an uninterrupted *Waiting on authors* period. A suppressed
handoff does not count as delivered, so a later author action after the general
nudge can still start a handoff clock.

## Substantive human activity

The following events count:

- A commit pushed by the author.
- A standalone or inline comment by the author or any other human.
- A submitted review by any other human.

Bot comments, dashboard mutations, reactions, label or assignment changes,
check-run activity, and edits to existing comments do not count.

## Delivery and mutation safeguards

The stale clock starts only after the general-nudge comment is successfully
posted. The close clock starts only after the dashboard successfully applies
`Stale`. Failed API calls do not advance the lifecycle.

Immediately before closing, fetch and verify the current PR state, route,
latest substantive human activity, and presence and ownership of the `Stale`
label. Do not close if any condition changed after the backfill scan.

If a maintainer manually removes a dashboard-owned `Stale` label, do not
reapply it immediately. Treat removal as an explicit intervention that returns
the PR to a fresh one-week stale-wait period. Label changes do not otherwise
count as substantive human activity.

Leaving *Waiting on authors* clears all pending clocks for the routing period
and removes only a `Stale` label that the dashboard applied. Re-entering starts
a new cycle from the persisted time when a backfill follow-up job first observes
the route. Do not reconstruct the transition from dashboard age. A pre-existing
`Stale` label is never treated as dashboard-owned.
