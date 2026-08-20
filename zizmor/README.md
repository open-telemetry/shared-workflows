# Zizmor

Reusable GitHub Actions workflow that runs [zizmor](https://github.com/zizmorcore/zizmor) against the calling repository's GitHub Actions workflows and uploads the SARIF results to the repository's code scanning dashboard.

## How to use

Add a workflow file to your repository (for example `.github/workflows/zizmor.yml`):

```yaml
name: Zizmor

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
  schedule:
    - cron: '45 9 * * 5'
  workflow_dispatch:

permissions: {}

jobs:
  zizmor:
    permissions:
      contents: read # for actions/checkout
      security-events: write # for zizmor to upload SARIF results
    uses: open-telemetry/shared-workflows/.github/workflows/zizmor.yml@<sha-or-tag>
    with:
      use-cncf-hosted-runner: true
```

Pin `<sha-or-tag>` to a commit SHA or release tag in this repository. No secrets are required. The workflow uses zizmor's `regular` persona by default.

The optional Boolean `use-cncf-hosted-runner` input defaults to `false`. When it is `true`, the Zizmor job runs on `cncf-ubuntu-2-8-x86`; when it is `false` or omitted, the job runs on `ubuntu-latest`. Runs triggered directly in this repository use `ubuntu-latest`. The mapping is fixed; callers cannot provide another runner label.

To use a different persona, pass the `persona` input:

```yaml
jobs:
  zizmor:
    permissions:
      contents: read # for actions/checkout
      security-events: write # for zizmor to upload SARIF results
    uses: open-telemetry/shared-workflows/.github/workflows/zizmor.yml@<sha-or-tag>
    with:
      persona: pedantic
```
