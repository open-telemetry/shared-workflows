# Copilot review loop report

Fixed the resumed-checkpoint reconciliation issue in
`7f1f402d9f7d5a925367dea4a1e00ad446e9d9a3`. Persisted window,
completion, and pending-run entries are now restricted to repositories
that remain active and public. Regression tests also cover removed
repositories and an empty reconciled window.

Validation:

- `python3 -m unittest discover -p 'test_*.py'` — 17 tests passed.
- Secret scanning — no secrets detected.
- CodeQL — no alerts.
- Automated code review — unavailable because its configured model was not
  present; no review findings were produced.

```json
{"comments":[{"body_sha256":"279da450bebd45cee558de6794a34c47824b222787ea592ce85c3fc7db275d42","changed_paths":[".github/scripts/github-actions-queue/collect.py",".github/scripts/github-actions-queue/test_collect.py"],"comment_id":4023137951,"disposition":"fixed","fix_commit":"7f1f402d9f7d5a925367dea4a1e00ad446e9d9a3","line":466,"path":".github/scripts/github-actions-queue/collect.py","review_id":5219253546,"source":"copilot-pull-request-reviewer","thread_id":"PRRT_kwDOTENyc86iz-Sk","url":"https://github.com/open-telemetry/shared-workflows/pull/377#discussion_r4023137951"}]}
```
