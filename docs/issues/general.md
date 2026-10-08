# tt-web Issues

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

## [open] ISSUE-EXPORT-20260915-e3a1：gateway ledger 一段读不了，整台机器的 export 就失败，用量与配额一起停更

- **Type**: bug
- **Priority**: high
- **Discovered**: 2026-09-15，hub 页显示 `tencent-webserver-china` 刷新失败；在该机手动跑 `~/.local/bin/tt-web export --out <tmp>/bundle --cached-quota` 复现，exit 1，末行 `llm_attempts.ProjectionError: Audit ledger schema is unsupported`
- **Description**: `statistics_snapshot._gateway_snapshot()` 对 ledger 只有 `missing` / `available` 两态，`_validate_schema` 抛出即让 `write_snapshot()` 整个失败，于是 `exporter.export_bundle()` 不产出 snapshot——该机的 Claude Code / Codex 用量、配额读数全部停在上一代，只因为 LLM Gateway 的 ledger schema 比这台机器上的 tt-web 新。契约 H8 要求"ledger 不可读 / schema 不兼容时显示明确 error"，但这个错误在 export 阶段就把整份快照否掉了，页面永远走不到 H8 那一态。
- **Notes**: 修法方向是给 gateway 段一个 `error` 态（`state: "error"`, `reason`），usage 段照常写入，hub 的 LLM Calls 按 H8 显示该机 ledger 错误。这是跨 `statistics_snapshot` / `llm_attempts` 读取端 / server 的行为决策，当轮未做。**这台机器的即时修复是更新其 tt-web checkout**——2026-09-15 已做（部署 commit `a831a34`，导出恢复，见 `archive/closed.md` f2b9），本条只剩设计缺陷本身：下一次 gateway 与 tt-web 版本再错开时，同样会整台停更。

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

## [open] ISSUE-CALLS-20261006-6d4a：调用页真实 Hub 的长等待与来源快照故障仍需后端处理

- **Discovered**: 2026-10-06，调用页 UI 改造前的真实入口观察。
- **Priority**: medium
- **Owner**: 用户指定的另一个 agent-monitor 后端优化/断言修复 session；本单元记录、不修复。
- **Attribution**: 基线独立、非边界。
- **Evidence**: 四个已纳入来源、498,194 请求/469,818 尝试；一次 `/api/llm-calls-page` 浏览器资源耗时 45,390ms，后续一次 fetch 失败；macstudio 来源显示 unsupported gateway snapshot，观测时间仍旧。此单次读数不表示长期平均或由 UI 变更引起，来源故障与既有 `ISSUE-EXPORT-20260915-e3a1` 的版本兼容边界相关，尚未证明同一根因。
- **Next**: 在真实 Hub 入口核对服务版本、admitted snapshot 身份及失败来源，分解读取等待后复测；新详情/导出使用既有完整 audit reader，本次不宣称获得 Gateway indexed UI 的性能保证。调用页布局和隔离 fixture 验证不能替代此项。

2026-10-06 发布 `25697a3` 并重启既有 Hub 后，主线程从真实 7d 页面读到 50 行、409,355 匹配请求并打开一条完整详情；列表/详情资源各一次读数为 39.916s/36.493s。此处只追加生产等待证据，不作跨时提速比较，亦不据部署健康与单条详情宣称来源快照故障已修复。归属继续为用户另一个后端 session。

