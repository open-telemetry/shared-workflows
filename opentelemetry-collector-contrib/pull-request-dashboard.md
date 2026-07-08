> [!NOTE]
> Open non-draft PRs grouped by who is expected to act next. Draft PRs are listed separately. The grouping is partly performed by an LLM ([source](https://github.com/open-telemetry/shared-workflows/blob/main/.github/scripts/pull-request-dashboard/dashboard.py)) and could contain mistakes.
>
> Reviewers column: ✅ approved · ✔️ approved (non-code-owner) · 💬 open thread · 🔴 changes requested.

## Waiting on maintainers

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [\[receiver/kafkametricsreceiver\] update docs for Kafka 4.x / KRaft compat (#47748)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/47748) | dherges | atoulme&nbsp;✅<br>dmitryax | ❌ | ✅ | 15d |
| [\[extension/oidcauth\] Add ignore issuer in config and propagate it to go-oidc (#48513)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48513) | Dainerx | axw&nbsp;✅<br>edmocosta | ❌ | ✅ | 14d |

## Waiting on reviewers

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [\[receiver/mysql\] Add service.instance.id resource attribute (#45444)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/45444) | aknuds1 | dashpole | ❌ | ❌ | 155d |
| [\[processor/transform\] Instrument the transform processor to emit traces (#44849)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/44849) | alexcams | atoulme<br>fatsheep9146<br>meldegwi&nbsp;✔️<br>songy23 | ✅ | ✅ | 79d |
| [\[receiver/postgresql\] Add receiver.postgresql.useOTelSemconv feature gate for OTel semconv resource model (#45345)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/45345) | aknuds1 | atoulme<br>axw<br>cyrille-leclerc&nbsp;✔️<br>dmitryax<br>edmocosta<br>lmolkova&nbsp;✔️<br>XSAM&nbsp;✔️ | ❌ | ❌ | 74d |
| [\[pkg/ottl\] Sync entityRefs with changes in resource attributes (#47092)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/47092) | meldegwi | dmitryax<br>edmocosta | ✅ | ❌ | 72d |
| [\[extension/awssecretsmanagerauthextension\] Add AWS Secrets Manager extension (#48474)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48474) | meebok | MovieStoreGuy | ❌ | ❌ | 51d |
| [\[pkg/ottl\] Grammar slice standardization Step 1 (#47985)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/47985) | meldegwi | edmocosta&nbsp;💬<br>jmacd | ❌ | ❌ | 44d |
| [\[httpcheck\] fix incorrect stale value in httpcheck.status when status_code changes (#44917)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/44917) | martin-schulze-e2m | atoulme<br>MovieStoreGuy | ❌ | ✅ | 27d |
| [\[receiver/googlecloudpubsub\] add ModifyAckDeadline support for StreamingPull (#48213)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48213) | smurtanv | fatsheep9146 | ✅ | ✅ | 27d |
| [\[pkg/ottl\] Implement AST constant folding for deterministic converters (#46506)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/46506) | Rajneesh180 | andrzej-stencel<br>edmocosta | ✅ | ❌ | 26d |
| [\[cmd/telemetrygen\] honour --allow-export-failures in batch flushBuffer (#47651)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/47651) | alliasgher | atoulme<br>axw<br>bogdan-st&nbsp;✔️<br>braydonk | ✅ | ✅ | 26d |
| [feat(resourcedetectionprocessor): add retry with backoff config to resource detector processor (#48223)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48223) | KyriosGN0 | atoulme<br>paulojmdias&nbsp;💬 | ✅ | ❌ | 25d |
| [\[pkg/stanza/fileconsumer\] Require explicit top_n when sort_by is configured (#47445)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/47445) | Dylan-M | andrzej-stencel<br>atoulme<br>braydonk<br>paulojmdias&nbsp;✔️ | ✅ | ✅ | 22d |
| [\[exporter/file\] Fix orphaned lumberjack backup files after migration to timberjack (#47694)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/47694) | paulojmdias | dehaansa | ✅ | ✅ | 16d |
| [\[pkg/stanza/fileconsumer\] Skip files unchanged by path+mtime since the previous poll (#48039)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48039) | Dylan-M | atoulme<br>crobert-1 | ✅ | ❌ | 16d |
| [\[receiver/github\] Process pull-requests in reverse order to adhere to chronological ordering (#48578)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48578) | vladorf | adrielp&nbsp;✔️<br>ArthurSens | ✅ | ✅ | 16d |
| [\[cmd/telemetrygen\] support base paths in OTLP HTTP endpoints (#48064)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48064) | jeevan6996 | atoulme | ✅ | ✅ | 14d |
| [\[exporter/awscloudwatchlogs\]\[internal/aws/cwlogs\] configurable 1 MiB per-event cap + fix batch threshold conflation (#48559)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48559) | challahc | VihasMakwana | ✅ | ✅ | 14d |
| [\[receiver/githubreceiver\] retry on primary rate-limit errors (#48539)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48539) | nissessenap | andrzej-stencel | ❌ | ✅ | 13d |
| [\[receiver/kafkametricsreceiver\] Set kafka.cluster.alias when cluster_alias is defined (#47573) (#47574)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/47574) | skreuzer | pjanotti | ✅ | ❌ | 11d |
| [\[connector/signaltometrics\] Add feature gate to change default error_mode to ignore (#48434)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48434) | singhvishalkr | VihasMakwana | ✅ | ✅ | 10d |
| [\[receiver/journald\] add option to include original log record (#48243)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48243) | belimawr | andrzej-stencel<br>ArthurSens | ❌ | ✅ | 7d |
| [\[receiver/tlscheckreceiver\] allow scraping all certs (#48520)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48520) | sigmaris | codeboten<br>michael-burt&nbsp;✔️ | ✅ | ✅ | 6d |
| [\[receiver/postgresql\] stabilizing preciselagmetrics and connectionPool gates (#48618)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48618) | codeboten | atoulme<br>VihasMakwana | ❌ | ✅ | 5d |
| [chore: add cloud.account.id to resource attributes (#46173)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/46173) | sfozz | atoulme<br>dyl10s&nbsp;✔️<br>evan-bradley<br>paulojmdias&nbsp;✔️<br>schmikei&nbsp;✔️ | ✅ | ❌ | 5d |

## Waiting on authors

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [\[exporter/file\] fix nil pointer dereference during shutdown (#46947)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/46947) | Akash-Kumar-Sinha | Dainerx&nbsp;💬<br>dmitryax | ✅ | ✅ | 113d |
| [\[receiver/prometheusreceiver\] Fix mitchellh/mapstructure vulnerability (#45773)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/45773) | aknuds1 | Aneurysm9&nbsp;💬<br>ArthurSens<br>atoulme&nbsp;💬<br>MovieStoreGuy<br>mx-psi | ✅ | ✅ | 96d |
| [\[receiver/journald\] feat: map journald fields (#46500)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/46500) | bruegth | atoulme&nbsp;💬<br>bachp&nbsp;💬⁠✔️<br>belimawr&nbsp;💬<br>dashpole<br>edmocosta<br>namco1992&nbsp;💬<br>thompson-tomo&nbsp;💬 | ❌ | ✅ | 91d |
| [\[receiver/hostmetrics\] Add cputicks reader behind feature gate (#47972)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/47972) | salvatore-campagna | dmitryax<br>rogercoll&nbsp;💬⁠✔️ | ❌ | ✅ | 70d |
| [OpAMP Supervisor: Vision and Roadmap (#48350)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48350) | aunshc | cijothomas&nbsp;💬<br>tigrannajaryan&nbsp;💬<br>VihasMakwana | ❌ | ✅ | 56d |
| [Fix partial-failure retries and disable misleading top-level loadbalancing exporter metrics (#45027)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/45027) | iblancasa | dashpole | ❌ | ❌ | 55d |
| [\[extension/jaegerremotesampling\] disable grpc (#48440)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48440) | ansh0014 | andrzej-stencel<br>jmacd&nbsp;💬<br>yurishkuro&nbsp;💬 | ✅ | ❌ | 52d |
| [Automation for validating components telemetry (#46315)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/46315) | vesari | ArthurSens<br>braydonk&nbsp;💬<br>ChrsMark<br>dashpole<br>jmacd<br>MovieStoreGuy | ✅ | ✅ | 50d |
| [\[exporter/clickhouse\] Respect configured database when create_schema is false (#48050)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48050) | Vanshul97 | fatsheep9146<br>Frapschen&nbsp;✔️<br>SpencerTorres&nbsp;✔️ | ✅ | ✅ | 48d |
| [\[extension/filestorage\] Recover from add. bbolt goroutine panic (#48565)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48565) | briandavis-viz | iblancasa&nbsp;💬<br>songy23<br>VihasMakwana | ❌ | ❌ | 48d |
| [feat(connector): rename grafanacloud to grafana_cloud (#48017)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48017) | Frapschen | andrzej-stencel<br>atoulme<br>dmitryax<br>songy23 | ❌ | ❌ | 44d |
| [fix(isolationforestprocessor): trees never split + contamination_rate ignored (#47115)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/47115) | pathcl | aarvee11&nbsp;💬⁠✔️<br>atoulme<br>codeboten<br>iblancasa&nbsp;💬 | ❌ | ✅ | 23d |
| [\[receiver/docker_stats\] Introduce feature gate to align metrics with new container semantic conventions (#48081)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48081) | ben-trans | atoulme<br>jamesmoessis&nbsp;💬<br>MovieStoreGuy&nbsp;✅<br>pjanotti | ✅ | ❌ | 23d |
| [\[pkg/expohisto\] Add binary marshaling for Histogram (#48298)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48298) | lahsivjar | jmacd | ✅ | ✅ | 23d |
| [\[receiver/fluentforward\] Limit msgpack allocations (#48479)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48479) | dmitryax | TylerHelmuth | ✅ | ✅ | 22d |
| [\[pkg/pdatatest\] Add pmetricassert collection include matcher (#48472) (#48545)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48545) | harshitt13 | ArthurSens<br>dmitryax&nbsp;💬 | ✅ | ✅ | 21d |
| [\[receiver/vcenter\] Add metrics (#48098)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48098) | SCadilhac | evan-bradley | ❌ | ❌ | 20d |
| [extension.oidauthextension added support for multiple JWT sigs in extension oidc (#47849)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/47849) | michaelalang | atoulme&nbsp;💬<br>pavolloffay&nbsp;✔️<br>VihasMakwana | ✅ | ✅ | 16d |
| [\[pkg/stanza\] Add cache for `container` operator's k8s attributes extraction (#44487)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/44487) | Nitesh-vaidyanath | andrzej-stencel<br>atoulme&nbsp;💬<br>codeboten | ✅ | ❌ | 15d |
| [\[receiver/k8scluster\] Fix container.image.name and container.image.tag when status image is a bare digest (#46987)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/46987) | ricardbejarano | atoulme<br>ChrsMark&nbsp;💬<br>dmitryax<br>povilasv&nbsp;✔️ | ✅ | ✅ | 13d |
| [\[cmd/opampsupervisor\] Restart from last working remote config when fail to apply new one (#47853)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/47853) | douglascamata | dehaansa<br>dpaasman00&nbsp;💬<br>evan-bradley&nbsp;💬 | ✅ | ✅ | 8d |
| [\[pkg/translator/azurelogs\] Support additional fields not in the common schema (#46165)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/46165) | rafaelrodrigues3092 | atoulme<br>axw&nbsp;✅<br>MikeGoldsmith&nbsp;✔️<br>pjanotti | ✅ | ✅ | 6d |
| [\[receiver/azure_functions\] Support metrics for Event Hub trigger (#48105)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48105) | tetianakravchenko | mx-psi<br>zmoog&nbsp;✔️ | ❌ | ✅ | 5d |
| [\[receiver/oracledb\]: Add OS-level metrics from V$OSSTAT (#48459)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48459) | spathlavath | codeboten<br>crobert-1&nbsp;💬 | ✅ | ✅ | 20h |
| [\[receiver/oracledb\]: Add SGA component memory metrics from V$SGAINFO (#48542)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48542) | spathlavath | crobert-1&nbsp;💬<br>mx-psi | ✅ | ✅ | 19h |
| [\[receiver/webhookeventreceiver\] Make webhookeventreceiver to support HMAC signature authentication (#47189)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/47189) | steveduan-IDME | atoulme&nbsp;✅<br>ChrsMark<br>shalper2&nbsp;✔️<br>songy23 | ❌ | ✅ | 57m |

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

_More 95 PRs not shown_

_Approvers may [force a refresh](https://github.com/open-telemetry/shared-workflows/actions/workflows/pull-request-dashboard.yml)._

