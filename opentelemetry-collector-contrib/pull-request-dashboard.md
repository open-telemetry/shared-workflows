> [!NOTE]
> Open non-draft PRs grouped by who is expected to act next. Draft PRs are listed separately. The grouping is partly performed by an LLM ([source](https://github.com/open-telemetry/shared-workflows/blob/main/.github/scripts/pull-request-dashboard/dashboard.py)) and could contain mistakes.
>
> Reviewers column: ✅ approved · ✔️ approved (non-code-owner) · 💬 open thread · 🔴 changes requested.

## Waiting on reviewers

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [\[receiver/mysql\] Add service.instance.id resource attribute (#45444)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/45444) | aknuds1 | dashpole | ❌ | ❌ | 155d |
| [\[processor/transform\] Instrument the transform processor to emit traces (#44849)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/44849) | alexcams | atoulme<br>fatsheep9146<br>meldegwi&nbsp;✔️<br>songy23 | ✅ | ✅ | 79d |
| [\[receiver/postgresql\] Add receiver.postgresql.useOTelSemconv feature gate for OTel semconv resource model (#45345)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/45345) | aknuds1 | atoulme<br>axw<br>cyrille-leclerc&nbsp;✔️<br>dmitryax<br>edmocosta<br>lmolkova&nbsp;✔️<br>XSAM&nbsp;✔️ | ❌ | ❌ | 74d |
| [\[httpcheck\] fix incorrect stale value in httpcheck.status when status_code changes (#44917)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/44917) | martin-schulze-e2m | atoulme<br>MovieStoreGuy | ❌ | ✅ | 27d |
| [\[pkg/ottl\] Implement AST constant folding for deterministic converters (#46506)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/46506) | Rajneesh180 | andrzej-stencel<br>edmocosta | ✅ | ❌ | 26d |
| [chore: add cloud.account.id to resource attributes (#46173)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/46173) | sfozz | atoulme<br>dyl10s&nbsp;✔️<br>evan-bradley<br>paulojmdias&nbsp;✔️<br>schmikei&nbsp;✔️ | ✅ | ❌ | 5d |

## Waiting on authors

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [\[receiver/prometheusreceiver\] Fix mitchellh/mapstructure vulnerability (#45773)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/45773) | aknuds1 | Aneurysm9&nbsp;💬<br>ArthurSens<br>atoulme&nbsp;💬<br>MovieStoreGuy<br>mx-psi | ✅ | ✅ | 96d |
| [\[receiver/journald\] feat: map journald fields (#46500)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/46500) | bruegth | atoulme&nbsp;💬<br>bachp&nbsp;💬⁠✔️<br>belimawr&nbsp;💬<br>dashpole<br>edmocosta<br>namco1992&nbsp;💬<br>thompson-tomo&nbsp;💬 | ❌ | ✅ | 91d |
| [Fix partial-failure retries and disable misleading top-level loadbalancing exporter metrics (#45027)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/45027) | iblancasa | dashpole | ❌ | ❌ | 55d |
| [Automation for validating components telemetry (#46315)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/46315) | vesari | ArthurSens<br>braydonk&nbsp;💬<br>ChrsMark<br>dashpole<br>jmacd<br>MovieStoreGuy | ✅ | ✅ | 50d |
| [\[pkg/stanza\] Add cache for `container` operator's k8s attributes extraction (#44487)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/44487) | Nitesh-vaidyanath | andrzej-stencel<br>atoulme&nbsp;💬<br>codeboten | ✅ | ❌ | 15d |
| [\[pkg/translator/azurelogs\] Support additional fields not in the common schema (#46165)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/46165) | rafaelrodrigues3092 | atoulme<br>axw&nbsp;✅<br>MikeGoldsmith&nbsp;✔️<br>pjanotti | ✅ | ✅ | 6d |

## Unknown

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [\[pkg/translator/jaeger\] preserve the sampled flag across Jaeger/OTLP translation (#49568)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49568) | s3onghyun |  | ? | ? | ? |
| [fix\[receiver/macos_unified_logging\]: Register the deprecated type alias for non-darwin platforms (#49566)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49566) | mrsillydog |  | ? | ? | ? |
| [\[pkg/translator/prometheusremotewrite\] preserve multiple underscores when permissive sanitization is enabled (#49565)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49565) | s3onghyun |  | ? | ? | ? |
| [chore: update version (#49563)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49563) | maryliag |  | ? | ? | ? |
| [\[chore\] \[receiver/azuremonitor\] Add namespace to logs (#49562)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49562) | celian-garcia |  | ? | ? | ? |
| [\[chore\]\[govuln\] revert to oldstable (#49560)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49560) | songy23 |  | ? | ? | ? |
| [\[chore\]\[internal/datadog\] Migrate cenkalti/backoff v4/v5 → v7 (#49559)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49559) | songy23 |  | ? | ? | ? |
| [\[receiver/rabbitmq\] Add exchange-level metrics (#49553)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49553) | shashank-reddy-nr |  | ? | ? | ? |
| [\[receiver/redis\] Enable redis.cmd.calls, redis.cmd.latency, and redis.cmd.usec by default (#49551)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49551) | ar-ash |  | ? | ? | ? |
| [\[processor/dynamic_sampling\] replace rule condition parser with OTTL (#49547)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49547) | MikeGoldsmith |  | ? | ? | ? |
| [\[chore\]\[k8s_attributes\] Add stability level and semconv refs (#49545)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49545) | ChrsMark |  | ? | ? | ? |
| [\[receiver/mongodb\] Ignore noisy `$indexStats` errors for views (#49543)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49543) | dyl10s |  | ? | ? | ? |
| [\[processor/transform/internal/logparsingfuncs\] Add ParseELF log parsing function (#49541)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49541) | Caleb-Hurshman |  | ? | ? | ? |
| [\[chore\] Add cooldown period to renovate.json (#49533)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49533) | mx-psi |  | ? | ? | ? |
| [\[receiver/azuremonitor\] Fix metric data loss and incorrect timestamps (#49532)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49532) | vishalmore90 |  | ? | ? | ? |
| [\[receiver/datadog\] Map Datadog k8s tags to OTel resource attributes (#49530)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49530) | SpencerTorres |  | ? | ? | ? |
| [\[receiver/datadog\] Translate Datadog span links and span events (#49528)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49528) | SpencerTorres |  | ? | ? | ? |
| [\[processor/dynamic_sampling\] honour incoming ot=th and ot=rv (#49520)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49520) | MikeGoldsmith |  | ? | ? | ? |
| [\[kubeletstats\] Calculate cpu usage on the fly behind feature gate (#49499)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49499) | ChrsMark |  | ? | ? | ? |
| [\[receiver/datadog\] Reconstruct 128-bit trace IDs for spans regardless of order (#49496)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49496) | SpencerTorres |  | ? | ? | ? |
| [\[processor/cumulativetodelta\] Rename cumulativetodelta to cumulative_to_delta with deprecated alias cumulativetodelta (#49487)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49487) | agarvin-nr |  | ? | ? | ? |
| [\[receiver/splunk_hec\] Fix `read_header_timeout` and `write_timeout` configuration options getting overridden (#49483)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49483) | dyl10s |  | ? | ? | ? |
| [\[pkg/translator/pprof\] Support translating OTLP sample attributes to pprof sample labels (#49481)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49481) | ms-hujia |  | ? | ? | ? |
| [\[reciever/jmx\]: remove deprecated code (#49478)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49478) | rogercoll |  | ? | ? | ? |
| [fix(receiver/memcached): report hit ratio, not miss ratio (#49470)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49470) | ogulcanaydogan |  | ? | ? | ? |
| [Use stable network attributes (#49465)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49465) | avy252004 |  | ? | ? | ? |
| [Add `dbauth` extension (#49464)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49464) | XSAM |  | ? | ? | ? |
| [\[processor/metricstransform\] Fix silent data loss for Summary metrics in aggregate_labels and aggregate_label_values operations (#49457)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49457) | himanshu130700 |  | ? | ? | ? |
| [\[pkg/translator/pprof\] reduce redundant work in sample conversion (#49454)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49454) | florianl |  | ? | ? | ? |
| [\[exporter/azuremonitor\] include url path in dependency name when no h… (#49448)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49448) | n0rq1 |  | ? | ? | ? |
| [test(prometheusexporter): regression test for add_metric_suffixes=false suppressing unit suffixes (#49446)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49446) | prabhaks |  | ? | ? | ? |
| [\[exporter/clickhouse\] update metrics schemas (#49438)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49438) | knudtty |  | ? | ? | ? |
| [Support resource detectors for service telemetry (#49428)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49428) | iblancasa |  | ? | ? | ? |
| [\[k8sattributesprocessor\] Add experimental kubelet `/pods` metadata source (#49427)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49427) | iblancasa |  | ? | ? | ? |
| [feature: skip index for event name (#49426)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49426) | emreyalvac |  | ? | ? | ? |
| [\[chore\]\[receiver/huaweicloudcesreceiver\] Migrate cenkalti/backoff v4/v5 → v7 (#49423)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49423) | songy23 |  | ? | ? | ? |
| [\[extension/opampextension\] Fix self-reported status events dropped by non-Reporter hosts (#49412)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49412) | CodeBlackwell |  | ? | ? | ? |
| [\[cumulativetodeltaprocessor\] Add histogram_fields config for selective field conversion (#49407)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49407) | Chau-Tran |  | ? | ? | ? |
| [fix(metricstransformprocessor): preserve absent histogram sum on merge (#49404)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49404) | CedricConday |  | ? | ? | ? |
| [\[exporter/azuremonitor\] Rename `azuremonitor` to `azure_monitor` (#49402)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49402) | dyl10s |  | ? | ? | ? |
| [\[exporter/azuremonitor\] add configurable HTTP success mapping (#49399)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49399) | tombiddulph |  | ? | ? | ? |
| [\[exporter/prometheusremotewriteexporter\] Retry transient WAL export errors and add configurable segment cache size (#49383)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49383) | charanck9 |  | ? | ? | ? |
| [\[exporter/opensearchexporter\] Validate attribute values in dynamic index names (#49362)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49362) | kylehounslow |  | ? | ? | ? |
| [\[receiver/awsecscontainermetricsreceiver\] Remove v1.21.0 import (#49360)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49360) | anarwal |  | ? | ? | ? |
| [\[receiver/httpcheck\] Use confighttp defaulting constructor (#49357)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49357) | swiatekm |  | ? | ? | ? |
| [\[pkg/ottl\] Add the `When` OTTL converter (#49356)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49356) | edmocosta |  | ? | ? | ? |
| [fix(pkg/translator/pprof): set instrumentation scope name when conver… (#49353)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49353) | avy252004 |  | ? | ? | ? |
| [\[receiver/postgresql\] Explicitly ignoring tables with AccessExclusiveLock to prevent receiver from stalling (#49352)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49352) | kartikgola |  | ? | ? | ? |
| [\[receiver/sqlserverreceiver\] Add per-index physical stats metrics (#49350)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49350) | akgrover |  | ? | ? | ? |
| [\[chore\]\[exporter/elasticsearch\] Document emitted document structure per mapping mode (#49349)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49349) | alliasgher |  | ? | ? | ? |

_More 135 PRs not shown_

_Approvers may [force a refresh](https://github.com/open-telemetry/shared-workflows/actions/workflows/pull-request-dashboard.yml)._

