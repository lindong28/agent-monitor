# tt-web UX Issues

基于真实产品入口观察、但不在发现当轮修复范围内的用户体验问题。条目只记录真实页面读数，不用源码推断冒充端到端证据。

## [open] ISSUE-UX-20261014-6c31 — 会话后台刷新收起已展开的记录

- **Observed**: 2026-10-14，`7f9fed0` 的 MacStudio 真实会话详情中，点击记录后 usage 证据与金额依据实际可读；后台数据更新后，URL 与 50 条记录仍在，但 `.session-entry-detail` 从 1 变成 0，需要再次展开。390px 再展开后的文字可见，问题是阅读状态没有保留。
- **Attribution / Scope**: 基线独立。`initSessionDetail` 既有数据 watcher 重新调用 `renderSessionDetail`，其整表重建不保存展开状态；本轮增加证据文字的 diff 未改变该机制。这不是金额或 token 算术错误，亦不据此否定已取得的展开内容读数。
- **Owner / Next**: agent-monitor Web 维护者；单独保留相同记录在后台数据更新时的展开状态，按稳定记录身份关联，显式换会话/筛选时另核重置语义。本轮十类上游契约修复不扩大为整个页面状态管理改造。
- **Evidence**: `/Users/lindong/task-artifacts/upstream-contract-repair-20261014/live-detail-evidence.json`、`live-detail-narrow.png` 及同轮实际浏览器读数；未进行长期发生率测量。

- **Repair 2026-10-16**: 同一详情页以记录身份保存记录与技术证据展开状态，并保存会话信息展开状态。真实旧页面触发 focus 数据更新后，DOM 已更新且展开从 true 变 false；隔离预览改后保持 true。生产复验与发布身份单独保存在 `/Users/lindong/task-artifacts/agent-monitor-reading-20261016/release-receipt.md`，本条不将源码或预览当作部署证据。

## [open] ISSUE-UX-20261007-95bd — LLM 调用筛选直接显示内部状态名

- **Observed**: 2026-10-07，macbook 本地 39001 的真实 `/llm-calls` 中，请求结果选项包括 `local_rejected`、`unknown`；尝试结果包含 `http_error`、`transport_error`。顶部摘要已有“拒绝、失败、中断或未知”等中文，筛选仍需读者自行映射。
- **Recommendation**: 选项显示对应中文含义，保留原值用于筛选参数和技术详情，不改变请求/尝试的统计语义。
- **Owner / Scope**: agent-monitor 后续内容优化；本轮仅观察，未改调用页。

---

## [open] 页面整体转中文后，自托管字体仍是 latin-only 子集，视觉系统校验器看不见这件事

- **Discovered**: 2026-09-15，文案中文化的独立审查中报出。
- **读数**：`web/vendor/fonts/` 只有 `ibm-plex-sans-latin-{400,500,600}-normal.woff2` 与 `ibm-plex-mono-latin-400`，四个 `@font-face` 全是 `*-latin-*` 且**没有 `unicode-range`**；`--font-sans: "IBM Plex Sans", ui-sans-serif, system-ui, sans-serif`。中文化后页面上约 95% 的字形逐字回落到系统字体。
- **两个后果**：
    1. 视觉系统里逐项校准过的 `13px/18px`、`letter-spacing: -0.015em … 0.04em` 与 4px 间距阶梯，**都是对 IBM Plex Latin 的字面量测的**，现在不作用于绝大多数文字；`font-weight: 600` 在缺半粗的回落字体上会被合成加粗。
    2. `claude/skills/web-ui/workflows/web-ui-design/scripts/validate-visual-system.js` 读 computed `fontFamily` 的**首项**，那仍然是 `"IBM Plex Sans"`——**这台仪器在「字体覆盖了」与「字体完全没覆盖」两种情况下给出相同读数**，所以 2026-09-15 更早那轮的 13 pass / 0 fail 不能用来支撑中文化之后的版式。
