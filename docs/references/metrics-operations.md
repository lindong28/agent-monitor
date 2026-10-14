# Maintenance and diagnostic metric appendix

[Developer] Part of the [metric specification](metrics.md); its source/version and verification boundaries apply here. These are operational counters, not additional usage or cost totals. Definitions cover human CLI output and its JSON/evidence fields. Update the owning producer and CLI together; a command exiting zero is not evidence that every reported status is healthy.

## Rollup execution and check

Define `P` as stored project keys `(date,agent,project,model)`, `U` as stored usage keys `(date,agent,model)`, `S` as the corresponding reconstructed source keys, `W` as the inclusive configured recompute dates, and `A` as the eligible authoritative replay scope. A project replay also requires its project identity to be in the writer's authority set. For usage replay, the effective floor is at least the persisted `usage_rollup_authoritative_from` date. The checker must classify with the writer's same scope, not infer deletion merely from source absence.

Source/update entry: `rollup._run_locked`, `_write_bucket_table`, `_compare_bucket_sets`, `_check_rollup` and `agent-monitor run_rollup_check`. The definitions are project contracts; set difference/intersection gives independent expected cardinalities.

| Field / human readout | Unit and definition | Argument / boundary |
| --- | --- | --- |
| `window_days`, `days_recomputed`, “over N days” | Days in W, default 28 | Configured planned window, not distinct changed dates or all touched dates; authority/backfill can extend beyond W |
| `dates_backfilled`, `usage_dates_backfilled` | Distinct scoped dates outside W, per table | Set cardinality; can include replayed existing dates, not only first-ever inserts |
| `buckets_written`, `usage_buckets_written` | Successful upsert operations in the transaction | Includes identical-value upserts; not equal to checker's changed-key count |
| `buckets_skipped`, `usage_buckets_skipped`, `skipped_keys` | Keys skipped because protected counters decrease outside authoritative rebuild | Old whole row, including cost, remains. Authority replay can replace rows rather than use shrink protection. |
| `sources_blocked` in run | Blocked source-path count | LoadedEntries must already deduplicate paths; not missing token count |
| `last_rollup_ts` | Timestamp taken at round start | Not scan end or last database write |
| `db_span.rows/start/end`, `db_span_scope` | Count/min/max of P; scope=`project_rollup` | Primary-key uniqueness makes key count equal project table row count; excludes usage table/event rows |
| `window.start/end/days` | Inclusive dates and number of dates | start=today−(days−1), end=today |
| `orphan_rows.count/items` | Keys P−S over persisted project rows | Absence alone does not decide retention. Each `items[].action` is `remove` in applicable authoritative rebuild scope, otherwise `preserve`. |
| `usage_orphan_rows.count/items` | Keys U−S_usage | Same classification, respecting the usage authority floor |
| CLI preserved/rebuild-removed orphan counts | Count of orphan items by action | Disjoint action partitions sum to orphan count; does not claim the dry run already deleted anything |
| `would_skip.count/items`, `usage_would_skip` | Compared keys with protected decreases which writer would preserve | Scope is the actual writer comparison/rebuild rules; authorized replay is not a skip |
| `would_write.count/items`, `usage_would_write` | New or changed keys in writer scope, excluding exact no-ops | Logical change count; cost-only differences count; not actual SQL statement count |
| `items[].old/new` token fields | Tokens per stored/reconstructed bucket | Four normalized categories, not raw inclusive input; no new normalization in check output |
| `old/new.cost_usd` | Known USD subtotal per bucket | Must be read with cost coverage; not a guaranteed complete cost |
| `old/new.cost_known_count`, `entry_count`, `message_count` | Priced observations / observations / weighted usage events | Non-null cost including zero counts as priced; entry and message counts are distinct |
| `decreased_fields` | Names of protected fields that declined | Categorical evidence; not a count of lost provider requests |
| `source_errors` count | Error-record count | One source can have several errors; not distinct bad files |
| `blocked_sources`, `sources_blocked` in check | Source-path-deduplicated union of persisted/current blockers; its size | Do not add the lengths of the two input arrays; they can overlap |
| `persisted_blockers`, `current_blockers` | Separate blocker record arrays | Current-only blockers need not yet exist in persistent blocker listing |
| `legacy_fallback_bucket_count` | Number of `(date,agent,model)` keys supplied by project fallback | Schema1: size of projection π(P); schema2: size of π(P)−U. Null if unreadable. |
| `usage_rollup_schema`, `usage_totals_basis` | Version and authority enum | v2 alone does not prove all historical dates have independent usage coverage |
| `status`, `verdict`, `scan_complete`, `db_state` | Overall state / shrink-only verdict / source scan completeness / DB readiness | Source error or incomplete scan makes overall result indeterminate; verdict can describe the compared subset. Missing comparison's zero placeholder counts do not establish “no issues”. |

