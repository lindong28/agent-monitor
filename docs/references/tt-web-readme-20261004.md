> Historical source snapshot: ai-agent-config `3d61487d5386dfe9bb6ba7d26807005bbf0bd7f2`, `tt-web/README.md`, extracted 2026-10-04. Commands, names, deployment identities and measurements below describe tt-web before extraction. Current commands live in the project [README](../../README.md). Only relative links were converted to source-commit permalinks.

# tt-web

Personal dashboard for reviewing Claude Code and Codex token usage, cost, model mix, session directories, sessions, and LLM Gateway calls. It can run as the existing standalone localhost service or as an opt-in three-Mac hub served by MacStudio.

Codex quota rows distinguish members by both workspace account ID and email. Different emails sharing an ID retain separate readings and history; deleting one historical member leaves the others intact. The history list has no account-count cap or automatic expiry. Existing v1 account memory is read using each saved record's own email and saved as v2 on the next write. Older server versions cannot display v2 history; reverting code alone does not downgrade that file. This migration preserves retained records and does not reconstruct previously overwritten readings.

Overview, Explore, and Sessions label the existing project dimension as “会话目录”: attribution follows the session's starting directory, displayed using its Git repository identity when available. Work on other projects within the same session is not split or reassigned. Historical data, grouping keys, and the `project` API/URL parameter remain unchanged. The LLM Calls page retains its separate Gateway project attribution.

Codex model metadata is queried from a private writable SQLite snapshot so WAL sidecars can be initialized without touching the source database. If metadata cannot be read, collection reports an incomplete scan and retains archived Codex records instead of replacing their models with the default. Other agents continue to update; Codex parsing resumes after metadata recovers.

Codex fork accounting excludes inherited cumulative usage from the first event and preserves the rollout's original session identity when copied parent metadata follows it. A normal collection reparses changed parser versions and corrects retained events in place; authoritative historical day buckets are rebuilt from those retained statistics. Missing source files and legacy days before archive authority remain protected. Codex costs are estimates at the identified model's configured rates, not subscription charges or actual invoices.

## Refresh behavior

Overview and Explore update existing charts without replaying their entrance animation when data refreshes. Changing the chart type or navigating to a new page still creates the appropriate chart. Data polling and collection intervals are unchanged.

On Overview, Refresh acknowledges the request and keeps the saved data usable while machines update in the background. Each machine publishes statistics first, then refreshes quota without exporting statistics again. The sync panel distinguishes collection from quota lookup; quota failures keep the previous reading and its timestamp. Button availability is not a claim that all sources are current. Remote exporters must support `tt-web export --quota-only`; upgrade them with the Hub. Unchanged published snapshots reuse an in-process validation result, invalidated by database, metadata or WAL file changes; publication still performs full validation.

## Gateway dependency

The LLM page uses Gateway's Python `audit.llm_calls_page` owner to prepare sources once and return the existing filters and calls envelopes together. tt-web exposes `/api/llm-calls-page` for initial loads and range reloads; the original calls and filters routes remain available. Selecting or clearing filters while a page read is pending also reloads its options; stale responses and background observers cannot replace the newer selection. This requires the updated Gateway Python package alongside the tt-web consumer, not a new Gateway daemon HTTP route. The change passed independent implementation review and is deployed with explicit owner approval: MacStudio tt-web `bca665a0` and Gateway reader `7eda44aa`. The inference Gateway was not restarted. Production sample timings and remaining verification boundaries are recorded in [operations](https://github.com/lindong28/ai-agent-config/blob/3d61487d5386dfe9bb6ba7d26807005bbf0bd7f2/tt-web/docs/operations/services.md). One read-only four-source comparison measured separate preparation at 6.1963 seconds and combined preparation at 3.7367 seconds, with identical complete responses; this is not browser timing or a production rollout result.

Gateway snapshots through schema v8 require both this consumer's versioned field mapping and a compatible independent Gateway audit reader on the hub. Schema v8 preserves `caller_route_constraint_json`; an older tt-web exporter rejects the new row shape with `invalid gateway row fields`, retaining the machine's last admitted data. Upgrade the affected exporter and Hub reader together. Unknown fields remain rejected, and this compatibility update leaves Claude Code and Codex subscription usage records unchanged.

LLM Calls uses the independent `llm-gateway` checkout at `~/research/llm-gateway`; set `LLM_GATEWAY_ROOT` when it lives elsewhere. `gateway_dependency.py` locates its Python package and `llm_attempts.py` imports its audit reader. Gateway owns its registry, credentials, inference service, and standalone Web UI; it does not load this repository.

The exporter no longer pins the Gateway source (the `gateway-runtime.sha256` digest check was removed on 2026-09-16; it coordinated versions but did not guard correctness, and it refused fleet exports after every Gateway commit). The dashboard still includes the independent package files in its source-drift check. Hub-side snapshot validation relies on the Gateway reader's ledger schema fingerprints instead. Cross-machine snapshots and machine identity remain tt-web responsibilities. Each exporter host needs a compatible independent checkout as well as this consumer. These source changes alone do not deploy either service.

## Install

```bash
./tt-web/install.sh
```

The installer is idempotent. It creates `state/` and `web/vendor/`, downloads pinned Chart.js `4.4.0` and the IBM Plex Sans/Mono web fonts the UI is set in, and links `tt-web` into `~/.local/bin/`.
Fonts that cannot be fetched are reported as `degraded` rather than failing the install — the pages still render, in the system font stack. Re-run the installer once the network allows it; it re-fetches anything missing, and also anything truncated or corrupt.
It also links `ip-check` into `~/.local/bin/` for terminal network diagnostics.
On macOS it also installs and loads the hourly refresh LaunchAgent by default, including when invoked from the repo-root installer with `INSTALL_SERVICES=0`. The job keeps the existing `com.ttweb.rollup` label and 3600-second interval but calls `tt-web refresh`: when the dashboard is running it requests or joins the dashboard's cross-machine sync, and when it is stopped the job updates only the local rollup, reports that cross-machine usage and Quota were not refreshed, and exits non-zero. A matching loaded schedule is reused without restarting it. Other platforms report an explicit platform skip for the schedule while still installing the portable CLI. Loading a schedule does not certify its last run; inspect `./tt-web/status.sh rollup-daemon` and `state/rollup-daemon.log`.

