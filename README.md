# agent-monitor

[![CI](https://github.com/lindong28/agent-monitor/actions/workflows/ci.yml/badge.svg)](https://github.com/lindong28/agent-monitor/actions/workflows/ci.yml)

A personal CLI and Web dashboard for **Claude Code and Codex usage**. Explore recorded tokens and estimated costs, find sessions, compare account quota observations, and inspect collection or network failures. The dashboard currently uses Chinese labels.

agent-monitor reads local agent logs. It can also aggregate snapshots from machines you explicitly configure over SSH. It does not route model requests and does not require another project's services or configuration.

Use it to answer: **Which project or session used these tokens, and what can the retained records tell me about its cost?** Start with **总览 (Overview)**, open **会话 (Sessions)**, then inspect a session's **用量记录 (Usage records)**. Costs are estimates from recorded usage, not provider invoices; quotas are timestamped observations, not a guarantee that a request will succeed.

**Run it on your own computer or a trusted network.** The dashboard has no client authentication. The quick-start commands below bind to `127.0.0.1`; plain `./agent-monitor start` defaults to all network interfaces. Do not expose it directly to the public Internet. For a first look without accessing your accounts, follow the [included sample](#try-the-included-sample-without-your-accounts).

The included fabricated sample shows how one session's 1,800 tokens break down into two usage records:

![Session details with 1,800 tokens and two fabricated usage records](docs/images/demo-session.png)

<details>
<summary>See the sample cost overview</summary>

![Overview with the fabricated sample's historical estimated cost](docs/images/demo-overview.png)

</details>

## Release status

The public source is [lindong28/agent-monitor](https://github.com/lindong28/agent-monitor). Use `main` for development and the supported deployment entry; [releases](https://github.com/lindong28/agent-monitor/releases) identify tested source snapshots and their verification limits. The project's own code is MIT-licensed; incorporated third-party source retains its respective license. See [LICENSE](LICENSE), [third-party notices](THIRD-PARTY-NOTICES.md) and [contributing](CONTRIBUTING.md). Installations from the earlier private history must follow the [existing-installation transition](docs/public-usage.md#transition-an-existing-installation) before updating.

## Install with your coding agent

Paste this prompt into your coding agent:

```text
Install agent-monitor from https://github.com/lindong28/agent-monitor in a new checkout. Read its README and follow the supported installation and first-use instructions. Help me inspect usage totals and open a session in the dashboard. Ask before accessing my real agent logs or provider account state, including automatic quota queries; otherwise use the documented isolated sample. Preserve any existing configuration and data. Report what worked, the source revision, how to open and stop it, and any missing prerequisites or unverified optional features.
```

The prompt can be used before cloning. The repository's instructions contain the operating details; no maintainer-specific skills or accounts are required.

## Requirements

- Python 3.10 or newer with `venv` and `pip`, Git, curl and `nc` (netcat).
- macOS or Linux on x86-64 or ARM64. macOS supports the optional LaunchAgents. Linux needs Electron's shared libraries and a usable Chromium sandbox; see [Linux sandbox setup](docs/public-usage.md#linux-sandbox-setup). The installer reports missing libraries. Windows is not supported by the current lifecycle scripts.
- Internet access during installation for Python packages, Chart.js, IBM Plex fonts and the pinned Electron system-proxy runtime. Downloads go to this checkout's `.venv`, `web/vendor` and `.runtime`.
- Your own Claude Code/Codex logs for real usage. Provider CLIs, logins and subscription access are optional for account/quota actions. SSH and rsync are optional for remote machines.

The core log-analysis flow can run without a provider login or a model call. The installer starts the proxy helper briefly to check it, but `--no-services` does not install background services or run public network probes.

## First installation

Choose **one** path before running the installer: [isolated sample](#try-the-included-sample-without-your-accounts) for a first look, or [your own data](#use-your-own-data) for regular use. Both need a new Git clone: collection verifies committed source identity, which GitHub's downloadable ZIP/tar archives do not contain. Existing private installations must use the [transition guide](docs/public-usage.md#transition-an-existing-installation).

The installer creates or updates `agent-monitor` and `ip-check` links under the selected HOME's `.local/bin`, even with `--no-services`. An ordinary installation can replace links to another checkout. It does not add that directory to PATH; commands below use `./agent-monitor`. Use `AGENT_MONITOR_PYTHON=/path/to/python3` on the installation command to select Python 3.10+; an older existing `.venv` is preserved and requires [runtime recovery](docs/public-usage.md#python-runtime-rebuild).

### Try the included sample without your accounts

Use this separate checkout only for fabricated data. Its HOME stays in the ignored `.runtime/demo-home` directory so installation, restart and update can reuse the same account-free environment. Neither installation nor startup uses your normal HOME's CLI links or agent stores.

```bash
git clone https://github.com/lindong28/agent-monitor agent-monitor-demo
cd agent-monitor-demo
demo_home="$PWD/.runtime/demo-home"
mkdir -p "$demo_home"
cp -R examples/demo-home/. "$demo_home/"
HOME="$demo_home" CLAUDE_CONFIG_DIR="$demo_home/.claude" CODEX_HOME="$demo_home/.codex" \
  ./install.sh --no-services
```

Start or restart the sample from this checkout using the same environment:

```bash
demo_home="$PWD/.runtime/demo-home"
HOME="$demo_home" CLAUDE_CONFIG_DIR="$demo_home/.claude" CODEX_HOME="$demo_home/.codex" \
  AGENT_MONITOR_BIND=127.0.0.1 ./agent-monitor start
./agent-monitor status
```

The sample has two fabricated Claude usage observations in one session on January 15, 2026: 1,500 input tokens and 300 output tokens. It contains no credentials and cannot validate live quotas or provider login.

Open the printed URL and choose **总览 → 全部** to view historical cost including the sample date. Then choose **会话 → 全部** and open the row labeled `~/example-project`, which shows 1,800 total tokens in the list and session details. In the details, expand **用量记录** to inspect the two observations and **会话标识与记录范围** to confirm the ID `demo-session`; that ID is not the list label.

This is the normal parser and dashboard flow with fabricated input. Automatic quota refresh still runs, but the sample has no signed-in account metadata; unavailable quota is expected. Do not click provider login/send or network diagnostics for this sample check. The server can attempt a public pricing refresh and falls back to bundled prices, so this is not an offline mode; no model request is needed. Stop with `./agent-monitor stop` before removing this disposable installation. Keep its HOME for any restart or update; use the same sample command above. State belongs to this checkout, so changing HOME alone does not clear earlier collected data. Do not turn a sample checkout into a real-data installation; use a separate checkout for that.

### Use your own data

Using your real HOME lets the monitor read local agent logs and current provider account state. Opening the dashboard, including `./agent-monitor open`, can automatically query current-account quota using an existing login. Proceed only if you authorize these reads and queries; otherwise use the sample above. Account login and sending messages are separate optional actions.

```bash
git clone https://github.com/lindong28/agent-monitor agent-monitor
cd agent-monitor
./install.sh --no-services
AGENT_MONITOR_BIND=127.0.0.1 ./agent-monitor start
./agent-monitor status
./agent-monitor open
```

Open the address printed by `start`. In **总览**, inspect recorded tokens and estimated cost. Open **会话**, select **全部** for the date range, then select a session to see its usage records. Missing logs produce an empty view; they do not mean zero historical spending.

The source default binds `0.0.0.0`, with port 39001 or the next free port; these examples bind only to loopback. `AGENT_MONITOR_PORT` selects a different starting port. Record any custom HOME, source overrides, bind address and port with your installation instructions so updates can reuse them. See [usage and data](docs/public-usage.md#local-data-and-first-results).

## Configuration and updates

A new installation starts with one local machine and Hub mode disabled. For custom machine names or SSH targets, copy `examples/machines.json` to **`machines.local.json`** and edit that file. For an optional Hub, similarly use **`hub.local.json`**. The local file takes precedence over its corresponding tracked default at process startup. Invalid local configuration fails visibly; it does not fall back to another fleet. Restart after adding or removing a local file.

Only one machine has `self: true`. Keep names, SSH identities and `retired_names` stable once data has been collected. Do not copy the sample over an existing configuration. The installer preserves local files, existing `state` (including symlinks) and persistent locks. Local configuration and runtime data are ignored by Git. See [configuration and retained data](docs/public-usage.md#configuration-and-retained-data) for migration and multi-machine details.

Choose the update path **before stopping anything**: [manual updates](docs/public-usage.md#manual-updates) preserve the sample or real-data startup environment; [service-managed deployment](docs/public-usage.md#services-and-deployment) preserves managed process settings. Do not run the ordinary real-data startup command against a sample checkout. Reinstallation does not migrate machine identities or erase retained history.

## Common operations and services

| Task | Entry |
| --- | --- |
| Inspect or stop a manually started dashboard | `./agent-monitor status` / `./agent-monitor stop` |
| Inspect all owned components | `./agent-monitor service status` (also `./status.sh`) |
| Enable scheduled collection or a macOS Hub | [Service setup and component operations](docs/public-usage.md#services-and-deployment) |
| Aggregate your configured machines | [Multi-machine setup](docs/public-usage.md#optional-multi-machine-collection) |
| Use optional account or browser-device actions | [Account and connector guide](docs/public-usage.md#optional-quota-and-account-actions) |
| Remove owned service/CLI integration | `./uninstall.sh`; source, state and locks remain. See [ownership and selectors](docs/public-usage.md#services-and-deployment) |

`--no-services` leaves services unchanged. On macOS, normal installation manages scheduled collection and the configured Hub; the connector is opt-in. Linux retains the CLI and manually started dashboard. A Hub client normally opens the central dashboard; the [local selector](docs/public-usage.md#services-and-deployment) manages an optional local instance. Managed updates can interrupt active operations: let account operations finish first and never replay an uncertain send. Component restart, recovery and runtime replacement details live in the linked guide.

## What the numbers mean

Usage is derived from retained local observations, not provider invoices. Known costs are subtotals for priceable observations; unknown price or incomplete usage is not zero. Codex inherited pagination usage and repeated Claude streaming observations are deduplicated under the existing accounting rules. Aggregate history can outlive detailed session records. Quotas are observations with their own timestamps; an expired reset does not prove fresh quota is available. Date boundaries use Asia/Shanghai.

## Repository and documentation

```text
agent-monitor / install.sh / deploy.py   CLI, installation and deployment entries
*.py                                    Backend modules; grouped by responsibility in the map below
parsers/                                Claude/Codex log and account-source readers
web/                                    Static dashboard pages, JavaScript and styles
proxy_runtime/                          System-proxy resolver helper
ip_check/                               Network diagnostics and attribution
examples/                               Public configuration examples and fabricated sample
scripts/ / tests/                       Explicit acceptance, measurements and regression tests
docs/                                   Usage, architecture, operations and accounting guides
```

| Reader task | Start here |
| --- | --- |
| Configure, update or troubleshoot | [Usage and operations](docs/public-usage.md) |
| Find the module and regression for a change | [Responsibility map](docs/public-architecture.md#responsibility-map) |
| Set up development and choose checks | [Contributing and validation](CONTRIBUTING.md) |
| Understand accounting and parser boundaries | [Accounting contracts](docs/references/metrics.md) |
| Report a sensitive issue | [Security policy](SECURITY.md) |

`.venv/`, `.runtime/`, `web/vendor/`, `state/` and `*.local.json` are local runtime/configuration, not tracked source. Preserve existing state symlinks and identities during updates.

## Attribution

Created by Dong Lin. agent-monitor succeeds the `tt-web` implementation extracted from ai-agent-config commit `3d61487d5386dfe9bb6ba7d26807005bbf0bd7f2`. That provenance does not create a runtime dependency. Third-party packages retain their own licenses.
