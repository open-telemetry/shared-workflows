# First-time contributor

Reusable GitHub Actions workflow that welcomes first-time contributors when they open a pull request. On the first `pull_request_target: opened` event, it:

1. Posts a welcome comment with contributing guidelines, CLA reminder, and etiquette links. The greeting name and CONTRIBUTING link auto-substitute the caller's repository, so a repo can adopt this workflow with a single `uses:` reference and no further config.
2. Optionally applies a label to the PR — only when the caller passes a `label` value. By default, no label is added.

Repo-specific content (SIG channels, priority-component lists, etc.) is supplied via the optional `custom_message` input.

## How to use

Add a caller workflow to your repository, for example `.github/workflows/first-time-pr.yml`:

```yaml
name: First time contributor

on:
  pull_request_target:
    types: [opened]

jobs:
  welcome:
    uses: open-telemetry/shared-workflows/.github/workflows/first-time-pr.yml@<sha-or-tag>
```

Pin `<sha-or-tag>` to a commit SHA or release tag in this repository. No secrets are required — the workflow uses the built-in `GITHUB_TOKEN` and posts as `github-actions[bot]`.

## Inputs

| Input | Required | Default | Description |
| --- | --- | --- | --- |
| `custom_message` | no | `''` | Markdown appended to the end of the default welcome comment. Use for repo-specific content: SIG Slack channels, priority-component lists, extra reminders, etc. |
| `label` | no | `''` | Single label to apply to the PR. May contain spaces. **If omitted (empty), the label step is skipped entirely and no label is added.** Pass e.g. `'first-time contributor'` to opt in. The label must already exist in your repository. If adding the label fails (typo, missing label, etc.), the labeling step is marked failed but the welcome comment is still posted. |

## Default comment body

The default comment is:

> Welcome, contributor! Thank you for your contribution to `<repo-name>`.
>
> **Important reminders:**
>
> - Read our [Contributing Guidelines](https://github.com/`<owner/repo>`/blob/main/CONTRIBUTING.md).
> - Sign the [CLA](https://identity.linuxfoundation.org/projects/cncf) if you haven't already.
> - Follow the OpenTelemetry [GenAI policy](https://github.com/open-telemetry/community/blob/main/policies/genai.md): disclose any AI use in your contribution, and communicate (PR descriptions, review replies) in your own words rather than AI-generated text.
> - Give reviewers at least a few days before pinging them for feedback.
> - If you need help with general setup, development process, or contributor etiquette, ask in [#opentelemetry-new-contributors](https://cloud-native.slack.com/archives/C09H3MNMBQV).

`<repo-name>` and `<owner/repo>` come from the caller's `github.event.repository.name` and `github.repository`.

## Example with a custom tail and a label

For a repo that wants to add a label and point contributors at a priority-component list and a SIG channel:

```yaml
jobs:
  welcome:
    uses: open-telemetry/shared-workflows/.github/workflows/first-time-pr.yml@<sha-or-tag>
    with:
      label: 'first-time contributor'
      custom_message: |
        - If your change isn't one of our [priority components](https://github.com/open-telemetry/opentelemetry-collector-contrib/issues/44130), reviews may take more time.
        - Raise technical or Collector-specific questions in [#otel-collector-dev](https://cloud-native.slack.com/archives/C07CCCMRXBK) or a [Collector SIG meeting](https://github.com/open-telemetry/community?tab=readme-ov-file#sig-collector).
```

## When it fires

The workflow only runs when **all** of the following are true:

- The event is `pull_request_target: opened` (draft-to-ready transitions do not re-trigger).
- The base repository is under `open-telemetry` (safety guard against `pull_request_target`'s elevated privileges on forks).
- The PR author's [`author_association`](https://docs.github.com/en/webhooks/webhook-events-and-payloads#pull_request) is `NONE` — i.e., they have no prior association with the repo or the org.
