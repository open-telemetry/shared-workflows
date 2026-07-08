> [!NOTE]
> Open non-draft PRs grouped by who is expected to act next. Draft PRs are listed separately. The grouping is partly performed by an LLM ([source](https://github.com/open-telemetry/shared-workflows/blob/main/.github/scripts/pull-request-dashboard/dashboard.py)) and could contain mistakes.
>
> Reviewers column: ✅ approved · ✔️ approved (non-code-owner) · 💬 open thread · 🔴 changes requested.

## Waiting on maintainers

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [chore(deps): lock file maintenance (#6559)](https://github.com/open-telemetry/opentelemetry-js/pull/6559) | app/renovate | david-luna&nbsp;✅<br>dyladan<br>legendecas<br>pichlermarc&nbsp;✅<br>trentm | ❌ | ✅ | 93d |
| [fix(opentelemetry-exporter-prometheus)!: default exporter host to localhost (#6599)](https://github.com/open-telemetry/opentelemetry-js/pull/6599) | cjihrig | legendecas&nbsp;✅<br>maryliag<br>pichlermarc | ✅ | ❌ | 83d |
| [docs(configuration): add declarative config example for startNodeSDK() (#6834)](https://github.com/open-telemetry/opentelemetry-js/pull/6834) | MikeGoldsmith | maryliag&nbsp;✅ | ✅ | ✅ | 1d |

## Waiting on reviewers

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [feat(otlp-grpc-exporter): add gRPC channelOptions as config parameter (#6332)](https://github.com/open-telemetry/opentelemetry-js/pull/6332) | vitorvasc | legendecas<br>pichlermarc | ✅ | ✅ | 166d |
| [fix(instrumentation-http): better solution for avoiding double-wrapping of http (#6491)](https://github.com/open-telemetry/opentelemetry-js/pull/6491) | trentm | david-luna<br>maryliag<br>trentm | ✅ | ❌ | 116d |
| [chore(deps): update ubuntu docker tag to v26 (#6635)](https://github.com/open-telemetry/opentelemetry-js/pull/6635) | app/renovate | dyladan<br>legendecas<br>pichlermarc<br>trentm | ✅ | ✅ | 72d |
| [feat(api): add factory pattern for TracerProvider (#6466)](https://github.com/open-telemetry/opentelemetry-js/pull/6466) | ida613 | dyladan | ✅ | ❌ | 65d |
| [feat(otlp-exporter-base): accept `fetch` parameter in `createFetchTransport`, and export `createFetchTransport`, `createRetryingTransport` and `FetchTransportParameters` (#6377)](https://github.com/open-telemetry/opentelemetry-js/pull/6377) | zakcutner | pichlermarc | ✅ | ✅ | 63d |
| [feat(sdk-metrics): add support for max scale for exponential histograms (#6493)](https://github.com/open-telemetry/opentelemetry-js/pull/6493) | andidev | dyladan | ✅ | ✅ | 62d |
| [fix(deps): update opentelemetry-js monorepo to v2 (#6721)](https://github.com/open-telemetry/opentelemetry-js/pull/6721) | app/renovate | dyladan<br>legendecas<br>pichlermarc<br>trentm | ✅ | ✅ | 51d |
| [Add config option for Prometheus default aggregation (#6761)](https://github.com/open-telemetry/opentelemetry-js/pull/6761) | ArthurSens | pichlermarc | ✅ | ✅ | 43d |
| [refactor(build): migrate from tsc to tsdown with dual CJS/ESM exports (#6293)](https://github.com/open-telemetry/opentelemetry-js/pull/6293) | overbalance | david-luna&nbsp;💬<br>pichlermarc<br>raphael-theriault-swi | ❌ | ❌ | 23d |
| [chore(deps): update ubuntu:24.04 docker digest to 4fbb8e6 (#6806)](https://github.com/open-telemetry/opentelemetry-js/pull/6806) | app/renovate | dyladan<br>legendecas<br>pichlermarc<br>trentm | ❌ | ✅ | 22d |
| [feat(sdk-metrics): metrics exemplars support (#6830)](https://github.com/open-telemetry/opentelemetry-js/pull/6830) | rnavarro |  | ✅ | ❌ | 19d |
| [chore(deps): update dependency msw to v2.14.6 (#6831)](https://github.com/open-telemetry/opentelemetry-js/pull/6831) | app/renovate | dyladan<br>legendecas<br>pichlermarc<br>trentm | ❌ | ✅ | 19d |
| [feat(api, context-async): add experimental attach/detach functionality (#6845)](https://github.com/open-telemetry/opentelemetry-js/pull/6845) | pichlermarc | legendecas | ✅ | ❌ | 1d |
| [feat(sdk-node,instrumentation,instrumentation-http,api-config,configuration): add declarative config support for `instrumentation/development` (#6868)](https://github.com/open-telemetry/opentelemetry-js/pull/6868) | mwear |  | ✅ | ❌ | 1h |

## Waiting on authors

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [feat(sdk-metrics): wire exemplar support into metrics pipeline (#6483)](https://github.com/open-telemetry/opentelemetry-js/pull/6483) | CharlieTLe | dyladan&nbsp;💬 | ✅ | ❌ | 120d |
| [perf(sdk-metrics): improve performance of hashAttributes() util (#6515)](https://github.com/open-telemetry/opentelemetry-js/pull/6515) | gunjam | dyladan&nbsp;💬 | ❌ | ❌ | 107d |
| [perf(sdk-trace-base): avoid _getTime for default Span.startTime (#6528)](https://github.com/open-telemetry/opentelemetry-js/pull/6528) | daniellockyer | david-luna&nbsp;💬<br>dyladan | ✅ | ❌ | 100d |
| [feat(sdk): implement exporter metrics (#6480)](https://github.com/open-telemetry/opentelemetry-js/pull/6480) | anuraaga | overbalance&nbsp;💬⁠✔️<br>trentm&nbsp;✅ | ✅ | ❌ | 89d |
| [feat(opentelemetry-exporter-prometheus): add translation strategy support (#6653)](https://github.com/open-telemetry/opentelemetry-js/pull/6653) | cjihrig | ArthurSens&nbsp;💬<br>github-advanced-security&nbsp;💬<br>JacksonWeber&nbsp;💬 | ❌ | ❌ | 65d |
| [fix(instrumentation): lazily initialize require-in-the-middle for empty instrumentations (#6590)](https://github.com/open-telemetry/opentelemetry-js/pull/6590) | biw | pichlermarc | ❌ | ✅ | 58d |
| [fix(otlp-exporter-base): honor env proxy settings (#6660)](https://github.com/open-telemetry/opentelemetry-js/pull/6660) | cyphercodes | pichlermarc<br>raphael-theriault-swi&nbsp;✅<br>trentm&nbsp;💬 | ✅ | ✅ | 53d |
| [docs(otlp-exporter-base): document HTTP exporter options (#6735)](https://github.com/open-telemetry/opentelemetry-js/pull/6735) | macayu17 |  | ✅ | ✅ | 48d |
| [feat(opentelemetry-core,sdk-trace-base,sdk-logs): append exception.cause chain to ATTR_EXCEPTION_STACKTRACE (#6634)](https://github.com/open-telemetry/opentelemetry-js/pull/6634) | abhisheksurve45 | david-luna&nbsp;💬<br>legendecas&nbsp;💬 | ❌ | ❌ | 48d |
| [fix(otlp-exporter-base): surface FetchTransport timeout as clean failure (#6751)](https://github.com/open-telemetry/opentelemetry-js/pull/6751) | devareddy05 | overbalance&nbsp;💬 | ✅ | ✅ | 25d |
| [feat(sdk-logs): implement log processor metrics (#6554)](https://github.com/open-telemetry/opentelemetry-js/pull/6554) | anuraaga | JacksonWeber<br>trentm&nbsp;💬 | ❌ | ❌ | 18d |
| [chore: Add size-limit check on Pull Requests (#6706)](https://github.com/open-telemetry/opentelemetry-js/pull/6706) | JPeer264 | pichlermarc&nbsp;💬 | ❌ | ❌ | 14d |
| [feat(exporter-metrics-otlp-http): respect OTEL_EXPORTER_OTLP_METRICS_DEFAULT_HISTOGRAM_AGGREGATION (#6874)](https://github.com/open-telemetry/opentelemetry-js/pull/6874) | buzztaiki | JacksonWeber | ✅ | ✅ | 22h |

## Waiting on external

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [feat(otlp-exporter-base): make better use of retry timeout (#6260)](https://github.com/open-telemetry/opentelemetry-js/pull/6260) | jsokol805 | pichlermarc | ❌ | ✅ | 112d |

## Unknown

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [feat(propagator-jaeger): deprecate JaegerPropagator (#6893)](https://github.com/open-telemetry/opentelemetry-js/pull/6893) | pichlermarc |  | ? | ? | ? |
| [fix(configuration): apply resource attribute limits (#6891)](https://github.com/open-telemetry/opentelemetry-js/pull/6891) | LarryHu0217 |  | ? | ? | ? |
| [feat(metrics): add experimental advisory attributes support (#6885)](https://github.com/open-telemetry/opentelemetry-js/pull/6885) | AkshitBhandariCodes |  | ? | ? | ? |
| [fix(sdk-metrics): drop stale async metric attribute sets (#6884)](https://github.com/open-telemetry/opentelemetry-js/pull/6884) | AkshitBhandariCodes |  | ? | ? | ? |
| [feat(semantic-conventions): update semantic conventions to v1.43.0 (#6883)](https://github.com/open-telemetry/opentelemetry-js/pull/6883) | trentm |  | ? | ? | ? |
| [docs: create 3.x announcement document (#6881)](https://github.com/open-telemetry/opentelemetry-js/pull/6881) | pichlermarc |  | ? | ? | ? |
| [chore(deps): update dependency &#64;types/webpack-env to v1.18.8 (#6877)](https://github.com/open-telemetry/opentelemetry-js/pull/6877) | app/renovate |  | ? | ? | ? |

## Draft pull requests

| PR | Author | Updated |
|---|---|:---:|
| [fix: cancel retries on shutdown (#6340)](https://github.com/open-telemetry/opentelemetry-js/pull/6340) | pichlermarc | 58d |
| [docs: add threat model document (#6676)](https://github.com/open-telemetry/opentelemetry-js/pull/6676) | pichlermarc | 50d |
| [refactor!: Do not use HrTime in browser instrumentations (#6555)](https://github.com/open-telemetry/opentelemetry-js/pull/6555) | dyladan | 50d |
| [feat(api): Integrate &#64;opentelemetry/api-logs package into &#64;opentelemetry/api as experimental (#4862)](https://github.com/open-telemetry/opentelemetry-js/pull/4862) | hectorhdzg | 38d |
| [PoC: widen 'Attributes' to support the extended AnyValue (#6579)](https://github.com/open-telemetry/opentelemetry-js/pull/6579) | trentm | 36d |
| [feat(api): widen Attributes values type to AnyValue, using unknown TS type (#6780)](https://github.com/open-telemetry/opentelemetry-js/pull/6780) | trentm | 35d |
| [refactor(sdk-node): model built-in exporter resolution on top of PluginComponentProvider spec (#6730)](https://github.com/open-telemetry/opentelemetry-js/pull/6730) | pichlermarc | 30d |
| [feat: add context attach/detach (#6387)](https://github.com/open-telemetry/opentelemetry-js/pull/6387) | pichlermarc | 19d |
| [chore(shim-opencensus): remove the `&#64;opentelemetry/shim-opencensus` package (#6843)](https://github.com/open-telemetry/opentelemetry-js/pull/6843) | trentm | 13d |
| [Entity-Resource prototype v3 (#6357)](https://github.com/open-telemetry/opentelemetry-js/pull/6357) | dyladan | 9d |
| [api: add experimental trace decorator support (#5906)](https://github.com/open-telemetry/opentelemetry-js/pull/5906) | legendecas | 2d |

_Approvers may [force a refresh](https://github.com/open-telemetry/shared-workflows/actions/workflows/pull-request-dashboard.yml)._

