# OSSF Scorecard

Reusable GitHub Actions workflow that runs [OpenSSF Scorecard](https://github.com/ossf/scorecard) against the calling repository, publishes the full result to [api.scorecard.dev](https://api.scorecard.dev) (which is what backs the Scorecard badge), and uploads a subset of its checks to the repository's code scanning dashboard.

## How to use

Replace your repository's inline Scorecard workflow (typically `.github/workflows/scorecard.yml`) with:

```yaml
name: OSSF Scorecard

on:
  push:
    branches: [main]
  schedule:
    - cron: '25 4 * * 4'
  workflow_dispatch:

permissions: {}

jobs:
  scorecard:
    permissions:
      contents: read # for actions/checkout
      id-token: write # for Scorecard to publish results
      security-events: write # for the SARIF upload to code scanning
    uses: open-telemetry/shared-workflows/.github/workflows/scorecard.yml@<sha-or-tag>
```

Pin `<sha-or-tag>` to a commit SHA or release tag in this repository. No secrets are required.

The optional Boolean `skip-publication` input defaults to `false`. Set it to
`true` for events such as pull requests that should retain the SARIF artifact
and code scanning upload without publishing the full result to
`api.scorecard.dev`.

The optional `file-mode` input accepts `archive` or `git` and defaults to
`archive`. Add `file-mode: git` when Scorecard must scan files excluded from
repository archives by `.gitattributes` `export-ignore` rules. Git mode
retrieves files from a Git clone instead of an export archive; it does not
change the commit or branch metadata available to checks.

The optional Boolean `use-harden-runner` input defaults to `false`. When it is
`true`, the analysis and upload jobs begin with `step-security/harden-runner`
using `egress-policy: audit`. That audits outbound calls on `ubuntu-latest`.

## What gets filtered

Only these checks are uploaded to code scanning:

- `BinaryArtifactsID`
- `DangerousWorkflowID`
- `PinnedDependenciesID`
- `TokenPermissionsID`

By default, the full result is still published, so the badge and the public `api.scorecard.dev` entry are unaffected. Runs with `skip-publication: true` do not update the badge or public entry.
