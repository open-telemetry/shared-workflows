> [!NOTE]
> Open non-draft PRs grouped by who is expected to act next. Draft PRs are listed separately. The grouping is partly performed by an LLM ([source](https://github.com/open-telemetry/shared-workflows/blob/main/.github/scripts/pull-request-dashboard/dashboard.py)) and could contain mistakes.
>
> Reviewers column: ✅ approved · ✔️ approved (non-code-owner) · 💬 open thread · 🔴 changes requested.

## Waiting on reviewers

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [#850 Scope don't need attributes (#853)](https://github.com/open-telemetry/weaver/pull/853) | thompson-tomo | jsuereth | ✅ | ✅ | 351d |
| [Proposal: Define namespaces in yaml (#867)](https://github.com/open-telemetry/weaver/pull/867) | thompson-tomo | jerbly&nbsp;💬 | ✅ | ✅ | 339d |
| [Proposal: Offer packages (#872)](https://github.com/open-telemetry/weaver/pull/872) | thompson-tomo | jsuereth | ✅ | ✅ | 324d |
| [The aggregation params of metrics can be defined #844 (#845)](https://github.com/open-telemetry/weaver/pull/845) | thompson-tomo | jerbly<br>jsuereth<br>lmolkova | ✅ | ❌ | 322d |
| [Replace regex with semver crate (#1108)](https://github.com/open-telemetry/weaver/pull/1108) | ArthurSens | jsuereth<br>lquerel&nbsp;🔴 | ✅ | ✅ | 186d |
| [Tolerate unknown properties in resolved schema / published manifest (#1365)](https://github.com/open-telemetry/weaver/pull/1365) | lmolkova | jsuereth&nbsp;💬 | ✅ | ❌ | 62d |
| [Use semantic conventions v2 for `weaver registry infer` (#1334)](https://github.com/open-telemetry/weaver/pull/1334) | ArthurSens | jsuereth | ✅ | ✅ | 30d |
| [Warn on invalid markdown snippet markers (#1576)](https://github.com/open-telemetry/weaver/pull/1576) | ahfoysal |  | ❌ | ✅ | 1d |
| [Update gcr.io/oss-fuzz-base/base-builder-rust Docker digest to 8d1f850 (#1579)](https://github.com/open-telemetry/weaver/pull/1579) | app/renovate |  | ✅ | ✅ | 21h |

## Waiting on authors

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [fix: allow loading registries and policies from hidden directories (#992)](https://github.com/open-telemetry/weaver/pull/992) | kuklyy | lquerel&nbsp;🔴 | ✅ | ❌ | 247d |
| [Proposal: Introduce bundles (#871)](https://github.com/open-telemetry/weaver/pull/871) | thompson-tomo | jsuereth | ✅ | ✅ | 115d |

## Waiting on external

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [Document generated file header best practice (#1085)](https://github.com/open-telemetry/weaver/pull/1085) | cnaples79 | jsuereth | ❌ | ✅ | 203d |

## Draft pull requests

| PR | Author | Updated |
|---|---|:---:|
| [Introduce the ability to define namespaces #802 (#849)](https://github.com/open-telemetry/weaver/pull/849) | thompson-tomo | 339d |
| [Wip allow multiple dependencies part 2 - Use semver to resolve *some* dependency conflicts (#1377)](https://github.com/open-telemetry/weaver/pull/1377) | jsuereth | 68d |
| [\[wip\] Forward compatibility for definition schema (#1422)](https://github.com/open-telemetry/weaver/pull/1422) | lmolkova | 62d |
| [Consolidate v2 unstable file format warnings and improve diagnostic report styling (#1404)](https://github.com/open-telemetry/weaver/pull/1404) | app/copilot-swe-agent | 35d |
| [Expand dependency conflict resolution to allow different versions if major-version is compatible (#1573)](https://github.com/open-telemetry/weaver/pull/1573) | jsuereth | 1m |

_Approvers may [force a refresh](https://github.com/open-telemetry/shared-workflows/actions/workflows/pull-request-dashboard.yml)._

