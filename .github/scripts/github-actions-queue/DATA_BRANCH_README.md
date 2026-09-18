# GitHub Actions queue data

This orphan branch contains compact, per-job GitHub Actions timing records for
public repositories in the `open-telemetry` organization.

Files under `jobs/date=YYYY-MM-DD/` are gzip-compressed JSON Lines. Each line is
one terminal job that ran on a runner. Collection files are immutable after any
documented one-time migration. `state.json` tracks live collection, and
`backfill-state.json` tracks automatic historical collection.
The files under `migrations/` record completed one-time data cleanups.

`report-state.json.gz` tracks the immutable files included in the hourly
aggregates. `report-data/manifest.json` and the daily JSON files under
`report-data/` are compact derived inputs for the
[queue dashboard](https://open-telemetry.github.io/shared-workflows/github-actions-queue/).
They can be rebuilt from the job files and are not the reporting source of
truth.

The collector and schema documentation live on the default branch under
[`github-actions-queue/`](https://github.com/open-telemetry/shared-workflows/tree/main/github-actions-queue).
