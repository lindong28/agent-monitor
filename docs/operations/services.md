# agent-monitor service operations

Current source layout is this repository's root. Install runtime and assets with `./install.sh`; stage without changing services with `./install.sh --no-services`. The canonical CLI is `~/.local/bin/agent-monitor`. `com.agent-monitor.rollup` collects locally (Hub mode: `collect`, 120 seconds; standalone: `refresh`, 3600 seconds); `com.agent-monitor.hub` serves MacStudio on port 39001. Configure a role with `./install.sh hub <machine>`. Lifecycle commands are documented in the [README](../../README.md).

State remains rooted at `state/`; managed releases can point it to `~/.local/share/agent-monitor/state`. The runtime environment belongs to this project at `.venv`, and the Gateway reader remains an independent checkout selected by `LLM_GATEWAY_ROOT`. Installation never reads the former parent repository's Python environment or statusline implementation.

## 2026-10-08 whole-product visual style

Commit `6ce606be5e2aa9a57ca01940b790c150ebfd1cc7` was integrated locally and deployed by Git bundle to MacStudio, fast-forwarding the service checkout from `a79a3c0`. No Git push was performed. Only `com.agent-monitor.hub` restarted; the existing `state` link to `~/.local/share/agent-monitor/state` and inode 5804813 stayed unchanged. The served CSS and two changed HTML assets matched the commit. Browser-origin health returned ok=true/stale=false, instance `a7e640a04ba648eaada1371da07b1021` and Web signature `15bc45a4d5a9abab`.

The shared style applies to all five routes and their details. Live native navigation, cost sorting, a session detail/return and a request detail/Escape were exercised at 390px. Existing local Web tests passed (40 cases); the isolated preview covered five routes at seven widths, with additional samples at affected breakpoints. Exact coverage and retained exceptions are in [design.md](../design.md). This is frontend delivery; backend performance, collection assertions and account operations were outside scope.

## 2026-10-08 template-directed console UI

Commit `a79a3c0e1cf1edf63e297952c3acac130eb0a08d` was integrated into local main and deployed by a Git bundle to the existing MacStudio checkout, fast-forwarding from `4a56a00`. No Git push was performed. Only the Hub restarted; the state symlink target and inode 5804813 were preserved. No backend files changed. Browser-origin health reported ok=true/stale=false, instance `db19ea9df29646a0aa28d65ae915de03`, source signature `8e1b0f7758086ee6`, and Web signature `df6144f038cf099f`.

The real 1440×1000 overview kept distinct today/week/range amounts, placed attribution before quota/account management, and kept account actions collapsed. Calls showed four primary filters with advanced filters collapsed. The 30d list returned 50 rows in a 2596ms resource observation. A 7d/project=aihot/provider=deepseek narrow-screen query returned 50 rows in 20599ms; this remaining backend latency is outside this frontend task and belongs to the owner's separate optimization session. Removing the provider chip preserved the project and range. At 390px, document width was 390px. One live success request showed the matching result, one attempt, 560 tokens, 988ms and unknown cost; Escape closed the panel and returned focus to the original request button. Snapshot/source details remained collapsed.

The local isolated regression run covered 51 interaction/quota/static tests plus 6 field regressions. One independent implementation review and a targeted filter-layout follow-up found no blockers. A nonblocking wording mismatch remains: the unloaded analysis placeholder uses the old button name while the button says “查看统计与尝试”. No account actions or full-analysis load were executed; these observations do not establish all retry/error states or performance improvement. Evidence: `/Users/lindong/.local/state/agent-monitor-ui-review-20261008/` on the validation MacBook.

## 2026-10-08 Codex quota retention delivery

The owner authorized push and deployment. MacStudio reached `1bd1239c86cb7e7ad42e8cbbcf43374a74a2aba9`; `./install.sh --no-services` preserved the state symlink (inode 5804813), then only the Hub restarted. Browser-origin `/api/health?asset_watch=1` reported ok=true/stale=false, instance `f2d813ffc75b4538ab817f348079b307`, source signature `b7d264936387eee3` and Web signature `7a5103c5f6d902d5`. The legacy health URL without `asset_watch=1` deliberately reports stale=true and is not the current frontend's health check.

One real browser quota-only refresh for `lindong4@philoai.xyz` succeeded at `2026-10-08T06:32:45.931155+00:00`: seven-day usage 0%, reset `2026-10-14T21:47:53+08:00`, with five-hour values unknown. The API returned the same reading in `operation.after` and `last_quota`; the card and historical-account row displayed the new observation, including after page reload. This operation sent no message. Local isolated fixtures separately covered post-send refresh for two email identities and retention after a failed refresh; the live check covers one authenticated account and quota-only refresh, not a live post-send run. `lindong2@philoai.xyz` still had no saved login and no new successful reading, so its October 1 machine observation cannot establish current quota.

The owner also granted standing authorization for agent-monitor deployments and their required service changes/restarts, recorded in the repository's `AGENTS.md`. Push and other remote publication continue to require explicit authorization.

## 2026-10-08 request-list performance delivery

The owner explicitly approved publishing 218bf2f, a MacStudio-only update/restart and the subsequent verification-note commit. The service checkout reached 218bf2f68024f4670e2b9b9af5b4b5f6a56dc78b. Health reported ok=true/stale=false, instance 06b402e2af234f96beff7a5179f6bf32, source signature cda03b7e949b44a0 and Web signature ef5346f6a4f4e522; state inode 5804813 and its target were unchanged.

The real browser opened the four-source 30d request page: list resource 822.4ms, 50 rendered rows and computed KPI display none. Next rendered page 2 in 866.1ms. Two simultaneous browser-origin list reads returned HTTP 200, four source envelopes and 50 rows each in 964.4/965.4ms. The 7d/project=aihot deep link retained its selection and returned 50 rows in 423.9ms; opening its first request rendered the matching detail in 24.6ms. This covers two ranges, two selection states and serial/two-concurrent reads on the deployed build. It is a set of point observations; the admission cache was not deliberately emptied, and it does not establish tail latency or all sparse-filter combinations. Evidence is in `live-final-next.txt`, `live-final-concurrent.txt`, `live-final-detail.txt` and `live-final-state.json` under `~/.codex/task-artifacts/monitor-list-20261008/`.

Ordinary list opening is now delivered, compared with the earlier 38.195s full-page observation. Full statistics and complete filter enumeration still use the expensive full reader, by the owner's explicit on-demand choice; unloaded figures are labeled and never presented as zero. ISSUE-CALLS-20261006-6d4a is resolved under this agreed interaction and archived with all historical measurements. Separately, the latest MacBook synchronization again reported a rollup-lock timeout while retaining its 05:37:06Z generation. That recurring collection issue is tracked as ISSUE-SYNC-20261008-b74e, with no lock deletion or source service change in this delivery. Four sources remained admitted; this is not a claim that every latest collection attempt succeeded.

## 2026-10-08 request-first release and legacy-source follow-up

The owner approved pushing c4674cc/a06da5e and updating/restarting only MacStudio. Main and the service checkout reached a06da5ecaf3439974a8c889c80a0739cc4ae6189; `./install.sh --no-services` preserved runtime assets and the state symlink (inode 5804813), then the existing Hub restarted. Health reported ok=true/stale=false, instance c74f82a81e654a0294ce61ebade8051e, source signature 529503be87107fd1 and Web signature ef5346f6a4f4e522. A new indexed MacStudio generation a392346c3540839574a09c179079e68e178621fc25e0557f833eb06cd9ba0c68, exported by a06da5e at 05:27:59Z, was published at 05:30:07Z. MacBook subsequently exported the same local main through its existing schedule; its service was not changed. MacMini and Tencent remained on older exporters without these indexes.

The real four-source browser initially read the unfiltered list in 9.923s and page 2 in 10.092s. An aihot deep link retained its selection and read 50 rows in 2.466s. After the new generation, unfiltered list reads still took 7.744s/8.166s and Next rendered in 7.940s. Detail remained responsive at 100.7ms. These are discrete observations, including a generation transition, not percentiles or isolated cold-admission timings. A direct per-source read on the service host separated admission (1.014s) from list work: MacBook 2.8ms, MacStudio 5.0ms, MacMini 7.025s and Tencent 553.5ms. The remaining dominant wait was therefore not resolved by installing only the new MacStudio index.

The follow-up batches complete request timestamp buckets before querying child attempts. An isolated read-only probe on MacStudio loaded candidate code only in its own process, leaving service files/processes unchanged. On the same leased four-source observation and frozen time, the complete response was identical while list work fell from 9.078s to 0.855s. Per-source comparison separately measured MacMini 6.985s→582.1ms and Tencent 552.9ms→65.3ms. Four existing list test methods passed, including two-source pagination, filters, HTTP and precise/offset timestamp boundaries. This batch-only change uses those direct equivalence checks for the review gate, without another independent review. Evidence: `live-identity.json`, `live-source-indexes.json`, `live-per-source.json` and `live-batch-complete-comparison.json` under `~/.codex/task-artifacts/monitor-list-20261008/`.

At this record's creation the follow-up was local only; its push and MacStudio deployment need a new explicit approval. The original a06da5e release is a partial improvement, not completion of the remaining eight-second list wait. MacBook briefly reported the previously observed rollup-lock timeout during restart and later published a newer generation; no lock or source service was manually changed.

## 2026-10-08 request-first list: local validation

The owner selected requests first, with optional analysis loaded on demand. One frozen MacStudio snapshot contained 369,775 requests and 369,825 attempts. Adding the request-time index to an isolated copy took 0.429s; existing immutable published snapshots were not modified. Direct list reads on the old unindexed copy took 1.487s unfiltered and 1.547s for project=aihot. Four already-admitted indexed reads across those two selections took 2.8–3.3ms. These are local API measurements, not live Hub or cold-generation guarantees.

An isolated headless browser used the indexed copy and the changed HTTP server. The initial list resource took 568.5ms during first admission; after restarting the test server, the first read was consumed before recording, so its subsequent 8.8ms unfiltered and 11.2ms filtered list reads are warm observations. The page displayed 50 requests and explicitly marked totals unloaded; computed KPI display was none. Clicking Next rendered page 2 in 10.9ms, and opening its first request rendered the matching complete detail in 15.6ms while retaining page 2. Explicit analysis took 16.386s and displayed 369,775 requests / 369,825 attempts. Explicit filter choices took 13.971s; selecting gateway-console then rendered its two requests in 464.4ms with Next disabled. A deep link combining valid project=aihot and an invalid machine retained the project, removed the invalid URL/control value and rendered 50 requests. Full analysis remains expensive; the change removes it from ordinary opening and refresh rather than claiming to accelerate that computation.

Eight affected test groups ran 106 methods: 105 passed, with one known schema assertion failure (10 != 7) reproduced separately on unchanged main c4674cc. The test scope includes two machines / three projects / 108 requests / 110 attempts; all, 7d and custom windows; valid and invalid filters; multi-page cursors; sub-millisecond ties and SQLite-unparseable offsets; HTTP and stale-response/analysis-poll ordering. Independent review found two attached issues, both corrected: an offset cursor could drop normal rows, and early analysis could lose URL filters. The persisted regressions and browser checks passed after correction. Complete execution inputs, loaded module paths and hashes are in `~/.codex/task-artifacts/monitor-list-20261008/final-test-input.json`, results in `final-tests.txt`, and profile/index readings in the same directory. No independent documentation review was run; this is ordinary source-linked documentation synchronization.

This implementation has not been deployed. A read-only MacStudio check found user lindong, checkout `~/research/agent-monitor` at 04d90cb, only the existing untracked state symlink, health ok=true/stale=false, instance d9b3e1b6f8764603b70b4882f822f620 and unchanged state inode 5804813. Publication and a MacStudio-only update/restart require approval for this new commit. Other producers can continue supplying readable snapshots without the optional index; their deployment is outside this scope. Production list latency, cross-source cold admission and sustained sparse-filter cost remain unverified. The follow-up belongs to this performance session under ISSUE-CALLS-20261006-6d4a.

## 2026-10-08 full-history HTTP isolation release

The owner explicitly approved pushing `04d90cb` and updating/restarting only MacStudio. Remote main and the service checkout reached `04d90cbda304b5bde027837da4871ee3988dcbb7`. `./install.sh --no-services` staged the existing runtime/assets before restarting `com.agent-monitor.hub`; Hub PID 21630 served health ok=true/stale=false, instance `d9b3e1b6f8764603b70b4882f822f620`, source signature `002d440e33d22f2d` and unchanged Web signature `70184018a3cd4bfb`. The state symlink retained inode 5804813 and its target. No other service was restarted.

