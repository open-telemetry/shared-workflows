On Copilot-authored PRs, a human assignee can be the dashboard's effective author while GitHub also lists them as a requested reviewer. The dashboard then shows the author as a pending reviewer, as happened on open-telemetry/opentelemetry-java-instrumentation#20246.

The Author column keeps `trask`; the Reviewers column omits `trask ⏳` and keeps actual reviewers such as `laurit ✅`. Effective authors no longer count as pending human re-reviews, while other reviewers' requests and assignments retain their existing behavior.

Addresses open-telemetry/opentelemetry-java-instrumentation#18435.
