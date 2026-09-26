Backfills now record a per-repository full-publish generation so the next serialized publisher can finish delivery if GitHub replaces the backfill's pending job. The publisher records completion only after repository-wide delivery and the dashboard issue succeed; ordinary webhook publishes remain PR-targeted.

The hourly health check reports outstanding generations instead of assuming canceled jobs drained them. Stable stays pinned to v0.15.0 until a separate promotion, so legacy stable cancellations cannot yet be verified or recovered by this protocol.
