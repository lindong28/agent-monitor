# Usage, configuration and operations

## Local data and first results

When the account launcher records multiple selected accounts for one resumed session, its list/detail account becomes **账号归属未记录或冲突**. Token usage is retained, but that session's log quota is excluded from every account, including the default home. A selection record does not prove that a request finished or incurred usage. Invalid or unreadable records produce source errors and unknown attribution; repair the launcher's `session-links` record before relying on new attribution. Older passive quota caches without a source UUID are withheld when cross-account records make their ownership unverifiable; active quota refresh and its pause controls are unchanged.

Managed Codex accounts registered at `~/.local/share/codex-accounts/NAME/profile.json` are discovered automatically. Sessions and archived sessions are collected from each profile's `codex/` directory. The session list and detail show the frozen account name and email. In the quota table, account labels show the email when available; expand **配额更新** to see the reading source and profile name. The account search also matches profile names. Unbound or invalid profiles never acquire an account identity from their directory name. Default-home sessions retain unknown account attribution, and copied session IDs with conflicting owners are also shown as unknown. No provider refresh or login is added for managed profiles. Install/upgrade the Monitor on the new machine as usual; the account registry itself belongs to your account launcher. For multi-machine deployments, upgrade the Hub before exporters because account metadata requires statistics extension version 5.

Start with the [README](../README.md). Claude observations normally come from `~/.claude/projects` and `~/.config/claude/projects`; `CLAUDE_CONFIG_DIR` adds a configured input location. Codex uses `CODEX_HOME` or the default `~/.codex`. Changing an override does not isolate every source by itself: use a disposable HOME and a separate checkout for sample/test data. Session paths and account metadata are sensitive even when messages and tokens are absent.

In **总览**, inspect cost/token totals and model/project attribution. Use **会话** to filter and open individual conversations. For the included January 2026 sample, choose **全部** on both pages and open `~/example-project` in the session list. Expand **用量记录** in its details for the two observations, and **会话标识与记录范围** for `demo-session`. **采集与诊断** explains unreadable, old or incomplete sources. A retained total may cover more history than detailed sessions, so those numbers need not reconcile.

`./agent-monitor status` reports the owned process and interpreter. `./agent-monitor stop` stops it. Opening the dashboard with `./agent-monitor open` requests collection and quota refresh. Reading the standalone overview can also schedule an automatic refresh, so ordinary browsing can read current account state and contact the provider using an existing login. `./agent-monitor refresh` explicitly requests another round, including configured remote machines. The included sample uses a separate HOME without account metadata; quota is unavailable there. When the Web service is stopped, refresh can preserve local history while returning nonzero because the full round was not completed.

## Configuration and retained data

Public defaults declare one machine called `local`, with SSH target `localhost` and `self: true`. No Hub role is active until explicitly installed. In a fresh clone, customize by copying `config/machines.json` to `config/machines.local.json`; the default file is also the template. If a local file already exists, edit it without replacing it. Preserve existing identities and retired names when updating an installation. Names are lowercase ASCII slugs. Exactly one active machine must be local. Comment lines starting with `#` or `//` and trailing commas are accepted in the machine file.

For an existing installation, copy its **current** machine/Hub files to the corresponding `.local.json` files before changing tracked defaults. Preserve its values instead of resetting to the public defaults. The installer never overwrites these local files. A process chooses the local/default path at startup; restart after adding or removing an override. Malformed local files and broken local symlinks produce errors rather than using another configuration.

Retirement is explicit: `./agent-monitor machines retire NAME` updates the selected machine configuration and retained retirement ledger. A retired machine name cannot be reused for a new source. A temporary commented-out source can be restored with its original identity. `./agent-monitor machines accept NAME --yes` accepts a newly verified source under the existing admission rules; do not use it to bypass an unexplained identity change.

Keep backups of state and local configuration outside the checkout before migration. Never replace an existing state symlink, remove persistent locks to force progress, or delete old archives merely because a source is temporarily unavailable. Uninstall keeps source and state.

