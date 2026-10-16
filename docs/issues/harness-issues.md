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


## [open] HARNESS-20261016-8f3a：界面写作优化仍遗漏重复信息与内容布局

- Type: execution / acceptance / template emphasis
- Priority: medium
- Owner: agent-monitor 页面优化执行者负责产品修复；ai-agent-config 的 writing-templates / Web 阅读工作流维护者负责后续通用机制候选。
- Scope: 用户对上一轮全站写作优化提出的八项反馈；本轮核实与诊断，不实施产品或 harness 行为修改。
- Evidence: 2026-10-16 在 `http://macstudio:39001/` 与 `/sessions` 使用独立 headless 浏览器，展开机器、账号操作和历史账号。总览检查 1440×1000 与 1024×900 两个视口，会话列表检查 1024×900；未触发账号发送、授权、移除或手动刷新。持久截图在 `/Users/lindong/task-artifacts/agent-monitor-content-feedback-20261016/`，包括 `machines-1440.png`、`accounts-1440.png`、`quota-1440.png`、`quota-1024.png`、`history-1440.png`。源码比较基线为本地 `ae5566238222e2ab87d232ce01ee28f21d981d68`；浏览器动态读数不等于任意时刻的固定值。

### 逐项判断与产品修法候选

| 用户反馈 | 直接读数与判断 | 建议及保留边界 |
| --- | --- | --- |
| 1. 刷新重复 | 自然刷新时同一机器同时显示“刷新中”标签和“刷新中，仍显示上次已采集的数据。” | 合并状态与数据时点，避免重复说刷新；仍让读者知道眼前是上次快照。失败或排除的影响单独保留。 |
| 2. 提前换行 | 1440 视口下四张机器内容区宽 1122px，`.sync-machine-time` 最大宽约 514px；CSS 使用共享 `--measure:68ch`，并非手写换行。 | 状态元信息按组件可用空间排版，低频质量计数分层；不要把长篇说明简单拉成一行。 |
| 3. 文字贴边 | 同一组四张机器内容区 computed padding 为 `12px 0px`，正文左端与白底左边界重合；截图可见。 | 使用现有间距体系补水平内边距，核对标题、正文、状态及异常左边线的对齐。 |
| 4. 操作区与配额表重复 | 操作卡固定显示上次 Web 查询；表格通过 `quotaDisplayReading` 选择同账号较新的采集或查询读数。现场一个账号操作卡为 10/15 14:39:16，表格为 10/16 15:15:27，虽均为 0% 也不是同次观测。 | 配额比较以表格为主，操作区聚焦本轮结果；必要的操作前后读数留在操作详情并标时点，不按相同百分比直接去重。 |
| 5. 搜索占一行 | 1440 时整个搜索控件仍与筛选/排序同行，但标签在输入框上方，控件高 58px，其他控件高 32–36px；1024 时排序与清空控件换到第二行。 | 标签/输入与同行控件统一排版，保持可访问名称，窄屏允许有意义换行；不能声称任意宽度搜索控件都独占整行。 |
| 6. 旧读数提示 | 历史账号多行重复“查询读数较旧，可在账号操作中仅刷新配额”；实现以超过 6 小时为旧。 | 保留有用的新鲜度差异，改为更新时间附近的简短状态，操作入口就近或集中说明一次；“数据旧”不等于这句跨区域指路必须重复。 |
| 7. 未知与时间重复 | 8 条展开历史记录的 5h 栏均出现缺失用量与未知重置；其中同一时间还出现在账号列、配额列和更新列。用量缺失与重置未知来自不同字段。 | 两项都未知时合并缺失表达；用量已知、重置未知时保留后者。相同来源/时点由行级上下文承载，移动端仍需保持关联；来源或时点不同则分别标明。 |
| 8. 会话采集详情重复 | `/sessions` 展开后显示与总览相同类型的四机快照、质量计数、历史起点与刷新状态，两页共用 `renderSyncStatus`。 | 跨页重复本身不是缺陷，直接进入会话页仍需知道采集覆盖。建议会话页保留紧凑的覆盖/异常摘要，完整机器清单按需查看；不能删除上下文后让局部数据看似完整。 |