An isolated real browser opened the 30d page and used the request-detail controls. Seven renders across four macstudio/aihot requests took 83.9, 27.9, 46.4, 80.0, 27.9, 527.8 and 39.5ms. Coverage includes repeated and switched identities, one- and two-attempt chains, concurrent full-history queries and a generation transition. The first four readings overlapped full-history work; the final three read MacStudio statistics observed at `04:39:16.714236+00:00`, advanced from `04:35:36`. They do not establish an unbounded latency guarantee or an empty admission-cache result: sync-status reads can warm that cache. The page's original full load remained 38.195s; two explicit concurrent full queries took 75.504s (page, including possible queueing behind automatic page loads) and 34.717s (filters). List projection is still slow even though it no longer caused a long detail wait in these observations.

One simultaneous process sample showed the HTTP parent at 38,736KiB RSS, sync worker at 12,543,280KiB, and two query workers at 9,737,056/7,325,600KiB. This is a sample, not peak-memory accounting. The two-worker bound is active on the real host. The local complete 618MB export comparison remains the large-export proof; no production export download was claimed here.

MacStudio published generation `f7ab9f8ea790b62a16776584092407dc6ee1541ca4001901c3659471cf8b917e`, exporter `04d90cb`, generated at `04:40:19.667475Z` and published at `04:42:44.775507Z`. Final sync API readings reported successful last attempts for all four admitted machines, including MacMini's `04:40:00.585804Z` generation. The browser visibly showed advancing source times and four admitted machines. MacMini still reported one blocked statistics source; freshness recovery does not erase that coverage caveat. Evidence is in `query-live-browser.json`, `query-live-health.json`, `query-live-rss.txt`, `query-live-sync-final.json` and `query-new-generation.json` under `~/.codex/task-artifacts/monitor-contention-20261008/`. The task-owned browser was closed after capture.

The targeted detail-wait and stale-generation work is delivered. `ISSUE-CALLS-20261006-6d4a` remains open for full-list projection cost, resource use and longer-term cold-read characterization; this release does not rewrite the independently owned Gateway algorithm. The earlier non-HTTP query-shape finding remains separately tracked as `ISSUE-CALLS-20261008-9a72`.

## 2026-10-08 full-history HTTP isolation: local validation

The owner approved isolating the four full-history HTTP routes with at most two simultaneous query workers. On the same immutable copy (369,775 requests / 369,825 attempts), a controlled HTTP comparison used two alternating detail identities, warm admission, a fixed observation time and fresh server processes for the old direct path and new worker path. During one 30d page load per mode, detail maxima were 1.393s (41 reads) and 0.00806s (121 reads). During one complete request export per mode, maxima were 4.223s (36 reads) and 0.01479s (125 reads). The page bodies and 618,351,695-byte exports matched byte-for-byte across both modes. Page durations were 18.191s/16.372s and export durations 18.185s/16.479s; these single runs establish contention reduction in this fixture, not a list-speed or production guarantee.

RSS samples, collected after detail responses, reached parent/worker maxima of 39,392/6,494,496KiB during the isolated list and 1,250,016/6,882,592KiB during its export. Direct-path parent samples reached 6,407,456KiB and 6,599,280KiB. These are sampled maxima, not exhaustive process peaks. The parent still buffers encoded exports, while full-history objects live in workers. Shared CPU/disk and cold detail admission remain outside this isolation claim.

The five affected modules passed 35 test methods under isolated HOME/state: four worker routes; GET/HEAD; normal, projection-error and five invalid-protocol states; three simultaneous full requests capped at two workers; detail bypass; EOF/shutdown/reexec; two-machine, three-project fixtures with 108 requests and 110 attempts; complete and filtered exports; progressive and late-response Web behavior. Existing Hub HTTP tests explicitly bind fresh workers to their isolated generation roots. Independent review found and verified the correction of an over-broad Hub-configuration check on health/timezone routes. Evidence is retained as `compare-query.json`, `compare-query.log` (including its final byte-equality assertion) and `query-regressions.txt` under `~/.codex/task-artifacts/monitor-contention-20261008/`. This change is not yet deployed; the read-only MacStudio check still found `a18f5ea`, user `lindong`, Hub PID 20798 and unchanged state symlink inode 5804813.

## 2026-10-08 expired export cleanup release

The owner approved pushing `a18f5ea` and restarting the MacStudio Hub, including running the existing age/owner-scoped reaper on source hosts. After deployment, MacMini reported 6.1GiB available instead of 261MiB and one export temporary directory instead of twelve. No manual deletion was used. The actual Hub sync API (instance `ab04df97f75f4efeb29410742dd8b1c5`) then admitted MacMini generation `40d7f611010d855037c0398ef19dc2c3481d51c7933acf4176d86c2bf68789f4`, generated at `2026-10-08T04:29:47.015742Z`, with successful contact at `04:30:54.944374Z`, `stale=false` and statistics observation `04:29:16.774544+00:00`. One blocked source remains explicitly represented in that statistics metadata; this is snapshot-refresh recovery, not a claim of complete source coverage.

## 2026-10-08 sync process isolation release and remaining bottlenecks

The owner approved pushing `e155aec` and updating/restarting MacStudio only. Remote main and the checkout reached `e155aec40de03eb1ab7522a9a92bd5536c144f01`; Hub PID 19941 and worker PID 19943 ran separately from the existing `.venv`. Asset-watch health returned ok=true/stale=false, instance `dd5628ee44a04a948cc4a9aee92aa0ff`, source signature `a099e44798fbd94b` and unchanged Web signature `70184018a3cd4bfb`. The state symlink retained inode 5804813 and its original target. MacStudio generation `ab95e8df86037252baccc723d8fd13bdb4e54f8c9b607802e23d264e06fea086` identifies exporter `e155aec`, generated at `04:15:37.155150Z` and published at `04:17:21.128740Z`; the browser read its statistics observation at `04:14:29.453752+00:00`.

Real browser detail renders during background sync took 0.612s / 0.131s / 0.070s for first/repeated/switched requests. After a new generation, a third request took 8.132s, followed by 0.044s on repeat. These five readings cover three successful macstudio/aihot requests with one complete attempt each; they do not establish consistently fast cold reads. One 30d list load still took 37.773s. During list and sync work the parent and child each used approximately one CPU core, with observed RSS reaching 12.0GB/13.4GB respectively. The independent source diagnosis confirms four full-history HTTP paths still decode and project all rows in the parent (`llm-calls`, `llm-calls-page`, `llm-call-filters`, `llm-calls-export`); page construction already shares a single observation. Gateway's local-ledger cache/index does not directly cover monitor's multi-source snapshot representation. Full-query isolation is a remaining option, with extra process memory/concurrency costs; it does not by itself make the list projection faster.

MacBook and Tencent successfully refreshed after this restart. MacMini still reported `database or disk is full`; a read-only check found only 261MiB free on its data volume and 12 leftover `/tmp/agent-monitor-export.*` directories totaling 6,469,540KiB (6.17GiB). Their modification times spanned October 4–8, and the process-list check found no command referencing those exact directories. Plain `find /tmp` did not traverse the macOS symlink; `find -H /tmp` enumerated the directories. The local correction follows only the command-line root symlink, retaining the existing owner/name/one-hour/depth predicates. Its real shell regression failed only on the symlink root before the correction; all 14 sync tests passed afterward, covering real/symlink roots, old/recent directories and preservation of unrelated directories and child symlinks. This correction and any remote cleanup require a new publication/deployment authorization; no manual deletion on MacMini was performed. These remaining items stay open under `ISSUE-CALLS-20261006-6d4a`.

## 2026-10-08 sync process isolation: local validation

The next change isolates each Hub synchronization round in a supervised Python process. Existing cadence, machine concurrency, statistics/quota progression, snapshot validation and retention stay in place. Parent-owned account memory and first admission of a new generation still run in the HTTP process. This is a local implementation, pending separate publication and MacStudio deployment authorization; the last read-only deployment check still found `c37ce00`, Hub PID 16168, user `lindong`, and the unchanged state symlink inode 5804813.

A controlled local HTTP comparison used the same indexed production copy (369,775 requests / 369,825 attempts), two request identities, warm admission, and one real full-history retention operation per mode. Each mode issued 80 alternating detail requests across the complete retention interval. In-process retention took 26.550s; HTTP median/max were 0.00799s/1.17950s, with 17/80 over 100ms. The new process runner took 26.500s; HTTP median/max were 0.00530s/0.01040s, with 0/80 over 100ms. All selected request payloads matched their pre-work readings. The sync fixture substituted only source acquisition with the copied snapshot; retention and HTTP detail reads were real. This establishes contention reduction in this fixture, not a production latency bound, cold-admission result, list improvement, or fleet refresh proof.

The affected five-module regression run passed 77 methods under isolated HOME/state. Coverage includes two outcome kinds, three protocol phases, four protocol-failure scenarios, three exit paths (EOF, explicit shutdown and same-PID reexec), progressive/queued refresh, admission and Web state handling. A subsequent test-assertion correction was independently checked with two methods in normal and forced-failure states: both normal states passed and both injected failures failed. The changed test modules were rerun on the final source. The independent implementation review found no blocking issue; the remaining cold-admission cost is recorded in the [decision](../adr/20261008-c4e2-sync-process.md).

An earlier test run had an isolation failure: old mocks covered the in-process entry but not the new Hub worker, unintentionally starting a real MacStudio export. The local test/worker/SSH and the identified remote exporter were terminated; a subsequent read confirmed those remote PIDs absent. The Hub stayed running. Export completion and absence of intermediate rollup writes were not established, and that run provides no validation evidence. The two affected tests now explicitly select Hub mode and replace the process entry. The successful reruns additionally blocked unisolated machine sync; no production credentials or provider stores were intentionally used as test inputs. Evidence and source hashes are retained under `~/.codex/task-artifacts/monitor-contention-20261008/`.

## 2026-10-08 indexed details and publication contention release

The owner explicitly approved pushing `bf28f21` and `c37ce00` and updating MacStudio only. Both remote main and the MacStudio checkout reached `c37ce001287af4223137ce22206df1cec01b5eec`; the existing Hub restarted. Health returned ok=true/stale=false, instance `211469c1b4cd4ae39babffdce57893de`, source signature `2a6569a1cf3a6f69` and Web signature `70184018a3cd4bfb`. The state symlink retained inode 5804813 and its original target.

The first observed generation with all four detail indexes was `5502d53af70ec6e2a0e8cb04dbb152d54baba43e3c77ed4467e30476feab5d58`, exported by `c37ce00`, published at `2026-10-08T03:24:59.889188Z`, with schema-10 statistics observed at `03:20:52.956280+00:00`. Request/attempt high watermarks were 518,689/518,906, with scan_complete=true and zero source errors or blocked sources. The real detail panel displayed this MacStudio observation time. A later successful generation advanced it to `03:25:08.191322+00:00`.

Production response times remain variable. One detail click on the first indexed generation rendered in 1.624s. After reopening the browser page, first/repeated detail and switching to a second request took 15.096s / 0.113s / 12.909s; another repeated click later took 1.358s. These are five observations over two successful macstudio/aihot requests, each with one complete attempt; historical route candidates and unknown cost remained visible. The three measured API resources spent 15.019s / 0.073s / 12.881s waiting for their first byte. Two 30d list loads took 43.557s and 67.886s. These observations do not establish a stable latency bound or resolution of the remaining wait.

A separate cold process on MacStudio executed the same detail function in 1.013s, including 0.997s validating digests of four admitted sources. Three paired, concurrent on-host probes measured the real loopback HTTP endpoint versus an independent process's function at 4.968s/1.007s, 0.044s/0.012s and 0.053s/0.007s. A two-second sample of the live Hub showed JSON decoding/GC work and the main thread waiting to reacquire Python's execution lock; peak physical footprint was 16.4GB. The sample was not simultaneous with the two slow browser clicks, so it supports investigating process contention without proving those clicks' precise cause. Background export and full validation still execute in Hub threads.

Source refresh is not uniformly healthy: the final sync observation reported a MacBook rollup-lock timeout and MacMini `sqlite3.OperationalError: database or disk is full`, retaining their previous generations; MacStudio and Tencent had successful contacts. The MacMini message is the export error, not a diagnosed filesystem cause. Other-machine deployment or service changes were outside this authorization. Remaining response and refresh work stays with `ISSUE-CALLS-20261006-6d4a`. Evidence is in `~/.codex/task-artifacts/monitor-contention-20261008/`.

## 2026-10-08 snapshot compatibility and targeted details release

The owner explicitly approved publishing `2933060` and `a94dfa9` and deploying MacStudio only. `origin/main` and the MacStudio checkout reached `a94dfa9`; `launchctl kickstart -k gui/501/com.agent-monitor.hub` restarted the existing Hub. Health returned ok=true/stale=false, instance `3ef70d28c701471785a4cd990261dd9a`, source signature `ddddc2831c371538`, Web signature `70184018a3cd4bfb`. The state symlink retained its target and inode 5804813. Other machine deployments and the Gateway checkout were not changed.

