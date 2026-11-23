# Instruction audit

This reusable workflow checks the calling public OpenTelemetry repository's Copilot instructions until the first finding or the end of the pass. Add a manual caller at `.github/workflows/instruction-audit.yml` in that repository:

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

The workflow continues past clean and non-auditable rules. At a finding, it opens a draft only if publication is enabled and the proposed fix passes validation. Otherwise, it saves the last preceding clean or non-auditable rule that has unique text in its file, so the next dispatch can retry the finding. If the audit job itself fails or times out, it saves no progress. Start a new dispatch instead of rerunning jobs.

## What the workflow can publish

The audit can report findings in any in-scope file. Report-only runs do not request edits. With publication enabled, Copilot can propose replacing or deleting one small span in an existing non-hidden Markdown file. The workflow checks that the file is in scope and that the original text occurs exactly once in the snapshot. It never runs target code. Findings without a publishable edit appear in the run summary for manual handling.

Draft descriptions link to the audit run and the source of the selected rule, quote the full rule, and then explain the proposed fix under **Explanation**.

The read-only Copilot job cannot use either publication credential. When a proposed fix passes validation and publishing is enabled, the publication job uses the caller's `GITHUB_TOKEN` to create a fix branch from the audited commit. It uses a caller-scoped `otelbot` token with **Pull requests: write** to open the draft, so eligible PR checks can start without the `GITHUB_TOKEN` approval gate. Each run can open at most one draft, and each repository can have up to five open audit PRs, including those marked ready for review. If GitHub denies the `otelbot` token request or the limit is reached, no new fix branch is created.