- **待用户裁决的取舍**（本轮未动 `styles.css`）：① 只加 `unicode-range` 并在字体栈里显式点名 CJK 家族——几乎零成本，让回落变成有意的而不是默认发生的，但版式参数仍未针对 CJK 校准；② 另打一份 CJK 子集字体随仓分发——版式可控，代价是体积（手机经 Tailscale 打开时尤其明显）与一次子集化流程；③ 就按系统字体，但把 CJK 的行高与字距单独校准一轮。

## [open] LLM 调用页的时间戳既不带 UTC-offset 标签，也不跟随服务端时区

- **Discovered**: 2026-09-15 独立审查报出。**缺陷先于本次改动存在**，但本次改到了那一行（`toLocaleString()` → `toLocaleString("zh-CN")`）、且跨页约定这一段本次被修订过，故属边界命中。
- **读数**：`web/llm-calls.js` 的 `formatTime()` 是 `new Date(value).toLocaleString("zh-CN")`——不传 `timeZone`，也不追加 `shortOffset`。`app.js` 的 `fmtAbs()` 两者都做。
- **与契约的冲突**：`ux-contract.md` 跨页约定要求「所有绝对时间戳按本机当前系统时区渲染并带 UTC-offset 标签…不随浏览器陈旧时区漂移」。LLM 调用页三处时间戳（请求表、尝试表、覆盖行）都不满足。
- **修法**：`formatTime()` 改为复用 `app.js` 已导出的格式化路径（须先把 `fmtAbs` 或等价物加进 `window.TTWeb`）。**本轮未改**：它要动跨文件的导出面，不属文案翻译。

## [open] 服务刚重启时，机器卡片同时挂「刷新中」和「未检查」，正文却说"还没检查过"

- **Discovered**: 2026-09-15，文案中文化后以读者身份通读五页时发现。**先于该次变更就存在**：英文版是同一组合（chip `refreshing` + `not checked` + 正文 `This server has not checked this machine since it started.`），中文化只是把同一矛盾译了过来。
- **读数**：`tt-web restart` 之后立刻加载任意带机器状态条的页面（总览 / 透视 / 会话 / LLM 调用），四台机器每台都同时显示「刷新中」「未检查」两个标签，正文是「用的是上次的数据；本服务启动后还没检查过这台机器。」。第一轮同步完成后自行消失。
- **成因**：`app.js` `renderMachineStatus()` 的 chip 是并列累加的（`syncing` 与 `availability === "unknown"` 各加一个），而 consequence 是 if-else 链、`availability === "unknown"` 排在 `machine.syncing` 之前，于是"正在刷新"这个事实只进了标签、没进正文。
- **读者代价**：两个标签时态相反，正文选了过去时那一侧；读者看不出"它此刻正在补这次检查"，可能以为卡住了。
- **本轮为何不修**：改的是 if-else 链的分支顺序与合并措辞，属行为改动而非文案翻译，不在本轮授权范围内。修法建议：`unknown` 且 `syncing` 时正文合成一句（如「本服务启动后还没检查过这台机器，正在检查；期间用的是上次的数据。」）。

## [open] 切到「会话」要等 3.4 s、切到「LLM 调用」要等 5.9 s，等待全在服务端出数

- **Discovered**: 2026-09-15，文案中文化任务里按「交付前的最低证据」取「等了多久」读数时撞见。**先于该次变更就存在**——`server.py` 本轮的改动逐行核过，全部落在字符串字面量上，没有一行查询或计算逻辑（`git diff -U0` 过滤后为空）。
- **读数**（`~/.claude/bin/interaction-latency`，视口 1440×900，阴性对照点已选中的导航项、如实报 `no_change`）：
    - 页面**外壳**切换（点侧栏 → `<main>` 换完）：冷 **59.9 ms** / 热 **64.5 ms**。这一段很快，客户端换页是有效的。
    - 切到「会话」、**等到真实计数出现**（`个会话`）：冷 **3760 ms** / 热 **3414 ms**。
    - 切到「LLM 调用」、等到 `条请求` 出现：冷 **6045 ms** / 热 **5914 ms**。
