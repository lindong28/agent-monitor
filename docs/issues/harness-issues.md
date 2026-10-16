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


## [open] HARNESS-20261016-reading-structure：配额阅读优化保留既有列结构，未充分比较信息重组

- Type: execution / information architecture
- Priority: medium
- Owner: agent-monitor Web 阅读优化执行者；通用指引候选归 ai-agent-config 的 writing-templates/ui-copy 与 review-ui owner。
- Scope: 用户在单机器重复修复后提出取消“读数来源”列、把账号下观测时间移到相对更新时间列。本轮只分析与记账，不改变页面或共享规则。
- Evidence: 已发布 `c51c14cb9d676fc2cbc3c8822d8f41c3d9de4f46` 的 `web/index.html` 配额表头、`web/app.js` 的 `quotaAccountCell` / `quotaMachinesCell` / `quotaUpdatedCell`；本会话前轮建议和实现；原行内去重记录 HARNESS-20261016-reading-redundancy 已归档，原修复不被本条否定。

### 判断与证据边界

当前账号列除了身份和动作，还显示配额观测时间；读数来源独占一列。前轮依据多机器时 reading_from 与 machines 概念不同而保留来源列，只在单机器同源时省略机名。这保留了必要事实，但没有比较这些事实是否必须各占一列。列内容正确与整张表便于扫描是两个问题；不能从局部条件去重的通过推导结构优化充分。

现有 ui-copy 已要求“先定信息分工”，允许保留、合并或移入明细；review-ui 已要求先判断信息取舍和层级。缺口不是完全没有结构原则，而是本案例未把保留语义与保留原布局区分开，未将“这列是否值得独立存在”作为方案比较。不能据此判定模型心理原因、规则未加载或所有执行者均有同样问题。

### 建议与后续验证

推荐按读者问题组织账号、机器与配额更新时间：账号承载身份及直接账号动作；机器承载使用位置，必要时在具体机器名旁标出读数来源；配额更新时间承载当前显示配额实际取得的相对时间。完整时间可在明细查看，时间未知不填零或刚刚；刷新页面不应使旧配额时间变新。本页查询与历史/无效读数的必要区别也应在对应更新时间或状态位置保留，不能因取消来源列抹掉事实。此为尚未实施的结构方案，不宣称已经在真实页面验证。

通用指引候选优先收敛到既有 ui-copy 的“先定信息分工”：信息须保留不等于原容器须保留；审核应从读者任务判定容器/列的独立用途，再处理重复和措辞。review-ui 继续引用唯一 owner，不新增另一份检查清单、hook 或默认多轮审核。是否需要改正文及其效果仍待实施阶段验证，不把追加原则当成收益。

后续产品验收同时检查单/多机器、采集/本页查询、当前/历史、已知/未知时间及宽窄布局；用实际配额观测时间验证相对时间，而非页面刷新时点。harness 方法验收应看执行者是否能提出并比较列合并/迁移方案，同时保留来源区别，不能只验证被点名字符串消失。上述实施归 owner 后续获授权接续；本轮诊断完成，产品与指引候选未实施。
