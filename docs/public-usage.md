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

## Transition an existing installation

This is distinct from updating an installation already based on the public `main`. The release candidate uses a separate publication history, so `git pull --ff-only` from the old history is not a migration command. Publication and replacement of the old main/history require the owner's separate decision. Do not force-push, reset or remove the old checkout as part of these preparation steps.

1. Record the old source revision, checkout path, selected machine/Hub configuration paths and service entrypoints. Preserve the old Git history and private operational documentation outside the public candidate. Keep a recoverable backup of the actual state target as well as the `state` symlink itself; archiving only the link does not back up its data. Coordinate a quiet window for the final state backup.
2. Before replacing tracked defaults, retain the selected configuration as `machines.local.json` and `hub.local.json`. If the local file already exists, preserve it; otherwise copy the corresponding current tracked file. For example, from the old checkout:

   ```bash
   for kind in machines hub; do
     if [ ! -e "$kind.local.json" ] && [ ! -L "$kind.local.json" ]; then
       cp "$kind.json" "$kind.local.json"
     fi
   done
   ```

   Keep the original names, `self`/Hub role, SSH targets, retired names and intervals. The public examples must never replace them. Preserve `state/hub-machine`, source/account identities, archives, generation metadata and persistent locks. Do not commit local configuration or state.
3. Rehearse in an isolated copy of the old installation, including a separate HOME and a private copy of its state target. Rewrite copied symlinks and service paths so none points back to live state or services. Use synthetic agent logs/account metadata when real data access is not authorized. Install the exact candidate source into that rehearsal checkout while preserving its local configuration and runtime state, then run `./install.sh --no-services`. Do not invoke service installation or remote collection from the rehearsal. A separate HOME does not isolate the macOS launchd domain: when `state/hub-machine` identifies this machine as the Hub, `./agent-monitor start` can install the real user service. Direct `server.py` startup also enables scheduled synchronization. Do not use either startup path for a preserved Hub/fleet rehearsal.
4. For a standalone installation with no remote targets, start the isolated instance with a free port and its isolated HOME; compare retained totals and session details with the old instance, then stop it. For a Hub or configured fleet, keep its role marker and configuration unchanged and limit this preparation to installation preservation and the local retained-data reading below. Run it before and after source replacement using the isolated HOME. It imports the reader without starting the server, scheduler, SSH or provider actions. In both cases, compare machine/source identities, state link target, persistent lock identities and retained data; repeat preservation/installation and compare again. Full Hub browser/collection checks remain for the authorized cutover. A successful new installation alone does not prove this transition.
   ```bash
   .venv/bin/python - <<'PY'
   import json
   import generation, hub, machine_config, rollup
   config = machine_config.load_machine_config()
   print(json.dumps({"machine": config.self_machine.name,
                     "installed_role": hub.local_machine(),
                     "retired_names": sorted(config.retired_names)}))
   with generation.generation_admission_snapshot() as admitted:
       print(json.dumps({"admitted": sorted(item.host for item in admitted.admitted)}))
       with rollup.use_admitted_generations(admitted.admitted):
           print(json.dumps(rollup.query_grouped([], "total")))
   PY
   ```

   Compare the actual returned totals and admitted names; an empty result does not establish retained-data continuity. This reads a copy of retained state and may create local reader leases/locks; it is not a live health or exporter check. Retain the old-source and candidate outputs privately.

5. After publication/history replacement is explicitly authorized, make the agreed public `main` authoritative at the existing primary checkout path, preserving the backed-up local configuration and state. Coordinate any source-path changes with CLI links and service definitions. Follow [services and deployment](#services-and-deployment) at the verified published revision; an uncommitted overlay or rehearsal checkout is not deployable source. Verify the actual dashboard and collection results before retiring the old recovery copy.

If any identity, retained-data or source check fails, stop before enabling collection. Preserve the failing state and logs; restore the prior source and configuration from the recovery copy under the same service-ownership checks. Do not delete locks, reset identities or accept a new source simply to pass preflight. This guide does not claim a production cutover has occurred.

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

For runtime observations and backend investigation, see [service diagnostics](operations/services.md) and [backend checks](public-architecture.md#backend-performance-checks).

## Troubleshooting

- Installer dependency errors: resolve the named Python/runtime/library error and rerun the installer. Missing optional fonts use system fallback and are reported as degraded; this is distinct from a failed proxy-runtime startup.
- Empty usage: confirm the source directories, date filter and log presence. Test with the included sample in its own checkout if needed. Do not infer historical zero spend from missing files.
- Startup: inspect this checkout's `state/server.log` and `./agent-monitor status`. Share only sanitized excerpts; errors and paths can contain private metadata.
- Collection: use **采集与诊断** to find the affected machine and stage. An old snapshot can remain readable while a new collection attempt fails.
- Provider hold: `.venv/bin/python upstream_controls.py status claude` (or `codex`) reads the local hold. After reviewing its reason, `resume PROVIDER --pause-id ID` acknowledges the specific current hold without sending a request. Account-specific restrictions remain separate.
- Network: `./agent-monitor network --json` runs public probes for the server host and can contact external diagnostic services. It is not an account-health test or a report of the browser device's IP.

Use [SECURITY.md](../SECURITY.md) for sensitive reporting. Do not attach raw state, credentials, account profiles or unredacted provider logs to issues.
