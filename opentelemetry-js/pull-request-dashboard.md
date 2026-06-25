> [!NOTE]
> Open non-draft PRs grouped by who is expected to act next. Draft PRs are listed separately. The grouping is partly performed by an LLM ([source](https://github.com/open-telemetry/shared-workflows/blob/main/.github/scripts/pull-request-dashboard/dashboard.py)) and could contain mistakes.
>
> Reviewers column: ✅ approved · ✔️ approved (non-code-owner) · 💬 open thread · 🔴 changes requested.

## Waiting on maintainers

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [chore(deps): lock file maintenance (#6559)](https://github.com/open-telemetry/opentelemetry-js/pull/6559) | app/renovate | david-luna&nbsp;✅<br>dyladan<br>legendecas<br>pichlermarc&nbsp;✅<br>trentm | ⏳ | ✅ | 80d |
| [fix(opentelemetry-exporter-prometheus)!: default exporter host to localhost (#6599)](https://github.com/open-telemetry/opentelemetry-js/pull/6599) | cjihrig | legendecas&nbsp;✅<br>maryliag<br>pichlermarc | ✅ | ❌ | 70d |
| [docs(configuration): link README to the cross-SDK language support status doc (#6846)](https://github.com/open-telemetry/opentelemetry-js/pull/6846) | MikeGoldsmith | JamieDanielson<br>maryliag&nbsp;✅<br>pichlermarc&nbsp;✅ | ✅ | ✅ | 8h |

## Waiting on reviewers

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [fix(instrumentation-http): better solution for avoiding double-wrapping of http (#6491)](https://github.com/open-telemetry/opentelemetry-js/pull/6491) | trentm | david-luna<br>maryliag<br>trentm | ✅ | ❌ | 103d |
| [chore(deps): update ubuntu docker tag to v26 (#6635)](https://github.com/open-telemetry/opentelemetry-js/pull/6635) | app/renovate | dyladan<br>legendecas<br>pichlermarc<br>trentm | ✅ | ✅ | 59d |
| [feat(api): add factory pattern for TracerProvider (#6466)](https://github.com/open-telemetry/opentelemetry-js/pull/6466) | ida613 | dyladan | ✅ | ❌ | 52d |
| [feat(otlp-exporter-base): accept `fetch` parameter in `createFetchTransport`, and export `createFetchTransport`, `createRetryingTransport` and `FetchTransportParameters` (#6377)](https://github.com/open-telemetry/opentelemetry-js/pull/6377) | zakcutner | pichlermarc | ✅ | ❌ | 51d |
| [chore(deps): update dependency &#64;types/sinon to v21 (#6693)](https://github.com/open-telemetry/opentelemetry-js/pull/6693) | app/renovate | dyladan<br>legendecas<br>pichlermarc<br>trentm | ❌ | ✅ | 45d |
| [chore(deps): update dependency sinon to v22 (#6720)](https://github.com/open-telemetry/opentelemetry-js/pull/6720) | app/renovate | dyladan<br>legendecas<br>pichlermarc<br>trentm | ❌ | ✅ | 38d |
| [fix(deps): update opentelemetry-js monorepo to v2 (#6721)](https://github.com/open-telemetry/opentelemetry-js/pull/6721) | app/renovate | dyladan<br>legendecas<br>pichlermarc<br>trentm | ❌ | ✅ | 38d |
| [Add config option for Prometheus default aggregation (#6761)](https://github.com/open-telemetry/opentelemetry-js/pull/6761) | ArthurSens |  | ✅ | ❌ | 31d |
| [chore(deps): update ubuntu:24.04 docker digest to 786a8b5 (#6806)](https://github.com/open-telemetry/opentelemetry-js/pull/6806) | app/renovate | dyladan<br>legendecas<br>pichlermarc<br>trentm | ⏳ | ✅ | 9d |
| [fix(core): guard timeInputToHrTime against clock-skew misclassification (#6772) (#6773)](https://github.com/open-telemetry/opentelemetry-js/pull/6773) | MohammadYusif | JacksonWeber | ✅ | ✅ | 8d |
| [feat(api): add setClock and getTick methods to ContextAPI (#6816) (#6820)](https://github.com/open-telemetry/opentelemetry-js/pull/6820) | ipsitapp8 |  | ✅ | ✅ | 7d |
| [feat(sdk-metrics): metrics exemplars support (#6830)](https://github.com/open-telemetry/opentelemetry-js/pull/6830) | rnavarro |  | ✅ | ❌ | 6d |
| [chore(deps): update dependency msw to v2.14.6 (#6831)](https://github.com/open-telemetry/opentelemetry-js/pull/6831) | app/renovate | dyladan<br>legendecas<br>pichlermarc<br>trentm | ❌ | ✅ | 6d |
| [feat(sdk-node): wire up tracer_provider.sampler from declarative config (#6847)](https://github.com/open-telemetry/opentelemetry-js/pull/6847) | MikeGoldsmith |  | ✅ | ✅ | 1d |
| [perf(sdk-metrics): optionally capture active context for sync instruments (#6848)](https://github.com/open-telemetry/opentelemetry-js/pull/6848) | legendecas |  | ✅ | ✅ | 1d |
| [feat(sdk-trace-web,fetch,grpc,http,xml-http-request): only emit stable http metrics, spans and attributes (#6819)](https://github.com/open-telemetry/opentelemetry-js/pull/6819) | maryliag | JacksonWeber<br>pichlermarc | ✅ | ❌ | 1d |
| [fix(deps): update all patch versions (#6852)](https://github.com/open-telemetry/opentelemetry-js/pull/6852) | app/renovate | dyladan<br>legendecas<br>pichlermarc<br>trentm | ✅ | ✅ | 22h |
| [feat(propagator-env-carrier): make EnvironmentGetter read the current process.env (#6853)](https://github.com/open-telemetry/opentelemetry-js/pull/6853) | pellared | kamphaus&nbsp;✔️ | ❌ | ✅ | 8h |
| [chore(*): migrate use of sdk-trace-base and sdk-trace-node to sdk-trace (#6851)](https://github.com/open-telemetry/opentelemetry-js/pull/6851) | trentm | trentm | ✅ | ✅ | 6h |
| [doc(sdk-node): encourage configuration of NodeSDK that does not result in default resource attributes being excluded by accident (#6857)](https://github.com/open-telemetry/opentelemetry-js/pull/6857) | trentm | trentm | ✅ | ✅ | 10m |

## Waiting on authors

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [feat(sdk-metrics): wire exemplar support into metrics pipeline (#6483)](https://github.com/open-telemetry/opentelemetry-js/pull/6483) | CharlieTLe | dyladan&nbsp;💬 | ✅ | ❌ | 107d |
| [feat(sdk-metrics): add support for max scale for exponential histograms (#6493)](https://github.com/open-telemetry/opentelemetry-js/pull/6493) | andidev | dyladan&nbsp;💬 | ✅ | ❌ | 94d |
| [perf(sdk-metrics): improve performance of hashAttributes() util (#6515)](https://github.com/open-telemetry/opentelemetry-js/pull/6515) | gunjam | dyladan&nbsp;💬 | ❌ | ❌ | 94d |
| [perf(sdk-trace-base): avoid _getTime for default Span.startTime (#6528)](https://github.com/open-telemetry/opentelemetry-js/pull/6528) | daniellockyer | david-luna&nbsp;💬<br>dyladan | ✅ | ❌ | 87d |
| [feat(sdk): implement exporter metrics (#6480)](https://github.com/open-telemetry/opentelemetry-js/pull/6480) | anuraaga | overbalance&nbsp;💬⁠✔️<br>trentm&nbsp;✅ | ✅ | ❌ | 76d |
| [feat(opentelemetry-exporter-prometheus): add translation strategy support (#6653)](https://github.com/open-telemetry/opentelemetry-js/pull/6653) | cjihrig | ArthurSens&nbsp;💬<br>github-advanced-security&nbsp;💬<br>JacksonWeber&nbsp;💬 | ❌ | ❌ | 52d |
| [fix(instrumentation): lazily initialize require-in-the-middle for empty instrumentations (#6590)](https://github.com/open-telemetry/opentelemetry-js/pull/6590) | biw | pichlermarc | ❌ | ✅ | 45d |
| [fix(otlp-exporter-base): honor env proxy settings (#6660)](https://github.com/open-telemetry/opentelemetry-js/pull/6660) | cyphercodes | pichlermarc<br>raphael-theriault-swi&nbsp;✅<br>trentm&nbsp;💬 | ✅ | ✅ | 40d |
| [docs(otlp-exporter-base): document HTTP exporter options (#6735)](https://github.com/open-telemetry/opentelemetry-js/pull/6735) | macayu17 |  | ✅ | ✅ | 35d |
| [feat(opentelemetry-core,sdk-trace-base,sdk-logs): append exception.cause chain to ATTR_EXCEPTION_STACKTRACE (#6634)](https://github.com/open-telemetry/opentelemetry-js/pull/6634) | abhisheksurve45 | david-luna&nbsp;💬<br>legendecas&nbsp;💬 | ❌ | ❌ | 35d |
| [feat(otlp-grpc-exporter): add gRPC channelOptions as config parameter (#6332)](https://github.com/open-telemetry/opentelemetry-js/pull/6332) | vitorvasc | legendecas&nbsp;💬<br>pichlermarc | ✅ | ✅ | 21d |
| [fix(otlp-exporter-base): surface FetchTransport timeout as clean failure (#6751)](https://github.com/open-telemetry/opentelemetry-js/pull/6751) | devareddy05 | overbalance&nbsp;💬 | ✅ | ✅ | 13d |
| [refactor(build): migrate from tsc to tsdown with dual CJS/ESM exports (#6293)](https://github.com/open-telemetry/opentelemetry-js/pull/6293) | overbalance | david-luna&nbsp;💬<br>pichlermarc<br>raphael-theriault-swi&nbsp;💬 | ✅ | ❌ | 10d |
| [docs(configuration): add declarative config example for startNodeSDK() (#6834)](https://github.com/open-telemetry/opentelemetry-js/pull/6834) | MikeGoldsmith | maryliag&nbsp;💬 | ✅ | ✅ | 6d |
| [feat(sdk-logs): implement log processor metrics (#6554)](https://github.com/open-telemetry/opentelemetry-js/pull/6554) | anuraaga | JacksonWeber<br>trentm&nbsp;💬 | ❌ | ❌ | 6d |
| [chore: Add size-limit check on Pull Requests (#6706)](https://github.com/open-telemetry/opentelemetry-js/pull/6706) | JPeer264 | pichlermarc&nbsp;💬 | ❌ | ❌ | 1d |
| [feat(api, context-async): add experimental attach/detach functionality (#6845)](https://github.com/open-telemetry/opentelemetry-js/pull/6845) | pichlermarc | legendecas&nbsp;💬 | ✅ | ✅ | 1d |
| [feat(sdk-trace): add AlwaysRecordSampler (#6168)](https://github.com/open-telemetry/opentelemetry-js/pull/6168) | majanjua-amzn | david-luna&nbsp;✅<br>dyladan<br>pichlermarc<br>trentm&nbsp;✅ | ✅ | ❌ | 1d |

## Waiting on external

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [feat(otlp-exporter-base): make better use of retry timeout (#6260)](https://github.com/open-telemetry/opentelemetry-js/pull/6260) | jsokol805 | pichlermarc | ❌ | ✅ | 99d |

## Draft pull requests

| PR | Author | Updated |
|---|---|:---:|
| [api: add experimental trace decorator support (#5906)](https://github.com/open-telemetry/opentelemetry-js/pull/5906) | legendecas | 85d |
| [Entity-Resource prototype v3 (#6357)](https://github.com/open-telemetry/opentelemetry-js/pull/6357) | dyladan | 65d |
| [fix: cancel retries on shutdown (#6340)](https://github.com/open-telemetry/opentelemetry-js/pull/6340) | pichlermarc | 45d |
| [docs: add threat model document (#6676)](https://github.com/open-telemetry/opentelemetry-js/pull/6676) | pichlermarc | 37d |
| [refactor!: Do not use HrTime in browser instrumentations (#6555)](https://github.com/open-telemetry/opentelemetry-js/pull/6555) | dyladan | 37d |
| [feat(api): Integrate &#64;opentelemetry/api-logs package into &#64;opentelemetry/api as experimental (#4862)](https://github.com/open-telemetry/opentelemetry-js/pull/4862) | hectorhdzg | 25d |
| [PoC: widen 'Attributes' to support the extended AnyValue (#6579)](https://github.com/open-telemetry/opentelemetry-js/pull/6579) | trentm | 24d |
| [feat(api): widen Attributes values type to AnyValue, using unknown TS type (#6780)](https://github.com/open-telemetry/opentelemetry-js/pull/6780) | trentm | 23d |
| [refactor(sdk-node): model built-in exporter resolution on top of PluginComponentProvider spec (#6730)](https://github.com/open-telemetry/opentelemetry-js/pull/6730) | pichlermarc | 17d |
| [feat: add context attach/detach (#6387)](https://github.com/open-telemetry/opentelemetry-js/pull/6387) | pichlermarc | 6d |
| [chore(shim-opencensus): remove the `&#64;opentelemetry/shim-opencensus` package (#6843)](https://github.com/open-telemetry/opentelemetry-js/pull/6843) | trentm | 1d |

<details>
<summary>Diagnostics</summary>

```text
PR #6857
llm: PRRT_kwDOCxSf386MYYtL -> reviewer (The author has already replied and offered a separate PR option; the reviewer now needs to decide whether to accept that or close the thread.)

PR #6853
llm: pr-conversation -> reviewer (The author is asking specific reviewers/approvers to take a look, so the next action is on the reviewer side.)

PR #6846
llm: pr-conversation -> reviewer (The author answered the concern and updated this PR; they mentioned a separate PR for filling the shared matrix, so the next step is for the reviewer to re-check this thread.)

PR #6845
llm: PRRT_kwDOCxSf386MCnzR -> author (A reviewer asked for a design change (expose `using`/`Symbol.dispose` instead of `attach`/`detach`), so the author needs to जवाब/implement or push back.)

PR #6834
llm: PRRT_kwDOCxSf386K62j1 -> author (A reviewer asked whether the lockfile removals are correct and whether they came from another PR, so the author needs to जवाब/confirm or fix it.)
llm: PRRT_kwDOCxSf386K63dB -> author (The reviewer pointed out that the added timeout is unnecessary and doesn’t guarantee the flush, so the author needs to update the code or जवाब back.)
llm: PRRT_kwDOCxSf386K66aN -> author (A reviewer pointed out that the sentence may be confusing and asked for a clearer distinction, so the PR author needs to update or जवाब/respond.)
llm: PRRT_kwDOCxSf386K67Wt -> author (A reviewer left a concrete suggestion on the README, so the author needs to apply or respond to it.)

PR #6819
llm: pr-conversation -> reviewer (The author says "Comments addressed," so the next step is for the reviewer/maintainer to re-check and respond.)

PR #6773
llm: pr-conversation -> reviewer (The author says they made the requested simplification and updated the comment; the thread is now waiting on reviewer review/acknowledgment.)

PR #6751
llm: PRRT_kwDOCxSf386JORwp -> author (A reviewer asked for a code change (`diag.error` suggestion) and the thread is unresolved, so the PR author needs to respond or update the code.)
llm: PRRT_kwDOCxSf386JOX5j -> author (A reviewer suggested a concrete code change and there’s no author reply yet, so the PR author needs to update the implementation.)
llm: PRRT_kwDOCxSf386JOFtk -> author (A reviewer asked for a changelog entry to be moved into the unreleased section, so the PR author needs to make that edit.)

PR #6735
llm: PRRT_kwDOCxSf386DulQ_ -> author (The latest comment is a review bot request to rewrite the interface remark and remove misleading import guidance, so the PR author needs to update the code/docs.)
llm: PRRT_kwDOCxSf386DulR7 -> author (The bot reviewer raised documentation fixes and asked to adjust the table, so the PR author needs to update the README or respond.)
llm: PRRT_kwDOCxSf386DulSU -> author (The only comment is a bot review requesting README table fixes, so the PR author needs to update the document or जवाब back.)

PR #6706
llm: PRRT_kwDOCxSf386L8NP2 -> author (A reviewer suggested adding `persist-credentials: false`, so the PR author needs to update the workflow or respond.)
llm: PRRT_kwDOCxSf386L8PD_ -> author (A reviewer suggested changing the workflow node version to 26, so the PR author needs to update or respond.)
llm: PRRT_kwDOCxSf386L8QJ_ -> author (Reviewer requested pinning the exact action version in the workflow, so the PR author needs to make that change or जवाब back.)

PR #6660
llm: PRRT_kwDOCxSf386CgDCs -> author (A reviewer suggested a code change and the thread has no follow-up from the author, so the author needs to act.)
llm: PRRT_kwDOCxSf386CgDzC -> author (The reviewer left a code suggestion asking to change the implementation, so the author needs to respond or apply the fix.)
llm: PRRT_kwDOCxSf386CgGWE -> author (Reviewer suggested a narrower `proxyEnv` object and raised a design preference, so the PR author needs to respond or apply the change.)
llm: PRRT_kwDOCxSf386CgHa9 -> author (A reviewer asked to drop the test, so the PR author needs to make that change or respond.)
llm: PRRT_kwDOCxSf386CgKTC -> author (The reviewer asked whether `envProxyAgentOptions` should be applied before the caller’s `options`, which is an open implementation question for the PR author to answer or change.)
llm: pr-conversation -> author (The reviewer asked whether the author is still working on it and indicated merge depends on addressing review comments, so the author needs to respond or update the PR.)

PR #6653
llm: PRRT_kwDOCxSf385_O-RA -> author (CodeQL flagged a missing backslash-escaping issue and no author reply is present, so the author needs to address it.)
llm: PRRT_kwDOCxSf386CDrMd -> reviewer (The author answered the reviewer’s question with a justification and spec reference, so the ball is back with the reviewer to acknowledge or continue the review.)
llm: PRRT_kwDOCxSf386CRQJf -> author (The reviewer pointed out a formatting bug, and the author replied that they will fix it, so the next action is still on the author.)
llm: pr-conversation -> reviewer (The author replied and handed off by saying the tests were added in a separate PR, so the reviewer/maintainer needs to respond to that decision.)

PR #6635
llm: pr-conversation -> author (The reviewer raised a concern that the change feels premature, so the author needs to respond or adjust the PR.)

PR #6634
llm: PRRT_kwDOCxSf385-XOhA -> author (The latest comment is a reviewer/approver agreeing with the proposed change and implying the function should be updated to take the error directly, so the author needs to respond or implement it.)
llm: PRRT_kwDOCxSf386D1Sgx -> author (A reviewer requested a code change (declare a named interface) and no author reply followed, so the author still needs to respond or update the PR.)

PR #6590
llm: pr-conversation -> author (The last substantive comment is from a reviewer saying the issue should be closed and proposing a different approach, so by the thread heuristic the author still owes a reply/acknowledgement.)

PR #6559
llm: pr-conversation -> author (The latest comment is from a reviewer flagging a problem with the stability-days behavior, so the author needs to address or respond to it.)

PR #6554
llm: PRRT_kwDOCxSf3854qNdg -> author (The latest reviewer comment confirms the point and implies the PR still needs the author to decide or implement the internal mechanism change; the discussion is not closed.)

PR #6528
llm: PRRT_kwDOCxSf3853n9Dk -> author (A reviewer suggested an alternative implementation and asked for the author’s take, so the author needs to respond or update the PR.)
llm: pr-conversation -> author (The latest comment is from a reviewer/approver and raises unresolved concern about whether the benchmark improvement is real, so the author needs to respond or adjust the PR.)

PR #6515
llm: PRRT_kwDOCxSf3852HzQG -> author (The latest comment is from the reviewer/approver proposing a concrete approach and asking for the author's opinion, so the author needs to respond or implement.)

PR #6493
llm: PRRT_kwDOCxSf3852IHBf -> author (The latest comment is from a reviewer clarifying that the change is only internally breaking; the thread is unresolved and the author still needs to respond or adjust the changelog entry.)
llm: pr-conversation -> reviewer (The latest comment is from the PR author asking for help and a response, so the ball is with a reviewer/maintainer.)

PR #6483
llm: PRRT_kwDOCxSf385zTBLv -> author (A reviewer left a short follow-up (“same”) on an unresolved thread, so the author needs to address or respond.)
llm: pr-conversation -> author (A reviewer/outsider provided a rebased branch and explicitly handed it to the PR author to pull in or decide on a successor PR, so the author needs to respond.)

PR #6480
llm: PRRT_kwDOCxSf3855sXk0 -> reviewer (The author has replied with their rationale and no further action is explicitly requested from them, so the reviewer/maintainer would need to decide whether to accept that explanation or continue the discussion.)
llm: PRRT_kwDOCxSf3855seEr -> author (The reviewer’s last reply leaves the suggestion open as optional, so the author still needs to acknowledge or decide whether to make the change.)
llm: pr-conversation -> author (The reviewer asked for conflict resolution, and `current_conflicts` is still yes; the author’s last reply only addressed the `selfObsMeterProvider` naming, so the author still needs to act.)

PR #6466
llm: pr-conversation -> none (The reviewer’s question was answered, and the last comment only says the PR is being put on hold with no explicit follow-up request.)

PR #6377
llm: pr-conversation -> reviewer (The latest comment is from the author asking for another review, so the ball is with a reviewer/maintainer to respond.)

PR #6332
llm: PRRT_kwDOCxSf385quits -> reviewer (The PR author asked whether to deprecate both attributes, so the ball is with a reviewer/maintainer to answer or decide.)
llm: PRRT_kwDOCxSf386HH2c3 -> author (A reviewer asked for a code change/clarification about merging the option bags versus optional chaining, so the PR author needs to respond or update the implementation.)

PR #6293
llm: PRRT_kwDOCxSf386JmHsD -> reviewer (The author replied last with a brief pointer back to an earlier comment, so the ball is back with the reviewer to clarify or respond.)
llm: PRRT_kwDOCxSf386JpjTZ -> author (A reviewer asked for a code change (add the dependency as a devDependency instead of relying on Mocha transitively), so the author needs to update the PR.)
llm: PRRT_kwDOCxSf386JpncL -> none (The only comment is an informational note for other reviewers and does not request any follow-up, so no action is needed.)
llm: PRRT_kwDOCxSf386Jl7CM -> none (The reviewer’s last comment is a clear acknowledgment that closes the discussion, so no follow-up is needed.)
llm: PRRT_kwDOCxSf386JlgiC -> reviewer (The author’s last comment is a follow-up note, and the reviewer has the next opportunity to acknowledge or close the thread.)

PR #6260
llm: pr-conversation -> external (The reviewer says this PR is blocked on an external opentelemetry-js PR and similar SDK changes before it can merge.)

PR #6168
llm: pr-conversation -> author (The latest reviewer comment is an approval with concrete follow-up suggestions (README section and possibly an issue), so the author still needs to respond or implement those changes.)

```

</details>

_Approvers may [force a refresh](https://github.com/open-telemetry/shared-workflows/actions/workflows/pull-request-dashboard.yml)._

