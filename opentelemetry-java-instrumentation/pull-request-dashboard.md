> [!NOTE]
> Open non-draft PRs grouped by who is expected to act next. Draft PRs are listed separately. The grouping is partly performed by an LLM ([source](https://github.com/open-telemetry/shared-workflows/blob/main/.github/scripts/pull-request-dashboard/dashboard.py)) and could contain mistakes.
>
> Reviewers column: ✅ approved · ✔️ approved (non-code-owner) · 💬 open thread · 🔴 changes requested.

## Waiting on maintainers

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [feat: Add ConfigPropertiesBackedConfigProvider options for extensions and distros (#15835)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/15835) | aviralgarg05 | robsunday&nbsp;✔️<br>trask<br>zeitlinger&nbsp;✅ | ❌ | ✅ | 52d |

## Waiting on reviewers

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [Add InstrumentationDefaults helper to declarative-config-bridge (#17816)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/17816) | zeitlinger | laurit<br>trask | ✅ | ✅ | 46d |
| [docs(agent-extension-api): mark ConfigProperties &#64;Nullable where null is possible (#18090)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18090) | zeitlinger | laurit | ✅ | ✅ | 39d |
| [test: parameterize KubernetesRequestUtilsTest cases (#18812)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18812) | zeitlinger |  | ✅ | ✅ | 25d |
| [Recover pulsar wrapped message ids (#18935)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18935) | zeitlinger | laurit | ✅ | ✅ | 17d |
| [Support excluding MDC attributes from capture-all (#18912)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18912) | philsttr | laurit<br>trask | ❌ | ✅ | 14d |
| [Add OSGi support for library instrumentation, API, and SDK extension artifacts (#18995)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18995) | royteeuwen |  | ✅ | ❌ | 12d |
| [Revive reduced servlet smoke test matrix on top of main (#18953)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18953) | zeitlinger |  | ✅ | ✅ | 8d |
| [Implement configurable metric bridge metric suppression (#19048)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19048) | somiljain2006 |  | ✅ | ✅ | 7d |
| [Add structured property support for declarative config metadata (#19077)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19077) | jaydeluca |  | ✅ | ✅ | 2d |
| [Gate process command attributes under v3 preview (#19082)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19082) | trask |  | ✅ | ✅ | 1d |
| [Add Cassandra JMX metrics target system (#19080)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19080) | jkoronaAtCisco | trask | ✅ | ✅ | 1d |
| [feat: add support for hbase-client 1.4 (#19087)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19087) | YaoYingLong |  | ✅ | ❌ | 1d |
| [Suppress duplicate warning log for same application logger factory class (#19088)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19088) | bhuvan-somisetty |  | ✅ | ✅ | 8h |

## Waiting on authors

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [Java agent insturmentation added for Failsafe 3.0 (#15759)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/15759) | onurkybsi | jaydeluca&nbsp;✅<br>laurit&nbsp;💬<br>trask&nbsp;💬 | ❌ | ❌ | 108d |
| [Add ability to customize span exception handling to instrumenter (#18530)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18530) | jaydeluca |  | ✅ | ❌ | 48d |
| [Add the Nacos-Client 2.x plugin (#18758)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18758) | peachisai | laurit&nbsp;💬 | ❌ | ❌ | 40d |
| [fix(webflux): register reactor hook in createWebFilter and add filter. (#18844)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18844) | amit306 | laurit<br>singhvibhanshu&nbsp;✔️ | ✅ | ✅ | 33d |
| [fix: separate default vs controller-telemetry metadata test suites so instrumentation-list.yaml reflects out-of-the-box telemetry (#18974)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18974) | mvanhorn | jaydeluca&nbsp;💬 | ✅ | ❌ | 15d |
| [Use Arguments.argumentSet() for named parameterized test cases (#18975)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18975) | zeitlinger | laurit<br>trask&nbsp;💬 | ✅ | ✅ | 15d |
| [feat(kafka): add messaging.kafka.cluster.id from client reflection (#18978)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18978) | shashank-reddy-nr |  | ✅ | ❌ | 15d |
| [Capture dubbo UNKNOWN requests (#16668)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/16668) | steverao | trask | ✅ | ✅ | 2d |
| [feat(spring-cloud-aws): instrument onMessage(Collection&lt;Message&lt;T&gt;&gt;) … (#19053)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19053) | Aryainguz | laurit&nbsp;💬<br>trask | ✅ | ✅ | 2d |
| [getModuleGroup removal using common class-loaders for instrumentation modules. (#18859)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18859) | SylvainJuge | JonasKunz&nbsp;✅<br>laurit&nbsp;💬<br>SylvainJuge<br>trask | ✅ | ✅ | 1d |

## Waiting on external

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [chore(deps): update plugin org.jetbrains.kotlin.jvm to v2.4.0 (#18948)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18948) | app/renovate | laurit | ❌ | ✅ | 17d |

## Draft pull requests

| PR | Author | Updated |
|---|---|:---:|
| [Draft: init spring-ai instrumentation (#15064)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/15064) | Cirilla-zmh | 87d |
| [Rename setCaptured* to setCapture* to have a single convention (#17154)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/17154) | trask | 86d |
| [ci: migrate to flint v2 for linting (#17759)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/17759) | zeitlinger | 65d |
| [Add network timing attributes to okhttp3 library (#15664)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/15664) | surbhiia | 62d |
| [Add NullAway to javaagent-tooling and javaagent (#17719)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/17719) | zeitlinger | 60d |
| [Migrate generative AI semantic conventions to OTel 1.37.0 (#15268)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/15268) | Cirilla-zmh | 52d |
| [Capture gRPC UNKNOWN requests (#16214)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/16214) | trask | 46d |
| [Retrieve gRPC `server.address`/`server.port` from gRPC target (#16161)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/16161) | trask | 46d |
| [Auto-regenerate gh-aw lock files in renovate PRs (#18865)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18865) | trask | 30d |
| [Add example declarative configuration doc (#17854)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/17854) | jaydeluca | 24d |
| [Tracking package and module name alignment (#18428)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18428) | trask | 24d |
| [Add support for capturing and extracting Dubbo response status codes (#16688)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/16688) | steverao | 11d |
| [Unify database batch tests into parameterized scenario tests (#19019)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19019) | trask | 9d |
| [switch non-inlined instrumentation by default + update doc (#19076)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19076) | SylvainJuge | 2d |

<details>
<summary>Diagnostics</summary>

```text
PR #19082
llm: PRRT_kwDODJKVX86MYTMr -> reviewer (The only comment is from the author explaining that declarative configuration no longer emits these attributes and linking an external issue; the reviewer/maintainer would need to acknowledge or decide whether that explanation is sufficient.)

PR #19080
llm: pr-conversation -> none (The latest comment only adds CCs and does not ask for a reply, change, or approval; no concrete follow-up is implied.)

PR #19053
llm: PRRT_kwDODJKVX86LLHT1 -> author (The latest comment is a reviewer asking for a different trace structure, so the author needs to update the implementation or reply with a correction.)

PR #19048
llm: pr-conversation -> reviewer (The author asked a reviewer to review the PR, so the next action is on the reviewer.)

PR #18978
llm: PRRT_kwDODJKVX86JLhy- -> author (The latest bot review comment points out a potential bug and asks for a code change, so the PR author needs to respond and update the implementation.)
llm: PRRT_kwDODJKVX86JLhzr -> author (The last comment is a bot review noting a likely bug and suggesting a code change, so the PR author needs to act on it.)
llm: PRRT_kwDODJKVX86JLh0d -> author (The latest comment is a reviewer bot suggesting a code change (negative caching / caching reflective lookups), so the PR author needs to address or जवाब to the review.)
llm: PRRT_kwDODJKVX86JLh0x -> author (The bot review comment flags a likely hot-path performance issue and suggests a cache/backoff fix, so the PR author needs to respond or implement a change.)

PR #18975
llm: PRRT_kwDODJKVX86JHnK3 -> author (The latest review comment from automation asks for a method/display-name rename to match the tested behavior, so the PR author needs to update the test.)
llm: PRRT_kwDODJKVX86JHnLi -> author (The latest bot review comment flags a robustness issue in the script and suggests a code change, so the PR author needs to act.)
llm: PRRT_kwDODJKVX86JLSCF -> author (The reviewer asked the PR author to add the import to StaticImportFormatter.kt, so the next action is on the author.)
llm: pr-conversation -> none (The reviewer only gave positive feedback, and the author’s reply is a completed acknowledgement with no follow-up requested.)

PR #18974
llm: PRRT_kwDODJKVX86JD0Ud -> author (The only comment is a bot review suggesting a code change to `testControllerTelemetry`, so the PR author needs to update the build script or respond.)
llm: PRRT_kwDODJKVX86JD0U3 -> author (The latest comment is a bot review requesting a code change in the PR, so the author needs to update the implementation.)
llm: PRRT_kwDODJKVX86JD0VD -> author (The bot flagged a concrete build.gradle issue and asked for a change to the PR, so the author needs to update the code or respond.)
llm: PRRT_kwDODJKVX86JD0VT -> author (The latest comment is a review bot flagging a likely task-splitting bug and explicitly suggests a code change, so the PR author needs to update the build logic.)
llm: PRRT_kwDODJKVX86JD0Vk -> author (The latest comment is a review bot request to add `exclude("**/server/**")`, so the PR author needs to update the build script.)
llm: PRRT_kwDODJKVX86KRup2 -> author (A reviewer said the file changes can be reverted because they had no telemetry effect, so the author needs to respond and make that change or push back.)
llm: PRRT_kwDODJKVX86KRu1n -> author (The reviewer asked to revert the changes in this file, so the PR author needs to act.)
llm: PRRT_kwDODJKVX86KRx2B -> author (A reviewer asked for a code change in this PR, so the author needs to update the build script and respond.)
llm: PRRT_kwDODJKVX86KRzA2 -> author (The latest comment is from a reviewer suggesting the author revert the file change, so the author needs to act or respond.)
llm: PRRT_kwDODJKVX86KQwF_ -> author (The reviewer suggests undoing the file change and says Ratpack is fine as-is, so the PR author needs to act on that feedback.)

PR #18948
llm: pr-conversation -> external (The only comment reports a CodeQL upstream limitation and links an external issue; the thread is blocked on that external fix rather than on an in-repo reply or change.)

PR #18935
llm: pr-conversation -> reviewer (The author says they already pushed a fix and the branch is rerunning CI, so the ball is back with the reviewer to re-check the updated PR.)

PR #18912
llm: pr-conversation -> reviewer (The author replied with the requested refactor and is now asking for guidance on the new dependency and muzzle failures, so the reviewer/maintainer needs to answer next.)

PR #18859
llm: PRRT_kwDODJKVX86MkdDF -> author (A reviewer asked a concrete code question/suggestion (“can't you use `moduleCl` here too?”), so the PR author needs to respond or make the change.)

PR #18844
llm: PRRT_kwDODJKVX86Ej8W0 -> author (The latest comment is a bot review requesting a code change (`@BeforeEach` reset), so the PR author needs to update the test.)
llm: PRRT_kwDODJKVX86Ej8YC -> author (The bot raised an API design issue and suggested a change; the PR author needs to decide and either update the code/Javadocs or respond.)
llm: PRRT_kwDODJKVX86Ej8YS -> author (The reviewer bot raised a design issue and suggested a change; the PR author needs to respond by adjusting the API or explaining why the distinction should remain.)
llm: PRRT_kwDODJKVX86Ej8Ym -> author (A reviewer bot raised a code concern and suggested a fix, so the PR author needs to respond or update the implementation.)

PR #18758
llm: PRRT_kwDODJKVX86CsCdm -> author (The only comment is a bot review noting missing agent-level test coverage and implying the PR needs an additional test before it can be safe.)
llm: PRRT_kwDODJKVX86EwMsv -> author (A reviewer requested removing `isMethod()`, and there is no author follow-up yet, so the author needs to respond or update the PR.)
llm: PRRT_kwDODJKVX86ExaJ8 -> author (A reviewer suggested a code change (`instanceof AbstractNamingRequest`) and there’s no author follow-up yet, so the PR author needs to respond or update the code.)
llm: PRRT_kwDODJKVX86ExjUC -> author (A reviewer asked a direct question about the implementation (`why response_error?`), so the author needs to जवाब/justify or adjust the code.)
llm: PRRT_kwDODJKVX86ExkXW -> author (A reviewer asked for a code change and there is no author follow-up yet, so the PR author needs to respond or update the implementation.)
llm: PRRT_kwDODJKVX86Exl3z -> author (A reviewer requested a code change (use a SpanStatusExtractor instead of creating an exception), and the author has not replied yet.)
llm: PRRT_kwDODJKVX86Exnj4 -> author (A reviewer noted the convention for missing values, so the author needs to update the code or reply.)
llm: PRRT_kwDODJKVX86ExsRk -> author (A reviewer flagged the items as unnecessary and the thread is unresolved, so the PR author needs to respond or adjust the change.)
llm: PRRT_kwDODJKVX86Exsqz -> author (A reviewer suggested changing the Gradle test setup, so the PR author needs to update the code or respond.)
llm: PRRT_kwDODJKVX86EyCqp -> author (The reviewer asked whether the file was manually edited and suggested the latest version should be 3.2.1, so the PR author needs to जवाब/adjust the change.)
llm: PRRT_kwDODJKVX86EyF1U -> author (The reviewer suggested changing the helper class pattern, so the PR author needs to respond and either update the code or explain why not.)

PR #18530
llm: PRRT_kwDODJKVX86A1hrH -> author (The latest comment is a review bot asking for a code/comment fix or version clarification, so the PR author needs to respond or update the test.)
llm: PRRT_kwDODJKVX86A1hrQ -> author (A bot reviewer flagged a concrete code change and no human follow-up has occurred, so the PR author needs to update the implementation.)
llm: PRRT_kwDODJKVX86A1hrV -> author (The latest comment is a bot review suggesting a code change to preserve stacktrace semantics, so the PR author needs to respond or update the implementation.)

PR #18090
llm: pr-conversation -> none (The author addressed the reviewer’s concern by changing the approach to normalize declarative-config inputs to empty properties, and there’s no remaining request or follow-up in the thread.)

PR #17816
llm: pr-conversation -> none (The reviewer answered the author’s question and clarified it was just a local/manual run, with no follow-up requested.)

PR #16668
llm: PRRT_kwDODJKVX86MJ-gq -> author (The bot flagged `HelloServiceErrorImpl` as dead code and asked to either wire it into a test or remove it, so the PR author needs to act.)
llm: PRRT_kwDODJKVX86MJ-hR -> author (The only comment is a bot review asking to change the assertions to `hasAttributesSatisfyingExactly(...)`, so the PR author needs to update the tests.)
llm: PRRT_kwDODJKVX86MJ-hl -> author (The reviewer bot requested a code change and there’s no follow-up reply yet, so the author needs to update the PR.)
llm: PRRT_kwDODJKVX86MJ-iC -> author (A bot review comment requests a code change, so the PR author needs to update the test helper usage.)
llm: PRRT_kwDODJKVX86MJ-ih -> author (The latest comment is a review suggestion from the bot asking for a rename, so the PR author needs to make or जवाब to the change.)
llm: PRRT_kwDODJKVX86MJ-iu -> author (The latest comment is a reviewer bot asking for naming changes to several `satisfies(...)` lambdas, so the author needs to update the PR.)

PR #15759
llm: PRRT_kwDODJKVX85zhorc -> author (The reviewer raised a policy concern about default-disabled instrumentation, so the author needs to respond or adjust the PR.)
llm: PRRT_kwDODJKVX858EBbi -> author (A reviewer requested a concrete ordering change in `settings.gradle.kts`, so the PR author needs to update the file and respond.)
llm: PRRT_kwDODJKVX858EBbl -> author (A reviewer requested a code change: remove `onThrowable` from the exit advice. The author needs to update the PR or जवाब back.)
llm: PRRT_kwDODJKVX858EBbq -> author (A reviewer left an unresolved style suggestion and there is no author follow-up yet, so the PR author needs to update the code or respond.)
llm: PRRT_kwDODJKVX858EBbs -> author (A reviewer flagged a risky reflection-based coupling and suggested follow-up changes; the author needs to respond or update the PR.)
llm: PRRT_kwDODJKVX858EBbt -> author (The reviewer requested a code change in test code: remove the `@Nullable` import and annotation, so the PR author needs to update the file.)
llm: PRRT_kwDODJKVX858EBbv -> author (A reviewer flagged a change and suggested removing `@Nullable`; the thread is unresolved, so the PR author needs to update the test code or respond.)
llm: PRRT_kwDODJKVX858EBby -> author (A reviewer noted a redundant fully qualified `RetryPolicy` reference and suggested a code change, so the PR author needs to update the code or respond.)
llm: PRRT_kwDODJKVX858EBb0 -> author (A reviewer asked to remove a leftover comment, so the PR author needs to update the file and reply.)
llm: PRRT_kwDODJKVX858EBb1 -> author (A reviewer requested dropping shared Mockito dependencies from the testing module, so the PR author needs to update the build configuration or जवाब back.)
llm: PRRT_kwDODJKVX858EBbr -> reviewer (The author replied with a proposed solution and asked the reviewer if it makes sense, so the ball is back with the reviewer.)

```

</details>

_Approvers may [force a refresh](https://github.com/open-telemetry/shared-workflows/actions/workflows/pull-request-dashboard.yml)._

