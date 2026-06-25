# Contributing to shared-workflows

Thanks for your interest in improving the OpenTelemetry shared workflows. This repository hosts GitHub Actions workflows that other OpenTelemetry repositories can use without copying any files — workflows execute from this repo.

There are two ways to contribute:

1. **[Propose](#proposing-a-new-shared-workflow)** a new shared workflow.
2. **[Use](#using-a-shared-workflow)** an existing shared workflow in your repo.

## Proposing a new shared workflow

New workflows are welcome, and anyone in the OpenTelemetry community can propose one. Start with an issue describing what the workflow does, which repos would benefit, and how it would be configured. Once there is rough consensus, open a pull request adding the workflow.

### Two shapes of shared workflow

Shared workflows in this repo come in two shapes. Pick the one that fits your use case:

- **Reusable workflow** — a workflow that other repos call directly via [`uses:`](https://docs.github.com/en/actions/using-workflows/reusing-workflows). The workflow runs in the *calling* repository's context. Example: [`zizmor.yml`](./.github/workflows/zizmor.yml).
- **Centrally-executed workflow** — a workflow that runs *from this repo* against an opted-in list of target repositories. The workflow runs in this repository's context and accesses target repos via the GitHub API. Example: [`pull-request-dashboard/`](./pull-request-dashboard/) plus `pull-request-dashboard*.yml`.

> Workflows that this repo runs only for its own checks (such as [`codeql.yml`](./.github/workflows/codeql.yml) and [`scorecard.yml`](./.github/workflows/scorecard.yml)) are **not** shared. They have no companion docs folder.

### Layout

> [GitHub Actions only discovers workflow files directly under `.github/workflows/`](https://docs.github.com/en/actions/writing-workflows/about-workflows). YAML files in subdirectories are silently ignored and will not run. **All workflow YAML must therefore live flat in `.github/workflows/`.**
>
> Each shared workflow gets a companion **docs folder at the repo root** for its consumer-facing `README.md`. If a single logical workflow ships multiple YAML files, group them by **filename prefix** (e.g. `pull-request-dashboard*.yml`).

#### Reusable workflow (`uses:`-style)

```
<workflow-name>/
  README.md                                # consumer docs (at repo root)

.github/workflows/
  <workflow-name>.yml                      # the reusable workflow (must live here)
```

Callers reference the workflow at its real location, e.g.:

```yaml
uses: open-telemetry/shared-workflows/.github/workflows/<workflow-name>.yml@<sha-or-tag>
```

#### Centrally-executed workflow

The consumer-facing `README.md` lives in a root folder named after the workflow. The scripts, workflow YAML files, and per-workflow `repositories.json` opt-in list stay under `.github/` because the workflow executes inside this repository.

```
<workflow-name>/
  README.md                          # consumer docs (at repo root)

.github/
  workflows/
    <workflow-name>.yml              # main YAML (one or more, prefix-grouped)
    <workflow-name>-<other>.yml      # sibling YAMLs for the same workflow
  scripts/
    <workflow-name>/
      repositories.json              # per-workflow opt-in list
      <workflow scripts...>          # flat, no scripts/ subfolder
```

Each centrally-executed workflow has its **own `repositories.json`**, so opting in is per workflow — a repository only appears in the workflows it chose to use.

### The workflow's README

The README at the root of the workflow's folder is the contract between the workflow and the repositories that use it. It must describe:

- **What** the workflow does, in plain language.
- **How a repository uses it.** Document the model that fits the workflow:
  - **Opt-in via `repositories.json`** — list the required and optional fields a repository entry must include, and show an example entry.
  - **Consume via `uses:`** — show the snippet a calling repo adds to its own workflow file, including any inputs or secrets it must pass.

### Code ownership

To be listed as a code owner of a workflow, you must be an **approver or maintainer of an existing OpenTelemetry SIG**. This keeps every shared workflow accountable to an active part of the project. See the [OpenTelemetry membership guide](https://github.com/open-telemetry/community/blob/main/guides/contributor/membership.md) for the definitions of approver and maintainer.

If you are not yet an approver or maintainer, you can still propose and contribute a workflow — you will just need a SIG approver or maintainer to co-own it. In the same pull request, add `CODEOWNERS` entries for all of the workflow's paths. For example, for a centrally-executed workflow:

```
<workflow-name>/**                       @open-telemetry/<sig>-approvers @co-owner
.github/scripts/<workflow-name>/**       @open-telemetry/<sig>-approvers @co-owner
.github/workflows/<workflow-name>.yml    @open-telemetry/<sig>-approvers @co-owner
.github/workflows/<workflow-name>-*.yml  @open-telemetry/<sig>-approvers @co-owner
```

### Adding your workflow to the catalog

When you add a new workflow, also add a row to the workflows table in the [main README](./README.md) so people can find it.

## Using a shared workflow

To use one of the workflows in this repo, find it in the workflows table in the [main README](./README.md) and follow the README in that workflow's root folder. Each workflow's README documents the exact steps — typically either adding your repository to that workflow's `repositories.json` or calling the workflow from your own repo via `uses:`.

## Maintainers and reviews

Pull requests are reviewed by the maintainers listed in the [README](./README.md). All paths currently default to `@open-telemetry/shared-workflows-approvers` via [`CODEOWNERS`](./.github/CODEOWNERS). To add or change ownership of a specific workflow, open a pull request updating `CODEOWNERS` with the workflow's paths and the SIG-affiliated owners.

## Getting help

If you are unsure where to start, open an issue describing what you are trying to do, and a maintainer will help point you in the right direction.