- **归因**（`curl --noproxy '*'`，本机 origin，各一次）：`/api/sessions?range=30d` TTFB **3.34 s** / 下行 **2 435 746 B**；`/api/llm-calls?range=30d&page_size=50` TTFB **2.88 s** / 86 264 B；对照 `/api/overview?range=30d` TTFB **0.13 s**。
    两条慢路径的成因不同：会话页是 **origin 现算 + 2.4 MB 一次性下行**（页面自己只显示 100 行 / 页，却把全部 6313 条一次取回后在前端分页）；LLM 调用页是 **origin 现算 2.9 s × 两次串行**（`/api/llm-call-filters` 与 `/api/llm-calls` 顺序发出），外壳 60 ms 之后读者还要盯着 loading 近 6 秒。
    **尚未分段**：这 2.9 s / 3.3 s 里有多少是 SQL、多少是 JSON 序列化，没有测过——归因到此为止，再往下要 profile。
- **本轮为何不修**：本轮授权范围是页面文案，改数据路径是另一件事，且两条路径的修法不同（分页下推 vs 两次请求并发化），各自要单独定方案。交用户裁决要不要起一轮。

## [open] ISSUE-UX-20260915-b7d4 — 同一台机器的配额刷新失败在 Claude 与 Codex 下各出现一行、文字逐字相同

- **Type**: content-redundancy
- **Discovered**: 2026-09-15，真实 hub 页 Quota 表
- **Observed**: `tencent-webserver-china` 的 exporter 太旧、不带 `refresh_error` 字段，服务端对每个 provider 各生成一条 `refresh_errors`，页面按 provider 各渲染一整行：`Quota refresh failed on tencent-webserver-china: Active quota refresh is unconfirmed; update this machine's tt-web exporter and refresh. Previous readings, if any, remain below.` 在 Claude 块与 Codex 块各出现一次；加上 macstudio 的 Claude 凭据失效那行，7 行数据里 3 行是错误行，且都排在账号行之前。
- **Impact**: 读者要先读过三行错误才到第一个账号；"exporter 太旧"与 provider 无关，重复一遍不增加信息。
- **Reproduce**: 任一机器的 exporter 早于 quota `refresh_error` 字段时打开 Overview。
- **Recommended direction**: 与 provider 无关的机器级原因（exporter 太旧 / 状态无效）合并为一行、跨两个 provider 块只出现一次；provider 特有的原因（如 Claude 凭据失效）保留在该 provider 下。行的位置（账号行之前还是之后）连同上一条一起交用户。

## [open] ISSUE-UX-20260915-c9e6 — 「Top projects this week」本周无数据时画一张空坐标轴，而不是空态

- **Type**: empty-state
- **Discovered**: 2026-09-15，本机 standalone 实例 `http://127.0.0.1:39001/`（其本地数据停在 2026-09-09，本周尚无用量）
- **Observed**: `top_projects_week` 为空数组时，Chart.js 仍画出 0–1.0 的 x 轴与网格、无任何条、无文字；同一页的「Model mix this month」有数据、正常。
- **Impact**: 空轴读起来像"图坏了"或"加载中"，与 A5 要求的明确占位不符；无法区分"本周没花钱"与"数据没到"。
- **Reproduce**: 在一台本周无用量的机器上打开 Overview（或把系统日期调到周一 00:05 后刷新）。
- **Recommended direction**: 数据为空时复用本轮新增的 `.chart-empty-state` 写一句 `No project usage recorded this week (Asia/Shanghai)`；若本周数据为空是因为机器数据陈旧，同时指向 Spend 区的覆盖说明。

## [open] ISSUE-UX-20260915-d0f8 — Cost over time 的日线把没有数据的日子直接跳过，x 轴看不出缺口

- **Type**: data-clarity
- **Discovered**: 2026-09-15，真实 hub 页与本机实例都可见
- **Observed**: `/api/overview?range=30d` 的 `cost_over_time.rows` 共 29 行，缺 `2026-08-26`；图的 x 轴是类目轴，标签直接从 `2026-08-25` 接到 `2026-08-28`（间隔标签隐藏了一档），曲线把两侧点连起来，看不出那天是零、还是没采到。
- **Impact**: 读日线时"哪天没花钱"是常见问题，而零花费与缺采集在图上同形。
- **Reproduce**: 选 30d，对照 API 行数与日历天数。
- **Recommended direction**: 服务端把窗口内没有 bucket 的日期补为 0（或补 `null` 并让图断线），二者的语义要与「成本口径」里"缺源保留最后可信值"那段一致，先确认那天是真零再决定填哪种。


