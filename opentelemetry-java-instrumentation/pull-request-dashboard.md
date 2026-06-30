> [!NOTE]
> Open non-draft PRs grouped by who is expected to act next. Draft PRs are listed separately. The grouping is partly performed by an LLM ([source](https://github.com/open-telemetry/shared-workflows/blob/main/.github/scripts/pull-request-dashboard/dashboard.py)) and could contain mistakes.
>
> Reviewers column: ✅ approved · ✔️ approved (non-code-owner) · 💬 open thread · 🔴 changes requested.

## Waiting on maintainers

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [feat: Add ConfigPropertiesBackedConfigProvider options for extensions and distros (#15835)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/15835) | aviralgarg05 | robsunday&nbsp;✔️<br>trask<br>zeitlinger&nbsp;✅ | ❌ | ✅ | 55d |
| [Fix flaky pulsar test (#19095)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19095) | laurit | jaydeluca&nbsp;✅ | ✅ | ✅ | 11h |

## Waiting on reviewers

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [Add InstrumentationDefaults helper to declarative-config-bridge (#17816)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/17816) | zeitlinger | laurit<br>trask | ✅ | ✅ | 48d |
| [docs(agent-extension-api): mark ConfigProperties &#64;Nullable where null is possible (#18090)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18090) | zeitlinger | laurit | ✅ | ✅ | 41d |
| [test: parameterize KubernetesRequestUtilsTest cases (#18812)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18812) | zeitlinger |  | ✅ | ✅ | 27d |
| [Recover pulsar wrapped message ids (#18935)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18935) | zeitlinger | laurit | ✅ | ✅ | 19d |
| [Support excluding MDC attributes from capture-all (#18912)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18912) | philsttr | laurit<br>trask | ❌ | ✅ | 17d |
| [Add OSGi support for library instrumentation, API, and SDK extension artifacts (#18995)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18995) | royteeuwen |  | ✅ | ❌ | 15d |
| [Revive reduced servlet smoke test matrix on top of main (#18953)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18953) | zeitlinger |  | ✅ | ✅ | 10d |
| [Implement configurable metric bridge metric suppression (#19048)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19048) | somiljain2006 |  | ✅ | ✅ | 10d |
| [Add structured property support for declarative config metadata (#19077)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19077) | jaydeluca |  | ✅ | ✅ | 4d |
| [feat: add support for hbase-client 1.4 (#19087)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19087) | YaoYingLong |  | ✅ | ❌ | 3d |
| [Suppress duplicate warning log for same application logger factory class (#19088)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19088) | bhuvan-somisetty |  | ✅ | ✅ | 2d |
| [chore(deps): update plugin com.gradle.common-custom-user-data-gradle-plugin to v2.7.0 (#19097)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19097) | app/renovate |  | ✅ | ✅ | 3h |
| [fix(deps): update spotless packages to v8.8.0 (#19098)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19098) | app/renovate |  | ✅ | ✅ | 3h |
| [chore(deps): update weekly update (#19099)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19099) | app/renovate |  | ⏳ | ✅ | 6m |
| [fix(deps): update all patch versions (#19100)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19100) | app/renovate |  | ⏳ | ✅ | 5m |

## Waiting on authors

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [Java agent insturmentation added for Failsafe 3.0 (#15759)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/15759) | onurkybsi | jaydeluca&nbsp;✅<br>laurit&nbsp;💬<br>trask&nbsp;💬 | ❌ | ❌ | 110d |
| [Add ability to customize span exception handling to instrumenter (#18530)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18530) | jaydeluca |  | ✅ | ❌ | 51d |
| [Add the Nacos-Client 2.x plugin (#18758)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18758) | peachisai | laurit&nbsp;💬 | ❌ | ❌ | 43d |
| [fix(webflux): register reactor hook in createWebFilter and add filter. (#18844)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18844) | amit306 | laurit<br>singhvibhanshu&nbsp;✔️ | ✅ | ✅ | 35d |
| [fix: separate default vs controller-telemetry metadata test suites so instrumentation-list.yaml reflects out-of-the-box telemetry (#18974)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18974) | mvanhorn | jaydeluca&nbsp;💬 | ✅ | ❌ | 17d |
| [Use Arguments.argumentSet() for named parameterized test cases (#18975)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18975) | zeitlinger | laurit<br>trask&nbsp;💬 | ✅ | ✅ | 17d |
| [feat(kafka): add messaging.kafka.cluster.id from client reflection (#18978)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18978) | shashank-reddy-nr |  | ✅ | ❌ | 17d |
| [Capture dubbo UNKNOWN requests (#16668)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/16668) | steverao | trask | ✅ | ✅ | 4d |
| [feat(spring-cloud-aws): instrument onMessage(Collection&lt;Message&lt;T&gt;&gt;) … (#19053)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19053) | Aryainguz | laurit&nbsp;💬<br>trask | ✅ | ✅ | 4d |
| [Add Cassandra JMX metrics target system (#19080)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19080) | jkoronaAtCisco | laurit&nbsp;💬<br>trask | ✅ | ✅ | 13h |
| [\[jdbc\] Capture custom object types in prepared statement parameter instrumentation (#19093)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19093) | CodingFabian | laurit | ✅ | ✅ | 13h |
| [Gate process command attributes under v3 preview (#19082)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19082) | trask | laurit&nbsp;✅ | ✅ | ✅ | 10h |
| [Add JFR metrics for virtual thread pinning and submit failures (#19092)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19092) | tsawada | laurit&nbsp;💬 | ✅ | ✅ | 9h |
| [feat: add commons pool2 instrumentation (#19091)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19091) | YaoYingLong | laurit&nbsp;💬 | ✅ | ✅ | 9h |

## Waiting on external

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [chore(deps): update plugin org.jetbrains.kotlin.jvm to v2.4.0 (#18948)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18948) | app/renovate | laurit | ❌ | ✅ | 19d |

## Draft pull requests

| PR | Author | Updated |
|---|---|:---:|
| [Draft: init spring-ai instrumentation (#15064)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/15064) | Cirilla-zmh | 89d |
| [Rename setCaptured* to setCapture* to have a single convention (#17154)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/17154) | trask | 88d |
| [ci: migrate to flint v2 for linting (#17759)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/17759) | zeitlinger | 67d |
| [Add network timing attributes to okhttp3 library (#15664)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/15664) | surbhiia | 65d |
| [Add NullAway to javaagent-tooling and javaagent (#17719)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/17719) | zeitlinger | 62d |
| [Migrate generative AI semantic conventions to OTel 1.37.0 (#15268)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/15268) | Cirilla-zmh | 54d |
| [Capture gRPC UNKNOWN requests (#16214)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/16214) | trask | 48d |
| [Retrieve gRPC `server.address`/`server.port` from gRPC target (#16161)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/16161) | trask | 48d |
| [Auto-regenerate gh-aw lock files in renovate PRs (#18865)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18865) | trask | 32d |
| [Add example declarative configuration doc (#17854)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/17854) | jaydeluca | 27d |
| [Tracking package and module name alignment (#18428)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18428) | trask | 26d |
| [Add support for capturing and extracting Dubbo response status codes (#16688)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/16688) | steverao | 13d |
| [Unify database batch tests into parameterized scenario tests (#19019)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19019) | trask | 11d |
| [switch non-inlined instrumentation by default + update doc (#19076)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19076) | SylvainJuge | 4d |

_Approvers may [force a refresh](https://github.com/open-telemetry/shared-workflows/actions/workflows/pull-request-dashboard.yml)._

