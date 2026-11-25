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

`otelbot[bot]` posts findings as inline review comments, with only an experimental-review disclaimer in the review body. Review summaries stay in the workflow logs. If there's nothing worth posting, it posts a **No findings** comment with the same disclaimer. If the PR closes or its base or head changes during the run, the workflow skips posting.

Each inline comment starts with a **Finding**, **Simplification**, or **Question** line that you can read on its own. A **Why** section follows with the supporting evidence, including permalinks to any code outside the diff. Comments leave the choice of fix to the author, except for small GitHub suggestions that you can apply directly. When a comment includes a suggestion, **Why** also says why the patch is safe and lists any edits it needs elsewhere.

The verifier drops unsupported findings. It posts a **Question** without a suggested patch only when the author can supply a missing fact that could change the code.

The workflow reviews the PR head and diff. All passes get a separate, read-only checkout of the target branch tip so they can find applicable review instructions and follow links to other repository docs. PR changes to instruction files don't change the rules for their own review.

The review sandbox allows the instruction audit's default, GitHub, and language-ecosystem network destinations, plus `opentelemetry.io`, so agents can download build tools and dependencies. Other destinations remain blocked, and publishing credentials remain outside the sandbox.

The **Review, simplify, and double-check** step shows tool activity and each pass's final answer, including any verification retry. Compare the review and simplification answers with the verification answer to see which findings made it through. The log redacts known credential patterns and omits prompts, intermediate messages, and tool output.

If the verifier's output is malformed or ambiguous, the workflow asks it to fix the format without reviewing the PR again.
If a comment doesn't end on a changed line, the workflow tries to place it on one in the same file without carrying over the suggested patch. Unrepairable comments are omitted. If none can be posted, the run fails rather than posting **No findings**.

The **Report Copilot token usage** step breaks down input, output, and cache tokens per model request when available. The run's **Summary** totals token usage and estimated AI credits by pass. Use GitHub billing reports for actual charges.
