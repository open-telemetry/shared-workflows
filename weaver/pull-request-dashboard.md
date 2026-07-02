> [!NOTE]
> Open non-draft PRs grouped by who is expected to act next. Draft PRs are listed separately. The grouping is partly performed by an LLM ([source](https://github.com/open-telemetry/shared-workflows/blob/main/.github/scripts/pull-request-dashboard/dashboard.py)) and could contain mistakes.
>
> Reviewers column: ✅ approved · ✔️ approved (non-code-owner) · 💬 open thread · 🔴 changes requested.

## Waiting on maintainers

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [fix: default enum member value to id (#1444)](https://github.com/open-telemetry/weaver/pull/1444) | nanookclaw | jsuereth&nbsp;✅ | ✅ | ✅ | 7d |
| [Add optional `name` field to `SpanRefinement` (#1403)](https://github.com/open-telemetry/weaver/pull/1403) | lmolkova | Copilot<br>jsuereth&nbsp;✅<br>lmolkova&nbsp;✔️ | ❌ | ✅ | 23h |

## Waiting on reviewers

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [#850 Scope don't need attributes (#853)](https://github.com/open-telemetry/weaver/pull/853) | thompson-tomo | jsuereth | ✅ | ✅ | 345d |
| [Proposal: Define namespaces in yaml (#867)](https://github.com/open-telemetry/weaver/pull/867) | thompson-tomo | jerbly&nbsp;💬 | ✅ | ✅ | 333d |
| [Proposal: Offer packages (#872)](https://github.com/open-telemetry/weaver/pull/872) | thompson-tomo | jsuereth | ✅ | ✅ | 318d |
| [The aggregation params of metrics can be defined #844 (#845)](https://github.com/open-telemetry/weaver/pull/845) | thompson-tomo | jerbly<br>jsuereth<br>lmolkova | ✅ | ❌ | 315d |
| [Replace regex with semver crate (#1108)](https://github.com/open-telemetry/weaver/pull/1108) | ArthurSens | jsuereth<br>lquerel&nbsp;🔴 | ✅ | ✅ | 180d |
| [Tolerate unknown properties in resolved schema / published manifest (#1365)](https://github.com/open-telemetry/weaver/pull/1365) | lmolkova | jsuereth&nbsp;💬 | ✅ | ❌ | 55d |
| [Use semantic conventions v2 for `weaver registry infer` (#1334)](https://github.com/open-telemetry/weaver/pull/1334) | ArthurSens | jsuereth | ✅ | ✅ | 23d |
| [Update Rust crate clap_complete to v4.6.7 (#1554)](https://github.com/open-telemetry/weaver/pull/1554) | app/renovate |  | ✅ | ✅ | 12h |
| [Update Rust to v1.96.1 (#1555)](https://github.com/open-telemetry/weaver/pull/1555) | app/renovate |  | ✅ | ✅ | 12h |
| [Update gcr.io/oss-fuzz-base/base-builder-rust Docker digest to 69eb410 (#1557)](https://github.com/open-telemetry/weaver/pull/1557) | app/renovate |  | ✅ | ✅ | 9h |

## Waiting on authors

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [fix: allow loading registries and policies from hidden directories (#992)](https://github.com/open-telemetry/weaver/pull/992) | kuklyy | lquerel&nbsp;🔴 | ✅ | ❌ | 240d |
| [Proposal: Introduce bundles (#871)](https://github.com/open-telemetry/weaver/pull/871) | thompson-tomo | jsuereth | ✅ | ✅ | 108d |
| [feat(live-check): add --fail-on severity gate (#1517)](https://github.com/open-telemetry/weaver/pull/1517) | cijothomas | jerbly&nbsp;💬<br>lmolkova&nbsp;💬 | ❌ | ✅ | 6d |
| [Fix panic when --policy uses a commit SHA refspec (#1414)](https://github.com/open-telemetry/weaver/pull/1414) | lmolkova | Copilot<br>jsuereth&nbsp;💬<br>lmolkova&nbsp;✔️ | ❌ | ❌ | 23h |

## Waiting on external

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [Document generated file header best practice (#1085)](https://github.com/open-telemetry/weaver/pull/1085) | cnaples79 | jsuereth | ❌ | ✅ | 197d |

## Draft pull requests

| PR | Author | Updated |
|---|---|:---:|
| [Introduce the ability to define namespaces #802 (#849)](https://github.com/open-telemetry/weaver/pull/849) | thompson-tomo | 333d |
| [Wip allow multiple dependencies part 2 - Use semver to resolve *some* dependency conflicts (#1377)](https://github.com/open-telemetry/weaver/pull/1377) | jsuereth | 61d |
| [\[wip\] Forward compatibility for definition schema (#1422)](https://github.com/open-telemetry/weaver/pull/1422) | lmolkova | 55d |
| [Consolidate v2 unstable file format warnings and improve diagnostic report styling (#1404)](https://github.com/open-telemetry/weaver/pull/1404) | app/copilot-swe-agent | 28d |

<details>
<summary>Diagnostics</summary>

```text
PR #1517
llm: PRRT_kwDOLPU8Hs6MScna -> author (The latest comment is a reviewer suggestion with alternative names, so the author still needs to respond or decide whether to change it.)
llm: PRRT_kwDOLPU8Hs6MSQV1 -> author (The last reviewer comment clarifies the current behavior and suggests a possible change, so the author needs to respond or adjust the PR.)

PR #1444
llm: pr-conversation -> reviewer (The reviewer approved but explicitly asked for confirmation from maintainers, so the ball is with a reviewer/maintainer rather than the author.)

PR #1414
llm: PRRT_kwDOLPU8Hs6NnL2b -> author (A reviewer requested replacing `Box<dyn Error>` with `From` conversions, so the author needs to update the code or जवाब back; the thread is unresolved.)

PR #1365
llm: PRRT_kwDOLPU8Hs5-gbm8 -> reviewer (The author answered the reviewer’s question and pointed to a separate PR, so the ball is back with the reviewer to approve or respond.)

PR #1334
llm: pr-conversation -> reviewer (The author’s latest comment asks whether anything else is needed, so the ball is with reviewers/maintainers to respond or close the thread.)

PR #1108
llm: PRRT_kwDOLPU8Hs5n6G-x -> reviewer (The author replied that they changed the comment; the reviewer is the next party to acknowledge or continue the review.)
llm: PRRT_kwDOLPU8Hs5oZkJN -> reviewer (The author’s last comment asks a question about allowed suffixes and proposes a possible implementation detail, so the reviewer/maintainer needs to answer or clarify next.)
llm: PRRT_kwDOLPU8Hs5oXuVJ -> none (The reviewer closed the discussion by saying the current PR can leave it as-is and the optional schema-url cleanup will happen in a follow-up, so no reply is needed in this thread.)
llm: pr-conversation -> reviewer (The author is pinging the reviewer to confirm whether their last commit addressed the concerns, so the next response is from the reviewer.)

PR #1085
llm: pr-conversation -> external (The review is blocked on the author signing the CLA, which is outside this repository.)

PR #992
llm: pr-conversation -> author (A reviewer requested a change to make hidden-file access opt-in, so the PR author needs to respond or update the implementation.)

PR #872
llm: pr-conversation -> reviewer (The reviewer asked two questions and the author answered them directly; the ball is back with the reviewer to acknowledge or continue review.)

PR #871
llm: pr-conversation -> author (The latest comment is from a reviewer asking the PR author to confirm which proposal to update and consider, so the author needs to जवाब/respond.)

PR #867
llm: PRRT_kwDOLPU8Hs5Wa2mT -> reviewer (The author replied with a proposed approach, and the reviewer now needs to confirm or continue the discussion about namespace defaults and separators.)
llm: PRRT_kwDOLPU8Hs5Wa2uo -> reviewer (The reviewer asked why `prefix` was removed, and the author replied with the reasoning; the ball is back with the reviewer to acknowledge or continue the review.)
llm: PRRT_kwDOLPU8Hs5Wa6xf -> reviewer (The only comment is from the author asking whether a configuration option should be added, so the reviewer/maintainer needs to პასუხ/decide next.)

PR #853
llm: pr-conversation -> reviewer (The author directly answered the scope question, so the next step is for the reviewer to acknowledge or continue review.)

```

</details>

_Approvers may [force a refresh](https://github.com/open-telemetry/shared-workflows/actions/workflows/pull-request-dashboard.yml)._

