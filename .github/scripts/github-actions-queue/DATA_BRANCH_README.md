# GitHub Actions queue data

This orphan branch contains compact, per-job GitHub Actions timing records for
public repositories in the `open-telemetry` organization.

Files under `jobs/date=YYYY-MM-DD/` are immutable gzip-compressed JSON Lines.
Each line is one terminal workflow job. `state.json` tracks live collection, and
`backfill-state.json` tracks automatic historical collection.

The collector and schema documentation live on the default branch under
[`github-actions-queue/`](https://github.com/open-telemetry/shared-workflows/tree/main/github-actions-queue).
