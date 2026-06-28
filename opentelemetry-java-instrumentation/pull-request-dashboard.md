> [!NOTE]
> Open non-draft PRs grouped by who is expected to act next. Draft PRs are listed separately. The grouping is partly performed by an LLM ([source](https://github.com/open-telemetry/shared-workflows/blob/main/.github/scripts/pull-request-dashboard/dashboard.py)) and could contain mistakes.
>
> Reviewers column: ✅ approved · ✔️ approved (non-code-owner) · 💬 open thread · 🔴 changes requested.

## Waiting on maintainers

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [feat: Add ConfigPropertiesBackedConfigProvider options for extensions and distros (#15835)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/15835) | aviralgarg05 | robsunday&nbsp;✔️<br>trask<br>zeitlinger&nbsp;✅ | ❌ | ✅ | 53d |

## Waiting on reviewers

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [Add InstrumentationDefaults helper to declarative-config-bridge (#17816)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/17816) | zeitlinger | laurit<br>trask | ✅ | ✅ | 47d |
| [docs(agent-extension-api): mark ConfigProperties &#64;Nullable where null is possible (#18090)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18090) | zeitlinger | laurit | ✅ | ✅ | 40d |
| [test: parameterize KubernetesRequestUtilsTest cases (#18812)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18812) | zeitlinger |  | ✅ | ✅ | 26d |
| [Recover pulsar wrapped message ids (#18935)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18935) | zeitlinger | laurit | ✅ | ✅ | 18d |
| [Support excluding MDC attributes from capture-all (#18912)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18912) | philsttr | laurit<br>trask | ❌ | ✅ | 15d |
| [Add OSGi support for library instrumentation, API, and SDK extension artifacts (#18995)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18995) | royteeuwen |  | ✅ | ❌ | 13d |
| [Revive reduced servlet smoke test matrix on top of main (#18953)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18953) | zeitlinger |  | ✅ | ✅ | 8d |
| [Implement configurable metric bridge metric suppression (#19048)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19048) | somiljain2006 |  | ✅ | ✅ | 8d |
| [feat: add commons pool2 instrumentation (#19091)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19091) | YaoYingLong |  | ❌ | ✅ | 3d |
| [Add structured property support for declarative config metadata (#19077)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19077) | jaydeluca |  | ✅ | ✅ | 2d |
| [Gate process command attributes under v3 preview (#19082)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19082) | trask |  | ✅ | ✅ | 2d |
| [Add Cassandra JMX metrics target system (#19080)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19080) | jkoronaAtCisco | trask | ✅ | ✅ | 2d |
| [feat: add support for hbase-client 1.4 (#19087)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19087) | YaoYingLong |  | ✅ | ❌ | 1d |
| [Suppress duplicate warning log for same application logger factory class (#19088)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19088) | bhuvan-somisetty |  | ✅ | ✅ | 1d |

## Waiting on authors

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [Java agent insturmentation added for Failsafe 3.0 (#15759)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/15759) | onurkybsi | jaydeluca&nbsp;✅<br>laurit&nbsp;💬<br>trask&nbsp;💬 | ❌ | ❌ | 109d |
| [Add ability to customize span exception handling to instrumenter (#18530)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18530) | jaydeluca |  | ✅ | ❌ | 49d |
| [Add the Nacos-Client 2.x plugin (#18758)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18758) | peachisai | laurit&nbsp;💬 | ❌ | ❌ | 41d |
| [fix(webflux): register reactor hook in createWebFilter and add filter. (#18844)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18844) | amit306 | laurit<br>singhvibhanshu&nbsp;✔️ | ✅ | ✅ | 33d |
| [fix: separate default vs controller-telemetry metadata test suites so instrumentation-list.yaml reflects out-of-the-box telemetry (#18974)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18974) | mvanhorn | jaydeluca&nbsp;💬 | ✅ | ❌ | 16d |
| [Use Arguments.argumentSet() for named parameterized test cases (#18975)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18975) | zeitlinger | laurit<br>trask&nbsp;💬 | ✅ | ✅ | 16d |
| [feat(kafka): add messaging.kafka.cluster.id from client reflection (#18978)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18978) | shashank-reddy-nr |  | ✅ | ❌ | 15d |
| [Capture dubbo UNKNOWN requests (#16668)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/16668) | steverao | trask | ✅ | ✅ | 3d |
| [feat(spring-cloud-aws): instrument onMessage(Collection&lt;Message&lt;T&gt;&gt;) … (#19053)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19053) | Aryainguz | laurit&nbsp;💬<br>trask | ✅ | ✅ | 2d |
| [getModuleGroup removal using common class-loaders for instrumentation modules. (#18859)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18859) | SylvainJuge | JonasKunz&nbsp;✅<br>laurit&nbsp;💬<br>SylvainJuge<br>trask | ✅ | ✅ | 1d |

## Waiting on external

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [chore(deps): update plugin org.jetbrains.kotlin.jvm to v2.4.0 (#18948)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18948) | app/renovate | laurit | ❌ | ✅ | 18d |

## Unknown

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [Add JFR metrics for virtual thread pinning and submit failures (#19092)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19092) | tsawada |  | ? | ? | ? |

## Draft pull requests

| PR | Author | Updated |
|---|---|:---:|
| [Draft: init spring-ai instrumentation (#15064)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/15064) | Cirilla-zmh | 88d |
| [Rename setCaptured* to setCapture* to have a single convention (#17154)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/17154) | trask | 87d |
| [ci: migrate to flint v2 for linting (#17759)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/17759) | zeitlinger | 65d |
| [Add network timing attributes to okhttp3 library (#15664)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/15664) | surbhiia | 63d |
| [Add NullAway to javaagent-tooling and javaagent (#17719)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/17719) | zeitlinger | 61d |
| [Migrate generative AI semantic conventions to OTel 1.37.0 (#15268)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/15268) | Cirilla-zmh | 53d |
| [Capture gRPC UNKNOWN requests (#16214)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/16214) | trask | 47d |
| [Retrieve gRPC `server.address`/`server.port` from gRPC target (#16161)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/16161) | trask | 46d |
| [Auto-regenerate gh-aw lock files in renovate PRs (#18865)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18865) | trask | 30d |
| [Add example declarative configuration doc (#17854)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/17854) | jaydeluca | 25d |
| [Tracking package and module name alignment (#18428)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18428) | trask | 25d |
| [Add support for capturing and extracting Dubbo response status codes (#16688)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/16688) | steverao | 11d |
| [Unify database batch tests into parameterized scenario tests (#19019)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19019) | trask | 9d |
| [switch non-inlined instrumentation by default + update doc (#19076)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19076) | SylvainJuge | 2d |

_Approvers may [force a refresh](https://github.com/open-telemetry/shared-workflows/actions/workflows/pull-request-dashboard.yml)._

