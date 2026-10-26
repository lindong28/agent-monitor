# Architecture

agent-monitor is an independent Python service with a static JavaScript dashboard. Bash entrypoints manage a checkout-local Python environment and runtime. No parent repository, LLM Gateway service or private skill is required.

## Usage path

`parsers/claude.py` and `parsers/codex.py` read the operator's agent logs into the shared usage representation in `parsers/__init__.py`. `usage_archive.py` retains normalized observations. `rollup.py` maintains daily aggregates with project/source identity and shrink guards. `token_cost.py` and `pricing_fetcher.py` derive known-cost subtotals from recorded tokens and available prices. Missing prices or incomplete counters stay distinguishable from known zero.

`server.py` exposes usage, session, quota, collection and network routes. `aggregators.py`, `session_cache.py` and `conversation_cache.py` prepare the corresponding views. `web/` renders them. Codex native subagents can be grouped with their main conversation using explicit ancestry; unresolved ancestry is not guessed. Date boundaries use Asia/Shanghai.

## Multiple machines

`machine_config.py` validates named machines, SSH targets, the local role and retired identities. At startup it selects `machines.local.json` if present, otherwise `machines.json`; explicit caller paths remain explicit. `hub.py` uses the same local-over-default convention for Hub configuration. All importers share the selected machine path, including retirement writes. Adding or removing a local configuration file requires restarting processes.

`exporter.py` produces versioned snapshots from a committed, clean source tree. `sync.py` invokes the installed exporter on declared SSH hosts; `generation.py` verifies and admits immutable snapshot generations. `query_view.py` prepares admitted aggregates for repeated API reads and keeps leases while consumers use a generation. Old snapshots remain distinct from newly observed quota readings. Raw provider secrets stay on their owning machines.

Statistics export copies only mutable usage metadata and formats timestamps directly. It validates and encodes every usage row before opening the destination, retaining encoded rows rather than both row dictionaries and a second encoded batch. Snapshot schema, row order, post-write readback and admission checks are unchanged. Export still processes the full retained history; this reduces serialization CPU and temporary memory, not collection frequency or snapshot size.

The optional Hub marker in `state/hub-machine` enables role-specific behavior. macOS LaunchAgents provide periodic local collection and the Hub service. Standalone mode works without a Hub marker and does not contact the maintainer's fleet in the exported public candidate.

Standalone schedules call `rollup`; they do not use the dashboard-dependent `refresh` path. Hub `collect` and requested exports both retain their local rollup work. Reusing a recent export by skipping a collector when observation age is less than interval I would weaken the collection bound: collection at 0, export at ε, skipped collection at I, then a disconnected Hub can leave the next collection until 2I. Safe reuse would need scheduling against the actual observation deadline, completeness and identity checks, and continued collection when transfer stops. The current implementation preserves independent collection; no CPU, I/O or process-count savings have been measured for reuse.

## Network and account actions

`network_policy.py` and `proxy_runtime/` use a pinned Electron helper for Chromium system-proxy resolution. This helper is installed locally; it is not a hosted gateway. `network_report.py` and `ip_check/` perform explicitly requested diagnostics, separate from log accounting.

Rollup also reaches this resolver through `aggregators._with_calculated_costs` → `pricing_fetcher.get_pricing` when price data needs refreshing. Removing Electron from a collector-only installation would change pricing fallback behavior. The installer therefore keeps one dependency set rather than adding role-specific installation modes and their maintenance burden. Local rollup is not an offline guarantee.

`codex_accounts.py`, `codex_messages.py`, `client_network.py` and `client_connector.py` implement optional account actions and paired browser-device routing. Login and sending require their own actions. Current-account quota refresh can run automatically when opening the dashboard or reading the standalone overview; explicit refresh also requests it, and Hub collection uses its configured schedule. These queries use existing account state and do not generate a model turn. Unknown send outcomes are retained and are not automatically replayed. `upstream_controls.py` retains local provider pauses. Live provider behavior is not established by synthetic sample data or unit tests.

## Persistence and maintenance

Runtime state is under `state/`, which may be an existing deployment-owned symlink. Preserve identity files, locks, archives, account profiles and retirement records during upgrades. `.venv`, `.runtime`, `web/vendor`, local configuration and state are not publication artifacts. Generated caches are not substitutes for authoritative retained history.

Backend work should inspect the actual affected consumer: aggregate preparation, retained-session reads, snapshot publication, sync and quota operations have different costs. Repeated view preparation and generation invalidation have regression tests in `tests/test_query_view.py`; `scripts/benchmark_backend.py` uses synthetic snapshots. Those observations do not establish production latency for other paths.

See [usage and operations](public-usage.md), [contributing](../CONTRIBUTING.md) and the module tests before changing these boundaries.

## Backend performance checks

| Consumer | Cost and lifetime | Regression entry |
| --- | --- | --- |
| Overview, Explore, sync status | Prepared `QueryView` aggregates; signatures grow with machine count and queries with selected daily rows | `tests/test_query_view.py` |
| View preparation | Startup/new-generation admission, digest and schema validation; old leased view remains readable | `scripts/benchmark_backend.py`, `tests/test_query_view.py` |
| Publication and GC | Snapshot copy and retained-history validation; long preparation stays outside the shared GC lock | `tests/test_snapshot_contention.py`, `tests/test_generation_gc.py` |
| Background collection/export | Archive materialization, rollup and full statistics export; synthetic initial/unchanged/append rounds | `scripts/benchmark_backend.py background`, `tests/test_benchmark_background.py` |
| Full-history sessions | Retained event admission/materialization and matching history; aggregate benchmarks do not cover this | `tests/test_sessions_cache.py`, `tests/test_conversation_sessions.py` |
| Sync and quota | Export, transport, publication, quota child lifecycle and browser API fan-out | `tests/test_sync_process.py`, `tests/test_quota_refresh.py` |

Residual costs include cold/new-generation validation, full-history admission/materialization and aggregate cardinality. Recheck the relevant row when its consumer changes; one page's improvement does not close shared-path costs. [Service diagnostics](operations/services.md) gives the runnable isolated benchmark and live observation interpretation. [Accounting contracts](references/metrics.md) and [UI design](design.md) preserve the maintenance boundaries behind parser and frontend code.
