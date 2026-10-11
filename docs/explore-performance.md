# Explore 性能缺口与实施记录

状态：本轮改进已实施并部署 MacStudio，2026-10-11 完成现场验证。用户要求先记录缺口和建议，再据此改进；目标入口为 `http://macstudio:39001/explore?range=30d`。

## 已取得的证据

2026-10-11 从 MacBook 访问 MacStudio，并核对两端源码。MacStudio 基线为 `2018322`，本地开发基线为 `ddd27a6`；generation.py、rollup.py 和 pivot.js 内容一致，server.py 存在既有部署差异。以下为诊断样本，不代表延迟分位数。

| 观察 | 读数 | 含义 |
| --- | --- | --- |
| 四台机器当前快照 | 约 3.05 GB，2,127,322 条明细；daily_rollup 1,504 行，daily_usage_rollup 422 行 | Explore 所需摘要远小于完整快照 |
| 独立 Python 进程首次 generation admission | 1.0706 秒，其中 MacStudio 文件摘要 0.7756 秒 | 读取入口承担与完整明细体积相关的校验 |
| 同进程第二次 admission | 0.0015 秒 | 热缓存掩盖冷路径成本 |
| 固定代际后的 filters / grouped pivot | 约 2–3 ms / 1–2 ms | 已有 SQLite 日聚合不是此次读数中的主导项 |
| 浏览器页面观察 | 一次约 0.47 秒；另一次首个 API 到 1.844 秒才启动 | 首屏存在连接、脚本与串行请求等待；未稳定复现持续多秒的后端慢查询 |
| 独立浏览器另一次连接阶段 | 约 441 ms | 网络连接不能全部归因为 SQL |
| HTTP 响应与静态资源 | HTTP/1.0、静态资源 no-store；主要资源约 479 KB | 重复连接和重复传输可减少 |

计时中的 OS 页缓存未清空。现场 MacBook 存在最近同步失败记录，当前失败原因未核实，本轮不能将其归因为 Explore。

## 缺口与改进方向

1. 请求入口混合了数据准入、完整性校验与查询。把经过校验且持有代际租约的读取视图提前准备，查询只读取已准备的数据；保留配置变更、校验失败、来源冲突的排除规则。
2. 快照发布在机器级互斥区执行复制和校验。将耗时准备放在互斥区之外，原子切换保持短临界区；保持保留历史、崩溃恢复和 GC 租约语义。
3. Explore 把状态、筛选项与 pivot 请求串行化。并行独立请求，保留 URL 筛选、过期响应抑制与逐台统计刷新；quota 单独变化不再触发 Explore 统计重算。
4. HTTP 连接与静态资源每次重复付费。启用正确分帧的持久连接和静态资源条件缓存，保持页面更新可见。
5. 缺少请求处理时间与数据准备时间的区分。提供低基数的请求服务端计时，记录数据准备日志；不引入新的监控服务器。
6. 完整快照复制仍随历史增长。先消除它对读取路径的影响；若上线读数仍显示传输或采集主导，再设计增量传输。本轮保留完整快照、历史和费用口径，不在无收益读数下迁移数据协议。

## 架构原则与范围