`./agent-monitor archive stats` reports the existing usage archive's main file, WAL and reusable pages without initializing or changing it. Reusable pages are not disk space already released. After an archive migration, explicit physical maintenance is available with `./agent-monitor archive compact --backup /persistent/new-backup.sqlite3`. Choose a new backup destination; existing files and symlinks are refused. This operation acquires the collection lock, makes and verifies a consistent recovery copy, runs in-place SQLite compaction, and checks the result. It does not expire history or install a scheduled cleanup. Allow enough free space and a maintenance window; `--budget-seconds` defaults to 300. Use `--archive /path/to/usage_archive.sqlite3` for a different existing state directory; its `rollup.db` and initialized lock must already exist alongside it.

The command reports archive reduction separately from the retained backup. A private recovery link and the requested backup name refer to the same file; keep or remove both under your backup policy. A failed or interrupted operation is not a successful reclaim: inspect the reported stage, current archive and backup verification status before retrying with a new destination. Never automatically restore an older backup over subsequent writes. See [maintenance behavior and limits](adr/20261028-4424-archive-maintenance.md).

## Ordinary service logs

`state/server.log`, `hub.log` and `rollup-daemon.log` retain recent troubleshooting output, including output across normal restarts. The default policy keeps two 8 MiB segments per stream: the active file and `.1`. To change it, create `state/service-logs.json` with `{"segment_bytes":8388608,"segments":2}` and restart the affected services through their normal lifecycle commands. Segment size must be a positive integer; segment count must be between 2 and 100. The optional `AGENT_MONITOR_LOG_SEGMENT_BYTES` and `AGENT_MONITOR_LOG_SEGMENTS` environment variables override the file, but terminal environment changes do not necessarily reach launchd services.

Maintenance runs in the existing Web loop (at most once per 30 seconds) and at collector startup/normal exit. Logs can exceed the configured capacity between checks; the copy-and-truncate window can lose concurrent output, so this is recent troubleshooting retention, not a lossless audit log or a fixed number of days. A failed maintenance operation warns without stopping collection and can leave logs growing. A hard exit can leave one bounded `.<log-name>.pending` file for reuse on the next maintenance attempt. Numbered backups and this pending name belong to the mechanism; keep manual backups elsewhere. Reducing segment size ages out older, larger backups through subsequent rotations rather than immediately rewriting them. Session/usage history, diagnostic events, connector output and manually named old logs are outside this policy. See [the lifecycle and failure limits](adr/20261028-4424-bounded-service-logs.md).

## Transition an existing installation

This is distinct from updating an installation already based on the public `main`. The public repository uses a separate history from the earlier private installation, so `git pull --ff-only` from that old history is not a migration command. Replacing an old installation's source requires its operator's decision. Do not force-push, reset or remove the old checkout as part of these preparation steps.

1. Record the old source revision, checkout path, selected machine/Hub configuration paths and service entrypoints. Preserve the old Git history and private operational documentation outside the public candidate. Keep a recoverable backup of the actual state target as well as the `state` symlink itself; archiving only the link does not back up its data. Coordinate a quiet window for the final state backup.
2. Before replacing tracked defaults, retain the selected configuration as `machines.local.json` and `hub.local.json`. If the local file already exists, preserve it; otherwise copy the corresponding current tracked file. For example, from the old checkout:

   ```bash
   for kind in machines hub; do
     if [ ! -e "$kind.local.json" ] && [ ! -L "$kind.local.json" ]; then
       cp "$kind.json" "$kind.local.json"
     fi
   done
   ```

   Keep the original names, `self`/Hub role, SSH targets, retired names and intervals. The public defaults must never replace them. Preserve `state/hub-machine`, source/account identities, archives, generation metadata and persistent locks. Do not commit local configuration or state.
3. Rehearse in an isolated copy of the old installation, including a separate HOME and a private copy of its state target. Rewrite copied symlinks and service paths so none points back to live state or services. Use synthetic agent logs/account metadata when real data access is not authorized. Install the exact candidate source into that rehearsal checkout while preserving its local configuration and runtime state, then run `./install.sh --no-services`. Do not invoke service installation or remote collection from the rehearsal. A separate HOME does not isolate the macOS launchd domain: when `state/hub-machine` identifies this machine as the Hub, `./agent-monitor start` can install the real user service. Direct `python -m agent_monitor.http_server` startup also enables scheduled synchronization. Do not use either startup path for a preserved Hub/fleet rehearsal.
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

