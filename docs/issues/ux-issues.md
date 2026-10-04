# tt-web UX Issues

基于真实产品入口观察、但不在发现当轮修复范围内的用户体验问题。条目只记录真实页面读数，不用源码推断冒充端到端证据。

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

## [open] ISSUE-UX-20260915-a1c2 — 同步面板一出问题就把首屏整个占掉，最该看的数字被推到一屏之下

- **Type**: information-architecture
- **Discovered**: 2026-09-15，真实 hub 页 `http://macstudio:39001/`，1440×900 / 1024×900 / 720×900 三档
- **Observed**: 4 台机器里 2 台刷新失败时面板自动展开（设计如此），高 524px（1024 宽）/ 492px（720 宽）；Spend 标题落到 y=601 / 651，Quota 标题 y=743 / 1017，而当天唯一需要立刻行动的事实——Codex Pro 账号 7d 已用 100%、`almost out`——那一行顶边在 y=1060（1440 宽）/ 1152 / 1426，三档都在首屏之外。四张机器卡片各带一行 `v2 full-history project-independent usage totals` / `v2 full-history hybrid totals (208 legacy date/agent/model buckets)`，这是 totals 口径的内部标识（对应 `generation_totals_basis`），读者无法据它做任何事；`History since 2026-09-01` 与下方 `Data updated` / `Last reached` / `Last attempt` 四个时间戳并排，要先分辨哪个才是"数据多旧"。
- **Impact**: 读者打开页面是来看"今天花了多少、配额还剩多少"；一出故障，首屏变成运维诊断面板，回答那两个问题的区域全部需要滚动。
- **Reproduce**: 任一远端 export 失败时打开 Overview，不滚动，读 Spend 与 Quota。
- **Recommended direction**: 三个方向都改变既有契约 G1/G2 的版面承诺，需用户裁决而非 agent 自定：① 面板仍在顶部但只展开出问题的那几张卡片、健康机器折叠成一行；② 面板整体移到 Spend / Quota 之下，顶部只留 `N/M machines included · 2 machines need attention` 一行摘要（点击跳转）；③ 保留位置，但把 totals 口径行移进 hover/title。本轮已把"哪几台没算进来"直接写到 Spend 区（见 ux-contract-issues 2026-09-15 条），部分缓解①的动机，但 Quota 那行仍在一屏之下。
- **2026-09-15 更新**：③ 已由文案中文化那轮**以更强的方式**执行——`v2 full-history …` 口径行不是移进 hover，而是整块删除（用户裁定）。因此上面 **Observed** 里"四张机器卡片各带一行 `v2 full-history …`"这段描述已不再成立，卡片也随之变矮；但首屏挤占这个本体问题未解，①②仍待裁决。

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

## [open] ISSUE-UX-20260826-8ade — Overview 的 Range 控件未说明只影响页面的一部分

- **Type**: information-architecture
- **Discovered**: 2026-08-26
- **Priority**: medium
- **Observed**: 在真实 `http://127.0.0.1:39001/?range=30d` 依次切换 30d、7d 与 All 后，第三张 Spend 卡和 Cost over time 随 Range 改变；Today、Week、Quota、Top projects this week 与 Model mix this month 保持各自固定窗口。页面只有一个位于顶栏的 Range 控件，没有说明它控制哪些区域。7d 状态下，`Model mix this month` 的 Explore 链接仍携带 `range=7d`。
- **Impact**: 控件位置暗示全页作用域，读者需要反复切换并比较标题才能知道哪些数字受它影响。
- **Reproduce**: 打开 30d Overview，切换到 7d 和 All，逐项比较 Spend、Quota、三个 chart 标题及 Explore 链接。
- **Recommended direction**: 在 Range 控件附近明确其作用域，或把受控区域与固定窗口区域在结构上分开；Explore 链接的 range 语义需与各卡标题共同核对，不能只改说明文字。

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

## [open] ISSUE-UX-20260826-5453 — 768px 下 Quota 的机器归属与更新时间默认不可见

- **Type**: responsive-usability
- **Discovered**: 2026-08-26
- **Priority**: medium
- **Observed**: 在真实页面 768×900 下，整页 `scrollWidth=clientWidth=768`，但 Quota 表容器 `clientWidth=736`、`scrollWidth=1040`、`scrollLeft=0`。默认视口只能看到部分 Machines，Updated 完全在右侧；容器可横向滚动，但没有持续可见的提示。
- **Impact**: 窄屏回访用户无法一眼同时确认 quota、机器归属与读数新鲜度，这三者正是判断当前账号是否可用的同一决策单位。
- **Reproduce**: 以 768×900 打开 Overview，滚到 Quota Used，在不横向滚动时读取 Machines 与 Updated。
- **Recommended direction**: 重新评估窄屏列优先级或给出明确的受控滚动提示；必须保留 provider、账号、quota、机器归属与更新时间之间的可关联性，不能仅隐藏列。
