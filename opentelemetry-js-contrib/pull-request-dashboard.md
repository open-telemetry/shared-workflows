> [!NOTE]
> Open non-draft PRs grouped by who is expected to act next. Draft PRs are listed separately. The grouping is partly performed by an LLM ([source](https://github.com/open-telemetry/shared-workflows/blob/main/.github/scripts/pull-request-dashboard/dashboard.py)) and could contain mistakes.
>
> Reviewers column: ✅ approved · ✔️ approved (non-code-owner) · 💬 open thread · 🔴 changes requested.

## Waiting on reviewers

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [chore(deps): update dependency &#64;rollup/plugin-commonjs to v29 - manually fixed (#3200)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3200) | app/renovate | dyladan<br>legendecas<br>martinkuba<br>overbalance&nbsp;✔️<br>pichlermarc<br>pkanal<br>trentm<br>wolfgangcodes | ⏳ | ❌ | 242d |
| [feat(instrumentation-ioredis): add Redis Cluster instrumentation support (#3010)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3010) | PavelPashov | blumamir<br>naseemkullah<br>pichlermarc | ✅ | ❌ | 236d |
| [chore(deps): update dependency expect to v30 (#3213)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3213) | app/renovate | blumamir<br>dyladan<br>JamieDanielson<br>jj22ee<br>legendecas<br>maryliag<br>MikeGoldsmith<br>mottibec<br>pichlermarc<br>trentm | ❌ | ✅ | 235d |
| [chore(deps): update dependency chai to v6 (#3276)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3276) | app/renovate | dyladan<br>legendecas<br>martinkuba<br>pichlermarc<br>pkanal<br>trentm<br>wolfgangcodes | ❌ | ✅ | 207d |
| [fix(instrumentation-ioredis): correctly mark MULTI/PIPELINE in operation name (#3278)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3278) | aryamohanan | blumamir<br>naseemkullah<br>pichlermarc | ✅ | ✅ | 207d |
| [chore(deps): update dependency mongodb to v7 (#3419)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3419) | app/renovate | dyladan<br>legendecas<br>onurtemizkan<br>pichlermarc<br>trentm | ❌ | ✅ | 116d |
| [chore(deps): update dependency node to v24 (#3420)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3420) | app/renovate | dyladan<br>legendecas<br>pichlermarc<br>trentm&nbsp;💬 | ✅ | ✅ | 116d |
| [fix(graphql): rewrap late-added resolvers (#3447)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3447) | Farhan-Abbas | obecny | ❌ | ✅ | 102d |
| [feat(instrumentation-aws-sdk): inject trace context into Kinesis PutR… (#3433)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3433) | mosheshaham-dash0 | blumamir<br>jj22ee<br>trivikr | ✅ | ✅ | 97d |
| [feat(detector-aws): detect Lambda availability zone metadata (#3460)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3460) | garysassano | jj22ee | ✅ | ✅ | 92d |
| [chore(deps): update dependency &#64;nestjs/core to v11.1.18 \[security\] (#3468)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3468) | app/renovate | dyladan<br>legendecas<br>neilime<br>pichlermarc<br>trentm | ❌ | ✅ | 84d |
| [chore(deps): update dependency babel-plugin-istanbul to v8 (#3502)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3502) | app/renovate | dyladan<br>legendecas<br>pichlermarc<br>trentm | ❌ | ✅ | 60d |
| [fix(instrumentation-express): prevent post-handler middleware from overwriting rpcMetadata.route (#3521)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3521) | Luffy-nani | JamieDanielson<br>pkanal<br>raphael-theriault-swi | ✅ | ✅ | 46d |
| [feat(deps): lock file maintenance (#3538)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3538) | app/renovate | dyladan<br>legendecas<br>pichlermarc<br>trentm | ❌ | ✅ | 40d |
| [chore: release main (#3568)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3568) | app/otelbot-js-contrib | blumamir<br>d4nyll<br>dyladan<br>hectorhdzg<br>JamieDanielson<br>legendecas<br>pichlermarc<br>pkanal<br>raphael-theriault-swi<br>yiyuan-he | ✅ | ✅ | 19d |
| [chore(deps): update dependency &#64;cucumber/cucumber to v13 (#3575)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3575) | app/renovate | dyladan<br>legendecas<br>pichlermarc<br>trentm<br>Ugzuzg | ❌ | ✅ | 11d |
| [fix(instrumentation-user-interaction): use WeakMap for per-element listener bookkeeping (#3576)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3576) | Renegade2345 | obecny | ✅ | ✅ | 9d |
| [chore(deps): update babel monorepo to v8 (#3580)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3580) | app/renovate | dyladan<br>legendecas<br>pichlermarc<br>trentm | ❌ | ✅ | 4d |
| [chore(eslint): use canonical n/ prefix for eslint-plugin-n in flat config (#3581)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3581) | bhuvan-somisetty |  | ✅ | ✅ | 22h |
| [fix(instrumentation-koa): use fallback name for anonymous middleware spans (#3582)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3582) | bhuvan-somisetty |  | ✅ | ✅ | 22h |
| [fix(instrumentation-knex): use connectionSettings to support function-based connection configs (#3584)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3584) | bhuvan-somisetty |  | ✅ | ✅ | 6h |

## Waiting on authors

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [feat(instrumentation-amqp): adds latest semantic conventions (#2976)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/2976) | lucas-gregoire | blumamir<br>JamieDanielson<br>pichlermarc<br>trentm&nbsp;🔴 | ❌ | ✅ | 320d |
| [feat(instrumentation-openai): support Responses API (#3194)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3194) | btomaj | hectorhdzg&nbsp;💬<br>JacksonWeber&nbsp;✅<br>pichlermarc<br>raphael-theriault-swi<br>seemk<br>trentm&nbsp;✅ | ❌ | ✅ | 227d |
| [ci: Update Renovate configuration to best practices (#3231)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3231) | thompson-tomo | pichlermarc&nbsp;🔴 | ✅ | ✅ | 221d |
| [feat(instr-runtime-node): add configurable gcDurationBuckets option (#3328)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3328) | lukeramsden | d4nyll<br>david-luna&nbsp;💬<br>maryliag&nbsp;💬<br>pichlermarc<br>trentm | ❌ | ❌ | 161d |
| [feat: add neo4j instrumentation (#3380)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3380) | t2t2 | mhennoch<br>seemk&nbsp;💬<br>t2t2<br>thompson-tomo&nbsp;💬 | ✅ | ❌ | 137d |
| [fix(instrumentation-sequelize): do not include 'server.address' for SQLite DB spans (#3436)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3436) | trentm | maryliag&nbsp;💬<br>mhennoch<br>seemk&nbsp;💬<br>t2t2<br>trentm | ✅ | ✅ | 108d |
| [fix(instrumentation-pino): Allow control over logged fields (#3356)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3356) | jnloureiro | dyladan&nbsp;💬<br>seemk | ✅ | ✅ | 75d |
| [feat(resource-detector-aws): read cloud.account.id from symlink in Lambda detector (#3377)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3377) | RaphaelManke | dyladan<br>jj22ee | ✅ | ✅ | 75d |
| [feat(nestjs): add instrumentation for NestJS microservice (#3435)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3435) | neilime | blumamir<br>david-luna<br>dyladan<br>legendecas<br>neilime<br>pichlermarc | ❌ | ❌ | 62d |
| [fix(instrumentation-aws-lambda): handle non-configurable exports from esbuild CJS bundles (#3422)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3422) | RaphaelManke | jj22ee&nbsp;💬<br>raphael-theriault-swi | ✅ | ✅ | 61d |
| [feat(auto-instrumentations-node): opt-in crash flush via OTEL_NODE_FLUSH_ON_CRASH (#3505)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3505) | ElfoLiNk | blumamir<br>dyladan<br>legendecas<br>pichlermarc<br>raphael-theriault-swi | ✅ | ✅ | 55d |
| [fix(instrumentation-knex): use resolved connection settings (#3493)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3493) | Genmin | david-luna | ❌ | ✅ | 8d |

## Waiting on external

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [feat(instrumentation-oracledb): add support for oracledb v7 (#3583)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3583) | abhilash-sivan | sharadraju&nbsp;💬<br>sudarshan12s | ❌ | ✅ | 1h |

## Draft pull requests

| PR | Author | Updated |
|---|---|:---:|
| [test(auto-instrumentations-node): reproduce blocking process termination (#3348)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3348) | basti1302 | 161d |
| [fix(instrumentation-pg): correct `db.client.connection.count` metric counting to handle multiple pg pools (#3367)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3367) | trentm | 151d |
| [refactor(sampler-aws-xray): change internals to be promise-based to improve test stability (#3396)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3396) | pichlermarc | 130d |
| [chore: add a compile cache server for builds (#3281)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3281) | david-luna | 83d |
| [feat(instrumentation-nats): add instrumentation nats package (#3352)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3352) | giacomoquinalia | 57d |
| [Feature/kafka cluster (#3577)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3577) | shashank-reddy-nr | 1d |

<details>
<summary>Diagnostics</summary>

```text
PR #3583
llm: PRRT_kwDOD0IL486NNEfo -> external (The author already answered the naming change and is waiting on a decision about `pdbName` from another person not participating in the thread, so the thread is blocked on an outside confirmation.)

PR #3582
llm: pr-conversation -> reviewer (The author asked for a sponsor to review and merge, so the next action is on a reviewer/maintainer.)

PR #3521
llm: pr-conversation -> reviewer (The only comment is from a reviewer and asks reviewers to look into prior history before proceeding, so the next action stays with reviewers.)

PR #3505
llm: pr-conversation -> author (The latest comment is from a reviewer/approver and references another PR/issue, so the author still needs to respond or act.)

PR #3493
llm: pr-conversation -> author (The latest reviewer comment says the CLA is still missing and asks the author to sign it, so the author needs to act next.)

PR #3436
llm: PRRT_kwDOD0IL4850SKfT -> author (A reviewer asked whether the comment should stay, so the author needs to जवाब/respond or revise it.)
llm: PRRT_kwDOD0IL4853tHyx -> author (The reviewer is asking for a code change (`:memory` -> `:memory:`), so the PR author needs to respond by updating the implementation or explaining why not.)
llm: pr-conversation -> external (The author says they’ve asked about this in SemConv Slack, so the thread is waiting on an external response outside the repository.)

PR #3435
llm: pr-conversation -> author (The latest comment is a reviewer asking for feedback on the proposed split/deprecation plan, so the PR author needs to respond.)

PR #3422
llm: PRRT_kwDOD0IL485-n0WD -> author (A reviewer asked for a code change and there was no author reply, so the PR author needs to act.)
llm: pr-conversation -> reviewer (The author asked a question and no reviewer has जवाबd yet, so the next action is on the reviewer to clarify what instrumentation is meant.)

PR #3420
llm: PRRT_kwDOD0IL485zIKQN -> author (The last comment is from a reviewer and raises a workaround suggestion tied to an external issue, so the PR author should respond or act on it.)

PR #3380
llm: PRRT_kwDOD0IL485uebWd -> author (A reviewer requested adding `server.address`/`server.port`, and there’s no author reply yet, so the author needs to act.)
llm: PRRT_kwDOD0IL485zVrDa -> reviewer (The last comment is from the author and asks back whether the readme text is still valid across many packages, so the reviewer/maintainer needs to जवाब or confirm next.)
llm: PRRT_kwDOD0IL4856J30_ -> author (The last comment is a reviewer making a substantive point about span name construction, so the author needs to respond or adjust the PR.)

PR #3377
llm: pr-conversation -> author (The reviewer asked for documentation/spec confirmation and raised a concern; the author needs to जवाब/clarify before the thread can close.)

PR #3356
llm: PRRT_kwDOD0IL4857MDI7 -> author (A reviewer suggested changing the type to `string | null`, so the PR author needs to respond or make that update.)
llm: PRRT_kwDOD0IL4857MEyK -> author (A reviewer asked whether the feature is related and suggested it may belong in a separate PR, so the author needs to respond or adjust the PR.)

PR #3328
llm: PRRT_kwDOD0IL485pjD_O -> author (The latest reviewer/approver comment proposes a test strategy and asks for the author’s opinion, so the author needs to respond or implement a follow-up.)
llm: pr-conversation -> author (A reviewer asked the PR author whether they had time to review the latest comments, so the author needs to respond.)

PR #3276
llm: pr-conversation -> author (The reviewer left a CHANGES_REQUESTED comment and there is no author follow-up yet, so the author needs to respond or update the PR.)

PR #3231
llm: pr-conversation -> author (The latest comment is from a reviewer/approver pushing back and explaining why the change is unnecessary, so the author needs to respond or adjust the PR.)

PR #3200
llm: pr-conversation -> none (The only comment is a reviewer note stating the change fixes the issue and makes #3276 redundant, with no explicit follow-up requested.)

PR #3194
llm: PRRT_kwDOD0IL485hkbTm -> author (The last comment is a reviewer asking for a TS union type instead of a raw string, so the author needs to make or address that change.)
llm: pr-conversation -> author (The reviewer asked for any additional feedback or thoughts and indicated they may merge soon, so the author is the one expected to respond next.)

PR #3010
llm: pr-conversation -> reviewer (The only comment is from a reviewer cc’ing component owners, so the next step is for reviewers/maintainers to respond or review.)

PR #2976
llm: PRRT_kwDOD0IL485XchqF -> author (The reviewer asked for a code change: move to a local `src/semconv.ts` file instead of using the `/incubating` entry point, so the PR author needs to update the implementation.)
llm: pr-conversation -> external (The last reviewer comment says acceptance depends on a semconv-repo proposal and newer experimental semconv support, so progress is blocked outside this repository.)

```

</details>

_Approvers may [force a refresh](https://github.com/open-telemetry/shared-workflows/actions/workflows/pull-request-dashboard.yml)._

