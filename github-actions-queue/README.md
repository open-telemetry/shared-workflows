# GitHub Actions queue data

This centrally executed workflow collects one compact timing record for every
terminal GitHub Actions job in active public repositories under
`open-telemetry`. Records are written to the orphan
`otelbot/github-actions-queue-data` branch in this repository and can be
queried to find queue-time outliers.

Queue time is measured per job:

```text
queue_seconds = job_started_at - job_created_at
```

Matrix jobs are separate records. Jobs that never receive a runner are not
stored because they have no runner queue time. This excludes skipped jobs,
jobs cancelled before assignment, and check runs created only to publish
results.

When only failed jobs are rerun, GitHub also returns cloned records for jobs
that did not execute again. The clones have a later `created_at` but retain the
original execution timestamps. The collector removes a clone only when it has
exactly one earlier non-negative execution match in the same workflow run.

## Dashboard

The hourly queue-time dashboard is published at
<https://open-telemetry.github.io/shared-workflows/github-actions-queue/>. It
opens to the latest seven days of GitHub-hosted runner data and provides:

- Required GitHub-hosted or self-hosted runner selection
- Runner labels scoped to the selected host category
- Repository filtering
- Inclusive UTC start and end dates that default to the latest seven days
- Shareable URLs that retain the selected filters
- Hourly p50, p90, p95, and p99 queue times in UTC
- Exact p50, p90, p95, and p99 queue times across the selected range

The self-hosted category matches runner labels case-insensitively against:

```text
self-hosted
cncf-*
oracle-*
*-s390x
```

