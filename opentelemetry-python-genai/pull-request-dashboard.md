> [!NOTE]
> Open non-draft PRs grouped by who is expected to act next. Draft PRs are listed separately. The grouping is partly performed by an LLM ([source](https://github.com/open-telemetry/shared-workflows/blob/main/.github/scripts/pull-request-dashboard/dashboard.py)) and could contain mistakes.
>
> Reviewers column: ✅ approved · ✔️ approved (non-code-owner) · 💬 open thread · 🔴 changes requested.

## Waiting on maintainers

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [Update dependency google-auth to v2.55.2 (#201)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/201) | app/renovate | DylanRussell&nbsp;✅ | ✅ | ✅ | 19h |
| [\[`opentelemetry-instrumentation-google-genai`\] Add  instrumentation for `interactions.create` and `asyncinteractions.create` methods (#165)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/165) | DylanRussell | eternalcuriouslearner&nbsp;✅<br>lmolkova&nbsp;✅ | ✅ | ✅ | 4h |

## Waiting on reviewers

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [fix(openai): expose headers on streaming with_raw_response wrapper (#147)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/147) | YuxiangJiangCT | eternalcuriouslearner | ❌ | ✅ | 21d |
| [Instrument OpenAI Responses.retrieve and AsyncResponses.retrieve (#184)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/184) | JacksonWeber | eternalcuriouslearner&nbsp;💬⁠✅<br>lmolkova&nbsp;💬 | ✅ | ❌ | 7d |
| [feat(langchain): add ChatAnthropic support to langchain instrumentation (#188)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/188) | bhumikadangayach | eternalcuriouslearner | ✅ | ✅ | 1d |
| [code scanning report: bump versions, make renovate update all req.txt (#204)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/204) | lmolkova |  | ✅ | ✅ | 1h |

## Waiting on authors

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [Add streaming timing metrics to generic stream wrappers (#13)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/13) | Nik-Reddy | aabmass<br>eternalcuriouslearner&nbsp;🔴<br>lmolkova&nbsp;🔴<br>lzchen&nbsp;💬<br>MikeGoldsmith&nbsp;🔴 | ✅ | ❌ | 41d |
| [opentelemetry-instrumentation-genai-openai-agents: handle MCPListToolsSpanData (#100)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/100) | Jwrede | lmolkova&nbsp;💬<br>lzchen | ✅ | ❌ | 39d |
| [Improve OpenAI Agents conformance and metrics (#49)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/49) | alfozan | lmolkova&nbsp;🔴<br>lzchen&nbsp;💬 | ✅ | ❌ | 26d |
| [Add Cohere instrumentation package scaffolding (#102)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/102) | Nik-Reddy | eternalcuriouslearner&nbsp;✅<br>lmolkova&nbsp;💬<br>lzchen&nbsp;✅ | ✅ | ❌ | 12d |
| [feat: Add instrumentation for AsyncMessages.stream, Messages.parse and AsyncMessages.parse methods. (#191)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/191) | eternalcuriouslearner | DylanRussell&nbsp;💬⁠✅<br>lmolkova&nbsp;✅ | ✅ | ✅ | 1d |
| [Don't call set attribute twice for the same key and make start attributes unsettable (#150)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/150) | lmolkova | DylanRussell&nbsp;✅<br>lzchen&nbsp;💬 | ❌ | ❌ | 1d |
| [fix(openai-agents): record tool calls as structured tool_call parts (#203)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/203) | SahilKumar75 |  | ✅ | ❌ | 4h |
| [Update langchain instrumentation (update to latest semantic conventions, bug fixes, update semantic conventions version, etc.) (#129)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/129) | rads-1996 | JacksonWeber&nbsp;✔️<br>lmolkova&nbsp;💬<br>lzchen&nbsp;✅<br>nagkumar91&nbsp;✔️ | ❌ | ✅ | 34m |
| [\[`opentelemetry-instrumentation-google-genai`\]: record response model attribute on inference span/event (#205)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/205) | DylanRussell | lmolkova&nbsp;✅ | ✅ | ✅ | 17m |

## Waiting on external

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [Add retrieval support in langchain (#124)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/124) | wrisa | eternalcuriouslearner<br>lmolkova&nbsp;💬 | ✅ | ✅ | 5h |

## Draft pull requests

| PR | Author | Updated |
|---|---|:---:|
| [\[langchain\] Prototype collecting number of llm and tool calls per agent invocation (#173)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/173) | lmolkova | 12d |
| [openinference migration skill output for langchain (#181)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/181) | keith-decker | 9d |
| [feat(bedrock): Migrate AWS Bedrock Runtime instrumentation (#93)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/93) | williazz | 7d |
| [Prototype: add nested workflow detection in langchain (#189)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/189) | lmolkova | 7d |
| [Prototype showing gen_ai.conversation_root span attribute to mark the root GenAI span of a conversation (WIP) (#187)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/187) | wrisa | 7d |

_Approvers may [force a refresh](https://github.com/open-telemetry/shared-workflows/actions/workflows/pull-request-dashboard.yml)._

