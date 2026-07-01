> [!NOTE]
> Open non-draft PRs grouped by who is expected to act next. Draft PRs are listed separately. The grouping is partly performed by an LLM ([source](https://github.com/open-telemetry/shared-workflows/blob/main/.github/scripts/pull-request-dashboard/dashboard.py)) and could contain mistakes.
>
> Reviewers column: ✅ approved · ✔️ approved (non-code-owner) · 💬 open thread · 🔴 changes requested.

## Waiting on reviewers

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [chore(deps): update dependency &#64;rollup/plugin-commonjs to v29 - manually fixed (#3200)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3200) | app/renovate | dyladan<br>legendecas<br>martinkuba<br>overbalance&nbsp;✔️<br>pichlermarc<br>pkanal<br>trentm<br>wolfgangcodes | ⏳ | ❌ | 243d |
| [feat(instrumentation-ioredis): add Redis Cluster instrumentation support (#3010)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3010) | PavelPashov | blumamir<br>naseemkullah<br>pichlermarc | ✅ | ❌ | 237d |
| [chore(deps): update dependency expect to v30 (#3213)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3213) | app/renovate | blumamir<br>dyladan<br>JamieDanielson<br>jj22ee<br>legendecas<br>maryliag<br>MikeGoldsmith<br>mottibec<br>pichlermarc<br>trentm | ❌ | ✅ | 236d |
| [chore(deps): update dependency chai to v6 (#3276)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3276) | app/renovate | dyladan<br>legendecas<br>martinkuba<br>pichlermarc<br>pkanal<br>trentm<br>wolfgangcodes | ❌ | ✅ | 208d |
| [fix(instrumentation-ioredis): correctly mark MULTI/PIPELINE in operation name (#3278)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3278) | aryamohanan | blumamir<br>naseemkullah<br>pichlermarc | ✅ | ✅ | 207d |
| [chore(deps): update dependency mongodb to v7 (#3419)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3419) | app/renovate | dyladan<br>legendecas<br>onurtemizkan<br>pichlermarc<br>trentm | ❌ | ✅ | 117d |
| [chore(deps): update dependency node to v24 (#3420)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3420) | app/renovate | dyladan<br>legendecas<br>pichlermarc<br>trentm&nbsp;💬 | ✅ | ✅ | 117d |
| [fix(graphql): rewrap late-added resolvers (#3447)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3447) | Farhan-Abbas | obecny | ❌ | ✅ | 103d |
| [feat(instrumentation-aws-sdk): inject trace context into Kinesis PutR… (#3433)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3433) | mosheshaham-dash0 | blumamir<br>jj22ee<br>trivikr | ✅ | ✅ | 98d |
| [feat(detector-aws): detect Lambda availability zone metadata (#3460)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3460) | garysassano | jj22ee | ✅ | ✅ | 93d |
| [chore(deps): update dependency &#64;nestjs/core to v11.1.18 \[security\] (#3468)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3468) | app/renovate | dyladan<br>legendecas<br>neilime<br>pichlermarc<br>trentm | ❌ | ✅ | 85d |
| [chore(deps): update dependency babel-plugin-istanbul to v8 (#3502)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3502) | app/renovate | dyladan<br>legendecas<br>pichlermarc<br>trentm | ❌ | ✅ | 61d |
| [feat(deps): lock file maintenance (#3538)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3538) | app/renovate | dyladan<br>legendecas<br>pichlermarc<br>trentm | ❌ | ✅ | 40d |
| [chore: release main (#3568)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3568) | app/otelbot-js-contrib | blumamir<br>d4nyll<br>dyladan<br>hectorhdzg<br>JamieDanielson<br>legendecas<br>pichlermarc<br>pkanal<br>raphael-theriault-swi<br>yiyuan-he | ⏳ | ✅ | 20d |
| [chore(deps): update dependency &#64;cucumber/cucumber to v13 (#3575)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3575) | app/renovate | dyladan<br>legendecas<br>pichlermarc<br>trentm<br>Ugzuzg | ❌ | ✅ | 12d |
| [fix(instrumentation-user-interaction): use WeakMap for per-element listener bookkeeping (#3576)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3576) | Renegade2345 | obecny | ✅ | ✅ | 10d |
| [chore(deps): update babel monorepo to v8 (#3580)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3580) | app/renovate | dyladan<br>legendecas<br>pichlermarc<br>trentm | ❌ | ✅ | 5d |
| [chore(eslint): use canonical n/ prefix for eslint-plugin-n in flat config (#3581)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3581) | bhuvan-somisetty |  | ✅ | ✅ | 21h |
| [chore: Move inactive members to emeritus (#3587)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3587) | opentelemetrybot |  | ✅ | ✅ | 7h |
| [feat(instrumentation-oracledb): add support for oracledb v7 (#3583)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3583) | abhilash-sivan | sharadraju&nbsp;💬<br>sudarshan12s&nbsp;💬 | ❌ | ✅ | 4h |

## Waiting on authors

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [feat(instrumentation-amqp): adds latest semantic conventions (#2976)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/2976) | lucas-gregoire | blumamir<br>JamieDanielson<br>pichlermarc<br>trentm&nbsp;🔴 | ❌ | ✅ | 321d |
| [ci: Update Renovate configuration to best practices (#3231)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3231) | thompson-tomo | pichlermarc&nbsp;🔴 | ✅ | ✅ | 222d |
| [feat(instr-runtime-node): add configurable gcDurationBuckets option (#3328)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3328) | lukeramsden | d4nyll<br>david-luna&nbsp;💬<br>maryliag&nbsp;💬<br>pichlermarc<br>trentm | ❌ | ❌ | 162d |
| [feat: add neo4j instrumentation (#3380)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3380) | t2t2 | mhennoch<br>seemk&nbsp;💬<br>t2t2<br>thompson-tomo&nbsp;💬 | ✅ | ❌ | 138d |
| [fix(instrumentation-sequelize): do not include 'server.address' for SQLite DB spans (#3436)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3436) | trentm | maryliag&nbsp;💬<br>mhennoch<br>seemk&nbsp;💬<br>t2t2<br>trentm | ✅ | ✅ | 109d |
| [fix(instrumentation-pino): Allow control over logged fields (#3356)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3356) | jnloureiro | dyladan&nbsp;💬<br>seemk | ✅ | ✅ | 76d |
| [feat(resource-detector-aws): read cloud.account.id from symlink in Lambda detector (#3377)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3377) | RaphaelManke | dyladan<br>jj22ee | ✅ | ✅ | 76d |
| [feat(nestjs): add instrumentation for NestJS microservice (#3435)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3435) | neilime | blumamir<br>david-luna<br>dyladan<br>legendecas<br>neilime<br>pichlermarc | ❌ | ❌ | 63d |
| [fix(instrumentation-aws-lambda): handle non-configurable exports from esbuild CJS bundles (#3422)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3422) | RaphaelManke | jj22ee&nbsp;💬<br>raphael-theriault-swi | ✅ | ✅ | 62d |
| [feat(auto-instrumentations-node): opt-in crash flush via OTEL_NODE_FLUSH_ON_CRASH (#3505)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3505) | ElfoLiNk | blumamir<br>dyladan<br>legendecas<br>pichlermarc<br>raphael-theriault-swi | ✅ | ✅ | 56d |
| [fix(instrumentation-knex): use resolved connection settings (#3493)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3493) | Genmin | david-luna | ❌ | ✅ | 9d |
| [feat(several): only emit stable http, network and database attributes (#3585)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3585) | maryliag | blumamir<br>jj22ee<br>martinkuba<br>maryliag<br>naseemkullah<br>neilime<br>onurtemizkan<br>pkanal<br>raphael-theriault-swi<br>seemk | ✅ | ✅ | 12h |
| [fix(instrumentation-knex): use connectionSettings to support function-based connection configs (#3584)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3584) | bhuvan-somisetty | david-luna | ⏳ | ✅ | 2m |

## Draft pull requests

| PR | Author | Updated |
|---|---|:---:|
| [test(auto-instrumentations-node): reproduce blocking process termination (#3348)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3348) | basti1302 | 162d |
| [fix(instrumentation-pg): correct `db.client.connection.count` metric counting to handle multiple pg pools (#3367)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3367) | trentm | 152d |
| [refactor(sampler-aws-xray): change internals to be promise-based to improve test stability (#3396)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3396) | pichlermarc | 131d |
| [chore: add a compile cache server for builds (#3281)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3281) | david-luna | 84d |
| [feat(instrumentation-nats): add instrumentation nats package (#3352)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3352) | giacomoquinalia | 58d |
| [Feature/kafka cluster (#3577)](https://github.com/open-telemetry/opentelemetry-js-contrib/pull/3577) | shashank-reddy-nr | 2d |

_Approvers may [force a refresh](https://github.com/open-telemetry/shared-workflows/actions/workflows/pull-request-dashboard.yml)._

