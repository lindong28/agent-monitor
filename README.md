# agent-monitor

Personal CLI and Web dashboard for Claude Code and Codex usage: tokens, estimated cost, model mix, session directories, sessions, quotas and network diagnostics. Run locally or aggregate declared machines through SSH with a MacStudio Hub.

Use it to answer: **How much coding-agent usage accumulated, which sessions contributed, which accounts have quota left, and whether machine collection is current?**

Usage accounting follows API-call observations across stored files: Codex pagination subtracts its inherited byte-boundary usage, and repeated Claude streaming observations keep the complete observed counters once. Missing ancestry or conflicting observations mark source coverage incomplete; they are not silently treated as zero. Corrected events replay retained authoritative history; unavailable or pre-authority history is not inferred. See [accounting correction](docs/usage-accounting-correction.md).

## Product definition

The independent [product spec](docs/product-spec.md) records target users, user tasks, expected outcomes, capabilities and product boundaries. It consolidates the existing definition for human alignment; it is not a new deployment acceptance result. See [Dashboard behavior](#dashboard-behavior) and [Codex account actions](#codex-account-actions) for usage details, and the [metric specification](docs/references/metrics.md) for cost, coverage and attribution semantics.

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

Manual refresh is labeled **更新统计与配额** on Overview, Explore and Sessions, **重新检测** on Network, and **更新采集状态** inside the Guide's status section. Explore keeps dates visible and marks the preset matching the current metric and groups; presets preserve dates and filters. Invalid dates replace the loaded status and query summary with validation feedback. New drilldowns start at the destination heading; manual detail pagination brings the new records into view while background refresh leaves the reader in place. Narrow detail cards keep amounts and counts intact. **重置筛选与排序** restores all providers, empty search, the 5h window and default ordering; it is hidden when all controls already have these defaults.

All pages share the Pulse-inspired visual layer: a white navigation rail with **控制台** and **使用与接入**, a light-gray canvas, white content panels and a full-width page banner showing the area and current page. On narrow screens the rail becomes a compact header and local navigation scrolls horizontally. **搜索页面与帮助** finds existing destinations and guide pages locally; it does not search account or session data. See [visual design and observed coverage](docs/design.md).

**使用与接入** has four independent pages: **数据来源** (`/guide/sources`), **采集状态** (`/guide/collection`), **首次接入** (`/guide/setup`) and **查找证据** (`/guide/questions`). Each has its own address and selected navigation item; browser refresh and Back/Forward retain the selected page. Old `/guide#chapter` links open the matching page with their query parameters preserved; `/guide` opens **数据来源**. Returning to **控制台** within the same page session restores the console address, including filters and session detail. Only **采集状态** queries machine collection status. Setup commands remain copyable and are never executed by the page; a readable machine snapshot does not prove every source or all historical data is present.

Overview's fixed week/month attribution links open Explore with the chart's actual dates. Explore ranking rows open Sessions with the selected dimensions and dates. Sessions filters match individual usage records before grouping: list totals and default detail represent that matching subset; the explicit all-retained switch widens detail. Custom dates include both endpoints in Asia/Shanghai and survive detail/return navigation. Aggregate history can exceed retained session detail; the two are not guaranteed to reconcile.

Expand **比较前后两个等长周期** to compare the latest N complete days (excluding today) with the preceding N days. Both windows show dates, recorded tokens and known cost subtotals. Empty data and known zero remain distinct; the difference is a known-cost subtotal, not an invoice or evidence of savings. Each period’s **透视此周期** link carries its exact dates into Explore for attribution. Coverage completeness remains unknown. All-history has no equal preceding period, and closed comparison does not request extra comparison reads.


Overview and Explore state the shared scope once: usage comes from collected logs; costs sum priceable records and are not invoices. **采集与诊断 → 费用记录诊断** groups currently readable unpriced observations by reason, machine and model, preserving source dates and filters. Entering the page or selecting **重新读取费用诊断** reads this diagnostic; ordinary status polling does not rescan usage. Each group links to all unknown-cost sessions for that machine/model, with a return link. The existing Explore `unknown_cost` metric remains available for aggregate analysis. Ranking rows continue to Sessions with `cost_status=unknown`; that filter applies before grouping, and detail initially uses the same filter; **查看全部留存记录** explicitly widens the scope. Unknown cost includes missing pricing and incomplete usage, not only unknown model names; known zero is excluded. Aggregate history and retained detail have different coverage, so their counts need not reconcile.

- **Overview / Explore:** totals, model mix and pivots; refreshing existing charts updates data without replaying their entrance animation. Range and selection remain visible during same-query refresh. Overview places the cost trend and project/model attribution before quota and account operations. The account-action section starts collapsed, with batch results visible in its summary; the adjacent account panel owns querying and account actions. Quota comparison and counts include identified accounts only; unidentified machine observations retain their original values and times on the collection diagnostics page (`/collection`), with applicable low-headroom warnings beside account comparison. Overview charts offer keyboard-accessible full data tables, complete identifiers and explicit USD/token units. Collection warnings stay visible.
- **Sessions:** In latest-usage order, dates can be expanded or collapsed; the newest date starts open. Each date keeps complete filtered conversation count, cost and token totals, including unpriced qualifications. A cross-day conversation belongs to its last matching usage date; these are conversation-group totals, not that calendar day's spending. Each expanded group starts with 100 rows and offers more on demand. Cost/token/span sorts keep their global paged list; returning to date order or from detail preserves the page session's disclosure state. One row represents a main conversation and its explicitly linked native Codex subagents, including nested subagents. Independent wrapper processes, ordinary forks and unresolved ancestry remain separate. Existing source titles identify Codex conversations; directory and latest usage time provide fallback. Click a conversation for matching cost/token totals, then expand session information, main/subagent usage or individual records. **查看全部留存记录** widens the detail scope; returning preserves the list filters and page. Costs sum priceable records, with partial/unknown/zero distinguished. “最近用量” is not running status. The “会话目录” filter still follows each usage record's starting directory and Git identity; it does not split work across every repository touched later. See [session metrics](docs/references/metrics.md#sessions).
- **Quota:** readings remain on the account that produced them. Codex members with the same workspace ID and different emails remain separate; saved account history has no automatic expiry or count cap. Signed-out machines are distinct from failed queries. Failed refreshes preserve old readings and their observation time.
- **Network:** `agent-monitor network`, `agent-monitor network --json` and `agent-monitor network --force` use the same report as the Web network page, which names the checked server host and does not describe the browser device. `ip-check` remains available independently; see [network remediation](NETWORK-REMEDIATION.md).

**Collection diagnostics** (`/collection`) separates machine collection, retained-history quality and unattributed machine quota observations from usage analysis. Coverage and freshness impact remain on Overview, Explore and Sessions. Contextual links preserve dates, filters and session detail in the return URL. Machine collection and retained-history quality counts cover all retained history, not the source date window; fee diagnostics uses the source window. Reloading does not launch collection. Hub fee diagnostics reads admitted snapshots; standalone mode follows Sessions by reading local sources and estimating with currently available prices without persisting usage.

Overview Refresh acknowledges promptly while machines update in the background. Each machine publishes statistics first, then refreshes quota without re-exporting statistics. The sync panel shows both stages and failures; a usable button does not mean all sources are current. Visible data pages refresh every 30 seconds and on focus/visibility return. Manual `agent-monitor refresh` waits for its requested round and reports incomplete results; if a round is already running, one additional round is queued. When the Web service is stopped, standalone refresh saves only local history and exits nonzero to report that cross-machine usage and quota were not refreshed.

Quota exploration supports All / Claude / Codex and account, plan or machine search. Counts distinguish current accounts, historical accounts and machine records with unknown identity. Filters and ordering remain during polling; clearing restores all records and the default order. Select the 5h/7d window and sort by most or least remaining quota, upcoming reset or latest observation; sorting uses the same final machine/Web observation shown in the row, keeps provider/current/history groups and places unknown values last. On narrow screens, each account presents its identity, quota windows, machines and observation time together vertically; desktop retains the table. Ordering helps select an account but does not establish that its saved login or quota is currently usable. Expired reset timestamps are not upcoming resets. Collection failures and signed-out notices remain visible regardless of filters. The page date range does not change provider quota windows. Codex displays reported 5h as well as 7d readings; an unreported window is unknown. An elapsed reset preserves the old observation and does not imply a new zero-percent reading.

Hub launchd bootstrap retries explicit nonzero failures at most three times, with a one-second pause between attempts. A timeout is not retried because the launch outcome is uncertain. After a failed replacement, the error reports whether the old job was unloaded; inspect the named job before another start. No automatic rollback or availability guarantee is implied.

## Local upstream pauses

Codex and Claude have independent local lanes under `state/upstream_controls`; processes using the same state share them. A crashed unfinished operation pauses on the next admission. Machine quota reads do not automatically retry, and Claude authentication refusal does not rotate to another token. Hub refreshes machines sequentially and stops the current combined-query round on failure, including delaying otherwise unrelated provider reads on later machines. Duplicate active requests for the same machines share that round. Autonomous collectors on different machines have no distributed lock.

To inspect a machine-only pause, run `.venv/bin/python upstream_controls.py status claude` (or `codex`) from that deployment. After reviewing the original failure and provider guidance, run `.venv/bin/python upstream_controls.py resume claude --pause-id ID`, using the current ID shown by status. A stale ID is rejected. Neither command contacts a provider; resuming preserves the prior pause and does not trigger a query. Codex account restrictions are separately managed in Web UI.

New machine quota failures retain the original stage, exception type and available HTTP/RPC status or process exit code in the pause record. Status and subsequent paused-query messages refer to that same pause ID. Legacy pauses report `原始诊断未记录`; upgrading cannot recover a missing historical cause. A skipped Hub machine names the earlier machine/provider that stopped the round; it did not make its own failed request. A recent quota observation from session logs can coexist with an active-query pause. See [failure investigation](docs/operations/services.md#failure-investigation) for the local evidence and correlation commands.

Public network diagnostics share one running probe and cache success or failure for at least 60 seconds in each Web server process, even with force refresh. A readable Retry-After extends that cache. Standalone CLI processes, other hosts and server restarts do not share this public cache. HTTP failures are not treated as successful JSON, redirects are not followed, and pricing TLS failures use bundled prices without disabling certificate verification.

## Stored Codex session quota

`agent-monitor session-quota FULL_SESSION_UUID` reads only that session's local rollout. Add `--json` for machine output. It reports the recorded seven-day percentage, reset Unix timestamp and observation time; it does not query a provider, infer account identity, or turn an elapsed reset into a new zero-percent observation. Exit 0 means a stored weekly reading was found, 1 means no matching rollout or usable weekly reading, and 2 means the input/source could not be verified. None proves the account's current quota. The public Python entry is `parsers.codex.load_session_rate_limits(session_id, sessions_dir=None)`; tests can supply an isolated sessions directory without reading the live store.

## Codex account actions

Compare quota in the table, then expand the adjacent **Codex 账号操作** panel and find the account by email (and workspace when names repeat). Use its **仅刷新配额** button for a quota-only request; the comparison table no longer duplicates account-operation shortcuts. Select one or more accounts and confirm **给所选 N 个账号发送**; the confirmation explains random selection from 100 short messages, email/workspace identities and quota use. **开始新一轮 · 所选 N 个账号** deliberately sends again, while **继续未发送账号** retains its existing recovery meaning. Each eligible card can start a one-account new round through the same batch guard. Selection and the expected previous batch are frozen together before confirmation. Accounts excluded from a new round retain their previous batch result and original timestamp, including after a later quota-only query.

See [the current action decision](docs/adr/20261017-82cb-task-oriented-web-actions.md).

Expand **Codex 账号操作** in the overview. Recorded current and historical identities appear automatically; identity metadata is not a credential. Use **单独登录** for a missing private login. Authorization only saves that login: it does not continue into a send or quota query. Select the next operation explicitly. Batch sends skip missing logins. Device codes and any email verification belong on the official OpenAI page; the serving machine needs a compatible Codex CLI.

Authentication refusal, rate limiting, an unknown send, or another unclassified upstream failure pauses subsequent local Codex operations, including machine quota reads. The current batch stops remaining accounts. After reviewing the original failure, account status and provider instructions, use **核对后解除本地暂停**. This only acknowledges local restrictions and makes no provider request; it does not prove recovery. Clearing saved credentials does not clear restrictions. Quota restrictions remain independent. Original failures are retained. No automatic device-code replacement, resend or recovery quota query is made.

The batch summary survives page refresh and service restart. Repeating the same creation request does not create another batch. **继续未发送账号** resumes only accounts still in the login directory and known not to have sent a message; successful, failed-after-send and uncertain attempts are not replayed. The previous round must be idle before a new round. A round's membership is fixed at creation; newly discovered accounts join the next round. Removed identities retain their current-batch results but are excluded from new rounds and retries. Missing-email identities are reported separately and excluded from the actionable count; unreadable history blocks discovery rather than silently presenting a partial list as complete. The application does not infer a reset cycle or send on a schedule.

Accounts run in email order, one complete operation at a time, including child-process cleanup. After identity verification and a successful pre-send quota read, agent-monitor independently selects one of [100 predefined Chinese everyday messages](codex_messages.py) with equal probability, sends it at most once, then reads quota. Examples include `你好！`, `现在几点了？` and `今天天气如何？`. Repeats across accounts or rounds are possible. The selected text is saved before transmission and appears in **账号与操作详情**, including retained batch results after quota-only refresh; selection alone does not prove delivery. Old records without saved text are not backfilled. The existing brief OK response instruction remains; this is not a weather or time-answering feature. See [the message selection decision](docs/adr/20261021-d39a-account-message-pool.md).

Sending consumes subscription usage. A target window showing 100% causes a precautionary skip, not a fabricated server refusal. The seven-day reset is the server-reported timestamp, never local time plus seven days; sending does not guarantee the window is fixed. Message outcome and quota outcome remain separate. Failed reads retain their original observation time. Cancellation or timeout cannot undo usage. See [the preventive interaction decision](docs/adr/20261021-6bd2-upstream-prevention.md).

Ordinary identity reads use `refreshToken=false`. The HTTP-only private sending alias sets `request_max_retries=0` and `stream_max_retries=0`; this does not establish that authentication, quota or model-catalog internals make only one HTTP attempt. Same-host operations sharing state use a persistent provider lock and pause record, with Web account operations waiting at least two seconds after the previous lease releases; machine quota admission does not add that delay. The 120-second send budget starts before pre-send quota, then covers config/thread/turn completion; queueing, initialization and identity checks precede it. Post-send quota has a separate 20-second bound. A timeout with known thread/turn IDs permits one bounded two-second interrupt before cleanup, not another message. Explicit server quota denial clears only after an explicit quota-only read affirmatively permits usage; 0%, a passed reset or missing permission cannot clear it. Safe categories, protocol progress and IDs remain in details; unavailable provider request IDs or Retry-After are not invented.

Each profile stores its managed login under `state/codex-accounts/<profile-id>/codex/auth.json`, separate from the ordinary CLI account. Later explicit operations reuse that login; **清除本页登录态** deletes only the selected profile's saved login. Profile directories have mode 0700 and credentials/records use 0600. Successful sends automatically query quota; the latest successful reading is saved independently of the current operation, so a later failed refresh or service restart does not erase it. Failed queries retain the original observation time; obtaining a current reading still requires a successful query. The quota table selects the newest observation for the same workspace and email from machine collection or Web actions. Expand **配额更新** to see the exact observation time and query provenance; machine membership and plans retain their collection provenance. Historical rows show **无在用机器** in the machine column, which does not imply the separately saved Web login is signed out. Action readings do not rewrite account history or export as machine snapshots. Compact account rows label the last successful query and show its observation time beside the quota, even when details are closed; a newer quota-table observation can therefore differ. Common authorization guidance lives once in batch help. The cards retain reset countdowns and exact reset timestamps in details; an expired timestamp says only that the recorded reset time has passed. This remains a trusted personal-network service without application authentication: anyone who can access it can operate saved accounts. Same-origin browser checks do not authenticate LAN clients. Do not expose it to an untrusted network.

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

Reviewed recovery previews a batch unless `--apply` is given and refuses changed blocker preconditions. It restores project attribution without clearing archives or rewriting protected history. Rollup shrink guards, timezone adoption, archived usage retention and cost arithmetic are unchanged. Codex fork accounting excludes inherited usage; metadata is read through private SQLite snapshots so source WAL files are not changed. See the [metric specification](docs/references/metrics.md) for cost, coverage and source-version limits. The [UX contract](docs/contracts/ux-contract.md) and [historical detailed behavior](docs/references/tt-web-readme-20261004.md) describe the preserved semantics.

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
