> [!NOTE]
> Open non-draft PRs grouped by who is expected to act next. Draft PRs are listed separately. The grouping is partly performed by an LLM ([source](https://github.com/open-telemetry/shared-workflows/blob/main/.github/scripts/pull-request-dashboard/dashboard.py)) and could contain mistakes.
>
> Reviewers column: ✅ approved · ✔️ approved (non-code-owner) · 💬 open thread · 🔴 changes requested.

## Waiting on reviewers

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [gen-ai: make input-messages BlobPart content optional and add stripped_reason (#144)](https://github.com/open-telemetry/semantic-conventions-genai/pull/144) | Mandark-droid | lmolkova<br>trask | ✅ | ❌ | 27d |
| [gen-ai: model agent-to-agent handoff as execute_tool span (#98)](https://github.com/open-telemetry/semantic-conventions-genai/pull/98) | Krishnachaitanyakc | lmolkova<br>MikeGoldsmith&nbsp;✅<br>trask | ✅ | ✅ | 6d |

## Waiting on authors

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [Add gen_ai.server.inter_token_latency metric (#164)](https://github.com/open-telemetry/semantic-conventions-genai/pull/164) | Jwrede | Cirilla-zmh<br>lmolkova&nbsp;💬<br>trask | ✅ | ❌ | 50d |
| [gen-ai: add evaluation operation name and gen_ai.evaluate.internal span (#185)](https://github.com/open-telemetry/semantic-conventions-genai/pull/185) | hippoley | Cirilla-zmh&nbsp;💬<br>singankit&nbsp;💬 | ❌ | ✅ | 48d |
| [gen-ai: add gen_ai.response.id to deepeval evaluation result event (#184)](https://github.com/open-telemetry/semantic-conventions-genai/pull/184) | hippoley | lmolkova&nbsp;✅<br>MikeGoldsmith&nbsp;🔴<br>trask | ✅ | ✅ | 41d |
| [gen-ai: add optional byte_size to multimodal content parts (#143)](https://github.com/open-telemetry/semantic-conventions-genai/pull/143) | Mandark-droid | Cirilla-zmh<br>trask&nbsp;💬 | ✅ | ❌ | 39d |
| [proposal: agent.threat.detection.* attributes + event (closes #132) (#165)](https://github.com/open-telemetry/semantic-conventions-genai/pull/165) | eeee2345 |  | ✅ | ❌ | 20d |
| [Add workflow node convention (#188)](https://github.com/open-telemetry/semantic-conventions-genai/pull/188) | RKest | aabmass<br>lmolkova&nbsp;🔴<br>trask | ✅ | ✅ | 1d |

## Unknown

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [feat: add gen_ai.attribution.link_type for tool-call provenance (addresses #311) (#370)](https://github.com/open-telemetry/semantic-conventions-genai/pull/370) | grvnmttl |  | ? | ? | ? |
| [Add GenAI input messages delta attribute (#365)](https://github.com/open-telemetry/semantic-conventions-genai/pull/365) | yyuuttaaoo |  | ? | ? | ? |
| [gen-ai: make response finish reasons authoritative (#363)](https://github.com/open-telemetry/semantic-conventions-genai/pull/363) | yyuuttaaoo |  | ? | ? | ? |
| [gen-ai: fix system instructions reference scenarios (#361)](https://github.com/open-telemetry/semantic-conventions-genai/pull/361) | yyuuttaaoo |  | ? | ? | ? |
| [Add evaluator provenance attributes to gen_ai.evaluation.* (#359)](https://github.com/open-telemetry/semantic-conventions-genai/pull/359) | Mohnish-Srivats |  | ? | ? | ? |
| [Clarify workflow span criteria and add reference scenarios for langchain and openai agents (#354)](https://github.com/open-telemetry/semantic-conventions-genai/pull/354) | lmolkova |  | ? | ? | ? |
| [gen-ai: add get_response operation and span (#353)](https://github.com/open-telemetry/semantic-conventions-genai/pull/353) | JacksonWeber |  | ? | ? | ? |
| [Remove inference spans from agentic reference instrumentations, add guidance (#351)](https://github.com/open-telemetry/semantic-conventions-genai/pull/351) | lmolkova |  | ? | ? | ? |
| [Rename gen_ai.workflow.duration to gen_ai.invoke_workflow.duration (#341)](https://github.com/open-telemetry/semantic-conventions-genai/pull/341) | lmolkova |  | ? | ? | ? |
| [\[chore\] Add signal requirement level to yaml and jinja templates (#340)](https://github.com/open-telemetry/semantic-conventions-genai/pull/340) | lmolkova |  | ? | ? | ? |
| [Introduce `gen_ai.invoke_agent.{inference,tool}_calls` (#336)](https://github.com/open-telemetry/semantic-conventions-genai/pull/336) | RKest |  | ? | ? | ? |
| [Migrate anthropic reference scenario to opentelemetry-util-genai (#324)](https://github.com/open-telemetry/semantic-conventions-genai/pull/324) | AgentGymLeader |  | ? | ? | ? |
| [feat(gen-ai): add agent authorization observability attributes (#180) (#291)](https://github.com/open-telemetry/semantic-conventions-genai/pull/291) | thebenignhacker |  | ? | ? | ? |
| [Add GenAI client metrics to the anthropic reference scenario (#283)](https://github.com/open-telemetry/semantic-conventions-genai/pull/283) | AgentGymLeader |  | ? | ? | ? |
| [Add `gen_ai.main_agent` entity (#270)](https://github.com/open-telemetry/semantic-conventions-genai/pull/270) | aabmass |  | ? | ? | ? |
| [gen-ai: add run guardrail span and security finding (#262)](https://github.com/open-telemetry/semantic-conventions-genai/pull/262) | nagkumar91 |  | ? | ? | ? |
| [Add gen_ai.invoke_agent.server span (SERVER kind) (#252)](https://github.com/open-telemetry/semantic-conventions-genai/pull/252) | singankit |  | ? | ? | ? |
| [Add gen_ai.agent.invocation.id attribute for invoke_agent spans (#250)](https://github.com/open-telemetry/semantic-conventions-genai/pull/250) | singankit |  | ? | ? | ? |
| [Add `gen_ai.agent.finish_reason` attribute for agent loop termination (#238)](https://github.com/open-telemetry/semantic-conventions-genai/pull/238) | Nik-Reddy |  | ? | ? | ? |
| [Clarify scope of `gen_ai.client.operation.duration` metric (#215)](https://github.com/open-telemetry/semantic-conventions-genai/pull/215) | trask |  | ? | ? | ? |
| [Add modality, cache, and phase breakdowns for token usage (#197)](https://github.com/open-telemetry/semantic-conventions-genai/pull/197) | trask |  | ? | ? | ? |
| [semconv for a2a protocol (#195)](https://github.com/open-telemetry/semantic-conventions-genai/pull/195) | eternalcuriouslearner |  | ? | ? | ? |
| [Add experimental GenAI context selection event (#190)](https://github.com/open-telemetry/semantic-conventions-genai/pull/190) | caioribeiroclw-pixel |  | ? | ? | ? |

## Draft pull requests

| PR | Author | Updated |
|---|---|:---:|
| [Add time_budget value for gen_ai.agent.finish_reason (#267)](https://github.com/open-telemetry/semantic-conventions-genai/pull/267) | Nik-Reddy | 27d |
| [genai: add `gen_ai.token.cache` and `gen_ai.token.reasoning` metric attributes (#96)](https://github.com/open-telemetry/semantic-conventions-genai/pull/96) | Nik-Reddy | 27d |
| [Add gen_ai.agent.identity.id and gen_ai.agent.identity.name: agent identity distinct from the deployed resource (#350)](https://github.com/open-telemetry/semantic-conventions-genai/pull/350) | AraiYuno | 12d |
| [Add nested workflow flag (#355)](https://github.com/open-telemetry/semantic-conventions-genai/pull/355) | lmolkova | 7d |
| [Add an opaque governance record reference for decision operations (#368)](https://github.com/open-telemetry/semantic-conventions-genai/pull/368) | AgentGymLeader | 1d |

_Approvers may [force a refresh](https://github.com/open-telemetry/shared-workflows/actions/workflows/pull-request-dashboard.yml)._

