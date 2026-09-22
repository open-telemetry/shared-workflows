# Changelog

Reusable GitHub Actions workflow that enforces the [chloggen](https://github.com/open-telemetry/opentelemetry-collector/tree/main/cmd/chloggen)-style changelog process on pull requests: `CHANGELOG*.md` files must not be edited directly, a `.chloggen/*.yaml` entry must be added instead, that entry must pass `make chlog-validate`, and any links in its rendered preview are checked with [lychee](https://github.com/lycheeverse/lychee).

## How to use

Replace your repository's inline changelog workflow (typically `.github/workflows/changelog.yml`) with something like:

```yaml
name: Changelog

on:
  pull_request:
    types: [opened, synchronize, reopened, labeled, unlabeled]
    branches:
      - main

concurrency:
  group: ${{ github.workflow }}-${{ github.head_ref }}
  cancel-in-progress: true

permissions: {}

jobs:
  changelog:
    permissions:
      contents: read
      pull-requests: read
    uses: open-telemetry/shared-workflows/.github/workflows/chloggen.yml@<sha-or-tag>
```

Pin `<sha-or-tag>` to a commit SHA or release tag in this repository. No secrets are required.

Keep your repository's own `on:` trigger, `concurrency:`, and `permissions:` blocks — those stay in the calling workflow, since a reusable workflow cannot define when it runs.

### Requirements

Your repository must have:

- A `.chloggen/` directory with a config the [chloggen](https://github.com/open-telemetry/opentelemetry-collector/tree/main/cmd/chloggen) tool understands.
- `make chlog-validate` and `make chlog-preview` targets that invoke chloggen.
- A `.github/lychee.toml` for the link-check step.

### Inputs

- `go-version` is passed straight to `actions/setup-go` and defaults to `oldstable`. Set it if your repository's `chloggen` build needs a specific Go version.
- `skip-title-marker` is the substring in the pull request title that skips changelog enforcement, and defaults to `[chore]`. Set it to an empty string to disable title-based skipping entirely.
- `skip-labels` is a comma-separated list of pull request labels that skip changelog enforcement, and defaults to `dependencies,Skip Changelog`. Set it to an empty string to disable label-based skipping entirely.
- `skip-actors` is a comma-separated list of actors for which the entire job is skipped, and defaults to `dependabot[bot],renovate[bot]`. Set it to an empty string to disable actor-based skipping entirely.

Example overriding every input:

```yaml
jobs:
  changelog:
    permissions:
      contents: read
      pull-requests: read
    uses: open-telemetry/shared-workflows/.github/workflows/chloggen.yml@<sha-or-tag>
    with:
      go-version: '1.23'
      skip-title-marker: '[skip changelog]'
      skip-labels: 'dependencies,Skip Changelog,internal'
      skip-actors: 'dependabot[bot],renovate[bot],otelbot[bot]'
```

## Skipping the check

A pull request skips every check in this workflow (other than the job itself running) when either of these is true:

- It has one of the labels in the `skip-labels` input (`dependencies` or `Skip Changelog` by default).
- Its title contains the `skip-title-marker` input's value (`[chore]` by default).

The job itself is always skipped when the triggering actor is in the `skip-actors` input (`dependabot[bot]` or `renovate[bot]` by default).
