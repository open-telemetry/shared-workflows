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
| [Use failExceptionally in PeriodicMetricReader when exporter is busy (#8525)](https://github.com/open-telemetry/opentelemetry-java/pull/8525) | vivekp14 | jack-berg&nbsp;✅ | ✅ | ✅ | 58m |

## Waiting on reviewers

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [Make StandardComponentId constructor public (#7763)](https://github.com/open-telemetry/opentelemetry-java/pull/7763) | brunobat | jack-berg | ✅ | ❌ | 239d |
| [Add JSON pretty-print to logging-otlp exporters (#8164)](https://github.com/open-telemetry/opentelemetry-java/pull/8164) | lucacavenaghi97 | jack-berg<br>zeitlinger | ✅ | ❌ | 105d |
| [Fix Groovy compatibility in OpenTelemetrySdkBuilder (#8467)](https://github.com/open-telemetry/opentelemetry-java/pull/8467) | ADITYA-CODE-SOURCE | psx95 | ✅ | ❌ | 15d |
| [profiles: improve JFR export example (#8349)](https://github.com/open-telemetry/opentelemetry-java/pull/8349) | jhalliday | zeitlinger | ✅ | ✅ | 3d |
| [Update dependency org.jetbrains.kotlin:kotlin-gradle-plugin to v2.4.0 (#8521)](https://github.com/open-telemetry/opentelemetry-java/pull/8521) | app/renovate |  | ❌ | ✅ | 2d |
| [Bound instruments (#8527)](https://github.com/open-telemetry/opentelemetry-java/pull/8527) | jack-berg |  | ❌ | ✅ | 2d |
| [Declarative config ref descriptions (#8540)](https://github.com/open-telemetry/opentelemetry-java/pull/8540) | jack-berg |  | ⏳ | ✅ | 1h |
| [FIx for BSP benchmark aux counters (exportedSpans/droppedSpans) always reporting zero (#8539)](https://github.com/open-telemetry/opentelemetry-java/pull/8539) | EvgeniiR |  | ⏳ | ✅ | 31m |

## Waiting on authors

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [\[DO NOT MERGE\] JFR API usage (#7741)](https://github.com/open-telemetry/opentelemetry-java/pull/7741) | jhalliday | laurit | ❌ | ✅ | 84d |
| [Replace ArrayBlockingQueue with park/unpark for BatchSpanProcessor$Worker (#8240)](https://github.com/open-telemetry/opentelemetry-java/pull/8240) | Khepu | jack-berg<br>zeitlinger | ✅ | ✅ | 63d |
| [Merge colliding Prometheus label values (#8364)](https://github.com/open-telemetry/opentelemetry-java/pull/8364) | ADITYA-CODE-SOURCE | jack-berg&nbsp;💬<br>psx95&nbsp;🔴<br>zeitlinger | ✅ | ❌ | 44d |
| [Fix Groovy OpenTelemetrySdk builder loading (#8407)](https://github.com/open-telemetry/opentelemetry-java/pull/8407) | ADITYA-CODE-SOURCE | jack-berg<br>laurit<br>psx95&nbsp;💬 | ✅ | ✅ | 37d |
| [Use HTTP error bodies in HttpExporter warnings (#8428)](https://github.com/open-telemetry/opentelemetry-java/pull/8428) | ADITYA-CODE-SOURCE | psx95&nbsp;💬 | ✅ | ✅ | 29d |
| [Fix W3CBaggagePropagator to allow empty baggage values per W3C spec (#8468)](https://github.com/open-telemetry/opentelemetry-java/pull/8468) | dahyvuun | jaydeluca&nbsp;💬<br>zeitlinger&nbsp;✅ | ✅ | ✅ | 14d |
| [Add a ConfigProvider callback for runtime instrumentation option changes (#8076)](https://github.com/open-telemetry/opentelemetry-java/pull/8076) | jackshirazi | jack-berg<br>trask | ❌ | ❌ | 9d |
| [Enforce OTLP request size limits (#8446)](https://github.com/open-telemetry/opentelemetry-java/pull/8446) | ADITYA-CODE-SOURCE | jack-berg&nbsp;💬⁠✅<br>jkwatson | ❌ | ✅ | 9d |
| [Entity SDK - Initial opt-in SDK features (#8464)](https://github.com/open-telemetry/opentelemetry-java/pull/8464) | jsuereth | jack-berg&nbsp;💬 | ❌ | ✅ | 6h |
| [Suppress more test logs (#8536)](https://github.com/open-telemetry/opentelemetry-java/pull/8536) | jack-berg | psx95&nbsp;💬 | ✅ | ❌ | 19m |

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

<details>
<summary>Diagnostics</summary>

```text
PR #8536
llm: PRRT_kwDOCkv3g86MWD3f -> author (A reviewer asked for justification about converting private static methods to instance methods, so the author needs to जवाब/adjust the PR.)
llm: PRRT_kwDOCkv3g86MWBff -> reviewer (The author answered the reviewer’s question and explained the fix, so the ball is back with the reviewer to acknowledge or continue the review.)
llm: PRRT_kwDOCkv3g86MWG7M -> none (The reviewer asked for confirmation, and the author answered directly with the rationale; no further follow-up is implied in the thread.)

PR #8481
llm: pr-conversation -> reviewer (The latest comment is from the author and defers the related `keys` discussion out of this PR, so the reviewer/maintainer is the next one who could acknowledge or push back.)

PR #8480
llm: pr-conversation -> none (The only comment is an approving reviewer note with no requested follow-up, so the thread is effectively closed.)

PR #8468
llm: PRRT_kwDOCkv3g86ImrO2 -> author (The latest reviewer comment says the issue is still not addressed and requests code/test changes, so the author needs to update the PR.)

PR #8467
llm: pr-conversation -> reviewer (The reviewer asked for motivation, and the author answered with the rationale and linked issue; the ball is back with the reviewer to acknowledge or continue review.)

PR #8464
llm: PRRT_kwDOCkv3g86I3qb_ -> author (The latest comment is from a reviewer asking whether the current approach is still needed and proposing alternatives, so the author needs to जवाब/decide on the suggested change.)
llm: PRRT_kwDOCkv3g86MPbOP -> author (The last comment is from a reviewer and reads like an unresolved review note, so the author still needs to address or respond to it.)
llm: PRRT_kwDOCkv3g86MPcNK -> author (The reviewer said the changes look like leftover work from an earlier branch, so the PR author likely needs to confirm and update the file or respond.)
llm: PRRT_kwDOCkv3g86MPrzN -> author (A reviewer suggested removing `EntityBuilder builder(String)` and changing the return site, so the author needs to implement or जवाब to that requested change.)
llm: PRRT_kwDOCkv3g86MPsyw -> author (A reviewer asked whether entities can have empty IDs and suggested making the constructor parameter required, so the PR author needs to जवाब/decide and respond.)
llm: PRRT_kwDOCkv3g86MPNhC -> author (The reviewer flagged that the code is unused, and the author replied that they accidentally reverted wiring and will fix it, so the next action is on the author.)
llm: PRRT_kwDOCkv3g86MPX-x -> author (The author replied that they need to detangle the logic and will update the PR, so the ball is back with the author.)
llm: PRRT_kwDOCkv3g86MPaWD -> author (The reviewer asked for a parameterized test, and the author replied that they were still working on it, so the follow-up is still with the author.)
llm: PRRT_kwDOCkv3g86MU18G -> reviewer (The latest comment is from a reviewer/approver asking other approvers for input, so the ball is with reviewers/maintainers to respond or decide.)
llm: PRRT_kwDOCkv3g86MPPJE -> author (The reviewer asked for clarification and suggested moving the constant to `opentelemetry-sdk-extension-autoconfigure-spi`, so the author needs to respond and update the PR.)

PR #8450
llm: pr-conversation -> none (The author only reports that they restored the branch to a previous state; there’s no explicit request or pending follow-up in the thread.)

PR #8446
llm: PRRT_kwDOCkv3g86KBUh4 -> author (A reviewer asked a direct question (“Why two methods?”), so the author needs to जवाब/respond or adjust the code.)
llm: PRRT_kwDOCkv3g86KBWSq -> author (A reviewer left a code change suggestion and the thread is still unresolved, so the PR author needs to respond by applying or addressing it.)

PR #8428
llm: PRRT_kwDOCkv3g86FNGwS -> author (A reviewer asked for the rationale behind the chosen number, so the author needs to जवाब/respond with an explanation or update.)
llm: PRRT_kwDOCkv3g86JKWNr -> author (A reviewer asked a code question/suggestion and there is no follow-up yet, so the author needs to respond or update the PR.)

PR #8407
llm: PRRT_kwDOCkv3g86CMfQS -> none (The reviewer asked for explanatory comments, and the author replied that they added them, so the request is addressed and no further follow-up is implied.)
llm: PRRT_kwDOCkv3g86CMnfF -> reviewer (The reviewer asked why it changed; the author explained the rationale, so the ball is back with the reviewer to accept or continue the review.)
llm: pr-conversation -> author (A reviewer’s last comment asks the author to try `@CompileStatic` and questions whether reflection is necessary, so the ball is with the author to respond or update the PR.)

PR #8364
llm: PRRT_kwDOCkv3g86BhQsA -> author (The latest reviewer comment requests a code change to reduce allocation overhead, so the PR author needs to update the thread.)
llm: PRRT_kwDOCkv3g86BhVsZ -> author (A reviewer指出 collision handling should also account for normalized attributes from other sources, so the PR author needs to respond and update the implementation.)
llm: pr-conversation -> author (The author says the PR is parked for #8346 and that they will rebase, apply the requested changes, and re-request review once that settles, so the next action is on the author.)

PR #8349
llm: pr-conversation -> reviewer (The latest comment is from the author responding to the review points and asking for clarification on the `sampledThread` concern, so the reviewer has the next turn.)

PR #8240
llm: pr-conversation -> author (The author’s latest comment says they will keep investigating why the benchmark metrics are zero, so the ball is still with the author.)

PR #8197
llm: pr-conversation -> external (The reviewer asked to wait for spec discussion, and the author’s latest comment just points to the external specification issue, so the next step is outside this repository.)

PR #8164
llm: PRRT_kwDOCkv3g85z-n0C -> none (The latest reviewer comment is a brief approval of the proposed convention, which closes the thread with no further action needed.)

PR #8076
llm: pr-conversation -> author (The latest comment is from the author and says they will still test and validate this week, so the ball remains with the author.)

PR #7763
llm: pr-conversation -> reviewer (The reviewer asked why, and the author answered with an explanation; the ball is back with the reviewer to acknowledge or continue the review.)

PR #7741
llm: pr-conversation -> author (The latest comment is a reviewer suggestion with an explicit request for the author to evaluate it, so the author needs to respond or act.)

```

</details>

_Approvers may [force a refresh](https://github.com/open-telemetry/shared-workflows/actions/workflows/pull-request-dashboard.yml)._

