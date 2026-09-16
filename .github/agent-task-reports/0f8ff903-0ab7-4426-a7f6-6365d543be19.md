## Decision

Keep the current title and body. They accurately describe the publisher write barrier, direct-workflow waiting behavior, queue-wide deferral with a five-minute delay, preservation of the processing-failure budget, and the compare-and-swap race handling. The description is concise and easy to scan without repeating test or implementation details.

```json
{
  "decision": "keep",
  "evidence": {
    "body_basis": "The complete diff adds a publisher-lock write barrier to direct and queued dashboard state updates, checks the barrier before targeted calculations and head-SHA resolution, maps lock contention to a dedicated busy status, returns all affected repository claims with a five-minute delayed retry that preserves their attempt budget, increases scheduled recovery to every five minutes, and documents and tests these behaviors. The pinned title and body cover these user-visible behavioral changes accurately and concisely.",
    "changed_files": [
      ".github/scripts/pull-request-dashboard/RATIONALE.md",
      ".github/scripts/pull-request-dashboard/WEBHOOK_SETUP.md",
      ".github/scripts/pull-request-dashboard/dashboard.py",
      ".github/scripts/pull-request-dashboard/netlify.toml",
      ".github/scripts/pull-request-dashboard/netlify/lib/dashboard-queue.mjs",
      ".github/scripts/pull-request-dashboard/process_queue_batch.py",
      ".github/scripts/pull-request-dashboard/state_branch.py",
      ".github/scripts/pull-request-dashboard/test_dashboard.py",
      ".github/scripts/pull-request-dashboard/test_dashboard_queue.mjs",
      ".github/scripts/pull-request-dashboard/test_process_queue_batch.py",
      ".github/scripts/pull-request-dashboard/test_state_branch.py"
    ]
  },
  "proposal": {
    "title": "Prevent dashboard publisher starvation",
    "body": "Prevents concurrent dashboard state updates from starving a publisher. State writers respect the repository publisher lease, while `--force-with-lease` handles races that begin before the lease commit.\n\nDirect workflows wait for the publisher. Queue workers return every claim for a busy repository and retry after five minutes without using the processing-failure budget. Targeted updates and head-SHA claim resolution check the lease before GitHub API or Copilot work.\n\nFixes #341"
  },
  "identity": {
    "request": "0f8ff903-0ab7-4426-a7f6-6365d543be19",
    "repository": "open-telemetry/shared-workflows",
    "pull_request": 347,
    "head": {
      "repository": "open-telemetry/shared-workflows",
      "branch": "trask-fix-dashboard-publisher-contention",
      "sha": "f1e7ea3dabd0fab27c6fadc2d257c97ce574e106"
    },
    "base": {
      "repository": "open-telemetry/shared-workflows",
      "branch": "main"
    },
    "title": "Prevent dashboard publisher starvation",
    "body": "Prevents concurrent dashboard state updates from starving a publisher. State writers respect the repository publisher lease, while `--force-with-lease` handles races that begin before the lease commit.\n\nDirect workflows wait for the publisher. Queue workers return every claim for a busy repository and retry after five minutes without using the processing-failure budget. Targeted updates and head-SHA claim resolution check the lease before GitHub API or Copilot work.\n\nFixes #341"
  }
}
```
