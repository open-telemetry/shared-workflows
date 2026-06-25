# Contributing to shared-workflows

Thanks for your interest in improving the OpenTelemetry shared workflows. This repository hosts GitHub Actions workflows that other OpenTelemetry repositories can use without copying any files — workflows execute from this repo.

There are two ways to contribute:

1. **[Propose](#proposing-a-new-shared-workflow)** a new shared workflow.
2. **[Use](#using-a-shared-workflow)** an existing shared workflow in your repo.

## Proposing a new shared workflow

New workflows are welcome, and anyone in the OpenTelemetry community can propose one. Start with an issue describing what the workflow does, which repos would benefit, and how it would be configured. Once there is rough consensus, open a pull request adding the workflow.

### Two shapes of shared workflow

Shared workflows in this repo come in two shapes. Pick the one that fits your use case:

- **Reusable workflow** — a workflow that other repos call directly via [`uses:`](https://docs.github.com/en/actions/using-workflows/reusing-workflows). The workflow runs in the *calling* repository's context. Example in this repo: [`zizmor.yml`](./.github/workflows/zizmor.yml).
- **Centrally-executed workflow** — a workflow that runs *from this repo* against an opted-in list of target repositories. The workflow runs in this repository's context and accesses target repos via the GitHub API.

The two shapes differ in how repositories opt in and in what supporting files the workflow needs.

### Layout

#### Reusable workflow (`uses:`-style)

A reusable workflow is usually just a single YAML file at `.github/workflows/<workflow-name>.yml`. It does not need a scripts folder. Document inputs, secrets, and the calling snippet in the workflow's header comment.

#### Centrally-executed workflow

A centrally-executed workflow keeps its supporting files in **its own folder** under [`.github/scripts/<workflow-name>/`](./.github/scripts/). The folder contains the workflow's scripts, its `README.md`, and its `repositories.json` (the per-workflow opt-in list). The actual GitHub Actions YAML files live alongside the other workflows under `.github/workflows/`; a single logical workflow may ship more than one YAML file.

```
.github/
  scripts/
    <workflow-name>/
      README.md                 # what the workflow does and how to use it
      repositories.json         # per-workflow opt-in list
      <workflow scripts...>     # flat, no scripts/ subfolder
  workflows/
    <workflow-name>.yml         # one or more YAML files for the workflow
```

Each centrally-executed workflow has its **own `repositories.json`**, so opting in is per workflow — a repository only appears in the workflows it chose to use.

### The workflow's README

The README inside the workflow's folder (or, for a reusable workflow, the header comment of the YAML file) is the contract between the workflow and the repositories that use it. It must describe:

- **What** the workflow does, in plain language.
- **How a repository uses it.** Document the model that fits the workflow:
  - **Opt-in via `repositories.json`** — list the required and optional fields a repository entry must include, and show an example entry.
  - **Consume via `uses:`** — show the snippet a calling repo adds to its own workflow file, including any inputs or secrets it must pass.

### Code ownership

To be listed as a code owner of a workflow, you must be an **approver or maintainer of an existing OpenTelemetry SIG**. This keeps every shared workflow accountable to an active part of the project. See the [OpenTelemetry membership guide](https://github.com/open-telemetry/community/blob/main/guides/contributor/membership.md) for the definitions of approver and maintainer.

If you are not yet an approver or maintainer, you can still propose and contribute a workflow — you will just need a SIG approver or maintainer to co-own it. In the same pull request, add `CODEOWNERS` entries for the workflow's paths — for a centrally-executed workflow, that means both the scripts folder and each `.yml` file. For example:

```
.github/scripts/<workflow-name>/**     @open-telemetry/<sig>-approvers @co-owner
.github/workflows/<workflow-name>.yml  @open-telemetry/<sig>-approvers @co-owner
```

### Adding your workflow to the catalog

When you add a new workflow, also add a row to the workflows table in the [main README](./README.md) so people can find it.

## Using a shared workflow

To use one of the workflows in this repo, find it in the workflows table in the [main README](./README.md) and follow its documentation:

- For a **reusable workflow**, follow the header comment of its YAML file in [`.github/workflows/`](./.github/workflows/) — typically you add a small `uses:` snippet to a workflow file in your own repo.
- For a **centrally-executed workflow**, follow the README in its folder under [`.github/scripts/`](./.github/scripts/) — typically you add an entry for your repository to that workflow's `repositories.json`.

## Maintainers and reviews

Pull requests are reviewed by the maintainers listed in the [README](./README.md). All paths currently default to `@open-telemetry/shared-workflows-approvers` via [`CODEOWNERS`](./.github/CODEOWNERS). To add or change ownership of a specific workflow, open a pull request updating `CODEOWNERS` with the workflow's paths and the SIG-affiliated owners.

## Getting help

If you are unsure where to start, open an issue describing what you are trying to do, and a maintainer will help point you in the right direction.
