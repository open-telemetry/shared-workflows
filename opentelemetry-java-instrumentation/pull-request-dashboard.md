> [!NOTE]
> Open non-draft PRs grouped by who is expected to act next. Draft PRs are listed separately. The grouping is partly performed by an LLM ([source](https://github.com/open-telemetry/shared-workflows/blob/main/.github/scripts/pull-request-dashboard/dashboard.py)) and could contain mistakes.
>
> Reviewers column: ✅ approved · ✔️ approved (non-code-owner) · 💬 open thread · 🔴 changes requested.

## Waiting on reviewers

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [Support excluding MDC attributes from capture-all (#18912)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18912) | philsttr | laurit<br>trask | ❌ | ✅ | 26d |
| [Implement configurable metric bridge metric suppression (#19048)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19048) | somiljain2006 |  | ✅ | ✅ | 19d |
| [\[jdbc\] Capture custom object types in prepared statement parameter instrumentation (#19093)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19093) | CodingFabian | laurit | ✅ | ✅ | 8d |
| [Add Instrumenter operation listener propagation test (#19123)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19123) | amirdeljouyi |  | ✅ | ✅ | 6d |
| [Add Cassandra JMX metrics target system (#19080)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19080) | jkoronaAtCisco | laurit<br>SylvainJuge<br>trask | ✅ | ✅ | 5d |
| [Add OSGi support for library instrumentation, API, and SDK extension artifacts (#18995)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18995) | royteeuwen | laurit&nbsp;💬 | ✅ | ❌ | 5d |
| [feat: add support for hbase-client 1.4 (#19087)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19087) | YaoYingLong | jaydeluca<br>laurit | ✅ | ❌ | 5d |
| [\[jmx-metrics\] bootstrap + validate registry definitions for jmx metrics (#19139)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19139) | SylvainJuge | SylvainJuge | ✅ | ✅ | 16h |
| [Extract multi-query batch operation attributes (#19147)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19147) | trask |  | ✅ | ✅ | 4h |
| [Deprecate config parameters in javaagent extension SPIs (#19148)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19148) | trask |  | ✅ | ✅ | 4h |
| [Add Ktor 3.0 client span accessors and bridge them under the agent (#19149)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19149) | amccague |  | ✅ | ✅ | 2h |

## Waiting on authors

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [Java agent insturmentation added for Failsafe 3.0 (#15759)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/15759) | onurkybsi | jaydeluca&nbsp;✅<br>laurit&nbsp;💬<br>trask&nbsp;💬 | ❌ | ❌ | 119d |
| [Add ability to customize span exception handling to instrumenter (#18530)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18530) | jaydeluca |  | ✅ | ❌ | 60d |
| [Add the Nacos-Client 2.x plugin (#18758)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18758) | peachisai | laurit&nbsp;💬 | ❌ | ❌ | 52d |
| [fix(webflux): register reactor hook in createWebFilter and add filter. (#18844)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18844) | amit306 | laurit<br>singhvibhanshu&nbsp;✔️ | ✅ | ✅ | 44d |
| [fix: separate default vs controller-telemetry metadata test suites so instrumentation-list.yaml reflects out-of-the-box telemetry (#18974)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18974) | mvanhorn | jaydeluca&nbsp;💬 | ✅ | ❌ | 26d |
| [Capture dubbo UNKNOWN requests (#16668)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/16668) | steverao | trask | ✅ | ✅ | 13d |
| [Gate process command attributes under v3 preview (#19082)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19082) | trask | laurit&nbsp;✅ | ✅ | ✅ | 9d |
| [fix(druid): optimize dataSourceName to resolve metrics high cardinality (#19108)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19108) | YaoYingLong | laurit&nbsp;💬 | ✅ | ✅ | 7d |
| [feat: add commons pool2 instrumentation (#19091)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19091) | YaoYingLong | jaydeluca&nbsp;💬<br>laurit&nbsp;💬 | ✅ | ✅ | 5d |
| [Emit messaging operation metrics (publish/receive duration) from Kafka instrumentation (#19107)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19107) | maryantocinn | laurit | ✅ | ✅ | 2d |
| [feat(kafka): add messaging.cluster.id attribute to Kafka Produce, Consumer metrics and spans (#18978)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18978) | shashank-reddy-nr | laurit&nbsp;💬 | ✅ | ✅ | 2d |
| [feat: Add GenAI agent/tool/retrieval semantic conventions to instrumentation-api-incubator (#19124)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19124) | eternalcuriouslearner | laurit&nbsp;💬 | ✅ | ✅ | 1d |
| [switch non-inlined instrumentation by default + update doc (#19076)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19076) | SylvainJuge | laurit&nbsp;💬<br>SylvainJuge | ✅ | ✅ | 1d |
| [Disable virtual field usage checker by default (#19138)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19138) | laurit |  | ✅ | ✅ | 1d |
| [Remove isIndyModule method (#19140)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19140) | laurit | steverao&nbsp;✅<br>SylvainJuge&nbsp;✅ | ✅ | ✅ | 1d |
| [Move experimental apis needed by indy instrumentations to InstrumentationModule (#19142)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19142) | laurit | SylvainJuge | ✅ | ✅ | 19h |
| [feat(spring-cloud-aws): instrument onMessage(Collection&lt;Message&lt;T&gt;&gt;) … (#19053)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19053) | Aryainguz | laurit&nbsp;💬<br>trask | ✅ | ✅ | 11h |
| [Suppress duplicate warning log for same application logger factory class (#19088)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19088) | bhuvan-somisetty | laurit&nbsp;✅<br>trask&nbsp;💬 | ✅ | ✅ | 8h |
| [feat: Add ConfigPropertiesBackedConfigProvider options for extensions and distros (#15835)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/15835) | aviralgarg05 | robsunday&nbsp;✔️<br>trask<br>zeitlinger&nbsp;✅ | ❌ | ✅ | 7h |
| [Add structured property support for declarative config metadata (#19077)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19077) | jaydeluca | trask&nbsp;💬⁠✅ | ✅ | ✅ | 7h |
| [Revive reduced servlet smoke test matrix on top of main (#18953)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18953) | zeitlinger | trask | ✅ | ✅ | 4h |
| [docs(agent-extension-api): mark ConfigProperties &#64;Nullable where null is possible (#18090)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18090) | zeitlinger | laurit<br>trask | ✅ | ✅ | 4h |
| [Add InstrumentationDefaults helper to declarative-config-bridge (#17816)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/17816) | zeitlinger | laurit<br>trask | ✅ | ✅ | 4h |
| [Added SchedulerListener Instrumentation for Scheduler-level errors in Quartz (#19117)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19117) | angad-2 | laurit&nbsp;💬⁠✅<br>trask&nbsp;💬 | ✅ | ✅ | 3h |
| [test: parameterize KubernetesRequestUtilsTest cases (#18812)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18812) | zeitlinger | trask&nbsp;💬 | ✅ | ✅ | 1h |

## Waiting on external

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [chore(deps): update plugin org.jetbrains.kotlin.jvm to v2.4.0 (#18948)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18948) | app/renovate | laurit | ❌ | ✅ | 28d |

## Draft pull requests

| PR | Author | Updated |
|---|---|:---:|
| [ci: migrate to flint v2 for linting (#17759)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/17759) | zeitlinger | 76d |
| [Add network timing attributes to okhttp3 library (#15664)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/15664) | surbhiia | 74d |
| [Add NullAway to javaagent-tooling and javaagent (#17719)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/17719) | zeitlinger | 71d |
| [Migrate generative AI semantic conventions to OTel 1.37.0 (#15268)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/15268) | Cirilla-zmh | 63d |
| [Capture gRPC UNKNOWN requests (#16214)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/16214) | trask | 57d |
| [Retrieve gRPC `server.address`/`server.port` from gRPC target (#16161)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/16161) | trask | 57d |
| [Auto-regenerate gh-aw lock files in renovate PRs (#18865)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18865) | trask | 41d |
| [Add example declarative configuration doc (#17854)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/17854) | jaydeluca | 36d |
| [Tracking package and module name alignment (#18428)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18428) | trask | 35d |
| [Unify database batch tests into parameterized scenario tests (#19019)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19019) | trask | 20d |
| [Draft: init spring-ai instrumentation (#15064)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/15064) | Cirilla-zmh | 8d |
| [Rename setCaptured* to setCapture* to have a single convention (#17154)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/17154) | trask | 7d |

_Approvers may [force a refresh](https://github.com/open-telemetry/shared-workflows/actions/workflows/pull-request-dashboard.yml)._

