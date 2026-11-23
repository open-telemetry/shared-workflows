# PR review

This proof of concept runs only on manual dispatch by someone with write access to `open-telemetry/shared-workflows`. Pass the URL of an open PR in a public OpenTelemetry repository where `otelbot` is installed. No repository enrollment is required.

```sh
gh workflow run pr-review.yml --repo open-telemetry/shared-workflows \
  --ref main -f pr_url=https://github.com/open-telemetry/REPOSITORY/pull/NUMBER
```

GPT-6.1 Sol reviews the diff and looks for worthwhile simplifications in separate passes. Claude Opus 5.5 checks both passes before publication. If validation fails, the workflow sends Opus the error and retries once. Each pass gets an isolated copy of the PR checkout behind the agent firewall. The workflow checks inline suggestions against the original PR head commit. There is no language-specific setup or dependency installation for the target repository.

A separate job posts the validated result as `otelbot[bot]`, using the shared repository's `OTELBOT_CLIENT_ID` variable and `OTELBOT_PRIVATE_KEY` secret. App tokens are scoped to the target repository. The review job requests pull request read access. The posting job requests pull request write access. The model sandbox receives no repository or publishing credentials. Publication stops if the PR closes or either its base or head changes. No findings produces a single top-level comment.

Open the workflow run's **Summary** or the **Report Copilot token usage** step log to see input, output, cache read and cache write tokens for each pass, including verification retries. The report also shows estimated AI credits when the firewall records them. Use GitHub's billing reports for actual charges.

When a pass ends, the **Review, simplify, and double-check** step logs its duration, outcome, last recorded Copilot event, and the slowest tool calls with recorded timings. It also notes tool calls with no recorded finish. The activity summary excludes prompts, command arguments, tool output, and draft findings. Canceling a run can interrupt the active pass before its summary is printed.

For a branch-only test, replace `main` with the test branch. GitHub must have registered the workflow before it can be dispatched; pushing a manual-only workflow to a non-default branch may not register it. Do not add an automatic live-review trigger to work around that.
