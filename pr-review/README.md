# PR review

Run Copilot on demand to look for problems and ways to simplify a pull request. The target repository doesn't need its own workflow or an opt-in entry.

## Run a review

You need write access to `open-telemetry/shared-workflows` and the URL of an open PR in a public OpenTelemetry repository where `otelbot` is installed.

Open the [PR review workflow](https://github.com/open-telemetry/shared-workflows/actions/workflows/pr-review.yml), select **Run workflow**, and enter the PR URL. Or use the GitHub CLI:

```sh
gh workflow run pr-review.yml --repo open-telemetry/shared-workflows \
  -f pr_url=https://github.com/open-telemetry/REPOSITORY/pull/NUMBER
```

## Check the result

`otelbot[bot]` posts findings on the PR, or a **No findings** comment. Both start with a note that the review is experimental. If the PR closes or its base or head changes during the run, the workflow skips posting.

The **Review, simplify, and double-check** step shows tool activity and each pass's final answer, including any verification retry. Compare the review and simplification answers with the verification answer to see which findings made it through. The log redacts known credential patterns and omits prompts, intermediate messages, and tool output.

The **Report Copilot token usage** step breaks down input, output, and cache tokens per model request when available. The run's **Summary** totals token usage and estimated AI credits by pass. Use GitHub billing reports for actual charges.
