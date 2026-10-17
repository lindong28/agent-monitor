# Harness issues

已关闭记录见 [archive/closed.md](archive/closed.md)。

## [open] HARNESS-20261014-perf-retro：性能优化的首轮归因与目标收尾证据不够完整

- Type: execution / workflow clarity
- Priority: medium
- Owner: agent-monitor 性能任务执行者；通用措辞候选归 ai-agent-config performance-workflows / performance-optimization owner。
- Scope: 2026-10-13 performance-workflows optimize 的项目具体执行复盘；本轮只分析并记账，不改 skill、不重启服务、不新增实验。
- Evidence: [原交付](../performance-20261013.md)；持久原始读数位于 `~/.local/share/agent-monitor-diagnostics/performance-20261013/`；Codex session `01a135c9-2f9f-7150-908b-09b68eb800d8`，rollout `2026/10/13/rollout-2026-10-13T23-02-19-01a135c9-2f9f-7150-908b-09b68eb800d8.jsonl`。

### 观察与归因

1. 首轮用三个旧快照的窄窗口 profile 决定索引，固定数据端点中位数0.594→0.193秒，结果相同，结构收益成立。但该数据不是线上30天窗口；第一批发布后的30天请求仍17.306秒，随后新仪器识别 response_body_write 主导等待，才复用既有gzip降至4.636秒。执行初期已知道HTTP计时止于响应头（原记录82行），却未在首次优化前把完整等待拆清。不能证明提前分解必然少一次发布，但它能提前区分查询和传输候选。既有正文已要求真实入口与主导等待，主因是执行顺序；正文的链路示意只显式列出消费者、准入、查询、存储，可考虑将结果交付到消费者可用的边界写清，避免只测到结果算完。
2. 前后台重叠并非没有证据。diagnostics-during-sync.json 中 sync_round 自1791906107.123开始，持续53.931秒；两次 GET /api/sessions 自1791906112.826和1791906132.622开始，分别1.623/1.587秒完成。它证明这个同步窗口内读者推进，不能证明任意并发或来源增长下无干扰。原交付未将该重叠读数与普通双HTTP并发分开呈现，也未建立后台空闲/在飞的匹配成本对比。增长分析有N/K/S/M机制推导，但来源M只有生产4这一档，不能外推容量上界。
3. 观测实际检测慢请求与长同步，且直接支持了第二次优化；整个进程冻结/死亡时检测器本身不能继续运行，外部探针与投递未接通，已记录ISSUE-PERF-20261013-observer。原请求明确要自动发现持续阻塞和停更，收尾应分别标出覆盖与未覆盖；写owner不等于能力已交付。原请求没有明确要求自动调用agent诊断，不能据此新增常驻自动诊断服务或把外部消息投递当作必需授权。
4. 30天最终仍约4.6秒、源端profile约3.16秒。原交付给出解码/聚合与全量快照等残余及owner，但未直接给出停止条件要求的进一步可节省上界、下一候选新增范围与失败面比较。它支持“本次优化有效”，不足以单独证明“继续优化不值得”。下一次收尾应写清停止新增优化与用户原目标已完成分别凭什么。

### 规则核对与版本边界

原rollout14行调用并在19行取得 optimize / performance-optimization 正文，包含“拆出关键路径与主导等待”“只加埋点或只写提醒不算接通消费”及停止条件；不是入口未加载。当前2026-10-14提交2b6a047b又增加共享资源反查使用者和“失败→可查询信号→detector→实际消费动作”，这些新条款不能倒推为上轮未遵守的旧要求，也不应重复新增。

检索命令：`rg -n 'performance-workflows|performance-optimization|主导等待|观测.*闭环|消费接续|进程外|停止条件' /Users/lindong/research/ai-agent-config/docs/issues/harness-issues.md /Users/lindong/research/ai-agent-config/docs/issues/archive/closed.md /Users/lindong/research/agent-monitor/docs/issues/harness-issues.md /Users/lindong/research/agent-monitor/docs/issues/archive/closed.md`。中央账本命中设计依据与停止投入边界候选；项目历史HARNESS-20261012-36ec已补主动入口。本条不重开其已完成的入口修复，记录新的执行样本。正文核对范围为实际加载的性能owner、optimize/audit、delivery-accountability以及上述当前commit差异，未做全harness语义普查。

### 建议、验收与本轮处置

推荐最低充分改进：在现有性能正文明确端到端测量的完成边界，先解释局部profile与用户等待间未解释部分；在optimize收尾指回既有逐目标交付对账，区分已完成优化、观测未覆盖和后续收益不足。最新共享资源/消费接续条款直接复用，不新增全局hook、固定压测矩阵或强制审查轮。索引构建和额外传输的代价继续放进项目同入口复验，不把成本移到后台当作消失。

验证建议由后续获授权的skill维护者执行：给相同API总耗时但不同计算/传输占比的案例，检查是否在选修法前区分主导阶段；给诊断文件可读但进程外detector缺失的案例，检查能否准确报告覆盖和缺口；已有数据足够的窄任务应直接完成，不被扩大为全站压测。尚未运行这些行为验证，不能宣称修改会减少耗时或已修复执行偏差。

