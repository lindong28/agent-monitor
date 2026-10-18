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


## [open] HARNESS-20261018-label-value：配额来源标签的语义必要性未落实到阅读验收

- Type: execution / content acceptance
- Priority: medium
- Owner: agent-monitor UX 执行与验收者；共享指引候选归 ai-agent-config 的 ui-copy / review-ui / test-ux owner。
- Scope: 用户询问上轮全站优化为何漏掉“本页查询”“机器记录”“机器历史账号”“账号身份未确认”；本轮仅诊断和记录，不修改产品或共享指令。
- Evidence: 产品 `459bff6`；上轮基线前端 `65dcd1d` 与当时线上页面资产已匹配。持久目录 `/Users/lindong/task-artifacts/monitor-ux-round2-20261017/`，`independent/evidence/commands.jsonl` 第36条同时包含四类文字；`independent/findings.md` 的配额行概括为保留未知值和来源说明。当前浏览器再次展开历史账号及配额更新时间，显示同一行的“机器历史账号”“机器记录”“本页查询”及较新时间；展开层已有“机器归属与套餐仍按采集记录显示”。这是此前覆盖区域中的漏检，不是新加入的页面状态。

### 诊断与证据边界

原报告从“信息有真实语义、可防误读”直接给出保留结论，没有说明为何必须常驻当前列、是否可由分组或详情承载，也未明确区分机器关联历史与配额观测年龄。上轮主线程核收主要补交互路径和跨区域按钮比较，没有要求配额阅读结论展示这些关系的判断；主线程对漏检负有责任，不能只归给worker。

现有 reading-principles 已要求主次与重复、保留改变判断的差异；human-facing-message-principles 已要求主视图按决策相关性组织；ui-copy 已要求先定信息分工、共同上下文不逐处复述。原始worker轨迹第40行可见“主次与重复”正文，第47行可见“主视图只留读者据以判断的内容”及review-ui对ui-copy的引用。该轨迹尚未证实ui-copy全文取得，不能据此说所有规则都完整加载，也不能把加载不足认定为这次漏检的原因。可以确认的是已有相关判断要求，但实际产出未充分落实；模型内部原因、时间压力或指令组织是否主导均未核实。

harness `d26d46c2` 增量补的是跨区域入口的替代路径交回与核收，不涵盖这四类字段/标签的阅读结果，不能用该提交或63项实现回归称本问题已解决。旧项目案例 HARNESS-20261016-reading-redundancy / reading-structure 已处理当时的重复机器名和列组织；本例说明同类判断仍会漏检，不推翻其具体修复，也不将其关闭解释为通用遵循率已证实。

### 最低充分改进候选与验证

优先重组既有内容判断和核收，不另造模板、标签黑名单、固定问题数或全局hook：ui-copy沿“先定信息分工”明确区分事实需要保留与当前展示必须保留，遇重复说明/混合来源行比较删去、合并到父层或下沉详情后，读者会失去哪项必要判断；review-ui继续引用同一owner。test-ux在既有内容验收中核收读者实际能否说清对象、当前采用的值、时点及影响决定的未知；控件可用、区域到访和泛称“来源说明应保留”不能代替这个结果。按实际不同信息关系取样，不为每个DOM节点填表。

实施后验证应使用未改造现场材料和用户自然短请求，不向执行者点名四个词或透露预期删改。本案已知，只能作回归样本；另用未参与修法的同类案例核迁移，保留来源确实改变判断、未知身份不可省略的合法反例。分别报告自主发现、提示后补查和页面修复复验，不把删除数量、字数减少或静态引用检查当行为收益。尚未运行这项验证，不承诺新指令必能检出全部问题。

- Search: 在项目 `docs/issues/harness-issues.md` 与 `docs/issues/archive/closed.md` 查询 `entry-necessity|reading|标签|信息分工|配额`，定向读取上述旧案例及当前owner；本次没有全规则栈普查。
- Disposition: 诊断与建议已记录。产品标签优化、共享指令调整及行为验证均未实施；本轮没有派发后续执行任务，也没有后台自动接续。待后续明确实施请求按本条与原始证据接续。