## [resolved 2026-09-15] ISSUE-UX-20260826-5b81 — 英文 Overview 的历史账号与 All-history 说明局部切换为中文

- **Type**: language-consistency
- **Discovered**: 2026-08-26
- **Priority**: low
- **Observed**: 真实英文 Overview 展开 `Past accounts (4)` 后出现 `已登出`、`移除`、`观测于`、`最后观测` 与 `最后观测值，不代表当前状态`；Range=All 时，Cost over time 出现 `历史自 <date> 起累积；更早未采集。`。
- **Impact**: 同一表格和同一页面内切换语言，降低扫描一致性，也让仅按英文关键词查找的读者漏掉状态与操作。
- **Reproduce**: 展开 Past accounts；再把 Range 切到 All 并查看 Cost over time 的 coverage 说明。
- **Recommended direction**: 按页面既有英文语言基线统一这些用户可见文案；不要改变历史值限定、删除确认或覆盖范围语义。
- **Resolution (2026-09-15)**: 统一的方向与当初建议相反——用户裁定把**整个界面**转为中文，语言基线由英文改为简体中文（见 `ux-contract.md`「界面语言」）。`已登出` / `移除` / `观测于` / `历史自 …` 这些原本"局部中文"的串现在与周围一致，历史值限定、删除确认与覆盖范围语义均未改动。

## [open] ISSUE-UX-20260826-da8a — 成本计算说明把内部流水线状态直接交给读者解释

- **Type**: content-clarity
- **Discovered**: 2026-08-26
- **Priority**: medium
- **Observed**: 真实页面的 `How these cost figures are computed` 第一段能说明 Codex/GLM 成本何时为估算；第二段使用 `accepted rows`、`currently readable source`、`protected-counter decrease`、`last trusted cost` 与 `remain frozen` 等实现词。
- **Impact**: 读者真正需要判断的是哪些金额为估算、数据可能旧到什么程度、哪些范围不完整，却必须先理解内部重算状态机。
- **Reproduce**: 在 Overview 的 Cost over time 下展开计算说明并阅读第二段。
- **Recommended direction**: 保留估算、重算窗口、缺源与保护性保留旧值的事实边界，用读者能据此判断可信度和是否要处理的语言重写；实现术语只在诊断文档保留。

## [open] ISSUE-UX-20261006-23be — 手机宽度下导航遮盖品牌文字

- **Type**: responsive-usability
- **Discovered**: 2026-10-06，第二产品模板适配期间
- **Observed**: 在真实 `http://macstudio:39001/` 的 390×844 页面中，「总览」导航覆盖 `agent-monitor` 品牌中段；本次隔离预览同样出现。改动前后的共用导航 HTML/CSS 未变，属基线独立问题。
- **Impact**: 手机顶部产品名称不完整；本次实际总览/透视导航仍可操作。
- **Disposition**: 后续 Web UI 导航优化处理；本次仅整合成本摘要与趋势，不扩改全站导航。

## [open] ISSUE-UX-20261012-e6a9 — 调用筛选更新期间旧请求仍可作为当前结果打开

