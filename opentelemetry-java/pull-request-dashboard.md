> [!NOTE]
> Open non-draft PRs grouped by who is expected to act next. Draft PRs are listed separately. The grouping is partly performed by an LLM ([source](https://github.com/open-telemetry/shared-workflows/blob/main/.github/scripts/pull-request-dashboard/dashboard.py)) and could contain mistakes.
>
> Reviewers column: ✅ approved · ✔️ approved (non-code-owner) · 💬 open thread · 🔴 changes requested.

## Waiting on maintainers

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [Restore compliance between Composite Samplers code and specs (#8450)](https://github.com/open-telemetry/opentelemetry-java/pull/8450) | PeterF778 | jack-berg&nbsp;✅<br>jkwatson<br>zeitlinger | ❌ | ✅ | 4d |

## Waiting on reviewers

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [Make StandardComponentId constructor public (#7763)](https://github.com/open-telemetry/opentelemetry-java/pull/7763) | brunobat | jack-berg | ✅ | ❌ | 240d |
| [Add JSON pretty-print to logging-otlp exporters (#8164)](https://github.com/open-telemetry/opentelemetry-java/pull/8164) | lucacavenaghi97 | jack-berg<br>zeitlinger | ✅ | ❌ | 106d |
| [Fix Groovy compatibility in OpenTelemetrySdkBuilder (#8467)](https://github.com/open-telemetry/opentelemetry-java/pull/8467) | ADITYA-CODE-SOURCE | psx95 | ✅ | ❌ | 16d |
| [profiles: improve JFR export example (#8349)](https://github.com/open-telemetry/opentelemetry-java/pull/8349) | jhalliday | zeitlinger | ✅ | ✅ | 4d |
| [Update dependency org.jetbrains.kotlin:kotlin-gradle-plugin to v2.4.0 (#8521)](https://github.com/open-telemetry/opentelemetry-java/pull/8521) | app/renovate |  | ❌ | ✅ | 4d |
| [Declarative config ref descriptions (#8540)](https://github.com/open-telemetry/opentelemetry-java/pull/8540) | jack-berg |  | ✅ | ✅ | 1d |
| [FIx for BSP benchmark aux counters (exportedSpans/droppedSpans) always reporting zero (#8539)](https://github.com/open-telemetry/opentelemetry-java/pull/8539) | EvgeniiR |  | ✅ | ✅ | 1d |
| [Replace jackson OTLP json serialization with handrolled version (#8545)](https://github.com/open-telemetry/opentelemetry-java/pull/8545) | jack-berg |  | ✅ | ✅ | 17h |
| [Bound instruments (#8527)](https://github.com/open-telemetry/opentelemetry-java/pull/8527) | jack-berg |  | ❌ | ✅ | 14h |
| [Enforce last-value-wins semantics in AttributesMap without performance regression (#8548)](https://github.com/open-telemetry/opentelemetry-java/pull/8548) | EvgeniiR |  | ✅ | ✅ | 1h |

## Waiting on authors

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [\[DO NOT MERGE\] JFR API usage (#7741)](https://github.com/open-telemetry/opentelemetry-java/pull/7741) | jhalliday | laurit | ❌ | ✅ | 85d |
| [Replace ArrayBlockingQueue with park/unpark for BatchSpanProcessor$Worker (#8240)](https://github.com/open-telemetry/opentelemetry-java/pull/8240) | Khepu | jack-berg<br>zeitlinger | ✅ | ✅ | 65d |
| [Merge colliding Prometheus label values (#8364)](https://github.com/open-telemetry/opentelemetry-java/pull/8364) | ADITYA-CODE-SOURCE | jack-berg&nbsp;💬<br>psx95&nbsp;🔴<br>zeitlinger | ✅ | ❌ | 45d |
| [Fix Groovy OpenTelemetrySdk builder loading (#8407)](https://github.com/open-telemetry/opentelemetry-java/pull/8407) | ADITYA-CODE-SOURCE | jack-berg<br>laurit<br>psx95&nbsp;💬 | ✅ | ✅ | 39d |
| [Use HTTP error bodies in HttpExporter warnings (#8428)](https://github.com/open-telemetry/opentelemetry-java/pull/8428) | ADITYA-CODE-SOURCE | psx95&nbsp;💬 | ✅ | ✅ | 30d |
| [Fix W3CBaggagePropagator to allow empty baggage values per W3C spec (#8468)](https://github.com/open-telemetry/opentelemetry-java/pull/8468) | dahyvuun | jaydeluca&nbsp;💬<br>zeitlinger&nbsp;✅ | ✅ | ✅ | 15d |
| [Add a ConfigProvider callback for runtime instrumentation option changes (#8076)](https://github.com/open-telemetry/opentelemetry-java/pull/8076) | jackshirazi | jack-berg<br>trask | ❌ | ❌ | 10d |
| [Enforce OTLP request size limits (#8446)](https://github.com/open-telemetry/opentelemetry-java/pull/8446) | ADITYA-CODE-SOURCE | jack-berg&nbsp;💬⁠✅<br>jkwatson | ❌ | ✅ | 10d |
| [Entity SDK - Initial opt-in SDK features (#8464)](https://github.com/open-telemetry/opentelemetry-java/pull/8464) | jsuereth | jack-berg&nbsp;💬 | ❌ | ✅ | 1d |

## Waiting on external

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [Add fallback endpoint support for OTLP exporters (#8197)](https://github.com/open-telemetry/opentelemetry-java/pull/8197) | sridharsurvi1 | jack-berg&nbsp;🔴 | ❌ | ❌ | 72d |

## Draft pull requests

| PR | Author | Updated |
|---|---|:---:|
| [Allow frameworks to add instrumentation scope conditions (#7312)](https://github.com/open-telemetry/opentelemetry-java/pull/7312) | brunobat | 422d |
| [EntityProvider prototype (#7360)](https://github.com/open-telemetry/opentelemetry-java/pull/7360) | breedx-splk | 372d |
| [Add support to finish instrument recording (#7792)](https://github.com/open-telemetry/opentelemetry-java/pull/7792) | atoulme | 58d |
| [Sketch out ScopedValue based context implementation (#8352)](https://github.com/open-telemetry/opentelemetry-java/pull/8352) | jack-berg | 57d |
| [Null checking applied (#8321)](https://github.com/open-telemetry/opentelemetry-java/pull/8321) | jack-berg | 49d |
| [add declarative config for log throttling (#7838)](https://github.com/open-telemetry/opentelemetry-java/pull/7838) | the-clam | 11d |
| [Increase HTTP connectTimeout test threshold to match gRPC (#8494)](https://github.com/open-telemetry/opentelemetry-java/pull/8494) | thswlsqls | 6d |
| [Fix sign extension on LogRecord flags in low-allocation log marshaler (#8493)](https://github.com/open-telemetry/opentelemetry-java/pull/8493) | thswlsqls | 6d |
| [Fix Jaeger propagator baggage header case sensitivity (#8496)](https://github.com/open-telemetry/opentelemetry-java/pull/8496) | thswlsqls | 6d |
| [Standardize OkHttpHttpSender shutdown to await executor termination (#8495)](https://github.com/open-telemetry/opentelemetry-java/pull/8495) | thswlsqls | 6d |
| [Fix serialization of array-valued scope and resource attributes in Prometheus exporter (#8497)](https://github.com/open-telemetry/opentelemetry-java/pull/8497) | thswlsqls | 6d |
| [Update documented Kotlin minimum version to 2.2 (#8498)](https://github.com/open-telemetry/opentelemetry-java/pull/8498) | thswlsqls | 6d |
| [Fix Javadoc and comment in OSGi integration tests (#8500)](https://github.com/open-telemetry/opentelemetry-java/pull/8500) | thswlsqls | 5d |
| [Return null from TracerShim extract when the carrier has no span context (#8505)](https://github.com/open-telemetry/opentelemetry-java/pull/8505) | thswlsqls | 5d |
| [Fix Javadoc errors in JFR profiles shim (#8503)](https://github.com/open-telemetry/opentelemetry-java/pull/8503) | thswlsqls | 5d |
| [Fix OpenTelemetrySdkBuilderUtil.setConfigProvider Javadoc copy-paste (#8502)](https://github.com/open-telemetry/opentelemetry-java/pull/8502) | thswlsqls | 5d |
| [Fix ReadWriteLogRecord default getObservedTimestampEpochNanos returning record timestamp (#8504)](https://github.com/open-telemetry/opentelemetry-java/pull/8504) | thswlsqls | 5d |
| [Reduce LongSumAggregator.doRecordLong visibility to protected (#8507)](https://github.com/open-telemetry/opentelemetry-java/pull/8507) | thswlsqls | 5d |
| [Strengthen graal incubating-not-found test to detect incubator API on classpath (#8510)](https://github.com/open-telemetry/opentelemetry-java/pull/8510) | thswlsqls | 5d |
| [Preserve OpenCensus status description when converting to OpenTelemetry (#8511)](https://github.com/open-telemetry/opentelemetry-java/pull/8511) | thswlsqls | 5d |
| [Fix typos in sdk-common Javadoc (#8512)](https://github.com/open-telemetry/opentelemetry-java/pull/8512) | thswlsqls | 5d |
| [Fix stale parameter name in JcTools.drain Javadoc (#8513)](https://github.com/open-telemetry/opentelemetry-java/pull/8513) | thswlsqls | 5d |
| [Fix profiles data model attribute count parameter name and timestamp doc unit (#8514)](https://github.com/open-telemetry/opentelemetry-java/pull/8514) | thswlsqls | 5d |
| [Fix SpanLimitsBuilder Javadoc to match non-negative argument check (#8516)](https://github.com/open-telemetry/opentelemetry-java/pull/8516) | thswlsqls | 5d |
| [Fix LongExemplarAssert hasFilteredAttributesSatisfyingExactly to enforce exact attribute matching (#8518)](https://github.com/open-telemetry/opentelemetry-java/pull/8518) | thswlsqls | 5d |
| [Deprecate TextMapGetter keys method (#8531)](https://github.com/open-telemetry/opentelemetry-java/pull/8531) | arnabnandy7 | 2d |
| [Jackson 3 support on Exporters (#8544)](https://github.com/open-telemetry/opentelemetry-java/pull/8544) | brunobat | 1d |
| [Remove Jackson dependency from exporters (#8546)](https://github.com/open-telemetry/opentelemetry-java/pull/8546) | brunobat | 19h |

<details>
<summary>Diagnostics</summary>

```text
PR #8548
llm: pr-conversation -> none (The author’s last comment is a completed informational reply about benchmark results and does not leave an open question or request for action.)

PR #8545
llm: pr-conversation -> reviewer (The author replied to the reviewer’s points and left an open technical disagreement, so the ball is back with the reviewer/maintainer.)

PR #8468
llm: PRRT_kwDOCkv3g86ImrO2 -> author (The latest reviewer comment says the issue is still not addressed and requests code/test changes, so the author needs to update the PR.)

PR #8467
llm: pr-conversation -> reviewer (The reviewer asked for motivation, and the author answered with the rationale and linked issue; the ball is back with the reviewer to acknowledge or continue review.)

PR #8464
llm: PRRT_kwDOCkv3g86I3qb_ -> author (The latest comment is from a reviewer asking whether the current approach is still needed and proposing alternatives, so the author needs to जवाब/decide on the suggested change.)
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

