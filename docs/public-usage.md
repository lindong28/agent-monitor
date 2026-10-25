# Usage, configuration and operations

## Local data and first results

Start with the [README](../README.md). Claude observations normally come from `~/.claude/projects` and `~/.config/claude/projects`; `CLAUDE_CONFIG_DIR` adds a configured input location. Codex uses `CODEX_HOME` or the default `~/.codex`. Changing an override does not isolate every source by itself: use a disposable HOME and a separate checkout for sample/test data. Session paths and account metadata are sensitive even when messages and tokens are absent.

In **总览**, inspect cost/token totals and model/project attribution. Use **会话** to filter and open individual conversations. For the included January 2026 sample, choose **全部** on both pages and open `~/example-project` in the session list. Expand **用量记录** in its details for the two observations, and **会话标识与记录范围** for `demo-session`. **采集与诊断** explains unreadable, old or incomplete sources. A retained total may cover more history than detailed sessions, so those numbers need not reconcile.

`./agent-monitor status` reports the owned process and interpreter. `./agent-monitor stop` stops it. Opening the dashboard with `./agent-monitor open` requests collection and quota refresh. Reading the standalone overview can also schedule an automatic refresh, so ordinary browsing can read current account state and contact the provider using an existing login. `./agent-monitor refresh` explicitly requests another round, including configured remote machines. The included sample uses a separate HOME without account metadata; quota is unavailable there. When the Web service is stopped, refresh can preserve local history while returning nonzero because the full round was not completed.

## Configuration and retained data

Public defaults declare one machine called `local`, with SSH target `localhost` and `self: true`. No Hub role is active until explicitly installed. Customize by copying `examples/machines.json` to `machines.local.json`; the schema is unchanged. Names are lowercase ASCII slugs. Exactly one active machine must be local. Comment lines starting with `#` or `//` and trailing commas are accepted in the machine file.

For an existing installation, copy its **current** machine/Hub files to the corresponding `.local.json` files before changing tracked defaults. Preserve its values instead of resetting to the examples. The installer never overwrites these local files. A process chooses the local/default path at startup; restart after adding or removing an override. Malformed local files and broken local symlinks produce errors rather than using another configuration.

Retirement is explicit: `./agent-monitor machines retire NAME` updates the selected machine configuration and retained retirement ledger. A retired machine name cannot be reused for a new source. A temporary commented-out source can be restored with its original identity. `./agent-monitor machines accept NAME --yes` accepts a newly verified source under the existing admission rules; do not use it to bypass an unexplained identity change.

Keep backups of state and local configuration outside the checkout before migration. Never replace an existing state symlink, remove persistent locks to force progress, or delete old archives merely because a source is temporarily unavailable. Uninstall keeps source and state.

## Optional multi-machine collection

Supply your own SSH aliases, with `agent-monitor` installed on each source. The collecting machine needs SSH and rsync access and invokes `~/.local/bin/agent-monitor export` remotely. Exporters require committed source and verify their runtime source identity. Noninteractive SSH environments must resolve the installed CLI and any required SSH agent.

For a macOS Hub, copy `examples/hub.json` to `hub.local.json` on each participating machine. Set `hub`, its trusted-network HTTP origin and port, and participating names. Match those names to `machines.local.json`. Then run `./install.sh hub MACHINE_NAME` on each host with its own selected role. This installs macOS services and starts collection; use only after reviewing your source and account access. The Hub pulls snapshots; collectors retain their own data. Intervals are specified in the Hub JSON.

## Optional quota and account actions

Local usage does not need a provider login. If your current account is signed in, normal dashboard collection can actively query quota; a supported provider login/client is required for those queries. Use the isolated sample if you do not authorize reading real account state or making quota queries. Quota observations retain account identity and observation time; old or missing values are not current permission to send. `./agent-monitor session-quota SESSION_ID --json` reads a stored Codex session quota observation without querying a provider.

Account operations use isolated managed profiles under state. Review the account, browser-device route and confirmation before sending; a message may consume your subscription quota. No scheduled message sender is installed. Failed or uncertain sends are not automatically replayed. If the UI reports a local hold, inspect its original failure before explicitly resuming; restarting does not erase the hold.

The optional connector supports `./agent-monitor connect --server URL` in the foreground and macOS service installation via `./agent-monitor connect install --server URL`. It pairs the browser device's network route with the account action. This is separate from the network diagnostics page, which reports the server machine's route.

## Services and deployment

`./install.sh --no-services` installs dependencies/assets and CLI links without changing services or running the post-install public probe. `./install.sh` on macOS additionally configures hourly collection with `com.agent-monitor.rollup`; Linux skips that LaunchAgent. The default installer is therefore a service-changing action. `./status.sh rollup-daemon` reports the scheduler.

Service-managed updates use `./deploy.py` from the primary, clean `main` checkout at the verified live `origin/main` commit. It checks source identity before calling the installer:

```bash
git fetch origin
git merge --ff-only origin/main
./deploy.py --check-only --expect FULL_SHA
./deploy.py --expect FULL_SHA
```

Replace `FULL_SHA` with the published commit you selected. `--check-only` does not install. A successful preflight is not service health: inspect status and the actual dashboard afterward. Deployment does not push, discard changes, migrate state or replace locks. Development worktrees are not persistent production roots. Preserve local edits and coordinate writers if preflight reports a dirty or diverged checkout.

## Troubleshooting

- Installer dependency errors: resolve the named Python/runtime/library error and rerun the installer. Missing optional fonts use system fallback and are reported as degraded; this is distinct from a failed proxy-runtime startup.
- Empty usage: confirm the source directories, date filter and log presence. Test with the included sample in its own checkout if needed. Do not infer historical zero spend from missing files.
- Startup: inspect this checkout's `state/server.log` and `./agent-monitor status`. Share only sanitized excerpts; errors and paths can contain private metadata.
- Collection: use **采集与诊断** to find the affected machine and stage. An old snapshot can remain readable while a new collection attempt fails.
- Provider hold: `.venv/bin/python upstream_controls.py status claude` (or `codex`) reads the local hold. After reviewing its reason, `resume PROVIDER --pause-id ID` acknowledges the specific current hold without sending a request. Account-specific restrictions remain separate.
- Network: `./agent-monitor network --json` runs public probes for the server host and can contact external diagnostic services. It is not an account-health test or a report of the browser device's IP.

Use [SECURITY.md](../SECURITY.md) for sensitive reporting. Do not attach raw state, credentials, account profiles or unredacted provider logs to issues.