Hub installation is explicit on every participating Mac:

```bash
./tt-web/install.sh hub macbook
./tt-web/install.sh hub macmini
./tt-web/install.sh hub macstudio
```

Run only the command matching that machine. The role is stored locally in `state/hub-machine`; shared [`hub.json`](https://github.com/lindong28/ai-agent-config/blob/3d61487d5386dfe9bb6ba7d26807005bbf0bd7f2/tt-web/hub.json) names `http://macstudio:39001`, the three participating roles, the 120-second statistics interval, and the 3600-second Quota interval. A normal install without `hub <role>` keeps the existing standalone behavior. Run a hub deployment from a real interactive zsh so the installer can carry its `PATH` and any custom `SSH_AUTH_SOCK` into launchd, including the fixed 1Password SSH agent socket. Apple launchd's temporary `com.apple.launchd.*/Listeners` paths are not saved: the hub inherits the current GUI session's socket when it starts after login. After updating an older installation, run `tt-web start` on MacStudio once to migrate its plist; later reboots do not require reinstalling it. This remains a user LaunchAgent, so desktop login and usable SSH credentials are required.

## Run

The detail-read optimization in [ADR 72ab](https://github.com/lindong28/ai-agent-config/blob/3d61487d5386dfe9bb6ba7d26807005bbf0bd7f2/docs/adr/20260928-72ab-ttweb-admitted-detail-reads.md) was deployed as `fa102015` from source `20b608ed`; the historical measurements below precede the current combined page reader deployment recorded in [operations](https://github.com/lindong28/ai-agent-config/blob/3d61487d5386dfe9bb6ba7d26807005bbf0bd7f2/tt-web/docs/operations/services.md). Hub Sessions reads only usage from an admitted generation and prefilters existing query dimensions; LLM Calls reads only Gateway metadata and request/attempt data. Full import validation and per-read admission, digest, identity, and lease checks remain. A single before/after run per query on the same four admitted sources and query time measured Sessions 7d at 8.5711 → 0.8079 seconds, LLM Calls 7d/page 50 at 11.1784 → 3.8367 seconds, and filters 7d at 10.1868 → 3.0953 seconds, with identical full-response hashes. These timings exclude admission, ran old then new without cold-disk isolation, and are not browser or deployed-service measurements. Before deployment, an isolated local three-source Sessions page showed readable content in 0.7771 seconds. Subsequent production Sessions 7d navigation showed 773 sessions and readable content in 6.1222 and 4.4283 seconds; expanding one session with 24 usage rows took 0.3884 seconds. Production LLM Calls first content took 10.1106 seconds, and a valid machine=macbook filter took 3.4364 seconds. At that measurement stage, Gateway full-history reads and sequential frontend filters/calls requests both contributed cost; the current page wrapper removes the duplicate preparation, while full-history reads still cost time. these page timings are separate from the endpoint-only A/B and do not support an instant-load claim. The separate MacMini 41-path attribution investigation is recorded in [operations](https://github.com/lindong28/ai-agent-config/blob/3d61487d5386dfe9bb6ba7d26807005bbf0bd7f2/tt-web/docs/operations/services.md): retained usage is present in the archive and Hub snapshot, project attribution was still blocked at that time. The subsequent reviewed batch has now resolved all 41 active blockers without rebuilding all protected history; current results are in the operations record.

Progressive refresh was first deployed with explicit owner approval as `287a6aa0` from `69deac92`; the following historical bounded production verification covered one journey on each of the four data pages. Overview showed new statistics at about 38.5 seconds while the button was busy, then returned to idle at about 198 seconds, including a queued round. Sessions and LLM detail reads were slow in those samples; subsequent detail-reader and combined-page changes are documented above. Deployment identity and coverage limits are recorded in [ADR 4f72](https://github.com/lindong28/ai-agent-config/blob/3d61487d5386dfe9bb6ba7d26807005bbf0bd7f2/docs/adr/20260928-4f72-ttweb-progressive-freshness.md). It keeps admitted generations as the only data source. Visible Overview, Explore, Sessions, and LLM Calls pages reread their current view every 30 seconds and on focus/visibility return, preserving range, filters, and paging; Sessions clamps the page when fewer results remain. During a sync, a generation change triggers a read without waiting for every machine. The Hub retains its 120-second statistics interval, measured from round start with a 10-second scheduling check and no overlapping rounds. The original active refresh used two complete statistics exports and held the button until global idle. [ADR 6d2f](https://github.com/lindong28/ai-agent-config/blob/3d61487d5386dfe9bb6ba7d26807005bbf0bd7f2/docs/adr/20261003-6d2f-ttweb-async-refresh.md) replaces those waits with per-machine quota-only collection and Overview acknowledgement; automatic Quota stays hourly, and manual Refresh plus CLI `open`/`refresh` still request active Quota. Source age and per-machine failures remain visible.

```bash
tt-web start
tt-web open
tt-web status
tt-web stop
```

Default URL is `http://127.0.0.1:39001`; if that port is occupied, the CLI increments to the next free port and writes it to `state/port`.

In hub mode, `tt-web open` always opens the MacStudio URL from `hub.json`. MacStudio runs `com.ttweb.hub` on `0.0.0.0:39001`; the port is fixed rather than incremented. `tt-web refresh` asks that center for an immediate real-time export and waits for it. If a collection round was already active before the request, the center schedules one more round so completion covers data produced after the click. An offline source keeps its previous admitted generation and is shown with the old observation time and failure state.

`tt-web` is symlinked into `~/.local/bin` (usable from any directory). For the repo's uniform service-ops convention (see [`service-operations-protocol.md`](https://github.com/lindong28/ai-agent-config/blob/3d61487d5386dfe9bb6ba7d26807005bbf0bd7f2/claude/references/service-operations-protocol.md)), the equivalent entry points `./tt-web/{start,stop,status,uninstall}.sh` wrap the same dispatcher; `./tt-web/uninstall.sh` stops the server, removes the owned hub LaunchAgent and the `~/.local/bin` symlinks, and retains collected data.

## Machines

### Reviewed project assignment recovery

`tt-web rollup recover-reviewed --manifest <file>` previews an explicitly reviewed source-path/project assignment batch; only adding `--apply` atomically writes project identities and resolves matching blockers. Each assignment must match the original blocker preconditions, and any mismatch refuses the whole batch. Preview does not initialize state and refuses a nonempty WAL rather than reading an incomplete database image. Apply does not clear the archive, rewrite historical rollup rows, or change historical authority. Later normal refresh retains existing history protections, so identity recovery does not mean all historical project totals have been rebuilt. The original strict-pin recovery and automatic inference remain unchanged. See [ADR c934](https://github.com/lindong28/ai-agent-config/blob/3d61487d5386dfe9bb6ba7d26807005bbf0bd7f2/docs/adr/20260928-c934-reviewed-project-assignment.md). Independent implementation review passed and the approved MacMini production batch has been applied: 41 assignments matched, active blockers became zero, and archive/rollup bytes were unchanged by apply. The subsequent normal rollup added 21,863 entries to 46 buckets dated September 1–13; 38,587 entries remain frozen under existing history protections. The recovered SJTU project is visible in the real Explore page; the subsequently requested refresh completed (requested=completed=1), with four successful source attempts and the next automatic statistics round continuing. Full deployment, backup and verification boundaries are in [operations](https://github.com/lindong28/ai-agent-config/blob/3d61487d5386dfe9bb6ba7d26807005bbf0bd7f2/tt-web/docs/operations/services.md).

### Machine declaration

The dashboard runs on one machine and reports usage for every machine you
declare. It reaches each remote over SSH, asks it to export a snapshot of its
own usage, and merges those snapshots into one view. `All` on any page means
"every machine currently admitted", and the page says which those are.

[`machines.json`](https://github.com/lindong28/ai-agent-config/blob/3d61487d5386dfe9bb6ba7d26807005bbf0bd7f2/tt-web/machines.json) is the single pull-target declaration. Keep one machine per line; prefix the whole line with `#` or `//` to stop pulling it and hide it from the dashboard and statistics. Indentation before the comment marker and trailing commas are supported; inline comments are not. Standalone requires exactly one enabled `self: true` machine. Hub mode additionally limits this list to `hub.json.machines` and selects self from the local installation marker; that local machine must remain enabled. SSH aliases and retirement continue to come from `machines.json`.

```jsonc
{
  "machines": [
    { "name": "macbook", "ssh_host": "macbook", "self": true },
    { "name": "macmini", "ssh_host": "macmini", "self": false },
    { "name": "macstudio", "ssh_host": "macstudio", "self": false },
    // { "name": "dgx0023", "ssh_host": "dgx0023", "self": false },
  ],
  "retired_names": []
}
```

The shipped configuration pauses dgx0023 and enables macbook, macmini, macstudio, and tencent-webserver-china. Saving this file is enough: the next sync round reloads the pull targets and subsequent page requests exclude commented machines. An already-issued pull is allowed to finish internally. Commenting preserves stored history and bindings; uncommenting an unchanged declaration restores its admitted history without a Git commit or server restart. Permanent `machines retire` remains a separate action and preserves comments on other declarations when updating the file.

- `name` labels the machine everywhere in the UI. Lowercase ASCII, digits, `-`
  and `_`; it is a directory component under `state/generations/`, so it cannot
  contain `/` or `.`. Exactly one machine has `"self": true`.
- `ssh_host` is whatever `ssh <host>` reaches — a real hostname or an alias from
  your SSH config. Two machines cannot share one target.
- **A name is never reused.** Retiring a machine records the name permanently so
  a later machine cannot inherit its published history: `tt-web machines retire
  <name>`. Removing a machine from `machines.json` without retiring it only
  deactivates it — put it back and its history returns.

**Each remote needs**: non-interactive SSH from the dashboard machine
(`ssh -o BatchMode=yes <host> true` must succeed without a prompt), this repo
checked out, and `~/.local/bin/tt-web` installed from it. Export refuses to run
when the code that produces the snapshot differs from its `HEAD` — the Python
modules, `parsers/`, `pricing.json`, the `tt-web` launcher and `install.sh`. A remote with uncommitted changes there reports a failure
rather than publishing a snapshot whose code version cannot be named. Edits
elsewhere in the checkout (docs, `web/`, `machines.json`) do not block it. The dashboard's declaration controls pulls; editing a remote's declaration does not change the dashboard's targets.

A remote only reports **which account it is signed in as** once its checkout has
the account-stamping exporter; until then the dashboard shows its quota on its own
row as `account unknown` rather than guessing. Bringing one up to date is the
ordinary `git pull` in its checkout — no lockstep upgrade, because the stamp is an
added field inside an unchanged schema, so old and new machines mix freely.

**No machine has to be signed in to every provider.** A build host that only runs Codex, or a server that runs neither, is a configuration rather than a fault: `tt-web refresh` does not count it among the machines needing attention, and nothing tells you to fix it. Both ways to have no usable sign-in are covered — no credential file at all, and a credential the provider no longer accepts — and the quota table names them separately underneath, because they leave the reader in different places:

- *not signed in, so not counted above* — no credential, no row, nothing to count.
- *sign-in has lapsed, so the figure above is the last one taken and stops advancing* — the row is still there, holding whatever reading the machine managed before it lapsed. That row's `updated` age is the only remaining cue that it stopped reporting, which is why the row is kept rather than hidden.

An active quota query retries a failed provider once after 2 seconds; a signed-out provider is not retried. Persistent failures keep the previous reading and its original observation time. If the account's displayed source (`reading_from`) is healthy and has a valid timestamp within the existing 6-hour freshness window and a displayable value, another machine's failure does not mark that account as delayed. Otherwise, an affected active account shows “更新延迟” when an old value remains, or “暂不可用” when none is available, with the existing Refresh action available to retry. Per-machine errors remain in collapsed collection details and clear after a successful query. Server-side reading selection, APIs, and refresh frequency are unchanged.

**Switching the account a machine is signed into is not one of them.** Only the hourly quota round runs an active query; the 120-second statistics round exports passively and carries the last query's outcome over from the machine's own quota cache, which it stops doing the moment the cached block names a different account — republishing the old account's "query succeeded" under the new one would be a false claim. The dashboard reads that gap for what it is and keeps showing the reading with its own `updated` age, rather than naming the exporter. It names the exporter only when a block carries neither `refresh_error` nor `signed_out`, which is the one shape that really does predate confirming a refresh.

**This one needs both ends upgraded**, unlike the account stamp described just above. The machine publishes the signed-out state and the dashboard reads it, so upgrading only the machine leaves the dashboard reading its block the old way (*"无法确认这台机器的配额刷新已生效…"*), and upgrading only the dashboard leaves an old machine publishing nothing for it to read. Deploy is the ordinary one under **Updating a remote**; there is no flag day, just no effect until both sides have it.

**Adding a machine** takes two steps. Declare it in `machines.json`, then
accept the binding:

```bash
tt-web machines accept <name>
```

The first contact with an unseen SSH target is refused by design: the system can
only promise "the same machine as last time" — it cannot verify that the alias
points where you think it does. The command prints the name and SSH target, says
so in those terms, and waits for confirmation; answer anything but `y` and no
binding is written. It then pulls that machine's usage and reports the identity
it pinned. Later syncs fail closed if that identity changes. Use `--yes` to skip
the prompt in a script — check your SSH config first, because that flag is the
whole of the verification.

**Refresh and status**: `tt-web refresh` is the common manual and scheduled refresh entry point. With the dashboard running, it forces a cross-machine generation or joins the sync already in progress; the active Quota phase queries the Claude and Codex quota for each machine's current signed-in account, after the statistics-only phase has published its results. Codex accepts a live quota only when the app-server rate-limit response includes the same `accountId` as the current account. A provider failure keeps its previous reading and original observation time, and the page and CLI identify the affected machine instead of presenting the old value as freshly observed. When the generation still identifies the active account but has no newly parsed quota, existing account memory supplies that account's last successful reading and keeps it in the active section; no new memory field is introduced. A generation from an older exporter remains readable but cannot confirm an active Quota refresh. With the dashboard stopped, `tt-web refresh` still updates the local rollup, warns that cross-machine usage and Quota were not refreshed, exits non-zero, and tells you to run `tt-web start` followed by `tt-web refresh`.

`tt-web open` requests a fresh cross-machine generation before it opens the page; if an existing round began before the request, a follow-up round is retained. Ordinary page loads pull from the remotes when data is older than 10 minutes, and the Refresh button forces a pull. The first response never waits for the network — the page renders what it already has and reads newly admitted generations while synchronization continues. If the CLI cannot request a refresh, it warns, opens the existing data, and tells you to retry with the page's Refresh button. The collapsed summary reports included machines, source age, refresh progress, and machines needing attention. Its details keep the machine names and distinguish *not reachable now* from *data is old*: both can be true at once, and a machine that has never been reached successfully is excluded from `All` while still counting in the denominator. Statistics becoming visible does not complete a pending Quota phase or the user's refresh request.

**Retention**: each machine keeps its current snapshot and the previous one;
older ones are removed as new ones publish. A snapshot in use by a reader is not
deleted out from under it.

**Updating a remote**: deploy by updating that machine's checkout to the commit
you want and rerunning its installer. To roll back, check that machine out to the
earlier commit and rerun the installer; published snapshots are independent of
the code version that produced them, so a rollback does not discard data.

## Services

| Service | Supervisor | Purpose | Operations |
| --- | --- | --- | --- |
| `tt-web` | Manual PID-file daemon in `state/` | Local dashboard server at `127.0.0.1:<port>` | `tt-web start`, `tt-web stop`, `tt-web status`; script equivalents: `./tt-web/start.sh web`, `./tt-web/stop.sh web`, `./tt-web/status.sh web` |
| `com.ttweb.hub` | Opt-in MacStudio LaunchAgent | Fixed `0.0.0.0:39001` center and non-overlapping background pulls | Installed only by `./tt-web/install.sh hub macstudio` |
| `com.ttweb.rollup` | Default macOS LaunchAgent | Standalone: existing hourly refresh. Hub: save local statistics every 120 seconds, then query and atomically cache Quota when its independent 3600-second interval is due. | Installed by `./install.sh` or `./tt-web/install.sh`; hub behavior is enabled only by `./tt-web/install.sh hub <role>`; inspect/remove with `./tt-web/{status,uninstall}.sh rollup-daemon` |

Operational details live in [docs/operations/services.md](https://github.com/lindong28/ai-agent-config/blob/3d61487d5386dfe9bb6ba7d26807005bbf0bd7f2/tt-web/docs/operations/services.md).

The refresh schedule is installed by default on macOS. Without hub opt-in it retains the standalone schedule and dashboard-owned synchronization described below. With hub opt-in, each machine independently collects its local archive every 120 seconds whether MacStudio is reachable or not. Quota queries remain hourly: a successful current-account reading is atomically stored in `state/quota_snapshot.json`, and intervening exports reuse it with its real `updated_at`. MacStudio pulls each machine independently, so one unavailable source does not delay publication of another source's generation.

## Check

```bash
tt-web start && curl -s 127.0.0.1:$(cat tt-web/state/port)/api/overview | head -50
```

To inspect rollup integrity without changing the rollup database, run:

```bash
tt-web rollup --check
tt-web rollup --check --json
```

`status` is the overall result: `safe` means the database snapshot and source scan were complete, with no protected-field shrink in either rollup table and no project-identity blocker; `attention` means the complete scan found at least one project or usage `would_skip` bucket, or a blocked source path; `indeterminate` means the checker could not obtain a complete, consistent comparison, so it is not evidence that the rollup is clean. `verdict` is narrower: it reports only the shrink guards (`safe`, `attention`, or `unknown`) and can be `safe` for the buckets that were compared while the overall `status` is `indeterminate`. Scripts must parse `status` rather than treating a zero exit code or `verdict: safe` as a clean result.

The project-aware dry-run readings are `orphan_rows`, `would_skip`, and `would_write`. Their `usage_orphan_rows`, `usage_would_skip`, and `usage_would_write` counterparts cover the project-independent agent/model rows. A `would_skip` bucket has token/message/entry counters below its stored values and keeps the old whole row; a `would_write` bucket is new or changed, including cost-only changes but excluding exact no-ops. Both table families compare the inclusive 28-day recompute window plus one-time backfills for source dates the corresponding table has never seen; existing rows outside the window are frozen and appear in neither write/skip set. Every v2 database records `usage_rollup_authoritative_from` when the v2 marker is written, and usage checks and later writes never backfill an earlier date; on v1 migration, older buckets remain explicit legacy fallback. Orphans are evaluated across all persisted rows. The JSON contract reports the self-describing `usage_totals_basis` together with the fields that validate it: `usage_rollup_schema: 1` means `legacy_project_derived`; schema `2` with `legacy_fallback_bucket_count: 0` means `project_independent_usage`; a positive count means `usage_with_legacy_project_fallback`. `legacy_fallback_bucket_dimensions` states the count's unit. The human CLI reads and prints that basis rather than deriving a second copy. `db_span_scope: project_rollup` makes explicit that `db_span` describes the project table; `window` describes the recompute window. Orphans after expected source retention do not require rollup-database repair, but an unexpected disappearance should be investigated and the source restored when possible.

For `attention`, inspect the reported `would_skip` old/new fields and identity blockers before running another rollup. The check is read-only: a blocker present only in `current_blockers` is not persisted by `--check` and may therefore be absent from `tt-web rollup blockers`; use the JSON diagnostic fields to distinguish it from a previously stored blocker. Use `tt-web rollup blockers` for persistent blocker details and only use a recovery command that it explicitly offers after the current source path or remote again directly matches the recorded candidate. For `indeterminate`, fix the reported database, snapshot, permission, or source-scan error and rerun the check. Do not respond by deleting `state/rollup.db`, deleting rows, or overwriting preserved history; those actions destroy the very baseline the guard protects.

## Tests

```bash
.venv/bin/python -m unittest discover -s tt-web/tests -t tt-web
```

The suite is pure `unittest` and depends on `requests`, which the
repo-root shared venv (`.venv/`, provisioned by the top-level `install.sh`)
already provides. Run it through that interpreter, not system `python3` — the
latter lacks `requests` and fails before any test runs. For a one-shot run
without the venv, `uvx --with requests pytest tt-web/tests -q` also works.

## Consumers

- `/` shows cost-first Spend cards and quota used as **one row per active account** — Claude 5h / 7d and Codex 7d. The table separates Provider, Plan, and Account into aligned columns, with each live row naming the machines signed into that account. Quota is metered per account, so rows are never summed; a machine whose tt-web predates account stamping gets its own row labelled `账号未知`, never folded into a named account. Upgraded exporters actively query each machine's current account on refresh. If one provider fails, its previous value retains its original observation time and the provider section warns for that machine; an older exporter is identified as not confirming active refresh. A row's numbers are attributed to the account that machine is signed in as *now*, which is wrong for the window right after an account switch — see [ADR-024](https://github.com/lindong28/ai-agent-config/blob/3d61487d5386dfe9bb6ba7d26807005bbf0bd7f2/docs/adr/024-tt-web-quota-account-attribution.md) for why that is accepted and what it costs. Accounts observed while signed in remain under the collapsed `历史账号（N）` disclosure after they disappear from every admitted machine; refresh never signs those historical accounts back in. Expanding it shows the visibly historical `已登出` rows with their last observed values and per-row `移除` controls; confirming a removal names the account, observation time, and last reading before forgetting it permanently. Memory starts accumulating when this feature is installed, so accounts that were already signed out cannot be backfilled from older data. `本周成本` is the `Asia/Shanghai` calendar week and carries the compact `自周一 00:00 · GMT+8` boundary; Today and Week no longer repeat token totals. The selected-range card adds `自 <date> 起` or `覆盖范围未知` only when the dollar figure needs that qualification. `成本趋势` remains range-aware, reads persisted history from `state/rollup.db`, and describes its view in reader terms such as `近 30 天 · 按天`.
- `/explore` follows SELECT / WHERE / GROUP BY: choose a metric, filter by time and agent/project/model/machine, then combine any of those four dimensions with an optional day/week/month unit. Custom dates include both endpoints (Asia/Shanghai daily buckets) and persist as `range=custom&start=YYYY-MM-DD&end=YYYY-MM-DD`; other pages revert to their default 30-day range. Time grouping shows a trend; categorical combinations also appear in a descending metric ranking with every combination retained. Hover or focus a ranking row to inspect its individual periods. Without a time unit, only the ranking appears; selecting no grouping gives one total. Filters and grouping persist in the URL; old `x`/`group` links remain supported.
  Range presets are `7d`, `30d`, `90d`, `6m`, `1y`, `2y`, and `all`. An initial link without an explicit grouping uses day buckets up to 90d, week buckets up to 1y, and month buckets beyond that; the time-unit control then makes the grouping explicit.
  The ranking retains every joint group and its full labels. Long project labels in the trend are shortened with the full value kept in the tooltip.
  The chart carries a second, tighter limit of its own: it draws at most 8 series,
  because the palette has 8 slots and a ninth series would have to reuse a colour.
  Token and message metrics pool the remainder into one `Other` line. Cost does
  not — a null cost cell means either "no activity" or "price unknown", and those
  are indistinguishable by the time they reach the page, so pooling would publish
  a total that silently omits real spend. Cost instead charts the 8 largest by
  known cost and says on the page how many series were left out. The ranking below the chart retains all combinations and their period values, ordered by total. The trend keeps its axis running oldest to newest.
  Pivots that neither display nor filter by project read project-independent
  usage totals, so a project-identity blocker cannot remove real agent/model
  tokens from the chart. Project-aware pivots continue to use the attributed
  project table and may remain incomplete while a blocker is active. Neither the
  totals-derivation basis nor the per-query totals provenance is shown on the
  page any more (owner decision, 2026-09-15); both remain in the API payloads
  (`/api/pivot.totals_provenance`, `/api/sync-status.machines[].generation_totals_basis`)
  for anyone who needs to tell a project-independent total from a legacy
  project-derived one.
- `/sessions` lists the full session history carried by the admitted machine snapshots and expands rows into turn-level usage, filtered by
  agent, project, and model (options drawn from the loaded rows) and paged 100 at
  a time; the column header stays fixed while the rows scroll, and rows are
  grouped under the date they started when sorted by time. For Codex, each displayed detail row is one
  positive `token_count` delta at that event's timestamp. The Sessions API calls
  this `usage_events` (and each detail count `usage_event_count`), not user prompt
  count. The current API does not duplicate these values under message-shaped
  names; the current frontend still normalizes the old server's `messages` field
  during the static-assets-before-restart upgrade window.
- `/llm-calls` is a request-first audit of calls made through each machine's local `llm-gateway`; the gateway writer and request path remain machine-local. Hub snapshots carry the full gateway ledger history, and the page namespaces identities and cursors by machine, offers a Machine filter, and keeps request and attempt timelines and pagination separate. It exposes provider, profile, non-sensitive credential source kind and reference, actual model, outcome, usage, latency, and cost basis without prompt, response, or credential values. Request-time and attempt-time windows remain separate. Cost remains grouped by currency, funding, pricing basis, and exact/estimated/unknown/subscription authority rather than combining unlike amounts or treating unknown and subscription usage as zero. An older exporter without source detail is shown as `not collected`, not as zero calls. Coverage is limited to API calls routed through the local gateways: `philo-prompt` is currently registered; Claude Code, Codex itself, and projects not yet named and registered are outside this view.
- A successful Gateway response may omit usage. `/llm-calls` keeps that attempt visible as `success` with `usage_state=not_reported`; absent charge evidence remains `cost_state=unknown`, with no invented token count or zero charge. Deploy the updated reader before a writer starts recording these native responses.
- `/network` shows the same DNS, IPv6, public IP, proxy-risk, and timezone
  diagnostics exposed by `ip-check --json`, with a 60s cache and Refresh for a
  forced recheck.

LLM Calls public projection v3 exposes one `funding_source` per attempt: company/personal paid or subscription. Subtotals retain the existing `funding_type` category and currency/basis grouping; company and personal subscriptions both belong to the subscription category. The reader accepts exact gateway ledger v3, v4, and v5, translating legacy attribution only in memory and preserving each source snapshot's original schema version and rows. Session references accept the gateway writer's UUID versions, including UUID v7 used by current agent sessions. Upgrade the local exporter and central reader together with the pinned independent `llm_gateway` package before the gateway writer migrates its ledger to v5.

Historical daily rollups are stored in SQLite WAL at `state/rollup.db`. Schema v2 writes `daily_rollup`, keyed by `(date, agent, project, model)`, and `daily_usage_rollup`, keyed by `(date, agent, model)`, in one transaction. The first table serves project-aware views. Totals that do not use project select each key from the second table when present, including entries whose project identity is blocked, and otherwise preserve that key through an explicitly reported legacy project-derived fallback. `/api/pivot` reports `totals_provenance` from the rows that actually entered the current query rather than copying a full-history generation label. Each rollup normally recomputes the inclusive most recent 28 days. Every v2 database requires an ISO-date `usage_rollup_authoritative_from` alongside its schema marker; a missing or invalid boundary is rejected rather than interpreted as unbounded authority. On v1→v2 migration, current raw logs are authoritative only from that boundary forward, so later runs cannot silently expand authority into older history. A stored key that current source no longer produces is left untouched. If current source still produces the key but any token, message, or entry counter is lower than its stored value, the entire update is skipped and the last trusted row is kept; other keys continue updating. Existing rows outside the window are frozen, while a previously unseen date that is still present in source may be backfilled once unless it precedes the persisted usage authority floor. New readers accept v1 snapshots with a fully legacy fallback; v2 snapshots require the schema marker, authority floor, and usage table and are rejected by old readers, so upgrades must deploy the dashboard reader before any remote v2 exporter. V2 generation/export metadata reports a validation-bound `metric_totals_basis`, `legacy_fallback_bucket_count` together with `legacy_fallback_bucket_dimensions=[date, agent, model]`, plus `project_row_count` and `usage_row_count`; `row_count` remains their physical-row sum for compatibility. A v2 fallback count of zero means project-independent usage is authoritative for every stored bucket; a positive count means the snapshot is hybrid. These statements describe the upgraded v2 reader; a still-running v1 dashboard will not show the new provenance until the consumer-first upgrade is deployed.

Hub collection additionally writes an append-only-by-identity SQLite usage archive of parsed `UsageEntry` fields. It retains events after their source logs disappear and excludes prompt/response content. The archive becomes authoritative from the next complete `Asia/Shanghai` day after its first collection; earlier history remains legacy best effort under the existing floor, shrink, and freeze guards. The archive-backed region replays all retained dates, including dates outside the old 28-day window. Each exported generation remains a single digest-admitted `snapshot.db` carrying source-detail admission and retention metadata; Sessions and the gateway ledger use their full exported history rather than the archive authority day boundary.

Cost is an API-list-price equivalent, not a bill. Claude Code and Codex are used here under seat subscriptions that do not meter tokens, and neither writes a per-call charge into its logs, so every row is priced from a model rate table: LiteLLM's published rates when they cover the model, the bundled `pricing.json` when the fetch fails or LiteLLM has no key, and a same-family estimate for GLM-5.1/5.2. A logged `costUSD`, if a source ever emits one, is used verbatim in place of the rate table. Anthropic 1-hour cache writes are charged at the model's `above_1hr` rate, falling back to the 5-minute rate when the table has no 1-hour entry; a source that does not report the 5-minute/1-hour split is priced entirely at the 5-minute rate. Models whose entry carries `*_above_N_tokens` rates switch every input-side rate to that band once the whole input side — fresh, cached, and cache-written tokens together — passes the threshold. A cache category the table prices at nothing is charged nothing rather than derived from the input rate: an entry carrying no cache-write rate usually means the provider bills no cache write, so deriving one invents a charge. The arithmetic lives in `token_cost.py`, a module kept byte-identical with llm-gateway's copy so the same usage prices the same in both; neither repository imports the other. Within the 28-day recompute window, accepted project rows and all project-independent usage rows are recomputed from currently readable source. Rows with missing source or a protected-counter decrease keep their last trusted cost, and existing rows outside the window remain frozen. Unknown model pricing is displayed as `—`, not `0`.

Two known cost gaps are not modelled. Codex reports no cache-write volume, so any cache write it performs is absent from Codex rows. Model identity is still resolved by substring match when neither the exact key nor the key with one provider namespace stripped is present, and that can land on a differently-priced host of the same model; a `Fuzzy pricing match` warning is logged for any non-exact resolution, the namespace strip included. A row priced with some cache category missing from the table logs a `with no rate for:` warning naming the model and the categories, since the amount itself has no field to carry that. What such a match can no longer do is produce a confident zero: a candidate entry must carry a base rate to count as a match at all, so a name that only resolves to a rate-less entry is reported as unknown pricing (`—`) instead of `$0.00`.

### Known rollup limitations

- Two quiet-WAL `test_rollup_read_connection` cases failed under the worker's Python 3.9 on both its implementation and unchanged `8e0f0c6`; the same cases passed in this task's Python 3.13 project environment. This is a Python 3.9 test-environment compatibility finding owned by tt-web maintenance, not evidence that the deployed project environment loses WAL updates. This hub work does not change that connection behavior.

- The v1→v2 migration deliberately trusts the current raw-log scan for its most recent 28 days, even when a corrected parser produces fewer tokens than the legacy project aggregate. This fixes known Codex duplicate/cumulative overcounts, but `scan_complete` only proves that the files still present were read; it cannot prove that no raw log was deleted earlier. Missing raw logs inside that initial authority window can therefore make the new total read low. Dates before `usage_rollup_authoritative_from` retain explicit legacy fallback instead of inheriting this assumption.
- Rows written before cross-file deduplication landed keep their pre-deduplication values. One Claude API call reaches the loader once per transcript file recording it, and Claude Code copies a whole transcript into a new session file on resume or fork, so those older rows counted such calls once per copy. Deduplicating lowers the token, entry, and message counters for the affected buckets, which the protected-counter guard reads as a regression and skips, and rows outside the 28-day window are frozen regardless. The stored history was therefore left as it stands rather than rebuilt, because 35 of its dates no longer exist in source and rebuilding would delete them. Buckets first written after the change are deduplicated; older ones read high, by roughly 9% on the affected Claude buckets.

- The integrity promise is **statistics do not decrease**, not **source retention is complete**. Both tables can undercount when, on the same day and in the same bucket, one session's source disappears while another session keeps growing. The guard first keeps the old aggregate; once the remaining session grows past that old aggregate, the new value is accepted even though the missing session's contribution is no longer included. The checker may report no shrink because neither the stored aggregate nor current source contains enough identity-level history to detect it. The project-independent table removes project-identity blockers from this failure path; it cannot restore raw logs that no longer exist.
- Two source paths may previously have contributed to the same project identity. If one path is later deleted or points at a different remote, another path can claim the old project during classification; a retry can then classify the changed path under its new project instead of keeping it blocked. The old project row is preserved while new usage is written under the new project, so the dashboard can show both old and new project rows and their sum can double count the changed path, without an active blocker remaining for that run. Distinguishing the old rows by originating path requires source-path lineage that `daily_rollup` does not store.
- Once a source path has a persisted project identity, later runs reuse it without re-reading Git. If that repository is genuinely renamed remotely, new usage continues under the old project name: the dashboard keeps showing the old name and no blocker appears. This is a stale classification label rather than a decrease in token totals; safely detecting the rename without reacting to transient Git failures also requires source-path lineage.
- The recovery tooling does not migrate historical rows to a renamed remote. When a path without a reusable persisted identity resolves to a remote that cannot be reconciled with unclaimed history, `tt-web rollup blockers` keeps it active: old project rows remain visible, but new usage from that path is paused. A strict pin is offered only when the blocker recorded one unique historical `pin_candidate` and the path's current path or remote again directly and uniquely matches that same candidate. Otherwise recovery is refused. Moving old history to the new name remains unsupported because the rows have no source-path lineage.

`state/rollup.db.lock` (generally `<db_path>.lock`) is a persistent coordination inode, not a disposable temporary file. Cleanup scripts, tmp-reapers, and manual `state/` maintenance must never delete, replace, rotate, or recreate it. `flock` follows the inode: recreating the same pathname while another process holds the old inode silently splits writers into two independent lock domains.

Timestamps (quota resets, session and turn times) render in the machine's
current system timezone with a UTC-offset label (e.g. `GMT+8`). The zone is
resolved live by the server from the OS setting (`/api/timezone`, read from
`/etc/localtime` per request), so the display follows System Settings and never
a browser left running on a stale timezone.

## Network Check

```bash
tt-web network            # the /network page, in the terminal
tt-web network --force    # skip the cache and probe again
tt-web network --json     # raw /api/network payload
ip-check                  # the upstream table, unmediated
```

`tt-web network` reports **this machine only**, matching the `/network` page —
nothing in it reaches the machines in `machines.json`, which govern usage sync
rather than diagnostics. To check another machine, run `ip-check` on it.

It reads the snapshot from a running tt-web server, so the terminal and the page
normally serve the same cached result instead of probing separately; with the
server stopped it runs the same check in-process. The server caches for 60s and
does not coalesce concurrent misses, so a cold cache or `--force` still lets two
near-simultaneous readers probe independently and land on different results.

The exit code says whether a report was produced, not how risky the network is:
`0` for any verdict including `high`, `2` when `ip-check` is missing, times out,
or returns unparseable output.

A risk grade is only printed when every input that feeds it answered. Several of
`ip-check`'s lookups report failure as ordinary result text rather than raising,
so a `proxycheck.io` timeout leaves a risk section whose score is null, an empty
`errors` list, and an upstream verdict that still reads `low`. In that case the
report prints **证据不足** and names the inputs that went unobserved, instead of a
grade three of its four inputs never earned — a qualifier under a headline that
still said "低风险" was not enough, because the headline is what a scanning
reader acts on. The exit code is unchanged: a report was still produced.

Egress goes through the route named in `~/.config/agent-proxy/current-proxy`,
resolved **once per round** and shared by all three probes, so the address the
report names is the one those probes actually went out through. Inheriting
`HTTP_PROXY` is not enough for a long-lived process: the tt-web server copies the
environment once at spawn, each route owns a fixed and distinct port, and the
tunnel behind the old one is gone the moment you switch — so a server started on
the Tencent route keeps dialing a dead port and never sees the public internet
again.

> **The publisher is not in place yet.** Nothing writes that file automatically
> today; it is maintained by hand, and this side cannot tell a current address
> from one left over from two switches ago — which is why the report says
> `发布文件指定`, never "current route". Until system-config's shell layer
> publishes on every switch, update it yourself when you switch. Deleting it is
> **not** a way to get the current route: a long-running server would fall back
> to the environment it inherited at spawn, which is the stale address this whole
> mechanism exists to route around.
>
> Whatever ends up writing it must publish by **atomic replace** (write a temp
> file in the same directory, then `rename`). An in-place truncate-then-write
> leaves a window where the file reads as empty, and empty is a meaningful value
> here — "this route runs direct" — so a probe landing in that window would
> bypass the proxy and snapshot the real public IP.

An unreadable or absent file, and content that is not a usable proxy URL (a route
name, a comment, two lines), both mean *no authoritative answer* rather than
*connect directly* — this side then does not take over routing at all and
`requests` uses the environment exactly as before. Reported as `本次探测出口`,
and in `--json` as `proxy_effective` (`address` + `source`, where `source` is
`published` / `unpublished` / `invalid`, plus a `reason` for the last two).

When a failed probe's address matches the route it went out through — or, absent
a publisher, one of this machine's proxy env vars — the report says so and spells
out the consequence, rather than printing the timeout and the proxy setting
sixteen rows apart and leaving the reader to notice they are the same address.

One case stays undecidable — `ip-check` reports no IPv6 address both when IPv6
is off and when the probe failed — so the report says "未检出地址" rather than
claiming it is disabled, and keeps that dimension outside the verdict.

`ip-check` prints the upstream table for a quick VPN or proxy sanity check.
`ip-check --json` is consumed by `/api/network` and is stable enough for local
scripts.

When `/network` reports `verdict: high`, see
[NETWORK-REMEDIATION.md](https://github.com/lindong28/ai-agent-config/blob/3d61487d5386dfe9bb6ba7d26807005bbf0bd7f2/tt-web/NETWORK-REMEDIATION.md) — a per-finding runbook for
fixing IPv6 leaks, CN DNS exposure, and timezone mismatch on macOS, including
the manual proxy-GUI step that cannot be scripted.

`install.sh` runs this check once at the end of setup. It prints the findings and
a pointer to the runbook **only** when the verdict is `high`; on a clean
environment it stays silent. The probe never fails the install.
