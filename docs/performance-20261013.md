# 监控响应、后台更新与运行观测优化

状态：进行中。用户授权 performance-workflows optimize：自行从真实入口、架构和已有证据识别主要成本，实施有依据的优化，补齐慢操作、持续阻塞和数据更新停滞的自动发现、量化及诊断接续；保留数据正确性、快照和更新语义。

## 范围、隔离与验收

当前 session 在自有 `codex/performance-20261013` / `/Users/lindong/research/.worktrees/agent-monitor-performance-20261013` 开发，基线 `c6734f52ec23c99a4c1bbb60b16b6bf618747f4e`。主树已有未跟踪 state 与另一 worktree 均保留。测试使用隔离 HOME/state；真实取证只读 Hub HTTP，离线诊断仅消费独立快照副本。生产发布沿项目部署授权与 deploy.py，不修改供应商账户、不触发发送消息。

顺序：真实入口基线与增长风险 → 最小有依据优化 → 复用既有后台线程及状态设施补观测/诊断 → 对应回归与同入口复验 → review、commit、整合和已授权部署。用户可见的接受条件是页面/API 输出语义保持，前后台竞争下读者能推进，慢与在途/停止更新有可消费证据。性能数字不构造新 SLA。

当前线程执行诊断与实现（task-routing R4，继承模型/effort）；需要独立判断的决策和变更按各 gate 单 reviewer 一轮。固定行为下的局部优化按 L0，新的诊断接口/后台观测契约按 L1 先审。保持会计与历史完整性边界，新增验证仅围绕本次具体变化。

## 已取证与待解问题

2026-10-13，真实 MacStudio Hub 与本地主树均为 c6734f5。HTTP 7d overview 200/0.181451s；sessions?range=7d&with_filters=1 200/2.543443s，单次初始观测，不代表分布。现有 Hub 有四来源，current snapshot 大小约 MacMini 280MB、MacBook 158MB、MacStudio 7.8MB、Tencent 45KB。

架构已有独立 sync 子进程、prepared QueryView、短 publication 锁。Sessions 仍单独 admission，SQLite 对 JSON timestamp/session 字段扫描，逐事件解码/校验并 Python 聚合。隔离 profile 已确认 SQLite 全扫描占主要成本之一。当前 Server-Timing 截止响应头；没有在途请求窗口/阶段、自动慢路径检测与有界持久诊断记录。现有 staleness 字段为查询时结果，需要核实后台消费者。

证据目录：`~/.local/share/agent-monitor-diagnostics/performance-20261013/`。真实浏览器使用独立 headless session `monitor-perf-20261013`，不接管用户浏览器。生产收益与长期分布待发布复验；通知未接入，不称已投递。

## 待审实施决定（2026-10-13）

选择在导出未发布副本上增加日期表达式与 session-id 表达式索引，读取继续使用既有 SQL/解码/分组，不改 schema 版本、时间边界、排序、未知成本或数据行。旧快照无索引仍可读。备选每请求缓存会引入换代/内存与首读问题；新建 sessions 聚合存储会扩大计算及排序语义面；仅并发不减少全历史扫描。索引为固定行为下局部实现 L0，验证返回对象相等、扫描步数和导出索引开销。sessions 保持现有 admission 更新语义。

观测 L1：Hub 进程内记录 HTTP 从 parse 后到 body 写完的耗时/结果，固定路由标签（不记录 query、正文、session/account 标识），追踪 active 操作和当前阶段；后台 sync、query-view、quota 包装独立操作。独立 daemon 观察线程在现有 server main 生命周期启动/退出，每5秒读取内存统计与已准备的来源 freshness 摘要，写原子 current JSON 及有界轮转 JSONL（1MiB x3）。不等待业务锁取得在途记录；观测失败显式 unknown/持久化错误。只读 /api/diagnostics 不依赖业务 view。active详情、完成样本和待写事件分别最多256条，超限报告当前/累计丢弃和coverage partial；固定路由/阶段标签，事件只含有界标量不含sources列表；current文件仅含这两组明细和配置机器集合。报告进程身份、窗口和累计计数；1s慢阈值只做诊断事件，不定义 SLA/失败/取消；30s在途标长操作，不声称纯锁等待。来源 stale 沿用6小时既有阈值，同时报告 observed/generated 年龄、实际hub.interval()配置周期和按该周期折算的age_intervals（非失败/遗漏轮次）。后续维护者根据操作阶段、来源名、耗时、最近结果接续定向 audit/optimize。

消费：HTTP diagnostics 和 state/diagnostics/current.json/events.jsonl。进程完全冻结/死亡时内部线程不能独立诊断；system-config watchdog 精确纳管五个label不含 Monitor。该外部探针缺口保留具名部署owner待办。本次不新增外部消息发送/新服务。替代仅 Server-Timing 或只完成日志检测不到进行中的挂起；新增整套监控服务增加部署依赖，不是本次最低充分方案。通知接收责任为 Monitor 部署维护者，外部投递未接入不称已通知。

基线：真实Hub单次0.18s overview/2.54s sessions；离线三个旧快照（09-09至10-08）共782059 events，custom 10-01至10-08 profile 0.782s中 SQLite execute 0.425s、read/decode 0.639s，251 sessions。SCP探索预算90s未完成，不完整副本不作证据；固定本机副本用于实现对比，生产收益另测。