The scheduled statistics round exported and admitted MacStudio schema 10 as generation `abe39acc65894670969b86ee37cf5c62ca822384ce2a29ba97fc01fb99de127d`, with exporter commit `a94dfa9`, observed at `2026-10-08T02:46:19.239911+00:00`, generated at `02:48:15.214374Z` and published at `02:50:56.905153Z`. Request/attempt high watermarks are 518,577/518,794. The real browser displayed the new observation time, 518,547 matching requests and 518,769 attempts in its 7d window, and opened two October 8 requests with their complete one-attempt chains. This establishes recovery from the schema rejection and delivery of the new snapshot to the consumer.

Performance is only partially improved. During the first sync, an old request took 27.193s click-to-render, then its repeated HTTP detail request took 1.961s. After the generation changed, two new successful requests in the same machine/project took 14.569s, 4.094s (repeat) and 16.586s to render their details. One list request took 29.149s. A separate concurrent read-only probe obtained MacStudio's current generation in 16.044s versus 0.019–0.214s for the other three sources; this probe includes acquisition and validation and does not isolate lock time. Publication holds the machine lock across retention checks, copy and validation, so publication contention remains a candidate for the residual waits. The local ~1s result does not establish a production latency bound. No index, publication-lock or collection-cadence redesign was included in this release.

A subsequent automatic round reported a MacBook rollup-lock timeout while preserving its admitted snapshot; its cause was not diagnosed or changed under the MacStudio-only deployment scope. MacStudio reported successful contact and the new snapshot. The calls issue remains open for residual performance and the per-source refresh observation. Browser and read-only probe artifacts are in `~/.codex/task-artifacts/monitor-perf-20261008/`. This deployment record is a subsequent local documentation commit, outside the two-commit publication approval.

## 2026-10-08 snapshot compatibility and detail performance: local validation

Read-only production inspection found MacStudio monitor at `e91b400` and Gateway at `01602eb`, with an actual schema-10 ledger accepted by Gateway's validator. Monitor rejected that version through membership in Gateway's legacy fingerprint dictionary (versions 3–8). The admitted MacStudio generation remained `4d6d5e24fe7c41ee57236a2d8f505622d6e555a92b7da143283cad8bfa360508`, observed at `2026-10-06T06:32:59.527445+00:00`; the other three sources had successful October 8 refreshes. This local change accepts exact schema 9/10 projections and targets detail reads before Python row decoding. Publication, service restart and live freshness recovery have not been performed.

On one immutable copy of that production generation (369,775 requests, 369,825 attempts), local profiled detail reads took 23.783s with the full reader and 1.220s/1.001s with the targeted reader. Responses matched apart from the independently generated range end time. In an isolated read-only browser server using the same copy, first/repeated detail and a second request rendered complete chains in 0.930s/0.947s/0.925s (one machine, one project, two successful requests, one attempt each). These are local observations, not production or cold-cache guarantees. A separate live browser observation before deployment took 27.466s for the list and 54.258s for one detail; it is not a controlled cross-host speed comparison. List processing is unchanged.

Nine targeted test methods passed with isolated HOME: schema 8/9/10 round trips, schema-10 missing/extra fields and unknown-version rejection, plus detail/projection tests over two machines, three projects, 108 requests and 110 attempts. The 89-method affected legacy suite still reported 10 failures; the same baseline tree reported 7 failures and 9 errors under the same Gateway/interpreter, and every currently failing method already failed or errored on that baseline. This is not an all-suite pass. Independent review found no blocking findings; the non-HTTP query-shape limitation is recorded in `ISSUE-CALLS-20261008-9a72`. Evidence remains in `~/.codex/task-artifacts/monitor-perf-20261008/`.

## 2026-10-08 historical routing diagnostics release

The owner approved publishing `bff56cd`, `d85514f`, `be404e9` and `e91b400` together and updating MacStudio only. Remote main and the deployment checkout reached `e91b400`; the existing Hub restarted through `launchctl kickstart -k gui/501/com.agent-monitor.hub`. Health returned ok=true/stale=false, instance `81397e06cad140f1812404095718f032`, Web signature `70184018a3cd4bfb`. Served llm-calls.js, styles.css and app.js matched source bytes. The state symlink retained target `/Users/lindong/.local/share/agent-monitor/state` and inode 5804813. No account action or backend optimization was performed.

A real isolated browser used an SSH loopback forward after direct hostname navigation failed while direct HTTP reads worked. From the 7d list it opened one macstudio/aihot request: policy selection, pin-not-applicable, first route `company_tencent_vod/deepseek-v4.1-flash/stream`, one successful attempt, 1734ms, 2309 tokens and unknown cost. Expanding three candidates showed one eligible route with an attempt and two ineligible routes with raw reason `caller_route_not_allowed`. Desktop 1440×900 and narrow 390×844 were inspected; document width remained 390px. Escape restored the same list/range. Live pin/retry/unknown cases were not exercised; those have isolated fixture coverage.

The list resource took 27074.5ms and detail 35417.2ms, one observation each. The selected MacStudio source/detail snapshot remained dated 2026-10-06 14:32:59 while other sources were dated 2026-10-08. UI rendering therefore does not establish data freshness or backend performance; those concerns remain with the owner's separate backend session. Evidence: `~/.codex/artifacts/routing-phase46-20261008/`. This subsequent documentation commit is local, separate from the approved product publication.

## 2026-10-08 local Codex quota display release

The owner approved applying `d85514f` to the MacBook service on port 39001. Local `main` fast-forwarded from `bff56cd`; the served `app.js` and `codex-accounts.js` matched the committed files byte-for-byte with `Cache-Control: no-store`. No backend restart, provider message, push or remote rollout was performed. The state symlink retained its target and inode 102763913.

An isolated browser displayed future reset countdowns for all six existing Codex identities (three current machine records and three historical records): four rows used the newer page query, and two retained newer machine observations. All six account cards displayed countdowns and absolute reset times; the batch summary read “本轮发送成功 6/6 · 下次重置时间已知 6/6”. The historical rows remained expanded across subsequent polling. This verifies one live local snapshot and existing results, not a new message or a claim that sending establishes a seven-day cycle.

Health reported `ok: true`, instance `037440e92f6142e09c942da4da08ff3a`, Web signature `5e21c56d74a16ac6`, and `stale: true`; the page also showed the backend-version warning. The stale flag concerns the loaded Python source signature, while this release changes only Web assets and documentation/tests. Its underlying source drift was not diagnosed or reloaded as part of this release; the frontend result above does not establish backend freshness.

## 2026-10-07 MacStudio quota explorer release

The owner approved publication and MacStudio rollout through `14b5944`, explicitly including the intervening batch-account and discovery commits. `origin/main` and the remote checkout reached `14b5944`. The existing `com.agent-monitor.hub` job restarted with `launchctl kickstart -k`; health then reported `ok: true, stale: false`, instance `aa2b51c2b7f34369aed8aef12c616ff7`, Web signature `67b55ac9a4a77ff8`. Served index, app.js, styles.css and codex-accounts.js matched this source revision byte-for-byte. The state symlink target and inode remained unchanged.

An isolated browser reached the MacStudio service through an SSH loopback forward and read 11 quota records: 5 current and 6 historical. A no-match search produced 0/11 with the explicit empty-state explanation; clearing restored 11/11. At 390×844, document width was 390px. This is one live data snapshot, not broad performance acceptance. Direct browser navigation to `macstudio` encountered HTTP 502 while direct HTTP reads worked, so the successful consumer path was the SSH forward; the temporary forward and browser were closed afterward. No account login, provider message, deletion or quota reset was performed. This documentation commit is local and is not included in the product publication above.

## 2026-10-07 local known-account discovery release

The owner approved local rollout of `4e863af`. Local `main` fast-forwarded from `bc626fd`; `POST /api/restart` re-executed the existing port-39001 service. Health reported `ok: true, stale: false`, instance `037440e92f6142e09c942da4da08ff3a`, Web signature `5a135addcc4d608e`. Served index and account script matched source bytes. The original state symlink target and inode were retained. No push or remote rollout was performed.

The actual account endpoint discovered six eligible identities, zero unavailable records, zero active operations and zero saved operations. One request took 10ms; this is a single local observation, not a performance guarantee. An isolated headless browser displayed six account cards and an enabled “给全部 6 个账号发送” button, with no add form or account notice error. No real authorization or message was initiated. This establishes live discovery and the page entry, not real provider message or reset acceptance.

## 2026-10-07 local Codex account batch release

The owner approved local rollout of `517903c`. Local `main` fast-forwarded from `4221af9`, then `POST /api/restart` re-executed the existing service on port 39001. No push or remote deployment was performed. The state symlink retained target `/Users/lindong/.local/share/agent-monitor/state` and inode 102763913.

After restart, `/api/health?asset_watch=1` returned `ok: true, stale: false`, instance `5d811fc66b4e4065941dde17836a1902`, and Web signature `cdad601ed5b14ad9`. Served index, account script and CSS matched the committed bytes. The account endpoint returned the new `batch` field with no saved accounts. An isolated headless browser read the open one-click panel and its empty state; the zero-account send button was disabled. This verifies one local empty-state deployment, not real account authorization, message delivery or reset behavior. Synthetic batch coverage is recorded in the [batch decision](../adr/20261007-b923-codex-account-batches.md).

## 2026-10-07 MacStudio session detail and account actions release

The owner explicitly approved publishing and deploying `6367450`, `8c75d17` and `66a2ecf` together. `origin/main` and the MacStudio checkout reached `66a2ecf9ae0b1085e8b117c181b48877f8549f60`. The existing `com.agent-monitor.hub` job was restarted with `launchctl kickstart -k`; PID 91480 uses the same checkout and `.venv`. The state symlink still points to `/Users/lindong/.local/share/agent-monitor/state`.

`/api/health?asset_watch=1` returned `ok: true, stale: false`, with Web signature `3a83398b7b027985`. HTTP-served app.js, styles.css, sessions.html and codex-accounts.js matched the committed source. The real browser opened a session with 6 retained records, 472,628 tokens and $0.9356 under existing pricing; the 2-minute span is not active duration. It opened source fields and returned to 100 list rows while preserving machine=macbook and sort=cost. Both 1440×900 and 390×844 were inspected; the latter had document width 390px. A single list-click-to-detail observation took 250ms (one session, one model, one machine); this is not broader performance acceptance.

Account-action code is included in this rollout, but no real account was authorized, messaged or reset. Existing backend performance and assertion issues remain with the owner's separate session. The later documentation-only commit records this deployment locally; it is not part of the three-commit publication approval. Evidence: `~/.codex/artifacts/session-detail-phase44-20261007/`.

## 2026-10-07 local Codex account actions release

The owner explicitly approved merging `6367450` and restarting only the MacBook service on port 39001. Local `main` fast-forwarded from `8182b64`; the existing process runs this checkout's `server.py` with its `.venv` interpreter. `POST /api/restart` re-executed that service. No Git push, remote deployment or MacStudio service change was performed.

After restart, `/api/health?asset_watch=1` reported `ok: true, stale: false`; the new account list endpoint returned an empty list. Served index, app script, account script and CSS matched the committed files byte-for-byte. A separate headless browser opened the real account panel and displayed the empty state without an account-action error; two account-list requests took 5ms and 9ms in that browser. These are individual observations, not a latency guarantee. The original `state` symlink target and inode were unchanged.

No real account was authorized or messaged in this release verification. The user must complete official authorization for the selected email before the actual send and reset behavior can be observed. Isolated synthetic coverage and the deferred multi-process limitation are recorded in the [account actions decision](../adr/20261007-c814-codex-account-actions.md).

## 2026-10-06 overview UI release

With explicit owner publication/deployment approval, MacStudio `/Users/lindong/research/agent-monitor` fast-forwarded from `1b3b8f1` to `8036b9c`. Only the Web layout and documentation changed. The existing `com.agent-monitor.hub` service, interpreter and state path remain in use; no collector configuration or stored account/statistics data was edited.

`./agent-monitor restart` stopped the Hub but its immediate `launchctl bootstrap` returned error 5. The job was absent and port 39001 refused connections. A subsequent `launchctl bootstrap gui/501 ~/Library/LaunchAgents/com.agent-monitor.hub.plist` succeeded against that same definition; the loaded Hub PID was 18133. This is a recovery observation, not a diagnosed root cause or lifecycle fix. If it recurs, inspect actual launchd state before retrying; the backend/lifecycle investigation remains outside this UI change.

