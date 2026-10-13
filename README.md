# agent-monitor

Personal CLI and Web dashboard for Claude Code and Codex usage: tokens, estimated cost, model mix, session directories, sessions, quotas and network diagnostics. Run locally or aggregate declared machines through SSH with a MacStudio Hub.

Use it to answer: **How much coding-agent usage accumulated, which sessions contributed, which accounts have quota left, and whether machine collection is current?**

Usage accounting follows API-call observations across stored files: Codex pagination subtracts its inherited byte-boundary usage, and repeated Claude streaming observations keep the complete observed counters once. Missing ancestry or conflicting observations mark source coverage incomplete; they are not silently treated as zero. Corrected events replay retained authoritative history; unavailable or pre-authority history is not inferred. See [accounting correction](docs/usage-accounting-correction.md).

## Who it is for

- Developers who use Claude Code and Codex frequently, especially across multiple models, accounts and computers.
- Engineers maintaining a personal AI development environment who need one place to inspect collection status, usage history, quotas and connectivity.

The current product is a personal monitoring console. Organization permissions, team budget enforcement and enterprise governance are outside its documented capabilities.

## Use cases and capabilities

| Use case | Question | Available capability |
| --- | --- | --- |
| Review daily or weekly usage | How much did I use, and how is it changing? | Overview totals, token usage, estimated cost, trends and model mix |
| Attribute consumption | Which model, machine or session directory accounts for the usage? | Explore filters and pivots, plus retained Claude/Codex sessions and their usage |
| Monitor several computers | How do laptop, desktop and server activity add up? | Local collection and SSH snapshot aggregation into one Hub dashboard |
| Manage account quotas | Which account is near its limit, when does it reset, and how old is the reading? | Claude/Codex account quotas, reset times, account history, observation times and refresh failures |
| Operate Codex accounts | Can I authorize an account, send a message or refresh its quota? | Explicit single-account and batch actions, queued device authorization and retained operation results |
| Check connectivity | What does the current network diagnostic report show? | Shared Web and CLI diagnostics through `agent-monitor network` and `ip-check` |

