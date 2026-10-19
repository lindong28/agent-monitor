# 会话视角的用量归因

状态：实现、145 项相关测试与独立变更审查完成；待发布及线上页面复验。用户于 2026-10-19 批准按讨论方案修复；本文件同时保存实施状态，不另建重复台账。

## 目标与验收

用户希望看到的数据尽可能对应直接使用体验：一条主会话可含多个轮次和原生子代理。列表默认按主会话展示，保留追溯明细和原有筛选、排序、分页、链接、刷新及复制能力。不从用量时间推断正在运行或已经结束。

| 用户目标 | 验收 |
| --- | --- |
| 原生子代理归到主会话 | 两层子代理按明确父 ID 递归归组；同一机器/agent 内解析。独立 wrapper、普通 fork、父缺失或冲突不猜测归并，不丢用量 |
| 认得出会话 | 已有可靠标题优先，目录/时间兜底；最近用量排序；创建时间只用源元数据，首条筛选记录不冒充创建时间 |
| 列表与详情一致 | 默认详情沿用日期和全部筛选；显式切换全部留存记录；返回保留列表状态；筛选命中的子代理数据显示在所属主会话下 |
| 减少技术细节 | 主视图保留会话、机器/Agent、最近用量、费用、token；模型、事件数、时间跨度、执行 ID、逐条记录进入展开层 |
| 金额可用且诚实 | 合计可计价记录；部分不可计价保留限定，全部未知显示未知，真实零显示零；诊断入口可查原因 |
| 数据不重不漏 | 归组前后 dedup_key 集合、token 和已知金额完全相同；沿用原有复制历史去重，不修改 token_cost.py 算术 |
| 发布可用 | 适用 Python/Node 测试、独立变更审查、发布与部署回执；真实浏览器验证列表→筛选→详情→全部记录→展开→返回，以及窄屏与其他页面回归 |

## 拟采用的数据流与边界（已通过独立决策审查）

保留 SQLite、现有归档/快照发布与锁。新增每个执行会话一份元数据目录，独立于 UsageEntry，不在每条用量上重复保存标题与父关系。源元数据是权威；本机归档保存已观测身份，快照是该次采集的投影。标题/关系不存在时旧记录仍独立可读，不要求重放历史金额。关系来自首条 owner session_meta 的 source.subagent.thread_spawn.parent_thread_id；forked_from_id 与 history_base 只继续承担既有复制历史排除，不参与 UI 归组。Codex thread DB 只取明确的 title/name、created_at 等白名单元数据，不采集对话正文、不调用模型生成摘要。

元数据在正常采集时更新并留存，保留周期沿用对应使用归档；规模按会话数增长，不按 token 事件数增长。通过 LoadedEntries 传给 snapshot，统计扩展升至 v4，读兼容 v1–v3。Hub 首先支持新旧快照，再更新采集端。新表不改旧用量主键、金额或总量。缺失父节点、冲突和循环都保留独立执行记录及可诊断状态；完整关联组件才按根归组。标题可后续更新，关系冲突不能被最后一条静默覆盖。

后端在日期/模型等过滤前建立整份元数据的关联索引；过滤只作用于用量事实。详情按明确成员 ID 使用现有 session 索引批量查询，不扫描所有历史用量。根元数据用于标题/身份，当前匹配记录用于金额/token/最近用量。多模型放详情，不把第一条记录的模型当作整个会话模型。旧子会话 URL 可解析到所属主会话并保持来源可追溯。

备选：在 UsageEntry 增加可选字段更少 plumbing，但会在每个事件重复元数据、不能表达无用量父节点或独立标题更新；仅浏览器合并缺少完整父链且各消费者口径会分叉；请求时读取远端原始日志会增加页面 I/O 和运行依赖。现有 SQLite 单采集写入/快照读取仍适用，不引入数据库服务。

## 基线、已知边界与决策证据

基线提交 `9e9d8997ba225e02f00d1b1509ca74a6c0e5cbbe`。已逐层检查 UsageEntry、usage_archive、statistics_snapshot、server._group_sessions/_session_stats、web/app.js：当前链路按 session_id 分组，详情全留存，部分未知成本使整个金额为空。153 份本机近期原生子代理日志头均含 parent_thread_id，其中存在嵌套；这是文件样本，不是所有版本或所有机器的覆盖证明。已读取本机 threads schema：含 title/name/created_at/created_at_ms/source/thread_source；尚未验证更新后跨机快照及 UI 的实际效果。

