# Instruction audit

This reusable workflow checks the calling repository's Copilot instructions and can open a fix PR ready for review. Add a manual caller at `.github/workflows/instruction-audit.yml` in that repository:

```yaml
name: Instruction audit
on:
  workflow_dispatch:
    inputs:
      publish:
        description: Allow one ready-for-review PR for a publishable finding
        type: boolean
        default: false
      continuation:
        description: Internal use; leave blank when starting an audit
        type: string
        default: ''
permissions: {}
jobs:
  audit:
    permissions:
      actions: write
      contents: write
      copilot-requests: write
    uses: open-telemetry/shared-workflows/.github/workflows/instruction-audit.yml@<commit-sha>
    with:
      publish: ${{ inputs.publish }}
      continuation: ${{ inputs.continuation }}
      otelbot_client_id: ${{ vars.OTELBOT_CLIENT_ID }}
    secrets:
      otelbot_private_key: ${{ secrets.OTELBOT_PRIVATE_KEY }}
```

Anyone with write access to the caller can dispatch it there. Report-only is the default. Set `publish=true` to allow a ready-for-review PR. For publication, supply credentials for a GitHub App installed on the caller repository. The inputs are named `otelbot_*`, but any App with the required permissions works.

The first run resolves the caller's default-branch commit with the caller's read token. Every run in the pass checks that same commit, even if the default branch moves. The token isn't saved in the checkout. For private repositories, enable the workflow only where the repository's source is approved for Copilot use.

The audit reads `.github/copilot-instructions.md` and `.instructions.md` files under `.github/instructions/`, including nested files. It visits paths in lexical order and list items or prose paragraphs in document order. Headings, front matter, and examples stay available as context. Rules for new or changed code are not applied retroactively.

## What advances the cursor

The `otelbot/instruction-audit-state` branch in the caller repository keeps a JSON file with the last completed rule's path and exact text. If that text no longer matches exactly once, or the file is gone, the next run starts in the next instruction file. At the end of the pass, the run reports completion and clears the cursor. The following dispatch starts at the beginning.

Each run checks one rule with a fresh GHAW agent. A separate job uses the caller's `GITHUB_TOKEN` with `actions: write` to dispatch the next rule on the same workflow branch. Report-only passes stop after the first rule with a verified violation. With publication enabled, the workflow reports findings it can't fix and keeps going until it opens a PR or reaches the end of the pass. If publishing is blocked or fails, the run fails and leaves that finding pending. Whenever a finding remains pending, the workflow saves progress through the last preceding rule whose text is unique in its file. An audit failure or timeout stops the chain without saving progress. Start a new dispatch instead of rerunning jobs.

## What the workflow can publish

The audit asks Copilot to search the selected rule's applicable scope, keep looking after the first verified violation, and report what it checked and any coverage limits. In publish mode, Copilot proposes one Git patch for all verified violations it can safely fix and explains any it leaves unfixed. Git checks the patch against the original snapshot in a separate job. Findings without a publishable patch appear in the run summary.

GitHub Agentic Workflows runs Copilot in the firewall with shell access. Network access includes `defaults`, `github`, `opentelemetry.io`, and the ecosystem bundles for OpenTelemetry projects: `bazel` for C++, `dart`, `dotnet`, `elixir`, `go`, `java`, `kotlin`, `node`, `php`, `python`, `ruby`, `rust`, and `swift`.

Audit PRs open ready for review and can trigger the caller's PR workflows. Enable publication only if those workflows can safely handle unreviewed changes from a same-repository branch. GitHub's repository rules and token permissions still apply to the proposed changes.

PR titles start with `Instruction audit:` and describe what the proposed patch changes. PR descriptions link to the audit run and the source of the selected rule, quote the full rule, and explain what changes and why under **Explanation**. Straightforward fixes get a brief explanation; more complex fixes can include the context reviewers need. Search details and coverage limits stay in the run summary.

Copilot cannot access the checkout or publication credentials. When a proposed fix passes validation and publishing is enabled, the publication job uses the caller's `GITHUB_TOKEN` to create a fix branch from the audited commit. It uses a caller-scoped GitHub App token with **Pull requests: write** to count existing audit PRs and open the PR, so eligible PR checks can start without the `GITHUB_TOKEN` approval gate. Each run can open at most one PR, and each repository can have up to five open audit PRs, whether draft or ready for review. If GitHub denies the app token request or the limit is reached, no new fix branch is created.

After editing `.github/workflows/instruction-audit-worker.md`, run `gh aw compile instruction-audit-worker` with GHAW v1.2.2 and commit the generated lockfile.
