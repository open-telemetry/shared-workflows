> [!NOTE]
> Open non-draft PRs grouped by who is expected to act next. Draft PRs are listed separately. The grouping is partly performed by an LLM ([source](https://github.com/open-telemetry/shared-workflows/blob/main/.github/scripts/pull-request-dashboard/dashboard.py)) and could contain mistakes.
>
> Reviewers column: ✅ approved · ✔️ approved (non-code-owner) · 💬 open thread · 🔴 changes requested.

## Waiting on maintainers

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [chore(deps): lock file maintenance (#6559)](https://github.com/open-telemetry/opentelemetry-js/pull/6559) | app/renovate | david-luna&nbsp;✅<br>dyladan<br>legendecas<br>pichlermarc&nbsp;✅<br>trentm | ❌ | ✅ | 85d |
| [fix(opentelemetry-exporter-prometheus)!: default exporter host to localhost (#6599)](https://github.com/open-telemetry/opentelemetry-js/pull/6599) | cjihrig | legendecas&nbsp;✅<br>maryliag<br>pichlermarc | ✅ | ❌ | 75d |
| [fix(core): guard timeInputToHrTime against clock-skew misclassification (#6772) (#6773)](https://github.com/open-telemetry/opentelemetry-js/pull/6773) | MohammadYusif | JacksonWeber&nbsp;✅ | ✅ | ✅ | 12d |
| [perf(sdk-metrics): optionally capture active context for sync instruments (#6848)](https://github.com/open-telemetry/opentelemetry-js/pull/6848) | legendecas | pichlermarc&nbsp;✅ | ⏳ | ✅ | <1m |

## Waiting on reviewers

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [fix(instrumentation-http): better solution for avoiding double-wrapping of http (#6491)](https://github.com/open-telemetry/opentelemetry-js/pull/6491) | trentm | david-luna<br>maryliag<br>trentm | ✅ | ❌ | 108d |
| [chore(deps): update ubuntu docker tag to v26 (#6635)](https://github.com/open-telemetry/opentelemetry-js/pull/6635) | app/renovate | dyladan<br>legendecas<br>pichlermarc<br>trentm | ✅ | ✅ | 64d |
| [feat(api): add factory pattern for TracerProvider (#6466)](https://github.com/open-telemetry/opentelemetry-js/pull/6466) | ida613 | dyladan | ✅ | ❌ | 56d |
| [feat(otlp-exporter-base): accept `fetch` parameter in `createFetchTransport`, and export `createFetchTransport`, `createRetryingTransport` and `FetchTransportParameters` (#6377)](https://github.com/open-telemetry/opentelemetry-js/pull/6377) | zakcutner | pichlermarc | ✅ | ❌ | 55d |
| [chore(deps): update dependency &#64;types/sinon to v21 (#6693)](https://github.com/open-telemetry/opentelemetry-js/pull/6693) | app/renovate | dyladan<br>legendecas<br>pichlermarc<br>trentm | ❌ | ✅ | 50d |
| [chore(deps): update dependency sinon to v22 (#6720)](https://github.com/open-telemetry/opentelemetry-js/pull/6720) | app/renovate | dyladan<br>legendecas<br>pichlermarc<br>trentm | ❌ | ✅ | 43d |
| [fix(deps): update opentelemetry-js monorepo to v2 (#6721)](https://github.com/open-telemetry/opentelemetry-js/pull/6721) | app/renovate | dyladan<br>legendecas<br>pichlermarc<br>trentm | ❌ | ✅ | 43d |
| [Add config option for Prometheus default aggregation (#6761)](https://github.com/open-telemetry/opentelemetry-js/pull/6761) | ArthurSens |  | ✅ | ❌ | 35d |
| [chore(deps): update ubuntu:24.04 docker digest to 786a8b5 (#6806)](https://github.com/open-telemetry/opentelemetry-js/pull/6806) | app/renovate | dyladan<br>legendecas<br>pichlermarc<br>trentm | ⏳ | ✅ | 14d |
| [feat(api): add setClock and getTick methods to ContextAPI (#6816) (#6820)](https://github.com/open-telemetry/opentelemetry-js/pull/6820) | ipsitapp8 |  | ✅ | ✅ | 12d |
| [feat(sdk-metrics): metrics exemplars support (#6830)](https://github.com/open-telemetry/opentelemetry-js/pull/6830) | rnavarro |  | ✅ | ❌ | 11d |
| [chore(deps): update dependency msw to v2.14.6 (#6831)](https://github.com/open-telemetry/opentelemetry-js/pull/6831) | app/renovate | dyladan<br>legendecas<br>pichlermarc<br>trentm | ❌ | ✅ | 11d |

## Waiting on authors

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [feat(sdk-metrics): wire exemplar support into metrics pipeline (#6483)](https://github.com/open-telemetry/opentelemetry-js/pull/6483) | CharlieTLe | dyladan&nbsp;💬 | ✅ | ❌ | 111d |
| [feat(sdk-metrics): add support for max scale for exponential histograms (#6493)](https://github.com/open-telemetry/opentelemetry-js/pull/6493) | andidev | dyladan&nbsp;💬 | ✅ | ❌ | 98d |
| [perf(sdk-metrics): improve performance of hashAttributes() util (#6515)](https://github.com/open-telemetry/opentelemetry-js/pull/6515) | gunjam | dyladan&nbsp;💬 | ❌ | ❌ | 98d |
| [perf(sdk-trace-base): avoid _getTime for default Span.startTime (#6528)](https://github.com/open-telemetry/opentelemetry-js/pull/6528) | daniellockyer | david-luna&nbsp;💬<br>dyladan | ✅ | ❌ | 92d |
| [feat(sdk): implement exporter metrics (#6480)](https://github.com/open-telemetry/opentelemetry-js/pull/6480) | anuraaga | overbalance&nbsp;💬⁠✔️<br>trentm&nbsp;✅ | ✅ | ❌ | 81d |
| [feat(opentelemetry-exporter-prometheus): add translation strategy support (#6653)](https://github.com/open-telemetry/opentelemetry-js/pull/6653) | cjihrig | ArthurSens&nbsp;💬<br>github-advanced-security&nbsp;💬<br>JacksonWeber&nbsp;💬 | ❌ | ❌ | 57d |
| [fix(instrumentation): lazily initialize require-in-the-middle for empty instrumentations (#6590)](https://github.com/open-telemetry/opentelemetry-js/pull/6590) | biw | pichlermarc | ❌ | ✅ | 50d |
| [fix(otlp-exporter-base): honor env proxy settings (#6660)](https://github.com/open-telemetry/opentelemetry-js/pull/6660) | cyphercodes | pichlermarc<br>raphael-theriault-swi&nbsp;✅<br>trentm&nbsp;💬 | ✅ | ✅ | 45d |
| [docs(otlp-exporter-base): document HTTP exporter options (#6735)](https://github.com/open-telemetry/opentelemetry-js/pull/6735) | macayu17 |  | ✅ | ✅ | 40d |
| [feat(opentelemetry-core,sdk-trace-base,sdk-logs): append exception.cause chain to ATTR_EXCEPTION_STACKTRACE (#6634)](https://github.com/open-telemetry/opentelemetry-js/pull/6634) | abhisheksurve45 | david-luna&nbsp;💬<br>legendecas&nbsp;💬 | ❌ | ❌ | 39d |
| [feat(otlp-grpc-exporter): add gRPC channelOptions as config parameter (#6332)](https://github.com/open-telemetry/opentelemetry-js/pull/6332) | vitorvasc | legendecas&nbsp;💬<br>pichlermarc | ✅ | ✅ | 25d |
| [fix(otlp-exporter-base): surface FetchTransport timeout as clean failure (#6751)](https://github.com/open-telemetry/opentelemetry-js/pull/6751) | devareddy05 | overbalance&nbsp;💬 | ✅ | ✅ | 17d |
| [refactor(build): migrate from tsc to tsdown with dual CJS/ESM exports (#6293)](https://github.com/open-telemetry/opentelemetry-js/pull/6293) | overbalance | david-luna&nbsp;💬<br>pichlermarc<br>raphael-theriault-swi&nbsp;💬 | ✅ | ❌ | 14d |
| [docs(configuration): add declarative config example for startNodeSDK() (#6834)](https://github.com/open-telemetry/opentelemetry-js/pull/6834) | MikeGoldsmith | maryliag&nbsp;💬 | ✅ | ❌ | 10d |
| [feat(sdk-logs): implement log processor metrics (#6554)](https://github.com/open-telemetry/opentelemetry-js/pull/6554) | anuraaga | JacksonWeber<br>trentm&nbsp;💬 | ❌ | ❌ | 10d |
| [chore: Add size-limit check on Pull Requests (#6706)](https://github.com/open-telemetry/opentelemetry-js/pull/6706) | JPeer264 | pichlermarc&nbsp;💬 | ❌ | ❌ | 5d |
| [feat(api, context-async): add experimental attach/detach functionality (#6845)](https://github.com/open-telemetry/opentelemetry-js/pull/6845) | pichlermarc | legendecas&nbsp;💬 | ✅ | ❌ | 3d |
| [feat(sdk-node): wire up tracer_provider.sampler from declarative config (#6847)](https://github.com/open-telemetry/opentelemetry-js/pull/6847) | MikeGoldsmith | trentm&nbsp;💬⁠✅ | ✅ | ✅ | 3d |
| [feat(sdk-trace-web,fetch,grpc,http,xml-http-request): only emit stable http metrics, spans and attributes (#6819)](https://github.com/open-telemetry/opentelemetry-js/pull/6819) | maryliag | JacksonWeber&nbsp;✅<br>pichlermarc&nbsp;✅ | ✅ | ✅ | 6m |

## Waiting on external

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [feat(otlp-exporter-base): make better use of retry timeout (#6260)](https://github.com/open-telemetry/opentelemetry-js/pull/6260) | jsokol805 | pichlermarc | ❌ | ✅ | 103d |

## Draft pull requests

| PR | Author | Updated |
|---|---|:---:|
| [api: add experimental trace decorator support (#5906)](https://github.com/open-telemetry/opentelemetry-js/pull/5906) | legendecas | 90d |
| [fix: cancel retries on shutdown (#6340)](https://github.com/open-telemetry/opentelemetry-js/pull/6340) | pichlermarc | 50d |
| [docs: add threat model document (#6676)](https://github.com/open-telemetry/opentelemetry-js/pull/6676) | pichlermarc | 42d |
| [refactor!: Do not use HrTime in browser instrumentations (#6555)](https://github.com/open-telemetry/opentelemetry-js/pull/6555) | dyladan | 42d |
| [feat(api): Integrate &#64;opentelemetry/api-logs package into &#64;opentelemetry/api as experimental (#4862)](https://github.com/open-telemetry/opentelemetry-js/pull/4862) | hectorhdzg | 30d |
| [PoC: widen 'Attributes' to support the extended AnyValue (#6579)](https://github.com/open-telemetry/opentelemetry-js/pull/6579) | trentm | 28d |
| [feat(api): widen Attributes values type to AnyValue, using unknown TS type (#6780)](https://github.com/open-telemetry/opentelemetry-js/pull/6780) | trentm | 27d |
| [refactor(sdk-node): model built-in exporter resolution on top of PluginComponentProvider spec (#6730)](https://github.com/open-telemetry/opentelemetry-js/pull/6730) | pichlermarc | 22d |
| [feat: add context attach/detach (#6387)](https://github.com/open-telemetry/opentelemetry-js/pull/6387) | pichlermarc | 11d |
| [chore(shim-opencensus): remove the `&#64;opentelemetry/shim-opencensus` package (#6843)](https://github.com/open-telemetry/opentelemetry-js/pull/6843) | trentm | 5d |
| [Entity-Resource prototype v3 (#6357)](https://github.com/open-telemetry/opentelemetry-js/pull/6357) | dyladan | 1d |

_Approvers may [force a refresh](https://github.com/open-telemetry/shared-workflows/actions/workflows/pull-request-dashboard.yml)._

