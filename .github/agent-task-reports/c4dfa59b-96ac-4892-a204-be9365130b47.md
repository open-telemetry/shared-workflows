# CI fix report

## Outcome

Fixed the pull-request security findings by preventing checkout from persisting its
credential and limiting the collector's read-only GitHub App token to the
organization's current public repositories.

## Validation

- `cd .github/scripts/github-actions-queue && python3 -m unittest discover -p 'test_*.py'`
  — passed, 20 tests.
- `git diff --check` — passed.
- Changed-file secret scan — passed, no secrets detected.
- Parallel CodeQL scan — passed, no Actions alerts.
- Local `actionlint` and `zizmor` were unavailable; the repository's completed
  queue collector tests and Zizmor workflow are authoritative on rerun.

```json
{
  "changed_paths": [
    ".github/agent-task-reports/c4dfa59b-96ac-4892-a204-be9365130b47.md",
    ".github/workflows/github-actions-queue-collector.yml"
  ],
  "failing_checks": [
    {
      "key": "104705281292",
      "name": "zizmor",
      "log_digest": "credential persistence through GitHub Actions artifacts: does not set persist-credentials: false; dangerous use of GitHub App tokens: token granted access to all repositories for this owner's app installation",
      "outcome": "fixed",
      "fix_commits": [
        "de6179d8f0edcd9c94bc995f24fc1735fbbce896",
        "4076ad1e7b825752d99231ba1634ad0067c6d83b"
      ]
    }
  ],
  "ordered_commits": [
    "de6179d8f0edcd9c94bc995f24fc1735fbbce896",
    "4076ad1e7b825752d99231ba1634ad0067c6d83b"
  ]
}
```