## Timezone adoption

Source/update entry: `rollup.adopt_bucket_timezone`, `agent-monitor run_rollup_adopt_timezone`. Let `E` be reconstructable entries at/before `last_rollup_ts` and after each agent's raw-log start, `K=bucket(E)`, `D` the database keys, `G` newer entries and `Gk=bucket(G)`. This maintenance evidence verifies overlap, not history beyond source retention.

| Field | Unit / exact population | Why / limitation |
| --- | --- | --- |
| `bucket_keys.expected/matched/missing` | Keys: size(K), size(K∩D), size(K−D) | Missing keys prevent adoption; zero in a successful result reflects that gate |
| `comparison_window.source_entries_in_scope` | Entries: size(E) | Timestamp and agent raw-start filters are applied before counting |
| `source_bucket_keys_in_scope` | Keys: size(K) | Same expected key count, not new independent evidence |
| `post_rollup_source_growth.source_entries/source_bucket_keys` | Entries/keys: size(G), size(Gk) | Distinguishes newer source activity from the old rollup comparison |
| `bucket_keys_missing_from_database` | Keys: size(Gk−D) | Does not establish that old committed use was lost |
| `protected_counter_verification.equal_bucket_keys.expected/actual` | Keys: size(K) / keys whose six protected counters all match | Each key contributes at most one match, not six matches |
| `non_equal...bucket_keys` | size(K) minus equal keys | Nonzero is not verified equality and needs the existing explicit disposition |
| `uncovered_existing_bucket_keys` | Keys: size(D−K) | Outside current overlap; not validated by matching K |
| `pre_log_history.start_date/end_by_agent` | Earliest stored date / day before each raw-log start | Earlier-history interpretation is an explicitly authorized assumption, not observed proof |
| `comparison_window.end_inclusive`, `adopted_at` | UTC timestamp for old round start / current adoption action | Distinct clocks and events |
| `cost_attribution_verification.status` | `not_verified` | Protected-counter equality does not verify `cost_usd` or `cost_known_count` |

## Identity recovery and machine operations

Source/update entry: `rollup_identity.py`, `agent-monitor run_machines_accept/run_machines_retire`, `generation.snapshot_stats`.

| Readout | Unit / definition | Boundary |
| --- | --- | --- |
| `rollup blockers` active count | Persistent identity rows with status active | Source path is identity; excludes discoveries not persisted by a read-only check |
| Blocker first_seen/last_seen | Stored timestamps | Not proof of a current successful source probe |
| Single recovery result | One committed source-identity mapping | Does not backfill all historical project usage |
| `recover-reviewed` assignments validated / identities committed | Unique source-path assignments | Duplicate sources are rejected; several sources can map to one project, so this is not distinct project count |
| `machines retire` active remaining | Configured machine entry count, including self | Not online, admitted or successful machine count; retirement does not delete historical data |
| `machines accept` physical rows / split | Project+usage aggregate row count / per-table counts | Schema1 has only project rows. Excludes statistics event rows and other SQLite tables. |
| `machines accept` data since | Earliest authoritative aggregate date | Not raw/session-detail start |
| Legacy fallback buckets | Distinct fallback date/agent/model keys | Unit must remain beside the count |

## Export and generation metadata

`exporter.export_bundle`, `generation.snapshot_stats` and `validate_export_manifest` own these fields. Let `F=π(P)−U`; authoritative aggregate rows `R` are usage rows plus grouped project rows only for keys in F. `NOT EXISTS` separates the two branches, so fallback does not double count an existing usage key. Schema1 uses project aggregation alone. Manifest equality checks identity/transport consistency, not the completeness or business correctness of the input observations.

| Field | Unit / definition | Boundary |
| --- | --- | --- |
| `row_count` | Schema1 size(P); schema2 size(P)+size(U) | Physical aggregate rows, not size(R), sessions or all SQLite records |
| `project_row_count`, `usage_row_count` | Per-table rows | Sum agrees with physical aggregate count |
| `legacy_fallback_bucket_count/dimensions` | size(F), dimensions=date/agent/model | Schema1 fallback is π(P); missing inspection is not zero |
| `metric_totals.input/output/cache_read/cache_creation` | ΣR of each token category | Normalization remains the upstream contract |
| `metric_totals.total` | Sum of the four category totals | No additional reasoning term |
| `metric_totals.messages` | ΣR message_count, usage events | Not Σentry_count |
| `metric_totals.cost` | ΣR stored known USD subtotals | Identity statistic lacks its own unknown count; must not be reused as a complete spending total |
| `metric_totals_basis` | legacy/project-independent/with-fallback enum | Derived from schema and fallback presence |
| `data_start_date` | Minimum date in R; null when empty | Does not prove prehistory absent |
| `schema_version` | Version 1/2 | Structural identity, not a count |
| `generated_at` | Export timestamp | Does not replace statistics observed_at |
| Digests and identity fields | Transfer/logical/manifest identity | Not business metrics or proof of provider truth |
| `rate_limits.*` | Provider percentages and reset/observation timestamps | Apply the main specification's quota rules; no extra sum |
| `statistics_meta.blocked_source_count/source_error_count` | Blocked source count / error-record count | Former depends on path deduplication; latter may have several records per source |
| `statistics_usage.position` | Zero-based retained row index | Not usage-event count; row tokens/cost/message_count follow the main specification |

