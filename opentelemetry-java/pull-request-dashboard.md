> [!NOTE]
> Open non-draft PRs grouped by who is expected to act next. Draft PRs are listed separately. The grouping is partly performed by an LLM ([source](https://github.com/open-telemetry/shared-workflows/blob/main/.github/scripts/pull-request-dashboard/dashboard.py)) and could contain mistakes.
>
> Reviewers column: ✅ approved · ✔️ approved (non-code-owner) · 💬 open thread · 🔴 changes requested.

## Waiting on maintainers

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [EnvironmentGetter and EnvironmentSetter empty name normalization (#8481)](https://github.com/open-telemetry/opentelemetry-java/pull/8481) | pellared | jack-berg&nbsp;✅<br>jkwatson&nbsp;🔴<br>zeitlinger&nbsp;✅ | ✅ | ✅ | 3d |
| [Restore compliance between Composite Samplers code and specs (#8450)](https://github.com/open-telemetry/opentelemetry-java/pull/8450) | PeterF778 | jack-berg&nbsp;✅<br>jkwatson<br>zeitlinger | ❌ | ✅ | 3d |
| [Fix baggage parsing for invalid percent-encoded members (#8480)](https://github.com/open-telemetry/opentelemetry-java/pull/8480) | Vcode2407 | jack-berg&nbsp;✅<br>psx95&nbsp;✅<br>zeitlinger&nbsp;✅ | ✅ | ✅ | 2d |
| [Use failExceptionally in PeriodicMetricReader when exporter is busy (#8525)](https://github.com/open-telemetry/opentelemetry-java/pull/8525) | vivekp14 | jack-berg&nbsp;✅ | ✅ | ✅ | 44m |
| [Remove PR dashboard workflow (#8537)](https://github.com/open-telemetry/opentelemetry-java/pull/8537) | trask | jack-berg&nbsp;✅<br>jaydeluca&nbsp;✅<br>psx95&nbsp;✅ | ✅ | ✅ | 41m |

## Waiting on reviewers

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [Make StandardComponentId constructor public (#7763)](https://github.com/open-telemetry/opentelemetry-java/pull/7763) | brunobat | jack-berg | ✅ | ❌ | 239d |
| [Add JSON pretty-print to logging-otlp exporters (#8164)](https://github.com/open-telemetry/opentelemetry-java/pull/8164) | lucacavenaghi97 | jack-berg<br>zeitlinger | ✅ | ❌ | 104d |
| [Fix Groovy compatibility in OpenTelemetrySdkBuilder (#8467)](https://github.com/open-telemetry/opentelemetry-java/pull/8467) | ADITYA-CODE-SOURCE | psx95 | ✅ | ❌ | 15d |
| [profiles: improve JFR export example (#8349)](https://github.com/open-telemetry/opentelemetry-java/pull/8349) | jhalliday | zeitlinger | ✅ | ✅ | 3d |
| [Update dependency org.jetbrains.kotlin:kotlin-gradle-plugin to v2.4.0 (#8521)](https://github.com/open-telemetry/opentelemetry-java/pull/8521) | app/renovate |  | ❌ | ✅ | 2d |
| [Bound instruments (#8527)](https://github.com/open-telemetry/opentelemetry-java/pull/8527) | jack-berg |  | ❌ | ✅ | 2d |
| [FIx for BSP benchmark aux counters (exportedSpans/droppedSpans) always reporting zero (#8539)](https://github.com/open-telemetry/opentelemetry-java/pull/8539) | EvgeniiR |  | ⏳ | ✅ | 17m |

## Waiting on authors

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [\[DO NOT MERGE\] JFR API usage (#7741)](https://github.com/open-telemetry/opentelemetry-java/pull/7741) | jhalliday | laurit | ❌ | ✅ | 83d |
| [Replace ArrayBlockingQueue with park/unpark for BatchSpanProcessor$Worker (#8240)](https://github.com/open-telemetry/opentelemetry-java/pull/8240) | Khepu | jack-berg<br>zeitlinger | ✅ | ✅ | 63d |
| [Merge colliding Prometheus label values (#8364)](https://github.com/open-telemetry/opentelemetry-java/pull/8364) | ADITYA-CODE-SOURCE | jack-berg&nbsp;💬<br>psx95&nbsp;🔴<br>zeitlinger | ✅ | ❌ | 44d |
| [Fix Groovy OpenTelemetrySdk builder loading (#8407)](https://github.com/open-telemetry/opentelemetry-java/pull/8407) | ADITYA-CODE-SOURCE | jack-berg<br>laurit<br>psx95&nbsp;💬 | ✅ | ✅ | 37d |
| [Use HTTP error bodies in HttpExporter warnings (#8428)](https://github.com/open-telemetry/opentelemetry-java/pull/8428) | ADITYA-CODE-SOURCE | psx95&nbsp;💬 | ✅ | ✅ | 29d |
| [Fix W3CBaggagePropagator to allow empty baggage values per W3C spec (#8468)](https://github.com/open-telemetry/opentelemetry-java/pull/8468) | dahyvuun | jaydeluca&nbsp;💬<br>zeitlinger&nbsp;✅ | ✅ | ✅ | 14d |
| [Add a ConfigProvider callback for runtime instrumentation option changes (#8076)](https://github.com/open-telemetry/opentelemetry-java/pull/8076) | jackshirazi | jack-berg<br>trask | ❌ | ❌ | 9d |
| [Enforce OTLP request size limits (#8446)](https://github.com/open-telemetry/opentelemetry-java/pull/8446) | ADITYA-CODE-SOURCE | jack-berg&nbsp;💬⁠✅<br>jkwatson | ❌ | ✅ | 9d |
| [Entity SDK - Initial opt-in SDK features (#8464)](https://github.com/open-telemetry/opentelemetry-java/pull/8464) | jsuereth | jack-berg&nbsp;💬 | ❌ | ✅ | 6h |
| [Suppress more test logs (#8536)](https://github.com/open-telemetry/opentelemetry-java/pull/8536) | jack-berg | psx95&nbsp;💬 | ✅ | ❌ | 5m |

## Waiting on external

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [Add fallback endpoint support for OTLP exporters (#8197)](https://github.com/open-telemetry/opentelemetry-java/pull/8197) | sridharsurvi1 | jack-berg&nbsp;🔴 | ❌ | ❌ | 70d |

## Draft pull requests

| PR | Author | Updated |
|---|---|:---:|
| [Allow frameworks to add instrumentation scope conditions (#7312)](https://github.com/open-telemetry/opentelemetry-java/pull/7312) | brunobat | 420d |
| [EntityProvider prototype (#7360)](https://github.com/open-telemetry/opentelemetry-java/pull/7360) | breedx-splk | 370d |
| [Add support to finish instrument recording (#7792)](https://github.com/open-telemetry/opentelemetry-java/pull/7792) | atoulme | 56d |
| [Sketch out ScopedValue based context implementation (#8352)](https://github.com/open-telemetry/opentelemetry-java/pull/8352) | jack-berg | 56d |
| [Null checking applied (#8321)](https://github.com/open-telemetry/opentelemetry-java/pull/8321) | jack-berg | 47d |
| [add declarative config for log throttling (#7838)](https://github.com/open-telemetry/opentelemetry-java/pull/7838) | the-clam | 9d |
| [Increase HTTP connectTimeout test threshold to match gRPC (#8494)](https://github.com/open-telemetry/opentelemetry-java/pull/8494) | thswlsqls | 4d |
| [Fix sign extension on LogRecord flags in low-allocation log marshaler (#8493)](https://github.com/open-telemetry/opentelemetry-java/pull/8493) | thswlsqls | 4d |
| [Fix Jaeger propagator baggage header case sensitivity (#8496)](https://github.com/open-telemetry/opentelemetry-java/pull/8496) | thswlsqls | 4d |
| [Standardize OkHttpHttpSender shutdown to await executor termination (#8495)](https://github.com/open-telemetry/opentelemetry-java/pull/8495) | thswlsqls | 4d |
| [Fix serialization of array-valued scope and resource attributes in Prometheus exporter (#8497)](https://github.com/open-telemetry/opentelemetry-java/pull/8497) | thswlsqls | 4d |
| [Update documented Kotlin minimum version to 2.2 (#8498)](https://github.com/open-telemetry/opentelemetry-java/pull/8498) | thswlsqls | 4d |
| [Fix Javadoc and comment in OSGi integration tests (#8500)](https://github.com/open-telemetry/opentelemetry-java/pull/8500) | thswlsqls | 3d |
| [Return null from TracerShim extract when the carrier has no span context (#8505)](https://github.com/open-telemetry/opentelemetry-java/pull/8505) | thswlsqls | 3d |
| [Fix Javadoc errors in JFR profiles shim (#8503)](https://github.com/open-telemetry/opentelemetry-java/pull/8503) | thswlsqls | 3d |
| [Fix OpenTelemetrySdkBuilderUtil.setConfigProvider Javadoc copy-paste (#8502)](https://github.com/open-telemetry/opentelemetry-java/pull/8502) | thswlsqls | 3d |
| [Fix ReadWriteLogRecord default getObservedTimestampEpochNanos returning record timestamp (#8504)](https://github.com/open-telemetry/opentelemetry-java/pull/8504) | thswlsqls | 3d |
| [Reduce LongSumAggregator.doRecordLong visibility to protected (#8507)](https://github.com/open-telemetry/opentelemetry-java/pull/8507) | thswlsqls | 3d |
| [Strengthen graal incubating-not-found test to detect incubator API on classpath (#8510)](https://github.com/open-telemetry/opentelemetry-java/pull/8510) | thswlsqls | 3d |
| [Preserve OpenCensus status description when converting to OpenTelemetry (#8511)](https://github.com/open-telemetry/opentelemetry-java/pull/8511) | thswlsqls | 3d |
| [Fix typos in sdk-common Javadoc (#8512)](https://github.com/open-telemetry/opentelemetry-java/pull/8512) | thswlsqls | 3d |
| [Fix stale parameter name in JcTools.drain Javadoc (#8513)](https://github.com/open-telemetry/opentelemetry-java/pull/8513) | thswlsqls | 3d |
| [Fix profiles data model attribute count parameter name and timestamp doc unit (#8514)](https://github.com/open-telemetry/opentelemetry-java/pull/8514) | thswlsqls | 3d |
| [Fix SpanLimitsBuilder Javadoc to match non-negative argument check (#8516)](https://github.com/open-telemetry/opentelemetry-java/pull/8516) | thswlsqls | 3d |
| [Fix LongExemplarAssert hasFilteredAttributesSatisfyingExactly to enforce exact attribute matching (#8518)](https://github.com/open-telemetry/opentelemetry-java/pull/8518) | thswlsqls | 3d |
| [Deprecate TextMapGetter keys method (#8531)](https://github.com/open-telemetry/opentelemetry-java/pull/8531) | arnabnandy7 | 16h |

_Approvers may [force a refresh](https://github.com/open-telemetry/shared-workflows/actions/workflows/pull-request-dashboard.yml)._

