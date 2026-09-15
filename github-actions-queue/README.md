# GitHub Actions queue data

This centrally executed workflow collects one compact timing record for every
terminal GitHub Actions job in active public repositories under
`open-telemetry`. Records are written to the orphan
`github-actions-queue-data` branch in this repository and can be queried to
find queue-time outliers.

Queue time is measured per job:

```text
queue_seconds = job_started_at - job_created_at
```

Matrix jobs are separate records. Jobs that never receive a runner retain their
timestamps but have `runner_assigned: false` and `queue_seconds: null`, because
GitHub can report equal creation and start timestamps for such jobs.

## GitHub App

Create a GitHub App owned by `open-telemetry` and install it for the
organization. It requires only:

- Actions: read-only
- Metadata: read-only, granted automatically

The collector requests no webhook events and no write permissions. It lists
only public repositories and rejects an explicitly selected repository unless
GitHub reports `private: false`.

Configure these values in the `protected` environment of `shared-workflows`:

- Variable `ACTIONS_QUEUE_CLIENT_ID`: the App client ID
- Secret `ACTIONS_QUEUE_PRIVATE_KEY`: a generated App private key

The workflow creates a short-lived installation token for API reads. Its
built-in `GITHUB_TOKEN`, scoped to `shared-workflows`, writes the data branch.

## Collection

`.github/workflows/github-actions-queue-collector.yml` runs at minute 17 of
every hour. It discovers active public repositories dynamically, then processes
closed one-hour workflow-run windows. A repository checkpoint lets the next run
resume an incomplete window without re-emitting repositories already committed.

Runs that have not reached a terminal state are saved in `state.json`. Later
collections revisit them and emit their jobs only after every returned job is
terminal. Job listing uses `filter=all`, so all attempts available when the run
is finalized are retained.

The collector checks the installation's REST quota before doing work and stops
before making another request once 50% or less remains. It commits the partial
checkpoint and resumes during the next scheduled run.

GitHub limits a filtered workflow-run search to 1,000 results. The collector
splits busy one-hour windows into smaller ranges until each result can be
paginated safely. Jobs are paginated at 100 per request.

## Data layout

Each successful collection adds one immutable gzip-compressed JSON Lines file:

```text
jobs/date=2026-09-15/collection-123456789-1.jsonl.gz
```

`state.json` is mutable collector state and is not reporting data. Each job line
contains:

| Field | Meaning |
| ----- | ------- |
| `schema_version` | Job-record schema version. |
| `organization` | GitHub organization, currently `open-telemetry`. |
| `repository` | Repository name under `open-telemetry`. |
| `repository_id` | Stable GitHub repository ID when the runs API supplies it. |
| `workflow_name`, `workflow_id` | Workflow identity. |
| `run_id`, `run_attempt`, `run_created_at` | Workflow run identity and creation time. |
| `event`, `from_fork` | Trigger and whether the head repository was a fork. |
| `head_branch`, `head_sha` | Source revision. |
| `job_id`, `job_name` | Individual job identity. Matrix values normally appear in `job_name`. |
| `job_status`, `job_conclusion` | Terminal state and result. |
| `job_created_at`, `job_started_at`, `job_completed_at` | GitHub job timestamps. |
| `queue_seconds` | Start minus creation time, or null when no runner was assigned. |
| `runner_assigned` | Whether GitHub reported a runner name. |
| `runner_labels`, `runner_name`, `runner_group_name` | Runner classification. |
| `html_url` | Direct link to the job. |
| `collected_at` | Time the collector finalized the record. |

The format deliberately retains self-hosted jobs, retries, fork runs, and large
queue values. Reports should filter those dimensions rather than discarding raw
records during collection.

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
ending `limit`, `remaining`, and `reset` values. It applies the same 50% safety
floor locally.

## Known REST limitation

The runs API filters by original creation time, not the time of a later rerun.
The collector preserves all attempts returned when it finalizes a discovered
run, but a rerun requested after that run leaves the pending set can fall
outside later creation-time windows. Closing this gap may require a bounded
lookback or `workflow_job` webhook ingestion.

Failed job lookups are retained in `state.json` with their failure count, last
error, and last attempt time. Later collections retry them while continuing to
discover newer runs. The workflow emits a warning as long as any failed lookup
remains unresolved; failures are never converted into successful empty records.
