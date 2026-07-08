> [!NOTE]
> Open non-draft PRs grouped by who is expected to act next. Draft PRs are listed separately. The grouping is partly performed by an LLM ([source](https://github.com/open-telemetry/shared-workflows/blob/main/.github/scripts/pull-request-dashboard/dashboard.py)) and could contain mistakes.
>
> Reviewers column: ✅ approved · ✔️ approved (non-code-owner) · 💬 open thread · 🔴 changes requested.

## Waiting on maintainers

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [\[chore\]\[exporter/awsxray\] migrate feature gate to metadata.yaml (#48619)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48619) | lazureykis | jmacd&nbsp;✅ | ✅ | ❌ | 21d |
| [\[receiver/kafkametricsreceiver\] update docs for Kafka 4.x / KRaft compat (#47748)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/47748) | dherges | atoulme&nbsp;✅<br>dmitryax | ❌ | ✅ | 15d |
| [\[extension/oidcauth\] Add ignore issuer in config and propagate it to go-oidc (#48513)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48513) | Dainerx | axw&nbsp;✅<br>edmocosta | ❌ | ✅ | 14d |
| [\[processor/spanpruning\] Add bytes metrics (#49008)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49008) | portertech | atoulme&nbsp;✅<br>csmarchbanks&nbsp;✔️<br>jmacd&nbsp;✅<br>songy23 | ✅ | ✅ | 14d |
| [\[receiver/hostmetrics\] Enable system.cpu.logical.count metric by default (#49325)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49325) | dmitryax | ArthurSens<br>braydonk&nbsp;✅<br>jmacd&nbsp;✅<br>mx-psi&nbsp;✅<br>rogercoll&nbsp;✔️ | ❌ | ✅ | 12d |
| [\[receiver/httpcheck\] Use confighttp defaulting constructor (#49357)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49357) | swiatekm | crobert-1<br>iblancasa&nbsp;✅ | ✅ | ✅ | 9d |
| [\[processor/tailsampling\] Change default error_mode of ottl_condition … (#48623)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48623) | pratik-mahalle | ArthurSens<br>atoulme<br>jmacd&nbsp;✅ | ✅ | ✅ | 8d |
| [\[exporter/azuremonitor\] Rename `azuremonitor` to `azure_monitor` (#49402)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49402) | dyl10s | iblancasa&nbsp;✅<br>TylerHelmuth | ✅ | ❌ | 8d |
| [New component: awsecsattributes processor Phase 2 - enrichment (#49036)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49036) | daidokoro | ChrsMark<br>dehaansa<br>iblancasa&nbsp;✅<br>povilasv&nbsp;✔️ | ✅ | ❌ | 7d |
| [\[receiver/memcached\] Add TLS support (#49146)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49146) | somaz94 | atoulme&nbsp;✅<br>evan-bradley<br>singhvibhanshu&nbsp;✔️ | ✅ | ❌ | 6d |
| [\[chore\]\[receiver/huaweicloudcesreceiver\] Migrate cenkalti/backoff v4/v5 → v7 (#49423)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49423) | songy23 | ArthurSens<br>codeboten&nbsp;✅ | ❌ | ✅ | 6d |
| [\[chore\] \[receiver/hostmetrics\] Fix flaky memory scraper tests on Linux (#49110)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49110) | vishalmore90 | braydonk&nbsp;✅<br>mwear&nbsp;✔️<br>osullivandonal&nbsp;✔️<br>pjanotti<br>rogercoll&nbsp;✔️ | ✅ | ✅ | 1d |
| [\[pkg/ottl\] Add the `When` OTTL converter (#49356)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49356) | edmocosta | iblancasa&nbsp;✅<br>mx-psi<br>TylerHelmuth&nbsp;✅ | ✅ | ✅ | 23h |
| [\[pkg/ottl\] Add `Filter` converter (#49184)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49184) | edmocosta | evan-bradley&nbsp;✅<br>MovieStoreGuy<br>TylerHelmuth&nbsp;✅ | ✅ | ✅ | 21h |
| [\[processor/resourcedetection\] add internal telemetry (#49128)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49128) | mwear | dashpole&nbsp;✅<br>mx-psi<br>paulojmdias&nbsp;✔️ | ✅ | ✅ | 20h |
| [\[chore\] exporter/awsxray: fix telemetry enabled test (#48724)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48724) | EraldCaka | axw&nbsp;✅<br>srprash&nbsp;✔️ | ✅ | ✅ | 15h |
| [\[receiver/awsecscontainermetrics\] migrate semantic convention from v1.21.0 to v1.42.0 (#49078)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49078) | singhvibhanshu | Aneurysm9&nbsp;✔️<br>ChrsMark<br>iblancasa&nbsp;✅<br>mx-psi<br>pjanotti<br>VihasMakwana | ✅ | ✅ | 2h |

## Waiting on reviewers

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [\[receiver/mysql\] Add service.instance.id resource attribute (#45444)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/45444) | aknuds1 | dashpole | ❌ | ❌ | 155d |
| [\[processor/transform\] Instrument the transform processor to emit traces (#44849)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/44849) | alexcams | atoulme<br>fatsheep9146<br>meldegwi&nbsp;✔️<br>songy23 | ✅ | ✅ | 79d |
| [\[receiver/postgresql\] Add receiver.postgresql.useOTelSemconv feature gate for OTel semconv resource model (#45345)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/45345) | aknuds1 | atoulme<br>axw<br>cyrille-leclerc&nbsp;✔️<br>dmitryax<br>edmocosta<br>lmolkova&nbsp;✔️<br>XSAM&nbsp;✔️ | ❌ | ❌ | 74d |
| [\[pkg/ottl\] Sync entityRefs with changes in resource attributes (#47092)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/47092) | meldegwi | dmitryax<br>edmocosta | ✅ | ❌ | 72d |
| [\[extension/awssecretsmanagerauthextension\] Add AWS Secrets Manager extension (#48474)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48474) | meebok | MovieStoreGuy | ❌ | ❌ | 51d |
| [\[pkg/ottl\] Grammar slice standardization Step 1 (#47985)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/47985) | meldegwi | edmocosta&nbsp;💬<br>jmacd | ❌ | ❌ | 44d |
| [\[chore\]\[receiver/fluentforward\] Fix incorrect tag attribute key in README (#48911)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48911) | Vanshul97 | dehaansa | ✅ | ✅ | 30d |
| [WIP: Exhausted resource middleware (#48908)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48908) | djluck | braydonk | ❌ | ❌ | 27d |
| [\[httpcheck\] fix incorrect stale value in httpcheck.status when status_code changes (#44917)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/44917) | martin-schulze-e2m | atoulme<br>MovieStoreGuy | ❌ | ✅ | 27d |
| [\[exporter/doris\] Report rows filtered out by Doris during a successful stream load (#49012)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49012) | vedatkilic | iblancasa | ❌ | ✅ | 27d |
| [\[receiver/googlecloudpubsub\] add ModifyAckDeadline support for StreamingPull (#48213)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48213) | smurtanv | fatsheep9146 | ✅ | ✅ | 27d |
| [\[pkg/ottl\] Implement AST constant folding for deterministic converters (#46506)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/46506) | Rajneesh180 | andrzej-stencel<br>edmocosta | ✅ | ❌ | 26d |
| [\[cmd/telemetrygen\] honour --allow-export-failures in batch flushBuffer (#47651)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/47651) | alliasgher | atoulme<br>axw<br>bogdan-st&nbsp;✔️<br>braydonk | ✅ | ✅ | 26d |
| [feat(resourcedetectionprocessor): add retry with backoff config to resource detector processor (#48223)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48223) | KyriosGN0 | atoulme<br>paulojmdias&nbsp;💬 | ✅ | ❌ | 25d |
| [\[receiver/vcenter\] Add host memory granted, active, and ballooned metrics (#49047)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49047) | Easonliuuuuu | andrzej-stencel | ✅ | ❌ | 25d |
| [connector/servicegraph: add links-based correlation for async messaging (#48782)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48782) | harshitt13 | dmitryax | ✅ | ✅ | 23d |
| [\[pkg/stanza/fileconsumer\] Require explicit top_n when sort_by is configured (#47445)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/47445) | Dylan-M | andrzej-stencel<br>atoulme<br>braydonk<br>paulojmdias&nbsp;✔️ | ✅ | ✅ | 22d |
| [Update module gopkg.in/yaml.v2 to v3 (#49119)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49119) | app/renovate | TylerHelmuth | ❌ | ✅ | 22d |
| [\[exporter/opensearchexporter\] Add manage_index_template option for otel-v1 mode (#48873)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48873) | ps48 | kylehounslow&nbsp;✔️<br>pjanotti | ✅ | ✅ | 21d |
| [\[receiver/hostmetrics\] Add hardwarescraper for hardware temperature monitoring (#49097)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49097) | moseoh | dmitryax | ✅ | ✅ | 20d |
| [\[cmd/opampsupervisor\] Use `confmap` instead of `koanf` to manage configuration (#49172)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49172) | douglascamata | mx-psi | ✅ | ❌ | 19d |
| [Support for GCD universe prefix in pubsub topics (#49198)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49198) | karpok78 | himanshu130700&nbsp;✔️<br>TylerHelmuth | ✅ | ✅ | 18d |
| [\[receiver/redis\] Add link to documentation.md for metrics details (#48709)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48709) | Madhumasa84 | dashpole | ✅ | ✅ | 16d |
| [\[exporter/file\] Fix orphaned lumberjack backup files after migration to timberjack (#47694)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/47694) | paulojmdias | dehaansa | ✅ | ✅ | 16d |
| [\[pkg/stanza/fileconsumer\] Skip files unchanged by path+mtime since the previous poll (#48039)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48039) | Dylan-M | atoulme<br>crobert-1 | ✅ | ❌ | 16d |
| [\[receiver/github\] Process pull-requests in reverse order to adhere to chronological ordering (#48578)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48578) | vladorf | adrielp&nbsp;✔️<br>ArthurSens | ✅ | ✅ | 16d |
| [\[receiver/netflowreceiver\] Add interface index and IP ToS attributes (#49077)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49077) | Abhishek-Bhandwalkar | MovieStoreGuy | ✅ | ✅ | 15d |
| [\[cmd/telemetrygen\] support base paths in OTLP HTTP endpoints (#48064)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48064) | jeevan6996 | atoulme | ✅ | ✅ | 14d |
| [multi host support (#49256)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49256) | pankaj101A | atoulme | ❌ | ✅ | 14d |
| [\[exporter/awscloudwatchlogs\]\[internal/aws/cwlogs\] configurable 1 MiB per-event cap + fix batch threshold conflation (#48559)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48559) | challahc | VihasMakwana | ✅ | ✅ | 14d |
| [\[exporter/prometheusremotewrite\] Add classic→NHCB conversion with dual write (#49010)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49010) | srstrickland | ArthurSens<br>dashpole<br>iblancasa | ✅ | ✅ | 13d |
| [\[receiver/githubreceiver\] retry on primary rate-limit errors (#48539)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48539) | nissessenap | andrzej-stencel | ❌ | ✅ | 13d |
| [\[chore\]\[pkg/stanza\] remove references to attributes.time from the container operator docs (#48886)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48886) | omrozowicz-splunk | dashpole<br>himanshu130700&nbsp;✔️<br>osullivandonal&nbsp;✔️<br>paulojmdias&nbsp;✔️ | ✅ | ✅ | 13d |
| [\[exporter/awss3\] Add optional webhook notifications on successful upload (#48866)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48866) | skandragon | atoulme&nbsp;🔴<br>dehaansa | ✅ | ❌ | 12d |
| [\[feat\]\[extension/bearertokenauthextension\] Add option to start without available auth-token file so retry, instead of failing (#48704)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48704) | iypetrov | bogdandrutu | ✅ | ✅ | 12d |
| [\[processor/spanpruning\] preserve outlier subtrees with multi-level detection (#49324)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49324) | csmarchbanks | atoulme<br>portertech&nbsp;💬 | ✅ | ✅ | 11d |
| [\[receiver/kafkametricsreceiver\] Set kafka.cluster.alias when cluster_alias is defined (#47573) (#47574)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/47574) | skreuzer | pjanotti | ✅ | ❌ | 11d |
| [\[chore\]\[exporter/elasticsearch\] Document emitted document structure per mapping mode (#49349)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49349) | alliasgher | MovieStoreGuy | ✅ | ✅ | 10d |
| [\[connector/signaltometrics\] Add feature gate to change default error_mode to ignore (#48434)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48434) | singhvishalkr | VihasMakwana | ✅ | ✅ | 10d |
| [\[chore\]\[receiver/kafkametrics\] add benchmark tests for franz-go (#48783)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48783) | paulojmdias | edmocosta | ✅ | ❌ | 9d |
| [\[receiver/postgresql\] Adding postgresql.query.conflicts metric (#49303)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49303) | Sprakhar97 | atoulme&nbsp;✅<br>axw<br>ebrdarSplunk&nbsp;✔️<br>splunk-shanu&nbsp;💬 | ❌ | ✅ | 8d |
| [\[receiver/vcenterreceiver\] Fix vcenter.vm.cpu.readiness always report… (#48900)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48900) | Easonliuuuuu | codeboten<br>Dylan-M&nbsp;✔️<br>schmikei&nbsp;✔️ | ✅ | ✅ | 8d |
| [docs(mongodbreceiver): clarify the `hosts` config (#48788)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48788) | Lenormju | ChrsMark<br>dyl10s&nbsp;✔️<br>paulojmdias&nbsp;✔️ | ❌ | ✅ | 8d |
| [\[exporter/prometheusremotewriteexporter\] Retry transient WAL export errors and add configurable segment cache size (#49383)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49383) | charanck9 | VihasMakwana | ❌ | ✅ | 8d |
| [lookupProcessor: add file watching for yaml (#49070)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49070) | michael-michalski | songy23 | ✅ | ✅ | 8d |
| [\[processor/transform/internal/logparsingfuncs\] Add ParseCEF log parsing function (#49288)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49288) | Caleb-Hurshman | Dylan-M&nbsp;✔️<br>pjanotti | ✅ | ✅ | 7d |
| [\[receiver/journald\] add option to include original log record (#48243)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48243) | belimawr | andrzej-stencel<br>ArthurSens | ❌ | ✅ | 7d |
| [\[processor/metricstransform\]\[processor/transform\] Fix histogram min/max aggregation overwritten with 0 by data points without min/max (#49202)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49202) | Osamaali313 | dmitryax | ✅ | ✅ | 7d |
| [\[exporter/prometheusremotewriteexporter\] Fix WAL buffered data stall … (#49131)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49131) | charanck9 | bogdandrutu | ✅ | ✅ | 7d |
| [add skip_conditions OTTL support for conditional redaction (#48632)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48632) | iblancasa | ChrsMark | ❌ | ❌ | 7d |

_More 34 PRs not shown_

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
| [\[exporter/loadbalancing\] Replace log.Fatalf with proper error return in AWS Cloud Map resolver (#48763)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48763) | Vanshul97 | atoulme&nbsp;💬<br>ChrsMark<br>iblancasa&nbsp;✅ | ❌ | ✅ | 40d |
| [\[receiver/awscloudwatch\] Use LogGroupIdentifier for cross-account log groups (#48762)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48762) | Vanshul97 | andrzej-stencel<br>dyl10s&nbsp;💬 | ✅ | ✅ | 40d |
| [\[tailsamplingprocessor\] Fix race in iter() that delays config updates (#48889)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48889) | vishalmore90 | csmarchbanks&nbsp;✔️<br>jmacd&nbsp;✅<br>paulojmdias&nbsp;💬<br>VihasMakwana | ✅ | ✅ | 31d |
| [\[chore\] google_cloud_spanner add metrics and attributes to metadata.yaml (#48975)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48975) | davidspek | dmitryax | ❌ | ✅ | 29d |
| [\[chore\] fix errInvalidEndpoint not wrapped in error chain in check receivers (#48729)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48729) | immanuwell | TylerHelmuth | ✅ | ✅ | 26d |
| [fix(isolationforestprocessor): trees never split + contamination_rate ignored (#47115)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/47115) | pathcl | aarvee11&nbsp;💬⁠✔️<br>atoulme<br>codeboten<br>iblancasa&nbsp;💬 | ❌ | ✅ | 23d |
| [\[receiver/docker_stats\] Introduce feature gate to align metrics with new container semantic conventions (#48081)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48081) | ben-trans | atoulme<br>jamesmoessis&nbsp;💬<br>MovieStoreGuy&nbsp;✅<br>pjanotti | ✅ | ❌ | 23d |
| [\[pkg/expohisto\] Add binary marshaling for Histogram (#48298)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48298) | lahsivjar | jmacd | ✅ | ✅ | 23d |
| [\[receiver/fluentforward\] Limit msgpack allocations (#48479)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48479) | dmitryax | TylerHelmuth | ✅ | ✅ | 22d |
| [\[pkg/pdatatest\] Add pmetricassert collection include matcher (#48472) (#48545)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48545) | harshitt13 | ArthurSens<br>dmitryax&nbsp;💬 | ✅ | ✅ | 21d |
| [\[receiver/postgresql\] Skip queries whose metrics are all disabled (#49086)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49086) | benchub | andrzej-stencel<br>crobert-1&nbsp;💬 | ✅ | ✅ | 21d |
| [\[processor/spanpruning\] Add span pruning conditions (#49026)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49026) | portertech | atoulme<br>braydonk<br>csmarchbanks&nbsp;💬<br>jmacd&nbsp;✅ | ✅ | ❌ | 20d |
| [\[receiver/vcenter\] Add metrics (#48098)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48098) | SCadilhac | evan-bradley | ❌ | ❌ | 20d |
| [\[connector/spanmetrics\] Introduce `source` resource attribute (#49175)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49175) | jcreixell | andrzej-stencel<br>iblancasa&nbsp;🔴<br>thompson-tomo&nbsp;💬 | ✅ | ✅ | 18d |
| [\[pkg/pdatatest\] Add pmetricassert attribute include matcher (#48622)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48622) | singhvibhanshu | ArthurSens<br>dmitryax | ✅ | ✅ | 17d |
| [extension.oidauthextension added support for multiple JWT sigs in extension oidc (#47849)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/47849) | michaelalang | atoulme&nbsp;💬<br>pavolloffay&nbsp;✔️<br>VihasMakwana | ✅ | ✅ | 16d |
| [\[pkg/stanza\] Add cache for `container` operator's k8s attributes extraction (#44487)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/44487) | Nitesh-vaidyanath | andrzej-stencel<br>atoulme&nbsp;💬<br>codeboten | ✅ | ❌ | 15d |
| [exporter/opensearchexporter: Fix silent data loss by correctly classifying transient network errors as retryable in log and trace bulk indexersOpensearch exporter issue (#49207)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49207) | johnynainwani | bogdandrutu | ❌ | ✅ | 15d |
| [\[chore\]\[processor/resource_detection\] Add paulojmdias as codeowner (#49141)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49141) | paulojmdias | dashpole&nbsp;✅<br>VihasMakwana | ✅ | ✅ | 14d |
| [\[extension/basicauthextension\] Add AWS Secrets Manager support for basicauth extension (#49025)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49025) | meebok | frzifus&nbsp;✔️<br>MovieStoreGuy | ✅ | ❌ | 14d |
| [\[receiver/k8scluster\] Fix container.image.name and container.image.tag when status image is a bare digest (#46987)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/46987) | ricardbejarano | atoulme<br>ChrsMark&nbsp;💬<br>dmitryax<br>povilasv&nbsp;✔️ | ✅ | ✅ | 13d |
| [\[receiver/sqlserverreceiver\] add db.query.full_text opt-in attribute to top_query and query_sample events (#48660)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48660) | avadla | crobert-1&nbsp;💬<br>songy23 | ✅ | ❌ | 11d |
| [\[receiver/sqlserverreceiver\] Add per-index physical stats metrics (#49350)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49350) | akgrover | atoulme<br>crobert-1&nbsp;💬<br>edmocosta<br>thompson-tomo&nbsp;💬 | ❌ | ❌ | 10d |
| [\[cmd/opampsupervisor\] Restart from last working remote config when fail to apply new one (#47853)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/47853) | douglascamata | dehaansa<br>dpaasman00&nbsp;💬<br>evan-bradley&nbsp;💬 | ✅ | ✅ | 9d |
| [\[cmd/opampsupervisor\] Add config structure and interfaces for upgrades (#48792)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48792) | dpaasman00 | douglascamata&nbsp;✔️<br>evan-bradley&nbsp;💬⁠✅<br>MovieStoreGuy<br>tigrannajaryan&nbsp;✔️ | ✅ | ✅ | 8d |
| [New framework processor (#49330)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49330) | ravularohit | ChrsMark<br>kylehounslow&nbsp;💬 | ✅ | ✅ | 8d |
| [\[receiver/sqlserver\] Add 10 new opt-in metrics for cursor, worker thread, CLR, and Service Broker monitoring (#49144)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49144) | splunk-shanu | atoulme<br>crobert-1&nbsp;💬 | ✅ | ❌ | 8d |
| [\[extension/opampextension\] Fix self-reported status events dropped by non-Reporter hosts (#49412)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49412) | CodeBlackwell | bogdandrutu<br>chatgpt-codex-connector&nbsp;💬<br>iblancasa | ❌ | ✅ | 7d |
| [\[processor/tailsampling\] Add num_shards config for parallel event loops (#48699)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48699) | dmfallak | carsonip&nbsp;💬<br>csmarchbanks&nbsp;💬<br>VihasMakwana | ✅ | ✅ | 7d |
| [Unbounded Entry Count in FluentForward Receiver Enables OOM Kill via Tiny Malicious Frames (#49295)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49295) | aniket866 | braydonk | ✅ | ✅ | 6d |
| [Make pcommon value comparable (#49214)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49214) | alexcams | edmocosta&nbsp;💬<br>pjanotti | ✅ | ✅ | 6d |
| [\[receiver/oracledb\] Add PDB auto-discovery and per-PDB metrics   for Oracle multitenant (CDB) deployments (#48921)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48921) | spathlavath | codeboten<br>crobert-1&nbsp;💬 | ❌ | ✅ | 6d |
| [\[pkg/translator/azurelogs\] Support additional fields not in the common schema (#46165)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/46165) | rafaelrodrigues3092 | atoulme<br>axw&nbsp;✅<br>MikeGoldsmith&nbsp;✔️<br>pjanotti | ✅ | ✅ | 6d |
| [\[exporter/opensearchexporter\] Validate attribute values in dynamic index names (#49362)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49362) | kylehounslow | dashpole<br>ps48&nbsp;💬 | ❌ | ✅ | 5d |
| [\[receiver/hostmetrics/cpu\] Make logical CPU attribute opt-in (#49162)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49162) | dmitryax | braydonk&nbsp;✅<br>ChrsMark&nbsp;💬<br>iblancasa<br>jmacd&nbsp;✅<br>mx-psi&nbsp;✅<br>rogercoll&nbsp;💬⁠✔️ | ✅ | ✅ | 5d |
| [\[receiver/azure_functions\] Support metrics for Event Hub trigger (#48105)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48105) | tetianakravchenko | mx-psi<br>zmoog&nbsp;✔️ | ❌ | ✅ | 5d |
| [\[cmd/telemetrygen\] Continue work on "add profiles signal support" (#49212)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49212) | bogdan-st | florianl&nbsp;💬<br>jmacd<br>mx-psi | ✅ | ❌ | 2d |
| [\[receiver/k8s_objects\] Migrate to Kubernetes SharedInformerFactory (#48663)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48663) | kangyili | ChrsMark<br>krisztianfekete&nbsp;💬<br>mx-psi | ✅ | ✅ | 2d |
| [fix(metricstransformprocessor): preserve absent histogram sum on merge (#49404)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49404) | CedricConday | evan-bradley<br>himanshu130700&nbsp;✔️ | ✅ | ✅ | 2d |

_More 8 PRs not shown_

## Waiting on external

| PR | Author | Reviewers | CI | Conflicts | Age |
|---|---|---|:---:|:---:|:---:|
| [exporter/prometheus: Add config option 'resource_constant_labels' (#48922)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/48922) | ArthurSens | axw<br>dashpole | ✅ | ❌ | 28d |
| [\[receiver/hostmetrics\] Add process.network.connection.count metric to process scraper (#49091)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49091) | GlqEason | bogdandrutu | ❌ | ✅ | 20d |
| [\[pkg/ottl\] Add `MapEach` converter (#49186)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49186) | edmocosta | dashpole<br>evan-bradley<br>TylerHelmuth&nbsp;✅<br>VihasMakwana&nbsp;✅ | ✅ | ✅ | 6d |
| [\[cumulativetodeltaprocessor\] Add histogram_fields config for selective field conversion (#49407)](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/49407) | Chau-Tran | axw<br>TylerHelmuth | ❌ | ✅ | 1d |

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

_Approvers may [force a refresh](https://github.com/open-telemetry/shared-workflows/actions/workflows/pull-request-dashboard.yml)._