After recovery, `/` and `/web/styles.css` matched the deployed source byte for byte. `/api/health?asset_watch=1` returned ok=true/stale=false (omitting asset_watch=1 deliberately reports stale in the existing API). A real browser opened `/?range=7d`, read three cost summaries, seven trend buckets and 4/4 machine coverage, followed the trend link into Explore and returned with the same range. Desktop and phone-sized renderings were inspected. These checks establish this UI rollout; they do not establish backend performance, all machine refresh success, native mobile behavior or complete UX-contract acceptance.

## 2026-10-06 calls explorer release

The owner explicitly selected publication and deployment for this unit together with the existing `12a9ae2` and `c83e99a` commits. `origin/main` was published at `25697a3`; MacStudio `/Users/lindong/research/agent-monitor` fast-forwarded from `21966eb` to that revision. The existing job was restarted with `launchctl kickstart -k gui/501/com.agent-monitor.hub`, and the resulting PID was 90212. `/api/health?asset_watch=1` returned ok=true/stale=false. The HTTP-served `llm-calls.html`, `llm-calls.js` and `styles.css` matched the deployed source hashes.

The real browser at `/llm-calls?range=7d` displayed 50 rows and 409,355 matching requests. Opening one `macstudio` / `aihot` request showed a successful `deepseek-v4.1-flash` call with one complete attempt, 1734ms, 2309 tokens and unknown cost; each of the four sources retained its own observation time. Escape closed the dialog and returned to the list. Selecting project `aihot` produced 369,757 matching requests. Only macmini/macstudio were valid machine options in that range; an initial macbook selection was cleared under the existing H5 invalid-option behavior, not a failed machine filter. The attempt-parent entry on machine macstudio resolved the same aihot parent and complete one-attempt chain (1734ms/2309 tokens/unknown cost), with a single resource duration of 30.762s. Escape after the first request-detail entry was verified; after the attempt-entry Escape command, browser connectivity was lost, so no second focus-return observation is claimed. Production export correctness was subsequently revalidated as recorded below.

Single production resource observations were 39.916 seconds for the list and 36.493 seconds for detail. These are one observation each, not a cold/warm comparison, performance acceptance or evidence that the UI caused the wait. Backend performance and the existing assertion failures were assigned to the owner's separate backend session; the history of `ISSUE-CALLS-20261006-6d4a` is now in [closed issues](../issues/archive/closed.md), while `ISSUE-TEST-20261004-6b2a` remains in [general issues](../issues/general.md).

The subsequent production request export failed its file check: HTTP 200 transferred 618,330,927 bytes in 252,832ms, but the downloaded JSON file contained only the four bytes `null`. The frontend swallowed a JSON parsing failure and serialized null as a successful download; the underlying parser failure is not yet diagnosed. A local patch now consumes the response as a Blob without parsing/re-serializing the complete JSON, with a failing-before/passing-after regression test. Targeted review passed and the patch was published and deployed as `0b85ade` (Hub PID 15184). Asset-watch health returned ok=true/stale=false; served `llm-calls.js` SHA256 `a47222c9a97e3b26002ab5b5592bac59c384f58966f28445cfdebfa15faa7df3` matched the source. The original failure is retained here as historical evidence; the subsequent file check succeeded.

The real browser exported requests for 7d/project=aihot: HTTP 200, resource duration 208,062.5ms, actual file 618,330,927 bytes. A complete `jq --stream` parse counted 369,757 items, equal to the visible matching count and greater than the 50-row page; projects contained only aihot, machines only macstudio, kind=requests and format=json. File SHA256: `f3a86647d327c661745b0626c12fd4f0550a4bbcce1dee8da3146fcf89831316`. Local evidence: `monitor-calls-phase42-20261006/production-export-file.json` and `production-export-download.json`. This completes this unit’s request-diagnostics and production requests-export journey; attempts export has isolated fixture coverage only. H9/H10 are synchronized and `ISSUE-UX-SYNC-20261006-b52c` is archived. The single export duration is not performance acceptance; backend waiting/assertions and existing navigation issues retain their original owners. Formal Prompt Planet template publication remains a later unit.

## Existing-deployment migration

1. Inspect each active process/service's actual source and state paths, role, service owner and outstanding collectors before changing it. Stage the new code, runtime and assets with `--no-services`; this changes CLI links but leaves services alone.
2. Stop the old Hub pull schedule and relevant old collectors/writers before changing state. Resolve the real directory behind any release symlink. Wait for the actual locks to be available; do not remove or recreate persistent lock files.
3. On the same filesystem, move the stopped state directory to `~/.local/share/agent-monitor/state` and point the new `root/state` there. Preserve an old-path compatibility symlink and the old source/service definitions for rollback. Do not copy an active SQLite tree or run parallel old/new writers.
4. Start the new local exporters/collectors first and verify their source identity and retained history. The old Hub may require a temporary `tt-web` CLI compatibility link during the transition. Switch the Hub only after its new `agent-monitor` remote commands work; then retire old labels and compatibility commands.
5. Verify source revision, loaded working directory/interpreter, real CLI/API paths, admitted machine identities and history, then browser navigation and refresh persistence. Code tests and a healthy endpoint alone are not deployment acceptance. Rollback restores the old source/service definitions against the preserved data; it does not roll data backward.

Current service status is available through `agent-monitor status`, `./status.sh rollup-daemon` and the per-machine Web sync panel. These are existing observation paths; this extraction does not add independent push monitoring. A fresh deployment must establish its own live status rather than treating the historical records below as current.

## Historical tt-web operations and deployment record

The following content was imported from ai-agent-config `3d61487d5386dfe9bb6ba7d26807005bbf0bd7f2` on 2026-10-04. Original names, paths, revisions, dates and timings are preserved. They document the former installation, not proof that the renamed deployment has been cut over. Parent-repository relative links are converted to immutable source links.

# tt-web Services

This page is the operational inventory for long-running tt-web services. Source,
state, logs, and generated SQLite files stay inside `tt-web/` unless noted.

## Service Inventory

| Service | Default | Supervisor | State / logs | Purpose |
| --- | --- | --- | --- | --- |
| `tt-web` | Manual | PID file in `state/pid`; port in `state/port` | `state/server.log` | Local dashboard server for usage, cost, sessions, Explore pivots, and `/network`. |
| `com.ttweb.rollup` | Installed on macOS | macOS LaunchAgent at `~/Library/LaunchAgents/com.ttweb.rollup.plist` | `state/rollup.db`, `state/rollup-daemon.log` | Runs `tt-web refresh` hourly: requests or joins dashboard-owned cross-machine sync when available, and otherwise updates only the local rollup with a non-zero partial result. |

## Operations

### 2026-10-03 刷新异步受理与逐机收敛

本次优化以首页点击后可继续操作、后台真实更新为验收对象。MacStudio 独立部署为 `~/.local/share/tt-web/deployments/async-refresh-20261003`：首版 `22816075`，受理脱离快照锁的补丁 `d3c5fe37`。MacMini 同名部署为 `e3b02447`，Tencent 为 `1bfd8cd8`，后二者仅更新 `exporter.py` 和 CLI 的 quota-only 路径；MacBook 使用本地主线。五个 Hub Python/JS 文件与本次源码逐字一致；MacStudio CLI 保留部署基线中没有 `recover-reviewed` 的旧路由差异，本次 quota-only hunk 一致。Hub、MacStudio/MacMini rollup LaunchAgent 与三台远端 CLI 均已切换，原 `state` 和 Mac 上的 `web/vendor` 继续复用。原 release、`previous-cli.txt` 与 `previous-launchagents/` 保留用于恢复匹配的 CLI/采集器/Hub，不回滚统计数据，不重启 Gateway。

受理只读机器配置并登记本次请求序号，`/api/refresh?ack=1` 返回内存状态；浏览器另起后台读取。首次上线仍经 force overview 读取快照，真实点击为 13.855 秒，资源 TTFB 为 13.823 秒；reader 与 publisher 共用机器 GC 锁，因此只移除全轮等待仍不充分。分离 ACK 后，同一个隔离 headless 浏览器两次实际点击为 26.4 / 45.8 ms，均恢复按钮并显示“刷新请求已受理 · 后台更新，当前数据仍可查看”。这是两次点击样本，不是 p95 或全环境保证。

校验缓存首次读取仍付完整验证：新进程首次 sync-status 为 983.8 ms，随后同入口 3.3 ms；overview 两次为 17.4 / 16.1 ms。此前空闲样本分别为 sync-status 356 / 281 ms、overview 294 / 294 ms；它们是跨时样本，不能当作受控倍数对比。发布仍完整校验并复制快照，后台读取仍可能等发布锁；统计/配额收敛时间与 ACK 时间分开报告。属性不变的底层静默损坏不在 stat 缓存检测范围。

首次生产整轮请求完成读数为 clicked +114.880 秒（`completed=requested=1`），四台机器陆续出现新 generation；当时 MacMini GUI 配额拒绝新 CLI，因为 loaded collector 仍指向旧 release。已把其 rollup plist 与 CLI 对齐并重新加载；该校验不能通过仅改变 CLI 路径满足。生产失败提示保留旧配额与原时间；不得以 statistics success 冒充配额查询成功。

最终 ACK 分离版两次点击相隔 10.914 秒，后台请求完成观察点为第一次点击后 113.549 / 222.830 秒，即第二次自身等待约 211.916 秒（含排队）。最终 `refresh_requested=refresh_completed=2`，四机最近 attempt 均 success，页面实际显示新统计和 Codex 新配额；此后定时统计又启动，不把全局 `syncing=true` 当作这两次点击未完成。四机 quota metadata 的 `refresh_error` 均为空；MacMini/MacStudio Claude 当前 signed_out，Tencent 两 provider signed_out，页面另保留一个 MacMini Claude 历史账号的三天前读数及“可能早于一次登录变更”，不把历史值算作新鲜配额。两次点击、四来源、新旧 generation 和 request counter 是本次生产验证范围，没有覆盖长期上游可用性。

相关 9 个测试模块共 175 个 test 方法通过，覆盖缓存命中与 DB/meta/WAL 变更、来源匹配与错误、逐机流水线、重叠请求和初态已完成、ACK 不触达快照、Sessions 后台观察等 fixture/参数；不代表长期稳定性。独立代码审查发现的请求序号竞态已修复并复核，Sessions 观察和重试后旧错误提示残留亦已修复。原有无人值守刷新告警待办不变；本次没有新增告警通道。实测明细位于执行机 `/Users/lindong/.codex/task-artifacts/ttweb-async-20261003/`。

### 2026-10-03 MacStudio schema 8 刷新恢复

MacStudio 数据停在 `2026-10-02T07:53:24.369886Z`；Hub `/api/sync-status` 持续报告 `invalid gateway row fields`。现场 Gateway `f4711f2` 的账本为 schema 8，新增 `caller_route_constraint_json`，但 tt-web `dfcb3745` 的请求字段映射只到 schema 7，schema 8 因而退回 v3/v4 字段表并拒绝整机导出。其他机器仍能更新。修复显式映射 v8 字段，保留 schema 指纹、未知字段拒绝和历史保留检查。

生产修复 `478c24945c2b65b0d8d704665fcb874dfcfaca67` 位于 MacStudio `~/.local/share/tt-web/deployments/schema8-20261003`，由原部署的独立 clone 加本次两文件补丁构成；Hub、rollup LaunchAgent 与 `~/.local/bin/tt-web` 均指向新目录，`state` / `web/vendor` 仍使用原路径。原部署和 `previous-launchagents/` 中的 plist、CLI 目标保留用于回退，远端共享 checkout 和推理 Gateway 未修改。切换 Hub 时一次 `launchctl bootstrap` 返回 5，随后重试成功；只换 plist 不代表服务已加载，须核对实际 PID 和 API。

真实 Gateway writer 的回归在补丁前出现三处 v8 导出错误（含两个 subtest），v7 用例仍通过；补丁后三个 test 方法通过，覆盖 v8 的 null / 非空 constraint、缺失 / 多余字段拒绝，以及非空 v7 快照无迁移读取。另从真实只读账本读出 16,363 条请求和 16,175 条尝试。MacBook 使用 v7 Gateway 的 30 个相关既有用例通过，新三个 v8 用例因该依赖版本跳过，不能以本机结果代替远端 v8 验证；两端最终 runtime 和新测试文件 SHA 已逐一比对相同。修复后 MacStudio 已连续发布 `2026-10-02T22:44:06.304873Z` 和 `2026-10-02T22:44:59.022835Z` 两份 generation，实际总览页面显示本机可用和新时间。

探活入口是同一 Hub 的 `/api/sync-status`：将 `last_attempt_ts` 持续推进但 `last_attempt_outcome=failure`、`generated_at` 停滞视为刷新失败，并读取 `reason`；有新 generation 且最近 attempt 成功是恢复读数。HTTP 200 只证明服务可访问。该失败面尚未纳入 user-scope 增强服务探活清单，待办归 ai-agent-config 维护者；既有无人值守刷新告警待办仍保留，不宣称本次实现了告警。

