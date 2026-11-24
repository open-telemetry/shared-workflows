# Instruction audit

This reusable workflow checks the calling public OpenTelemetry repository's Copilot instructions and can open a draft fix PR. Add a manual caller at `.github/workflows/instruction-audit.yml` in that repository:

```yaml
name: Instruction audit
on:
  workflow_dispatch:
    inputs:
      publish:
        description: Allow one draft PR for a publishable finding
        type: boolean
        default: false
permissions: {}
jobs:
  audit:
    permissions:
      contents: write
      copilot-requests: write
    uses: open-telemetry/shared-workflows/.github/workflows/instruction-audit.yml@<commit-sha>
    with:
      publish: ${{ inputs.publish }}
      otelbot_client_id: ${{ vars.OTELBOT_CLIENT_ID }}
    secrets:
      otelbot_private_key: ${{ secrets.OTELBOT_PRIVATE_KEY }}
```

Anyone with write access to the caller can dispatch it there. Report-only is the default. Set `publish=true` to allow a draft PR. The `otelbot` variable and secret are only needed for publication.

The workflow checks out the caller's default branch once and records the commit in the run summary. It reads `.github/copilot-instructions.md` and `.instructions.md` files under `.github/instructions/`, including nested files. It visits paths in lexical order and list items or prose paragraphs in document order. Headings, front matter, and examples stay available as context. Rules for new or changed code are not applied retroactively.

## What advances the cursor

The `otelbot/instruction-audit-state` branch in the caller repository keeps a JSON file with the last completed rule's path and exact text. If that text no longer matches exactly once, or the file is gone, the next run starts in the next instruction file. At the end of the pass, the run reports completion and clears the cursor. The following dispatch starts at the beginning.

Report-only runs stop at the first finding. With publication enabled, the workflow reports findings it can't fix and keeps going until it can open a draft or reaches the end of the pass. If publishing is blocked or fails, the run fails and leaves that finding pending. Whenever a finding remains pending, the workflow saves progress through the last preceding rule whose text is unique in its file. If the audit job itself fails or times out, it saves no progress. Start a new dispatch instead of rerunning jobs.

## What the workflow can publish

The audit applies the selected rule across the repository, honoring any `applyTo` or exceptions in the rule itself. Report-only runs do not request changes. With publication enabled, Copilot proposes a Git patch for the selected rule. The patch can change multiple parts of a file or multiple files, regardless of location or extension. Git checks the patch against the audited snapshot without modifying the checkout or running target code. Findings without a publishable patch appear in the run summary.

Drafts can trigger the caller's PR workflows. Enable publication only if those workflows can safely handle unreviewed changes from a same-repository branch. GitHub's repository rules and token permissions still apply to the proposed changes.

Draft descriptions link to the audit run and the source of the selected rule, quote the full rule, and then explain the proposed fix under **Explanation**.

The read-only Copilot job cannot use either publication credential. When a proposed fix passes validation and publishing is enabled, the publication job uses the caller's `GITHUB_TOKEN` to create a fix branch from the audited commit. It uses a caller-scoped `otelbot` token with **Pull requests: write** to open the draft, so eligible PR checks can start without the `GITHUB_TOKEN` approval gate. Each run can open at most one draft, and each repository can have up to five open audit PRs, including those marked ready for review. If GitHub denies the `otelbot` token request or the limit is reached, no new fix branch is created.