2026-10-08 用户授权本性能 session 实施优化。现场确认 MacStudio 的 Gateway 已是 schema 10，而 monitor 仍通过只列出 3–8 的旧 fingerprint 字典拒绝快照；其余三个来源已更新到 10 月 8 日。已在本地补齐 9/10 精确字段校验，并将详情改为在 SQLite 中选定请求后再解码完整尝试链；同一生产快照副本的详情 profile 从 23.783s 到 1.220s/1.001s，隔离浏览器两条请求三次打开为 0.930s/0.947s/0.925s。此 issue 保持 open：真实 Hub 的部署、快照恢复和部署后响应验收尚待用户许可；执行归本 session。列表仍用完整读取，其后续优化留在本 issue，不以详情优化代表列表已改善。完整读数与测试边界见 [运维记录](../operations/services.md#2026-10-08-snapshot-compatibility-and-detail-performance-local-validation)。

2026-10-08 用户明确批准后，`a94dfa9` 已推送并部署 MacStudio。schema 10 新快照已纳入，真实调用页显示 10 月 8 日 10:46:19 的观测时间及当日请求，旧快照拒绝问题在该来源恢复。性能只完成部分改善：旧请求重复 HTTP 读取 1.961s；新快照两条请求的三次详情渲染为 14.569s/4.094s/16.586s，列表一次 29.149s。另一个只读 probe 取得 MacStudio generation 用时 16.044s，其他三来源 0.019–0.214s；此耗时含获取与校验，尚未将锁等待单独计时。发布期锁内保留检查/复制/校验仍是剩余等待的候选，后续应先分解此段再决定是否缩短临界区，保留租约、身份与历史保留语义。该残余项及列表优化归 agent-monitor 后端维护者，已记本 issue，本轮不改发布协议；不以本地副本约 1s 宣称线上等待完全消除。后续自动轮次还读到 MacBook rollup-lock timeout，保留旧快照，根因未诊断；本次仅获 MacStudio 部署许可。详见 [部署记录](../operations/services.md#2026-10-08-snapshot-compatibility-and-targeted-details-release)。

2026-10-08 第二轮按用户要求继续定位：在同一份 MacStudio 生产快照副本（369,775 requests / 369,825 attempts，原文件 1,519,538,176 bytes）上，完整历史保留检查耗时 27.051s，是原发布锁覆盖的耗时步骤。现将其移到锁外，并用既有租约保护历史、在锁内重核 current 指针；冷 generation 校验也在取得租约后移出机器锁。修改后的实际发布持锁阶段 1.230s，含复制、校验与指针切换，整个替换仍为 28.842s。相同传输 manifest 的完整验证由两次减为一次。详情 SQL 的四个查询条件原来均为全表扫描；导出时添加可选索引后，同一请求的原始行读取从 0.950s 降至 0.000250 / 0.000124 / 0.000103s，内容逐字段相同；一次建索引 2.074s，文件增加 40,824,832 bytes。索引不改变版本化表、payload 或既有读取兼容性。

隔离 headless 浏览器使用这份带索引的副本，实际点击两条 success 请求、三次详情渲染为 1.046s / 0.017s / 1.009s（首次、重复、发布期间切换）；最后一次发生在另一进程执行 31.364s 保留检查期间，未等到发布完成。机器/项目、历史候选与完整单次尝试均已读到，成本 unknown 保持未知。此样本仅覆盖本地副本的两个请求，不代表生产或尾延迟。列表一次仍为 16.587s，其完整历史投影没有在本轮改写；继续归本 issue 的 agent-monitor 后端维护范围。第二轮改动尚未部署，线上复验须取得本轮 push / MacStudio 更新许可；不将上一轮针对 `2933060+a94dfa9` 的许可扩大到新提交。

第二轮回归边界：受影响八个测试模块共 89 个方法，得到 7 个失败断言，集中在 5 个旧 schema 假设的测试方法；在改动前 `bf28f21` 的独立归档、同一 Python 3.13 / Gateway / 隔离 HOME 下，这 5 个方法同样产生 7 个失败断言。它们属于既有 Gateway 测试漂移（基线独立），本轮不改其断言，也不宣称全套测试全绿。最终定向 61 个方法通过（详情 fixture 为 2 台来源、3 个项目；当前 schema 8/9/10；并发与串行、冷与热 admission；切指针前后两种崩溃位置、同进程和跨进程租约）。新增测试覆盖保留检查期间读取、竞争发布重检、冷校验租约与 GC、四种索引查询计划；既有真实子进程崩溃恢复和跨进程租约测试保留。诊断子代理及一次恢复均被登录令牌刷新失败阻断，没有独立诊断产出；本轮结果来自执行者测量与回归检查，生成后 gate 采用可直接捕获上述回归的自动检查，不宣称独立外审。原始读数与实际测试加载路径/文件摘要保存在本机 `~/.codex/task-artifacts/monitor-contention-20261008/`。

2026-10-08 第二轮已获用户许可并将 `c37ce00` 推送、部署到 MacStudio；带四个详情索引的 schema 10 快照已发布，页面读到推进后的观测时间。首次详情 1.624s，但后续两条请求的三次打开仍为 15.096s / 0.113s / 12.909s，另一次重复为 1.358s；不能结案或宣称等待稳定消除。30d 列表两次 43.557s / 67.886s。现场独立进程冷调用详情 1.013s；同机成对真实 HTTP / 独立函数三次为 4.968s/1.007s、0.044s/0.012s、0.053s/0.007s。Hub 原生线程采样显示大量 JSON/GC 工作及主线程等待 Python 执行锁，支持继续诊断后台全量处理与 HTTP 共进程的争用，但该采样并非与两次慢点击同时取得，不作精确因果结论。本性能 session 继续承担此残余诊断。最终同步还读到 MacBook rollup-lock timeout 复现、MacMini 导出报 database or disk is full；这两项属来源刷新运维，未包含在 MacStudio-only 服务变更许可内。完整现场身份、读数及边界见 [第二轮部署记录](../operations/services.md#2026-10-08-indexed-details-and-publication-contention-release)。

第三轮已在隔离分支实现 Hub 同步进程隔离，保留原有校验、历史、身份与刷新状态。相同生产副本、2 条详情、每种方式 80 次 HTTP 读取覆盖完整保留检查：线程模式中位/最大 0.00799s/1.17950s，进程模式 0.00530s/0.01040s；保留检查分别 26.550s/26.500s。受影响 5 模块的 77 个测试方法通过；测试断言吞异常的缺口已修并以反向对照验证。独立审查遗留项是新 generation 的父进程冷 admission 校验（缓存不跨进程），本轮不取消此校验，归本性能 issue 后续真实入口验收。线上效果仍未核实，下一动作是用户批准本轮具体提交的 push / MacStudio 部署后，由本 session 更新并复验；30d 列表及另外两台来源故障仍未结案。测试期间旧 mock 错位曾误触发真实导出，终止情况、未核实副作用与修复边界已记入 [第三轮本地验证](../operations/services.md#2026-10-08-sync-process-isolation-local-validation)。

第三轮已获明确许可并将 `e155aec` 推送、部署到 MacStudio。新 generation 已发布并由浏览器读到；真实详情前 3 次为 0.612s/0.131s/0.070s，新 generation 后为 8.132s/0.044s。等待已改善但尚不稳定，30d 列表仍为 37.773s；独立诊断确认四个全量 HTTP 入口仍在父进程解码并投影全量历史，现有 Gateway 缓存/索引不能直接用于这条多来源快照路径。下一阶段可隔离这四个入口及其 JSON 编码，须明确新增进程的内存/并发取舍；本 session 已完成定位，待用户裁决该取舍后实施，不把热详情结果充当冷读结案。

MacMini 旧快照原因已进一步定位：数据卷仅剩 261MiB，12 个遗留 export 临时目录共 6.17GiB；现有 `find /tmp` 在 macOS 不遍历入口符号链接。修复为只跟随入口链接的 `find -H /tmp`，原有 owner/name/age/depth 限制不变；合成目录红绿对照及 14 项同步测试完成。修复尚未发布，MacMini 未手动清理，归用户的新 push / 部署与清理许可；原始快照和锁不在清理范围。MacBook 本轮已恢复成功刷新。现场身份、读数与验证事故边界见 [第三轮部署记录](../operations/services.md#2026-10-08-sync-process-isolation-release-and-remaining-bottlenecks)。

2026-10-08 随后获批推送并部署 `a18f5ea`，现有 reaper 清除过期导出后，MacMini 可用空间恢复到 6.1GiB，成功发布 `04:29:47Z` 的新 generation，Hub 于 `04:30:54Z` 报成功、`stale=false`。快照恢复已由实际同步 API 核实；统计元数据仍显示 1 个 blocked source，不外推为全部来源完整。用户另外批准了四个全量 HTTP 入口进程隔离与最多 2 个并发任务，归本 session 实现和本地验证；具体提交的 push / 部署仍须单独授权。

第四轮本地实现已完成：四个全量 HTTP 入口及 JSON 编码在独立 worker 执行，上限 2，详情绕过其排队。相同真实副本、2 条交替详情的 HTTP 对照：列表期间最大等待 1.393s→0.00806s，完整导出期间 4.223s→0.01479s；列表与 618,351,695-byte 导出均逐字节一致。35 个受影响测试方法通过，输入维度和采样内存见 [本地验证](../operations/services.md#2026-10-08-full-history-http-isolation-local-validation)。这些读数不证明线上冷详情或列表算法已解决；下一项为具体提交的 push / MacStudio 部署许可，执行与真实浏览器复验仍由本 session 承担。

第四轮已获明确许可将 `04d90cb` 推送并部署 MacStudio。真实浏览器在 4 个请求上打开详情 7 次，分别为 83.9/27.9/46.4/80.0/27.9/527.8/39.5ms，覆盖全量查询并发、重复/切换、1/2 attempts 及 generation 换代；新快照由 `04d90cb` 导出并已显示推进的观测时间。四台来源最后一次同步均成功，MacMini 已恢复更新但元数据仍含 1 个 blocked source。本轮详情等待与快照恢复交付完成；本 issue 因全量列表仍慢而保留 open：首次列表 38.195s，并发显式页面查询 75.504s（含可能的排队）。列表算法、持续资源成本及更广冷读刻画归 agent-monitor 后端维护者后续处理，本轮不扩展到 Gateway 重写。现场身份、全部读数与边界见 [第四轮部署记录](../operations/services.md#2026-10-08-full-history-http-isolation-release)。

第五轮按用户选择“请求优先，附加功能按需加载”完成本地实现：初始请求页不再解码完整历史以计算总数、成本、独立 attempts 或完整筛选选项。可选时间索引在导出时构建；选中条件、完整请求链、精确排序与多机 cursor 语义保留。单来源隔离副本的真实浏览器列表热读为 8.8/11.2ms，翻页 10.9ms；手动全量统计仍为 16.386s、选项 13.971s，不能宣称这些算法也已提速。106 个受影响测试方法中 105 通过，1 个旧 schema 断言在未改 main 同样失败。审查发现的时间游标与初始点击丢筛选两项已修并回归。具体范围、来源与读数见 [本地验证](../operations/services.md#2026-10-08-request-first-list-local-validation)。当前仍未部署，issue 保持 open；新提交的 push / MacStudio 更新重启须用户许可，获批后的部署及真实入口复验由本性能 session 执行。大时间桶、稀疏 attempt 筛选仍可能扫描大量行，当前不宣称所有筛选都达到毫秒级。

## [open] ISSUE-CALLS-20261008-9a72：定向详情读取仅支持 HTTP query 的列表值形态

- **Discovered**: 2026-10-08，本次性能改动的独立审查。
- **Priority**: medium；改动依附，非阻断。
- **Owner**: agent-monitor 后端维护者；本轮记录，采纳扩展由用户裁决。
- **Evidence**: Gateway reader 接受 query 值为字符串、tuple 或 list；新增定向读取按 HTTP `parse_qs` 的 list 形态取值，内部直接调用传字符串或 tuple 时返回 404，旧完整读取可返回同一请求的两个 attempts。现有 HTTP 入口始终传 list，不受该差异影响。
- **Disposition**: 本次按网页 HTTP 运行包络交付，未扩展内部调用形态；以后支持此类直接调用前，应复用一致的单值归一化并验证三种形态。
