# agent-monitor

Personal CLI and Web dashboard for Claude Code, Codex and LLM Gateway usage: tokens, estimated cost, model mix, session directories, sessions, quotas and network diagnostics. Run locally or aggregate declared machines through SSH with a MacStudio Hub.

This is the independent successor to `tt-web`, extracted from ai-agent-config commit `3d61487d5386dfe9bb6ba7d26807005bbf0bd7f2` on 2026-10-04. The CLI, Web UI, backend, network tools, tests and operational documents live here. Runtime code and installation do not require an ai-agent-config checkout. [Extraction decision](docs/adr/20261004-a902-standalone-project.md) and [historical feature/deployment record](docs/references/tt-web-readme-20261004.md) preserve provenance; historical measurements are not new deployment checks.

## Install

Prerequisites: Python 3 with `venv`/`pip`, Git, curl, and an independent [llm-gateway checkout](https://github.com/lindong28/llm-gateway) at `~/research/llm-gateway` (or set `LLM_GATEWAY_ROOT`). SSH and rsync are required for remote collection. macOS supports launchd scheduling; Linux installs the portable CLI and Web service and reports a scheduling skip. The test suite requires Python 3.10 or newer and Node. Set `AGENT_MONITOR_PYTHON=/path/to/python3` on first installation to choose the interpreter that creates the project environment.

```bash
git clone git@github.com:lindong28/agent-monitor.git ~/research/agent-monitor
cd ~/research/agent-monitor
./install.sh
```

The idempotent installer creates this project's `.venv`, installs [requirements.txt](requirements.txt), fetches Chart.js 4.4.0 and IBM Plex Sans/Mono assets, and links `agent-monitor` and the secondary `ip-check` CLI into `~/.local/bin`. It never chooses a parent repository's environment. Python falls back to `python3` only when this project's environment is absent; `agent-monitor status` reports its interpreter. Missing fonts report a degraded installation and use system fonts until installation is retried.

Default macOS installation installs the `com.agent-monitor.rollup` LaunchAgent, running `agent-monitor refresh` every 3600 seconds. As before, `INSTALL_SERVICES=0` from a parent installer does not disable this default schedule. For deployment staging, use the explicit mode:

```bash
./install.sh --no-services
```

Staging installs the same runtime, assets and CLI links, but does not load, stop or replace services and skips the post-install network probe. It still changes the CLI links in the selected HOME. `INSTALL_LEDGER_FD=3` optionally reports the existing four-column installation ledger to a parent installer; no parent library is loaded.

## Run and services

```bash
agent-monitor --help
agent-monitor start
agent-monitor open
agent-monitor status
agent-monitor refresh
agent-monitor stop
```

The default local address is `http://127.0.0.1:39001`, listening on `0.0.0.0`; standalone mode advances to a free port when needed and saves it in `state/port`. The equivalent lifecycle wrappers are [start.sh](start.sh), [stop.sh](stop.sh), [status.sh](status.sh) and [uninstall.sh](uninstall.sh). Uninstall retains state and source. See [service operations](docs/operations/services.md) before changing an existing deployment.

| Service | Default behavior | Status and log |
| --- | --- | --- |
| Standalone Web | `agent-monitor start`, manual PID-file lifecycle | `agent-monitor status`, `state/server.log` |
| Local collector | `com.agent-monitor.rollup`, hourly refresh or Hub `collect` every 120 seconds | `./status.sh rollup-daemon`, `state/rollup-daemon.log` |
| MacStudio Hub | `com.agent-monitor.hub`, fixed port 39001 | `agent-monitor status`, `state/hub.log` |

Explicit Hub setup on each participating Mac:

```bash
./install.sh hub macbook
./install.sh hub macmini
./install.sh hub macstudio
```

Run only the role for that host. The local marker is `state/hub-machine`; [hub.json](hub.json) sets roles, Hub URL, the 120-second statistics interval and hourly quota interval. The collector saves local data independently of the center. MacStudio's Hub pulls snapshots; other Macs open its URL. Use an interactive shell for installation so PATH and a custom SSH agent socket are available. Temporary Apple launchd socket paths are not persisted; the current GUI session provides those after login. The existing desktop-login requirement remains.

## Dashboard behavior

- **Overview / Explore:** totals, model mix and pivots; refreshing existing charts updates data without replaying their entrance animation. Range and selection remain visible during same-query refresh.
- **Sessions:** retained Claude/Codex sessions and usage. The UI calls the project dimension “会话目录”: attribution follows the session's starting directory and Git identity, not every project touched later in the conversation. Grouping keys and the `project` API parameter remain unchanged.
- **LLM Calls:** Filter Gateway records by machine, project, model and account, then open a request ID to inspect its complete attempt chain without losing the list context. Routing diagnostics separate caller intent and pin resolution from the first attempt, same-route retries and route switches. Expand historical candidates to compare eligibility, reasons and corresponding attempt evidence; missing values remain unknown and history does not imply current readiness. Raw audit fields, cost breakdown and the independently paginated attempt table remain expandable. Export either all matching requests or all matching attempts as JSON. Detail and export each show their own snapshot observation; they are not a frozen continuation of the list. The independent Gateway audit reader supplies `/api/llm-calls-page`, `/api/llm-call-request` and `/api/llm-calls-export`; Gateway owns credentials, inference and its separate Web UI.
- **Quota:** readings remain on the account that produced them. Codex members with the same workspace ID and different emails remain separate; saved account history has no automatic expiry or count cap. Signed-out machines are distinct from failed queries. Failed refreshes preserve old readings and their observation time.
- **Network:** `agent-monitor network`, `agent-monitor network --json` and `agent-monitor network --force` use the same report as the Web network page. `ip-check` remains available independently; see [network remediation](NETWORK-REMEDIATION.md).

Overview Refresh acknowledges promptly while machines update in the background. Each machine publishes statistics first, then refreshes quota without re-exporting statistics. The sync panel shows both stages and failures; a usable button does not mean all sources are current. Visible data pages refresh every 30 seconds and on focus/visibility return. Manual `agent-monitor refresh` waits for its requested round and reports incomplete results; if a round is already running, one additional round is queued. When the Web service is stopped, standalone refresh saves only local history and exits nonzero to report that cross-machine usage and quota were not refreshed.

Quota exploration supports All / Claude / Codex and account, plan or machine search. Counts distinguish current accounts, historical accounts and machine records with unknown identity. Filters remain during polling; clearing restores all records. Collection failures and signed-out notices remain visible regardless of filters. The page date range does not change provider quota windows.

## Codex account actions

Use **Codex 账号操作** in the overview's quota section. Current and historical Codex login identities already recorded by agent-monitor appear automatically; no manual account addition is required. Click **给全部 N 个账号发送** to send one message to those accounts. The quota-row **登录/发消息** action locates its account card. Saved valid logins in this panel are reused; historical identity metadata is not a credential. Accounts needing authorization show an official OpenAI link and device code at the front of the list; their wait does not block other accounts. Any email verification code belongs on that official page. Device-code login must be enabled in the account or workspace security settings. The serving machine needs a Codex CLI supporting `chatgptDeviceCode` and experimental thread/turn `environments` (interface checked locally with 0.158.0).

The batch summary survives page refresh and service restart. Repeating the same creation request does not create another batch. **继续未发送账号** resumes only accounts still in the login directory and known not to have sent a message; successful, failed-after-send and uncertain attempts are not replayed. After the next reset, click **开始新一轮 · 全部 N 个账号** to deliberately send again to the current directory. The previous round must be idle first. A round's membership is fixed at creation; newly discovered accounts join the next round. Removed identities retain their current-batch results but are excluded from new rounds and retries. Missing-email identities are reported separately and excluded from the actionable count; unreadable history blocks discovery rather than silently presenting a partial list as complete. The application does not infer a reset cycle or send on a schedule. Individual send buttons remain available for current-directory accounts outside the batch; batch members use batch controls to prevent accidental repetition.

After checking the signed-in email and workspace, agent-monitor sends exactly one fixed message, `Please reply with OK.`, and reads the account's quota. This consumes subscription usage. The next seven-day reset is the server's reported timestamp; missing data is unknown, never computed as the local time plus seven days. **仅刷新配额** reads quota without sending another message. Cancellation, timeout or a lost connection do not undo usage; an uncertain send is never retried automatically.

Each profile stores its managed login under `state/codex-accounts/<profile-id>/codex/auth.json`, separate from the ordinary CLI account. Later explicit operations reuse that login; **清除本页登录态** deletes only the selected profile's saved login. Profile directories have mode 0700 and credentials/records use 0600. The quota table selects the newest observation for the same workspace and email from machine collection or Web actions. Web observations are labeled **本页查询**; machine membership and plans retain their collection provenance. Action readings do not rewrite account history or export as machine snapshots. The cards show both a countdown and the reported timestamp; an expired timestamp says only that the recorded reset time has passed. This remains a trusted personal-network service without application authentication: anyone who can access it can operate saved accounts. Same-origin browser checks do not authenticate LAN clients. Do not expose it to an untrusted network.

Design and validation boundaries are recorded in the [account actions decision](docs/adr/20261007-c814-codex-account-actions.md) and [batch decision](docs/adr/20261007-b923-codex-account-batches.md). Local tests do not establish a real account's reset behavior or deployment readiness.

## Machines and retained data

[machines.json](machines.json) declares SSH targets. Standalone mode requires exactly one enabled `self: true` machine; Hub mode selects self from the local role marker and restricts targets to `hub.json.machines`. The shipped fleet declares macbook, macmini, macstudio and tencent-webserver-china; dgx0023 is commented out. These are configuration defaults, not a reachability claim. Update them for another installation.

Each remote needs this project, a compatible independent Gateway reader, non-interactive SSH from the Hub, and `~/.local/bin/agent-monitor`. After declaring a new target, bind its source identity explicitly:

```bash
agent-monitor machines accept NAME
agent-monitor export --version
agent-monitor export --out /path/to/new-bundle
```

Snapshot export requires a clean committed runtime: Python modules (including `claude_oauth.py`), parsers, pricing, launcher, installer, requirements and installer library. Web-only, documentation and machine-declaration edits do not change exporter authority. Snapshots preserve source identity, digest checks, leases and versioned schema. Both Hub and exporters need Gateway schema-v8-compatible readers; unknown fields remain rejected.

Comment a whole machine line with `#` or `//` to pause it; existing snapshots and bindings remain available when re-enabled. Saving the declaration is sufficient for the next sync and page read. Permanent `agent-monitor machines retire NAME` is a separate irreversible action that reserves the name. An offline machine retains its last admitted generation and is shown with its original observation time.

Local state remains `state/`. For a managed deployment it may be a symlink to `~/.local/share/agent-monitor/state`. Preserve the existing database, host identity, account history, generations and persistent lock inode during migration. Never copy a live database or start both old and new collectors against one state directory. The migration sequence is in [operations](docs/operations/services.md).

Rollup maintenance commands remain available:

```bash
agent-monitor rollup
agent-monitor rollup --check
agent-monitor rollup blockers
agent-monitor rollup recover --help
agent-monitor rollup recover-reviewed --help
agent-monitor rollup adopt-timezone --help
```

Reviewed recovery previews a batch unless `--apply` is given and refuses changed blocker preconditions. It restores project attribution without clearing archives or rewriting protected history. Rollup shrink guards, timezone adoption, archived usage retention and cost arithmetic are unchanged. Codex fork accounting excludes inherited usage; metadata is read through private SQLite snapshots so source WAL files are not changed. Cost is an estimate at configured model rates, not a subscription charge or an invoice. The [UX contract](docs/contracts/ux-contract.md) and [historical detailed behavior](docs/references/tt-web-readme-20261004.md) describe the preserved semantics.

## Configuration and development

| Setting | Meaning |
| --- | --- |
| `AGENT_MONITOR_BIND` / `AGENT_MONITOR_PORT` | Standalone listen address / starting port |
| `AGENT_MONITOR_PYTHON` | Explicit interpreter override for the CLI; the default is this project's `.venv/bin/python` |
| `AGENT_MONITOR_ROLLUP_INTERVAL_SECONDS` | Installer override for the local collection schedule |
| `AGENT_MONITOR_EXTRA_JSONL` | Additional Claude usage sources, using the existing parser contract |
| `LLM_GATEWAY_ROOT` | Independent Gateway source checkout; defaults to `~/research/llm-gateway` |
| `CLAUDE_CONFIG_DIR`, `CODEX_HOME` | Existing provider input locations where supported by the readers |

`claude_oauth.py` reads file/keychain credentials and dates locally. It never refreshes tokens, logs them, writes them to state, or follows HTTP redirects; OAuth tokens go only in request headers. Quota requests remain bounded child processes. Statusline rendering and its cache are owned by the harness, not this project.

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
LLM_GATEWAY_ROOT=/path/to/llm-gateway .venv/bin/python -m unittest discover -s tests
```

Use a disposable HOME when testing to isolate credentials and user files. Tests use temporary databases and mocked services; live deployment and browser acceptance are separate checks. [Architecture](docs/architecture.md), [documentation index](docs/AGENTS.md) and [changelog](CHANGELOG.md) are the maintenance entry points. `token_cost.py` retains the original byte-identical cross-project arithmetic contract, including its historical naming comments.