借鉴 [Prometheus 架构](https://prometheus.io/docs/introduction/overview/) 的采集、存储和查询分离，以及 [recording rules](https://prometheus.io/docs/prometheus/latest/configuration/recording_rules/) 的预计算思想。现有日聚合已经发挥预计算作用；不把高基数请求明细或费用账本迁入 Prometheus。保留 sync-process 与 query-process 的隔离和并发界限。

实施前核对 `20261008-c4e2-sync-process`、`20261008-d102-query-process`、`20261008-f43a-request-first-loading` 和独立 quota 刷新 ADR。读取视图必须有明确的生命周期、来源身份和失败行为，不以无限期 stale 数据掩盖准入失败。后台准备保持有界，不增加产品 LLM 调用或第三方服务。

## 实施决策（独立审查已放行）

选择复用 SQLite 日聚合，加一层进程内已准入代际视图；不增加 TSDB、持久聚合副本或全历史请求 worker 池。替代项是仅预热原验证缓存（仍有首次请求抢到新代际的竞态）和复制紧凑摘要到第二个数据库（本次 2–3 ms 查询读数不支持新增一套持久一致性机制）。

视图在服务开始监听前初始化；一个后台线程按已有调度检查间隔刷新，统计完成回调在公布完成前刷新。每次构建沿用完整 admission 校验、来源碰撞及配置指纹规则，在短锁下换入新视图。请求只取得已构建视图引用并复用现有日聚合 SQL；视图持有真实 generation lease，旧视图在最后一个读者退出后释放。请求核对当前机器配置和已准入文件的 stat 签名；配置/退休改变或文件被修改时返回明确的暂不可用并触发后台重建，不能回退到请求内全量校验。新指针准备期间可以读上一份仍有效且带真实代际时间的视图；构建失败不能无限静默沿用旧数据。关服释放视图与后台线程。单个来源无效仍沿用既有 admission 排除与 coverage。

发布使用带独占 lease 的 `.preparing-*` 目录，复制和全量校验在机器 GC 锁之外；清理器只有取得准备 lease 才可移除该目录，崩溃遗留可回收。对准备好的不可变副本执行历史保留检查，进入机器锁后重新核对前代与退休状态，再复用现有 journal/current/previous 切换。避免刚验证的副本改名后又在锁内全量验证。保留故障注入、回滚、GC 和租约测试。

前端先读 sync-status 建立代际屏障，再并行 filters 与 pivot，首次 pivot 直接携带 URL 筛选值；统计视图更新以机器代际变化为准，quota-only 更新不引起 Explore 数据重算。HTTP/1.1 保证响应分帧，写请求关闭连接避免未消费 body 污染后续请求；静态资产 ETag 条件缓存，HTML 保留 no-store。服务端计时使用标准 Server-Timing header，不新增业务响应字段。

相关决定及读数：2026-10-08 sync-process ADR 记录保留检查 27 秒、主进程干扰与独立进程收益；同日 query-process ADR 记录全历史查询 37 秒与高内存，保留两槽 fresh worker。request-first ADR 保留请求列表优先和按需重查询；2026-10-11 quota ADR 保留独立刷新。原始具体证据见对应 ADR，部署后数值未在本轮重新测量。此方案不覆写这些决定。

审查档位：L1（架构与发布数据完整性）；一轮独立设计审查和一轮变更审查，发布竞争/故障回归来自现有数据完整性约束，浏览器验证来自用户明确的性能入口。收益未验证项：新代际后台准备期间的真实页面延迟、网络连接成本可减少多少、长期同步传输是否仍主导。部署/restart 由项目 AGENTS 常设授权覆盖；不 push。

服务失败面自检：新增视图准备失败必须在 API 返回暂不可用并记录日志，用户打开页面即能看到失败，不另加消息投递服务；同步/查询进程与部署身份保持不变。后台线程通过停止事件退出，磁盘准备仍由现有本地文件操作完成，无外部无限等待。

## 执行与验收

- [x] 对读取视图和发布临界区做一轮独立设计审查，七项均成立，无未处置 finding。
- [x] 实现读取路径隔离及发布准备，覆盖配置切换、失败、代际切换和租约竞争。
- [x] 实现 Explore 并发加载、统计变化检测及 HTTP 条件缓存。
- [x] 执行相关 Python/Node 回归和一轮变更审查；一次 HIGH 修复后定向复核通过，无遗留 finding。
- [x] 本地提交并整合 main，部署 MacStudio，以同一入口核对源码身份与浏览器行为。

隔离开发树：`/Users/lindong/research/agent-monitor-explore-read-model`；分支：`perf/explore-read-model-20261011`。测试使用隔离 HOME/state；不修改他人工作树、不 push。

## 实施结果

本轮五项实施改进均已完成。增量传输仍为有条件的后续方向，没有新增同步协议或监控服务。

实施前新增复制竞争回归：冻结 snapshot copy 后读取 current，旧实现稳定在 1 秒读取期限超时；现有 46 项 generation/GC/聚合测试通过。

第一批改动后 145 项相关 Python/Node 回归通过（7.325 秒）；复制暂停期间读取和 GC 均可继续。全量套件与新增并行加载检查仍在进行。文档同步限于实现记录、架构、ADR/索引、变更记录和服务运维说明，不另开文档审查循环。

实现审查发现并修复一项 HIGH：首个状态与 pivot 完全并行会把旧统计配上新代际状态，因此恢复首次状态屏障，只并行 filters/pivot。另同步架构文档中过时的锁内复制描述。修复前的全量 921 项回归仅剩基线相同的 9 个失败和 1 个错误；已在 ddd27a6 隔离归档逐一复现，证据位于 `~/.local/share/agent-monitor-diagnostics/20261011-explore/baseline-existing-failures.txt`。它们涉及 Gateway schema/projection 预期和 account-memory 测试 mock，未在本性能任务中修改，归 agent-monitor 既有测试维护。

变更审查复核通过：状态屏障回归在当前代码退出 0，删除 await 的内存变异退出 1；19 项相关前端测试通过。全量最终复跑 921 项（87.995 秒），仅有已在基线复现的 9 失败/1 错误，没有本改动新增失败。真实部署前的新基线为 MacStudio `e459642`、本地 main `2008c5a`，均包含另一会话刚完成的账号恢复改动；部署须在这些版本上保留并整合本改动。


### 最终验证与部署

本地实现提交 `30f8af8c7d5188dc29eb183f54e34553303ae0f6` 快进整合到 main，保留 `2008c5a` 的账号恢复改动。MacStudio 有独立历史，因此在其 `e459642` 上生成部署提交 `02d1f040db0cafc3903dc538710ab4b8ad25f048`，经 Git bundle 快进后只重启 `com.agent-monitor.hub`；未执行 git push。六个改动运行文件的 SHA256 在本地实现与远端部署一致。state symlink 保留 inode 5804813 和目标 `/Users/lindong/.local/share/agent-monitor/state`。

部署 health 为 `ok=true`、`stale=false`，instance `c5c77737849a4af7893dd9e7607e1d9e`，signature `6fa3a14286cef7cc`、web_signature `2268f805c579a350`。本地整合版本 199 项相关测试全部通过（16.239 秒）。远端历史上的部署候选同组 199 项为 197 通过、2 个旧 UI 断言失败；两项均在部署前 `e459642` 独立归档复现，分别为 quota 表列顺序和历史行 selector 预期。测试维护归 agent-monitor 既有测试维护，本次不扩入性能修复。

同一客户端、部署前后分别新建隔离 headless 浏览器，每个浏览器连续访问三次目标 `range=30d`。下表的就绪时间是探针读到页面“就绪”时的 navigation-relative 时间，包含观测开销，不是精确首绘时刻或 P95；OS 缓存未清空，三次样本不能作长期延迟保证。

| 观测 | 部署前 | 部署后 |
| --- | --- | --- |
| 三次就绪观测 | 531.3 / 437.1 / 396.7 ms | 431.4 / 351.2 / 298.7 ms |
| 五项主要静态资源首次传输 | 479,169 B | 479,688 B |
| 同一浏览器后两次主要资源传输 | 每次 479,169 B | 每次 1,500 B（条件缓存响应） |
| filters / pivot 请求起点 | 串行 | 首次分别 240.4 / 240.6 ms，保留前置状态屏障 |

实际交互覆盖两个机器值（macstudio / macbook）、两个时间范围（30d / 7d）：切换后页面就绪，URL 和请求筛选同步，排名为两行。另直接打开 `?range=7d&machine=macstudio`，控件和首次 pivot 请求均保留 URL 筛选。总览正常呈现 14 条 quota 表行，overview 请求总耗时 27.1 ms、服务端 14.94 ms。早期探针误用不存在的排名/配额 selector 所得零行不是产品失败；此处采用页面真实 DOM selector 重新观察。

部署后的直接 API 样本：sync-status / filters / pivot 总耗时 24.6 / 23.5 / 28.9 ms，Server-Timing 为 1.43 / 3.18 / 2.24 ms；四个配置来源均已准入，pivot 返回 54 行。Hub 日志出现两次已准备视图记录，耗时 0.209 / 0.749 秒，所读日志尾段无准备失败。它们证明准备机制运行和请求计时可观测；未精确对齐一次真实后台准备与页面请求，不能据此宣称已测得重叠窗口的线上延迟上界。复制/准备暂停时的读者与 GC 行为由隔离竞争回归覆盖。

原始证据位于本机 `~/.local/share/agent-monitor-diagnostics/20261011-explore/`：`browser-before.json`、`browser-after.json`、`browser-final.json`、`deployed-api.json`、`integrated-tests.txt`、`deployment-variant-tests.txt`、`deployment-baseline-failures.txt` 和全量/基线测试记录。服务部署身份另见 operations/services.md。

本轮实现与评审未处置 finding：无。范围外事项：既有全量及远端 UI 测试失败按上文保留证据；MacBook 同步失败的当前原因未核实，保留既有服务状态，不将其归因为本轮 Explore 改动。后续若采集/传输成为主导项，再由 agent-monitor 同步链路任务取得读数并设计增量传输。本轮原始请求（诊断、文件记录、据记录改进与真实入口验证）均已完成，没有在飞任务。
