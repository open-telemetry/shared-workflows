> [!NOTE]
> Open non-draft PRs grouped by who is expected to act next. Draft PRs are listed separately. The grouping is partly performed by an LLM ([source](https://github.com/open-telemetry/shared-workflows/blob/main/.github/scripts/pull-request-dashboard/dashboard.py)) and could contain mistakes.
>
> Reviewers column: ✅ approved · ✔️ approved (non-code-owner) · 💬 open thread · 🔴 changes requested.

## Waiting on reviewers

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [ci: Use doctoc to manage toc (#816)](https://github.com/open-telemetry/opentelemetry-proto/pull/816) | thompson-tomo |  | ✅ | ✅ | 9d |

## Waiting on authors

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [Support grpc-retry-pushback-ms (#664)](https://github.com/open-telemetry/opentelemetry-proto/pull/664) | DylanRussell | bogdandrutu&nbsp;💬<br>MrAlias&nbsp;💬<br>pellared<br>tigrannajaryan&nbsp;💬 | ✅ | ✅ | 316d |
| [Add support for SELinux Systems. (#340)](https://github.com/open-telemetry/opentelemetry-proto/pull/340) | hdost | bogdandrutu<br>marcalff&nbsp;✅<br>tigrannajaryan&nbsp;✅ | ? | ❌ | 99d |
| [\[DO NOT MERGE\] profiles: more efficient and flexible original_payload handling. (#786)](https://github.com/open-telemetry/opentelemetry-proto/pull/786) | jhalliday | aalexand<br>felixge&nbsp;💬<br>florianl&nbsp;💬 | ✅ | ❌ | 73d |

## Draft pull requests

| PR | Author | Updated |
|---|---|:---:|
| [Add EntityRef ID context type (#810)](https://github.com/open-telemetry/opentelemetry-proto/pull/810) | dmitryax | 24d |
| [\[chore\] Makefile: Enable breaking change check for profiles (#774)](https://github.com/open-telemetry/opentelemetry-proto/pull/774) | florianl | 6d |

<details>
<summary>Diagnostics</summary>

```text
PR #786
llm: PRRT_kwDOCzPJrc58JGxE -> author (A reviewer raised a requested change about dropping the field, and there’s no author reply resolving it yet.)
llm: PRRT_kwDOCzPJrc58JH7r -> author (A reviewer raised whether the attributes should be required, and the latest reviewer comment gives a recommendation but still leaves the PR decision to the author.)

PR #664
llm: PRRT_kwDOCzPJrc5WK4OD -> none (The reviewer asked a question about preference; the author answered it directly and did not ask for any further change, so the discussion appears complete.)
llm: PRRT_kwDOCzPJrc5YDSEA -> author (The latest comment is a reviewer question asking for clarification, so the PR author needs to जवाब/clarify the spec.)
llm: PRRT_kwDOCzPJrc5YDTuW -> reviewer (The reviewer asked about compatibility, and the author replied with an explanation and a proposed alternative, so the ball is back with the reviewer to confirm or respond.)
llm: PRRT_kwDOCzPJrc5WK3wd -> author (The latest comment is from a reviewer/approver asking about the link choice, so the author needs to जवाब/adjust the PR.)

PR #340
llm: pr-conversation -> author (The latest approver comment says the PR now has merge conflicts and they cannot verify it, so the PR author needs to resolve the conflicts and update the thread.)

```

</details>

_Approvers may [force a refresh](https://github.com/open-telemetry/shared-workflows/actions/workflows/pull-request-dashboard.yml)._

