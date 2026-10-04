# Independent agent-monitor project

Date: 2026-10-04. Status: accepted by the owner request to extract all tt-web functionality and rename the project and CLI, including existing deployment migration. Independent decision review accepted extraction and staged deployment; that review does not establish deployment completion.

## Decision

Import the tracked tt-web application snapshot from [ai-agent-config 3d61487d](https://github.com/lindong28/ai-agent-config/tree/3d61487d5386dfe9bb6ba7d26807005bbf0bd7f2/tt-web) into this repository's root. Canonical command: `agent-monitor`; environment prefix: `AGENT_MONITOR_`; macOS labels: `com.agent-monitor.rollup` and `com.agent-monitor.hub`. Own the Python environment, dependencies, assets, lifecycle scripts and minimal installation ledger emitter here. Keep Gateway independent.

Extract only the read-only credential, date and HTTP portion of the source [statusline helper](https://github.com/lindong28/ai-agent-config/blob/3d61487d5386dfe9bb6ba7d26807005bbf0bd7f2/claude/statusline-usage.py). The three statusline-specific test files remain owned by the harness; applicable credential tests are adapted here. `token_cost.py` remains byte-identical to the source. Runtime state, credentials, caches, vendored assets and unrelated private repository history are not imported.

## Alternatives and consequences

A wrapper over the parent checkout would leave the project dependent on it. Copying the full repository history would import unrelated private material. A source snapshot with immutable provenance preserves the current application without those dependencies. Historical documentation remains identifiable as tt-web evidence.

Installers can stage runtime/assets/CLI with `--no-services`. Exporters switch before the Hub; any temporary old-CLI compatibility belongs to the deployment transition and is retired after the Hub switch. Existing data moves only after all writers are stopped and its locks are available, preserving the lock inode. Managed deployments use `~/.local/share/agent-monitor/state` through `root/state`; standalone installs can use the root directory directly. Source extraction does not change accounting, snapshot schemas or identity.
