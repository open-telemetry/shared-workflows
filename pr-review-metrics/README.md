# PR review metrics and instructions

The collector assesses inline review findings on merged PRs in opted-in public OpenTelemetry repositories. A finding is addressed if the issue shown in the reviewed revision is gone at merge. The instruction generator uses addressed human findings as examples. The collector skips assessments for bot-authored PRs.

## Collect review evidence

Add an unqualified repository name to [the opt-in list](../.github/scripts/pr-review-metrics/repositories.json). Collection starts at the configured UTC date, initially October 1, 2026, and runs daily at 05:43 UTC or by dispatching [Collect PR review metrics](https://github.com/open-telemetry/shared-workflows/actions/workflows/pr-review-metrics-collector.yml). Removing a repository stops collection without deleting its existing evidence.

Each run checks today and yesterday first, then fills the oldest unconfirmed days. It caps collection at three backfill days per repo, 180 GitHub API requests, ten new prepared evidence bundles and six new PR assessments. GitHub API calls, Git evidence commands and model requests during collection share a 25-minute deadline. This leaves time to save completed work before the 45-minute Actions job timeout.

The workflow owns the `otelbot/pr-review-metrics-data` branch. Its first run loads a pinned snapshot of the previous archive as a seed. Subsequent runs read and replace the shared-workflows snapshot with a lease on its previous revision. Do not publish to the old archive or hand-edit the new branch. The archive stores public review text, code excerpts and validated outcomes so older assessments can still be used if the originals change. Newly published records go through the secret check; model explanations and intermediate judgments are not published.

## Generate Copilot instructions

Dispatch [Generate Copilot review instructions](https://github.com/open-telemetry/shared-workflows/actions/workflows/generate-copilot-review-instructions.yml) with one input:

```sh
gh workflow run generate-copilot-review-instructions.yml \
  --repo open-telemetry/shared-workflows -f repository=semantic-conventions-conformance
```

The target must be public and opted in to collection. The generator requires at least two paired examples from addressed human findings. GPT-6.2 Sol Max drafts and judges edits from Claude Opus 5.5 High. The result replaces only Copilot instruction files, includes the path-scoped authoring guide, and leaves `AGENTS.md` alone. The run retains the patch and its citations for seven days.

Collection does not opt a repository in to bot PRs. Until `publish_instructions` is `true`, the run previews the publishing decision and makes no target writes. After enabling it, a nonempty patch against the current default-branch revision can open a draft from `opentelemetrybot`'s fork. An existing managed draft can be updated if its head still matches the last generated commit. Another open instruction PR, manual changes to the bot branch, or a closed unmerged managed PR blocks publishing. The workflow requests Copilot review and reports a warning if the request cannot be confirmed. It does not merge.

## Administrator setup

- Allow Actions to make [Copilot SDK requests billed to the organization](https://docs.github.com/en/copilot/how-tos/copilot-sdk/auth/server-to-server-tokens). No personal Copilot token is needed.
- Allow the `protected` environment to write the snapshot branch from `main`. Keep `main` protected from direct workflow pushes.
- Before enabling bot publishing, make `OPENTELEMETRYBOT_GITHUB_TOKEN` available to the `protected` environment. The token only goes to the publish step. The bot needs fork and PR permissions, not write access to target repositories. Confirm the review request with the bot on an opted-in pilot before expanding.

Run the scanner and generator tests locally from `.github/scripts/pr-review-metrics`:

```sh
python -m unittest discover -p 'test_*.py'
```
