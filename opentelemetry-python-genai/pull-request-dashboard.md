> [!NOTE]
> Open non-draft PRs grouped by who is expected to act next. Draft PRs are listed separately. The grouping is partly performed by an LLM ([source](https://github.com/open-telemetry/shared-workflows/blob/main/.github/scripts/pull-request-dashboard/dashboard.py)) and could contain mistakes.
>
> Reviewers column: ✅ approved · ✔️ approved (non-code-owner) · 💬 open thread · 🔴 changes requested.

## Waiting on maintainers

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [\[`opentelemetry-instrumentation-google-genai`\] Update `generate_content` streaming variants to use `AsyncStreamWrapper` and `SyncStreamWrapper`  from utils (#167)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/167) | DylanRussell | lmolkova&nbsp;✅ | ✅ | ✅ | 5d |
| [feat: add instrumentation around openai responses stream method. (#131)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/131) | eternalcuriouslearner | lmolkova&nbsp;✅ | ✅ | ✅ | 4d |
| [\[`opentelemetry-instrumentation-google-genai`\] Add  instrumentation for `interactions.create` and `asyncinteractions.create` methods (#165)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/165) | DylanRussell | lmolkova&nbsp;✅ | ✅ | ❌ | 4d |

## Waiting on reviewers

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [fix(openai): expose headers on streaming with_raw_response wrapper (#147)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/147) | YuxiangJiangCT | eternalcuriouslearner | ❌ | ✅ | 13d |
| [Don't call set attribute twice for the same key and make start attributes unsettable (#150)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/150) | lmolkova | DylanRussell&nbsp;✅ | ❌ | ❌ | 12d |
| [OpenAI agents: rewrite to util-genai (#90)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/90) | lmolkova | rads-1996&nbsp;✔️ | ❌ | ✅ | 9d |
| [Add retrieval support in langchain (#124)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/124) | wrisa | lmolkova&nbsp;💬 | ✅ | ✅ | 6d |

## Waiting on authors

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [Add streaming timing metrics to generic stream wrappers (#13)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/13) | Nik-Reddy | aabmass<br>eternalcuriouslearner&nbsp;🔴<br>lmolkova&nbsp;🔴<br>lzchen&nbsp;💬<br>MikeGoldsmith&nbsp;🔴 | ✅ | ❌ | 33d |
| [opentelemetry-instrumentation-genai-openai-agents: handle MCPListToolsSpanData (#100)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/100) | Jwrede | lmolkova&nbsp;💬 | ✅ | ❌ | 31d |
| [util-genai \| Add MCPInvocation type for MCP span (#105)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/105) | etserend | shuningc&nbsp;💬 | ✅ | ✅ | 28d |
| [Improve OpenAI Agents conformance and metrics (#49)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/49) | alfozan | lmolkova&nbsp;🔴<br>lzchen&nbsp;💬 | ✅ | ✅ | 18d |
| [Update langchain instrumentation (update to latest semantic conventions, bug fixes, update semantic conventions version, etc.) (#129)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/129) | rads-1996 | JacksonWeber&nbsp;✔️<br>lzchen&nbsp;💬⁠✅<br>nagkumar91&nbsp;✔️ | ✅ | ✅ | 15d |
| [Add Cohere instrumentation package scaffolding (#102)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/102) | Nik-Reddy | eternalcuriouslearner&nbsp;✅<br>lmolkova&nbsp;💬<br>lzchen&nbsp;✅ | ✅ | ❌ | 4d |
| [Instrument OpenAI Responses.retrieve and AsyncResponses.retrieve (#184)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/184) | JacksonWeber | eternalcuriouslearner&nbsp;✅<br>lmolkova&nbsp;💬 | ✅ | ✅ | 4h |

## Draft pull requests

| PR | Author | Updated |
|---|---|:---:|
| [feat(bedrock): Migrate AWS Bedrock Runtime instrumentation (#93)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/93) | williazz | 14d |
| [\[langchain\] Prototype collecting number of llm and tool calls per agent invocation (#173)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/173) | lmolkova | 4d |
| [openinference migration skill output for langchain (#181)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/181) | keith-decker | 1d |
| [Add conversation root(WIP) (#187)](https://github.com/open-telemetry/opentelemetry-python-genai/pull/187) | wrisa | <1m |

_Approvers may [force a refresh](https://github.com/open-telemetry/shared-workflows/actions/workflows/pull-request-dashboard.yml)._

