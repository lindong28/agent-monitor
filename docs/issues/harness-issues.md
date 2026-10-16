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

## [open] HARNESS-20261016-8d72：阅读宽度优化误将文档实例上限迁移到控制台

- Type: execution / template transfer / acceptance
- Priority: medium
- Owner: agent-monitor Web UI 执行者；共享指令候选归 ai-agent-config web-ui-workflows owner。
- Scope: 本轮诊断并记账，未实施页面回退或共享指令修改。
- Evidence: 用户要求采用 reading-instance 中“正文、说明和列表使用内容区宽度；整页统一限宽，数据不变”，随后指出两侧空白过多。提交 `8cce163787962c4d7d2120b7dff07a09411510e5` 为 app-header/app-shell 增加居中 1560px 上限。上一轮真实 Chromium 1920px 读数为 shell 1920→1560、main 1684→1324，五个页面一致；左右各新增 180px 外壳留白。原始 before-results.json、live-results.json、截图与 release-receipt.md 位于 `/Users/lindong/task-artifacts/monitor-content-width-20261016/`。更宽视口的留白只能由 CSS 推导，未在本轮重测。
- Diagnosis: “统一边界/块内用满”被执行为“统一复制文档实例的固定最大宽度”。reading-instance 入口明示不适用于 dashboard，§5 明示 1560px 是该实例整体上限且控制台没有对应项；关联 ADR `20261016-ccb1-reading-width` 也注明实例起点、非所有页面最优值。执行者读过该实例，仍将参数带入既有控制台，故不能归为缺文档或未加载。用户点名某条规则并未指定 1560px。后续为被压窄的内容区将网络卡片四列改为两列，是上游决定的连带改动。
- Acceptance gap: 原先测量已显示主内容区变窄，但将“实现的宽度与自己选定的 1560px 一致、无横溢出、段落使用块宽”视为改进充分证据，未据控制台扫读任务评估外壳留白及表格/图表可用空间。现有 design-ui L0/L1/L2 与 web-ui-observation 已要求适用性、视觉关系及真实任务检查；不能把本次执行失效外推为规则完全缺失。
- Recommended recovery: 保留取消段落独立 ch 上限；控制台外壳恢复随视口扩展和统一页边距。网络卡片随实际可用宽度重新决定列数，恢复宽屏容量并保留窄屏溢出修复；设计记录同步到最终选择。此为建议，尚未执行。
- Shared improvement candidates: 在参数使用处澄清原则迁移与实例数值继承的区别，并把文档阅读区与控制台应用外壳分开；沿现有观察方法对照改前/改后外侧留白和任务内容可用宽度，判断可读性目标是否真正达成。无需另建模板、通用占屏比例或强制外审。用本项目控制台与长篇文档两类合法场景验证，保留文档合理限宽；尚未检验后续 agent 行为收益。
- Rule lookup: 定向读取 reading-instance、关联 ADR、design-ui 的 form factor 与参数层级、web-ui-observation 的文字行框/留白/真实任务规则；在 monitor 与 harness 两仓 open/archive 以“限宽|留白|1560|reading-instance|模板.*参数”检索，命中原阅读区域决定与既有留白测量/评分案例，未将这些命中视作本次误用已修复。直接复用本会话原始改动和观察记录，不重跑已具区分力的页面测量；当前模型完成短链诊断，不追加独立审查或行为实验。
