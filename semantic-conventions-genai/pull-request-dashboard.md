> [!NOTE]
> Open non-draft PRs grouped by who is expected to act next. Draft PRs are listed separately. The grouping is partly performed by an LLM ([source](https://github.com/open-telemetry/shared-workflows/blob/main/.github/scripts/pull-request-dashboard/dashboard.py)) and could contain mistakes.
>
> Reviewers column: ✅ approved · ✔️ approved (non-code-owner) · 💬 open thread · 🔴 changes requested.

## Waiting on maintainers

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [Update dependency google-genai to v2 (#112)](https://github.com/open-telemetry/semantic-conventions-genai/pull/112) | app/renovate | lmolkova&nbsp;✅ | ❌ | ✅ | 49d |

## Waiting on reviewers

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [gen-ai: make input-messages BlobPart content optional and add stripped_reason (#144)](https://github.com/open-telemetry/semantic-conventions-genai/pull/144) | Mandark-droid | lmolkova<br>trask | ✅ | ❌ | 16d |
| [Update dependency google-adk to v2 (#328)](https://github.com/open-telemetry/semantic-conventions-genai/pull/328) | app/renovate |  | ❌ | ✅ | 7d |
| [Migrate anthropic reference scenario to opentelemetry-util-genai (#324)](https://github.com/open-telemetry/semantic-conventions-genai/pull/324) | AgentGymLeader |  | ✅ | ✅ | 7d |
| [\[chore\] Add signal requirement level to yaml and jinja templates (#340)](https://github.com/open-telemetry/semantic-conventions-genai/pull/340) | lmolkova |  | ✅ | ✅ | 2d |
| [gen-ai: model agent-to-agent handoff as execute_tool span (#98)](https://github.com/open-telemetry/semantic-conventions-genai/pull/98) | Krishnachaitanyakc | lmolkova<br>MikeGoldsmith&nbsp;✅<br>trask | ✅ | ✅ | 1d |
| [Rename gen_ai.workflow.duration to gen_ai.invoke_workflow.duration (#341)](https://github.com/open-telemetry/semantic-conventions-genai/pull/341) | lmolkova |  | ✅ | ✅ | 23h |
| [Add Agent Framework reference scenario (#325)](https://github.com/open-telemetry/semantic-conventions-genai/pull/325) | eavanvalkenburg | lmolkova&nbsp;✅ | ✅ | ✅ | 15h |
| [feat(gen-ai): add agent authorization observability attributes (#180) (#291)](https://github.com/open-telemetry/semantic-conventions-genai/pull/291) | thebenignhacker | lmolkova&nbsp;🔴 | ✅ | ❌ | 8h |
| [Propose GenAI agent entity (#270)](https://github.com/open-telemetry/semantic-conventions-genai/pull/270) | aabmass | AgentGymLeader&nbsp;✔️<br>lmolkova&nbsp;✅<br>trask | ✅ | ✅ | 5h |

## Waiting on authors

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [Add gen_ai.server.inter_token_latency metric (#164)](https://github.com/open-telemetry/semantic-conventions-genai/pull/164) | Jwrede | Cirilla-zmh<br>lmolkova&nbsp;💬<br>trask | ✅ | ❌ | 39d |
| [gen-ai: add evaluation operation name and gen_ai.evaluate.internal span (#185)](https://github.com/open-telemetry/semantic-conventions-genai/pull/185) | hippoley | Cirilla-zmh&nbsp;💬<br>singankit&nbsp;💬 | ❌ | ✅ | 36d |
| [gen-ai: add gen_ai.response.id to deepeval evaluation result event (#184)](https://github.com/open-telemetry/semantic-conventions-genai/pull/184) | hippoley | lmolkova&nbsp;✅<br>MikeGoldsmith&nbsp;🔴<br>trask | ✅ | ✅ | 29d |
| [gen-ai: add optional byte_size to multimodal content parts (#143)](https://github.com/open-telemetry/semantic-conventions-genai/pull/143) | Mandark-droid | Cirilla-zmh<br>trask&nbsp;💬 | ✅ | ❌ | 27d |
| [Add modality, cache, and phase breakdowns for token usage (#197)](https://github.com/open-telemetry/semantic-conventions-genai/pull/197) | trask | alexmojaki&nbsp;💬<br>lmolkova&nbsp;💬<br>Nik-Reddy&nbsp;💬 | ✅ | ❌ | 26d |
| [Add gen_ai.invoke_agent.server span (SERVER kind) (#252)](https://github.com/open-telemetry/semantic-conventions-genai/pull/252) | singankit | Cirilla-zmh&nbsp;💬<br>trask | ✅ | ❌ | 21d |
| [Add gen_ai.agent.invocation.id attribute for invoke_agent spans (#250)](https://github.com/open-telemetry/semantic-conventions-genai/pull/250) | singankit | lmolkova&nbsp;💬<br>MikeGoldsmith&nbsp;🔴<br>trask | ✅ | ❌ | 16d |
| [Clarify scope of `gen_ai.client.operation.duration` metric (#215)](https://github.com/open-telemetry/semantic-conventions-genai/pull/215) | trask | lmolkova | ✅ | ❌ | 16d |
| [Add workflow node convention (#188)](https://github.com/open-telemetry/semantic-conventions-genai/pull/188) | RKest | aabmass<br>lmolkova&nbsp;🔴<br>trask | ✅ | ❌ | 16d |
| [Add gen_ai.agent.input.content.size and gen_ai.agent.output.content.size metrics (#202)](https://github.com/open-telemetry/semantic-conventions-genai/pull/202) | pvlsirotkin | lmolkova&nbsp;💬<br>MikeGoldsmith&nbsp;🔴<br>trask | ✅ | ❌ | 15d |
| [chore: auto-regenerate outputs on SEMCONV_VERSION bumps via Renovate post-upgrade task (#290)](https://github.com/open-telemetry/semantic-conventions-genai/pull/290) | lmolkova | Copilot<br>lmolkova&nbsp;✔️<br>trask&nbsp;💬 | ✅ | ✅ | 12d |
| [proposal: agent.threat.detection.* attributes + event (closes #132) (#165)](https://github.com/open-telemetry/semantic-conventions-genai/pull/165) | eeee2345 |  | ✅ | ❌ | 8d |
| [Add GenAI client metrics to the anthropic reference scenario (#283)](https://github.com/open-telemetry/semantic-conventions-genai/pull/283) | AgentGymLeader | JWinermaSplunk&nbsp;💬<br>MikeGoldsmith&nbsp;🔴 | ✅ | ✅ | 4d |
| [Add experimental GenAI context selection event (#190)](https://github.com/open-telemetry/semantic-conventions-genai/pull/190) | caioribeiroclw-pixel | lmolkova&nbsp;🔴<br>trask | ❌ | ❌ | 2d |
| [Add `gen_ai.agent.finish_reason` attribute for agent loop termination (#238)](https://github.com/open-telemetry/semantic-conventions-genai/pull/238) | Nik-Reddy | aabmass&nbsp;✅<br>lmolkova&nbsp;🔴<br>MikeGoldsmith&nbsp;✅<br>trask | ✅ | ❌ | 2d |
| [semconv for a2a protocol (#195)](https://github.com/open-telemetry/semantic-conventions-genai/pull/195) | eternalcuriouslearner | aabmass&nbsp;💬<br>JWinermaSplunk<br>pwkowalski&nbsp;💬⁠✔️<br>trask | ✅ | ✅ | 1d |
| [Introduce `gen_ai.invoke_agent.{inference,tool}_calls` (#336)](https://github.com/open-telemetry/semantic-conventions-genai/pull/336) | RKest | aabmass&nbsp;💬⁠✅<br>lmolkova&nbsp;💬⁠✅<br>trask&nbsp;💬 | ✅ | ❌ | 1d |
| [gen-ai: add run guardrail span and security finding (#262)](https://github.com/open-telemetry/semantic-conventions-genai/pull/262) | nagkumar91 | aabmass<br>habibam&nbsp;✔️<br>hemanshubelani&nbsp;✔️<br>sjain700&nbsp;✔️<br>trask | ✅ | ❌ | 23h |
| [Remove inference spans from agentic reference instrumentations, add guidance (#351)](https://github.com/open-telemetry/semantic-conventions-genai/pull/351) | lmolkova |  | ✅ | ✅ | 19h |

## Draft pull requests

| PR | Author | Updated |
|---|---|:---:|
| [Add time_budget value for gen_ai.agent.finish_reason (#267)](https://github.com/open-telemetry/semantic-conventions-genai/pull/267) | Nik-Reddy | 16d |
| [genai: add `gen_ai.token.cache` and `gen_ai.token.reasoning` metric attributes (#96)](https://github.com/open-telemetry/semantic-conventions-genai/pull/96) | Nik-Reddy | 16d |
| [Add gen_ai.agent.identity.id and gen_ai.agent.identity.name: agent identity distinct from the deployed resource (#350)](https://github.com/open-telemetry/semantic-conventions-genai/pull/350) | AraiYuno | 20h |

<details>
<summary>Diagnostics</summary>

```text
PR #351
llm: PRRT_kwDOSUeMrM6MamGP -> author (The only comment is a bot review raising a conflict between the README and the scenario/AGENTS guidance, so the author needs to respond or update the docs/code.)

PR #336
llm: PRRT_kwDOSUeMrM6LwmDS -> reviewer (The author replied with an implementation update and changed the wording; the ball is back with the reviewer to confirm or close the thread.)
llm: PRRT_kwDOSUeMrM6MELJI -> author (The latest reviewer comment asks RKest to update the PR description and refresh metric names, so the author needs to act next.)

PR #325
llm: pr-conversation -> reviewer (The author says they fixed the pipeline and it should be good now, so the ball is back with the reviewer to re-check or close the thread.)

PR #291
llm: pr-conversation -> reviewer (The author’s last comment addresses the scope concern and clarifies the proposal with concrete examples, so the ball is back with the reviewer to respond or decide whether this framing is acceptable.)

PR #290
llm: PRRT_kwDOSUeMrM6JXzS- -> author (The bot flagged a config issue and suggested changing `matchPackageNames` to `matchDepNames` or capturing `packageName`, so the PR author needs to update the Renovate config.)
llm: PRRT_kwDOSUeMrM6J7Q-G -> author (The reviewer pointed out a limitation in the current Renovate config and suggested an alternative, so the PR author needs to adjust the implementation or respond.)

PR #283
llm: PRRT_kwDOSUeMrM6KQIPN -> reviewer (The author replied with a fix and left an open question about whether `gen_ai.client.token.usage` should also get `error.type`, so the reviewer/maintainer needs to answer or confirm.)
llm: PRRT_kwDOSUeMrM6LV3l5 -> reviewer (The last comment is from a reviewer asking for input on whether unit tests should be added, so the next action is on the reviewer/maintainer side.)
llm: PRRT_kwDOSUeMrM6LWVxo -> author (A reviewer asked for clarification on the rationale for the change, so the PR author needs to जवाब/respond.)
llm: pr-conversation -> reviewer (The latest comment is from the author and explicitly asks the reviewer to take another look and decide whether to keep the unit tests.)

PR #262
llm: pr-conversation -> author (The last comment is a reviewer/maintainer giving additional suggestions and clarifications, so the PR author needs to respond or update the PR.)

PR #252
llm: PRRT_kwDOSUeMrM6HO1Cy -> author (The latest comment is a bot review request asking for new reference scenario coverage and regenerated outputs, so the PR author needs to make the change.)
llm: PRRT_kwDOSUeMrM6IKVVh -> author (A reviewer left a suggestion on the changelog and no one has responded yet, so the PR author needs to make or address the change.)
llm: pr-conversation -> author (The latest comment is a reviewer asking for clarification about how to emit `gen_ai.invoke_agent.server`, so the PR author needs to जवाब/implement the guidance.)

PR #250
llm: PRRT_kwDOSUeMrM6HQjux -> reviewer (The author has replied with a proposed approach and additional context, ending with an open question/no-objection stance, so the next action is on the reviewer to respond or approve.)
llm: pr-conversation -> author (The latest reviewer comment requests a changelog move into a Towncrier fragment, so the PR author needs to make that follow-up change and reply.)

PR #238
llm: PRRT_kwDOSUeMrM6LzhqO -> author (The reviewer asked the PR author to rebase and regenerate the docs, and the thread still shows conflicts.)
llm: PRRT_kwDOSUeMrM6Lzi1l -> author (A reviewer asked for a code change suggestion ('can we do better than failed?'), so the PR author needs to respond or update the code.)
llm: PRRT_kwDOSUeMrM6LzkeQ -> author (A reviewer asked why `status` is needed and suggested `error.type` is more precise, so the ball is with the PR author to जवाब/respond or adjust the implementation.)
llm: PRRT_kwDOSUeMrM6LzlG9 -> author (A reviewer raised a concern that the status field may be duplicative of existing fields, so the author needs to respond or adjust the code.)
llm: PRRT_kwDOSUeMrM6LznoC -> author (A reviewer raised a design concern about the YAML entry and no author reply followed, so the author needs to respond or adjust the PR.)
llm: pr-conversation -> author (The latest comment is a reviewer CHANGES_REQUESTED asking the author to reconsider the approach, so the author needs to जवाब/act next.)

PR #215
llm: PRRT_kwDOSUeMrM6Fl7mu -> none (The latest reviewer comment is a closing acknowledgement that says the clarification looks directionally aligned and useful as-is, with no follow-up requested.)
llm: pr-conversation -> author (The latest comment requests a PR change: move the changelog entry into a Towncrier fragment and remove the direct CHANGELOG.md edit, so the PR author needs to act.)

PR #202
llm: PRRT_kwDOSUeMrM6Is_Ar -> author (A reviewer left a substantive suggestion and asked for a change to be made, so the author needs to respond or update the PR.)
llm: PRRT_kwDOSUeMrM6ItCxM -> author (The reviewer flagged ambiguous wording and suggested replacement text, so the PR author needs to update the description or respond to the clarification.)
llm: PRRT_kwDOSUeMrM6ItDZZ -> author (A reviewer suggested removing the justification from the spec, so the PR author needs to update the file or respond.)
llm: pr-conversation -> author (The only comment is from the PR author and says there are still open review threads that need follow-up, so the ball remains with the author.)

PR #197
llm: PRRT_kwDOSUeMrM6E-Ear -> reviewer (The reviewer asked whether to add a separate embeddings token metric or broaden the existing one; the author replied with a preferred name and linked a change, so the ball is back with the reviewer to acknowledge or review.)
llm: PRRT_kwDOSUeMrM6FkB2H -> author (The latest comment is from a reviewer asking for more specifics and still debating the schema choice, so the PR author needs to respond or adjust the change.)
llm: PRRT_kwDOSUeMrM6F1og7 -> author (The latest reviewer comment raises a follow-up design point and leaves it for later, so the PR author needs to respond or decide how to handle the token breakdown.)
llm: PRRT_kwDOSUeMrM6F1nUT -> author (A reviewer/approver asked a question and proposed a change, so the author needs to respond or update the PR.)
llm: PRRT_kwDOSUeMrM6HcJqe -> author (A reviewer suggested an alternative naming approach and no author reply followed, so the PR author needs to respond or update the change.)
llm: PRRT_kwDOSUeMrM6HcSsx -> author (The latest comment is a reviewer question/suggestion about the spec shape, so the author needs to जवाब/respond or adjust the PR.)
llm: PRRT_kwDOSUeMrM6HckTh -> author (The latest comment is a reviewer/approver suggesting a design change, so the PR author needs to respond or update the proposal.)
llm: PRRT_kwDOSUeMrM6HcoWo -> author (A reviewer asked whether span attributes and metrics should be in the same PR, so the author needs to जवाब/adjust the PR.)
llm: pr-conversation -> author (The latest substantive comment is from a reviewer asking for an explicit documentation note about cost/accounting boundaries, so the PR author needs to respond or make the requested update.)

PR #195
llm: PRRT_kwDOSUeMrM6MX4n7 -> author (A reviewer asked for clarification on “context correlation,” so the PR author needs to जवाब/clarify.)
llm: PRRT_kwDOSUeMrM6MX5ua -> author (A reviewer asked for a documentation change (“Can we add a link to the A2A spec?”), so the PR author needs to respond by updating the doc or replying with a rationale.)
llm: PRRT_kwDOSUeMrM6MX_b2 -> author (A reviewer asked whether getting these in the instrumentation is straightforward, so the author needs to პასუხ/clarify or make the change.)
llm: PRRT_kwDOSUeMrM6MYC-V -> author (The reviewer asked a substantive question about whether context propagation needs to be mentioned, so the author needs to जवाब/confirm before the thread is closed.)
llm: PRRT_kwDOSUeMrM6MX-aI -> author (The latest comment is a reviewer raising open questions and saying it needs further discussion, so the PR author needs to respond or adjust the proposal.)

PR #190
llm: PRRT_kwDOSUeMrM6LzGuu -> author (A reviewer raised a blocking concern about the magic numbers, so the PR author needs to respond or adjust the code.)

PR #188
llm: PRRT_kwDOSUeMrM6EP5P6 -> reviewer (The reviewer asked where the value would come from, and the author replied with a concrete real-instrumentation example and trace, so the ball is back with the reviewer to confirm or continue.)
llm: PRRT_kwDOSUeMrM6H_pco -> reviewer (The author answered the question and deferred wording to a separate PR, so the ball is back with the reviewer to acknowledge or continue review.)
llm: PRRT_kwDOSUeMrM6KiI8p -> author (The latest comment is a review bot requesting a code change: the empty `invoke_node` span should run minimal validation logic, so the PR author needs to update the scenario.)
llm: PRRT_kwDOSUeMrM6KiI9k -> author (The bot flagged a mismatch between the report and the committed Haystack scenario, indicating the PR author needs to update the scenario or the report content.)
llm: pr-conversation -> author (A reviewer asked the PR author to move the changelog entry into a Towncrier fragment and remove the direct CHANGELOG.md edit, so the author needs to make that change.)

PR #185
llm: PRRT_kwDOSUeMrM6DuuPn -> author (The only comment is a reviewer bot suggesting a terminology alignment change, so the PR author needs to act on or जवाब to the recommendation.)
llm: PRRT_kwDOSUeMrM6E_Amb -> author (A reviewer flagged a likely wording fix (“we need a verb here”) and there’s no follow-up response yet, so the author needs to update the PR.)
llm: PRRT_kwDOSUeMrM6E_COY -> author (A reviewer asked for clarification and a prototype, so the PR author needs to जवाब/respond with details or an example.)
llm: PRRT_kwDOSUeMrM6HOBHP -> author (A reviewer raised a concern about the attribute’s relevance, and there’s no author reply yet.)
llm: PRRT_kwDOSUeMrM6HOBik -> author (A reviewer left follow-up feedback asking for the same change on another attribute, so the PR author needs to update the file.)
llm: pr-conversation -> author (A reviewer asked for clarification questions and there is no author response yet, so the author needs to जवाब/answer.)

PR #184
llm: pr-conversation -> author (A reviewer requested changes after a force-push, so the PR author needs to update the branch and respond.)

PR #165
llm: PRRT_kwDOSUeMrM6KdZA7 -> author (The latest bot review comment flags missing required changelog and reference scenario updates, so the PR author needs to make those changes and respond.)
llm: PRRT_kwDOSUeMrM6KdZBh -> author (The bot points out a mismatch between the PR description and committed generated artifacts and asks to update one or the other, so the author needs to act.)

PR #164
llm: PRRT_kwDOSUeMrM6C-3Kb -> author (A reviewer asked for clarification and questioned whether a new metric is needed; the author needs to respond or adjust the PR.)
llm: pr-conversation -> author (The latest substantive comment is a maintainer request to move the changelog entry into a Towncrier fragment and remove the direct CHANGELOG.md edit, so the PR author needs to make that change.)

PR #144
llm: pr-conversation -> reviewer (The latest comment is from the author and responds to the review points, so the ball is back with the reviewer/maintainer to react, resolve the remaining preference question, or approve.)

PR #143
llm: PRRT_kwDOSUeMrM6F1Aqk -> author (The latest reviewer comment explains the suggestion is useful with #144, so the ball is still with the author to respond or adjust the PR.)
llm: PRRT_kwDOSUeMrM6F0-FD -> author (The latest comment is from the reviewer asking the author to add reference scenarios for FilePart and UriPart, so the author needs to act next.)
llm: pr-conversation -> reviewer (The author addressed the changelog request and reported the related rebase/regeneration work as done, so the ball is back with the reviewer to re-check and continue review.)

PR #112
llm: pr-conversation -> author (The reviewer said the change will not work yet and requested changes; the author needs to wait for the dependency bump or update the PR accordingly.)

PR #98
llm: pr-conversation -> reviewer (The author asked whether it can be added to the merge queue and if anything else needs addressing, so the ball is with a reviewer/maintainer to respond or merge.)

```

</details>

_Approvers may [force a refresh](https://github.com/open-telemetry/shared-workflows/actions/workflows/pull-request-dashboard.yml)._