- **Priority / Expected**: medium；README 请求结果筛选、从列表打开请求尝试链的排障场景。等待中的旧结果应明确标为上一查询结果，或停止其详情操作，避免让新筛选与旧行产生错误关联。
- **Observed**: 2026-10-12 真实 `/llm-calls` 获取完整条件后选择 failed；URL 和已选条件已变为 failed，2.5 秒后页面仍显示“加载中”和上一组 success 行，旧请求按钮仍能打开 success 详情。这是加载过渡态的上下文问题，不是声称 failed 终态查询返回了 success。
- **Reproduce / Evidence**: 从默认请求列表选择 failed，在新结果返回前点仍在表中的第一条请求。`~/.local/share/agent-reports/ui-reaudit-20261012/evidence/calls-result.json` 保存筛选、加载与详情三个阶段。当前 `setLoading()` 只设置加载说明和 aria-busy，未给旧行查询范围标记或禁用详情入口；主线程核对了源码与原始阶段记录。
- **Recommendation / Owner**: agent-monitor Web 维护者在条件变化时使列表与已生效查询保持关联；可清空为加载态，或标明上一查询并暂时禁用详情/分页。不要只把轮询刷新与筛选变化都当作同一种加载。本轮审查，不修改运行行为。

### 2026-10-12 调用页既有问题复查

- **ISSUE-UX-20261007-95bd**: 当前真实筛选仍直接出现内部 outcome 值；请求行 success 与请求详情等状态仍使用英文内部名/下划线转空格。现行 ux-contract「界面语言」明确允许原始枚举，便于与 URL/日志对照；本轮不把英文枚举本身判为缺陷。可增加中文解释作为产品建议，同时保留原始值。没有发现推翻该约定前提的新读数。
- **时间戳无 UTC-offset**: 当前真实请求行显示如 `2026/10/12 18:25:38`，没有 GMT+8；guide 的绝对时间带时区。源码 `formatTime()` 仍直接用浏览器 `toLocaleString("zh-CN")`。本轮确认缺少标签，未修改操作系统时区，也未实测切换系统时区后的行为。
- **边界**: 本轮六个页面在 390px 的整页宽度均未超过视口；主导航可横向滚动并到达网络页。默认屏外入口属于可发现性改进空间，不据此新报导航不可达；不据本轮有限观察关闭旧版品牌遮盖或所有响应式问题。

## [open] ISSUE-UX-20261015-f28c — 全页阅读层级、状态关联与窄屏明细需要优化

- **Discovered**: 2026-10-15，真实 `http://macstudio:39001`，桌面 1440×1000 与窄屏 390×844，包含同步、图表表格、周期比较、历史账号、11 个账号操作详情和会话记录展开。
- **Observed / Impact**: 会话首条记录约在桌面 1,189px / 窄屏 1,782px；旧配额与登录失效分区呈现；批次 9/11 与新一轮 9 个账号缺少就地关联；网络主要异常证据排在末尾。其他逐页读数与建议见报告 R01–R12。
- **Evidence / Scope**: [全页阅读审查](../references/ui-reading-review-20261015.md)。覆盖 6 种页面形态，历史记录按状态抽样；未触发发送、登录、移除或主动刷新作业，未固定线上部署 SHA。
- **Disposition / Owner**: 本轮只分析，全部建议尚未实施；agent-monitor 产品与 Web 维护者按报告第一至第三批迭代并在对应真实入口复验。历史 issue 不在本轮批量关闭。

- **Follow-up 2026-10-16**: 已实施阅读层级优化；逐项采用范围、保留项和预览读数见原报告的同日续记。旧观察保持不变，发布与真实入口读数见该续记指向的 release receipt；不批量关闭其他 UX issue。
- **Second pass 2026-10-16**: 在 `95e6dc2` 复现机器技术详情刷新收起、逐期内容刷新回到顶部，以及会话身份与逐期内容层级残留问题；已补实现并取得隔离预览对照。与 R06 / R08 / R09 / R12 的对应关系及最终线上回执入口见同一报告“第二轮复查”，不另建平行 UX 台账。

- **STE content audit 2026-10-16**: 按 ASD-STE100 Issue 9 的可迁移写作原则复查五页及展开内容，发现 S01–S10：金额未知与缺价格混称、完整会话的范围歧义、估值与实际花费比较、术语、批次指令、历史重算长句、网络规则、接入步骤、时间标签和快照可用范围。全部为待实施建议，具体原文、候选改写、规则与覆盖边界见同一报告“ASD-STE100 文案审核”。本轮只审核，未改产品；历史成本说明继续关联 ISSUE-UX-20260826-da8a。