The CLI also manages start, stop, status and refresh. Periodic collection and manual refresh retain usable history when a machine is offline, with source times and failures visible. See [Dashboard behavior](#dashboard-behavior) for the page workflows and [Codex account actions](#codex-account-actions) for action semantics.

Cost is an estimate at configured model rates, not a subscription charge or an invoice. Data comes from collection rounds and snapshots; check observation times before treating a reading as current. The “session directory” dimension follows the session's starting directory and Git identity, not every repository touched later. Agent session records support usage review and tracing prior work; they are not API request/attempt ledgers.

## Project status

Open-source preparation is in progress. The license and publication history scope have not been selected, and a clean adopter installation has not yet been verified as part of this preparation. See the [preparation checklist](docs/references/open-source-preparation.md) for concrete gaps and the [contribution guide](CONTRIBUTING.md) for development and reporting instructions.

This is the independent successor to `tt-web`, extracted from ai-agent-config commit `3d61487d5386dfe9bb6ba7d26807005bbf0bd7f2` on 2026-10-04. The CLI, Web UI, backend, network tools, tests and operational documents live here. Runtime code and installation do not require an ai-agent-config checkout. [Extraction decision](docs/adr/20261004-a902-standalone-project.md) and [historical feature/deployment record](docs/references/tt-web-readme-20261004.md) preserve provenance; historical measurements are not new deployment checks.

## Install

Prerequisites: Python 3 with `venv`/`pip`, Git and curl. SSH and rsync are required for remote collection. macOS supports launchd scheduling; Linux installs the portable CLI and Web service and reports a scheduling skip. The test suite requires Python 3.10 or newer and Node. Set `AGENT_MONITOR_PYTHON=/path/to/python3` on first installation to choose the interpreter that creates the project environment.

Monitor runs independently of llm-gateway. It does not collect Gateway calls, require a Gateway checkout, or expose a Gateway navigation entry. Existing installations should follow the [history-preserving cutover](docs/product-boundary-separation.md) before upgrading source exporters.

The following clone command requires repository access. Before running the installer, configure [machines.json](machines.json) for your own installation: the shipped entries name the maintainer's fleet. For a new standalone installation, use exactly one local entry, for example `{"machines":[{"name":"local","ssh_host":"localhost","self":true}],"retired_names":[]}`. This uses the existing configuration format; do not replace the identities or retirement records of an existing installation. Hub setup additionally requires your own [hub.json](hub.json) roles, URL and SSH targets. See [Machines and retained data](#machines-and-retained-data).

The direct installer below supports local setup. For production updates, publish the intended changes and use [the published-main entrypoint](#production-deployment-from-published-main). The personal configuration repository's Monitor adapter uses that entrypoint by default; explicit `--no-services` retains staging behavior.

```bash
git clone git@github.com:lindong28/agent-monitor.git ~/research/agent-monitor
cd ~/research/agent-monitor
# Configure machines.json for your installation before continuing.
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

Operate the dashboard on a trusted personal network. Account-action origin checks are not client authentication, and the dashboard exposes usage and account metadata. Do not expose the service directly to an untrusted network or the public Internet. Publishing the source repository and exposing a running dashboard are separate actions.

Restart and SIGTERM shutdown stop admitting account operations, cancel active account work and reap the owned child processes. Restart is refused if account cleanup cannot be confirmed. The CLI waits up to ten seconds for graceful shutdown before its existing forced-stop fallback; it returns sooner when the server exits. Interrupted messages are never automatically replayed. To clear children left by an older execution, use a full `agent-monitor stop` followed by `agent-monitor start` after checking account work is idle.

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

The sidebar's **使用与接入** page explains sources, installation prerequisites and current machine collection status. It is a read-only guide; a readable machine snapshot does not prove every source or all historical data is present.

Overview's fixed week/month attribution links open Explore with the chart's actual dates. Explore ranking rows open Sessions with the selected dimensions and dates. Sessions filters match individual usage records before grouping: list totals represent that matching subset, while a session detail retains all available records. Custom dates include both endpoints in Asia/Shanghai and survive detail/return navigation. Aggregate history can exceed retained session detail; the two are not guaranteed to reconcile.

Expand **比较前后两个等长周期** to compare the latest N complete days (excluding today) with the preceding N days. Both windows show dates, recorded tokens, known cost and unknown-pricing entries. Empty data and known zero remain distinct; the difference is a known-cost subtotal, not an invoice or evidence of savings. Coverage completeness remains unknown. All-history has no equal preceding period, and closed comparison does not request extra comparison reads.


- **Overview / Explore:** totals, model mix and pivots; refreshing existing charts updates data without replaying their entrance animation. Range and selection remain visible during same-query refresh. Overview places the cost trend and project/model attribution before quota and account operations. The account-action section starts collapsed, with batch results visible in its summary; quota-row actions still open the matching account. Overview charts offer keyboard-accessible full data tables, complete identifiers and explicit USD/token units. Collection warnings stay visible.
- **Sessions:** retained Claude/Codex sessions and usage. The UI calls the project dimension “会话目录”: attribution follows the session's starting directory and Git identity, not every project touched later in the conversation. Grouping keys and the `project` API parameter remain unchanged.
- **Quota:** readings remain on the account that produced them. Codex members with the same workspace ID and different emails remain separate; saved account history has no automatic expiry or count cap. Signed-out machines are distinct from failed queries. Failed refreshes preserve old readings and their observation time.
- **Network:** `agent-monitor network`, `agent-monitor network --json` and `agent-monitor network --force` use the same report as the Web network page, which names the checked server host and does not describe the browser device. `ip-check` remains available independently; see [network remediation](NETWORK-REMEDIATION.md).

Overview Refresh acknowledges promptly while machines update in the background. Each machine publishes statistics first, then refreshes quota without re-exporting statistics. The sync panel shows both stages and failures; a usable button does not mean all sources are current. Visible data pages refresh every 30 seconds and on focus/visibility return. Manual `agent-monitor refresh` waits for its requested round and reports incomplete results; if a round is already running, one additional round is queued. When the Web service is stopped, standalone refresh saves only local history and exits nonzero to report that cross-machine usage and quota were not refreshed.

Quota exploration supports All / Claude / Codex and account, plan or machine search. Counts distinguish current accounts, historical accounts and machine records with unknown identity. Filters and ordering remain during polling; clearing restores all records and the default order. Select the 5h/7d window and sort by least remaining quota, upcoming reset or latest observation; sorting uses the same final machine/Web observation shown in the row, keeps provider/current/history groups and places unknown values last. Expired reset timestamps are not upcoming resets. Collection failures and signed-out notices remain visible regardless of filters. The page date range does not change provider quota windows.

Hub launchd bootstrap retries explicit nonzero failures at most three times, with a one-second pause between attempts. A timeout is not retried because the launch outcome is uncertain. After a failed replacement, the error reports whether the old job was unloaded; inspect the named job before another start. No automatic rollback or availability guarantee is implied.

## Stored Codex session quota

`agent-monitor session-quota FULL_SESSION_UUID` reads only that session's local rollout. Add `--json` for machine output. It reports the recorded seven-day percentage, reset Unix timestamp and observation time; it does not query a provider, infer account identity, or turn an elapsed reset into a new zero-percent observation. Exit 0 means a stored weekly reading was found, 1 means no matching rollout or usable weekly reading, and 2 means the input/source could not be verified. None proves the account's current quota. The public Python entry is `parsers.codex.load_session_rate_limits(session_id, sessions_dir=None)`; tests can supply an isolated sessions directory without reading the live store.

## Codex account actions

Expand **Codex 账号操作** in the overview's quota section. Current and historical Codex login identities already recorded by agent-monitor appear automatically; no manual account addition is required. Click **给全部 N 个账号发送** to send one message to those accounts. The quota-row **登录/发消息** action locates its account card. Saved valid logins in this panel are reused; historical identity metadata is not a credential. Accounts needing authorization queue for one official OpenAI link and device code at a time; completion or cancellation admits the next account. Queued accounts show **等待授权排队** and do not consume the check/send slots, so saved logins continue working. Any email verification code belongs on that official page. Device-code login must be enabled in the account or workspace security settings. The serving machine needs a Codex CLI supporting `chatgptDeviceCode` and experimental thread/turn `environments` (interface checked locally with 0.158.0).

If OpenAI rate-limits device authorization, the current attempt stops and pending authorizations stop before requesting more codes. The page explains the **429** failure; an ended operation's code cannot complete that operation and is removed. After waiting, click **继续未发送账号** to explicitly retry the unsent members. Unknown authorization failures are shown without guessing that device-code login is disabled. Codes are not automatically replaced, and successful or uncertain sends are never automatically repeated. Serial authorization reduces simultaneous polling; it cannot guarantee that the provider has lifted a rate limit.

The batch summary survives page refresh and service restart. Repeating the same creation request does not create another batch. **继续未发送账号** resumes only accounts still in the login directory and known not to have sent a message; successful, failed-after-send and uncertain attempts are not replayed. After the next reset, click **开始新一轮 · 全部 N 个账号** to deliberately send again to the current directory. The previous round must be idle first. A round's membership is fixed at creation; newly discovered accounts join the next round. Removed identities retain their current-batch results but are excluded from new rounds and retries. Missing-email identities are reported separately and excluded from the actionable count; unreadable history blocks discovery rather than silently presenting a partial list as complete. The application does not infer a reset cycle or send on a schedule. Individual send buttons remain available for current-directory accounts outside the batch; batch members use batch controls to prevent accidental repetition.

After checking the signed-in email and workspace, agent-monitor sends exactly one fixed message, `Please reply with OK.`, and reads the account's quota. This consumes subscription usage. The next seven-day reset is the server's reported timestamp; missing data is unknown, never computed as the local time plus seven days. **仅刷新配额** reads quota without sending another message. Cancellation, timeout or a lost connection do not undo usage; an uncertain send is never retried automatically. Failed or uncertain sends make one bounded quota-only recovery attempt using the saved identity; failure reasons and message outcomes remain separate from quota results. These isolated account operations use HTTP directly, without changing ordinary CLI settings. Successful pre-send observations also remain visible if the send or later query fails. See the [recovery decision](docs/adr/20261011-7c3a-account-round-recovery.md).

Each profile stores its managed login under `state/codex-accounts/<profile-id>/codex/auth.json`, separate from the ordinary CLI account. Later explicit operations reuse that login; **清除本页登录态** deletes only the selected profile's saved login. Profile directories have mode 0700 and credentials/records use 0600. Successful sends automatically query quota; the latest successful reading is saved independently of the current operation, so a later failed refresh or service restart does not erase it. Failed queries retain the original observation time; obtaining a current reading still requires a successful query. The quota table selects the newest observation for the same workspace and email from machine collection or Web actions. Web observations are labeled **本页查询**; machine membership and plans retain their collection provenance. Action readings do not rewrite account history or export as machine snapshots. Compact account rows label the last successful query and show its observation time beside the quota, even when details are closed; a newer quota-table observation can therefore differ. Common authorization guidance lives once in batch help. The cards retain reset countdowns and exact reset timestamps in details; an expired timestamp says only that the recorded reset time has passed. This remains a trusted personal-network service without application authentication: anyone who can access it can operate saved accounts. Same-origin browser checks do not authenticate LAN clients. Do not expose it to an untrusted network.

Design and validation boundaries are recorded in the [account actions decision](docs/adr/20261007-c814-codex-account-actions.md) and [batch decision](docs/adr/20261007-b923-codex-account-batches.md). Local tests do not establish a real account's reset behavior or deployment readiness.

## Machines and retained data

[machines.json](machines.json) declares SSH targets. Standalone mode requires exactly one enabled `self: true` machine; Hub mode selects self from the local role marker and restricts targets to `hub.json.machines`. The shipped fleet declares macbook, macmini, macstudio and tencent-webserver-china; dgx0023 is commented out. These are configuration defaults, not a reachability claim. Update them for another installation.

Each remote needs this project, non-interactive SSH from the Hub, and `~/.local/bin/agent-monitor`. After declaring a new target, bind its source identity explicitly:

```bash
agent-monitor machines accept NAME
agent-monitor export --version
agent-monitor export --out /path/to/new-bundle
```

Snapshot export requires a clean committed runtime: Python modules (including `claude_oauth.py`), parsers, pricing, launcher, installer, requirements and installer library. Web-only, documentation and machine-declaration edits do not change exporter authority. Snapshots preserve source identity, digest checks, leases and versioned schema. Statistics extension v2 carries agent usage only. The Hub still reads agent usage from retained v1 snapshots; upgrade the Hub reader before source exporters. Existing history is not deleted.

Comment a whole machine line with `#` or `//` to pause it; existing snapshots and bindings remain available when re-enabled. Saving the declaration is sufficient for the next sync and page read. Permanent `agent-monitor machines retire NAME` is a separate irreversible action that reserves the name. An offline machine retains its last admitted generation and is shown with its original observation time. Overview Refresh starts quota queries independently of statistics synchronization and shows quota progress beside the account table. Successful readings appear as each machine finishes; failures retain the last reading and its original time. An account identity change still waits for a validated statistics snapshot.

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
| `CLAUDE_CONFIG_DIR`, `CODEX_HOME` | Existing provider input locations where supported by the readers |

`claude_oauth.py` reads file/keychain credentials and dates locally. It never refreshes tokens, logs them, writes them to state, or follows HTTP redirects; OAuth tokens go only in request headers. Quota requests remain bounded child processes. Statusline rendering and its cache are owned by the harness, not this project.

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests
```

Use a disposable HOME when testing to isolate credentials and user files. Tests use temporary databases and mocked services; live deployment and browser acceptance are separate checks. [Architecture](docs/architecture.md), [documentation index](docs/AGENTS.md) and [changelog](CHANGELOG.md) are the maintenance entry points. `token_cost.py` retains the original byte-identical cross-project arithmetic contract, including its historical naming comments.

For backend cost diagnosis, run `.venv/bin/python scripts/benchmark_backend.py`. It creates isolated synthetic snapshots and reports admission, preparation and aggregate-query work across sizes and generations. Read [the diagnostic scope and regression entry](docs/proactive-performance.md); these measurements do not establish production latency or an SLA.

## Production deployment from published main

Use `./deploy.py` for a production installation using the current published release. It checks the primary checkout, `main`, tracked and untracked source changes, unfinished Git operations, and the live `origin/main` SHA before invoking the existing `install.sh`. Runtime `state` and ignored runtime assets are preserved. `--check-only` performs no installation; `--expect FULL_SHA` also binds the node to the release selected after publication.

```sh
git fetch origin
git merge --ff-only origin/main
./deploy.py --check-only --expect FULL_SHA
./deploy.py --expect FULL_SHA
```

Replace `FULL_SHA` with the full commit verified after authorized publication. Finish and publish changes in a development worktree first; a diverged or dirty deployment checkout needs its owner's resolution. Coordinate writers throughout deployment. This check is not a filesystem lock, and direct `install.sh` remains available for development/staging. Verify the installed runtime and actual consumer separately; a successful source check is not a service-health result.

Installer arguments follow `--`, for example `./deploy.py --expect FULL_SHA -- rollup-daemon`. Keep `./install.sh --no-services` for isolated staging. The production entrypoint does not push, synchronize Git, migrate state or replace persistent locks.

The preflight rejects `assume-unchanged` and `skip-worktree` index flags, including on files whose content currently matches HEAD. On rejection, inspect `git ls-files -v`, preserve any local edits and clear the flags before retrying. The entrypoint never clears them or discards edits automatically.

For live slow-operation and stalled-update diagnosis, read `/api/diagnostics` or the Hub's `state/diagnostics/current.json` and rotating `events.jsonl`. The [operations guide](docs/operations/services.md#runtime-performance-diagnostics) explains thresholds, coverage, recovery and remaining external-monitoring limits. Session date/detail queries use indexes in newly exported snapshots; older snapshots remain readable.
