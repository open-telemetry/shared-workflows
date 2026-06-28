> [!NOTE]
> Open non-draft PRs grouped by who is expected to act next. Draft PRs are listed separately. The grouping is partly performed by an LLM ([source](https://github.com/open-telemetry/shared-workflows/blob/main/.github/scripts/pull-request-dashboard/dashboard.py)) and could contain mistakes.
>
> Reviewers column: ✅ approved · ✔️ approved (non-code-owner) · 💬 open thread · 🔴 changes requested.

## Waiting on maintainers

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [\[`opentelemetry-instrumentation-google-genai`\] Update `generate_content` streaming variants to use `AsyncStreamWrapper` and `SyncStreamWrapper`  from utils (#167)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/167) | DylanRussell | lmolkova&nbsp;✅ | ✅ | ✅ | 3d |
| [feat: add instrumentation around openai responses stream method. (#131)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/131) | eternalcuriouslearner | lmolkova&nbsp;✅ | ✅ | ✅ | 2d |
| [\[`opentelemetry-instrumentation-google-genai`\] Add  instrumentation for `interactions.create` and `asyncinteractions.create` methods (#165)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/165) | DylanRussell | lmolkova&nbsp;✅ | ✅ | ✅ | 2d |
| [\[`opentelemetry-instrumentation-google-genai`\] Instrument the embeddings API (#176)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/176) | DylanRussell | lmolkova&nbsp;✅ | ✅ | ✅ | 1d |

## Waiting on reviewers

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [fix(openai): expose headers on streaming with_raw_response wrapper (#147)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/147) | YuxiangJiangCT | eternalcuriouslearner | ❌ | ✅ | 11d |
| [Don't call set attribute twice for the same key and make start attributes unsettable (#150)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/150) | lmolkova | DylanRussell&nbsp;✅ | ❌ | ❌ | 10d |
| [Add retrieval support in langchain (#124)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/124) | wrisa | lmolkova&nbsp;💬 | ✅ | ✅ | 4d |

## Waiting on authors

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [Add streaming timing metrics to generic stream wrappers (#13)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/13) | Nik-Reddy | aabmass<br>eternalcuriouslearner&nbsp;🔴<br>lmolkova&nbsp;🔴<br>lzchen&nbsp;💬<br>MikeGoldsmith&nbsp;🔴 | ✅ | ❌ | 31d |
| [opentelemetry-instrumentation-genai-openai-agents: handle MCPListToolsSpanData (#100)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/100) | Jwrede | lmolkova&nbsp;💬 | ✅ | ❌ | 29d |
| [util-genai \| Add MCPInvocation type for MCP span (#105)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/105) | etserend | shuningc&nbsp;💬 | ✅ | ✅ | 26d |
| [Improve OpenAI Agents conformance and metrics (#49)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/49) | alfozan | lmolkova&nbsp;🔴<br>lzchen&nbsp;💬 | ✅ | ✅ | 16d |
| [Update langchain instrumentation (update to latest semantic conventions, bug fixes, update semantic conventions version, etc.) (#129)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/129) | rads-1996 | JacksonWeber&nbsp;✔️<br>lzchen&nbsp;💬⁠✅<br>nagkumar91&nbsp;✔️ | ✅ | ✅ | 13d |
| [Add Cohere instrumentation package scaffolding (#102)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/102) | Nik-Reddy | eternalcuriouslearner&nbsp;✅<br>lmolkova&nbsp;💬<br>lzchen&nbsp;✅ | ✅ | ❌ | 2d |
| [OpenAI agents: rewrite to util-genai (#90)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/90) | lmolkova | bhumikadangayach&nbsp;💬<br>rads-1996&nbsp;💬 | ❌ | ✅ | 12h |

## Draft pull requests

| PR | Author | Updated |
|---|---|:---:|
| [feat(bedrock): Migrate AWS Bedrock Runtime instrumentation (#93)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/93) | williazz | 12d |
| [\[langchain\] Prototype collecting number of llm and tool calls per agent invocation (#173)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/173) | lmolkova | 2d |

<details>
<summary>Diagnostics</summary>

```text
PR #150
llm: PRRT_kwDOSbspP86KbITi -> reviewer (The latest comment is from the author and looks like a response/clarification, so the reviewer should check it and continue the thread.)
llm: PRRT_kwDOSbspP86KbIpn -> reviewer (The latest comment is from the author and proposes a change, so the ball is back with the reviewer to respond or review that suggestion.)

PR #147
llm: PRRT_kwDOSbspP86KDEr1 -> none (The author արդեն answered the suggestion and indicated no current PR change is needed, with any broader cleanup deferred to a separate task.)

PR #129
llm: PRRT_kwDOSbspP86JpPxW -> author (The latest reviewer comment asks the author to change the source of values to either semantic conventions or LangChain’s available values, so the author has the next action.)

PR #124
llm: PRRT_kwDOSbspP86K8dTl -> reviewer (The reviewer requested an issue/PR to make the score optional, and the author replied with the issue link, so the ball is back with the reviewer to acknowledge or continue the review.)

PR #105
llm: PRRT_kwDOSbspP86GRCWP -> author (Reviewer指出测试锁定了错误行为，暗示作者需要调整实现或测试并回复。)
llm: PRRT_kwDOSbspP86GevWT -> author (The latest comment is a bot review requesting a code change to accept `trace_state`, so the PR author needs to update the implementation.)
llm: PRRT_kwDOSbspP86GevW- -> author (The only comment is a reviewer bot suggestion asking to change the implementation from empty string to `None`, so the PR author needs to act.)
llm: PRRT_kwDOSbspP86GevXY -> author (The latest comment is an automated review suggestion asking to centralize duplicated attribute-building, so the PR author would need to make the change or respond to it.)

PR #102
llm: PRRT_kwDOSbspP86Ma8Es -> author (The reviewer requested a code change (“please create TelemetryHandler instead”), so the PR author needs to respond and update the thread.)
llm: PRRT_kwDOSbspP86Ma8Pq -> author (A reviewer said the code does not seem correct and no follow-up reply or fix is shown, so the PR author needs to respond or update the change.)
llm: PRRT_kwDOSbspP86Ma8sK -> author (A reviewer said the changed cassette looks unrelated, so the author needs to respond or adjust the PR.)
llm: pr-conversation -> author (The last comment is from a reviewer pushing back on using OpenInference because it doesn’t appear to support Cohere instrumentation, so the author needs to जवाब/respond with an alternative or clarification.)

PR #100
llm: PRRT_kwDOSbspP86F3vOM -> author (A bot reviewer flagged a concrete code change and no one has replied yet, so the PR author needs to update the implementation.)
llm: PRRT_kwDOSbspP86F3vON -> author (A bot review comment suggests a code change, so the PR author needs to update the implementation or respond.)
llm: PRRT_kwDOSbspP86F3vOP -> author (A bot reviewer flagged a naming/API issue and suggested a code change, so the PR author needs to update the implementation or respond.)
llm: PRRT_kwDOSbspP86KaNp1 -> author (Reviewer requested a design change and no author reply followed, so the PR author needs to respond or update the implementation.)
llm: pr-conversation -> reviewer (The author asked the reviewer to choose whether to keep this PR open as a tracking reference or close it and move the gap to an issue, so the reviewer needs to जवाब/decide next.)

PR #90
llm: PRRT_kwDOSbspP86LaGma -> author (The latest comment is from a reviewer and asks for a deprecation warning / possible follow-up, so the PR author needs to respond or implement the change.)

PR #49
llm: PRRT_kwDOSbspP86H4tJU -> none (The author answered the suggestion and explicitly deferred the refactor to a follow-up PR, so the current thread is effectively closed.)
llm: PRRT_kwDOSbspP86I8NyE -> author (The only comment is from a reviewer raising a concern and asking for thoughts, so the PR author needs to respond or adjust the code.)
llm: PRRT_kwDOSbspP86I8O6k -> author (A reviewer asked an open design question about undefined behavior and whether root spans should be marked OK, so the PR author needs to जवाब/respond and decide the implementation.)
llm: pr-conversation -> author (The latest comment is from a reviewer requesting the PR author’s response/action, so the ball is with the author.)

PR #13
llm: PRRT_kwDOSbspP86Dn1e0 -> reviewer (The author answered the question and explained why the method is not needed here, so the ball is back with the reviewer to accept or continue the discussion.)
llm: PRRT_kwDOSbspP86Dn5N7 -> reviewer (The author explained the tradeoff and asked the reviewer to decide which direction they want, so the next action is with the reviewer.)
llm: PRRT_kwDOSbspP86Dn925 -> author (The latest comment is from the author and says they will batch the decision with the next push, so the PR still needs author follow-up.)
llm: PRRT_kwDOSbspP86DoNGR -> reviewer (The author has responded with a proposed resolution and asked the reviewer whether to prefer option 2 instead, so the ball is back with the reviewer.)
llm: PRRT_kwDOSbspP86KYIl0 -> author (A reviewer asked whether these files are related to the PR, so the author needs to जवाब/respond and clarify.)
llm: pr-conversation -> author (A reviewer left CHANGES_REQUESTED with concrete suggestions and no follow-up from the author yet, so the PR author needs to respond and update the code.)

```

</details>

_Approvers may [force a refresh](https://github.com/open-telemetry/shared-workflows/actions/workflows/pull-request-dashboard.yml)._

