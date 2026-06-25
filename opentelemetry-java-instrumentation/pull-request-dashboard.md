> [!NOTE]
> Open non-draft PRs grouped by who is expected to act next. Draft PRs are listed separately. The grouping is partly performed by an LLM ([source](https://github.com/open-telemetry/shared-workflows/blob/main/.github/scripts/pull-request-dashboard/dashboard.py)) and could contain mistakes.
>
> Reviewers column: ✅ approved · ✔️ approved (non-code-owner) · 💬 open thread · 🔴 changes requested.

## Waiting on maintainers

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [feat: Add ConfigPropertiesBackedConfigProvider options for extensions and distros (#15835)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/15835) | aviralgarg05 | robsunday&nbsp;✔️<br>trask<br>zeitlinger&nbsp;✅ | ❌ | ✅ | 51d |
| [Align DynamoDB batch telemetry with database batch semantics (#19034)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19034) | trask | laurit&nbsp;✅ | ✅ | ✅ | 16h |
| [feat: add reactive command support for lettuce-4.0 instrumentation (#19071)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19071) | YaoYingLong | trask&nbsp;✅ | ✅ | ✅ | 15h |

## Waiting on reviewers

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [Add InstrumentationDefaults helper to declarative-config-bridge (#17816)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/17816) | zeitlinger | laurit<br>trask | ✅ | ✅ | 44d |
| [docs(agent-extension-api): mark ConfigProperties &#64;Nullable where null is possible (#18090)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18090) | zeitlinger | laurit | ✅ | ✅ | 37d |
| [test: parameterize KubernetesRequestUtilsTest cases (#18812)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18812) | zeitlinger |  | ✅ | ✅ | 23d |
| [getModuleGroup removal using common class-loaders for instrumentation modules. (#18859)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18859) | SylvainJuge | JonasKunz&nbsp;✅<br>laurit&nbsp;💬<br>SylvainJuge<br>trask | ✅ | ✅ | 23d |
| [Recover pulsar wrapped message ids (#18935)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18935) | zeitlinger | laurit | ✅ | ✅ | 15d |
| [Support excluding MDC attributes from capture-all (#18912)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18912) | philsttr | laurit<br>trask | ❌ | ✅ | 12d |
| [Add OSGi support for library instrumentation, API, and SDK extension artifacts (#18995)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18995) | royteeuwen |  | ✅ | ❌ | 11d |
| [Revive reduced servlet smoke test matrix on top of main (#18953)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18953) | zeitlinger |  | ✅ | ✅ | 6d |
| [Implement configurable metric bridge metric suppression (#19048)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19048) | somiljain2006 |  | ✅ | ✅ | 6d |
| [fix(lettuce): set span status for Redis command errors in \[5.1.0, 6.0.0) (#19075)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19075) | YaoYingLong |  | ✅ | ✅ | 6h |
| [Add structured property support for declarative config metadata (#19077)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19077) | jaydeluca |  | ✅ | ✅ | 3h |
| [Fix ambiguous IPv6 address in db.connection_string for MySQL/MariaDB (#19078)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19078) | bhuvan-somisetty |  | ✅ | ✅ | 3h |
| [Add Cassandra JMX metrics target system (#19080)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19080) | jkoronaAtCisco |  | ❌ | ✅ | 1h |
| [Reduce gradle warnings (#19079)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19079) | laurit |  | ✅ | ✅ | 1h |

## Waiting on authors

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [Java agent insturmentation added for Failsafe 3.0 (#15759)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/15759) | onurkybsi | jaydeluca&nbsp;✅<br>laurit&nbsp;💬<br>trask&nbsp;💬 | ❌ | ❌ | 106d |
| [Add ability to customize span exception handling to instrumenter (#18530)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18530) | jaydeluca |  | ✅ | ❌ | 47d |
| [Add the Nacos-Client 2.x plugin (#18758)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18758) | peachisai | laurit&nbsp;💬 | ❌ | ❌ | 38d |
| [fix(webflux): register reactor hook in createWebFilter and add filter. (#18844)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18844) | amit306 | laurit<br>singhvibhanshu&nbsp;✔️ | ✅ | ✅ | 31d |
| [fix: separate default vs controller-telemetry metadata test suites so instrumentation-list.yaml reflects out-of-the-box telemetry (#18974)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18974) | mvanhorn | jaydeluca&nbsp;💬 | ✅ | ✅ | 13d |
| [Use Arguments.argumentSet() for named parameterized test cases (#18975)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18975) | zeitlinger | laurit<br>trask&nbsp;💬 | ✅ | ✅ | 13d |
| [feat(kafka): add messaging.kafka.cluster.id from client reflection (#18978)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18978) | shashank-reddy-nr |  | ✅ | ❌ | 13d |
| [feat(spring-cloud-aws): instrument onMessage(Collection&lt;Message&lt;T&gt;&gt;) … (#19053)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19053) | Aryainguz | laurit&nbsp;💬<br>trask&nbsp;💬 | ✅ | ✅ | 20h |
| [Add Lettuce batch span support (#19055)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19055) | trask | laurit&nbsp;💬⁠✅ | ✅ | ✅ | 11h |
| [Capture dubbo UNKNOWN requests (#16668)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/16668) | steverao | trask | ✅ | ✅ | 10h |
| [Add Jedis pipeline span batch support (#19054)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19054) | trask | laurit&nbsp;💬⁠✅ | ✅ | ✅ | 8h |

## Waiting on external

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [chore(deps): update plugin org.jetbrains.kotlin.jvm to v2.4.0 (#18948)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18948) | app/renovate | laurit | ❌ | ✅ | 15d |

## Draft pull requests

| PR | Author | Updated |
|---|---|:---:|
| [Draft: init spring-ai instrumentation (#15064)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/15064) | Cirilla-zmh | 85d |
| [Rename setCaptured* to setCapture* to have a single convention (#17154)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/17154) | trask | 84d |
| [ci: migrate to flint v2 for linting (#17759)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/17759) | zeitlinger | 63d |
| [Add network timing attributes to okhttp3 library (#15664)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/15664) | surbhiia | 60d |
| [Add NullAway to javaagent-tooling and javaagent (#17719)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/17719) | zeitlinger | 58d |
| [Migrate generative AI semantic conventions to OTel 1.37.0 (#15268)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/15268) | Cirilla-zmh | 50d |
| [Capture gRPC UNKNOWN requests (#16214)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/16214) | trask | 44d |
| [Retrieve gRPC `server.address`/`server.port` from gRPC target (#16161)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/16161) | trask | 44d |
| [Auto-regenerate gh-aw lock files in renovate PRs (#18865)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18865) | trask | 28d |
| [Add example declarative configuration doc (#17854)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/17854) | jaydeluca | 23d |
| [Tracking package and module name alignment (#18428)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/18428) | trask | 22d |
| [Add support for capturing and extracting Dubbo response status codes (#16688)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/16688) | steverao | 9d |
| [Unify database batch tests into parameterized scenario tests (#19019)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19019) | trask | 7d |
| [switch non-inlined instrumentation by default + update doc (#19076)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19076) | SylvainJuge | 4h |
| [Remove PR dashboard workflow (#19081)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/19081) | trask | 12m |

<details>
<summary>Diagnostics</summary>

```text
PR #19080
llm: pr-conversation -> reviewer (The author’s only comment is a CC/tag to reviewers, so the next action is on a reviewer/maintainer to respond or review.)

PR #19055
llm: PRRT_kwDODJKVX86MJ3Su -> author (A reviewer suggested limiting the batch query text, so the PR author needs to respond or make the change.)

PR #19054
llm: PRRT_kwDODJKVX86Lq7Rc -> reviewer (The author’s last comment only explains that the code was copied from another test file; it doesn’t ask for more author action, so the reviewer/maintainer has the next move.)
llm: PRRT_kwDODJKVX86Lq7Rm -> reviewer (The latest comment is from the author and appears to present a code change/diff, so the ball is back with a reviewer to inspect or respond.)
llm: PRRT_kwDODJKVX86Lq7Rq -> reviewer (The latest comment is from the author and appears to be a response/proposed code change, so the reviewer/maintainer is next to review or reply.)
llm: PRRT_kwDODJKVX86Lq7Rt -> reviewer (The latest comment is from the author and shows a proposed code change; the ball is with the reviewer/maintainer to review or respond.)
llm: PRRT_kwDODJKVX86Lq7Rz -> reviewer (The only comment is from the PR author and shows a code change/copy with no explicit follow-up needed from the author, so the ball is with a reviewer to respond or review.)
llm: PRRT_kwDODJKVX86Lq7R1 -> reviewer (The latest comment is from the PR author and presents a code change/diff, so the ball is back with the reviewer to respond or review it.)
llm: PRRT_kwDODJKVX86Lq7R5 -> reviewer (The latest comment is from the PR author, so the ball is with a reviewer/maintainer to respond or continue review.)
llm: PRRT_kwDODJKVX86Lq7R6 -> reviewer (The latest comment is from the author and appears to be a reply/update, so the ball is back with the reviewer to confirm or continue the review.)
llm: PRRT_kwDODJKVX86Lq7R7 -> reviewer (The latest comment is from the PR author and appears to present a code change/diff, so the ball is back with the reviewer to respond or review.)
llm: PRRT_kwDODJKVX86Lq7SC -> reviewer (The last comment is by the PR author and appears to propose/describe a code change, so the reviewer/maintainer is the next one expected to respond.)
llm: PRRT_kwDODJKVX86Lq7SF -> reviewer (The only comment is from the author sharing a code diff/copy; they’ve passed the ball back for review and there’s no sign the thread is closed.)
llm: PRRT_kwDODJKVX86Lq7SK -> reviewer (The latest comment is from the PR author and appears to be an update/diff with no request for further author-side work, so the ball is with the reviewer to respond or review.)
llm: PRRT_kwDODJKVX86Lq7SO -> reviewer (The only comment is from the PR author and presents a copied diff, so the ball is with a reviewer/maintainer to review or respond.)
llm: PRRT_kwDODJKVX86Lq7SW -> reviewer (The latest comment is from the PR author and appears to be a response/update, so the reviewer is next to review or reply.)
llm: PRRT_kwDODJKVX86Lq7Sa -> reviewer (The only comment is from the PR author and appears to present the change for review, so the next move is for a reviewer to respond or approve.)
llm: PRRT_kwDODJKVX86Lq7Sd -> reviewer (The latest comment is from the PR author and appears to be an implementation update/snippet, so the ball is back with the reviewer to review or respond.)
llm: PRRT_kwDODJKVX86MM5tj -> author (A reviewer left a suggestion about `@Deprecated`, so the author would need to decide whether to change it or respond.)
llm: PRRT_kwDODJKVX86MM6LJ -> author (The reviewer asked to update the supported frameworks doc, so the PR author needs to make that follow-up change.)
llm: PRRT_kwDODJKVX86MNP7O -> author (A reviewer asked an open question about limiting batch query text length, so the author needs to जवाब/decide and reply.)
llm: PRRT_kwDODJKVX86MNY4n -> author (The reviewer raised a concern that these should already be added automatically, so the author needs to respond or adjust the change.)

PR #19053
llm: PRRT_kwDODJKVX86MEDIK -> author (The only comment is a bot review nit asking to move a static field for consistency, so the author would need to update or जवाब/acknowledge it.)
llm: PRRT_kwDODJKVX86MEZwK -> author (A reviewer flagged a batch-tracing regression and asked for a code change or clearer multi-message semantics, so the PR author needs to respond or update the implementation.)
llm: PRRT_kwDODJKVX86LLHT1 -> author (The latest reviewer comment says the trace should be structured differently and points to the expected pattern, so the author needs to update the PR or respond.)

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
llm: PRRT_kwDODJKVX86JD0Ud -> author (The reviewer bot pointed out a concrete test-task configuration issue and suggested a fix; the PR author needs to update the build script.)
llm: PRRT_kwDODJKVX86JD0U3 -> author (The bot flagged a likely test-splitting bug and asked for a change in `testControllerTelemetry`, so the PR author needs to update the build script.)
llm: PRRT_kwDODJKVX86JD0VD -> author (The bot reviewer flagged a concrete build.gradle.kts change needed to avoid duplicate server test execution, so the PR author needs to update the task configuration.)
llm: PRRT_kwDODJKVX86JD0VT -> author (The latest comment is a bot review noting `testStableSemconv` likely needs to exclude `**/server/**` or be split further, so the PR author needs to update the build matrix or respond.)
llm: PRRT_kwDODJKVX86JD0Vk -> author (The latest comment is a bot review suggestion asking to add an exclusion in this build task, so the PR author needs to make the change or respond.)
llm: PRRT_kwDODJKVX86KRup2 -> author (A reviewer said the file changes should be reverted, so the author needs to act on that request.)
llm: PRRT_kwDODJKVX86KRu1n -> author (The reviewer says the file changes should be reverted, so the PR author needs to update the branch or respond to that request.)
llm: PRRT_kwDODJKVX86KRx2B -> author (A reviewer suggested replacing the `finalizedBy` block with explicit test entries in `instrumentations.sh`, so the PR author needs to update the code or respond.)
llm: PRRT_kwDODJKVX86KRzA2 -> author (The reviewer asked the PR author to revert a file change, so the next action is on the author.)
llm: PRRT_kwDODJKVX86KQwF_ -> author (The reviewer suggests undoing the file change and treating Ratpack as fine as-is, so the author needs to act on that feedback.)

PR #18948
llm: pr-conversation -> external (The only comment reports a CodeQL upstream limitation and links an external issue; the thread is blocked on that external fix rather than on an in-repo reply or change.)

PR #18935
llm: pr-conversation -> reviewer (The author says they already pushed a fix and the branch is rerunning CI, so the ball is back with the reviewer to re-check the updated PR.)

PR #18912
llm: pr-conversation -> reviewer (The author replied with the requested refactor and is now asking for guidance on the new dependency and muzzle failures, so the reviewer/maintainer needs to answer next.)

PR #18859
llm: PRRT_kwDODJKVX86GIxjb -> reviewer (The reviewer asked for justification, and the author replied with the runtime error explanation; the ball is back with the reviewer to acknowledge or continue the review.)
llm: PRRT_kwDODJKVX86MRHC9 -> reviewer (The reviewer asked for clarification, and the author replied with a proposed rephrasing, so the ball is back with the reviewer to confirm or continue the discussion.)

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