本轮未实施上述skill建议，因为用户请求是复盘与是否改进的分析。未处置发现均已列于本条；项目外部探针与重启竞态继续沿general.md原owner，不复制业务待办。归因不支持给性能skill增加SSH、launchd或浏览器操作教程，也不支持以本次134/59项短回归和有效独立审查为由削减正确性保障。


## [open] HARNESS-20261016-browser-continuity：Monitor 导航后部分调用回到空白页

- Type: browser validation / transport
- Priority: medium
- Owner: agent-monitor 页面验收执行者；确认通用原因后转 ai-agent-config 的 agent-browser 维护者。
- Scope: 复现只覆盖 Monitor 的 MacStudio 入口及本机预览，未用其他站点隔离通用性，暂按归属不明保留项目账本。
- Evidence: 独立 headless session 的 open 返回目标标题与 URL，后续 get url 为 about:blank、snapshot 无元素；直连 HTTP 返回 200。独立诊断在 CLI 0.27.0 与现有 0.39.1 均观察到该形态。共享配置含 idleTimeout 字符串 30m，但对照同时改变启动参数，不能认定超时解析为根因。
- 可用路径：每条调用保持同一 session 和 `--args --no-proxy-server,--mute-audio`，以 subprocess 环境传整数毫秒 AGENT_BROWSER_IDLE_TIMEOUT_MS，已连续完成 open/get url/snapshot/click。脚本与证据在 `/Users/lindong/task-artifacts/agent-monitor-writing-20261016/`。这只证明本次组合可用，不证明单一因素因果或未来稳定性。
- Next: 后续浏览器维护任务分别控制启动参数和超时，核对 daemon/session 身份与页面存活；本轮不改共享配置或升级工具，也不据此诊断 Monitor 线上故障。


## [open] HARNESS-20261017-entry-necessity：账号快捷入口审查把用途误当成保留依据

- Type: execution / workflow clarity
- Priority: medium
- Discovered: 2026-10-17，用户追问总览“账号操作”“查询配额”是否必要。
- Owner: agent-monitor UX 任务执行者；通用判据候选归 ai-agent-config 的 product-ux-workflows。
- Scope: 本轮分析和记账；未修改产品或 skill，未运行新的行为实验。案例依赖本项目行内入口和展开面板，落项目账本。
- Evidence: 产品版本 `4b6a5d00fe059a617f62a1a76537747fd8404152`；`web/app.js` 的行内按钮及 `web/codex-accounts.js` 的 select/perform 路径。前一问的真实页面检查已从下方面板直接展开，观察到账号卡片上的“仅刷新配额”及发送入口；未执行真实查询、发送或登录。记录在 `/Users/lindong/task-artifacts/monitor-ux-improve-20261017/monitor-button-necessity-commands.jsonl`。
- Decision evidence: 同目录 `decision.md` 第1项主动增加行内查询，比较了只改名和新增抽屉，却没有比较移除行内入口、保留统一面板；`decision-review.md` 第1判据沿该候选集放行。记录支持候选比较遗漏，不证明执行者从未读过简化规则。
- Rule evidence: 当前及 harness `c16a66d78865d352ffd61fca146d4820d99ec113^` 的 `review-product-capabilities.md` 已要求比较保留、删除、合并、迁移、更名，且明确不同名可能是重复入口。`review-product-simplification.md` 已要求比较保留与简化的收益和负担。因此不能归因为没有按钮或简化指令。上一轮 `test-ux.md` 修订补的是覆盖判断和委派衔接，不足以证明保留理由已充分。
- Diagnosis: 关键执行偏差是把减少定位步骤直接当作额外入口值得保留的依据；未在同一用户任务下比较主入口与快捷入口的增量价值和常驻阅读负担。真实交互与实现测试证明可用，不单独证明组织方式值得保留。加载层原因未核实，不据此新增加载门禁。
- Candidate: 优先在现有简化判断 owner 澄清能力必要性与入口必要性的区别，对重复入口比较移除后的完整任务路径、损失的上下文或效率与保留的阅读选择负担；能力审查消费该判据，不复制多套规则。不默认删除所有快捷入口，不新增 hook、固定评分或整站测试矩阵。
- Validation proposal: 后续获授权维护时，用本例检查能否主动提出移除快捷入口的完整替代路径，同时用确有上下文或恢复价值的重复入口反例检查是否误删。验证决定是否改变，不能只验引用、点击或控件存在。效果尚未验证。
- Search: 在项目与 ai-agent-config 的 open/closed harness 账本检索“重复入口|独立用途|行内.*配额|入口.*必要性”；中央旧条目包含全面体验入口与独立用途要求，未将其当作本次遗漏已经修复。只核相关 owner 与决策记录，未做全 harness 普查。
- Disposition: 本轮诊断完成，产品调整与上述 skill 候选均未实施；由后续获授权的 UX/harness 修复任务承接。
