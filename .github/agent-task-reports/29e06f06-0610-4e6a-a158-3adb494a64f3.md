# PR description analysis

Keep the current title and body. They accurately describe the hourly organization-wide collector, its per-job gzip-compressed JSON Lines output, the orphan data branch, token permissions, checkpoint and retry behavior, retained job metadata, and handling of GitHub's 1,000-run and REST quota limits. The description is concise and easy to scan, and a replacement would not be meaningfully better.

```json
{
  "decision": "keep",
  "evidence": {
    "body_basis": "The workflow runs hourly, discovers active public OpenTelemetry repositories, uses a read-only GitHub App for Actions data, and uses the built-in workflow token to write the orphan data branch. The collector emits immutable gzip-compressed per-job JSON Lines records, checkpoints repository and pending-run progress, retries failed job lookups, retains attempts, matrix jobs, fork provenance, runner metadata, and job URLs, splits searches above 1,000 runs, and pauses cleanly when the REST quota is exhausted.",
    "changed_files": [
      ".github/CODEOWNERS",
      ".github/scripts/github-actions-queue/.gitignore",
      ".github/scripts/github-actions-queue/DATA_BRANCH_README.md",
      ".github/scripts/github-actions-queue/collect.py",
      ".github/scripts/github-actions-queue/test_collect.py",
      ".github/workflows/github-actions-queue-collector.yml",
      ".github/workflows/github-actions-queue-test.yml",
      "README.md",
      "github-actions-queue/README.md"
    ],
    "head_sha": "8f66336f18bbb637f105548ec82e1de7a4f611a0",
    "title_basis": "The change adds an organization-wide GitHub Actions queue-data collector, so the current title is accurate and concise."
  },
  "proposal": {
    "title": "Collect organization-wide GitHub Actions queue data",
    "body": "Collect per-job GitHub Actions timing data hourly across active public OpenTelemetry repositories and store immutable gzip-compressed JSON Lines on the orphan `otelbot/github-actions-queue-data` branch.\n\n- Use the read-only `OpenTelemetry Actions Telemetry` GitHub App and keep the built-in workflow token limited to writing the data branch.\n- Checkpoint repository progress, revisit unfinished runs, and retain failed job lookups for retry.\n- Preserve matrix jobs, attempts, fork runs, runner metadata, and direct job links in the raw dataset.\n- Split searches around GitHub's 1,000-run API limit and checkpoint cleanly if the App exhausts its REST quota."
  }
}
```
