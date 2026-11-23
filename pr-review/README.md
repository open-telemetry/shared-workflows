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

`otelbot[bot]` posts findings on the PR, or a **No findings** comment. If the PR closes or its base or head changes during the run, the workflow skips posting.

The run's **Summary** shows token usage and estimated AI credits when available. Use GitHub billing reports for actual charges. To investigate a slow run, open the **Review, simplify, and double-check** step log. It shows tool activity and timings after each pass, not live.