5. After the operator approves replacing the old installation's source, make the public `main` authoritative at the existing primary checkout path, preserving the backed-up local configuration and state. Coordinate any source-path changes with CLI links and service definitions. Follow [services and deployment](#services-and-deployment) at the verified published revision; an uncommitted overlay or rehearsal checkout is not deployable source. Verify the actual dashboard and collection results before retiring the old recovery copy.

If any identity, retained-data or source check fails, stop before enabling collection. Preserve the failing state and logs; restore the prior source and configuration from the recovery copy under the same service-ownership checks. Do not delete locks, reset identities or accept a new source simply to pass preflight. This guide does not claim a production cutover has occurred.

## Optional multi-machine collection

Supply your own SSH aliases, with `agent-monitor` installed on each source. The collecting machine needs SSH and rsync access and invokes `~/.local/bin/agent-monitor export` remotely. Exporters require committed source and verify their runtime source identity. Noninteractive SSH environments must resolve the installed CLI and any required SSH agent.

For first-time macOS Hub setup, copy `config/hub.json` to `config/hub.local.json` on each participating machine that does not already have a local file; otherwise edit the existing file. Set `hub`, its trusted-network HTTP origin and port, and participating names. Match those names to the selected machine configuration. Then run `./install.sh hub MACHINE_NAME` on each host with its own selected role. This installs macOS services and starts collection; use only after reviewing your source and account access. The Hub pulls snapshots; collectors retain their own data. Intervals are specified in the Hub JSON.

## Optional quota and account actions

Local usage does not need a provider login. If your current account is signed in, normal dashboard collection can actively query quota; a supported provider login/client is required for those queries. Use the isolated sample if you do not authorize reading real account state or making quota queries. Quota observations retain account identity and observation time; old or missing values are not current permission to send. `./agent-monitor session-quota SESSION_ID --json` reads a stored Codex session quota observation without querying a provider.

Account operations use isolated managed profiles under state. Review the account, browser-device route and confirmation before sending; a message may consume your subscription quota. No scheduled message sender is installed. Failed or uncertain sends are not automatically replayed. If the UI reports a local hold, inspect its original failure before explicitly resuming; restarting does not erase the hold.

The optional connector supports `./agent-monitor connect --server URL` in the foreground and macOS service installation via `./agent-monitor connect install --server URL`. It pairs the browser device's network route with account actions and supplies the local process observations described below. Process observations do not change the account action's route or the actual agents' proxy settings.

## Local agent network

The network page starts with the **browser computer's** Codex/Claude processes. Install this repository on that computer using the [public installation steps](../README.md#requirements), even when the dashboard is hosted elsewhere. Run the connector as the same OS user who starts those agents. Use the exact dashboard origin, including its port:

```bash
./install.sh --no-services
./agent-monitor connect --server http://your-monitor-host:39001
```

Keep this foreground command running, open the network page, click “读取本机 Agent”, then select a process. The connector needs its installed system-proxy runtime and a reachable Hub; its loopback popup uses port 39002. Allow the popup for your trusted dashboard origin. The browser checks the popup, origin and one-time nonce. The report travels directly from this computer to the page, without storage or transit through the Hub. Do not expose the connector port externally. On macOS, `./agent-monitor connect install --server URL` is the optional managed alternative. Linux uses the foreground command or your own supervisor. No maintainer-specific harness, proxy application or account credential is required for process observations.

The report separates process instance identity (PID and start time), observation time, redacted startup network environment and currently established TCP sockets. A local proxy peer is not a public exit IP. Startup environment is not proof of runtime overrides or actual request routing. The supported discovery scope is same-user local native Codex/Claude, native binaries reached through `~/.local/bin/codex` or `claude`, and the recognized npm Node/Bun CLI entrypoints. Other users, containers, SSH hosts, DNS/UDP traffic and client config-file overrides are outside this observation. Permission failures, process exit and incomplete scans are shown; an empty field does not establish direct access. The CLI `./agent-monitor process-network` reads the same observations without a connector or upstream request; `--json` emits the structured report.