这些是现有页面问题，不全是上一 commit 新增。`git show ae55662 -- web/app.js` 显示第 1 项上一轮将“刷新中，现有数据仍可用。”改为现文案，重复结构原已存在；本次比较不把其他基线问题归为该 commit 引入。1024 截图内机器和更新列未同时可见，因此将时间合并到更新列时必须检查窄屏关联，不能只删文字。

### Harness 诊断与最小修法

上一轮执行将重点放在措辞、标题和展开后的自解释，并自行限定沿用布局。它做过真实页面操作，但既有浏览器/测试读数主要证明内容渲染、展开与回归，不能替代“这条信息是否还值得显示”“相邻区域是否承担同一职责”的判断。此次反馈证明上轮的全站优化完成表述超出了实际达到的阅读效果；不能据此声称没有用过浏览器、没有读过规则或是 ASD-STE100 导致冗余。

当前 `human-facing-message-principles.md` §3 已要求“主视图只留读者据以判断的内容”；`reading-principles.md` 已要求“不要求每个值重复时间戳”“共同说明不挤占每条的有效信息”。`ui-copy.md` 已接入阅读原则，并明确模板不能替代页面试用。已有要求可覆盖第 1、4、6、7 项；`web-ui-observation.md` 的几何与覆盖图要求可覆盖第 2、3、5 项。主要已定位缺口是执行与验收，尚不能证明入口不可达或规则缺失。

候选的组织性缺口是 `ui-copy.md` 的默认结构突出补齐上下文，而检查重点未直接提醒先判断内容取舍；这可能诱使执行者逐句补说明。这里只是有依据的机制假设，尚未通过行为对照证明因果。推荐在该模板把“按读者任务先决定保留、合并、下沉或删除，再改写”置于结构使用之前，并指回已有阅读原则；在阅读原则的“主次与重复”处明确同一对象、来源、时点的共同上下文可以在读者实际可见的父层承载。不同观测、异常或移动端不可见上下文不能误并。无需新增 writing 模板或另一套重复规范。

验收应落实已有 Web 工作流：按读者任务比较相邻组件及主视图/展开层，在真实页面说明保留或删除信息如何影响判断；内容优化涉及换行、留白、边距时按同一目标修布局。现有覆盖图与 reviewer 分工继续复用，不新增全局 hook、强制多 reviewer 或截图数量指标。对本任务可用这八项及三个反例检验：用量已知但重置未知、同账号不同观测时间、直接进入会话页仍能判断覆盖。后续修订是否改善行为尚未验证。

### 规则检索、边界与本轮处置

规则查询：`rg -n '同一|上下文|重复|^##|^###' ~/.claude/references/human-facing-message-principles.md`，并完整读取 `~/.claude/references/writing-templates/ui-copy.md` 与 `~/.claude/skills/product-ux-workflows/references/reading-principles.md`；在 `~/.claude/skills/web-ui-workflows/references/web-ui-observation.md` 查询并读取覆盖图、几何及交付最低证据相关正文。命中的既有要求如上，建议只调整决策顺序和跨区域承载，不能再写成新发现“必须查展开内容”。

历史查询：对 ai-agent-config 的 `docs/issues/harness-issues.md`、`docs/issues/archive/closed.md` 及本项目 `docs/issues/archive/closed.md` 执行 `rg -n '写作模板|ASD|STE100|writing-templates|信息.*重复|重复.*信息|阅读体验'`。7 个命中中重点核读 2026-08-26“整页网页信息检查没有先建分母”的历史实例：当时已补共享覆盖图。本次是展开后仍未充分判断意义和密度的新样本，不重开已完成的入口修复，不将其余命中扩为全 harness 审计。本项目 open 账本的 `rg -n 'writing|阅读|文案'` 仅命中浏览器连续性记录；那条不能解释此次页面内容问题。

本轮由当前线程综合同一会话和现场证据（task-routing R4，current / review，继承运行配置）；用户纠正触发了独立判断评估，但具体几何/时间重复可直接对照页面与源码，跨异质证据的校验成本接近重新完成分析，未新增委派。只记录诊断，无执行语义改动，review-changes 按“无行为面”免外审；自核差异与证据，不新增软件测试。八项产品建议由后续页面修复任务接续，通用模板候选由获授权的 harness 维护任务实施；本轮未实施原因是用户当前请求为分析与建议。其他未处置发现：无。
