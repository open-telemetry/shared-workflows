> [!NOTE]
> Open non-draft PRs grouped by who is expected to act next. Draft PRs are listed separately. The grouping is partly performed by an LLM ([source](https://github.com/open-telemetry/shared-workflows/blob/main/.github/scripts/pull-request-dashboard/dashboard.py)) and could contain mistakes.
>
> Reviewers column: ✅ approved · ✔️ approved (non-code-owner) · 💬 open thread · 🔴 changes requested.

## Waiting on maintainers

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [feat: Add ConfigPropertiesBackedConfigProvider options for extensions and distros (#15835)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/15835) | aviralgarg05 | robsunday&nbsp;✔️<br>trask<br>zeitlinger&nbsp;✅ | ❌ | ✅ | 55d |
| [Suppress duplicate warning log for same application logger factory class (#19088)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19088) | bhuvan-somisetty | laurit&nbsp;✅ | ✅ | ✅ | 3d |

## Waiting on reviewers

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [Add InstrumentationDefaults helper to declarative-config-bridge (#17816)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/17816) | zeitlinger | laurit<br>trask | ✅ | ✅ | 49d |
| [docs(agent-extension-api): mark ConfigProperties &#64;Nullable where null is possible (#18090)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18090) | zeitlinger | laurit | ✅ | ✅ | 42d |
| [test: parameterize KubernetesRequestUtilsTest cases (#18812)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18812) | zeitlinger |  | ✅ | ✅ | 28d |
| [Recover pulsar wrapped message ids (#18935)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18935) | zeitlinger | laurit | ✅ | ✅ | 20d |
| [Support excluding MDC attributes from capture-all (#18912)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18912) | philsttr | laurit<br>trask | ❌ | ✅ | 17d |
| [Revive reduced servlet smoke test matrix on top of main (#18953)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18953) | zeitlinger |  | ✅ | ✅ | 11d |
| [Implement configurable metric bridge metric suppression (#19048)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19048) | somiljain2006 |  | ✅ | ✅ | 10d |
| [Add structured property support for declarative config metadata (#19077)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19077) | jaydeluca |  | ✅ | ✅ | 5d |
| [fix(druid): optimize dataSourceName to resolve metrics high cardinality (#19108)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19108) | YaoYingLong |  | ✅ | ✅ | 3h |
| [fix(deps): update gradle develocity packages to v4.5.0 (#19109)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19109) | app/renovate |  | ✅ | ✅ | 2h |
| [Emit messaging operation metrics (publish/receive duration) from Kafka instrumentation (#19107)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19107) | maryantocinn |  | ✅ | ✅ | 1h |

## Waiting on authors

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [Java agent insturmentation added for Failsafe 3.0 (#15759)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/15759) | onurkybsi | jaydeluca&nbsp;✅<br>laurit&nbsp;💬<br>trask&nbsp;💬 | ❌ | ❌ | 111d |
| [Add ability to customize span exception handling to instrumenter (#18530)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18530) | jaydeluca |  | ✅ | ❌ | 51d |
| [Add the Nacos-Client 2.x plugin (#18758)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18758) | peachisai | laurit&nbsp;💬 | ❌ | ❌ | 43d |
| [fix(webflux): register reactor hook in createWebFilter and add filter. (#18844)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18844) | amit306 | laurit<br>singhvibhanshu&nbsp;✔️ | ✅ | ✅ | 36d |
| [fix: separate default vs controller-telemetry metadata test suites so instrumentation-list.yaml reflects out-of-the-box telemetry (#18974)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18974) | mvanhorn | jaydeluca&nbsp;💬 | ✅ | ❌ | 18d |
| [Use Arguments.argumentSet() for named parameterized test cases (#18975)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18975) | zeitlinger | laurit<br>trask&nbsp;💬 | ✅ | ✅ | 18d |
| [feat(kafka): add messaging.kafka.cluster.id from client reflection (#18978)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18978) | shashank-reddy-nr |  | ✅ | ❌ | 18d |
| [Capture dubbo UNKNOWN requests (#16668)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/16668) | steverao | trask | ✅ | ✅ | 5d |
| [feat(spring-cloud-aws): instrument onMessage(Collection&lt;Message&lt;T&gt;&gt;) … (#19053)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19053) | Aryainguz | laurit&nbsp;💬<br>trask | ✅ | ✅ | 5d |
| [Add Cassandra JMX metrics target system (#19080)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19080) | jkoronaAtCisco | laurit&nbsp;💬<br>SylvainJuge&nbsp;💬<br>trask | ✅ | ✅ | 1d |
| [Gate process command attributes under v3 preview (#19082)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19082) | trask | laurit&nbsp;✅ | ✅ | ✅ | 1d |
| [Add JFR metrics for virtual thread pinning and submit failures (#19092)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19092) | tsawada | laurit&nbsp;💬 | ✅ | ✅ | 1d |
| [feat: add commons pool2 instrumentation (#19091)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19091) | YaoYingLong | jaydeluca&nbsp;💬<br>laurit&nbsp;💬 | ✅ | ✅ | 23h |
| [feat: add support for hbase-client 1.4 (#19087)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19087) | YaoYingLong |  | ✅ | ❌ | 13h |
| [Add OSGi support for library instrumentation, API, and SDK extension artifacts (#18995)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18995) | royteeuwen | laurit&nbsp;💬 | ✅ | ❌ | 2h |
| [\[jdbc\] Capture custom object types in prepared statement parameter instrumentation (#19093)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19093) | CodingFabian | laurit | ✅ | ✅ | 1h |

## Waiting on external

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [chore(deps): update plugin org.jetbrains.kotlin.jvm to v2.4.0 (#18948)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18948) | app/renovate | laurit | ❌ | ✅ | 20d |

## Draft pull requests

| PR | Author | Updated |
|---|---|:---:|
| [Rename setCaptured* to setCapture* to have a single convention (#17154)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/17154) | trask | 89d |
| [ci: migrate to flint v2 for linting (#17759)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/17759) | zeitlinger | 67d |
| [Add network timing attributes to okhttp3 library (#15664)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/15664) | surbhiia | 65d |
| [Add NullAway to javaagent-tooling and javaagent (#17719)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/17719) | zeitlinger | 63d |
| [Migrate generative AI semantic conventions to OTel 1.37.0 (#15268)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/15268) | Cirilla-zmh | 55d |
| [Capture gRPC UNKNOWN requests (#16214)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/16214) | trask | 49d |
| [Retrieve gRPC `server.address`/`server.port` from gRPC target (#16161)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/16161) | trask | 49d |
| [Auto-regenerate gh-aw lock files in renovate PRs (#18865)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18865) | trask | 32d |
| [Add example declarative configuration doc (#17854)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/17854) | jaydeluca | 27d |
| [Tracking package and module name alignment (#18428)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18428) | trask | 27d |
| [Add support for capturing and extracting Dubbo response status codes (#16688)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/16688) | steverao | 13d |
| [Unify database batch tests into parameterized scenario tests (#19019)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19019) | trask | 12d |
| [Draft: init spring-ai instrumentation (#15064)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/15064) | Cirilla-zmh | 2h |
| [switch non-inlined instrumentation by default + update doc (#19076)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19076) | SylvainJuge | 2h |

<details>
<summary>Diagnostics</summary>

```text
PR #19093
llm: pr-conversation -> author (The reviewer raised open questions about `Serializable` IDs and `toString()` behavior and explicitly asked for suggestions, so the author needs to respond or adjust the PR.)

PR #19092
llm: PRRT_kwDODJKVX86NBhpy -> author (A reviewer suggested adding `@EnabledOnJre(JRE.JAVA_21)`, so the author needs to respond or make the change.)

PR #19091
llm: PRRT_kwDODJKVX86M_lZ8 -> author (The latest reviewer comment pushes back on the author’s proposal and leaves the point unresolved, so the author needs to respond or adjust the PR.)
llm: PRRT_kwDODJKVX86NPJGU -> author (A reviewer suggested a code change replacing `named(String.class)` in the current PR, so the author needs to apply or respond to it.)
llm: PRRT_kwDODJKVX86NQoDt -> author (Reviewer requested a change ('put in alphabetical order'); the author needs to update the docs or जवाब back.)
llm: PRRT_kwDODJKVX86NQlac -> reviewer (The author asked the reviewers to choose between two implementation approaches, so the next response is on the reviewer/maintainer side.)

PR #19087
llm: pr-conversation -> author (The latest comment is from the author and indicates the PR still has conflicts/work to be resolved, so the next action remains with the author.)

PR #19082
llm: PRRT_kwDODJKVX86MYTMr -> reviewer (The only comment is from the author explaining that declarative configuration no longer emits these attributes and linking an external issue; the reviewer/maintainer would need to acknowledge or decide whether that explanation is sufficient.)
llm: pr-conversation -> author (The latest comment is a reviewer approval with an open question/suggestion about whether sanitization should also be removed, so the author needs to जवाब/decide.)

PR #19080
llm: PRRT_kwDODJKVX86M82fx -> author (A reviewer/approver suggested wording changes to the metrics descriptions and the author has not replied, so the ball is with the author to update or respond.)
llm: PRRT_kwDODJKVX86M83yV -> none (The reviewer question was answered by another reviewer with the project’s stated convention, and no follow-up was requested in-thread.)
llm: PRRT_kwDODJKVX86NQhaq -> author (A reviewer left a concrete change request suggesting a `lowercase` modifier, so the PR author needs to update or respond.)
llm: PRRT_kwDODJKVX86NQkol -> author (A reviewer requested a code change to rename the metric suffix, so the PR author needs to update the file or respond.)
llm: PRRT_kwDODJKVX86NQ0bD -> author (A reviewer suggested removing the `.count` suffix, so the ball is with the PR author to apply or respond to the change.)
llm: PRRT_kwDODJKVX86NQ15e -> author (The last comment is a reviewer suggestion with no author reply yet, so the PR author needs to respond or apply the change.)
llm: PRRT_kwDODJKVX86NQ3x0 -> author (A reviewer left a code suggestion and there’s no author response yet, so the PR author needs to address or acknowledge it.)
llm: PRRT_kwDODJKVX86NQ4PP -> author (A reviewer left a suggested change and the author has not responded yet, so the ball is with the author.)
llm: PRRT_kwDODJKVX86NQ4lL -> author (A reviewer left a code suggestion with no follow-up from the author, so the PR author needs to respond or apply it.)
llm: PRRT_kwDODJKVX86NRLQT -> author (A reviewer asked for clarification and possible changes about overlapping Cassandra error metrics, so the PR author needs to जवाब/adjust the implementation.)
llm: PRRT_kwDODJKVX86NRNZB -> author (A reviewer suggested a possible metric breakdown and left the thread open; the author needs to respond or update the PR.)
llm: pr-conversation -> author (The latest comment is a reviewer/approver asking the author to choose how to handle `otel.jmx.target.system`, so the ball is with the author to जवाब/decide.)

PR #19053
llm: PRRT_kwDODJKVX86LLHT1 -> author (The latest comment is a reviewer asking for a different trace structure, so the author needs to update the implementation or reply with a correction.)

PR #19048
llm: pr-conversation -> reviewer (The author asked a reviewer to review the PR, so the next action is on the reviewer.)

PR #18995
llm: PRRT_kwDODJKVX86NSb16 -> author (A reviewer asked a naming question and no reply from the author is present, so the author needs to respond.)
llm: PRRT_kwDODJKVX86NShVS -> author (A reviewer raised a concern and asked whether OSGi manifests should be disabled for some instrumentations; the author needs to जवाब/respond or make the requested change.)

PR #18978
llm: PRRT_kwDODJKVX86JLhy- -> author (The latest bot review comment points out a potential bug and asks for a code change, so the PR author needs to respond and update the implementation.)
llm: PRRT_kwDODJKVX86JLhzr -> author (The last comment is a bot review noting a likely bug and suggesting a code change, so the PR author needs to act on it.)
llm: PRRT_kwDODJKVX86JLh0d -> author (A reviewer bot flagged a likely hot-path performance issue and asked for a caching/negative-cache change; the author needs to respond by implementing or addressing it.)
llm: PRRT_kwDODJKVX86JLh0x -> author (The only comment is a reviewer bot request to add caching/negative-cache behavior for repeated reflective failures, so the author needs to respond or implement a change.)

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

