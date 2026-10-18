# Harness issues

已关闭记录见 [archive/closed.md](archive/closed.md)。

## [open] HARNESS-20261018-live-label-recurrence：真实全站审查仍漏未知身份行的组织判断

- Type / Priority: instruction execution / medium。
- Owner: agent-monitor UX审查执行与核收者；共享规则候选归ai-agent-config ui-copy / product-ux-workflows维护者。
- Evidence: `/Users/lindong/task-artifacts/monitor-live-harness-20261018/` 的事前acceptance、initial/candidate原生轨迹与独立初稿。基线d1993e42实跑未完成四类标签判断；两份正文细化后的候选F3完成三类来源/历史标签，仍未判断“macmini 未登录／账号身份未确认”与机器列的组织关系。截图quota.png实际含该行，适用规则已加载；不能归因为未到访或规则不存在。
- Scope: 这是同一产品两版各一次真实执行；必要未知没有被误删，严格盲验因宿主历史概述注入未核实，不证明长期检出率。原HARNESS-20261018-label-value的共享修订已完成，不重写其静态验证历史；本项跟踪新真实失败。
- Implemented / Remaining: 本轮已按ai-agent-config ADR b71f要求具体阅读候选，并在全面UX入口并列核收信息组织、操作结果和入口取舍；候选只部分满足目标。按有界方案保留失败，不反复追加指令到本样本过关。产品四标签修复仍归ISSUE-UX-20261018-8e19。
- Next: 后续harness维护者从完整未知身份行及本次实际轨迹判断是否遗漏不同信息关系的取样或具体判断，再决定最小修改；必须保留必要未知和分组关联，不能用标签黑名单或提示后发现替代自主检出。当前未派发后续任务，不承诺后台自动继续。
- Search / Disposition: 核对当前ui-copy、全面UX入口、review-ui/test-ux及项目closed中的label-value；详情见[本轮审查](../references/ui-harness-review-20261018.md)和共享仓reading-judgment-20261018.md。残余自主检出未解决，明确保持open。

### 2026-10-18接续诊断：有限复跑不等于整体任务可以停止

用户追问是否应让improve-skill持续修复与复跑，直到本轮预期缺口满足验收。本次只分析并记录建议，未修改共享指令、未重跑实验。主线程按R4直接处理，复用上轮原始失败和当前owner正文，不额外委派。

纠正上轮处置理由：候选仅检出三类、未知身份行仍漏检后，caller用“一次候选实跑”的自定阶段边界结束整个改进任务。improve-skill已有“新证据只接续受影响部分；没有改善不能写成通过，也不意味着无限迭代”，但未在验证结果处明确返回诊断/修复的分支。上游execution-control §1已明确阶段检查点不是任务总预算，plan-execution-principles的Trajectory/Stop Gate也已要求继续可做的授权工作。因此这是执行偏差，伴随局部接续表达可补强；不能把已有规则说成不存在，也不能将“无需无限迭代”理解为固定一轮后可以退回未完成目标。指令改动能否防复发仍待实测。

建议由improve-skill在验证出口接回既有owner：本轮必需验收失败且可继续时，诊断失败层次，修正有依据的环节，再验证失败项及受影响正常路径；修法未明时先做能区分解释的调查，不能原样重跑碰运气。每轮固定原验收并保留版本及全部结果；无效实验先修实验，部分通过不抵销剩余失败。no-fix只代表当前候选无需修改，不自动表示任务目标完成；重复停滞返回Trajectory Gate重设路径。任务停止、预算、权限与真正取舍仍引用既有owner，不建立第二套预算政策或允许无限循环。

推荐完成条件是本轮已确认应修的缺口达到事前验收，且受影响合法行为未回归；不扩成所有历史或未来问题清零。若目标需要改动、碰到有效总预算或不可代办的真实边界，按原政策交回具体决定，不能由agent自行把目标降成“已记open”。验证应包含部分通过后继续、实验无效先修仪器、反复无进展后重设方法，以及真实边界下正确停轮的相关最小场景；不写固定重跑次数或只保留最后一次通过。

机制取证：读取improve-skill、diagnose-harness、execution-control-principles §1/§4、plan-execution-principles Trajectory/Stop Gate及maintenance-validation；中央账本按improve-skill/轮数/部分通过检索，定向核HARNESS-20261017-e4c2，其“不要新增固定实验轮数或另一套停止规则”与本建议相容。最低修改面为improve-skill的验证返回段，必要时同步SKILL入口描述；不预设新增循环引擎、hook或独立工作流。本建议与剩余真实漏检继续归本条owner，尚未实施，没有后台任务。

### 2026-10-18接续实施：共享入口已补返回分支，产品漏检仍开放

用户随后授权按上述建议修复 improve-skill。共享仓已按 ADR `20261018-9c72-improve-skill-continuation` 修改验证出口及主题入口：必要项未达时接回诊断、修复与受影响复验，真实预算和停止边界复用既有 owner。方案与证据归 `ai-agent-config/docs/references/improve-skill-continuation-20261018.md`，不在本条复制通用规则。

两个隔离合成任务各执行一次（Codex gpt-6-astra/medium）：继续场景修正校验路径后仍3/4失败，接着修产物并复验4/4；权限场景修好已知数值，保留缺失源数据与非零验收，明确整体未完成。caller核实际产物、完整工具轨迹及10份只读文件不变。前者覆盖0、null、正数三类数量，后者覆盖已知与缺失两类；不是严格盲验、旧新同输入收益对照或真实页面复验。

此前分析中的“尚未实施”现由本段更新：验证后接续的指令修订已实施并完成上述有限验证；未知身份行自主判断与产品四标签修复仍未验收，本条保持open，原Next继续只针对该产品问题。本次没有后台任务，也未将合成任务通过计为产品自主检出。

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