These broad patterns cover the special self-hosted runners documented in the
[OpenTelemetry community asset inventory](https://github.com/open-telemetry/community/blob/main/assets.md#special-github-action-runners).
All other assigned runners are classified as GitHub-hosted. In particular,
`otel-windows-latest-8-cores` is GitHub-hosted even though it uses an
organization-defined runner group.

The collector incrementally updates `report-state.json.gz` and daily report
partitions under `report-data/` on the data branch. The Pages artifact includes
only the dashboard assets and those derived partitions, not the raw job files
or collector checkpoints.

Repository administrators must enable GitHub Pages once in the repository
settings and select **GitHub Actions** as the source. The deployment uses the
standard `github-pages` environment and needs no new GitHub App permission,
token, or secret.

## GitHub App

Use the private organization-owned GitHub App named
`OpenTelemetry Actions Telemetry`. Its homepage is
`https://github.com/open-telemetry/shared-workflows`, and it has no webhook or
event subscriptions.

The App requires only:

- Actions: read-only
- Metadata: read-only, granted automatically

Install it for **All repositories** so one installation token can read Actions
data across the organization and new repositories are covered automatically.
It requests no webhook events and no write permissions. The collector lists
only public repositories and rejects an explicitly selected repository unless
GitHub reports `private: false`.

Store its credentials in the `protected` environment of `shared-workflows`:

- Variable `ACTIONS_TELEMETRY_CLIENT_ID`
- Secret `ACTIONS_TELEMETRY_PRIVATE_KEY`

The workflow creates a short-lived installation token for API reads. Its
built-in `GITHUB_TOKEN`, scoped to `shared-workflows`, writes the data branch.

## Collection

`.github/workflows/github-actions-queue-collector.yml` runs at minute 17 of
every hour. It discovers active public repositories dynamically, then processes
closed one-hour workflow-run windows. A repository checkpoint lets the next run
resume an incomplete window without re-emitting repositories already committed.

The collector prioritizes live data, then uses the remaining run budget to
backfill from `2026-09-17T02:00:00Z` toward `2026-01-01T00:00:00Z`. Backfill
processes newer hours first and continues automatically on each schedule.
`backfill-state.json` stores the oldest complete hour plus partial repository
progress, independently of the live `state.json` cursor.

Up to four repositories are processed concurrently. Workflow invocations stop
after at most 20 total windows, 12,000 requests, or 40 minutes of collection.
These limits leave room below the App's 15,000-request hourly quota and the
workflow's 50-minute timeout. A stopped run commits its records and checkpoints
before the next schedule resumes it.

When a run performs the one-time migration, collection has a 30-minute limit
to leave time for the migration, full report rebuild, and data-branch push
within the workflow timeout. Later runs use the normal 40-minute limit.

Runs that have not reached a terminal state are saved in `state.json`. Later
collections revisit them and emit their jobs only after every returned job is
terminal. Job listing uses `filter=all`, so actual executions from every
available attempt are retained when the run is finalized. Carry-forward clones
created by partial reruns are not stored.

The collector tracks the installation's REST quota. If it exhausts the quota,
it commits the partial checkpoint and resumes during the next scheduled run.

The one-time data migration reads collection files in streaming passes to
validate runner assignment and partial-rerun matches before rewriting any
files. It records completion under `migrations/` on the data branch.

GitHub limits a filtered workflow-run search to 1,000 results. The collector
splits busy one-hour windows into smaller ranges until each result can be
paginated safely. Jobs are paginated at 100 per request.

## Data layout

Each successful collection adds one immutable gzip-compressed JSON Lines file:

```text
jobs/date=2026-09-15/collection-123456789-1.jsonl.gz
```

`state.json` and `backfill-state.json` are mutable collector checkpoints, not
reporting data. Each job line contains:

| Field | Meaning |
| ----- | ------- |
| `schema_version` | Job-record schema version. |
| `organization` | GitHub organization, currently `open-telemetry`. |
| `repository` | Repository name under `open-telemetry`. |
| `repository_id` | Stable GitHub repository ID when the runs API supplies it. |
| `workflow_name`, `workflow_id` | Workflow identity. |
| `run_id`, `run_attempt`, `run_created_at` | Workflow run identity and creation time. |
| `event`, `from_fork` | Trigger and whether the head repository was a fork. `from_fork` is null when GitHub no longer provides the head repository. |
| `head_branch`, `head_sha` | Source revision. |
| `job_id`, `job_name` | Individual job identity. Matrix values normally appear in `job_name`. |
| `job_status`, `job_conclusion` | Terminal state and result. |
| `job_created_at`, `job_started_at`, `job_completed_at` | GitHub job timestamps. |
| `queue_seconds` | Start minus creation time. |
| `runner_assigned` | Always true for retained queue records. |
| `runner_labels`, `runner_name`, `runner_group_name` | Runner classification. |
| `html_url` | Direct link to the job. |
| `collected_at` | Time the collector finalized the record. |

The format deliberately retains self-hosted jobs, actual retries, fork runs,
and large queue values. Reports should filter those dimensions rather than
discarding raw records during collection.

## Local use

Set `GH_TOKEN`, `GITHUB_AUTH_TOKEN`, or `GITHUB_TOKEN`, then use a temporary
state and output directory:

```bash
python3 .github/scripts/github-actions-queue/collect.py \
  --repository shared-workflows \
  --start 2026-09-15T00:00:00Z \
  --state /tmp/actions-queue-state.json \
  --output-dir /tmp/actions-queue-jobs \
  --collection-id local-test \
  --max-windows 1
```

The command prints a JSON summary containing the request count plus starting and
ending `limit`, `remaining`, and `reset` values. The starting snapshot comes
from the first ordinary API response because GitHub's `/rate_limit` response
can lag the bucket reported by repository endpoints. The summary also reports
the lowest remaining fraction observed because GitHub can return fluctuating
quota headers across sequential public-repository requests.

## Known REST limitation

The runs API filters by original creation time, not the time of a later rerun.
The collector preserves all attempts returned when it finalizes a discovered
run, but a rerun requested after that run leaves the pending set can fall
outside later creation-time windows. Closing this gap may require a bounded
lookback or `workflow_job` webhook ingestion.

Failed job lookups are retained in `state.json` with their failure count, last
error, and last attempt time. Backfill failures are retained in
`backfill-state.json` the same way. Later collections retry them while
continuing to discover other runs. The workflow emits a warning as long as any
failed lookup remains unresolved; failures are never converted into successful
empty records.

GitHub announced that organization retention settings will cover workflow runs
created on or after October 1, 2026. The change is not retroactive. The January
2026 workflow-run and job metadata needed by this backfill was verified before
collection began.
