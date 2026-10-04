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
