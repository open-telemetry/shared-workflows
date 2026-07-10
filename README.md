# OpenTelemetry Shared Workflows

This repository contains reusable GitHub Actions components shared across multiple OpenTelemetry repositories.

See [`CONTRIBUTING.md`](./CONTRIBUTING.md) for how to propose a new shared workflow or use an existing one.

## Workflows

| Name | Description | How to use |
| ---- | ----------- | ---------- |
| [First-time contributor](./first-time-pr/) | Reusable workflow that welcomes first-time contributors on `pull_request_target: opened`: applies a label and posts a customizable welcome comment. | Call via `uses:` from your repo's `pull_request_target` workflow. See the [First-time contributor README](./first-time-pr/README.md) for the snippet. |
| [Pull Request Dashboard](./pull-request-dashboard/) | Centrally-executed workflow that builds a per-repository pull request triage dashboard (issue body, status, Slack notifications) for opted-in repositories. | Add your repository to [`repositories.json`](./.github/scripts/pull-request-dashboard/repositories.json) and follow the setup in the [workflow's README](./pull-request-dashboard/README.md). |
| [Survey on merged PR](./survey-on-merged-pr/) | Reusable workflow that posts a survey link to a merged PR when the author is a new contributor. | Call via `uses:` from your repo's `pull_request_target: closed` workflow. See the [Survey on merged PR README](./survey-on-merged-pr/README.md) for the snippet. |
| [Workflow failure issue](./workflow-failure-issue/) | Reusable workflow that tracks a workflow's pass/fail state by opening, commenting on, and closing a GitHub issue in the calling repository — useful for scheduled workflows whose failure notifications otherwise reach only a single user. | Call via `uses:` from a final `if: always()` job in the workflow you want to monitor. See the [Workflow failure issue README](./workflow-failure-issue/README.md) for the snippet. |
| [Zizmor](./zizmor/) | Static analysis of GitHub Actions workflows for security issues using [zizmor](https://github.com/zizmorcore/zizmor). | Call via `uses:` from your repo's workflow. See the [Zizmor README](./zizmor/README.md) for the snippet. |

## Maintainers

- [Adriel Perkins](https://github.com/adrielp), Grainger
- [Marylia Gutierrez](https://github.com/maryliag), Grafana Labs
- [Pablo Baeyens](https://github.com/mx-psi), Datadog
- [Trask Stalnaker](https://github.com/trask), Microsoft

For more information about the maintainer role, see the [community repository](https://github.com/open-telemetry/community/blob/main/guides/contributor/membership.md#maintainer).
