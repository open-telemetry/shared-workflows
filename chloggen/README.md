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
    uses: open-telemetry/shared-workflows/.github/workflows/chloggen.yml@<sha-or-tag>
```

Pin `<sha-or-tag>` to a commit SHA or release tag in this repository. No secrets are required.

Keep your repository's own `on:` trigger, `concurrency:`, and `permissions:` blocks — those stay in the calling workflow, since a reusable workflow cannot define when it runs.

Your repository must have:

- A `.chloggen/` directory with a config the [chloggen](https://github.com/open-telemetry/opentelemetry-collector/tree/main/cmd/chloggen) tool understands.
- `make chlog-validate` and `make chlog-preview` targets that invoke chloggen.
- A `.github/lychee.toml` for the link-check step.

### Inputs

The optional `go-version` input is passed straight to `actions/setup-go` and defaults to `oldstable`. Set it if your repository's `chloggen` build needs a specific Go version.

## Skipping the check

A pull request skips every check in this workflow (other than the job itself running) when any of these are true:

- It has the `dependencies` label.
- It has the `Skip Changelog` label.
- Its title contains `[chore]`.

The job itself is always skipped for pull requests opened by `dependabot[bot]`.
