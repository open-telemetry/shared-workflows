> [!NOTE]
> Open non-draft PRs grouped by who is expected to act next. Draft PRs are listed separately. The grouping is partly performed by an LLM ([source](https://github.com/open-telemetry/shared-workflows/blob/main/.github/scripts/pull-request-dashboard/dashboard.py)) and could contain mistakes.
>
> Reviewers column: ✅ approved · ✔️ approved (non-code-owner) · 💬 open thread · 🔴 changes requested.

## Waiting on maintainers

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [Update dependency google-genai to v2 (#112)](https://github.com/open-telemetry/semantic-conventions-genai/pull/112) | app/renovate | lmolkova&nbsp;✅ | ❌ | ✅ | 54d |

## Waiting on reviewers

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [gen-ai: make input-messages BlobPart content optional and add stripped_reason (#144)](https://github.com/open-telemetry/semantic-conventions-genai/pull/144) | Mandark-droid | lmolkova<br>trask | ✅ | ❌ | 21d |
| [Update dependency google-adk to v2 (#328)](https://github.com/open-telemetry/semantic-conventions-genai/pull/328) | app/renovate |  | ❌ | ✅ | 12d |
| [\[chore\] Add signal requirement level to yaml and jinja templates (#340)](https://github.com/open-telemetry/semantic-conventions-genai/pull/340) | lmolkova |  | ✅ | ✅ | 7d |
| [Migrate anthropic reference scenario to opentelemetry-util-genai (#324)](https://github.com/open-telemetry/semantic-conventions-genai/pull/324) | AgentGymLeader | lmolkova | ✅ | ✅ | 4d |
| [Update reference implementation dependencies (non-major) (#352)](https://github.com/open-telemetry/semantic-conventions-genai/pull/352) | app/renovate |  | ❌ | ✅ | 1d |
| [gen-ai: model agent-to-agent handoff as execute_tool span (#98)](https://github.com/open-telemetry/semantic-conventions-genai/pull/98) | Krishnachaitanyakc | lmolkova<br>MikeGoldsmith&nbsp;✅<br>trask | ✅ | ❌ | 27m |

## Waiting on authors

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [Add gen_ai.server.inter_token_latency metric (#164)](https://github.com/open-telemetry/semantic-conventions-genai/pull/164) | Jwrede | Cirilla-zmh<br>lmolkova&nbsp;💬<br>trask | ✅ | ❌ | 44d |
| [gen-ai: add evaluation operation name and gen_ai.evaluate.internal span (#185)](https://github.com/open-telemetry/semantic-conventions-genai/pull/185) | hippoley | Cirilla-zmh&nbsp;💬<br>singankit&nbsp;💬 | ❌ | ✅ | 41d |
| [gen-ai: add gen_ai.response.id to deepeval evaluation result event (#184)](https://github.com/open-telemetry/semantic-conventions-genai/pull/184) | hippoley | lmolkova&nbsp;✅<br>MikeGoldsmith&nbsp;🔴<br>trask | ✅ | ✅ | 34d |
| [gen-ai: add optional byte_size to multimodal content parts (#143)](https://github.com/open-telemetry/semantic-conventions-genai/pull/143) | Mandark-droid | Cirilla-zmh<br>trask&nbsp;💬 | ✅ | ❌ | 32d |
| [Add modality, cache, and phase breakdowns for token usage (#197)](https://github.com/open-telemetry/semantic-conventions-genai/pull/197) | trask | alexmojaki&nbsp;💬<br>lmolkova&nbsp;💬<br>Nik-Reddy&nbsp;💬 | ✅ | ❌ | 32d |
| [Add gen_ai.invoke_agent.server span (SERVER kind) (#252)](https://github.com/open-telemetry/semantic-conventions-genai/pull/252) | singankit | Cirilla-zmh&nbsp;💬<br>trask | ✅ | ❌ | 27d |
| [Add gen_ai.agent.invocation.id attribute for invoke_agent spans (#250)](https://github.com/open-telemetry/semantic-conventions-genai/pull/250) | singankit | lmolkova&nbsp;💬<br>MikeGoldsmith&nbsp;🔴<br>trask | ✅ | ❌ | 21d |
| [Clarify scope of `gen_ai.client.operation.duration` metric (#215)](https://github.com/open-telemetry/semantic-conventions-genai/pull/215) | trask | lmolkova | ✅ | ❌ | 21d |
| [Add workflow node convention (#188)](https://github.com/open-telemetry/semantic-conventions-genai/pull/188) | RKest | aabmass<br>lmolkova&nbsp;🔴<br>trask | ✅ | ❌ | 21d |
| [Add gen_ai.agent.input.content.size and gen_ai.agent.output.content.size metrics (#202)](https://github.com/open-telemetry/semantic-conventions-genai/pull/202) | pvlsirotkin | lmolkova&nbsp;💬<br>MikeGoldsmith&nbsp;🔴<br>trask | ✅ | ❌ | 20d |
| [chore: auto-regenerate outputs on SEMCONV_VERSION bumps via Renovate post-upgrade task (#290)](https://github.com/open-telemetry/semantic-conventions-genai/pull/290) | lmolkova | Copilot<br>lmolkova&nbsp;✔️<br>trask&nbsp;💬 | ✅ | ✅ | 17d |
| [proposal: agent.threat.detection.* attributes + event (closes #132) (#165)](https://github.com/open-telemetry/semantic-conventions-genai/pull/165) | eeee2345 |  | ✅ | ❌ | 13d |
| [Add GenAI client metrics to the anthropic reference scenario (#283)](https://github.com/open-telemetry/semantic-conventions-genai/pull/283) | AgentGymLeader | JWinermaSplunk&nbsp;💬<br>MikeGoldsmith&nbsp;🔴 | ✅ | ✅ | 9d |
| [Add experimental GenAI context selection event (#190)](https://github.com/open-telemetry/semantic-conventions-genai/pull/190) | caioribeiroclw-pixel | lmolkova&nbsp;🔴<br>trask | ❌ | ❌ | 7d |
| [Add `gen_ai.agent.finish_reason` attribute for agent loop termination (#238)](https://github.com/open-telemetry/semantic-conventions-genai/pull/238) | Nik-Reddy | aabmass&nbsp;✅<br>lmolkova&nbsp;🔴<br>MikeGoldsmith&nbsp;✅<br>trask | ✅ | ❌ | 7d |
| [semconv for a2a protocol (#195)](https://github.com/open-telemetry/semantic-conventions-genai/pull/195) | eternalcuriouslearner | aabmass&nbsp;💬<br>JWinermaSplunk<br>pwkowalski&nbsp;💬⁠✔️<br>trask | ✅ | ❌ | 6d |
| [Introduce `gen_ai.invoke_agent.{inference,tool}_calls` (#336)](https://github.com/open-telemetry/semantic-conventions-genai/pull/336) | RKest | aabmass&nbsp;✅<br>lmolkova&nbsp;💬⁠✅<br>trask&nbsp;💬 | ✅ | ❌ | 6d |
| [gen-ai: add run guardrail span and security finding (#262)](https://github.com/open-telemetry/semantic-conventions-genai/pull/262) | nagkumar91 | aabmass<br>habibam&nbsp;✔️<br>hemanshubelani&nbsp;✔️<br>sjain700&nbsp;✔️<br>trask | ✅ | ❌ | 6d |
| [Propose GenAI agent entity (#270)](https://github.com/open-telemetry/semantic-conventions-genai/pull/270) | aabmass | AgentGymLeader&nbsp;✔️<br>lmolkova&nbsp;💬⁠✅<br>trask | ✅ | ❌ | 3d |
| [Remove inference spans from agentic reference instrumentations, add guidance (#351)](https://github.com/open-telemetry/semantic-conventions-genai/pull/351) | lmolkova | JWinermaSplunk&nbsp;💬 | ✅ | ❌ | 1d |
| [Rename gen_ai.workflow.duration to gen_ai.invoke_workflow.duration (#341)](https://github.com/open-telemetry/semantic-conventions-genai/pull/341) | lmolkova | JWinermaSplunk&nbsp;✅<br>wrisa&nbsp;✔️ | ✅ | ✅ | 1d |
| [Clarify workflow span criteria and add reference scenarios for langchain and openai agents (#354)](https://github.com/open-telemetry/semantic-conventions-genai/pull/354) | lmolkova |  | ✅ | ✅ | 20h |
| [feat(gen-ai): add agent authorization observability attributes (#180) (#291)](https://github.com/open-telemetry/semantic-conventions-genai/pull/291) | thebenignhacker | lmolkova&nbsp;🔴 | ✅ | ❌ | 8h |
| [gen-ai: add get_response operation and span (#353)](https://github.com/open-telemetry/semantic-conventions-genai/pull/353) | JacksonWeber |  | ✅ | ✅ | 8h |

## Draft pull requests

| PR | Author | Updated |
|---|---|:---:|
| [Add time_budget value for gen_ai.agent.finish_reason (#267)](https://github.com/open-telemetry/semantic-conventions-genai/pull/267) | Nik-Reddy | 21d |
| [genai: add `gen_ai.token.cache` and `gen_ai.token.reasoning` metric attributes (#96)](https://github.com/open-telemetry/semantic-conventions-genai/pull/96) | Nik-Reddy | 21d |
| [Add gen_ai.agent.identity.id and gen_ai.agent.identity.name: agent identity distinct from the deployed resource (#350)](https://github.com/open-telemetry/semantic-conventions-genai/pull/350) | AraiYuno | 5d |
| [Add nested workflow flag (#355)](https://github.com/open-telemetry/semantic-conventions-genai/pull/355) | lmolkova | 19h |

_Approvers may [force a refresh](https://github.com/open-telemetry/shared-workflows/actions/workflows/pull-request-dashboard.yml)._

