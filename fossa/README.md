# FOSSA scanning

Reusable GitHub Actions workflow that runs a [FOSSA](https://fossa.com/) license and dependency scan against the calling repository, reporting under the `OpenTelemetry` FOSSA team.

## How to use

Add a workflow file to your repository (for example `.github/workflows/fossa.yml`):

```yaml
name: FOSSA scanning

on:
  push:
    branches: [main]

permissions: {}

jobs:
  fossa:
    permissions:
      contents: read # for actions/checkout
    uses: open-telemetry/shared-workflows/.github/workflows/fossa.yml@<sha-or-tag>
    secrets:
      FOSSA_API_KEY: ${{ secrets.FOSSA_API_KEY }}
```

Pin `<sha-or-tag>` to a commit SHA or release tag in this repository. The workflow only runs when `github.repository_owner == 'open-telemetry'`, so forks won't fail on a missing secret.

### Secrets

| Name | Required | Description |
| ---- | -------- | ----------- |
| `FOSSA_API_KEY` | yes | API key used to authenticate with FOSSA. |
