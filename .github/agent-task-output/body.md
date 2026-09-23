Publishes hourly GitHub Actions queue-time percentiles at `https://open-telemetry.github.io/shared-workflows/github-actions-queue/` using compact daily aggregates from the existing data branch.

- Adds runner-host, runner-label, repository, and inclusive UTC date filters with shareable query-string selections; dates default to the latest seven days.
- Shows exact p50, p90, p95, and p99 summaries across the selected dates.
- Collects only completed jobs that ran on a runner.
- Removes verified partial-rerun carry-forward clones without changing the raw schema.

The first collector run after merge removes 99,094 runnerless records and 20,991 rerun clones, then rebuilds the report from the remaining 466,100 measurable executions. GitHub Pages must be enabled once with GitHub Actions as the source.