### Capabilities and prerequisites

These capabilities complement each other in the selected process view. None of the optional capabilities is required for basic observations. Available in the capability-aware network revision following `b01cbf8`; update both the Web server and browser computer's connector. Older connector reports remain readable but cannot enable the new probe controls.

| Capability | Prerequisite and action | External traffic and result scope | Missing condition / next step |
| --- | --- | --- | --- |
| Process identity and TCP snapshot | Same-user supported macOS/Linux process; click “读取本机 Agent”, search by directory/PID/start/terminal and explicitly select | No public request. Shows available OS working directory, start time, parent/terminal, redacted startup configuration and established TCP endpoints | Missing context or permissions remain unknown. Start the connector as the agent's user; read again during normal agent use. A missing socket does not prove direct access. |
| Existing proxy targets and chain | Optional local Mihomo Unix controller; read with the same snapshot | Only local `GET /connections`. Matches source IP/port and proxy inbound IP/port to this process's current TCP sockets, and process path when supplied. Shows existing destination and controller chain, not the public source IP | Absent/unreadable controller or incomplete/nonunique tuple leaves base observations intact. Configure the optional socket below, or continue with available observations. |
| Third-party exit observation | Explicit “发送第三方出口观测” action, admitted current process proxy | One anonymous GET to `http://ip-api.com/json/` through the observed HTTPS/ALL proxy. **HTTP is unencrypted; the free endpoint permits noncommercial use only.** Returns this diagnostic's IP and location. It does not reproduce `HTTP_PROXY` routing or prove model-service egress | Unmet admission is shown before the action. Users who cannot accept the service's terms/HTTP can use passive information or independently choose provider probing. A failure never triggers another target. |
| Fixed provider target observation | Separate explicit provider probe action, admitted current process proxy | One anonymous HTTPS GET to `https://chatgpt.com/cdn-cgi/trace` for Codex or `https://api.anthropic.com/cdn-cgi/trace` for Claude. No model message, provider login, cookie or account header. The endpoint may not return a trace | This fixed target need not be the process's actual service/login mode. Receipt proves only this target/proxy/time; it does not establish the source IP of earlier agent traffic. Refusing this action leaves other capabilities available. |
| Server system diagnostics | Open the secondary section, then explicitly click “检测服务端” | Runs the existing server-host diagnostics, including external IP/reputation queries. Merely expanding the section sends no diagnostic request | Independent of browser-computer observations; never used as their fallback. |

Both active probes require a unique supported startup HTTPS/ALL proxy matching a current process socket. Custom endpoint variables, any nonempty bypass rule, conflicting proxies, unresolved proxy hostnames, missing sockets and changed process instances block requests. The report and request share the same admission policy; it is rechecked immediately before dispatch and after the response. Do not clear `NO_PROXY`, change proxies or remove a custom endpoint just to enable diagnostics. No redirects, automatic retry, direct fallback or automatic change of probe kind occurs.

Receipts retain their process instance, diagnostic target, proxy and observation time. Refresh preserves the selected full instance and prior results; disappeared instances are identified rather than silently replaced. Previous receipts remain explicitly historical after refresh, and a failed attempt does not erase an independent successful receipt. Cancellation stops waiting; an already-dispatched request can finish. The connector keeps only in-memory receipts/cooldowns, with a minimum 60-second per-route cooldown and longer `Retry-After`. Third-party requests additionally share a target-wide cooldown across processes, honoring ip-api `X-Rl=0` / `X-Ttl`; other applications sharing the same exit may still consume its quota. Restarting clears this memory, so do not restart to evade an upstream wait.

### Optional Mihomo integration

