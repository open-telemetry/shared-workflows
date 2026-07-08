> [!NOTE]
> Open non-draft PRs grouped by who is expected to act next. Draft PRs are listed separately. The grouping is partly performed by an LLM ([source](https://github.com/open-telemetry/shared-workflows/blob/main/.github/scripts/pull-request-dashboard/dashboard.py)) and could contain mistakes.
>
> Reviewers column: ✅ approved · ✔️ approved (non-code-owner) · 💬 open thread · 🔴 changes requested.

## Waiting on reviewers

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [Make StandardComponentId constructor public (#7763)](https://github.com/open-telemetry/opentelemetry-java/pull/7763) | brunobat | jack-berg | ✅ | ❌ | 252d |
| [Add JSON pretty-print to logging-otlp exporters (#8164)](https://github.com/open-telemetry/opentelemetry-java/pull/8164) | lucacavenaghi97 | jack-berg<br>zeitlinger | ✅ | ❌ | 117d |

## Waiting on authors

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [\[DO NOT MERGE\] JFR API usage (#7741)](https://github.com/open-telemetry/opentelemetry-java/pull/7741) | jhalliday | laurit | ❌ | ✅ | 96d |
| [Add a ConfigProvider callback for runtime instrumentation option changes (#8076)](https://github.com/open-telemetry/opentelemetry-java/pull/8076) | jackshirazi | jack-berg<br>trask | ❌ | ❌ | 22d |

## Waiting on external

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [Add fallback endpoint support for OTLP exporters (#8197)](https://github.com/open-telemetry/opentelemetry-java/pull/8197) | sridharsurvi1 | jack-berg&nbsp;🔴 | ❌ | ❌ | 83d |

## Unknown

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [Update fossas/fossa-action action to v2 (#8577)](https://github.com/open-telemetry/opentelemetry-java/pull/8577) | app/renovate |  | ? | ? | ? |
| [Fix/sampler shutdown lifecycle (#8574)](https://github.com/open-telemetry/opentelemetry-java/pull/8574) | Debashismitra01 |  | ? | ? | ? |
| [Fix OkHttp client mTLS when using the platform default trust store (#8565)](https://github.com/open-telemetry/opentelemetry-java/pull/8565) | Debashismitra01 |  | ? | ? | ? |
| [Add BatchSpanProcessor.create(SpanExporter) to match SimpleSpanProcessor (#8564)](https://github.com/open-telemetry/opentelemetry-java/pull/8564) | jimbobbennett |  | ? | ? | ? |
| [Enforce last-value-wins semantics in AttributesMap without performance regression (#8548)](https://github.com/open-telemetry/opentelemetry-java/pull/8548) | EvgeniiR |  | ? | ? | ? |
| [Replace jackson OTLP json serialization with handrolled version (#8545)](https://github.com/open-telemetry/opentelemetry-java/pull/8545) | jack-berg |  | ? | ? | ? |
| [Bound instruments (#8527)](https://github.com/open-telemetry/opentelemetry-java/pull/8527) | jack-berg |  | ? | ? | ? |
| [Update dependency org.jetbrains.kotlin:kotlin-gradle-plugin to v2.4.0 (#8521)](https://github.com/open-telemetry/opentelemetry-java/pull/8521) | app/renovate |  | ? | ? | ? |
| [Fix serialization of array-valued scope and resource attributes in Prometheus exporter (#8497)](https://github.com/open-telemetry/opentelemetry-java/pull/8497) | thswlsqls |  | ? | ? | ? |
| [Fix Jaeger propagator baggage header case sensitivity (#8496)](https://github.com/open-telemetry/opentelemetry-java/pull/8496) | thswlsqls |  | ? | ? | ? |
| [Standardize OkHttpHttpSender shutdown to await executor termination (#8495)](https://github.com/open-telemetry/opentelemetry-java/pull/8495) | thswlsqls |  | ? | ? | ? |
| [Increase HTTP connectTimeout test threshold to match gRPC (#8494)](https://github.com/open-telemetry/opentelemetry-java/pull/8494) | thswlsqls |  | ? | ? | ? |
| [Fix sign extension on LogRecord flags in low-allocation log marshaler (#8493)](https://github.com/open-telemetry/opentelemetry-java/pull/8493) | thswlsqls |  | ? | ? | ? |
| [Fix Groovy compatibility in OpenTelemetrySdkBuilder (#8467)](https://github.com/open-telemetry/opentelemetry-java/pull/8467) | ADITYA-CODE-SOURCE |  | ? | ? | ? |
| [Entity SDK - Initial opt-in SDK features (#8464)](https://github.com/open-telemetry/opentelemetry-java/pull/8464) | jsuereth |  | ? | ? | ? |
| [Restore compliance between Composite Samplers code and specs (#8450)](https://github.com/open-telemetry/opentelemetry-java/pull/8450) | PeterF778 |  | ? | ? | ? |
| [Enforce OTLP request size limits (#8446)](https://github.com/open-telemetry/opentelemetry-java/pull/8446) | ADITYA-CODE-SOURCE |  | ? | ? | ? |
| [Use HTTP error bodies in HttpExporter warnings (#8428)](https://github.com/open-telemetry/opentelemetry-java/pull/8428) | ADITYA-CODE-SOURCE |  | ? | ? | ? |
| [Fix Groovy OpenTelemetrySdk builder loading (#8407)](https://github.com/open-telemetry/opentelemetry-java/pull/8407) | ADITYA-CODE-SOURCE |  | ? | ? | ? |
| [Merge colliding Prometheus label values (#8364)](https://github.com/open-telemetry/opentelemetry-java/pull/8364) | ADITYA-CODE-SOURCE |  | ? | ? | ? |
| [profiles: improve JFR export example (#8349)](https://github.com/open-telemetry/opentelemetry-java/pull/8349) | jhalliday |  | ? | ? | ? |
| [Replace ArrayBlockingQueue with park/unpark for BatchSpanProcessor$Worker (#8240)](https://github.com/open-telemetry/opentelemetry-java/pull/8240) | Khepu |  | ? | ? | ? |

## Draft pull requests

| PR | Author | Updated |
|---|---|:---:|
| [Allow frameworks to add instrumentation scope conditions (#7312)](https://github.com/open-telemetry/opentelemetry-java/pull/7312) | brunobat | 433d |
| [EntityProvider prototype (#7360)](https://github.com/open-telemetry/opentelemetry-java/pull/7360) | breedx-splk | 383d |
| [Add support to finish instrument recording (#7792)](https://github.com/open-telemetry/opentelemetry-java/pull/7792) | atoulme | 69d |
| [Sketch out ScopedValue based context implementation (#8352)](https://github.com/open-telemetry/opentelemetry-java/pull/8352) | jack-berg | 69d |
| [Null checking applied (#8321)](https://github.com/open-telemetry/opentelemetry-java/pull/8321) | jack-berg | 60d |
| [add declarative config for log throttling (#7838)](https://github.com/open-telemetry/opentelemetry-java/pull/7838) | the-clam | 22d |
| [Fix OpenTelemetrySdkBuilderUtil.setConfigProvider Javadoc copy-paste (#8502)](https://github.com/open-telemetry/opentelemetry-java/pull/8502) | thswlsqls | 6d |
| [Fix Javadoc errors in JFR profiles shim (#8503)](https://github.com/open-telemetry/opentelemetry-java/pull/8503) | thswlsqls | 6d |
| [Reduce LongSumAggregator.doRecordLong visibility to protected (#8507)](https://github.com/open-telemetry/opentelemetry-java/pull/8507) | thswlsqls | 6d |
| [Strengthen graal incubating-not-found test to detect incubator API on classpath (#8510)](https://github.com/open-telemetry/opentelemetry-java/pull/8510) | thswlsqls | 6d |
| [Return null from TracerShim extract when the carrier has no span context (#8505)](https://github.com/open-telemetry/opentelemetry-java/pull/8505) | thswlsqls | 6d |
| [Update documented Kotlin minimum version to 2.2 (#8498)](https://github.com/open-telemetry/opentelemetry-java/pull/8498) | thswlsqls | 6d |
| [Fix SpanLimitsBuilder Javadoc to match non-negative argument check (#8516)](https://github.com/open-telemetry/opentelemetry-java/pull/8516) | thswlsqls | 6d |
| [Fix LongExemplarAssert hasFilteredAttributesSatisfyingExactly to enforce exact attribute matching (#8518)](https://github.com/open-telemetry/opentelemetry-java/pull/8518) | thswlsqls | 6d |
| [Fix ReadWriteLogRecord default getObservedTimestampEpochNanos returning record timestamp (#8504)](https://github.com/open-telemetry/opentelemetry-java/pull/8504) | thswlsqls | 6d |
| [Fix Javadoc and comment in OSGi integration tests (#8500)](https://github.com/open-telemetry/opentelemetry-java/pull/8500) | thswlsqls | 6d |
| [Fix profiles data model attribute count parameter name and timestamp doc unit (#8514)](https://github.com/open-telemetry/opentelemetry-java/pull/8514) | thswlsqls | 6d |
| [Fix stale parameter name in JcTools.drain Javadoc (#8513)](https://github.com/open-telemetry/opentelemetry-java/pull/8513) | thswlsqls | 6d |
| [Fix typos in sdk-common Javadoc (#8512)](https://github.com/open-telemetry/opentelemetry-java/pull/8512) | thswlsqls | 4d |
| [Assert String-key setAttribute overloads in shared default logger test (#8567)](https://github.com/open-telemetry/opentelemetry-java/pull/8567) | thswlsqls | 3d |
| [Remove duplicate getStringList resolver in DeclarativeConfigPropertyUtil (#8572)](https://github.com/open-telemetry/opentelemetry-java/pull/8572) | thswlsqls | 3d |
| [Document experimental type routing in OtelObjectRule Javadoc (#8573)](https://github.com/open-telemetry/opentelemetry-java/pull/8573) | thswlsqls | 3d |
| [Deprecate TextMapGetter keys method (#8531)](https://github.com/open-telemetry/opentelemetry-java/pull/8531) | arnabnandy7 | 2d |
| [Fix Tracer manual context propagation Javadoc example (#8571)](https://github.com/open-telemetry/opentelemetry-java/pull/8571) | thswlsqls | 2d |
| [Preserve OpenCensus status description when converting to OpenTelemetry (#8511)](https://github.com/open-telemetry/opentelemetry-java/pull/8511) | thswlsqls | 1d |

_Approvers may [force a refresh](https://github.com/open-telemetry/shared-workflows/actions/workflows/pull-request-dashboard.yml)._