决策语料：本仓 docs/adr、docs/issues 已检索 session/statistics/observ/monitor/diagnos/slow；相关20261011-a8d3 prepared-view（冷准入1.07s）、20261008-c4e2 sync-process（历史前台竞争）、20261012-b97e product-boundary（移除Gateway）、20261007-e781 session-detail（全详情）、20261013-42b6 accounting（本轮不动计算）。无已有 diagnostics contract 命中。现有 Server-Timing 截止响应头，QueryView日志只有完成后；新观测为本轮用户明确要求（rigor来源3）。验证对照包括慢/在途/过期/观测失败及正常恢复；生产开销、通知投递、长期分布尚未核实。错误决定可回退代码，不改原始数据/周期/保留。发布须沿 deploy.py 已审入口，固定 origin/main SHA。

## 固定数据优化与成本读数

最终索引实现复测：独立副本三个来源、782059 events、10-01至10-08窗口、251 sessions，每种状态三次同一 sessions_endpoint 调用（准入被固定副本替身隔离，不代表生产准入/网络）。未索引秒数0.66842/0.59372/0.59216；索引后0.19788/0.19336/0.19121，中位数下降67.4%。两侧完整排序JSON SHA256同为 `0e27c292a956267e0875ed4a7b07d34fb8a6d21674831553281fa86cb595e054`。证据 `endpoint-fixed-before-after.json`。

SQL层三个体量161/244834/537064行，各三次、命中0/21930/11行。未索引plan为SCAN；索引后SEARCH加position排序，输出hash相同。244834行SQL中位数138.1ms→20.4ms，537064行300.0ms→0.009ms；这两个缩小后的命中集不代表30天全窗口。索引构建分别0.0008/0.337/0.754秒；副本文件增长16KiB/15.0MiB/33.0MiB（已有602MB大副本增长约5.75%）。最初SQL实验使用临时旧索引名称，最终helper已用兼容快照校验器的idx_usage_*名称复测端点。索引仍增加每次导出的O(N log N)构建与传输字节，未减少完整校验/保留检查成本。

观测开销：10000次内存operation记录约10.8ms；4来源、recent=0/256两个状态各三次真实tick及文件写入0.15–0.49ms，current文件1.9KB/41.5KB。只代表本机样本，不是慢磁盘或生产上界。容量、慢/快、成功/失败、在途/完成、来源过期/未知/恢复、写失败/恢复与轮转由隔离回归覆盖。

相关回归132项通过：diagnostics、statistics_snapshot、query_view、sync_process、independent_quota、ui_workflows、exporter、snapshot_contention、generation、async_refresh、progressive_freshness、freshness。首轮用模块方式加载async_refresh缺tests搜索路径，后按该既有导入布局设置PYTHONPATH=.:tests重跑通过；不是产品失败。后续审查修复的最终读数另记。

## 增长边界与再评估

| 成本 | 本轮效果 / 残余 | 归属及再评估条件 |
| --- | --- | --- |
| 历史总事件N | 日期/session索引缩小读取；全历史读取仍O(N)，冷digest、保留检查、导出/传输随字节增长 | 后端维护者；新generation准备/同步长操作反复出现时用已有benchmark/contention入口与真实source定位 |
| 窗口内事件K、会话S | 解码/规范化仍O(K)，分组/排序和整页payload随S增长，浏览器仍前端分页 | Sessions维护者；慢事件集中在30d/全历史时采集同一窗口profile，下一步评估共享聚合或按代际准备，不自行减少历史或推迟刷新 |
| 来源M | admission签名、来源遍历与metadata读数增长；同步复用既有进程和有界并发 | 同步维护者；新接来源或队列持续超过实际配置周期时结合active与sync-status实测容量，不从age_intervals推算遗漏轮次 |
| 观测自身 | 固定256详情/事件容量，轮转3MiB；超限明确coverage损失 | 部署维护者；events_dropped/persistence_error或文件停止更新时按operations接续；整进程外探针见ISSUE-PERF-20261013-observer |

真实HTTP部署前：同一Hub、7d和30d各3次、overview7d三次，另两并发请求。7d为3.02/3.99/6.04秒，30d17.77/15.71/16.78秒；overview0.307/0.736/0.209秒。并发7d sessions2.452秒、overview0.155秒。页面实际自动更新取最近8次资源时长，见browser-before.json。线上数据继续变化，后测不能冒充同一固定数据A/B。

## 审查与发布前验收

独立决策审查按R14执行，首轮要求约束active/事件容量并使用真实配置周期，两项已修，七项复核成立。独立变更审查首轮发现配额来源失败误记成功、拒绝reexec后观察器被关闭，两项HIGH已按失败读数建立RED测试并修复；同一reviewer定向复核通过，无遗留/独立findings、无承重未核实项。来源失败保留原callback，仅修正诊断outcome；成功exec替换线程，失败时不提前停观察器。

最终134项相关回归通过（9.670秒）；两新增测试覆盖quota成功/机器错误/provider错误三种、reexec正常/cleanup拒绝/exec失败三种。仪器涵盖慢/快、失败/成功、在途/完成、过期/未知/恢复、持久化失败/恢复；不据此宣称所有生产状态均已覆盖。源码和测试完整纳入同一提交，token_cost.py未改。生产发布及同入口复验仍待下节实际记录。
