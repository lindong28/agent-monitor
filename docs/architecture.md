# Architecture

agent-monitor collects local agent usage and publishes read-only machine snapshots for a Web dashboard. It is an independent repository; ai-agent-config may invoke its installer but supplies no implementation at runtime.

## Data flow and ownership

Claude Code and Codex logs enter through `parsers/`; `usage_archive.py` retains normalized events and `rollup.py` preserves daily totals and project identity through bounded rebuilds and shrink guards. State lives under `state/`, optionally a deployment-owned symlink. Source identities and persistent locks survive source-directory changes.

`exporter.py` writes versioned snapshots only from a committed, clean runtime. `statistics_snapshot.py` validates their schema and retained event/Gateway projections. `sync.py` asks declared SSH sources to run `~/.local/bin/agent-monitor export`; `generation.py` admits verified immutable generations, with leases and digests. Remote credentials and raw account secrets stay on their owner machines.

`hub.py` configures local roles and macOS lifecycle: every participating Mac collects locally, while MacStudio's Hub pulls admitted snapshots and serves the dashboard. Statistics collection stays at 120 seconds in Hub mode and quotas at 3600 seconds. Quota-only refresh follows statistics publication without a second statistics export.

`server.py` and `aggregators.py` answer the existing usage, session, pivot, quota, network and Gateway API routes. `web/` renders those APIs; the renamed `AgentMonitor` JavaScript namespace is internal to these pages. Network diagnostics share `ip_check/` with the secondary `ip-check` CLI.

The overview borrows the value-and-trend panel relationship from Prompt Planet's `usage-observability-console` v1 while retaining the blue theme, independent account quotas and distinct cost windows. Its three summaries and trend share one surface; only the third summary and trend follow the range selector. This is a markup/style adaptation with no template runtime dependency or shared component package. See the [reuse decision](adr/20261006-91da-overview-template-reuse.md).

## Independent runtime boundary

`agent-monitor`, lifecycle scripts and `install.sh` resolve this project's `.venv`; the installer creates it from `requirements.txt` and vendors browser assets under `web/vendor`. `lib/install-output.sh` is the small optional FD ledger emitter, not a loader for the former parent repository. Installers can stage with `--no-services` before service cutover.

`claude_oauth.py` owns the extracted read-only credential/date/HTTP helpers. File and keychain candidates are lazy, deduplicated and locally validated; profile/usage requests refuse redirects and carry tokens only as headers. `quota_refresh.py` bounds the complete provider child process. Statusline rendering, statusline cache persistence and model-specific statusline windows remain harness responsibilities.

The independent llm-gateway repository supplies its Python audit reader through `gateway_dependency.py`; the default checkout is `~/research/llm-gateway`, overridden by `LLM_GATEWAY_ROOT`. Gateway owns ledger schema fingerprints, pricing policy and inference credentials. This consumer retains versioned field mappings and unknown-field rejection. No Gateway service is started by this installer.

The calls page, request diagnostics and JSON export use that reader through read-only HTTP adapters. Hub adapters hold an admitted-generation lease throughout each read. Detail lookup carries machine and project identity; its complete child chain is independent of the list window. Export returns one selected record kind across all matching rows. Different HTTP observations can see different generations; their envelopes retain their own source times. UI teardown and selection guards prevent a late detail response from replacing a newer page or request. No new collection, ledger or indexing mechanism is introduced by this UI integration.

## Preserved contracts

`codex_accounts.py` owns explicit, click-triggered Codex account actions through a dedicated app-server subprocess per active profile. `server.py` exposes same-origin account-action endpoints; `web/codex-accounts.js` renders state within the overview lifecycle. Private profile directories under `state/codex-accounts` contain managed credentials and the last operation, with a persistent per-profile file lock. Isolated HOME/config/work directories keep ordinary CLI credentials and project tools out of these operations. Only pending device authorization information and sanitized account/operation metadata reach the browser. No background weekly scheduler is installed.

Message status and quota observation are separate: an interrupted transmission stays unknown, a successful message survives a quota-query failure, and quota-only refresh sends no message. The server timestamp remains authoritative; action results do not rewrite collected quotas, snapshots or account history. See the [design and acceptance boundary](adr/20261007-c814-codex-account-actions.md).

Extraction changes names, installation ownership and source paths. It does not change bucket timezone, snapshot schemas, host/account identity, retention or arithmetic. `token_cost.py` is byte-identical to its extraction source. The source commit and historical docs are recorded in the [extraction ADR](adr/20261004-a902-standalone-project.md).