No Mihomo installation is required or performed by Agent Monitor. If you already use it, enable a Unix controller according to [Mihomo's configuration](https://wiki.metacubex.one/config/general/) and restrict access to trusted local users: Unix-controller requests do not verify the API secret. Agent Monitor never changes controller settings. Discovery uses `lsof` and `ps` to find the local proxy listener (`mihomo`, `verge-mihomo`, `clash` or `clash-meta`) and its Unix socket. On systems without those tools or with a differently named executable, explicitly supply the socket when starting the connector:

```bash
AGENT_MONITOR_MIHOMO_SOCKET=/absolute/path/to/mihomo.sock ./agent-monitor connect --server http://your-monitor-host:39001
```

The same variable works with `./agent-monitor process-network --json`. For a supervised connector, supply it through your supervisor's environment and restart that connector; the macOS `connect install` command does not persist arbitrary shell variables. No token, provider store or proxy configuration file is read. Controller versions must return TCP `sourceIP/sourcePort` and `inboundIP/inboundPort`; missing fields stay unmatched. A target or chain name is controller-reported routing evidence, not a verified NAT/public exit. Only related fields from matched connections reach the browser; unrelated controller records are discarded. Reports and directory hints remain in the trusted page's memory, so take care when sharing screenshots.

The third-party contract is documented by [ip-api](https://ip-api.com/docs/api:json). The free endpoint has no API key, does not offer HTTPS and has service-level rate/use restrictions; Agent Monitor does not supply a commercial subscription or silently substitute another service.

After updating with the normal update/deploy procedure, restart an existing connector (`./agent-monitor connect restart` for the managed service; stop and rerun the original foreground command otherwise). A connector predating process reports shows a connection/version error; a connector with the older process report remains read-only until upgraded. If connecting fails, check `./agent-monitor connect status`, the configured dashboard origin and whether another installation owns port 39002. Do not replace existing configuration or state to make pairing succeed. The collapsed “服务端系统网络诊断” retains the old server diagnostics as an explicit independent action. Neither report is a 30-day history; `range=30d` does not change its observation time.

## Manual updates

This section is for a **manually started standalone dashboard** already on public `main`. A configured Hub or service-managed installation uses [services and deployment](#services-and-deployment) instead; the older private history uses [transition an existing installation](#transition-an-existing-installation). Finish active account operations before stopping. Preserve a recoverable backup of state and local configuration outside the checkout; do not discard source edits to make `git pull --ff-only` succeed.

For the README's **sample checkout**, first confirm `.runtime/demo-home` is the existing sample HOME. The checks below stop the sequence if it is absent; do not substitute your real HOME. An older sample created with `mktemp -d` must use its original HOME path instead. If that path is lost, stop the old sample and create a new isolated sample checkout rather than restarting it against real data.

```bash
(
  export HOME="$PWD/.runtime/demo-home"
  export CLAUDE_CONFIG_DIR="$HOME/.claude" CODEX_HOME="$HOME/.codex"
  export AGENT_MONITOR_BIND=127.0.0.1
  test -f "$CLAUDE_CONFIG_DIR/projects/demo/session.jsonl" || exit 1
  ./agent-monitor stop &&
  git pull --ff-only &&
  ./install.sh --no-services &&
  ./agent-monitor start &&
  ./agent-monitor status
)
```

The subshell keeps this HOME out of your normal shell. If you selected another sample bind address or port, restore those values before stopping and retain them through restart. Check **总览 → 全部** and **会话 → 全部** again: the included session should still show 1,800 tokens and two usage records. An unexpected source or account is a reason to stop and inspect the chosen HOME and retained state.

For the README's **default real-data setup**, run the following from its original checkout and original HOME. If you customized `HOME`, `CLAUDE_CONFIG_DIR`, `CODEX_HOME`, `AGENT_MONITOR_BIND` or `AGENT_MONITOR_PORT`, set those same values for the entire sequence first; the commands do not recover them from the old process. Do not use this real-data path for a sample installation.

```bash
(
  export AGENT_MONITOR_BIND="${AGENT_MONITOR_BIND:-127.0.0.1}"
  ./agent-monitor stop &&
  git pull --ff-only &&
  ./install.sh --no-services &&
  ./agent-monitor start &&
  ./agent-monitor status
)
```

Open the printed address and check the expected machines, totals and a retained session. Successful installation or process startup alone does not verify those results. On failure, keep state/configuration and inspect the reported error and `state/server.log`; do not retry with different data sources or clear retained history. To remove integration, use the ownership-aware `./uninstall.sh` described below; it retains source and state.

## Configuration path migration

New defaults and overrides live under `config/`. Existing root `machines.local.json` and `hub.local.json` remain supported without automatic relocation. Both old and new overrides for one configuration are a conflict, even when their contents match or one is a broken symlink. Resolve that conflict before restarting; no identity is selected silently.

To relocate an existing override, stop the affected services, back up the selected configuration, check that the destination does not exist (including a symlink), then move the file into `config/`. Do not copy the public defaults over an installation's selected identities or retired names. For a relative symlink, preserve its effective target by adjusting the link, or leave it at the supported legacy location. Restart and check the selected machine/Hub identity before resuming collection.

When rolling back to a version that only reads root configurations, stop the affected services and preserve the current override first. Restore its contents or valid link to the root `*.local.json` path before reverting source. If a root override already exists, reconcile and preserve both versions rather than overwriting one. Then restart and verify identity. Reverting source alone is insufficient after adopting `config/*.local.json`; installations still using root overrides need no configuration move.

## Command migration

The redundant root adapters have been retired. Update external scripts as well as interactive commands using this table; repository checks cannot discover every external caller.

| Previous command | Current command |
| --- | --- |
| `./start.sh` / `./stop.sh` | `./agent-monitor start` / `./agent-monitor stop` |
| `./status.sh` | `./agent-monitor service status` |
| `./status.sh COMPONENT` | `./agent-monitor service status COMPONENT` |
| `./deploy.py ARGS` | `./agent-monitor service deploy ARGS` |
| `.venv/bin/python upstream_controls.py ARGS` | `./agent-monitor upstream-holds ARGS` |

Component names are `web`, `rollup-daemon` and `connector`; selectors retain their narrower status checks and exit status. Deployment arguments and admission checks remain in the deployment owner. `runtime_rebuild.py` remains available with an external interpreter for rescue when `.venv` is broken.

## Services and deployment

`./install.sh --no-services` installs dependencies/assets and CLI links without changing services or running the post-install public probe. `./install.sh` on macOS additionally configures hourly collection with `com.agent-monitor.rollup`; Linux skips that LaunchAgent. The default installer is therefore a service-changing action. `./agent-monitor service status rollup-daemon` reports the scheduler.

The standalone schedule runs `rollup` locally; manual `refresh` retains its existing dashboard/Hub behavior. Scheduler status distinguishes not installed, installed but not loaded, running, previous success/failure and unverified collection. A loaded job alone does not prove a successful collection. A launchctl query failure other than confirmed absence blocks modification and preserves the existing definition; inspect the printed diagnostics before retrying.

`hub MACHINE --no-services` is rejected before changing the live role marker. Stage assets without a role argument, then apply the role through the normal Hub installer. Changing a former Hub to a client removes only the old Hub job owned by this checkout; an unknown or foreign job is preserved and the installer fails. This does not retire a manually started local dashboard.

On a Hub client, `open` and `refresh` target the configured central Hub. Default `start`, `stop` and `restart` refuse to change a different local instance and direct you to the Hub host; `status` reports the role and central target without claiming availability. Use `./agent-monitor local start|stop|restart|status|open` for the client's local dashboard. On the Hub itself, default status inspects its launchd job and local HTTP deployment identity; the `local` selector is rejected to avoid operating on that Hub's process.

Service-managed updates use `./agent-monitor service deploy` from the primary, clean `main` checkout at the verified live `origin/main` commit. It checks source identity before calling the installer:

```bash
git fetch origin
git merge --ff-only origin/main
./agent-monitor service deploy --check-only --expect FULL_SHA
./agent-monitor service deploy --expect FULL_SHA
```

Replace `FULL_SHA` with the published commit you selected. `--check-only` does not install. A successful preflight is not service health: inspect status and the actual dashboard afterward. Deployment does not push, discard changes, migrate state or replace locks. Development worktrees are not persistent production roots. Preserve local edits and coordinate writers if preflight reports a dirty or diverged checkout.

Installation and service-management entrypoints share this checkout's persistent `state/deployment.lock`; a concurrent operation fails promptly. The installer captures its shell body and checks source/version at service boundaries and completion. Source drift stops further steps and reports partial completion; it does not roll back earlier changes. These checks coordinate the supported commands, not arbitrary edits or changes restored between checks. Keep using the same checkout/state and do not remove its lock to bypass a busy result. `--check-only` remains read-only and does not acquire/create this lock.

The installer/managed command has a 30-minute execution limit. On interruption or timeout it terminates its own process group and checks termination; inspect any reported remaining state before retrying. Browser restart requests also use this lock through the delayed restart, and return a conflict while an installation or another restart holds it.

For the configured macOS Hub and an already installed connector, installation compares source and selected configuration identity, reloads changed jobs, and checks the local HTTP endpoint for the target checkout and version. The polling budget is 30 seconds, plus at most two seconds for the final HTTP attempt. The connector retains its configured server and status-file path; without an existing installation it stays disabled. `--no-services` skips both convergence and these checks. Periodic collectors and CLI commands read updated source on their next execution.

`./agent-monitor service deploy` executes `agent_monitor.runtime.deploy` with its source checks and installer argument forwarding. Normal deployment updates an already-running owned local dashboard as well as managed components. It captures the local Web's full startup environment privately before stopping, retains its bind address and port, and checks the replacement's HTTP source/configuration identity. Matching processes are retained; a stopped optional Web is not started. `--no-services` and the targeted `rollup-daemon` installer do not update Web processes. Default `open` on a client still opens the central Hub; use `local open` for the local page.

Managed updates use stop/start and can interrupt requests or connector routes. Finish account operations before updating; uncertain sends are not automatically replayed. The installer preserves retained state and startup definitions after a failed replacement, reports nonzero, and does not claim that the old process is still available. Use `./agent-monitor service status`, `state/hub.log` and `./agent-monitor connect status` to locate the remaining failure before rerunning the same deployment entry. A source rollback is only appropriate when compatible with the retained data/configuration; this update does not migrate their schemas. Manually started standalone dashboards use [manual updates](#manual-updates).

`./agent-monitor service status` includes the role, central target (availability unchecked), owned local Web with HTTP identity, collector result and optional connector. Checks continue when one component fails and the overall exit is nonzero. A disabled optional component is not a failure. Use `service status web|rollup-daemon|connector` to inspect one component with its original narrower scope. `service restart local-web|hub|collector|connector` manages one component through its existing lifecycle owner. Local Web restart preserves startup conditions, including from the private recovery record if a replacement failed; first enablement still uses `local start`. Collector restart refuses to interrupt a running collection; completion must be checked afterward. Connector restart retains its existing semantics: old routes can fail and uncertain sends are not replayed.

`./uninstall.sh` selects `web`, `rollup-daemon` or `connector` and defaults to all three, including disabling connector autostart. It checks service ownership before stopping or removing integration, retains foreign or unrecognized definitions/loaded jobs, and removes only CLI links pointing to this checkout. It never removes state or persistent locks. A foreign component can fail removal while other owned components are still removed; the overall exit code is nonzero.

On Linux, `./agent-monitor open` uses `xdg-open` when a desktop session and that command are available. Otherwise it prints the dashboard URL and leaves the running server available.

For runtime observations and backend investigation, see [service diagnostics](operations/services.md) and [backend checks](public-architecture.md#backend-performance-checks).

## Linux sandbox setup

The Electron system-proxy helper requires a working Chromium sandbox even though it opens no browser window. On Linux systems that restrict unprivileged user namespaces, the downloaded helper may abort because its bundled `chrome-sandbox` is not a root-owned setuid executable. The installer reports this as `runtime_protocol_failed`; do not treat it as a provider login failure.

Ask the machine administrator to provide a protected sandbox helper compatible with the pinned Electron runtime. The actual runtime's `chrome-sandbox` path must resolve to this helper: Chromium prefers that adjacent file over `CHROME_DEVEL_SANDBOX`, so setting the environment variable alone does not replace the downloaded file. An administrator may install the helper in a protected directory and point the runtime's adjacent path to it with a symbolic link.

The resolved helper must be root-owned, executable and mode 4755; its target parent directories must be protected from unprivileged writes and its mount must permit execution and setuid. See [Chromium's Linux guidance](https://chromium.googlesource.com/chromium/src/+/main/docs/security/apparmor-userns-restrictions.md). Rerun `./install.sh --no-services` after the administrator prepares the sandbox; the installer checks actual startup before replacing services. Repeat this preparation if an update changes the pinned runtime directory. The installer does not elevate privileges or configure host security policy. Do not disable sandboxing to make installation pass. On disposable GitHub Linux runners, CI separately prepares and links a protected copy of the pinned Electron sandbox before running the normal installer; see the [CI sandbox decision](adr/20261027-b648-ci-linux-sandbox.md) and its [mount correction](adr/20261027-c291-ci-sandbox-mount.md).

## Python runtime rebuild

From the primary clean checkout at live `origin/main`, run `./agent-monitor service rebuild-runtime --python /absolute/path/to/python3`. The selected interpreter must be outside this checkout's `.venv` and at least Python 3.10. If the old interpreter cannot run the CLI, invoke `/absolute/path/to/python3 runtime_rebuild.py --python /absolute/path/to/python3` directly. `--expect FULL_SHA` pins the published release.

The command checks service ownership and account-operation locks before stopping services, refuses a running collector, records loaded launchd definitions and local Web startup conditions, and retains `.venv` under `.runtime/runtime-backups/rebuild-*`. For legacy Hub/connector script entries, the transaction records both the original definition and a module-based restoration definition before stopping anything; server, state-file and launch environment settings are preserved. It creates the replacement at the original `.venv` path using the staging installer, then restores only the original running/loaded components. State symlinks, persistent locks and local configuration are retained. Maintenance interrupts Web/connector availability; finish remote account sends first. It does not replay provider requests or validate provider availability.

On failure, restoration is attempted for every original component; the command exits nonzero even if rollback succeeds. If interrupted or restoration is incomplete, preserve the printed directory and rerun with `--restore rebuild-NAME` using the external interpreter, then inspect `service status` and the dashboard. Recovery requires the same published source revision. Backup environments are retained for explicit rollback, not executed from their backup path; after accepting the replacement, the operator may remove that specific backup directory. Private launch environments and transaction records may contain credentials: keep them private and never attach them to public issues.

## Troubleshooting

- Installer dependency errors: resolve the named Python/runtime/library error and rerun the installer. Missing optional fonts use system fallback and are reported as degraded; this is distinct from a failed proxy-runtime startup.
- Python older than 3.10: use the external-interpreter [runtime rebuild](#python-runtime-rebuild) entry; preserve state and local configuration.
- Chart.js download/checksum failure: rerun the installer. It validates the pinned asset on every install, downloads replacements to a temporary file, and publishes them only after verification; a failed download cannot become an installed asset.
- Empty usage: confirm the source directories, date filter and log presence. Test with the included sample in its own checkout if needed. Do not infer historical zero spend from missing files.
- Startup: inspect this checkout's `state/server.log` and `./agent-monitor status`. Share only sanitized excerpts; errors and paths can contain private metadata.
- Collection: use **采集与诊断** to find the affected machine and stage. An old snapshot can remain readable while a new collection attempt fails.
- Provider hold: `./agent-monitor upstream-holds status claude` (or `codex`) reads the local hold. After reviewing its reason, `resume PROVIDER --pause-id ID` acknowledges the specific current hold without sending a request. Account-specific restrictions remain separate.
- Network: `./agent-monitor network --json` runs public probes for the server host and can contact external diagnostic services. It is not an account-health test or a report of the browser device's IP. See [network diagnostic findings](operations/services.md#network-diagnostic-findings) for interpreting missing observations and checking the affected route.

Use [SECURITY.md](../SECURITY.md) for sensitive reporting. Do not attach raw state, credentials, account profiles or unredacted provider logs to issues.
