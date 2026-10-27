# Architecture

agent-monitor is an independent Python service with a static JavaScript dashboard. Bash entrypoints manage a checkout-local Python environment and runtime. No parent repository, LLM Gateway service or private skill is required.

## Responsibility map

The backend currently consists of root-level Python modules rather than an installed Python package. Keep the public shell/CLI entries stable; use this map to locate a behavior before changing it. Tests below are starting points for the affected responsibility, not a substitute for other consumers of a shared contract.

| Responsibility | Source entry | Regression starting point |
| --- | --- | --- |
| CLI, install, runtime and service lifecycle | `agent-monitor`, `install.sh`, `deploy.py`, `deployment_guard.py`, `local_web.py`, `service_manager.py`, `service_runtime.py`, `runtime_rebuild.py`, `*_service.py` | `tests/test_install.py`, `tests/test_deployment_guard.py`, `tests/test_deployment_convergence.py` |
| Log parsing and source/account attribution | `parsers/`, `session_identity.py`, `project_alias.py` | `tests/test_managed_codex_profiles.py`, `tests/test_conversation_sessions.py` |
| Retained usage and accounting | `usage_archive.py`, `rollup.py`, `aggregators.py`, `token_cost.py`, `pricing_fetcher.py` | `tests/test_usage_archive.py`, `tests/test_rollup.py`, [accounting contracts](references/metrics.md) |
| Snapshot export, admission and cross-machine collection | `exporter.py`, `statistics_snapshot.py`, `generation.py`, `query_view.py`, `sync.py`, `sync_process.py`, `machine_config.py`, `hub.py` | `tests/test_usage_evidence_snapshot.py`, `tests/test_query_view.py`, `tests/test_snapshot_contention.py`, `tests/test_sync_process.py` |
| Sessions and conversation views | `session_cache.py`, `conversation_cache.py`, `server.py` | `tests/test_sessions_cache.py`, `tests/test_conversation_sessions.py` |
| Quotas and optional account actions | `quota_*.py`, `codex_accounts.py`, `codex_messages.py`, `claude_oauth.py`, `upstream_controls.py` | `tests/test_quota_refresh.py`, `tests/test_quota_recovery_flow.py`, `tests/test_managed_codex_profiles.py` |
| Network policy and browser-device routing | `network_policy.py`, `proxy_runtime/`, `network_report.py`, `ip_check/`, `account_egress.py`, `client_network.py`, `client_connector.py` | `tests/test_network_policy.py`, `tests/test_client_network.py`, `tests/test_device_connection.py` |
| HTTP and dashboard | `server.py`, `asset_delivery.py`, `web/*.html`, `web/app.js`, `web/pivot.js`, account modules and `web/styles.css` | `tests/test_web_static.py`, `tests/test_http_delivery.py`; [browser acceptance](../CONTRIBUTING.md#explicit-browser-and-connector-acceptance) |

`server.py` owns HTTP dispatch, view handlers and collection scheduling; `web/app.js` owns shared UI behavior and several page initializers. These are useful starting points, but a change there can affect multiple pages or background collection. The [backend cost map](#backend-performance-checks) identifies their distinct runtime consumers. `examples/` holds synthetic inputs and public configuration; `scripts/` holds explicit acceptance/measurement tools; `tests/fixtures/` holds test inputs. See [development checks](../CONTRIBUTING.md#choose-a-validation-entry) before running optional tools.

### Paths are part of runtime identity

Module moves require more than import changes. The CLI and service launchers address scripts relative to the checkout; modules derive `state` and resource paths from their source location. `server._source_files()` and `service_runtime.signature()` enumerate specific source directories, while `exporter._RUNTIME_AUTHORITY_PATHS` defines clean-source admission. A new package directory must be included in these mechanisms and their tests, or new code could escape drift/admission checks. Preserve public commands, state location, machine identity and the shared `token_cost.py` arithmetic contract when reorganizing code.

## Usage path

`parsers/claude.py` and `parsers/codex.py` read the operator's agent logs into the shared usage representation in `parsers/__init__.py`. `usage_archive.py` retains normalized observations. `rollup.py` maintains daily aggregates with project/source identity and shrink guards. `token_cost.py` and `pricing_fetcher.py` derive known-cost subtotals from recorded tokens and available prices. Missing prices or incomplete counters stay distinguishable from known zero.

`server.py` exposes usage, session, quota, collection and network routes. `aggregators.py`, `session_cache.py` and `conversation_cache.py` prepare the corresponding views. `web/` renders them. Codex native subagents can be grouped with their main conversation using explicit ancestry; unresolved ancestry is not guessed. Date boundaries use Asia/Shanghai.

`parsers/codex_profiles.py` independently reads the optional portable registry at `~/.local/share/codex-accounts/NAME/profile.json` (schema version 1). Each valid profile contributes `codex/sessions`, `codex/archived_sessions` and its own `codex/state_5.sqlite`; no module or service from the registry producer is imported. Immutable profile identity supplies optional session `account` metadata (`name`, `account_id`, `email`). The ordinary default Codex source remains supported without retrospectively assigning its sessions to today's login. Conflicting copies of one session ID retain the existing usage deduplication and expose unknown account attribution; cross-account ancestry is not grouped.

Statistics extension version 5 carries optional account metadata and continues reading versions 1–4. Upgrade the Hub/consumer before exporters: an old reader cannot admit version 5 and retains its prior successful generation. Managed profiles also contribute passive quota observations under the existing Codex block's `managed_accounts` array; the server merges these into ordinary account rows by provider account ID and email, never by directory label. Multiple observations from one machine contribute only one machine association per account. These reads do not log in, refresh tokens or query a provider. The dashboard identifies reading sources in the quota update details, separately from account labels, and missing readings remain unknown. Profile identity describes the managed home, not independent proof of a token's validity.

## Multiple machines

`machine_config.py` validates named machines, SSH targets, the local role and retired identities. At startup it selects `machines.local.json` if present, otherwise `machines.json`; explicit caller paths remain explicit. `hub.py` uses the same local-over-default convention for Hub configuration. All importers share the selected machine path, including retirement writes. Adding or removing a local configuration file requires restarting processes.

`exporter.py` produces versioned snapshots from a committed, clean source tree. `sync.py` invokes the installed exporter on declared SSH hosts; `generation.py` verifies and admits immutable snapshot generations. `query_view.py` prepares admitted aggregates for repeated API reads and keeps leases while consumers use a generation. Old snapshots remain distinct from newly observed quota readings. Raw provider secrets stay on their owning machines.

The optional Hub marker in `state/hub-machine` enables role-specific behavior. macOS LaunchAgents provide periodic local collection and the Hub service. Standalone mode works without a Hub marker and does not contact the maintainer's fleet in the exported public candidate.

## Network and account actions

`network_policy.py` and `proxy_runtime/` use a pinned Electron helper for Chromium system-proxy resolution. This helper is installed locally; it is not a hosted gateway. `network_report.py` and `ip_check/` perform explicitly requested diagnostics, separate from log accounting.

`codex_accounts.py`, `codex_messages.py`, `client_network.py` and `client_connector.py` implement optional account actions and paired browser-device routing. Login and sending require their own actions. Current-account quota refresh can run automatically when opening the dashboard or reading the standalone overview; explicit refresh also requests it, and Hub collection uses its configured schedule. These queries use existing account state and do not generate a model turn. Unknown send outcomes are retained and are not automatically replayed. `upstream_controls.py` retains local provider pauses. Live provider behavior is not established by synthetic sample data or unit tests.

## Persistence and maintenance

Runtime state is under `state/`, which may be an existing deployment-owned symlink. Preserve identity files, locks, archives, account profiles and retirement records during upgrades. `.venv`, `.runtime`, `web/vendor`, local configuration and state are not publication artifacts. Generated caches are not substitutes for authoritative retained history.

Backend work should inspect the actual affected consumer: aggregate preparation, retained-session reads, snapshot publication, sync and quota operations have different costs. Repeated view preparation and generation invalidation have regression tests in `tests/test_query_view.py`; `scripts/benchmark_backend.py` uses synthetic snapshots. Those observations do not establish production latency for other paths.

See [usage and operations](public-usage.md), [contributing](../CONTRIBUTING.md) and the module tests before changing these boundaries.

## Implementation constraints and tradeoffs

HTTP binding does not perform reverse DNS. Browser action admission accepts the existing IP and local-hostname cases without reverse DNS; other hostname aliases still use the host's FQDN lookup. Origin, action-header and cross-site checks remain required. A slow resolver can still delay requests using those other aliases.

`asset_delivery.py` binds static resource URLs to the Web tree's path-and-content signature, including a symlinked vendor directory. HTML remains uncached; matching versioned resources can be reused in the browser without a validation round trip. Unversioned or obsolete URLs retain conditional validation. Dynamic account modules carry the document's version, and the existing health watcher reloads a document when its Web signature changes. Text responses and aggregate JSON negotiate gzip when it reduces bytes; API results remain `no-store`. The bounded in-process byte cache invalidates on file identity, size or timestamp changes; it does not persist usage data. See the [delivery decision](adr/20261026-8d31-dashboard-delivery.md).

Statistics export copies only mutable usage metadata and formats timestamps directly. It validates and encodes every usage row before opening the destination, retaining encoded rows rather than both row dictionaries and a second encoded batch. Snapshot schema, row order, post-write readback and admission checks are unchanged. Export still processes the full retained history; this reduces serialization CPU and temporary memory, not collection frequency or snapshot size.

Standalone schedules call `rollup`; they do not use the dashboard-dependent `refresh` path. Hub `collect` and requested exports both retain their local rollup work. Reusing a recent export by skipping a collector when observation age is less than interval I would weaken the collection bound: collection at 0, export at ε, skipped collection at I, then a disconnected Hub can leave the next collection until 2I. Safe reuse would need scheduling against the actual observation deadline, completeness and identity checks, and continued collection when transfer stops. The current implementation preserves independent collection; no CPU, I/O or process-count savings have been measured for reuse.

Rollup also reaches this resolver through `aggregators._with_calculated_costs` → `pricing_fetcher.get_pricing` when price data needs refreshing. Removing Electron from a collector-only installation would change pricing fallback behavior. The installer therefore keeps one dependency set rather than adding role-specific installation modes and their maintenance burden. Local rollup is not an offline guarantee.

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
