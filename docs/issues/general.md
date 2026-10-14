# tt-web Issues

## [open] ISSUE-IDENTITY-20261014-7a62: producer identity and credential selectors diverge

- Owner / Priority: identity/account maintainer / high for Codex workspace selection, medium for missing labels or unavailable custom stores.
- Evidence: fixed Codex 0.162.1 ordinary auth uses selected `tokens.account_id`; `parsers.accounts` overwrites it with the JWT workspace claim. A selected/claim conflict returns different identities; matching fields are the control. Namespaced profile email is valid upstream but becomes null locally. These affect display and pre-send checks; no production wrong-send was observed.
- Claude 2.1.296: fixed binary selects custom global metadata and directory-specific Keychain service/account, including secure-storage override. Monitor's metadata/file/Keychain selectors disagree in that configuration. Synthetic default/custom inputs and selector comparison establish this without reading real credentials or contacting Keychain. Existing profile UUID verification mitigates wrong-account attribution; same UUID across organizations remains unverified, not a proven additional bug.
- Next: use producer-auth-mode identity precedence and email fallback; align metadata/file/Keychain location semantics while retaining strict ownership checks. Stable member identity and historical identity migration require their own evidence and scope. [Argument](../references/upstream-contracts.md#2-账号身份与凭据归属); `upstream-contract-audit-20261014/accounts/audit.md` and `offline-counterexamples.json`.
- Disposition: audit only; no identity migration, account action or product repair.

## [open] ISSUE-QUOTA-20261014-c5e7: other quota buckets can be displayed as Codex

- Owner / Priority: quota/parser maintainer / high for wrong bucket; medium for unsupported CLI-update advice.
- Evidence: fixed producer's legacy rateLimits view chooses the first bucket when codex is absent. Both `quota_reading` and machine refresh fallback to this view. A synthetic other=91 is displayed as Codex, while codex=21 control is correct. The real passive producer emits multiple RateLimits into TokenCount; parser picks the last and discards limit_id, so codex21 followed by other91 becomes91.
- Next: select the intended bucket before recency/window projection; explicit non-codex must not substitute. Missing target is unknown, with version-backed compatibility for legacy no-ID payloads. Preserve fail-closed handling of nullable accountId, but replace the unsupported claim that updating CLI resolves it. No live provider calls or frequency sampling in this audit.
- Evidence/Disposition: [argument](../references/upstream-contracts.md#7-四项指标来源边界), `accounts/audit.md` and 10-case offline results. Product repair/replay not performed.

## [open] ISSUE-ISOLATION-20261014-40ac: configuration and zero-tool guarantees need narrower boundaries

- Owner / Priority: account-operation maintainer / medium, effective production tool reachability unverified.
- Evidence: fixed Codex loader reads system/managed layers independently of HOME/CODEX_HOME. environments=[] disables environment access, but PlanHandler and other utility tools have separate enablement conditions. Rejecting server requests only gates that RPC direction, not every internal tool. Monitor separately disables several features; those controls are not equivalent to zero tools.
- Next: express the exact existing isolation boundary; if zero tools is the intended account-action contract, identify a producer-supported enforcement point and verify effective tool exposure. Do not bypass managed policy or infer real MCP execution merely from registry code. [Argument and fixed links](../references/upstream-contracts.md#5-配置工具隔离与http请求边界); `accounts/audit.md`.
- Disposition: configuration untouched; no real system-config/MDM inspection or app-server execution for this finding.

## [open] ISSUE-HTTP-20261014-b6d4: write routes have inconsistent browser request boundaries

- Owner / Priority: Web maintainer / high for restart admission; production browser exploitability unmeasured.
- Evidence: at `eb6e797`, isolated real HTTP handler rejects cross-origin account POST (403) but admits cross-origin text/plain restart POST (200, scheduler called once), and refresh HEAD invokes forced collection. Only final side effects were replaced; no real restart occurred. [Argument and sources](../references/upstream-contracts.md#5-配置工具隔离与http请求边界), artifact `upstream-contract-audit-20261014/runtime/probes.json`.
- Next: apply the existing browser-write boundary consistently and move explicit refresh actions off safe methods; retain the trusted-personal-network product scope. CORS response visibility is not a server-side admission check. No implementation in this audit.

## [open] ISSUE-PROCESS-20261014-3e90: standalone PID ownership and descendant guarantees are incomplete

- Owner / Priority: lifecycle maintainer / high for stale-PID termination; conditional descendant and remote-lifetime boundaries are medium.
- Evidence: isolated actual CLI stop with a stale PID pointing at a separately owned fixture process sends SIGTERM to it and reports success. A separate `supervised_worker` fixture exits its leader normally while a same-group descendant survives the context. These are controlled local processes, not observed production incidents. [Argument](../references/upstream-contracts.md#6-进程sshrsynclaunchd与git部署); raw `runtime/probes.json`.
- Next: establish stable process ownership before signalling; then define/reconcile the supervisor's normal-exit contract. The observed worker normally joins threads, so fixture leakage does not prove production leakage. Parent EOF across exec and SSH timeout provide distinct, conditional guarantees; killing a local SSH client is not remote cancellation evidence. Clarify the `shutdown` docstring versus the actual reexec path. No remote supervisor or higher availability requirement is added by this issue.
- Disposition: recorded only because this task requested argumentation, not runtime repair or deployment.

## [open] ISSUE-STORAGE-20261014-2b31: immutable/WAL assumptions and runtime-version risk

- Owner / Priority: storage maintainer / medium; no established live data loss.
- Evidence: `rollup._open_read_only` only checks WAL around opening, whereas immutable requires lifetime immutability. An injected first-open failure plus a real subsequent writer yields stale fallback data. Actual maintenance callers hold the rollup lock and Web callers use leased generations, so this does not establish a live request race. A real FULL checkpoint `[0,2,2]` leaves an 8272-byte WAL; `_wal_has_frames` and the resulting uncheckpointed diagnostic conflate file allocation with pending frames.
- Version: local Python 3.13.12 embeds SQLite 3.50.4, within the official WAL-reset affected range; deployment versions and the necessary concurrent checkpoint/write pattern were not established. Do not infer corruption from version alone.
- Next: bound the helper contract to locked/immutable callers and correct WAL diagnostics without weakening safety; verify actual runtime versions and checkpoint actors before choosing a dependency update. [Detailed argument and upstream sources](../references/upstream-contracts.md#3-sqlite导出和-archiverollup-恢复); `storage/report.md`, `reproduce-results.json`. No implementation/upgrade in this audit.

## [open] ISSUE-GENERATION-20261014-f086: publication timestamp and legacy freshness semantics

- Owner / Priority: snapshot/sync maintainer / medium.
- Evidence: `published_at` is sampled before copy/validation/commit-lock wait but consumed as sync/contact time. A controlled 60-second preparation delay leaves it 60 seconds earlier than pointer switch. Actual `install_export_bundle` accepts a valid legacy A10→B20→C30→A10 sequence after A was GC'd; current replay is idempotent and previous replay is rejected. Statistics-extension retention/freshness checks have a narrower compatibility domain.
- Next: align timestamp sampling/meaning with consumers. Separately decide and document whether explicit legacy rollback is supported; no independent prohibition or normal-scheduler occurrence was established, so do not silently impose anti-replay policy. [Argument](../references/upstream-contracts.md#4-generation多机查询租约与回放), `storage/report.md` and isolated reproduction results.
- Disposition: no product change; legacy behavior is a capability boundary, timestamp is an implementation/semantic mismatch.

## [resolved] ISSUE-TEST-20261012-bd71: baseline fixture and census mismatch

- Type / Priority: test maintenance / low.
- Observed: the 847-test separation run failed `test_pivot_sync_publish_remembers_account_before_later_signed_out_publish` because its sync mock does not accept `quota_refresh`, leaving no account-memory file; `test_production_daily_rollup_and_attach_sites_match_explicit_census` omits two synthetic daily-rollup accesses in `scripts/benchmark_backend.py:make_snapshot`.
- Baseline: both failures reproduced in a clean `git archive` of c16ce83 with isolated HOME and the project Python. The final 139-test affected run retains only the census failure. The separate census change caused by removing the Gateway reader is fixed.
- Owner / Next: Monitor test maintainer should update the mock keyword arguments and classify the synthetic benchmark sites, then rerun both tests. Existing fixture maintenance is outside this product-separation change; the full suite is not reported as passing.
- Evidence: local `~/.local/state/agent-monitor-boundary-20261012/baseline-tests.log` and `monitor-final-tests.log`; scope and result in `docs/product-boundary-separation.md`.
- Resolution 2026-10-14: updated the sync mock's keyword arguments and explicitly classified both synthetic benchmark accesses. The final isolated 924-test run passed, including both affected cases; evidence is `/Users/lindong/task-artifacts/all-metric-spec-20261014/full-tests-final.log` and `failure-attribution/`. This resolves these two fixture failures, not every historical test-environment issue below.

## [open] ISSUE-METRIC-20261014-716c: source facts and historical provenance limit complete accounting

- Owner: Monitor accounting maintainer, with upstream producer/identity evidence required for recovery.
- Follow-up contract audit 2026-10-14 at `eb6e797`: [upstream arguments](../references/upstream-contracts.md#7-四项指标来源边界) establish that Claude 2.1.296 sidechain queues can serialize incomplete usage and Monitor accepts it without source_errors. Later same-UUID appends remain possible, so permanent undercount after a complete CLI run is not proved. Six fixed Codex versions omit ModelReroute from durable rollout; actual serving model cannot generally be recovered from requested-model history. A standard/fast price fixture yields the same nominal USD5 while current official first-party rates imply USD5/USD10. This does not prove production fast usage or invoice error. Preserve recorded/nominal-estimate scope, investigate final-write completion before claiming full usage, and obtain model/mode/effective-price evidence before expanding cost claims. Fixed sources, schedules and raw results are in `upstream-contract-audit-20261014/history/`; no runtime changes in this follow-up.
- Evidence: the specification audit and first four-host replay at `8d94501` are retained in `/Users/lindong/task-artifacts/all-metric-spec-20261014/`. Two machines each contain one Claude response with incomparable token observations; selecting per-field maxima would create a vector never observed. MacMini additionally reports 86 copied forks without an explicit inherited cutoff and one existing `unreconciled_remote` project identity blocker. The paginated-subagent missing-counter errors found in the same replay have a proven producer-specific recovery and are handled by the follow-up correction; they are not an unavoidable limitation.
- Current handling: preserve retained facts and explicit source errors/identity blockers. Owner checkpoints recover later known child deltas but cannot establish the earlier ambiguous copied interval. Do not delete history, invent zero usage or adopt an unverified remote identity to make the counts green.
- Remaining evidence: complete Claude JSONL final-usage persistence for all producer paths, historical tariff effective dates and original model fallback/identity facts are not established. Costs remain configured-price estimates for recorded usage, not a complete provider invoice. The full definitions and version/consumer boundaries are in [metrics.md](../references/metrics.md).
- Next: use an authoritative upstream final response/finite fork boundary or explicit project identity proof if it becomes available; re-evaluate only affected records under existing archive authority. No additional counter manipulation can reconstruct those missing facts. Final deployment/replay receipts belong to the task artifact's execution record, separately from this issue's initial observation.
- Verification at `0e8f624`: the six-version correction removed all 168 paginated-counter errors in the observed four-host cohort; final error counts were MacBook 1, MacMini 87, MacStudio 0, Tencent 0, with the same Mini identity blocker. The remaining error hashes identify exactly the two Claude conflicts and 86 unbounded copied prefixes above. All four new generations were admitted by the live Hub, whose Overview API exposed the same completeness metadata. This bounded replay result does not close the missing-source/provenance issue.

本组件里已知、但当轮未就地修的问题。

## [open] ISSUE-TEST-20260915-6c2e：测试套在**开发工作树**上有 26 个 ERROR，在干净导出树上一个都没有

- **Type**: test-environment
- **Priority**: medium
- **Discovered**: 2026-09-15，文案中文化那次提交后按 create-commit 的「在导出树上跑一遍」检查时撞见。
- **读数**（同一 commit `11152a7`、同一 `.venv/bin/python`、同一批 10 个测试模块）：

    | 跑在哪 | 结果 |
    |---|---|
    | 开发工作树 `~/research/ai-agent-config/tt-web` | `Ran 264` · **5 failures + 26 errors** |
    | `git archive HEAD` 导出的干净树 | `Ran 264` · **2 failures + 0 errors** |

    导出树剩的那 2 条（`test_iv10_force_starts…` 计时、`test_filter_registry…`）在工作树侧也失败，属既有。26 个 ERROR **全部只在工作树上出现**，典型形态是 `AttributeError: 'types.SimpleNamespace' object has no attribute 'db_path'`（`server.py:_sync_status_from_admission` 读 `statistics_snapshot.read_metadata(current.db_path)`）。差异变量是工作树里有 `tt-web/state/`（活的 rollup / snapshot 库，`.gitignore` 掉了，导出树没有）。
- **为什么值得记**：这 26 个 ERROR **在到达断言之前就挂了**，于是"断言期望的字符串对不对"在它们身上**读数恒定**。同一天的文案中文化就因此漏掉 5 条真回归——"失败集合与基线逐条相同"这条验证读数在那 26 个用例上什么也证明不了，却读起来像全绿。任何拿本地套件做回归基线的人都会踩同一个坑。
- **修法方向**：要么让这些用例的 fixture 不依赖 `state/` 的存在与否（给 `SimpleNamespace` 补 `db_path`，或让 `_sync_status_from_admission` 对缺字段的 admission 走显式分支），要么让套件在有 `state/` 时也跑同一条路径。在此之前，**回归比较应以 `git archive HEAD` 的导出树为准**，工作树读数只作参考。


## [open] ISSUE-DEPLOY-20260915-0a4c：hub 部署目录由仓外流程生成，`web/vendor` 以 symlink 指回源码 checkout

- **Type**: improvement
- **Priority**: medium
- **Discovered**: 2026-09-15，macstudio 上 `com.ttweb.hub` 的 `ProgramArguments` 指向 `~/.local/share/tt-web/deployments/registry-v5-20260913-2d3c50e/tt-web/hub.py`；该目录 2026-09-13 生成，`web/vendor -> /Users/lindong/research/ai-agent-config/tt-web/web/vendor`
- **Description**: 本仓没有任何脚本产出 `deployments/<name>/` 这种布局（本轮之前 `grep -rn deployments tt-web llm-gateway` 零命中；现在命中的只有本轮加的注释与本条），`tt-web/install.sh hub macstudio` 与 `hub.py install_web()` 都以 `ROOT`（即所在 checkout）为部署根。这份部署是某次 llm-gateway registry-v5 任务手工或用仓外脚本做的；它依赖 vendor symlink，而本轮之前的静态文件处理拒绝 symlink 越界，正是三张图表全空的直接原因。部署与源码脱节还意味着：本轮修复要生效必须再做一次同样不在仓内的部署动作。
- **Notes**: 要么把这种"导出到独立目录 + symlink vendor"的部署方式写进 `tt-web/install.sh hub` 并让 hub plist 由它维护，要么回到"hub 直接跑源码 checkout"的既有设计并删掉这份部署目录。二选一由用户定。
    **2026-09-15 实测补充**：部署目录其实是一个稀疏的 git checkout（HEAD `df9ddc7`，是自己 rebase 过的历史），exporter 的 `exporter_version()` 拿它的 `git status -- <authority paths>` 当代码权威——只覆盖文件不动 HEAD 会让 macstudio 自导出被拒（"runtime authority differs from HEAD"，本轮踩到并修复）。本轮实际走通的步骤已写进 `docs/operations/services.md`「Hub 部署副本」；同日先后两份部署副本：`main-20260915-a15c8c2`（目录名里的 SHA 是提交时的 `a15c8c2`，随后本地 main 被 rebase 成内容相同的 `348cb5e`，`source-commit` 与 HEAD 记后者）与当前在跑的 `main-20260915-dbe1edc`；`registry-v5-20260913-2d3c50e` 与前者保留作回滚。

## [open] ISSUE-STATIC-20260915-1b5e：请求路径含 NUL 字节时静态文件处理返回 500 而非 404

- **Type**: bug
- **Priority**: low
- **Discovered**: 2026-09-15，review-gate 的探针：`_serve_static("/web/app.js\x00")` 在 `Path.resolve()` 抛 `ValueError: embedded null byte`，落到 `_handle_request` 的 `except Exception` → `send_error(500, str(exc))`。改动前后行为相同（基线）。
- **Description**: 500 会带出异常文本，且与"服务端真的坏了"同形。应在 `_serve_static` 里把含 `\x00` 的路径直接判 404。

## [open] ISSUE-NETWORK-20260909-43b8：IPv6 告警把本地地址推断为公网泄漏

- **Discovered**: 2026-09-09
- **Priority**: medium
- **Owner**: tt-web / ip_check

`ip_check/cli.py` 的 `get_ipv6()` 仅对 IPv6 UDP socket 调用 `connect()` 和 `getsockname()`，没有发送公网请求；JSON 将地址非空直接作为 `local.ipv6_leaked`，结论分别写“real address is exposed”或“IPv6 is disabled”。本地选源地址不能单独证明端到端泄漏，取不到地址也不能证明系统禁用了 IPv6。`web/app.js` 的 IPv6 标签同样消费该二值推断。

本次 Mac Studio 告警另经 `curl -6 --noproxy '*' https://api6.ipify.org` 成功回显相同运营商 IPv6 得到独立确认，随后按用户授权关闭 Wi-Fi IPv6 并修正 Clash 全局覆盖。恢复后两个 IPv6 HTTPS 端点无法连接，两个公网 IPv6 字面量连接均返回 macOS errno 65，页面 API 不再报 IPv6 泄漏。现场修复不使当前探针获得通用泄漏判定能力。

后续修复应区分本地 IPv6 地址、实测公网可达/出口身份、失败或未核实，避免仅改变文案便宣称检测已完备。本轮只恢复真实网络链路，未修改检测器。复核入口：`ip_check/cli.py:get_ipv6`、JSON 的 `ipv6_leaked` 与 conclusions、`web/app.js` 的 IPv6 展示，以及 `tests/test_ip_check_json.py` 的现有 mock 覆盖。

2026-10-14 metric audit: Web and both CLI renderers now disclose local-address evidence and `ipv6_leak_unverified`; missing public/DNS/timezone evidence no longer yields an unqualified healthy result. The legacy positive heuristic and thresholds remain for compatibility. Actual end-to-end IPv6 leak measurement is still not implemented, so this issue remains open as a detector capability limitation rather than a claim that the label change proves leakage.

---

## [resolved] 出口选路的发布方尚未实现，消费侧只能信任一个手工维护的文件

- **Type**: incomplete-integration
- **Discovered**: 2026-08-07
- **Resolved**: 2026-08-07（system-config `2d0b7e6`）

**原问题**：`ip_check/cli.py` 的 `resolve_route()` 每轮从 `~/.config/agent-proxy/current-proxy` 读取本轮出口线路，用来绕开「长驻 server 在 fork 时冻结了 `HTTP_PROXY`，线路一切换就永久指向死端口」这个故障。消费侧先行落地时，写这个文件的一侧还没有——该改动当轮被并发写入者阻塞。期间文件由人手工维护，切线路后忘记更新就会复现原故障。

**解决**：system-config 的 `shell/common.sh` 增加了 `_sc_publish_proxy_addr`，挂在 `zshrc` / `bashrc` 的 `_refresh_proxy_addr` 末尾——所有 `enable-proxy-*` 最终都会调它，且它每个提示符都跑。它读后再写（不一致才落盘）、必定原子替换，目标是目录或 FIFO 时放弃。

**验证**：改 `mode` 后开一个新 shell，不做任何手工操作，`tt-web network` 的「本次探测出口」与公网 IP 即跟随新线路（gcp `35.198.217.6` ↔ zyt `185.126.80.150`），切回亦然。

---

## [open] 兜底价目表的 bare key 会盖过在线表里只有 provider 前缀的同名条目

- **Type**: pricing-precedence
- **Discovered**: 2026-08-07
- **Priority**: medium

`_with_bundled_supplements()` 用 `setdefault` 把 `pricing.json` 的条目补进 LiteLLM 表，`resolve_model_key()` 又优先精确匹配。若 LiteLLM 某天只发布 `anthropic/claude-sonnet-5` 而不再发布裸 key，补进去的裸 key 会精确命中、把在线价挡掉。

这条优先级同时是 `glm-4.7` 修正的依据：在线表对它唯一的匹配是模糊命中 `cerebras/zai-glm-4.7`（$2.25/$2.75，另一家托管商），而 z.ai 自己的价是 $0.6/$2.2。所以「在线表永远优先」和「精选兜底优于模糊猜测」在此冲突，没有单一规则能同时满足——区分点是模糊匹配有没有跨厂商，而当前 matcher 判不出来。

触发需要 LiteLLM 改变 key 命名，属上游 schema 变更，故未就地修。真要修，方向是让 `resolve_model_key` 区分"精确/同厂商模糊/跨厂商模糊"三档，而不是继续在补表时机上打补丁。

## [open] 57 个跨文件重复调用的 project 归属不一致，去重按路径序任选其一

- **Type**: attribution-ambiguity
- **Discovered**: 2026-08-07
- **Priority**: medium

Claude Code 在 resume/fork 时把整份 transcript 复制进新 session 文件，同一次 API 调用因而出现在多个文件里。`aggregators._deduped()` 保留先遇到的那条（路径排序，故确定但任意）。实测有 57 个 `dedup_key` 在不同副本里带着不同的 `cwd`——多为 worktree 与主仓、或 subagent 换过工作目录，token 与 cost 一致、只有归属不同。

结果是这 57 次调用的成本被记到两个候选 project 中排序靠前的那个。总额不受影响，按 project 切分的视图会有偏差。要修需要一条能判"哪个副本的 cwd 才是这次调用真实发生地"的规则，源日志里目前没有能直接支撑该判断的字段。

## [open] `codex.load_entries()` 按 session_id 去重，会把同一线程 resume 出的独立分支合并掉

- **Type**: latent-undercount
- **Discovered**: 2026-08-07
- **Priority**: medium

`parsers/codex.py` 的 `load_entries()` 用 `if entry.session_id in seen: continue` 去重。但 resume 一个 Codex 线程会写出新的 rollout 文件、沿用原 `session_meta.payload.id`，各自从同一起点独立累计 token——实测某个 id 下 10 个文件的首个 total 都是 28,316，事件数 866~2025 各不相同，是各自真实计费的分支而非同一次运行的重复日志。按 session_id 去重会丢掉其中除一个之外的全部。

实测影响：1,799 个 rollout 文件里 1,608 个唯一 session_id，按此去重后 Codex 成本从 $18,579 掉到 $7,904。

dashboard 不受影响——生产路径走 `aggregators._parse_usage_file()` 而非这个函数，且 `UsageEntry.message_id` 现已是 rollout 文件名，全局去重不会合并分支。该函数目前只被测试调用，故未改动其语义。

## [open] `/api/restart` 是无认证写端点，且服务默认绑 0.0.0.0

- **Type**: exposure-surface
- **Discovered**: 2026-08-20
- **Priority**: low（当前暴露面下）

`server.py` 的 `do_POST` 只认一个路径 `/api/restart`（`:319` → `:324` `_handle_restart`），它做完 `_compile_check()` 就 `_schedule_reexec()` 让进程重新 exec 自己。这条路径**没有任何认证、来源检查或确认**。

同时服务并非只绑 loopback：launcher `tt-web/tt-web:18` 是 `BIND_HOST="${TT_WEB_BIND:-0.0.0.0}"`，运行中的进程命令行确为 `--host 0.0.0.0`，`lsof` 显示 `TCP *:39001`。所以 Tailnet / LAN 上任何能访问该端口的客户端都能重启这个服务。

**注意 `server.py` 会给出相反的读数**：`:49` 的 `_BIND_HOST` 与 `:1267` 的 argparse default 都写着 `127.0.0.1`，但 launcher 显式传 `--host` 覆盖它。只读 server.py 判暴露面会判错——这个坑已经真实发生过一次。

**当轮未修的原因**：2026-08-20 的账号记忆改动要新增一个删除端点，借此评估了可达档位。用户明确裁决**沿用既有档位**（该 plan 的 D5）：既然 `/api/restart` 这个权限更大的端点已经敞着，只给新端点加锁不会真的提高防护。这条记录的是**事实与那次裁决的适用前提**，不是待办。

**什么时候要重新裁决**：服务的暴露面变化时——接入更宽的网络、多人使用、或 `TT_WEB_BIND` 的默认值被改动。届时 `/api/restart` 与账号记忆的删除端点应一并处置，不要只看其中一个。

## [open] 账号记忆的删除可被一个"删除前取得 admission"的在途请求复活

- **Type**: race-condition
- **Discovered**: 2026-08-20
- **Priority**: low（后果是"删掉的账号又出现一次"，再删一次即可）
- **Status**: 用户显式 waive，带着它交付

`/api/account-memory/remove` 永久删除一条 remembered 记录后，一个**在删除之前就取得了 admission 快照**、但尚未进入内层 epoch context 的 overview 或 sync-publish 请求，仍可能把该账号写回：删除发生时若没有 active upsert epoch，墓碑会被 GC 立刻清空；随后那个旧请求注册到删除**之后**的 epoch，从**旧**快照派生候选，比较时已无墓碑可依，于是写回。

overview 侧的窗口尤其宽——它在取得 admission 与注册 epoch 之间还夹着 rollup 查询。

**这不是没修，是修了三轮后确认它落在一个坐标轴上**，三个打点位置各错一头：

| epoch 打点位置 | 结果 |
|---|---|
| 入口开始 | 跨越 generation/rollup 工作 → 删除后取得的新读数被误拒 |
| **admission 取得时** | 理论正解：删前取得的快照正确拒绝、删后取得的正确接受 |
| 派生循环（当前实现） | 本条竞态 |

真要修，方向是把 `_account_memory_upsert_epoch()` 上移到包住 admission 快照的获取。当轮未做是因为已连续两轮出现"新问题来自上一轮修法"，用户裁定带着它收口。

**附带两条同源边界**：

- tombstone map 只有最终收敛、没有硬上界：任一 derive/write context 长期不返回时，`oldest_active_epoch` 不前进，其后所有不同 key 的墓碑都不能回收。个人账号规模下风险很低。
- `tests/test_account_memory.py` 里覆盖该路径的测试**不区分 admission 是在删除前还是删除后取得**，且在墓碑已被 GC 时对 epoch 语义不具区分力。它的名字与注释已按此更正，别再据旧名推断覆盖面。

## [open] ISSUE-TEST-20261004-6b2a：拆分时复现的七项既有测试失败

- **Discovered**: 2026-10-04 独立项目提取验证。
- **Owner**: agent-monitor 测试维护者；本轮保留原行为，不借拆分修改生产逻辑。
- **Attribution**: 基线独立、非边界。冻结 ai-agent-config `3d61487d5386dfe9bb6ba7d26807005bbf0bd7f2` 与提取版本，在同一 Python 3.13.12 / SQLite 3.50.4、同一独立 Gateway reader、隔离 HOME 下重跑首轮失败的 11 项，两者均为 6 failures + 1 error，失败名称和原因一致。原始日志位于提取任务的 `failed-baseline-313.log` / `failed-target-313.log`；下表保留不依赖本机日志路径的复现入口。

| unittest 测试入口（前缀 `tests.`） | 既有失败 |
| --- | --- |
| `test_account_memory.AccountMemoryTests.test_pivot_sync_publish_remembers_account_before_later_signed_out_publish` | mock `sync_all()` 不接受当前 `quota_refresh` 参数，账号历史文件未生成 |
| `test_gateway_v5_reader.GatewayV5ReaderTests.test_legacy_pin_validates_against_frozen_resolved_target` | fixture 期望 schema 7，当前 Gateway 生成 schema 8 |
| `test_gateway_v5_reader.GatewayV5ReaderTests.test_modes_survive_snapshot_round_trip_and_existing_projection` | 同上 |
| `test_llm_attempts.HttpAndStaticContractTests.test_filter_registry_drives_controls_url_restore_change_clear_and_visible_profile` | JavaScript 测试注入已不存在的 `loadFilters` 符号 |
| `test_llm_attempts.ProjectionTests.test_public_projection_uses_frozen_field_allowlists` | 旧 allowlist 未含当前 `caller_route_constraint` |
| `test_progressive_freshness.ProgressiveFreshnessTests.test_statistics_are_published_before_quota_and_request_stays_pending` | 旧两次采集 mock 不接受当前 `progressive` 参数 |
| `test_statistics_snapshot.StatisticsSnapshotTests.test_v3_archive_and_v5_source_preserve_funding_and_cost_partitions` | fixture 期望 schema 7，当前 Gateway 生成 schema 8 |

首轮 Python 3.9.6 全套 784 项还有四个环境相关失败：两个 SQLite WAL 测试、`sys.stdlib_module_names` 不存在、GatewayApplication fixture 缺 `httpx`。同一四项在新旧源码的 Python 3.13.12 测试环境均通过；测试说明因此要求 Python 3.10+ 和 `requirements-dev.txt`，不据此提高生产代码的 Python 下限。本轮独立安装、helper、quota、exporter 和 Web 静态回归 107 项在该环境通过，不能替代上述七项，也不代表整套测试全绿。

2026-10-06 调用页改造再次复现上表三项：`test_llm_attempts` 32 项中 30 通过，失败仍为旧 `loadFilters` 注入和缺少 `caller_route_constraint` 的 allowlist；`test_progressive_freshness` 9 项中 8 通过，失败仍为 mock 不接受 `progressive`。本次新增详情/导出 HTTP 4 项、交互 4 项和既有 Web/static 38 项均通过；它们不消解这三项既有失败。归属为用户指定的另一个后端优化/断言修复 session，本次 UI 单元不修改其行为或断言。复现命令为 `.venv/bin/python -m unittest discover -s tests -p test_llm_attempts.py` 及对应 `test_progressive_freshness.py`；本次 worktree 使用主 checkout 的同一 `.venv/bin/python`，详情见调用页 ADR `20261006-d83e`。

2026-10-07 Codex 账号操作改动的全量回归收集 813 项，得到 4 failures / 10 errors（含 subtests，共 11 个测试方法）。在改动前 `8182b64` 的独立归档、同一 Python 3.13.12 / Node 26 / Gateway reader 和隔离 HOME 下重跑这 11 项，失败名称和数量完全相同。除上表既有问题外，`test_gateway_schema8` 的 round-trip / field-validation 与 `test_gateway_v5_reader` 的 session-UUID / forbidden-field 测试也被当前 Gateway schema 9 对旧 snapshot validator 的不兼容阻断。没有改 Gateway 或放宽 snapshot 契约；归属仍为 Gateway reader / 测试维护任务。最终账号、Web/static、overview 相关 85 项通过；这不表示全套测试通过。

## [resolved] ISSUE-CODEX-20261007-8e41：双服务共享账号状态时可能覆盖刚完成的操作

- **Priority**: medium
- **Owner**: agent-monitor 账号操作维护。
- **Attribution**: 本次改动增量。独立审查与主线程各自以内存锁交接复现，未观察真实部署双服务运行。
- **Trigger**: 两个服务进程共用 `state/codex-accounts`。观察进程的 `Manager.view()` 读到旧 `sending/unknown`；执行进程保存 `succeeded` 和 quota 后释放锁；观察进程取得锁后未重读，随后以 `interrupted` 保存旧记录，丢失刚完成的状态及 quota。
- **Disposition**: 普通单服务进程通过本地 `jobs` 避开此恢复分支，本轮按该运行包络交付，不扩为多服务部署。以后支持双进程共享状态前，应在取得恢复锁后重读记录，再判断是否需要标记中断。
- **Acceptance gap**: 官方账号登录、真实消息与实际 reset 行为仍需真实账号验收；合成协议与浏览器读数不能证明「首次使用后加七天」。未取得该读数前，不将新流程记为已验收 UX 契约。

2026-10-07 批次扩展修复：`Manager.view()` 在取得恢复锁后重新读取并判断终态，避免把刚完成的结果覆盖为 interrupted。`test_observer_rereads_after_worker_releases_lock` 用锁交接时写入 succeeded 的确定性时序验证保留结果。上方 Disposition 保留为首次登记时的处置；这次修改不宣称支持多服务部署。

批次 UX 验收缺口：已批准的一键全账号、持久结果、确定未发送项继续与显式新轮行为，待真实账号授权 / 消息 / 服务端 reset 端到端验证后再转为契约演化候选；现有合成协议和隔离浏览器验证只覆盖本地编排及页面，owner 为 agent-monitor 本功能的真实账号验收，需用户完成官方授权。

## [open] ISSUE-SYNC-20261008-b74e：MacBook 采集间歇等待 rollup 锁超时

- **Discovered**: 2026-10-08，调用页性能部署后的真实 Hub 状态检查；此前同日多轮已观察到此现象。
- **Priority**: medium
- **Owner**: agent-monitor 来源采集维护；本次列表优化记录，未改变 MacBook 服务或锁。
- **Evidence**: MacStudio 的 `/api/sync-status` 最后报告 macbook 的 `last_attempt_outcome=failure`，reason 为取得 `/Users/lindong/.local/share/agent-monitor/state/rollup.db` 锁超时；仍保留 generated_at=2026-10-08T05:37:06.226297Z 的 admitted generation。此前失败后也成功推进过，不能推断为永久卡死或简单删除锁即可恢复。
- **Attribution**: 基线独立、非本次列表读取边界。
- **Next**: 来源采集维护者按实际 rollup/export 持锁者和轮次耗时定位，区分正常长持锁与重复采集争用，再确定修复；服务变更另取对应授权，不以列表变快代表采集已恢复。

- **2026-10-12 复验**：本轮 Web UI 审查期间，真实调用页再次显示 macbook 采集取得 rollup 锁超时，并保留旧读数、来源时间和错误原因。仅确认页面报告了该状态，未检查远端持锁者或认定持续故障；来源维护仍归本条。
- **2026-10-12 当前状态补查**：Hub API 在 08:35 UTC 附近显示 MacBook 最后采集成功（08:34:15Z），生成时间推进到 08:33:49Z，scan_complete=true、source_error_count=0。本轮未复现锁超时，未删除锁或改采集调度；间歇争用根因仍归本条来源采集维护。MacMini 当前另有 1 个 blocked source，成功采集不代表其项目归属覆盖完整。

## [open] ISSUE-CALLS-20261008-9a72：定向详情读取仅支持 HTTP query 的列表值形态

- **Discovered**: 2026-10-08，本次性能改动的独立审查。
- **Priority**: medium；改动依附，非阻断。
- **Owner**: agent-monitor 后端维护者；本轮记录，采纳扩展由用户裁决。
- **Evidence**: Gateway reader 接受 query 值为字符串、tuple 或 list；新增定向读取按 HTTP `parse_qs` 的 list 形态取值，内部直接调用传字符串或 tuple 时返回 404，旧完整读取可返回同一请求的两个 attempts。现有 HTTP 入口始终传 list，不受该差异影响。
- **Disposition**: 本次按网页 HTTP 运行包络交付，未扩展内部调用形态；以后支持此类直接调用前，应复用一致的单值归一化并验证三种形态。

## [open] ISSUE-QUOTA-20261009-741d：session-quota 非法字段形状未使用来源不可核退出码

- Type: bug
- Priority: medium
- Discovered: 2026-10-09，Wave128 独立 change review（finish_68）。
- Description: 新 `session-quota FULL_UUID --json` 入口只捕获 ValueError/OSError；若合法 JSON 的 `token_count.rate_limits` 是数组等非法字段形状，复用 parser 抛 AttributeError，CLI exit 1 并打印 traceback。README 将来源不可核约定为 exit 2，而 exit 1 表示缺 rollout 或无可用 weekly reading，消费者会混淆二者。
- Evidence: 隔离 HOME 下，rollout 首条 session_meta 与完整请求 UUID 相符，后续 token_count 的 `rate_limits=["invalid"]`；独立 reviewer 实测 stdout 为空、exit 1、stderr 有 AttributeError。仅合成异常形状，真实 provider 出现率未核；正常 public reader/CLI 与既有 parser 回归不受此结论替代。
- Notes: 用户 2026-10-09 已裁决“单列待办，先交付”，本轮接受此非阻塞边界，不改已审源码、不追加 review。后续归 CLI/parser owner 定点统一非法来源的受控失败与退出契约，不新增 provider 请求或扩大成 schema 全矩阵。


## [open] ISSUE-CALLS-20261012-5f26：按需完整筛选仍有约30秒等待

- **Observed**: 2026-10-12 真实 MacStudio Hub，30d 按钮点击至完整选项加载成功32162ms，响应 Server-Timing app31905.44ms；新 UI 7d 同入口30336ms后成功，一次点击仅一个请求。不是永久卡住。
- **Cause / Scope**: `/api/llm-call-filters` 仍向独立 Gateway reader 提供 admitted 来源的完整请求/尝试历史；本轮没有改变该 reader 或它的全量投影成本。已有按需设计保留，前端补重复点击去重、条件失效和失败重试状态。
- **2026-10-12 follow-up**: 本轮真实浏览器再次观察到完整选项一次加载约 30.4 秒（`ui-reaudit-20261012/evidence/calls-result.json`，30419ms）。这是单次观察，不是冷热性能基准，也不据此新增 SLA。
- **Owner / Next**: agent-monitor 后端维护者；后续性能工作应从筛选所需字段的定向投影/聚合入手，并协调 Gateway reader 契约。本轮按 UI 缺口范围交付，不扩大修改另一个仓库。当前能用，但等待仍在。
- **Evidence**: 本机 `~/.local/share/agent-reports/ui-workflows-20261012/live-options-before.json`、`live-other-result.json`；含私有运行环境读数，不作公开演示数据。


## [open] ISSUE-PRODUCT-20261012-72c1：周期比较缺少变化来源归因

- **Type / Priority**: 产品改进建议 / medium；不是现有合同违约。
- **Expected source**: README 的每日/每周复盘与消耗归因场景；当前合同只承诺两个完整等长周期的已记录总量比较。
- **Observed**: 2026-10-12 复查本地 `bee318c`，`web/app.js` 的 `renderComparison()` 只呈现两期已知成本、token、未知定价条目和总差额。当前线上 app.js 字节与该源码一致。用户要知道哪些会话目录、模型或机器贡献了变化，仍需手工在透视页分别设置两个窗口并比较；这一项是实现检查得到的能力边界，不是本轮完成的双窗口用户试用。
- **Proposal**: 在已有比较中提供两期相同维度的增减贡献及到透视的准确日期入口。保留未知定价与覆盖不完整说明，不能把缺记录解释成节省，也不推断因果。
- **Owner / Next**: agent-monitor 产品与实现维护者；由用户决定是否进入下一轮功能范围，本轮只审查和记录。

## [open] ISSUE-PRODUCT-20261012-b9f4：接入说明尚未形成首次数据到达检查

- **Type / Priority**: 产品改进建议 / medium；不要求新增配置写入向导。
- **Expected source**: README 的独立安装、多机监控场景和 `docs/references/open-source-preparation.md` 的采纳者可用性目标。
- **Observed**: 2026-10-12，已部署 `/guide` 与本地 `bee318c` 页面字节一致；页面提供来源解释、配置/安装命令和机器状态，但明确把 Gateway 来源状态交给调用页。当前说明不提供同一处逐来源的首次有效观测、未配置/无匹配/采集失败区分及对应检查入口。尚未做全新安装，因此不把这些结构事实称为实际安装失败。
- **Proposal**: 在现有只读接入页归拢来源是否配置、是否产生首份有效数据、最后观测和下一步检查入口；优先复用已有状态，不增加自动登录、后台发送或自动改配置。公开依赖可取得性、中性默认配置和干净安装仍按开源准备清单处理。
- **Owner / Next**: agent-monitor 产品与实现维护者；本轮只记录建议，待确定下一轮功能范围。


## [open] ISSUE-PRODUCT-20261012-63d2 — 机器可用状态没有呈现已知的部分来源阻止

- **Type / Priority / Expected**: 能力缺口建议 / medium；不声称已有合同逐字段承诺 blocked-source 提示。README 多机监控、检查来源时点与失败的场景，以及 guide 的当前采集状态。机器纳入与来源完整性是两回事，不能把本项解释为要求放松来源保护。
- **Observed**: 2026-10-12 当前 MacStudio Hub `/guide` 的 macmini 卡显示“可用”；同轮真实 `/api/sync-status` 为 admitted=true、stale=false、last_attempt_outcome=success、scan_complete=true，但 statistics.blocked_source_count=1。guide 虽有“已纳入不保证每种来源都存在或历史完整”的通用说明，没有把这一项已知缺口呈现在该机卡片上。其他三台 blocked_source_count=0，不能把它们一并报成受影响。
- **Reproduce / Evidence**: 打开 `/guide` 读取 macmini 状态，与同一轮 sync-status 配对。隔离真实浏览器证据：`~/.local/share/agent-reports/ui-reaudit-20261012/evidence/guide.json`、`sync-status.json`；主线程另读取当前 API 核实该计数。线上 Web signature 为 `5555e188edd051e8`。
- **Recommendation / Owner**: agent-monitor Web 维护者在机器状态保留“快照可用”，另外显示“部分来源未计入”及数量、可解释的原因/诊断入口；保留旧数据与身份保护。本轮只记录，不修采集器，不断言机器离线或数据损失。

## [open] ISSUE-PRODUCT-20261012-81f0：回访与首次使用的几个可发现性改进点

- **Type / Priority**: 低优先级产品建议；均未作为现有合同缺陷或必须实现项。
- **Observed / Source**: 本轮真实浏览器 390px 的 guide 数据映射表宽 720px、滚动区宽 322px，“查看入口”列需要局部横滚；六项导航中的接入入口初始在屏外，但横滚后网络导航实际可达，符合现行 `docs/design.md`。配额已有“剩余额度少优先”、重置和观测时间排序，适合查快耗尽账号；直接找余量多的账号没有对应反向排序。调用筛选保留英文原始枚举，符合现行语言契约，但可为首次访问者增加中文解释。
- **Proposal / Owner**: agent-monitor 产品维护者可按首次接入与挑选账号的优先级考虑：窄屏来源说明与入口成组；更易发现接入入口；余量多优先或可用账号视图（陈旧/未知不能当当前可用）；枚举旁补解释而不改取值。属于用户可选的产品优化，不在本轮实施。
- **Evidence**: `~/.local/share/agent-reports/ui-reaudit-20261012/issues.md` 与 `evidence/mobile-result.json`、`tail-result.json`、`journey-result.json`。这些截图与原始读数含私人部署信息，不作公开演示资产。


## [open] ISSUE-20261013-test-drift Existing suite fixtures lag consumer paths

Owner: agent-monitor test maintenance. Clean163be3c baseline reproduces test_aggregators.test_production_daily_rollup_and_attach_sites_match_explicit_census (benchmark make_snapshot not classified) and test_account_memory.test_pivot_sync_publish_remembers_account_before_later_signed_out_publish (mock sync_all missing quota_refresh). Both also fail in the accounting correction's874-test run. Correct the census and mock under their own behavior scope; this metric repair does not silently waive or disable them. Evidence: metric-review-20261013/product-tests-isolated.log plus baseline2-test replay in task transcript.

## [open] ISSUE-20261013-lineage-notification Source completeness is pull-visible only

Owner: agent-monitor service maintainer. The accounting correction surfaces unresolved Codex ancestry and incomparable Claude observations through source_errors/scan_complete and existing snapshot/Hub coverage indicators. It does not add a proactive push route; absent a page visit these failures may go unnoticed. Service-operations6.1 self-check therefore leaves a named follow-up to connect source completeness to the project's approved alert route with recovery/dedup semantics. This task repairs the requested calculation and shared on-demand review workflow; it does not claim an autonomous production detector is deployed. Raw source gaps remain visible unknowns rather than guessed zeros.

## [open] ISSUE-PERF-20261013-observer: process-external observation and notification

Owner: agent-monitor deployment maintainer. The performance observer detects long-running operations and stale source observations while its process can run, and exposes local HTTP/files. Whole-process freeze/death leaves the last file in place and cannot generate a fresh event; existing system-config watchdog's configured label set does not include agent-monitor (read on 2026-10-13). External message delivery has not been integrated. Reassess when enrolling Monitor in the shared operational monitor or when repeated stale diagnostic files are observed. Consume instance_id, sampled_at and last_written_at from the deployed state file plus a bounded HTTP probe; use the approved notification route and its recovery/dedup contract. Do not interpret retained observer_running=true as a live heartbeat. This task adds bounded local observation without a new service or external message destination.

## [open] ISSUE-SERVICE-20261013-restart-race: immediate reinstall races launchd removal

Owner: agent-monitor lifecycle maintainer. During the 2026-10-13 performance rollout, `agent-monitor restart` returned zero with `hub: stopped` then `existing launchd service unchanged`; subsequent `launchctl print gui/501/com.agent-monitor.hub` reported no job and the real HTTP入口 refused connections. `stop-web` followed immediately by `install-web` can inspect the still-visible stopping job and skip bootstrap. Recovery through the same `hub.py install-web` restored health and the browser, without deleting state or locks. Evidence: performance-20261013/final-deploy-macstudio.log, failed 40-second startup probe, then health-final.json. Reassess before the next automated restart: distinguish stopping/running ownership state and verify the final job/consumer; reproduce with a bounded launchd-removal fixture before changing lifecycle. This baseline issue was discovered during deployment, restored immediately, and remains outside the query/diagnostic change.
