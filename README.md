# OpenTelemetry Shared Workflows

This repository contains reusable GitHub Actions components shared across multiple OpenTelemetry repositories.

See [`CONTRIBUTING.md`](./CONTRIBUTING.md) for how to propose a new shared workflow or use an existing one.

## Workflows

| Name | Description | How to use |
| ---- | ----------- | ---------- |
| [Pull Request Dashboard](./.github/scripts/pull-request-dashboard/) | Centrally-executed workflow that builds a per-repository pull request triage dashboard (issue body, status, Slack notifications) for opted-in repositories. | Add your repository to [`repositories.json`](./.github/scripts/pull-request-dashboard/repositories.json) and follow the setup in the [workflow's README](./.github/scripts/pull-request-dashboard/README.md). |
| [Zizmor](./.github/workflows/zizmor.yml) | Static analysis of GitHub Actions workflows for security issues using [zizmor](https://github.com/zizmorcore/zizmor). | Call via `uses:` from your repo's workflow. See the header comment in [`zizmor.yml`](./.github/workflows/zizmor.yml) for the snippet. |

## Maintainers

- [Adriel Perkins](https://github.com/adrielp), Grainger
- [Marylia Gutierrez](https://github.com/maryliag), Grafana Labs
- [Pablo Baeyens](https://github.com/mx-psi), Datadog
- [Trask Stalnaker](https://github.com/trask), Microsoft

For more information about the maintainer role, see the [community repository](https://github.com/open-telemetry/community/blob/main/guides/contributor/membership.md#maintainer).