### 2026-10-01 配额恢复修复部署

用户明确授权本次修复的 push 和部署；源码 `f0d8c282` 已包含在 `origin/main` 的 `ca3a830f`。独立部署 decision-review 七项成立，部署保留两机既有 `~/.local/share/tt-web/deployments/codex-repair-20260921-f0903db` 目录、状态、配置和运行入口，仅应用本次修复：

| 机器 | 部署提交 | 父提交 | 本次更新与源码核对 |
| --- | --- | --- | --- |
| MacStudio | `dfcb374582291cacf3a6ecd4b6101665d32d4735` | `bca665a0` | `exporter.py`、`server.py`、`web/app.js` 与修复源码逐文件相同 |
| MacMini | `0a54a8cca29f3257b9f16115f3dc30ce45c111ce` | `996e09c9` | 仅 `exporter.py`，与修复源码相同 |

两机 `export --version` 均报告各自新提交；MacBook 原 main 已包含源码，无需更新；Tencent 不扩展部署。仅以 `launchctl kickstart` 重启 `com.ttweb.hub`，PID `56377` 的 cwd 经 `lsof` 核为上述 release 的 `tt-web`。HTTP `/web/app.js` 的 SHA256 为 `7a61edd398973bc101413d7406febe2e36d102b7a8c1869a4f6953e60b58d363`，与本地修复源码相同。回退方式为反向提交本次目标文件 patch，必要时重启 Hub，不回滚数据。

部署后通过独立 headless 浏览器在真实页面点击 Refresh，一次请求已满足 `refresh_requested=refresh_completed=1`；页面 `#refresh` 恢复“⟳刷新”、`aria-busy=false`。四台声明机器最近 attempt 均为 success（macbook、macmini、macstudio、tencent-webserver-china），MacMini 的 generation 为部署后的 `2026-10-01T02:59:28.685246Z`。两个 provider 的刷新错误均为空；Codex 显示 7D 30%、更新刚刚，无错误详情，`reading_from=macbook`，读数时间为 `2026-10-01T03:00:13.794Z`。采样时已开始下一轮自动 statistics，`terminal=false`、`completed_at=null`，未捕获空闲终态；请求完成由计数和页面按钮状态共同确认。机器与配额快照保存于主线程本机 `/Users/lindong/.local/state/ttweb-quota-recovery-20260929/deployment-20261001.json`；页面读数来自同轮浏览器观察。

范围外基线仍包括 MacMini statistics 的 `blocked_source_count=1`，以及一个 Claude 历史读数停留在 `2026-09-29T19:12:12.335972+00:00`、`reading_from=null`，页面标注可能早于登录变更；本次不扩展修复，不宣称所有历史读数均已新鲜。以上覆盖一次手动页面刷新、四台机器最近 attempt 和两个 provider 的当前错误状态，不证明长期稳定性或真实上游超时已消失。

### 2026-09-28 LLM 页面组合读取与 MacMini 审定身份应用

用户明确批准本轮生产部署与应用后，本地 tt-web 来源 `72491193`、Gateway 来源 `8f30098` 已整合至各自本地 main。MacStudio 实际部署为 tt-web `bca665a0299f7d6fea0536be0ad8782fd2e095d0`、Gateway reader `7eda44aa978b873dee7b2746443e29d977214226`；MacMini tt-web 为 `996e09c96f4e150e7d935da61e53fe365a0eb513`。tt-web 部署目录均为 `~/.local/share/tt-web/deployments/codex-repair-20260921-f0903db`。Hub PID `38825` 的实际 cwd 已核验，instance 为 `ebac006eadf84703a43d305c3fd6d337`；生产返回的 `llm-calls.js` SHA 为 `08909e141a0d8c48d65be265b2638483aee5ff128f317e3f127fb771b449f7d6`，响应为 `no-store`。仅重启 Hub，推理 Gateway 未重启。

LLM 页面使用组合 reader 后，真实生产 `7d` 初次 rows 可读为 7.6671 秒（API 4.1781 秒），选择 `machine=macbook` 交互为 4.7179 秒，保留该 machine 切换 `30d` 为 3.9783 秒；抽查两条真实 rows 均符合 macbook。最终 LCP 为 8056 ms；早期 3536 ms 不是最终值，首内容可读仍按 7667.1 ms 单独报告。样本为一次首屏、一次机器筛选、一次范围切换，不是冷热或全过滤矩阵。可比较的性能证据仍是固定四来源、完整响应相等的只读 A/B：6.1963 → 3.7367 秒，约减少 40%；生产首屏跨时读数不用于声称受控提速。

MacMini 实际 apply 匹配 41 条审定映射，随后 active blocker 为 0；apply 前后 archive／rollup 文件 SHA 完全相同，身份操作未重写事件或桶。之后的常规 rollup 在 9 月 1 日至 13 日的 46 个桶增加 `entry_count` 共 21,863，9 月 1 日前未变；额外 9 月 28 日变化属于实时增长，不能并入本批恢复。仍有 38,587 条受既有历史保护冻结（并非全在 9 月 1 日前，其中 725 条为 9 月 1 日 ai-radar Codex gpt-5.4 的新模型桶，仍受旧保护规则约束），不宣称全部历史项目总量补齐。备份与 manifest 在 MacMini `~/.local/state/ttweb-followup-20260928/macmini-identity`；回退须考虑常规投影及已发布汇总，不能把只回退 identity 当成完整恢复。

Hub 已成功接收 MacMini generation `4bf7b45a5068ab422d9a505c417791a29456807db5df6036ec775ac986144fcd`，blocked 为 0；实际 Hub pivot 已取得五个目标项目聚合。恢复已消费到真实项目网页：`/explore?range=30d&x=project&group=none&metric=cost&machine=macmini&project=github.com%2FSJTU-AAA%2Fsjtu-aaa-homepage` 显示一个组合 `github.com/SJTU-AAA/sjtu-aaa-homepage`、估算成本 USD 3008.1725，与该项目 9 月 1–13 日 pivot API 一致；主线程已查看真实截图 `production-project.png`。这是一项目／一组合的网页验证，不代表五项目均逐一网页验收。本次获授权的主动 normal round 已收口：`refresh_requested=refresh_completed=1`，四机最近 attempt 均 success、source_error=0、blocked=0；观察时 phase 已转为下一次 automatic statistics，证明本次主动刷新完成且自动轮继续，不声称捕获 terminal=true 瞬间。MacMini `observed_at=2026-09-28T09:21:09.843712+00:00`。一轮 MacMini 曾遇 rollup lock timeout，下一普通轮已成功且解除，无服务停止。

本轮证据位于 `/Users/lindong/.local/state/ttweb-followup-20260928/`：`production-browser.json`、`llm-page-ab.json`、`macmini-production-final.json`、`sync-after-apply-latest.json`、`production-project-pivot.json`。关键身份与读数已在本文保留；`production-browser.json` 有 JSON 字符串封装，`macmini-production-final.json` 有 OSC 前缀，解析时需按实际格式处理。本节是随后已授权应用的当前记录；下方较早 MacMini 调查段的“未恢复”描述保留其当时事实。

### 生命周期操作

| Operation | Web server | Rollup daemon |
| --- | --- | --- |
| Install | `./tt-web/install.sh` | `./tt-web/install.sh rollup-daemon` |
| Status | `./tt-web/status.sh web` or `tt-web status` | `./tt-web/status.sh rollup-daemon` |
| Integrity check | — | `tt-web rollup --check` or `tt-web rollup --check --json` |
| Refresh now | `tt-web refresh` | `tt-web refresh` (the scheduled command) |
| Start | `./tt-web/start.sh web` or `tt-web start` | LaunchAgent runs at load and every 3600 seconds; reinstall to load |
| Stop | `./tt-web/stop.sh web` or `tt-web stop` | `./tt-web/uninstall.sh rollup-daemon` |
| Uninstall | `./tt-web/uninstall.sh web` | `./tt-web/uninstall.sh rollup-daemon` |

No-argument `./tt-web/install.sh` installs the web assets and CLI links plus `com.ttweb.rollup` on macOS. The repo-root installer invokes this same default path; `INSTALL_SERVICES=0` only gates the other optional services. Unsupported platforms report `[skipped: platform]` in the root ledger for rollup. The existing `TT_WEB_ROLLUP_INTERVAL_SECONDS` override defaults to 3600; the job runs on load and then at that interval, exiting after each run. It calls `tt-web refresh`. If the dashboard is running, refresh asks that process to own the cross-machine sync or joins its current round. If the dashboard is stopped, refresh still scans local source logs and updates `state/rollup.db`, but reports that cross-machine usage and Quota were not refreshed, exits non-zero, and directs the operator to run `tt-web start` followed by `tt-web refresh`. The schedule does not add or accept a host in `machines.json`.

The schedule is a machine-local singleton. Its rendered plist and loaded program identify the owning checkout. Reinstalling an unchanged loaded schedule does not restart it; a foreign owner is preserved and reported as failed. To move the schedule, run `uninstall.sh rollup-daemon` from its current owner before installing from the new checkout. Removing the schedule keeps the databases; a later default installation reinstalls it. The GUI launchd domain must exist, so a fresh host must have a macOS desktop login before bootstrap can succeed.

The hub must inherit Apple's current login-session SSH agent instead of storing its temporary socket in `com.ttweb.hub.plist`. After updating the code, run `tt-web start` on MacStudio: the installer replaces an old pinned Apple socket and reloads the hub once. Fixed custom agent paths, including 1Password, remain explicit. Subsequent starts after desktop login use the new GUI environment without reinstalling. Diagnose stale data with `/api/sync-status`: a loaded job alone does not prove successful pulls. A missing old socket with current GUI-agent authentication succeeding indicates this installation fault; check a real automatic sync round after migration. This does not enable collection before desktop login or supply unavailable SSH keys.

No-argument `./tt-web/status.sh` is read-only and prints both web and rollup
daemon status. No-argument `./tt-web/uninstall.sh` removes both service layers
if present while keeping source, `state/`, and vendored assets.

## Cross-Machine Sync

### 2026-09-28 recovery and deployed freshness change

The actual MacStudio Hub checkout inspected on 2026-09-28 was `~/.local/share/tt-web/deployments/codex-repair-20260921-f0903db`, originally at `e3074243`. An uncommitted Gateway schema v7 compatibility patch caused the exporter's dirty-source refusal. The owner authorized validating and committing that existing patch; commit `78b44d199a9101d54593bac9647167e3ee1db263` restored export without restarting the service. One four-machine round ran from `2026-09-28T03:09:56.992Z` to `03:11:06.331Z`, with advancing source times. MacMini still reported `blocked_source_count=41`; source identity protection was not relaxed. This is recovery evidence for that round, not deployment evidence for the freshness optimization.