决策语料为本仓 docs/adr、docs/references/metrics.md、docs/contracts/ux-contract.md、相关性能文档；按 session/statistics/会话/成本/snapshot 检索。相关既有决定：20261007-e781-session-detail（当时独立详情、全部留存与 strict cost，实测 fixture）；20261013-b4d8-runtime-performance（索引、准入与诊断，性能记录）；20261014-97cd-storage-publication（持锁/发布/保留）；20261018-8c42-cost-diagnostics 与 20261019-a42e-task-focused-diagnostics（已知金额与诊断分层）。本次用户批准改变列表归组、默认详情范围和会话金额表现；保留来源观测、快照准入、归档和原始量的约束。旧 ADR 作为历史记录不改写，新决定说明被替代部分。

需要验证：旧快照兼容、元数据冲突/缺失/循环、只命中子节点或跨日父节点、标题安全转义、未知/零/混合成本、旧链接、模型过滤、嵌套复制历史金额不重算。未核实来源不填推断值。

## 执行状态

- [done] 决策审查与元数据/查询设计；L1（共享数据契约及用户可见归因）。一个独立 reviewer，一轮，按 finding 定向复核。
- [done] 采集→归档→v4 快照→后端会话归组和筛选详情。
- [done] 会话 UI、权威产品/指标/UX 文档同步及适用测试。
- [pending] 独立变更审查、整合与普通 push、部署、真实页面复验。
- [pending] 核销自有 worktree 和证据交付。

执行方式：跨模块实现由主线程负责（task-routing R4）；独立审查用 Codex 原生 collaboration，独立上下文，R14 gpt-6-astra/high。候选仅原生 collaboration，符合 Codex 路由；不启动 wrapper。审查以终态回传收回，不在审查期间改输入。测试使用隔离 HOME/state，浏览器使用自有 headless surface。开发树 `/Users/lindong/research/.worktrees/monitor-conversations-20261019`，分支 `feat/conversation-sessions-20261019`，回收责任归本 session。

## 验证与审查记录

隔离 HOME 的 145 项相关测试通过（tests-final.log）。完整测试 1017 项首次出现 13 failures / 1 error，其中本次 3 failures / 1 error 为旧 v3/v2 fixture 和 CLI 复制清单遗漏，已修正并包含在 145 项复验中。其余 7 个测试的 10 个失败子项在基线 `9e9d899` 的独立 archive 副本中逐项复现，涉及旧 coverage/异常机名/定价说明文案及 unknown_cost 指标键。本次不扩大修改这些无关断言，保留基线测试债说明；原始日志为 tests-full.log / tests-baseline.log。测试中出现 SQLite ResourceWarning，未据此声称新的运行故障或已修复。

独立变更审查（R14，gpt-6-astra/high）一轮发现三个依附项：执行标识内层展开态被重读清除、v4/新模块测试 fixture 未同步、README 一句默认详情范围过期。全部修复；无 CRITICAL/HIGH、无基线独立 finding、无承重未核实项。审查前后 Git 状态一致；reviewer 实跑 101 项通过并复现旧 fixture 失败，另验证 2 台机器同 ID 分组隔离与真实 v2 表集读取。小范围修正按直接复验闭环，不另开全审。

真实 headless 浏览器使用隔离合成数据：1440×900 列表从主会话 $3/36 tokens（含两个子代理）切 model B 为 $2/12，进入详情仍 $2/12，切全部回 $3/36 并提示部分未计价；展开三个执行贡献 $1/$2/未知及各 12 tokens，返回保留 B；model C 显示未知而非零，独立行真实零为 $0。390×844 详情宽度 390、截图已查看。该读数不代替生产数据；preview-evidence.md 及两张截图位于 `/Users/lindong/task-artifacts/conversation-sessions-20261019/`。

修复后浏览器展开主执行 ID，手动刷新及随后 30 秒重读均保持该内层展开；其余两个执行仍关闭。费用、token、筛选与范围切换保持原结果。
