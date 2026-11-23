# Instruction audit

This reusable workflow manually checks one rule from the calling public OpenTelemetry repository's Copilot instructions. Add a caller at `.github/workflows/instruction-audit.yml` in that repository:

```yaml
name: Instruction audit
on:
  workflow_dispatch:
    inputs:
      publish:
        description: Allow one draft PR for a verified finding
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

A clean check or a rule that can't be checked retroactively advances the cursor in either mode. A finding only advances it after a draft opens. Report-only findings, publication blocked by an existing audit draft, and infrastructure or validation failures leave the cursor in place. Start a new dispatch instead of rerunning jobs.

## What the workflow can publish

The audit can report findings in any in-scope file. It can replace one prose line or delete one redundant fenced YAML example and its lead-in in an existing non-hidden Markdown file. A separate model pass verifies the proposed edit before publication. The workflow never runs target code or applies a model-supplied patch. Other findings appear in the run summary for manual handling.

The read-only Copilot job cannot use either publication credential. When there is a verified fix and publishing is enabled, the publication job uses the caller's `GITHUB_TOKEN` to create a fix branch from the audited commit. It uses a caller-scoped `otelbot` token with **Pull requests: write** to open the draft, so eligible PR checks can start without the `GITHUB_TOKEN` approval gate. If GitHub denies the `otelbot` token request or an audit PR is already open, no new fix branch is created.
