# PR review

To opt in, add your repository to the [PR review opt-in list](../.github/scripts/pr-review/repositories.json). Use the repository name without the `open-telemetry/` prefix. The only field is `name`:

```json
{"name": "my-repository"}
```

Dashboard enrollment does not opt a repository into PR review. Once enrolled, run the central review workflow manually with the URL of an open PR:

```sh
gh workflow run pr-review.yml --repo open-telemetry/shared-workflows \
  --ref main -f pr_url=https://github.com/open-telemetry/REPOSITORY/pull/NUMBER
```

GPT-6.1 Sol reviews the diff and looks for worthwhile simplifications in separate passes. Claude Opus 5.5 checks both passes before publication. If validation fails, the workflow sends Opus the error and retries once. Each pass gets an isolated copy of the PR checkout behind the agent firewall. The workflow checks inline suggestions against the original PR head commit. There is no language-specific setup or dependency installation for the target repository.

A separate job posts the validated result as `otelbot[bot]`, using the shared repository's `OTELBOT_CLIENT_ID` variable and `OTELBOT_PRIVATE_KEY` secret. App tokens are scoped to the target repository. The review job requests pull request read access. The posting job requests pull request write access. The model sandbox receives no repository or publishing credentials. Publication stops if the PR closes or either its base or head changes. No findings produces a single top-level comment.

For a branch-only test, replace `main` with the test branch. GitHub must have registered the workflow before it can be dispatched; pushing a manual-only workflow to a non-default branch may not register it. Do not add an automatic live-review trigger to work around that.
