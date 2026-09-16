# Copilot review loop report

Fixed the pending-run retry ordering so a never-attempted run uses its creation
time as its initial scheduling timestamp. Updated the regression test to verify
that an older previously attempted run is selected ahead of a full batch of
newer runs.

Fix commit: `8f66336f18bbb637f105548ec82e1de7a4f611a0`

Validation:

- `python3 -m unittest discover -p 'test_*.py'`: 20 tests passed.
- `git diff --check`: passed.
- Secret scan: no secrets detected.
- CodeQL: 0 alerts.
- Automated code review: no comments (review engine reported its configured
  model was unavailable).

```json
{"comments":[{"body_sha256":"155337067d355b313ecf0916975458687f2282a0b1c171f69f55b11013194603","changed_paths":[".github/scripts/github-actions-queue/collect.py",".github/scripts/github-actions-queue/test_collect.py"],"comment_id":4024801108,"commit":"8f66336f18bbb637f105548ec82e1de7a4f611a0","current_line":478,"diff_side":"RIGHT","disposition":"fixed","original_line":478,"path":".github/scripts/github-actions-queue/collect.py","review_id":5221258792,"source":"copilot-pull-request-reviewer","thread_id":"PRRT_kwDOTENyc86i4Npm","url":"https://github.com/open-telemetry/shared-workflows/pull/377#discussion_r4024801108"}]}
```