[ADR 4f72](https://github.com/lindong28/ai-agent-config/blob/3d61487d5386dfe9bb6ba7d26807005bbf0bd7f2/docs/adr/20260928-4f72-ttweb-progressive-freshness.md) is implemented and deployed to the production Hub: start-time throttling uses a 10-second scheduler check while retaining the 120-second Hub statistics interval and non-overlap. Active refresh rounds first publish statistics without querying Quota, then run the active Quota phase. Status distinguishes phases and per-machine progress; requested/completed accounting and the follow-up round for a click made after the current round began remain required. Statistics can become visible before completion; completion still includes Quota. Visible data pages reread admitted results every 30 seconds and on focus/visibility return, and check generation changes every 2 seconds during synchronization. These browser reads are not new remote export or provider-query schedules. Active rounds add a statistics export and may take longer to finish Quota and return the button to idle.

Production deployment of ADR 4f72 was explicitly approved and completed as remote commit `287a6aa0ef632aad5be7f7a82b5b61165f4426e6`, derived from local `69deac92` after five target-file baseline SHA checks. The five deployed files match the local source, and the remote `tt-web` scope is clean. Only `com.ttweb.hub` was restarted with `launchctl kickstart -k`; PID `26791` runs from `~/.local/share/tt-web/deployments/codex-repair-20260921-f0903db/tt-web`. Runtime exporter version matches that remote commit, API instance is `5f97dc99dac443eebb93ca6415efce42`, and production HTTP serves three JavaScript files matching `69deac92`. The API showed `phase=statistics` with MacStudio finished while MacMini was still syncing. Bounded production verification completed one journey per data page, each with initial and subsequent reads. Overview showed new statistics at observer +38499 ms while busy and returned to idle at +197805 ms (the button entered busy at +230 ms). The manual round completed at `05:14:17.889671Z` with requested=completed=1 and four successful machine attempts; the next automatic statistics round started at `05:14:33.226237Z`. This confirms resumption after a long round, not a measured fixed 120-second SLA. Explore retained 7d/codex, Sessions retained 7d/codex/page 101–200, and LLM Calls retained 7d/machine=macbook across subsequent reads. Detailed timings and the full-snapshot parsing bottleneck remain documented in ADR 4f72; this does not claim all pages load quickly. Long-term stability and exhaustive failure combinations were outside this bounded verification. Before deployment, an agent-only verification proxy served the new frontend against the existing production Hub: one Overview refresh displayed a cost change from `77.70` to `81.60` after 27.460 seconds while the button was still refreshing; it subsequently reached its terminal state, without a precise total-duration measurement. This covers one page and one refresh, not the new backend or all four data pages, and is not a matched-input comparison with the earlier 117-second observation. Local tests were run; focused implementation re-review approved the fixes for failure propagation, Sessions/LLM Calls generation reads, and Sessions page clamping, with none of those three HIGH findings remaining. A separate Sessions browser resource sample took 38616.8 ms for one `/api/sessions` request before normal sync-status polling resumed; server-side full-list read latency remains a known boundary, not a claim of this optimization. No service, port, or LaunchAgent label is added. Per-machine failures and original observation times remain the failure surface; the existing scheduled network/provider push-alert TODO below remains owned by the ai-agent-config maintainer. This change does not implement or send alerts when no page is open.

The dashboard machine pulls usage from machines that are enabled in `tt-web/machines.json` and also listed in `tt-web/hub.json`. The dashboard process is the single owner of this work: a page load pulls when its data is older than 10 minutes, the page Refresh control forces a pull, and `tt-web refresh` forces or joins the same synchronization path. Declaring, retiring and first-use acceptance are covered in [../../README.md](../../README.md#machines). The 2026-09-10 rollout added the `tencent-webserver-china` producer that hosts `sjtu.aiplanet.live`; its production checkout, CLI binding, identity admission, and Refresh validation are complete, so the live hub includes macbook, macmini, macstudio, and tencent-webserver-china. dgx0023 remains declared but disabled and outside the hub scope.

Edit [../../machines.json](../../machines.json) on the dashboard machine to manage every pull target. Prefix a machine's whole line with `#` or `//` to stop future pulls and hide it from subsequent page requests and statistics; remove the prefix to restore its stored history. Trailing commas and indentation before the marker are allowed, but inline comments are not. Keep the single `self: true` entry enabled. The file is reloaded without a server restart or Git commit; a pull already in flight finishes internally. Commenting does not retire a name or delete its stored data. The separate permanent-retirement command preserves other machine comments when writing the configuration.

| Operation | Command | Notes |
| --- | --- | --- |
| Produce a snapshot (runs on the remote, normally invoked over SSH) | `tt-web export --out <dir>` | Writes `snapshot.db` + `export.json`. Refuses when the snapshot-producing code differs from `HEAD` — `*.py`, `parsers/**`, `pricing.json`, `tt-web`, `install.sh`. Uncommitted docs, `web/` assets or `machines.json` do not block it. |
| Report the exporter's commit | `tt-web export --version` | Same scoped refusal — it will not print a clean-looking SHA it cannot stand behind. |
| Bind a newly declared machine to its SSH target | `tt-web machines accept <name> [--yes]` | Required once before that machine can be pulled. Prompts with the name and target; refuses to write a binding unless confirmed. |
| Retire a machine name permanently | `tt-web machines retire <name>` | Written before `machines.json` is updated; the name can never be reused. |
| Adopt a bucket-timezone marker on an unmarked database | `tt-web rollup adopt-timezone --db <path> --known-utc-offset +08:00 …` | For databases predating the marker. Requires explicit authorization flags and prints exactly what it did and did not verify. |
| Refresh local and cross-machine data | `tt-web refresh` | With the dashboard running, requests or joins its current sync round. Without it, updates local rollup only, explains the missing cross-machine and Quota refresh, and exits non-zero. |

Published snapshots live under `state/generations/<machine>/<generation-id>/`,
with a `current` pointer per machine. Each machine keeps its current and previous
generation; older ones are removed as new ones publish, and a generation a reader
holds open is not removed under it.

Failure of one machine never blocks the others: that machine keeps its previous
snapshot, is reported as unreachable with a reason, and the sync still reaches a
terminal state. A machine that has never been reached successfully is excluded
from totals but still counts in the coverage denominator, so the page shows
`N/M` rather than silently narrowing what `All` means.

Each upgraded exporter actively queries the Claude and Codex quota for that machine's current signed-in account. Codex live quota requires the app-server `account/rateLimits/read` response to include an `accountId` matching the current account; a response without that identity is a refresh failure, even if it contains rate limits. The two providers fail independently. A failed provider keeps its prior reading and its original observation time, while the sync result and the Quota section identify the affected machine with a static, credential-free reason; a provider with no prior reading still reports the failure. When a generation still supplies `account_id` but has no newly parsed quota, the existing account memory supplies that active account's last successful reading and keeps the row `in_use`, without adding a memory field. A generation from an older exporter can contribute its existing usage and quota fields but does not count as confirmation that active quota refresh ran. Historical signed-out accounts remain historical and are never logged in or refreshed by this path.

Deployment verified on 2026-09-09: local merge `835f6cb` enabled the hourly refresh and restarted the dashboard at port 39002. The remote producer commits are macmini `160b9a8`, macstudio `5ff5d14`, and dgx0023 `e951632`; each changes only `exporter.py` and `quota_refresh.py`. The first launchd round completed at 07:42:03 GMT+8 with successful usage sync from all four machines. The real Overview page showed the four update times and fresh current-account quota values. No Git push or global Codex upgrade was performed.

At the 2026-09-09 deployment, quota refresh was partial by provider: macstudio and dgx0023 had no identifiable Claude current account, and dgx0023's Codex 0.147 response lacked the required `accountId`. Its independently obtained account ID was confirmed equal to macstudio's, whose active Codex reader succeeded, so the dashboard updated that shared account from macstudio while retaining the dgx0023 warning. The launchd job and joined CLI correctly exited 1 for this partial outcome. These conditions did not block machine admission or usage sync. A new Claude login requires the account holder's authentication and is not performed by this refresh service. The old-reader limitation remains owned by tt-web maintenance if an independent dgx0023 quota source becomes necessary; at that point, the shared-account path already supplied its account's fresh quota.

Gateway audit v4 reader compatibility was deployed to MacStudio as local commit `29c5729` on 2026-09-10. After the Hub restart, a real Refresh admitted all three declared machines: MacBook, Macmini, and MacStudio were reachable, successful, and not stale. The LLM Calls API projected MacBook's schema v4 ledger together with the other two schema v3 sources. The final queued refresh round completed in 41.6 seconds; the page returned the Refresh button to its idle state. MacStudio's Claude quota initially remained a separate partial failure because its file-backed access token had expired and the collector could not match a usable credential. Starting Claude from the project directory through the operator's usual interactive SSH shell refreshed the existing file-backed credential without a new login or model turn. A subsequent real Refresh returned `refresh_errors=[]` and `unavailable_reason=null`; the Overview page removed the failure row and showed MacStudio's Claude reading updated in the current round.

Current operational visibility for refresh failure is the LaunchAgent exit status and `state/rollup-daemon.log`, plus per-machine Quota warnings in the dashboard. **TODO (owner: ai-agent-config maintainer):** add a separate push-alert path for scheduled network/provider refresh failures. This rollout does not add or send IM alerts.

Remote calls are non-interactive (`ssh -o BatchMode=yes -o ConnectTimeout=10`)
with a hard timeout on the whole export, so an unreachable host cannot hang the
page. Temporary export directories on the remote are reaped by the next sync.

Usage snapshot schema v2 is intentionally consumer-first. Upgrade and restart
the dashboard machine before upgrading a remote exporter; the new reader accepts
both v1 and v2, while an old reader rejects v2. After the consumer is running the
new code, upgrade each producer and refresh it. A v1 generation remains readable
but its non-project totals use the explicit legacy project-derived fallback; v2
requires `usage_rollup_schema=2` and `daily_usage_rollup` together.

After migration, neither basis is shown on the page any more (owner decision,
2026-09-15). Both still ship in the API: `/api/pivot.totals_provenance` is the
authority for the current query — derived after applying the selected range and
filters, and reporting any visible legacy fallback buckets with their dimensions
— while `/api/sync-status.machines[].generation_totals_basis` (with
`generation_legacy_fallback_bucket_count`) is the broader per-generation reading.
Do not use the machine-level count to describe a narrower Explore window.

Reader-visible copy on all five pages is Simplified Chinese as of 2026-09-15.
`quota_refresh.py`, `hub.py` and `exporter.py` run on **each machine's own
exporter**, so their wording only changes there after that machine is
redeployed; until then a central page shows Chinese framing around an English
reason from the remote.

## Hub 部署副本（macstudio）

### 2026-09-28 明细读取优化部署

经用户明确裁决“部署并验证”，本地 `20b608ed` 的两个 runtime 文件 `tt-web/server.py`、`tt-web/statistics_snapshot.py` 部署为远端 `fa10201554184e314263cc56fb31b876a1972c3f`，两文件 SHA 一致，原 2918 个状态项保留。Hub 新 PID 为 `35667`、instance 为 `9afb66ecca3b4566a6168893bd8ed940`；`health?asset_watch=1` 为 `ok=true`、`stale=false`。部署决定独立审查 PASS；回退限定为这两个文件的补偿 commit，避免 dirty source 拒绝导出。未 push、未重启 Gateway、未调整 MacMini 来源归属。

真实生产 Sessions `7d` 两次导航可读 6.1222／4.4283 秒（HTTP 3.5478／3.6758 秒，773 sessions），展开 macstudio 一个 session 的 24 条用量为 0.3884 秒。LLM 首屏可读 10.1106 秒，filters 3.2600 秒与 calls 3.8298 秒串行；有效 macbook 筛选 3.4364 秒（HTTP 3.3913 秒），请求 `machine=macbook` 与结果归属一致。不存在的 macstudio 选项尝试不是有效筛选证据。最终 sync-status 为 `syncing=false`、四来源尝试 success。剩余全历史 Gateway 读取与前端串行等待未消除，不能据本次部署宣称瞬时；完整身份、样本边界与证据位置见 [ADR 72ab](https://github.com/lindong28/ai-agent-config/blob/3d61487d5386dfe9bb6ba7d26807005bbf0bd7f2/docs/adr/20260928-72ab-ttweb-admitted-detail-reads.md)。

### 既有部署方式与历史记录

自 2026-09-13 起，MacStudio 的 `com.ttweb.hub` 不直接跑 `~/research/ai-agent-config` checkout，而是跑 `~/.local/share/tt-web/deployments/<name>/tt-web/`——一个由仓外流程生成的稀疏 git checkout（历史是自己 rebase 过的），其中 `tt-web/state` 与 `tt-web/web/vendor` 是指回 checkout 同名目录的 symlink，`hub.json` / `machines.json` 是拷贝，plist 的 `ProgramArguments` / `WorkingDirectory` / `PYTHONPATH` 三处都指向该目录。生成它的脚本不在本仓（跟踪见 `../issues/general.md` ISSUE-DEPLOY-20260915-0a4c）。2026-09-15 把 hub 从 `registry-v5-20260913-2d3c50e` 切到 `main-20260915-a15c8c2`（内容 = 本仓 `348cb5e`）时实际走通的步骤如下，下次再部署照此做：

1. **只动源码，不动接线**：`cp -R <旧目录> <新目录>`（BSD `cp -R` 默认保留 symlink），本机 `git archive <commit> tt-web | ssh macstudio 'tar -x -C <新目录>'` 覆盖 `tt-web/`；`state` / `vendor` 不在 archive 里，symlink 原样保留。之后 `find <新目录>/tt-web -name __pycache__ -exec rm -rf {} +`。
2. **HEAD 必须跟着动**：`exporter.exporter_version()` 用该目录的 `git rev-parse HEAD` 与 `git status -- <authority paths>` 当代码权威，只覆盖文件会让 macstudio 自导出报 `exporter runtime authority differs from HEAD`。做法：本机 `git bundle create x.bundle <该仓已有的祖先>..main`（range 尾端必须是 ref 名，裸 SHA 会得到 "empty bundle"）、传过去 `git fetch x.bundle main:refs/heads/deploy-<sha>`，然后 `git reset --soft <sha> && git checkout <sha> -- tt-web claude/statusline-usage.py`，并把 `<sha>` 写进目录根的 `source-commit`。验证：`cd <新目录>/tt-web && ./tt-web export --version` 打印该 sha、exit 0。
3. **切 plist**：`sed "s#<旧目录>#<新目录>#g"` 生成新 plist 并 `plutil -lint`；`launchctl bootout gui/$UID/com.ttweb.hub`，拷贝新 plist（mode 600），`launchctl bootstrap gui/$UID <plist>`。**bootstrap 紧跟 bootout 会报 `Bootstrap failed: 5: Input/output error`**——等 2 秒再 `launchctl enable` + 重跑 bootstrap 即成功（2026-09-15 实测），期间 hub 停机约半分钟。旧目录与旧 plist（拷贝为新目录的 `previous-hub.plist`）保留作回滚。
4. **验证**：`/api/health` 200；`/web/vendor/chart.umd.min.js` 200、`--path-as-is /web/../server.py` 404；`/api/overview?force=1` 后轮询 `/api/sync-status` 到 terminal，macstudio 卡片须为 `up to date`（自导出经过第 2 步的权威校验）。

`hub.py install_web()` 对这份部署无能为力：它以所在 checkout 为 `ROOT`，并且会因 plist 属于"另一个 checkout"而拒绝接管——所以上面第 3 步是手工的。

2026-09-15 第二次照此流程把 hub 从 `main-20260915-dbe1edc` 切到 `main-20260915-a0b53db`（内容 = 本仓 `a0b53db`），四步全部走通，另记两条上次没写下的：

- **远程命令必须走 `zsh -ic` / `zsh -is`**，否则 macstudio 的非交互 shell 里 `git-crypt` 与 `SSH_AUTH_SOCK` 都不存在，`git status` 会以 `clean filter 'git-crypt' failed` 中止。上一版此处写的"该 checkout 的 git 已不可用"是这个环境差异的表象——实测交互 shell 下 `git-crypt` 在 `/opt/homebrew/bin/git-crypt`、agent socket 也在。判据与修法见 `~/.claude/references/remote-command-execution.md`。
- **第 3 步的 2 秒等待照做就不会踩那个 I/O error**：`bootout` → `cp` plist → `sleep 2` → `enable` → `bootstrap`，本次 bootstrap 一次成功、无需重试。
- `~/research/ai-agent-config` 这次没能跟着更新：`codex/config.toml` 有另一个写入者的未提交改动挡住 `merge --ff-only`，按并发隔离协议未 stash。hub 不受影响（它跑部署副本），但该 checkout 落后于 main，**其他机器经 SSH 拉取 macstudio 时用的是它**。

2026-09-16 第三次照此流程把 hub 从 `main-20260915-a0b53db` 切到 `main-20260916-00225d3`（内容 = 本仓 `00225d3`，页面文案中文化）。四步走通，两条新的：

- **`git` 在 macstudio 上已经不能用了**——`/usr/bin/git` 是 Xcode shim，该机未接受新版许可，任何 git 调用 exit 69。第 2 步改用 `/Library/Developer/CommandLineTools/usr/bin/git` 显式路径（实测 2.50.1、无许可门）走完 bundle / fetch / reset / checkout，HEAD 与 `source-commit` 都正确。**但第 2 步文档要求的 `./tt-web export --version` 验证做不了**：exporter 调的是 `git` 这个名字，解析到坏的那个。改用 CLT git 在同一目录取 `rev-parse HEAD` 作为等价读数（得到目标 sha），并单独核了授权路径干净。完整自查与修复（要 sudo，本人在该机终端跑）见仓根 `docs/references/known-risks.md` 第 4 条。
- **第 3 步拆成两次 ssh 调用**（调用一：备份旧 plist 为 `previous-hub.plist` → sed 生成 → `plutil -lint` → `bootout` → 拷贝新 plist + `chmod 600`；调用二：`enable` → `bootstrap`），两次调用之间的 ssh 建连天然满足文档那 2 秒，`bootstrap` 一次成功、未遇 I/O error。
- 部署后实测：`/api/health` 200、`/web/vendor/chart.umd.min.js` 200、`--path-as-is /web/../server.py` 404、页面 `<title>` 为 `tt-web 总览`；一轮 `force=1` 同步后覆盖由 **3/4 升到 4/4**（tencent-webserver-china 首次纳入），macbook / macmini / tencent 三台 `最新`，macstudio 卡片显示上面那条 Xcode 许可错误的原文。

**同轮其余三台**（目标同为 `00225d3`）：macmini 走 bundle + `merge --ff-only`，HEAD 到位、`export --version` exit 0；tencent-webserver-china 走 `git archive` 覆盖 + 部署 commit `3d08069`，`export --version` exit 0；macbook 就是本 checkout 自身，`export --version` 直接 exit 0。

**macstudio 自己那份 exporter checkout 是第五个落点，别漏**（`~/.local/bin/tt-web -> ~/research/ai-agent-config/tt-web/tt-web`——hub 拉取 macstudio 时跑的是**它**，不是部署副本）。2026-09-16 把它从 `54daa41` 推到 `6f74fa2` 后，四台才全部回到「最新」。这一步撞了两个坑：

- **不走 `zsh -ic` 的 `git merge` 会把工作树留在半更新状态**。上面那条只说了"必须走 `zsh -ic`"，没说违反的后果：`merge --ff-only` 在 git-crypt smudge filter 上中止时，**HEAD 不动、工作树却已被写了一批文件**，并留下若干未跟踪文件（它们在目标 commit 里存在）。于是重试时报的是另一件事——`error: The following untracked working tree files would be overwritten by merge`——把人指向"清理未跟踪文件"，而真正的根因是上一次用错了 shell。**恢复**：`git checkout -- .` 收回已改文件，再删掉那些"在目标 tree 里存在"的未跟踪文件（只删这些，别 `clean -fd`——本机 `claude/downloads/` 是合并前就在的，不能删）：

    ```sh
    git ls-files --others --exclude-standard | while read -r f; do
      git cat-file -e <目标ref>:"$f" 2>/dev/null && rm -f "$f"
    done
    ```

    干跑版把 `&& rm -f` 换成 `&& echo DEL ||  echo KEEP` 先看一眼分布再动手。

- **`codex/config.toml` 的未提交改动可能与待合入的 commit 改在同一行**（本次两边都是第 46 行，由 `37d417e` 带入）。别直接 stash/pop——pop 必冲突。先按行比哈希：两边取值相同（本次即如此，另一个写入者手改的正是上游同一个改动）时 `git checkout --` 丢弃本地改动是**无损**的，合并后该行取值不变；**不同**时才是真取舍，按并发隔离协议交用户。动手前先 `cp` 一份到 `~/.local/share/tt-web/backups/`（mode 600），该文件按仓库政策不得回显取值，比较一律走 `shasum`。

- 修完后 macstudio 的 `export --version` 打印 commit、exit 0，`force=1` 一轮后四台卡片全部「最新」，覆盖 4/4。

2026-09-19 第四次照此流程把 hub 从 `main-20260916-00225d3` 切到 `main-20260919-5f65f8e`（内容 = 本仓 `5f65f8e`）。四步走通，三条新的：

- **第 2 步不必再走 bundle**：macstudio 自己的 `~/research/ai-agent-config` 已经 `git pull` 到目标 commit 时，在部署目录里直接 `git -C <新目录> fetch --no-tags ~/research/ai-agent-config main` 即可，省掉本机 `git bundle` + 传输，后面的 `reset --soft` / `checkout <sha> -- tt-web claude/statusline-usage.py` / 写 `source-commit` 不变。本轮 `git` 也不再需要 CLT 显式路径——macstudio 上 `git --version` 已是 2.54.0 (Apple Git-157)、无许可门，`known-risks.md` 第 4 条那个 exit 69 本机已不复现。
- **远程执行只有 `zsh -ic '<命令>'` 可靠，`ssh macstudio "zsh -is" <<'EOF'` 不行**：后者会进交互式 shell、把 heredoc 当终端输入吞掉，只回显提示符、脚本一行不跑且 exit 0——看起来像"跑了但没输出"。本轮的做法是把脚本 `scp` 到 `/tmp/`，再 `ssh macstudio "zsh -ic 'zsh /tmp/<script>.sh'"`。
- **`git pull` + `/api/restart` 不是部署**，这是本轮一开始踩的：hub 跑的是部署副本，`/api/restart` 的 `os.execv` 重新执行的也是**该副本**的 `server.py`，所以 `instance_id` 会变（确实重启了）、代码却一点没变。判据是 `/api/health` 的 `signature`：它是运行进程 import 时对**自己 `ROOT`** 算的摘要，拿它和本仓 checkout 里 `python3 -c 'import server; print(server._source_signature())'` 的读数比，不等就说明跑的不是这份源码。**`stale` 字段在这里帮不上忙**——它比的是运行进程自己的 `ROOT` 与自己的 `BOOT_SIGNATURE`，部署副本冻结着、两者恒等，所以它诚实地报 `false`。另外 `/api/restart` 的 re-exec 有 0.4s 定时器，紧跟着查 `instance_id` 会读到旧进程、误判成"没重启"，要轮询到它变为止。

### Exporter 机器更新

每台被 hub 拉取的机器都用自己 `~/.local/bin/tt-web -> ~/research/ai-agent-config/tt-web/tt-web` 导出，exporter 先核 `git status -- <authority paths>` 干净（2026-09-16 起不再核 Gateway 源码 pin）。2026-09-15 三台的更新方式（各机 git 状况不同，别套同一条）：

| 机器 | checkout 状况 | 更新方式 |
|---|---|---|
| macmini | 正常 clone、历史与本地 main 同源 | `git bundle create x.bundle <它的HEAD>..main` → 该机 `git fetch x.bundle main:refs/heads/bundle-main && git merge --ff-only bundle-main` |
| tencent-webserver-china | 单个孤儿 commit（9/10 部署时如此）、无 remote、无 git-crypt | `git archive <sha> tt-web claude/statusline-usage.py` 解压覆盖，删 `__pycache__`，`git add -A tt-web claude/statusline-usage.py && git commit`（部署 commit，只在该机存在；`export --version` 报的是这个 sha） |
| macstudio | `~/research/ai-agent-config` 的 git 因缺 git-crypt 已不可用，且 hub 自导出走的是部署副本 | 只更新部署副本（上节） |

更新后各机 `cd tt-web && ./tt-web export --version` 须打印 sha、exit 0；gateway 账本 schema 升级时"先升 reader 再升 writer"的顺序见 `docs/references/known-risks.md` 第 3 条末段（pin 那一步已随 2026-09-16 的移除作废）。

## Codex 计量修复部署（2026-09-21）

修复源码 `f0903dbd68973b2f7215248f21ac3b542214ce03` 已整合至 MacBook 本地 main；macmini/macstudio 使用独立 release `~/.local/share/tt-web/deployments/codex-repair-20260921-f0903db`。release 有独立 Git 元数据，`tt-web/state` 和 `web/vendor` 指向各机原 checkout；远端共享 checkout 的 HEAD/WIP 未改。两机 `~/.local/bin/tt-web` 与 `com.ttweb.rollup` plist 均指向 release，MacStudio 的 `com.ttweb.hub` 同样切换；只切 CLI 而不切定时任务，会由旧 parser 再次覆盖修正统计。

三机 SQLite 备份在 `~/.local/share/tt-web/backups/codex-cost-20260921/`，含 `usage_archive.sqlite3`、`rollup.db`、`project_identity.db`；远端另存原 CLI 目标和原 plist。回退代码时需同时处理 CLI、定时采集与 hub 入口；不要仅恢复旧数据库后继续运行错误 parser。后续 installer 默认仍以其所在 checkout 为源，执行前确认不会把运行入口指回未包含此修复的旧源码。

该轮 admitted generation 的三个 Mac `exporter_commit` 均为该提交，页面覆盖 4/4、source errors 为 0；Tencent 无 Codex 日志，保留原版本。9/20、Codex、ai-agent-config 启动目录口径的 API 金额由 $2821.133542 降至 $432.581084，浏览器显示 $432.5811；会话 API 包含全部 41 个核查 session。归档纠错未删除既有事件键，authority 自 9/10 起的日桶已重算；之前的 legacy 日桶未强行缩减。归属调查与后续裁决见根 `docs/issues/general.md` 的 `ISSUE-GENERAL-20260921-codex-cost`。

同日后续部署 `098ff3193522b9f4a7ae3058383439c39f1900cd`：用户选择保留启动目录口径，总览、透视和会话列表统一使用“会话目录”，注明跨项目工作不拆分费用。MacBook 本地 main 已整合；macmini/macstudio 复用上述独立 release 路径，HEAD 已更新，MacStudio hub 已重启。

此次同时修复 Mac mini 关闭后的 WAL 元数据库只读查询失败：仅在私有副本上允许 SQLite 初始化旁文件，失败时保留归档模型而不按默认模型覆写。重采后 9/20 配置仓目录的 1,568 个 Codex events 恢复为 `gpt-6-astra`；12:23:27 UTC 完成的中央同步四机均成功且 source errors 为 0，原透视 API 恢复 $432.581084、浏览器显示 $432.5811。该费用仍为模型费率估算。Mac mini 既有的 41 个目录身份 blocker 仍保留，未自动变更其归属；9/10 前 legacy 汇总边界同样不变。

## MacMini 41 个来源路径阻断（2026-09-28）

本次只读调查核对 MacMini 部署 `e3074243` 与 Hub `287a6aa0`；MacMini 的 `aggregators.py`、`usage_archive.py`、`rollup.py` 与本地 `00af0e34` 逐文件 SHA 一致。读数取于 2026-09-28 05:33–05:43 UTC，未恢复、改远端文件或重启。**41 的单位是不同 `source_path`，不是日志文件或会话。** 这批路径的 60,450 条去重事件在 MacMini 归档与 Hub 已 admitted 快照均保留，涉及 929 个 `(agent_id, session_id)`、2 种工具、9 个模型；41 条路径全部有记录。事件身份、时间、模型、来源路径、四类 token 与 message_count 的排序摘要两端一致，不含会话正文，也不证明归档之外没有未采集调用。核对 generation 为 `754e3adb6983e211964a6ecbdd9f0199e1cda31e19d4017eb7508a1eaada2657`，观察时间 `2026-09-28T05:35:12.504629+00:00`。

影响是项目／“会话目录”归属聚合：事件进入 `unattributed_entries`，不进入本轮 `daily_rollup` 项目桶，仍保留在项目无关用量处理与 `statistics_usage`。一个实际消费端对照中，Sessions 的 `range=all&machine=macmini&project=/Users/lindong/research/ai-radar-worktrees/t4-content-align` 返回 347 个会话，同路径按项目 pivot 返回 `rows=[]`、`totals_provenance.basis=no_data`。这是一个路径的 API 对照，不是 41 条路径的逐一浏览器验收。Hub 同时报告 `scan_complete=true`、`source_error_count=0`，不能将归属阻断解释成当前扫描失败或整机导出失败。

快照中这批事件的挂牌价派生估算为 USD 7,392.86661271，不是账单、损失金额或已证明的 Overview 总额缺口。原始归档 `cost_usd` 全为 NULL，表示尚未加价，不能当零；项目桶与项目无关桶不是同口径，不能直接相减算缺失金额。

根因是旧项目身份无法唯一对齐，且 active blocker 持久化：`_identify_project()` 先拒绝已有 active 路径，不会因目录恢复或 Refresh 自动清除。58 个旧聚合项目中 40 个已认领，18 个未认领；41 条路径均无 `project_identity`、`pin_candidate` 全为 NULL，路径／记录候选与现有项目直接匹配数为 0。持久 reason 为 10 个 `source_unavailable`、31 个 `unreconciled_remote`；今天目录状态为 39 个不存在、2 个存在，不能混同首次原因。存在的两个目录虽可读 Git，也没有唯一旧项目匹配；`t4-content-align` 的 origin 是本地 `t3-eval-regression` 路径。旧聚合缺少 source-path lineage，不能靠目录名猜归属。

事件跨度为 `2026-08-11T20:12:05.896Z` 至 `2026-09-13T03:44:06.214Z`，本次未读到这些路径之后新增的归档用量；首次 blocker 记录在 8 月 12 日至 9 月 9 日，`last_seen` 更新到今天不表示今天新丢了 41 项。相关缓存记录过 1,027 个日志文件路径，969 个仍在、58 个不在；文件缺失不否定上述已归档事件，完整文本恢复未检查，未追查谁删除了工作目录。

**现有 `tt-web rollup recover --path … --pin-existing …` 不能直接修复这批路径。** 只读运行真实 `_derive_pin_candidate` 得到 39 条需恢复路径／Git、2 条没有恰好一个旧项目匹配；41 条持久 pin 均为 NULL，恢复目录本身仍不满足既有命令后续条件。不要删除数据库或 blocker，也不要将该命令当成现成修复。

恢复归 ai-agent-config 维护者另行实施，并先取得相应数据处理授权：建立有证据的 `source_path → intended project` 映射；补支持无唯一历史 pin、但有逐事件归档的恢复能力；明确权威日 `2026-09-10`（+08:00）以前历史的重建／保留口径。60,436 条事件早于权威日，14 条在当日及之后，不能将新映射直接与缺 lineage 的旧项目桶相加。之后重算并导出同步，核对事件／token 保留、项目结果可见、blocker 处置真实、项目无关统计未重复增加。这不是 Hub 可用性的前置条件，也无需重启 Gateway；本轮未解除任何 blocker。

报告位于 `/Users/lindong/.local/state/ttweb-freshness-20260928/macmini-blockers-followup.md`，关键事实已在本节保留。上述调查时尚未修改的旧 CLI 误导文案已随 `72491193` 修复，闭环记录见 [已关闭的 ISSUE-TTWEB-20260928-72ab](../issues/archive/closed.md)。

## Account Memory

`state/account_memory.json` stores the last admitted observation of each account that tt-web has seen while signed in. The web server manages this file; it persists across server restarts and the uninstall paths above, which intentionally keep `state/`. It is durable source state, not a regenerable cache: manually deleting it permanently loses remembered accounts that are no longer signed in on any machine, because no current snapshot can reproduce those observations.

## Rollup Details

`state/rollup.db` is a local SQLite WAL database with two daily tables. `daily_rollup` is keyed by `Asia/Shanghai` date, agent, project, and model for project-aware views. Schema v2 adds `daily_usage_rollup`, keyed by date, agent, and model. Views that neither display nor filter by project use its row for each available key, including usage whose project identity is blocked; only a missing usage key falls back to the corresponding aggregation of legacy project rows. Both tables are written in one transaction. The bucket boundary is fixed rather than following the host's timezone, so that snapshots from machines in different timezones can be summed by date; the database records which boundary its rows were built with and refuses to be read if that marker is absent or disagrees. Absolute timestamps in the UI still render in the viewer's local timezone — only the aggregation boundary is fixed. `tt-web rollup` normally recomputes an inclusive 28-day window. Missing keys and updates whose token/message/entry counters would shrink are preserved at their last trusted row; sibling keys continue updating. Within the 28-day recompute window, rows are recomputed from currently readable source. Logged costs remain the logged values; entries without exact cost use the pricing data currently available to tt-web. Rows with missing source or a protected-counter decrease keep their last trusted cost, and existing rows outside the window remain frozen.

`tt-web rollup --check [--json]` takes a coordinated, read-only snapshot and compares it with a read-only source scan. The human form is for diagnosis; `--json` exposes the same fields to scripts. A successful command exit does not mean the result is clean, so automation must parse `status`:

| Reading | Operational meaning |
| --- | --- |
| `status: safe` | Database and source scan were complete, with no protected-field shrink in either rollup table and no project-identity blocker. |
| `status: attention` | The complete comparison found one or more project/usage skip buckets or blocked source paths. Investigate before treating the rollup as healthy. |
| `status: indeterminate` | A complete, consistent comparison was unavailable because the database/snapshot was not ready or a source scan failed. This is not a clean result. |
| `verdict` | Bucket shrink result only: `safe` for compared buckets, `attention` when a protected field would decrease, or `unknown` only when bucket comparison could not begin at all. It does not replace overall `status`. |
| `db_state` | Whether the database snapshot was ready for comparison. Any value other than `ready` makes the result indeterminate; when present, `diagnostic_errors` describes the database or lock-snapshot failure. |
| `scan_complete` / `source_errors` | Completeness of the read-only source scan and its per-source failures. `scan_complete: false` or any `source_errors` forces overall `status: indeterminate`, even if `verdict` is `safe` or `attention` for the subset that was compared. |
| `diagnostic_errors` | Database or coordinated-snapshot failures that prevented comparison. These accompany an indeterminate result and `verdict: unknown`. |
| `blocked_sources` | The source-path-deduplicated union of persisted and currently discovered identity blockers. On a complete scan, any item drives overall `status: attention`. |
| `persisted_blockers` | Active blocker records that were already stored before this check. |
| `current_blockers` | Blockers found by this read-only scan. `--check` does not persist them, so a current-only blocker can be absent when a subsequent `tt-web rollup blockers` lists stored blockers. |
| `orphan_rows` | Stored keys absent from current source, evaluated across all persisted rows. Rollup preserves them. After expected retention, a non-zero count needs no rollup-database repair; if disappearance was unexpected, investigate or restore the source. |
| `would_skip` | Keys whose protected token/message/entry fields are lower than stored values. Rollup would keep the whole old row, including its cost. This set is limited to the inclusive recompute window plus one-time backfills for source dates the database has never seen. |
| `would_write` | New or changed keys that rollup would write, including cost-only changes and excluding exact no-ops. It has the same window/backfill scope as `would_skip`; frozen existing rows outside that scope appear in neither set. |
| `usage_orphan_rows` / `usage_would_skip` / `usage_would_write` | The corresponding readings for the project-independent `(date, agent, model)` table. A shrink here drives `status` and `verdict` to `attention`. For a migrated v1 database, comparison and one-time backfill never cross the persisted `usage_rollup_authoritative_from` date. |
| `usage_rollup_schema` / `usage_totals_basis` | `usage_rollup_schema=1` means the stored totals are legacy project-derived. Schema `2` uses the usage table and requires an authority floor. The self-describing `usage_totals_basis` says `legacy_project_derived`, `project_independent_usage`, `usage_with_legacy_project_fallback`, or `unknown`; it is validated by deriving it from the schema and fallback count in the same checker result. |
| `legacy_fallback_bucket_count` | Number of distinct `(date, agent, model)` buckets still supplied by legacy project aggregation; `null` when the database could not be inspected. |
| `legacy_fallback_bucket_dimensions` | Unit carried beside the generation/export fallback count; schema v2 requires `[date, agent, model]`. |
| `db_span_scope` / `db_span` / `window` | `project_rollup` identifies the table whose persisted date span and row count are reported; `window` is the inclusive recompute window. |

`usage_rollup_authoritative_from` is an internal, required `rollup_meta` boundary written atomically with the v2 marker. It records the first date whose current raw logs were selected as authoritative; on a v1 migration, earlier project rows remain legacy fallback, and later rollups do not expand the migration into them. Missing or invalid floor state is indeterminate rather than an unbounded default.

For `attention`, inspect the `would_skip` old/new fields and all three blocker arrays. Run `tt-web rollup blockers` for persistent status and use only an explicitly offered recovery command after the current source path or remote again directly matches the recorded candidate. For `indeterminate`, resolve the reported database, lock snapshot, permission, or source-scan error and rerun the checker. On a genuinely fresh installation with no initialized database, one normal `tt-web rollup` creates it; on an existing installation, never use deletion or replacement as a repair shortcut. Do not delete the database, delete rows, or overwrite preserved history while investigating.

`state/rollup.db.lock` (generally `<db_path>.lock`) is a persistent coordination inode and part of the service state. Every cleanup script, tmp-reaper, backup rotation, and manual cleanup touching `state/` must exclude `*.lock`: never unlink, replace, rotate, or recreate this file. `flock` is attached to the inode, so a same-name replacement can let a writer holding the old inode and a writer holding the new inode both enter what appear to be protected sections. The existing uninstall path intentionally keeps `state/`; future cleanup tooling must preserve this constraint.

### If the lock file is already missing

Do not run a normal rollup immediately: the web service, rollup daemon, or a manual rollup may still hold the unlinked inode, and creating the pathname again would split coordination into two lock domains. Stop the web service with `tt-web stop`, unload the daemon with `./tt-web/uninstall.sh rollup-daemon`, and confirm that `pgrep -fl '[t]t-web rollup'` prints no manual rollup process. Only after all possible holders are gone, run one normal `tt-web rollup` to initialize a new lock file, then run `tt-web rollup --check` before restarting the web service with `tt-web start` and reinstalling the daemon with `./tt-web/install.sh rollup-daemon`. Re-enable only the service layers that were active before the incident.

The LaunchAgent plist uses:

- Label: `com.ttweb.rollup`
- Program: `tt-web refresh`
- RunAtLoad: `true`
- StartInterval: `3600`
- Log: `tt-web/state/rollup-daemon.log`

In standalone mode, starting the dashboard captures the machine configuration and begins serving requests without itself starting a rollup or cross-machine sync. Hub mode starts its background scheduler and can begin a startup synchronization round, as observed in the 2026-09-28 deployment above. Overview and Explore API requests may start the dashboard-owned sync when admitted data is due, while `tt-web refresh` uses the force path and waits for that round or the round already in progress. The LaunchAgent calls this refresh entry point; when the dashboard is absent, it preserves the former local-history update but advertises the partial result through its non-zero exit and log.