## Runtime diagnostics

Source/update entry: `diagnostics.Monitor` and `/api/diagnostics`, `state/diagnostics/current.json`, `events.jsonl`. All cumulative counters are process-instance-local, starting at `started_at`; preserve `instance_id`. Samples can be nested, so they are not independent provider requests and their durations do not sum to wall-clock latency. HTTP and observer-file reads are different snapshots in time.

| Field | Unit and formula | Preconditions / limits |
| --- | --- | --- |
| `counts_since_start.started` | Number of begin calls | Increment under tracker lock |
| `completed` | Number of finish calls | Normal context-manager consumer finishes each token once; duplicate finish is outside that contract |
| `active` | started−completed, operations | Includes operations omitted from detail storage |
| `failed` | Completed operations whose outcome is not success | Can overlap slow; not incident/event queue length |
| `slow` | Completed operations with duration≥slow threshold | Do not add failed and slow as disjoint totals |
| `active[].elapsed_seconds` | max(0, current monotonic−start monotonic), seconds | Live elapsed age, not final duration |
| `recent[].duration_seconds` | finish monotonic−start monotonic, seconds | Frozen completion sample; bounded recent order is not an unbiased latency distribution |
| `coverage.active_details_omitted` | Active count minus retained active detail count | Current omission, changes as work completes |
| `active_details_dropped_total` | Cumulative begins denied a detail slot | Counts historical admission loss, not currently omitted operations |
| `completed_samples_evicted` | Number of completed samples evicted by bounded recent deque | Each full-queue append evicts one prior sample |
| `events_dropped` | Queue-evicted events plus events not successfully emitted by the file writer | Decrement pending count after each successful emit; snapshot-write failure must not count already emitted events. Emit success does not guarantee fsync/crash durability. |
| `detail_limit` | Maximum retained detail/sample count | Capacity, not current count; default 256 |
| `sources[].observed_age_seconds/generated_age_seconds` | max(0, sampled wall time−source timestamp), seconds | Missing/unparseable timestamps are null; future-clock clamp does not prove freshness |
| `sources[].age_intervals` | observed age / configured collection interval | Dimensionless, not a missed/failed-round count; unknown/zero interval gives null |
| `sources[].stale` | observed age≥configured source-stale threshold | Null age is unknown; default threshold six hours |
| `thresholds_seconds.slow_operation/long_running/source_stale/collection_interval` | Seconds; effective configuration | Diagnostic thresholds, not SLA, deadlines or cancellation policy |
| `started_at/sampled_at/last_observer_tick_at/last_written_at/sources_checked_at` | Unix seconds at each named observation/write | Null means not obtained; last_written is last successful snapshot write, not current health |
| `instance_id/pid/active[].id` | Instance identity / OS PID / operation sequence | PIDs can be reused; do not sum process-local counters across restarts without explicit bookkeeping |
| Event `at`, completed duration | Unix timestamp / seconds | Event creation time differs from file emission time |
| Long-running/stale/unknown transitions | Incident state sets, not health percentages | A clear transition says a later sample no longer observed that condition; unknown does not clear known stale |
| `persistence_error` / observer source error | Explicit status | Snapshot failure and event loss are separate; process-local observer cannot prove its own process is alive from a stale file |

The arithmetic relies on [Python set semantics](https://docs.python.org/3/library/stdtypes.html#set-types-set-frozenset), [bounded deque eviction](https://docs.python.org/3/library/collections.html#collections.deque) and [monotonic elapsed differences](https://docs.python.org/3/library/time.html#time.monotonic), not on fixture PASS counts. The baseline audit independently contrasted four event-write failure stages and authority-present/absent replay to expose incorrect drop and orphan descriptions. These finite counterexamples establish those defects; they do not establish production failure rates, fsync durability or exhaustive concurrency correctness.

`status`/health output also carries PID, port, interpreter, running/degraded state and version. These identify the installed process and lifecycle state, not consumption. Refresh reports per-machine statistics/quota outcomes and incomplete rounds; successful request acknowledgement is not all-machine completion. Updating these fields must preserve the [service operations](../operations/services.md) distinction between acceptance, progress, terminal outcome and retained older data.
