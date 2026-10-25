# Service diagnostics

Installation, update and existing-installation transition are documented in [usage and operations](../public-usage.md). Run lifecycle commands from the owning checkout. `./agent-monitor status` and `/api/health` identify the running instance; source preflight alone does not prove service health. Inspect `state/server.log` for startup failures. Never remove persistent locks or replace an existing state symlink to force startup.

Managed deployment checks the Hub's `/api/health?asset_watch=1` and the connector's loopback `/health`. Their `source_root` and `deployment_signature` identify the checkout and source/selected configuration captured at process startup; changing a plist alone is not proof of activation. The installer checks this identity with a bounded wait after replacement and retains an already matching process. This proves local process response, not browser access, collected-data admission, provider availability or completion of account actions. Hub startup diagnostics remain in `state/hub.log`; connector connection/failure observations remain at its configured status-file path. Deployment failures are synchronous nonzero installer results; no new background alert service is installed.

## Runtime performance diagnostics

`GET /api/diagnostics` reports the serving process's active operations, recent completions, cumulative counts, coverage and source freshness. Read it at the actual address printed by `./agent-monitor status`; it does not acquire the query-view or sync lock. The observer also writes `state/diagnostics/current.json` and bounded `events.jsonl` files. Inspect these locally when HTTP is unresponsive, and sanitize paths/account data before sharing any logs.

```bash
python3 -m json.tool state/diagnostics/current.json
```

Keep `instance_id`, `sampled_at` and observer/persistence status with a reading. Restart resets counters and changes the instance. Recent samples are bounded, not a latency distribution or complete attempt ledger. `coverage` reports omitted active details, evicted completed samples and dropped events; event emission is not an fsync guarantee. A stale `current.json` does not establish that the observer is still running.

Read `active.phase` to distinguish JSON encoding, response-body writing and publication. HTTP durations include encoding and body writing, but not header parsing. Nested operations overlap; summing their durations double-counts elapsed time. A generic running phase does not prove lock contention. The reported slow/long-running thresholds are observation thresholds, not cancellation deadlines.

For synchronization progress, read `/api/sync-status` and the affected source's collector/export logs. `observed_age_seconds` concerns collection observation; `generated_age_seconds` concerns publication. `age_intervals` is observation age divided by configured interval, not a count of missed rounds. Missing metadata stays unknown. A cleared incident means a later sample no longer saw that condition, not that every operation is healthy.

Statistics refresh, network refresh and restart use POST with same-origin checks and `X-Agent-Monitor: action`. Legacy GET/HEAD force parameters do not trigger these explicit actions. Normal quota/collection schedules are separate. Let active account operations finish before restart; do not replay an uncertain send. See [account operations](../public-usage.md#optional-quota-and-account-actions).

## Isolated backend investigation

From a development checkout with its dependencies installed, run:

```bash
.venv/bin/python scripts/benchmark_backend.py --rows 100 1000 --padding-mib 0 4 --repeat 3
```

The script creates synthetic snapshots in its own temporary HOME/state and calls real admission, QueryView and aggregate code. It does not start HTTP, SSH or providers. Cases run serially because disk/cache contention would contaminate comparisons. JSON stdout contains raw samples; save it outside temporary directories if retaining evidence. Exit zero means measurement completed, not an SLA pass.

Rows and padding vary aggregate cardinality and snapshot bytes separately. Compare application-cold admission, hot admission, preparation, prepared reads and generation changes; application-cold does not flush the OS cache. Padding does not model real session retention or network traffic. Use the [cost map](../public-architecture.md#backend-performance-checks) to select the affected consumer and its existing regressions. Real page/API latency requires measurements at that consumer, separately from these synthetic results.
