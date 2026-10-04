# Architecture

agent-monitor collects local agent usage and publishes read-only machine snapshots for a Web dashboard. It is an independent repository; ai-agent-config may invoke its installer but supplies no implementation at runtime.

## Data flow and ownership

Claude Code and Codex logs enter through `parsers/`; `usage_archive.py` retains normalized events and `rollup.py` preserves daily totals and project identity through bounded rebuilds and shrink guards. State lives under `state/`, optionally a deployment-owned symlink. Source identities and persistent locks survive source-directory changes.

`exporter.py` writes versioned snapshots only from a committed, clean runtime. `statistics_snapshot.py` validates their schema and retained event/Gateway projections. `sync.py` asks declared SSH sources to run `~/.local/bin/agent-monitor export`; `generation.py` admits verified immutable generations, with leases and digests. Remote credentials and raw account secrets stay on their owner machines.

`hub.py` configures local roles and macOS lifecycle: every participating Mac collects locally, while MacStudio's Hub pulls admitted snapshots and serves the dashboard. Statistics collection stays at 120 seconds in Hub mode and quotas at 3600 seconds. Quota-only refresh follows statistics publication without a second statistics export.

`server.py` and `aggregators.py` answer the existing usage, session, pivot, quota, network and Gateway API routes. `web/` renders those APIs; the renamed `AgentMonitor` JavaScript namespace is internal to these pages. Network diagnostics share `ip_check/` with the secondary `ip-check` CLI.

## Independent runtime boundary

`agent-monitor`, lifecycle scripts and `install.sh` resolve this project's `.venv`; the installer creates it from `requirements.txt` and vendors browser assets under `web/vendor`. `lib/install-output.sh` is the small optional FD ledger emitter, not a loader for the former parent repository. Installers can stage with `--no-services` before service cutover.

`claude_oauth.py` owns the extracted read-only credential/date/HTTP helpers. File and keychain candidates are lazy, deduplicated and locally validated; profile/usage requests refuse redirects and carry tokens only as headers. `quota_refresh.py` bounds the complete provider child process. Statusline rendering, statusline cache persistence and model-specific statusline windows remain harness responsibilities.

The independent llm-gateway repository supplies its Python audit reader through `gateway_dependency.py`; the default checkout is `~/research/llm-gateway`, overridden by `LLM_GATEWAY_ROOT`. Gateway owns ledger schema fingerprints, pricing policy and inference credentials. This consumer retains versioned field mappings and unknown-field rejection. No Gateway service is started by this installer.

## Preserved contracts

Extraction changes names, installation ownership and source paths. It does not change bucket timezone, snapshot schemas, host/account identity, retention or arithmetic. `token_cost.py` is byte-identical to its extraction source. The source commit and historical docs are recorded in the [extraction ADR](adr/20261004-a902-standalone-project.md).
