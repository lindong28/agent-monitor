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


## [open] HARNESS-20261016-reading-redundancy：全站阅读复验漏掉同一行的条件性重复

- Type: execution / acceptance
- Priority: medium
- Owner: agent-monitor 阅读优化执行者；通用执行指引候选归 ai-agent-config 的 review-ui / reading-principles owner。
- Scope: 用户追问上一轮全站优化为何漏检。本轮诊断、建议与记账，不修改页面或共享 skill。
- Evidence: 产品提交 `a11759194d8b47186734d6a516a287ba236eee56`；harness 提交 `0dfe3d3b8d0a9aa9d0383d65259890ad47914da4`；持久原始记录 `/Users/lindong/task-artifacts/monitor-full-reading-20261016/` 的 `live-overview-expanded.txt`、`audit_flows.py`、`audit_live_flows.py`、`state.md`、`instructions-before/`、`change-review.md`。

### 观察与定位

上一轮真实页面文本已经包含单机器行的 `macbook` 与 `机器采集 · macbook`、`macstudio（本机）` 与 `机器采集 · macstudio`。问题不是后来才出现或页面无法取得。`web/app.js` 的 `quotaMachinesCell` 输出机器集合，`quotaUpdatedCell` 在普通采集分支无条件重复 `reading_from`；后端 `server.py` 按账号聚合机器、选择最新读数来源。两个概念可不同，但单机器且来源相同的实际行里，重复机器名没有增加区分。

覆盖图把配额区域列为 deep review；配额操作脚本记录服务商筛选、搜索、空结果、排序及截图。上述记录可以证明操作和区域覆盖，不能证明同一行相邻字段是否各有必要增量。59 项测试和整页无横向溢出的读数同样不能发现这类意义重复。独立变更审查明确未承担完整浏览器阅读验收，不能用其通过替代 caller 的内容判断。

冻结的旧版 reading-principles 已有“主次与重复”“保留会改变判断的差异”“长列表按不同内容/状态取样”；新版 review-ui 又明确核对各部分是否提供不同的必要答案。原规则存在，入口可达；本次不能归因为缺少去重原则。可确认的是该条件性重复未被识别或记录为 finding，语义检查的交付结果不充分；无法从留存记录判明当时是未比较两列，还是比较后错误地认为来源信息必须无条件保留。不能凭记录缺席推断内在思考或指令完全未加载。

### 推荐与验收

按实际阅读单位落实已有原则：以整行、卡片或父子展开为单位，判断删去第二处信息是否损失对象、来源、时点、状态或行动上的必要区别。对本案分别检查单机器且来源一致、多机器、来源与机器列表不一致、本页查询、历史读数；按关系取样，不能只按服务商和控件取样。前一轮只加强同一覆盖图的接续，解决的是范围遗漏，未证明这种行内意义判断已经落实。

产品候选是仅在机器列表不能唯一表达采集来源时显示来源机器名，保留查询/采集方式和必要状态区别；不是删掉整个来源列。通用候选是细化既有 reading-principles 的同一条“主次与重复”，让检查单位和判断依据明确，再由 review-ui 复用；不新增全局 hook、模板或固定全站检查轮。本轮只提出候选，未修改或声称验证其效果。

后续验收应同时发现单机器冗余、保留多机器的真实来源差异，并在宽屏与窄屏确认邻接上下文仍成立。可以给明确的项目显示分支补测试，但文本关键词计数不能充当通用语义判定。归因与建议已交用户；后续获授权的页面/skill 修改由上述 owner 接续。没有其他新增未处置 finding。
