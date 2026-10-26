# agent-monitor

A personal CLI and Web dashboard for **Claude Code and Codex usage**. Explore recorded tokens and estimated costs, find sessions, compare account quota observations, and inspect collection or network failures. The dashboard currently uses Chinese labels.

agent-monitor reads local agent logs. It can also aggregate snapshots from machines you explicitly configure over SSH. It does not route model requests and does not require another project's services or configuration.

## Release status

The project's own code is MIT-licensed; incorporated third-party source retains its respective license. The canonical source is `https://github.com/lindong28/agent-monitor`; use this repository's `main` for development and deployment. The preparation candidate is not a second maintained fork. Installations based on the earlier history must follow the [existing-installation transition](docs/public-usage.md#transition-an-existing-installation) before updating; preserve their configuration and retained data. See [LICENSE](LICENSE), [third-party notices](THIRD-PARTY-NOTICES.md) and [contributing](CONTRIBUTING.md).

## Install with your coding agent

Paste this prompt into your coding agent:

```text
Install agent-monitor from https://github.com/lindong28/agent-monitor in a new checkout. Read its README and follow the supported installation and first-use instructions. Help me inspect usage totals and open a session in the dashboard. Ask before accessing my real agent logs or provider account state, including automatic quota queries; otherwise use the documented isolated sample. Preserve any existing configuration and data. Report what worked, the source revision, how to open and stop it, and any missing prerequisites or unverified optional features.
```

The prompt can be used before cloning. The repository's instructions contain the operating details; no maintainer-specific skills or accounts are required.

## Requirements

- Python 3.10 or newer with `venv` and `pip`, Git, curl and `nc` (netcat).
- macOS or Linux on x86-64 or ARM64. macOS supports the optional LaunchAgents. Linux needs the shared libraries required by Electron; the installer reports missing libraries. Windows is not supported by the current lifecycle scripts.
- Internet access during installation for Python packages, Chart.js, IBM Plex fonts and the pinned Electron system-proxy runtime. Downloads go to this checkout's `.venv`, `web/vendor` and `.runtime`.
- Your own Claude Code/Codex logs for real usage. Provider CLIs, logins and subscription access are optional for account/quota actions. SSH and rsync are optional for remote machines.

The core log-analysis flow can run without a provider login or a model call. The installer starts the proxy helper briefly to check it, but `--no-services` does not install background services or run public network probes.

## First installation

```bash
git clone https://github.com/lindong28/agent-monitor agent-monitor
cd agent-monitor
./install.sh --no-services
```

The installer creates CLI links in `~/.local/bin`; it does not add that directory to your shell's PATH. Commands below use `./agent-monitor` so they also work before changing PATH. On first installation, `AGENT_MONITOR_PYTHON=/path/to/python3 ./install.sh --no-services` selects a Python interpreter.

Existing environments must also use Python 3.10 or newer. An older `.venv` is preserved and installation stops with recovery instructions; selecting another interpreter does not silently replace it. See [runtime recovery](docs/public-usage.md#troubleshooting).

Using your real HOME lets the monitor read your local agent logs and current provider account state. Opening the dashboard, including `./agent-monitor open`, can automatically query current-account quota using an existing login. Proceed only if you authorize both the local reads and quota queries described in [usage and data](docs/public-usage.md); otherwise use the isolated sample below. Then run:

```bash
AGENT_MONITOR_BIND=127.0.0.1 ./agent-monitor start
./agent-monitor status
./agent-monitor open
```

Open the address printed by `start`. In **总览**, inspect recorded tokens and estimated cost. Open **会话**, select **全部** for the date range, then select a session to see its usage records. Missing logs produce an empty view; they do not mean zero historical spending. Account login and sending messages are separate optional actions and are not required for this flow.

The source default binds `0.0.0.0`, with port 39001 or the next free port. The local-only command above uses loopback. **The dashboard has no client authentication.** Keep it off the public Internet and untrusted networks; read [SECURITY.md](SECURITY.md) before sharing network access. `AGENT_MONITOR_PORT` selects a different starting port.

### Try the included sample without your accounts

Use a separate checkout that has never contained real state. The sample has two fabricated Claude usage observations in one session on January 15, 2026: 1,500 input tokens and 300 output tokens. It contains no credentials and cannot validate live quotas or provider login.

```bash
demo_home="$(mktemp -d)"
cp -R examples/demo-home/. "$demo_home/"
HOME="$demo_home" CLAUDE_CONFIG_DIR="$demo_home/.claude" CODEX_HOME="$demo_home/.codex"   AGENT_MONITOR_BIND=127.0.0.1 ./agent-monitor start
./agent-monitor status
```

Open the printed URL and choose **总览 → 全部** to view historical cost including the sample date. Then choose **会话 → 全部** and open the row labeled `~/example-project`, which shows 1,800 total tokens in the list and session details. In the details, expand **用量记录** to inspect the two observations and **会话标识与记录范围** to confirm the ID `demo-session`; that ID is not the list label.

This is the normal parser and dashboard flow with fabricated input. Automatic quota refresh still runs, but the sample has no signed-in account metadata; unavailable quota is expected. Do not click provider login/send or network diagnostics for this sample check. The server can attempt a public pricing refresh and falls back to bundled prices, so this is not an offline mode; no model request is needed. Stop with `./agent-monitor stop` before deleting the disposable sample HOME or starting with another HOME. State belongs to this checkout, so changing HOME alone does not clear earlier collected data.

## Configuration and updates

A new public candidate starts with one local machine and Hub mode disabled. For custom machine names or SSH targets, copy `examples/machines.json` to **`machines.local.json`** and edit that file. For an optional Hub, similarly use **`hub.local.json`**. The local file takes precedence over its corresponding tracked default at process startup. Invalid local configuration fails visibly; it does not fall back to another fleet. Restart after adding or removing a local file.

Only one machine has `self: true`. Keep names, SSH identities and `retired_names` stable once data has been collected. Do not copy the sample over an existing configuration. The installer preserves local files, existing `state` (including symlinks) and persistent locks. Local configuration and runtime data are ignored by Git. See [configuration and retained data](docs/public-usage.md#configuration-and-retained-data) for migration and multi-machine details.

To update a public candidate installed from `main`, stop the service, preserve a backup of state and local configuration, and fast-forward the same checkout. Do not discard local source changes to make an update pass:

```bash
./agent-monitor stop
git pull --ff-only
./install.sh --no-services
AGENT_MONITOR_BIND=127.0.0.1 ./agent-monitor start
```

For a service-managed deployment, use the existing [published-main deployment entry](docs/public-usage.md#services-and-deployment). Reinstallation does not migrate machine identities or erase retained history. `./uninstall.sh` removes owned service/CLI integration while retaining source and state.

## Services

On macOS, the installer manages the periodic collector and the configured Hub. An optional browser-device connector is enabled explicitly with `./agent-monitor connect install --server URL`; later installations update it while preserving its server address and state path. A fresh installation leaves the connector disabled. `--no-services` leaves all services unchanged. Linux retains the CLI and manually started dashboard.

Standalone scheduled collection runs local `rollup`, independently of whether a dashboard is running. Hub machines retain their configured `collect` schedule. Changing a Hub role with `hub MACHINE --no-services` is rejected; stage with `./install.sh --no-services`, then apply the role with `./install.sh hub MACHINE`.

Use `./agent-monitor service status` (also `./status.sh`) for the role, central target, local Web, collector and optional connector. `service restart local-web|hub|collector|connector` selects one component; `service deploy` uses the published-main deployment entry. Deployment also updates an already-running local Web, retaining its bind address, port and environment; a stopped local Web stays stopped. For Python replacement, use `service rebuild-runtime --python /absolute/path/to/python3`; see [rebuild and recovery](docs/public-usage.md#python-runtime-rebuild).

Existing dashboard and connector commands remain compatible. `./uninstall.sh` removes this checkout's service integration and CLI links; `web`, `rollup-daemon` and `connector` select one component. Foreign or unrecognized service ownership is preserved and reported. Saved data and locks remain.

On a Hub client, `open` and `refresh` target the central Hub; default Web management commands direct you to that host. To keep a local dashboard on the client, use `./agent-monitor local start|stop|restart|status|open`. Installation preserves an already running local dashboard. The `local` selector does not manage the Hub, collector or connector.

Published-main deployment restarts managed processes when their source or selected configuration changes and checks their local HTTP identity. This can interrupt active operations: let account operations finish first, and never replay an uncertain send. An unchanged, confirmed process is retained. A failed replacement returns nonzero with its remaining state; inspect status and logs before retrying. See [deployment and recovery](docs/public-usage.md#services-and-deployment) for the supported entry and verification limits.

## What the numbers mean

Usage is derived from retained local observations, not provider invoices. Known costs are subtotals for priceable observations; unknown price or incomplete usage is not zero. Codex inherited pagination usage and repeated Claude streaming observations are deduplicated under the existing accounting rules. Aggregate history can outlive detailed session records. Quotas are observations with their own timestamps; an expired reset does not prove fresh quota is available. Date boundaries use Asia/Shanghai.

See [usage, optional features and troubleshooting](docs/public-usage.md) and [architecture](docs/public-architecture.md). For development, use [CONTRIBUTING.md](CONTRIBUTING.md).

## Attribution

Created by Dong Lin. agent-monitor succeeds the `tt-web` implementation extracted from ai-agent-config commit `3d61487d5386dfe9bb6ba7d26807005bbf0bd7f2`. That provenance does not create a runtime dependency. Third-party packages retain their own licenses.
