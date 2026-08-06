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

Pin `<sha-or-tag>` to a commit SHA or release tag in this repository. No inputs or secrets are required.

## What gets filtered

Only these checks are uploaded to code scanning:

- `BinaryArtifactsID`
- `DangerousWorkflowID`
- `PinnedDependenciesID`
- `TokenPermissionsID`

The full result is still published, so the badge and the public `api.scorecard.dev` entry are unaffected.
