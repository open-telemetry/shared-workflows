# Instruction audit

Maintainers with write access to `shared-workflows` can manually check one rule from a public OpenTelemetry repository's own Copilot instructions. No target workflow or configuration is needed.

Run `Instruction audit` in `shared-workflows`. Enter the repository name without `open-telemetry/`. Report-only is the default. To allow one draft PR, set `publish=true`.

```shell
gh workflow run instruction-audit.yml \
  --repo open-telemetry/shared-workflows \
  --ref trask-instruction-audit-workflow \
  -f repository=opentelemetry-java-instrumentation -f publish=true
```

The workflow checks out the target's default branch once and records the commit in the run summary. It reads `.github/copilot-instructions.md` and `.instructions.md` files under `.github/instructions/`, including nested files. It visits paths in lexical order and list items or prose paragraphs in document order. Headings, front matter, and examples stay available as context. Rules for new or changed code are not applied retroactively.

## What advances the cursor

The `otelbot/instruction-audit-state` branch in `shared-workflows` keeps one JSON file per target with the last completed rule's path and exact text. If that text no longer matches exactly once, or the file is gone, the next run starts in the next instruction file. At the end of the pass, the run reports completion and clears the cursor. The following dispatch starts at the beginning.

A clean check or a rule that can't be checked retroactively advances the cursor in either mode. A finding only advances it after a draft opens. Report-only findings, publication blocked by an existing audit draft, and infrastructure or validation failures leave the cursor in place. Start a new dispatch instead of rerunning jobs.

## What the workflow can publish

The audit can report findings in any in-scope file. It can replace one prose line or delete one redundant fenced YAML example and its lead-in in an existing non-hidden Markdown file. A separate model pass verifies the proposed edit before publication. The workflow never runs target code or applies a model-supplied patch. Other findings appear in the run summary for manual handling.

The read-only Copilot job cannot use the publication token. When publication is enabled and there is a verified fix, a separate job requests a token scoped to the target repository from the `otelbot` installation. The installation needs **Contents: write** to create the branch and **Pull requests: write** to open the draft. If GitHub denies the token request, the workflow leaves the cursor unchanged and does not create a branch. It does not open another audit draft while one is already open for that repository.
