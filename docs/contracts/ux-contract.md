# agent-monitor — UX 验收规格 (ux-contract)

> 面向使用者的**验收规格**：使用者拿它判断「这些都过了我放心继续用」。只描述 user-observable 行为，不含实现细节 / 内部状态 / 后端架构 / 待办路线。下游 `execute-ux-contract` 按本规格展开端到端测试。
>
> 基线：基于运行中真实产品（`http://127.0.0.1:39001`，真实本机 token 用量数据，跨度约 2026-04-21 至今）的实际观察撰写。
>
> 2026-09-09 新增的三机 hub 条款是 accepted target contract；当前代码尚未部署到三台真实机器，因而这些新增条款还没有生产观察读数。
>
> 2026-09-21 根据用户后续反馈移除 FROM 与明细表，增加含首尾起止日期与组合排名；本轮条款据隔离数据库及真实 HTTP／Chromium 操作同步，生产部署另验。
>
> **写判据的硬规则**：每条判据必须指出一对**读数确实不同**的状态，并且那对状态在基线机上真的可分——否则它在「成立」与「不成立」两种情况下给出同一读数，判不了任何事。数据跨度只到 2026-04-21 是本文件最常见的陷阱来源：凡窗口起点早于该日的 range（`6m`/`1y`/`2y`/`All`）取值全部相同，拿它们互验"随 range 变化"必然假失败。**基线机上凑不出可分的一对时，把这件事写进判据本身**，而不是留下一条注定失败的检查。这条规则是被反复咬出来的：2026-08-21 一轮里，配额表的验收 fixture（两个窗口都填 96）、B2b 的初版判据①、以及既有的 B3「长范围数据跨度严格大于短范围」被查出同属此类——其中 B2b① 正是写在修掉前一个实例的那次改动里，而修 B2b① 的那一版又漏掉了 B3 六对里的四对、其中 `1y`↔`2y` 会给出**假通过**。三个实例现已全部修完（记账见仓根 `docs/issues/general.md`）。**这条规则被连续咬中三次，说明它不是提醒而是必查项**：写完一条判据，先在基线机上把它声称能区分的那两个状态各跑一遍，读数真的不同才算数。

---

## L1 — 产品全貌 + 使用方式

**产品形态**：默认仍可作为本机 localhost Web dashboard 运行；三机 hub 模式显式安装后，macbook、macmini、macstudio 的 `agent-monitor open` 均进入 MacStudio 上固定的 `http://macstudio:39001`。中心页面由 Python stdlib HTTP server 在 `0.0.0.0:39001` 提供。

**产品类型**：功能型数据可视化 dashboard（非游戏 / 非 AIGC 生成类）→ 价值来自「数字正确 + 能查到 + 能钻取」。无 registry 列出的 domain 专属验收（见末尾 domain 段）。

**使用者**：单用户、本机所有者本人。复盘自己在 Claude Code + Codex 上的 token 用量与成本。

**使用方式 / 使用者拿它做什么**：
- 短期盯当下消耗（今天 / 本周花了多少、配额已用多少）。
- 长期看趋势与构成（按天/周/月看成本曲线、哪些项目/模型长期占成本）——最长回看 2 年（数据从启用起向前累积）。
- **一次看全部常用开发机的合计**，而不是逐台打开各自的 dashboard；也能按机器拆开比较。
- 查单个 session 的明细（成本、token、消息、turn 级展开）。
- 顺带做网络环境诊断（VPN/代理/时区是否影响 Claude 使用）。

**多机范围（决定页面上"All"是什么）**：hub 模式固定由 MacStudio 汇聚 macbook、macmini、macstudio。三台机器各自持续采集本地 archive，MacStudio 每 120 秒独立拉取每个来源的完整快照；gateway 仍在各机本地承接调用。使用者看到的是：`All` 覆盖了哪几台、每台的数据有多新、哪台联系不上。首次接入一台远端仍需显式确认机器绑定。

机器清单每台一行，可在行首加 `#` 或 `//` 停用；保存后后续拉取跳过该机，页面更新后隐藏其卡片、在用机器标签及统计贡献。取消注释恢复已有历史，无需重启或 Git commit；必须保留一台 `self: true`。支持缩进和尾随逗号，不支持行尾注释。验证条件见 G1a。

**聚合日边界（影响所有 KPI 数字）**：Today / Week / Month 与 `Nd` 范围的**日、周、月边界固定按 `Asia/Shanghai`**，不随任何一台机器的系统时区变化——否则同一时刻的同一条用量会在不同机器上落进不同日期，跨机合计就不成立。**绝对时间戳的显示**仍按本机系统时区渲染（见 A1），两者是各自独立的约定。

**功能集合（user-observable 能力全景）**：

| 页面 | 入口 | 能力 |
|---|---|---|
| 总览 | `/` | **机器状态条**（coverage N/M + 逐台卡片）；KPI 卡片（今日成本、本周成本、当前 range 的成本）与**逐账号配额列表**（Claude 5h/7d、Codex 7d）；「成本趋势」时间图；「本周会话目录成本」「本月模型构成」侧面板。**除配额外均为全机合计** |
| 透视 | `/explore` | 机器状态条；SELECT 选择指标；WHERE 提供预设范围或起止日期与四类过滤；GROUP BY 组合 Agent 类型／会话目录／模型／机器及可选天／周／月；预设、时间趋势与组合排名，无分组时显示总量 |
| 会话 | `/sessions` | **全机完整已采历史**：session 列表（列：agent/project/model/起始/cost/tokens/messages）；排序下拉（Time / Cost / Tokens / Duration）；行展开看 turn 级明细 |
| LLM 调用 | `/llm-calls` | **全机、仅各机本地 LLM Gateway**：Machine 过滤；按 request 汇总调用结果，按 attempt 追踪实际 provider/model、fallback、credential source、usage、latency、成本与定价依据；request 与 attempt 各自独立分页；从请求进入右侧完整诊断并关闭返回列表（H9） |
| 网络 | `/network` | **仅本机**：五块诊断卡：本机（内网 IP / IPv6 泄漏 / DNS+地域）、公网（IP/位置/运营商/时区）、风险（proxycheck 风险分+type、ip-api hosting/marked-proxy、stopforumspam 垃圾评分+报告次数、本地 shell 代理环境变量）、时区（本机 vs 公网时区匹配）、结论（逐条结论 + verdict 规则说明）；总体 verdict banner + Refresh |

**全局控件**：顶部 range 下拉 `7d / 30d / 90d / 6m / 1y / 2y / 全部`；Refresh 按钮；五页导航。切到另一页后顶部 range 仍是之前所选。Refresh 立即要求中心更新；若点击前已有一轮在跑，中心在其后补排一轮，不能只认旧轮为本次完成。首页按钮在受理后恢复，后台进度继续可见；需要等待完成的消费者只等本次请求，不等后来的队列。

**跨页约定**：所有绝对时间戳按本机当前系统时区渲染并带 UTC-offset 标签（如 `GMT+8`），跟随系统时区设置、刷新即更新，不随浏览器陈旧时区漂移。时刻本身按 `zh-CN` 格式化（`2026/9/15 21:22:59 GMT+8`），不随浏览器 locale 变动。

**界面语言**（用户 2026-09-15 裁定）：五页的**界面用语**——导航、页标题、面板标题、表头、按钮、状态短语与全部说明段落——一律简体中文；**取值型标识符保留原文**：机器名、账号、模型名、项目路径、provider / route id，以及 `success` / `failed` / `usd_per_request` 这类枚举值（它们同时是过滤器取值、URL 参数与 gateway 账本里的原词，译了就与排查时 grep 的词对不上）。**判据**：逐页取 `document.body.innerText`，其中长度 ≥18 的纯拉丁字符行必须全部可归入上述取值类，不得出现英文句子；同时配额表头恰为 B2 那七个中文列名。

**会话目录口径**（用户 2026-09-21 裁定）：总览、透视和会话列表保留启动目录归类；可显示该目录的 Git 仓库标识，同一会话的跨项目工作不拆分费用。**判据**：三页对应标题、过滤器、分组和表头显示“会话目录”，并能读到上述说明；相同 URL 的 `project` 值、历史归类与统计数值不因改名变化。LLM 调用页仍使用 Gateway 的独立项目归属。

**成本口径（user-observable）**：
- Codex 成本按已识别模型的费率**估算**，不代表实际账单；GLM-5.1/5.2 无精确 LiteLLM key 时由 bundled GLM-5 family 定价**推算**；推算项标 `推算`；未知模型定价显示 `—`，不显示 `0`。
- 28 天 recompute 窗口内，各 `(day, agent, project, model)` bucket 分别从当前可读源更新。日志中已有精确成本的条目保持记录值；没有精确成本的条目使用 agent-monitor 当时可用的定价数据推算。某个 bucket 的源缺失，或其 token、消息、条目计数低于此前显示值时，该 bucket 的成本贡献保持最后可信值；其他 bucket 继续更新，因此页面或 project 聚合成本仍可能变化。窗口外的既有 bucket 保持冻结。长范围图上有 footnote 说明这三段口径。
- 数据从启用起累积，最早可见日 = 最早采集日（当前约 2026-04-21）；长范围超出该日时图上有覆盖提示。
- 新 archive authority 从首次采集后的下一完整日开始；此后的 token、消息与条目统计不会因原始日志被裁剪、归档或项目目录删除而减少。此前历史是 legacy best effort，沿用旧 floor / shrink / freeze 口径。Sessions 与 gateway ledger 的已采全历史不受该日界裁剪。

**范围 / 约束 / 假设**：
- 单用户本机 dashboard。G4e 的移除是明确的破坏性操作：经逐账号确认后，不可恢复地删除该条 server-side remembered record。
- archive 只保留统计所需 `UsageEntry`，不保留 prompt/response。LLM Calls 只覆盖经各机本地 gateway 的 API 调用；当前已知接入项目是 `philo-prompt`，其它尚未列名接入的项目不在覆盖范围内。
- 假设本机有 Claude Code / Codex 的 JSONL 日志；无数据时相应视图为空。
- 时区随系统设置；改系统时区后刷新即更新。

---

## 验收侧重（横切 L1 + L2）

**与用户对齐结果：均匀覆盖——所有维度同等严格**，不分优先级。功能正确性、数据正确性与跨视图一致性、时间窗口/标签/时区清晰度、信息架构与 range 一致性、视觉与可读性、响应式/缩放韧性、空/边界状态，全部按高标准验收。

- 对 L1 影响：产品描述各区域均衡铺开，不刻意加深某一块。
- 对 L2 影响：每个维度都给具体可观测判定条件，不留「基本校验即可」的低标准维度。
- 对 L3（execute-ux-contract）影响：测试资源均衡分配到各维度——本段只列方向，不替 execute 决策具体测法。

---

## L2 — 用户视角 verify（独立于内部实现）

> 形式：操作序列 + 操作后观测点 + 通过判据。均覆盖 happy + 边界。数值类一律 **expected-vs-actual**（不接受「有输出即过」）。

### A. 跨页 / 全局

- **A1 时区：显示随系统、聚合固定上海**。两条独立、须分别验：
  - **绝对时间戳的显示**随本机系统时区，带 UTC-offset 标签（如 `GMT+8`）。改系统时区并刷新后标签随之变化。与 `/network` Timezone 卡报告的 CLI 时区**按 UTC-offset 比较一致**（IANA 名可不同，按 offset 判）。
  - **用量聚合的日 / 周 / 月边界固定 `Asia/Shanghai`**，**不**随系统时区变化。**判定方法**：冻结同一份数据，在两个不同的系统时区下各跑一次——① Today / Week / Month 与 `Nd` 的**数值必须一致**；② 同一页上的 generation 时间、配额 reset 时间等绝对时间戳的**显示值与 offset 必须不同**。缺了②，「聚合固定、显示随系统」这条承诺就没有会失败的验证。
- **A2 range 切换连贯**：切换顶部 range（7d→2y 等），受 range 驱动的视图（总览「成本趋势」、透视趋势与排名、LLM 调用 request/attempt 切片）随之更新；切到另一页后顶部 range 仍是之前所选。**已知固定窗口面板**（总览「本周会话目录成本」「本月模型构成」、会话列表保留窗口）不随 range 变内容是**预期**，但其 →Explore 钻取链接须带当前 range。**判定方法**：把顶部 range 设为非默认值（如 2y），点该面板 →Explore 链接后，地址栏 URL 含 `range=2y` 且 Explore 顶部 range 下拉显示 2y（非回落 30d）；再进入 LLM Calls，地址栏与顶部 range 均保持 2y。 Explore 自定义日期仅用于透视：其导航链接保留 start/end；进入其他页面恢复 30d，不向其他页传递 custom。
- **A3 五页可达且无报错**：五页均正常渲染，无 JS error banner / 服务端错误页 / 空白。
- **A4 数据真实非占位**：所有成本/ token 数字来自真实用量，非示例/ mock；Codex 与 GLM-5.1/5.2 推算项标 `推算`，未知定价显示 `—` 而非 `0`。
- **A5 空 / 无数据状态（边界）**：某视图无对应数据时，显示明确占位（`—` / 「无数据」/ 查询失败提示），**不显示 `0`、不报 JS error、不空白**（如网络页某诊断段查询失败显示「查询失败」提示而非整页崩）。

### B. Overview `/`

- **B1 Week cost 窗口可读**：Week cost 卡片副标题显式显示窗口起止 + 时区，语义为「**`Asia/Shanghai` 本周一 00:00** → 此刻」（形如 `周一 <本周周一日期> 00:00:00 <tz-offset> → <now> <tz-offset>`，按跨页约定的 `zh-CN` 渲染）；**判据是起始 = `Asia/Shanghai` 本周周一 00:00（非滚动 7 天、非周日起、非本机时区周一），不依赖具体日期**（不同周跑结果不同，对照规则非对照固定日期）。
- **B2 Today / Week 卡片与配额列表**：Today cost = 今日累计（`Asia/Shanghai` 日）；Week cost = 本周至今（同上周边界）；**两者均为全部 admitted 机器的合计**。配额不是卡片而是**逐账号的表格**（行集合 = 在用账号 + remembered 账号，行数随两者之和变化，不是固定三张卡）：列恰为 `服务商 | 套餐 | 账号 | 5h 已用 | 7d 已用 | 机器 | 更新于` 且顺序一致（**短窗口在前**：5h 决定"这几分钟还能不能干活"，先读），每行给出已用百分比、同向量条、距重置的**时长**（完整时间戳在 hover）、在用机器与更新新鲜度；Claude 行含 5h 与 7d，**Codex 行的 5h 单元格给出明确的不适用标记**（Codex 不上报该窗口）。**判据**：①表头恰为上述七列且顺序一致；②只有 remembered 账号、没有任何在用账号时，配额区仍有行而不是“不可用”空态；③同一行的 5h 与 7d 取值不同时，两个数字分别落在与其窗口同名的列下（**取值相同的行验不出列序**）。**配额是唯一不合计的 KPI**——见 G4。
- **B2b Range cost 卡片**：Spend 区第三张卡的成本 = 右上角 range 选择器所选窗口的合计，**卡片标题自带窗口名**（`近 <range> 成本`，`All` 时为 `全部历史成本`），读者不必回看工具栏即知它报的是哪段。**判据**：①在**数据覆盖确实不同的两个 range 之间**切换后，标题与数字同时变化——取值对须先实测确认可分，**不可用 `6m`/`1y`/`2y`/`All` 互验**：rollup 起点（本机 2026-04-21）晚于这四者的窗口起点，四档的 `range.cost_usd` 与 `tokens` 完全相同，此时数字不变是正确行为而非缺陷。本机可分的取值对如 `7d`↔`30d`↔`90d`。**验的是"相不相等"，不是具体数值**——下面这组是 2026-08-21 的当日快照，只用来示意可分性：四长档同为 `109625.05`，`7d`/`30d`/`90d` 为 `12764.34` / `54220.33` / `106658.54`。**别拿它们当期望值比对**：rollup 会重算，绝对值随时可能变——本文件写下这组数字的同一天里就已经变过一次（`109573.57` → `109625.05`）。**变化的时机不可预期**：它跟着 rollup 的重算走、不是连续漂移，独立复核在相隔约一小时的两次采样里读到的是逐字相同的值。所以数字不变**不能**推出同步坏了，数字变了也不代表页面错了——判据是相等关系，不是取值。②副标题按 `rollup_coverage` **实际说了什么**分三态，判据不是"这个字段在不在"——产出方 `_rollup_coverage` 即使一无所知也照样返回一个完整 dict：`partial_before_range` 为真且给出了 `earliest_date` 时附累积起始日（本机 `6m` 及更长窗口如此）；明确为假且给出了 `earliest_date` 时只报 token 数（本机 `90d` 及更短）；**其余一律报「覆盖范围未知」**——包含 `earliest_date` 为 null（rollup 库为空，此时数字是 `$0.00 / 0 tokens`，「一条数据没采到」绝不能与「你从没花过钱」同形）、字段整个缺失、以及取值不是布尔的情形。**第三态在基线机上验不了，但原因逐子态不同、别一概说成"触发不了"**：`earliest_date` 为 null 这一支**真实可达**——全新主机首次 rollup 之前，或 admitted generation 指向空 / 无 `daily_rollup` 表的 DB，产品就会进入该态；只是在这台正在服务的机器上现场制造它要临时清空 rollup 状态，代价不划算。而"字段整个缺失"与"取值非布尔"确实触发不了（`server.py` 无条件输出该 dict）。三者统一由 `tests/test_web_static.py` 的 `emptyRollup` / `noCoverage` / `partialNoDate` 三个用例覆盖。按本文件头部的硬规则，此处写明各自为什么验不了，而不是留一条注定判不了的检查——也不要因为这句话就以为第一支做不到，它做得到。使标题的窗口声明不超出数据实际覆盖——B4 的同一事实在此卡自身可读，不依赖一屏之外的面板提示。
- **B3 成本趋势面板**：标题为「成本趋势」；按所选 range 取数、随 range 变化——**跨度严格递增只在数据覆盖确实不同的相邻档之间成立**，本机是 `7d` ⊂ `30d` ⊂ `90d` ⊂ `6m`（2026-08-21 实测起始桶 `2026-08-15` / `2026-07-23` / `2026-05-24` / `2026-04-20`）。**`6m`、`1y`、`2y`、`All` 四档在本机跨度两两全部相同**——rollup 起点 2026-04-21 晚于这四者的窗口起点，取的是同一段数据（同日实测四档 `range.cost_usd` 同为 `109625.05`）——**这六对全都验不出递增**。其中 `1y`↔`2y`（及 `6m`↔`2y`、`6m`↔`All`、`1y`↔`All`）尤其危险：跨粒度时**不能拿桶标签比跨度**，`1y` 的首桶是周标签 `2026-04-20`、`2y` 的是月标签 `2026-04`，后者字面更早，照桶标签比会读出"跨度增大了"而**判 PASS**——这是假通过，比假失败更坏，因为没有人会去查它。要验递增只用前一组四档，且比的是覆盖的**数据起止**、不是桶标签；**曲线为全部 admitted 机器的合计**；桶按 `Asia/Shanghai` 日 / 周 / 月切分；长范围自动按周/月聚合（≤90d 天 / ≤1y 周 / >1y 月；`6m` 同 `1y` 走周，`All` 走月）——**粒度在面板副标题 meta 行可读**（形如 `<range> · <day|week|month> buckets · historical rollup`），选 90d 显示 day、1y 显示 week、2y 与 All 显示 month；footnote 准确说明成本口径：28 天 recompute 窗口内，各 `(day, agent, project, model)` bucket 分别从当前可读源更新。日志中已有精确成本的条目保持记录值；没有精确成本的条目使用 agent-monitor 当时可用的定价数据推算。某个 bucket 的源缺失，或其 token、消息、条目计数低于此前显示值时，该 bucket 的成本贡献保持最后可信值；其他 bucket 继续更新，因此页面或 project 聚合成本仍可能变化。窗口外的既有 bucket 保持冻结。
- **B4 长范围覆盖提示（边界）**：当所选 range 起点早于最早采集日时，面板显示覆盖提示（如「历史自 <最早日> 起累积；更早未采集」）；range 在数据范围内（如 7d）时不显示该提示，避免误导。**`All`（无界窗口）下覆盖提示恒显示**（其起点恒早于最早采集日）。
- **B5 侧面板**：「本周会话目录成本」按本周成本排序的会话目录；「本月模型构成」本月模型 token 构成；两者的**周 / 月边界同为 `Asia/Shanghai`、数据为全机合计**；两者 →Explore 链接带当前 range。

### C. Explore `/explore`

- **C0 图表刷新连续性（同样适用于 Overview）**：已有图表收到新数据时原位、无动画更新；首次创建、画布替换或图表类型改变仍创建匹配图表。**判据**：后台更新与时间范围切换后，未换类型的图表实例保持相同，数据与标签反映新响应，更新模式为 `none`；不因此停止数据轮询或改变采集周期。
- **C1 时间粒度**：未指定分组的初始链接按范围默认选择时间单位：至 90d 为天、6m／1y 为周、2y／All 为月。GROUP BY 可显式改选天／周／月或不按时间；指定后的分组保持在 URL 中，改变 WHERE 时间范围不改写显式分组。
- **C2 透视正确**：无分组时显示筛选后的总量。有时间分组时按日期顺序显示趋势，其余联合维度组成系列；选任意非时间维度时另显示跨时间合计的横条排名，无时间时只显示排名。排名按当前指标降序，未知成本显示 — 并排在已知值之后，真实零值显示 0。
- **C3 历史全维分组**：四类维度可任意组合，再加一个时间单位。排名保留范围内每个真实联合组合，不折叠为 Other；只按 Agent 类型时跨会话目录汇总，加选会话目录后分别显示每个 Agent 类型与会话目录的组合。改变 SELECT 指标后数值和排序对应变化。
- **C4 过滤与日期**：WHERE 提供 Agent 类型／会话目录／模型／机器四个单选下拉，各以「全部 …」为首项；选值后趋势与排名只包含其数据，过滤须在可分样本上改变数值。时间范围可选预设或「自定义日期」，后者提供起始和终止日期，按 Asia/Shanghai 日桶含首尾两日；同一天有效，缺失或倒置显示提示并隐藏旧图。日期同时约束图和过滤候选；缩短终止日期须移除范围外数据和候选。过滤与日期写入 URL，重开链接恢复。深链过滤值不在候选中仍保持选中，无数据时明确显示「无数据」，不伪造 0。
- **C5 边界**：趋势图最多 8 个系列：token／计数将剩余系列汇为 Other，成本只显示已知成本最高的 8 个并说明省略数量。排名保留全部组合和完整标签，不受系列限制；悬浮或聚焦排名项可查看该组合全部已返回时间桶与精确值，长列表可滚动到最后一期。排名区域局部纵向滚动、长标签换行，整页不横向溢出。
- **C6 预设按钮**：点每日成本／会话目录成本／模型 token／Agent × 会话目录／缓存读取任一预设后，SELECT 指标与 GROUP BY 复选框、时间单位切到对应组合，趋势与排名随之刷新。
- **C7 原始日志部分失源时保真**：只在受控副本（不得使用生产库）中，在同一天、同一 agent、同一 model 下准备 project A 与 B，并确保每个 project 只对应一个被观察的 bucket。令 A 的部分或全部原始日志失源后刷新 Overview / Explore：A bucket 显示的 token、消息与条目计数不得低于刷新前，B bucket 的新增计数必须出现；A bucket 的当前源计数低于此前显示值时，该 bucket 的成本贡献保持最后可信值。

### D. Sessions `/sessions`

- **D1 保留说明准确**：standalone 读取本机已留存统计；hub 读取各台已准入机器的留存统计，并显示来源观察时间与尚未采集状态。源日志清理后，已采集 session 明细仍可见；早于首次采集的缺失日志不能恢复。日汇总的完整保留边界按各机 archive authority 显示，不把 legacy 历史改称完整。
- **D2 列表与排序**：列出 session（列：agent/project/model/起始时间/cost/tokens/messages）；排序下拉提供 **Time / Cost / Tokens / Duration** 四项，选定后列表按该维排序且符合排序语义。注：Duration 可排序但**不作为可见列**展示（按起始/结束时间差计算）——验 Duration 排序时以相邻行的时间跨度推断顺序，或视为已知的「可排序但无对应列」观察点。
- **D3 行展开**：点行展开 turn 级明细（每 turn 时间/模型/in/out/cost）。
- **D4 range 行为**：range 筛选已留存事件的真实时间，不因选 2y 就声称已有两年历史。删除已采集源并再次采集后，旧 session 仍在对应 range；hub 离线来源仍显示上次成功记录及原观察时间。

### E. Network `/network`

- **E1 诊断渲染**：显示**五块卡** + 总体 verdict banner，正常环境无 error banner：
  - **Local**：LAN IP、IPv6 是否泄漏、DNS 服务器 + 地域（是否 CN resolver）。
  - **Public**：公网 IP、位置、ISP、Org、时区。
  - **Risk**：proxycheck 风险分 + type、ip-api marked-proxy / hosting 标记、stopforumspam 垃圾评分 + 报告次数 + 最近报告、本地 shell 代理环境变量。
  - **Timezone**：CLI 时区 vs 公网时区是否匹配。
  - **Conclusion**：逐条结论 + verdict 规则说明文本。
- **E1c Risk 卡部分未查询态（边界）**：当 proxycheck / stopforumspam 未查询或不可用、但其余诊断正常渲染时——Risk 卡对应行显示 `not queried` / `—`（**非 `0`、非 JS 崩**），其余四块卡与 verdict 照常渲染，且 verdict **忽略缺失的风险分**（按 E1b 规则，risk 分缺失不触发 HIGH）。这是区别于 E1 全present、A5 单段失败、E4 整页降级的第三态。
- **E1b verdict banner 状态**：banner 显示四态之一，且与 Conclusion 卡逐条结论一致——`HIGH`（文案「High risk for Claude use」，命中 IPv6 泄漏 / CN DNS / 风险分≥70 / 时区不符 任一）、`PROXY-IN-USE`（文案「Claude usable, but proxy is in use」，Risk 卡「Marked proxy (ip-api / proxycheck)」行显示 yes 且无 HIGH 信号）、`LOW`（文案「Low risk for Claude use」，无上述信号）、`UNKNOWN`（文案「Network status unknown」，检测不可用）。判定：banner 文案与 verdict 规则、Conclusion 结论三者自洽。**注**：本地 shell 代理环境变量仅在 Risk 卡「Proxy envs」展示，**不参与 verdict**（设置了 HTTP_PROXY 不等于 PROXY-IN-USE）。
- **E2 Refresh**：Refresh 触发强制重新检测（默认 60s 缓存），结果可更新。
- **E3 本轮保留**：本轮历史功能改动**不影响** Network 页行为（保留承诺）。
- **E4 整页降级 / 不可用（边界）**：当 `ip-check` 整体不可用（未安装 / 退出非 0 / 超时 / 返回非法 JSON）时——verdict banner 显示 `UNKNOWN` + 原因行；五块卡均显示 unavailable 提示；并出现对应入口：未安装 → 顶部 error 面板带「ip-check 未安装」+ Docs 链接；检测失败 → error 面板 + Retry 按钮。**全程不 JS 崩、不空白页**（区别于 A5 的单段失败，这是整页降级态）。

### F. 视觉与缩放（均匀覆盖要求，跨五页）

- **F1 可读性（需人工判断）**：五页默认窗口下的具体可读检查点——Overview KPI 数值不被裁切；Explore 长项目路径标签截断带省略号（不撑破）；Sessions 表头不换行错位；LLM Calls 的长 model/profile/credential 标签截断但可通过 title 读取完整值、成本与 usage 不被裁切；图例/坐标轴标签可辨识。这些为具体锚点；**整体美学层级 / 视觉噪声为单一 holistic「需人工判断」检查点**（视觉质量无法机械断言，刻意保留为人工 gate 而非逐点拆）。
- **F2 缩放 / 响应式韧性**：浏览器缩放至 125% / 150%（或等价 viewport）、并将窗口收窄至 ~1024px / ~720px 宽时，五页无元素重叠 / 裁切 / 破版；表格过宽时出现**受控横向滚动**而非撑破整页布局（Explore 排名标签换行、局部纵向滚动，见 C3/C5；LLM Calls 的 request 与 attempt 表各自局部横向滚动）。**判据含整页不横向滚动**：任一档位下 `document.documentElement.scrollWidth` 不得大于 `clientWidth`——过滤器下拉的宽度取决于容器而非最长选项文本，一条很长的项目路径或 credential source 不得把整页推宽。

### G. 跨机全局视图

> 这一段的概念是本产品独有的（`All` 的范围、数据新鲜度、机器可达性、配额来源）。判据是**使用者不查文档就能看懂**：页面本身要能回答"现在算了哪几台、数据多旧、有没有哪台没连上、配额是谁的、哪些页面只是本机"。

- **G1 `All` 的范围可见**：Overview 与 Explore 顶部显示 `coverage N/M` 与**逐台机器卡片**；另有一句明说当前 `All` 包含哪几台（形如 `All currently includes dgx0023, macbook, macmini`）。**判据**：`N` = 实际计入的机器数，`M` = 声明的机器数；被排除的机器不出现在那句话里，且其数据**不计入**任何 KPI / 图 / 表——把一台置为不可计入后，`coverage` 分子精确减一，且逐 metric 的 `All` 数值恰好减去该机上一次的贡献（**expected-vs-actual，不接受"看起来变小了"**）。
- **G2 每台机器的状态可读**：每张卡片显示机器名、是否计入（「未纳入」标签的有无）、可达状态、最近一次同步 / 尝试 / 成功联系的时间、该机数据的生成时间与**起始日**（`历史自 <date>`）。source detail 显示单一 `snapshot.db` 的 digest/admission/retention；旧 exporter 没有 detail 时显示“未采集”，不显示空值或成功。**判据**：状态词能区分四种处境——正常、正在同步、联系不上但仍用上一份数据、从未成功过因而不计入；"联系不上"与"数据陈旧"是两件事，可同时成立且都要显示。离线但有旧代时旧数据仍计入并保留原生成时间，不能因本轮失败把时间改新。
- **G1a 按行停用与恢复机器**：对一台已有用量的远端，注释其配置行并刷新页面后，coverage 的分子与分母各减一，该机不再出现在机器卡片和 Quota 在用机器清单，汇总精确减去其贡献；下一轮不再拉取它。取消注释后，不重启服务即恢复该机已有历史及原汇总。已发出的拉取可在内部完成，不使已停用卡片重新出现。端到端样例观察（2026-09-09，隔离配置与 generation，经实际 Overview）：注释 macstudio 后 `4/4 → 3/3`、Today cost `$15.00 → $10.50`，取消注释恢复 `4/4` 与 `$15.00`；这不是生产机器的用量读数。
- **G3 Machine 过滤与拆分**：机器下拉以「全部机器」为首项，选定某台后趋势与排名只含该台，URL 出现 `machine=<name>`，深链保持选中；GROUP BY 勾选机器后，排名标签包含机器。**判据**：某台切片值 + 其余各台切片值 = `All` 同桶值（逐 metric 相等）。
- **G4 配额按账号分组、不求和**：配额的显示单位是**账号**，不是 provider、也不是机器——额度按账号计量，同一账号在多台机器上的读数是同一个计数器的多次观测，不同账号是互不相干的池子。每个账号一行：账号标识、该 provider 的窗口百分比与重置时间、**在用机器清单**、观测新鲜度；在用账号取其 admitted 机器中最近一次更新的那份**原值**。配额区还包含曾观测到、当前不在任何 admitted 机器上登录的 remembered 账号；这类账号显示最后一次被观测到的原值，页面不对其做任何推算。**判据**：①同一在用账号的多台机器合并为一行，行内值等于最新那台的原值且**不等于**各台之和，机器清单含全部贡献机器；②不同账号各占一行，**不合并、不互相顶替**——把一台切到另一个账号后，页面出现两行而不是一行变值；③被排除的机器既不贡献数值也不出现在机器清单里；④该 provider 既无在用账号也无 remembered 账号时显示不可用 + 原因，**不退回只用本机**；⑤一个账号从所有机器登出后仍留在配额区，数值等于登出前最后一次观测的原值，Machines 明确显示 `—`，并排在全部在用账号之后；⑥页面不出现任何对其当前额度的推断性表述；⑦冻结读数含 reset 原值时，其单元格 hover/title 仍给出原本声明的绝对 reset 时刻；⑧冻结读数不含 reset 原值时，单元格明确显示「重置时间未知」并附观测时刻，不把未知解释成窗口仍在运行或已经重置。
- **G4g Codex 同工作区的不同邮箱独立保留**：Codex 按 `(account_id, account_label)` 区分成员，邮箱缺失不归入其他具名成员；Claude 仍按 account_id。**判据**：①同 ID、不同邮箱的在用与历史记录分别成行，各保留自身读数；②移除一个历史邮箱不会移除同 ID、同观测时间的另一邮箱，也不因另一邮箱仍在用而拒绝；③刷新页面后仍只剩未移除的成员。
- **G4h 配额采集失败按账号读数影响呈现**：逐机原因保留在默认折叠的「采集详情」。当前展示来源健康、时间有效且不超过原 6 小时阈值、有该 provider 可显示数值时，其他机器失败不在账号行报警；受影响的账号保留旧值和原时间并标「更新延迟」，没有数值时标「暂不可用」，可点击既有刷新入口重试。**判据**：同一账号与读数保持不变，把失败机器从非展示来源切为实际展示来源，账号行须从正常变成延迟；展开详情能读到机器与原因；恢复成功后的重新渲染须移除延迟及失败详情，不能留下旧提示。不同账号的错误不得相互污染。2026-09-29 以隔离浏览器、真实页面 renderer 与受控数据观察上述展示／展开／恢复，未代表生产已经部署。
- **G4b 配额答的是“用了多少”，且低余量仍必须自己跳出来**：数字与量条都直接编码 provider 上报的**已用**比例——列头写明方向（`5h 已用` / `7d 已用`），数字越大量条越长。显示值 >75% 时出现 `余量偏低`，>90% 时出现 `即将用尽`，且颜色不是唯一载体。该警示要求对 `remembered` 且**其自身声明的窗口 reset 时间已过、或 reset 时间未知**的行不适用：该行保留数值与量条，但不发现在式警示，这是经用户拍板的取舍而非缺陷。**判据**：①页面值等于上游 `*_used_pct` 的取整值，数字与量条同向；②`remembered` 行的冻结 reset 在未来时警示标记照常出现；③同一行的冻结 reset 在过去时警示标记消失而数值与量条不变；④同一行的冻结 reset 未知时警示标记消失、数值与量条不变，并显示 `重置时间未知` 与观测时刻；⑤`in_use` 行的警示行为在未来、过去与未知 reset 情形下都不变；⑥页面不含任何教读者把数值反过来读的说明句。
- **G4c 同名窗口与账号标识跨行同列**：配额区是**表格**，一个账号一行、一个窗口一列——列对齐由表格结构保证，不靠 CSS 槽位模拟。某 provider 没有的窗口，其单元格给出**明确的不适用标记**（而非留空，留空与“数据缺失”不可分辨），且**不得**把 `7d` 滑进 `5h` 的位置。账号标识本身拆为 `Provider | Plan | Account` 三个表格列，跨行同列左对齐；plan 缺失给明确占位而非留空。**判据**：①取各行 `7d` 数值的左边界，两两相等；②取各行 Provider / Plan / Account 三列内容左边界，同列两两相等（实测偏差 0px）；③`account_plan` 为空的行，Plan 列呈现明确占位符；④对齐不依赖任何固定宽度的 CSS 槽位。
- **G4f Plan 有两个来源，页面只在它们冲突时开口**：一行的 plan 有两个独立来源——**配额读数自己报的**（与该行的百分比同一个事件）与**该读数所在机器的凭据文件**。Plan 列显示的是前者，缺它才回退后者。三态各自的呈现是：**两者都有且不同** → 单元格内出现可见文字，明说 `plan 不一致` 并同时命名两边的来源与取值（不依赖 hover、不依赖颜色）；**两者都有且相同** → 无标记；**少于两个可比来源**（该机已登出、凭据不可读、API key 模式、remembered 行、导出端早于本字段——后两者其实是**零个**可用原值，不是一个）→ 同样无标记——页面对它不作任何主张，这与"比较过且相同"在**版面上**有意合并（否则每个 Claude 行都会永久挂一条"无法比对"，那是噪声不是信息），但**在 payload 里必须仍可区分**。**判据**：①两来源不同的行，其可见文本含 `不一致` 且含两边取值**的显示标签**（`pro` 渲染为 `Pro`、`prolite` 渲染为 `Pro Lite`——判据按屏上文字判，不按原值判），`title` 之外可读；②两来源相同的行无该标记；③少于两个可比来源的行无该标记——**一个来源**（已登出 / 凭据不可读 / API key）与**零个来源**（remembered 行 / 导出端早于本字段）两种都要各验一行；④payload 同时给出两个来源的原值，使 `少于两个可比来源` 与 `两个来源相同` 可由消费者分辨——只比较"显示值 vs 凭据值"分辨不出它们，因为回退会让显示值就是凭据值；⑤`account_state` 非 `known` 的行不出现该标记；⑥读数侧无 plan 的 provider（当前是 Claude）永不出现该标记。
- **G4d 未标注机器不按机器数撑开首屏**：≥2 台机器无账号标记时折叠为**一行**汇总并点名待更新的机器，展开后才逐台显示；该折叠行由一个 `<button>` 承载（`aria-expanded` 随开合变化），可键盘聚焦，且带可见的展开标记。**判据**：①3 台机器全部未标注时，配额区行数为「具名账号数 + 每 provider 一行」而非「provider × 机器数」；②折叠行**逐状态点名机器**（待更新的哪几台、未登录的哪几台），两种补救不同、不得混成一句笼统话；③**折叠不得吞掉警示**——组内任一窗口的使用率进入 G4b 的警示档时，折叠行本身带出该档的文字标记与最高使用率、对应窗口及机器名，闭合状态下即可见。
- **G4a 归属的已知风险须可见**：一台机器的读数按**该机当前登录账号**归属（ADR-024 的 waive）。换号后、该机产生新账号的第一条读数之前，页面会把上一个账号的数字挂在新账号名下。`in_use` 行显示观测新鲜度，陈旧读数以 stale pill 可见为陈旧；`remembered` 行不显示这种 live stale pill，而以“已登出 + 最后观测时刻”标明历史身份，并可读地写明“最后观测值，不代表当前状态”。**判据**：①`remembered` 行不出现 live stale pill，但明确显示最后观测时刻与不代表当前状态的文字，且不依赖颜色单独承载；②`in_use` 行的 stale pill 行为不变；③机器可读的 `stale` 类名在两类行上都保留；④导出端未标注账号的机器（agent-monitor 版本过旧）**不得并入任何具名账号，其读数也不得与另一台未标注机器的读数合并成一个数**——未知不是一个可以共享的账号。版面上把多台未标注机器**折叠成一个可展开的入口**不违反本条（见 G4d），前提是折叠行点名涉及哪几台、展开后逐台各自成行、且折叠不吞掉 G4b 要求的低余量警示。
- **G4e 账号记忆的边界与移除**：配额区记住的是**曾在某次 admitted 快照中被观测为已登录**的账号——观测下限由各机器的导出节奏决定，在两次导出之间登录又登出的账号不保证被记住。记忆自本功能上线起累积，**上线前已登出的账号不会出现**（无历史数据源可回填）。记录无自动过期，只由用户在页面上手动移除；移除**不可恢复**，且只对已不在任何机器使用的账号开放。记忆**跨服务重启存续**。**判据**：①`remembered` 行提供移除入口，`in_use` 行不提供；②移除前的确认文案点名该账号、它的最后观测时刻与最后记录的读数，取消不删除；③对在用账号调用移除端点被拒绝并说明仍在用它的机器；④该 provider 存在任何 `unstamped` entry 时移除被拒，说明无法确认归属、点名相关机器并提示更新其 agent-monitor；`signed_out` 不阻塞删除；⑤被移除的账号若日后重新登录，会作为在用账号重新出现；⑥重启服务后记忆中的账号仍在、值不变；⑦对一个已不在任何机器使用、且其 provider 不存在任何 `unstamped` entry 的 remembered 账号，用户确认后移除必须成功，当前表格不再显示该行；⑧随后刷新页面、离开后重新进入页面、以及重启服务后重新进入页面，该行均不再出现。
- **G5 页面范围标明**：`/sessions` 与 `/llm-calls` 明示覆盖 admitted 机器，`/network` 明示仅为打开页面的中心机器。LLM Calls 还明示只覆盖经各机本地 LLM Gateway 发出的 API 调用，不把 Claude Code / Codex 自身调用或未接入项目算作全量。**判据**：页面上可直接读出三者的数据源和机器范围；当前 registry 只有 `philo-prompt` 时，页面不得写“全部项目已覆盖”。
- **G6 首页受理与后台完成分离**：打开页面先渲染已有数据并显示后台同步。首页点击 Refresh 只等待受理与当前数据读取，明确显示“已受理”，不暗示来源已更新；每机统计发布后立即开始该机配额查询，不等待其它机器，配额更新保留统计采集时间。**判据**：同步仍在进行时按钮已恢复且进度可见；新代发布后同页自动读取；统计或配额失败都能显示原因并保留旧值，恢复后同页能更新；第一轮中发生的新事件与新点击由补排轮覆盖，按本次请求完成时检查事件，不用按钮恢复作为后台完成证据。需要等待的消费者在本次请求完成后返回，即使后续轮仍运行。

### H. LLM Calls `/llm-calls`

- **H1 范围与快照语义可读**：页面明示数据来自各机本地 LLM Gateway ledger，不含 Claude Code / Codex 自身调用，也不声称覆盖尚未注册的 API 项目；一次刷新里的 KPI、request 表与 attempt 表来自同一组 admitted machine snapshots，并显示 `as of`。本地 request/attempt id 仅在机器内唯一，页面 identity、排序和 cursor 均含 machine。翻到下一页表示读取当时的 latest-first 结果，不承诺跨多次请求冻结同一历史快照。
- **H2 request-first 汇总**：首屏先显示 request 总数，并把成功、拒绝、失败、中断或未知、仍在进行中的 request 分开计数，再显示独立的 attempt-time 成本分组与 request 表；request 行至少可读 session、project、logical model、outcome、attempt 数与时间，不生成 request-level 成本字段。一个 request 经 fallback 产生多个 attempt 时，request 只计一次，attempt 表保留每次尝试。**判据**：构造上述五类 outcome 共存的 fixture，首屏各项计数与 API `request_summary` 一致，`interrupted_or_unknown_requests` 不得被省略或并入 failed。
- **H3 attempt 追溯**：attempt 表可读 request/attempt identity、provider、API profile、非敏感 credential source kind / ref、funding source（公司/个人与 paid/subscription 合一）、route、actual model、outcome、latency、usage、cost 与 pricing basis；同一行还要把 API profile 的 current-label source 与 pricing state / basis 组合呈现，避免把当前 registry 装饰误读为调用时快照、或把 pricing state 与 basis 拆散。Standalone 的 profile label source 分成三态：`current_registry` 表示当前 registry 给出了 label；`current_registry_missing_profile` 表示 registry 有效但已无该历史 profile；`registry_unreadable` 表示当前 registry 无法读取、解析或解释。后两态都保留稳定 profile id 和 ledger audit，页面分别显示「当前标签不可用」与「当前 registry 不可读」，不把装饰源故障升级成历史账本故障。不显示 credential value。长标签的完整值可通过 title 读取，未知 actual model/cost/usage 不伪装成空字符串或 `0`；ledger 未报告 latency 时显示 `未报告`，不把它推断成某一种恢复路径。**判据**：使用同一 ledger fixture 做两次 observation；valid registry observation 中让 current label 与历史 profile 缺席共现，unreadable registry observation 中让同一稳定 profile id 读成 `registry_unreadable`，分别核对 API row/filter option 的 source 与页面文字，并确认后两态仍显示 profile id 与其余 audit 字段。 Hub 额外使用 `source_registry_not_collected`，表示此次来源快照未收集 profile label；保留来源机的稳定 profile id 与审计行，不用中心 registry 冒充来源机标签。判据另含三机快照中的该状态与实际页面文字。
- **H4 双时间窗过滤**：顶部 range 同时形成 request-time 与 attempt-time 窗口。未启用 attempt-level filter 时，request 切片只按 request 自身时间判断；启用 provider/profile/credential/actual-model/attempt-outcome 等 attempt-level filter 时，request 自身须在 request-time 窗口内，且至少一个匹配 child attempt 须在 attempt-time 窗口内。页面可读地说明当前 request 数是“有匹配 attempt 的 request 集合”，避免把任意历史 child 当成当前窗口命中。Request 维度的 option 集合覆盖窗内请求及窗内 attempt 的父请求，不能因为父请求在窗外就清掉仍有效的机器、项目或 session 深链。
- **H5 过滤与深链**：过滤器覆盖 machine、session、project、logical model、request outcome，以及 provider、API profile、credential source kind / ref、route、actual model、attempt outcome 与 cost basis；`/api/llm-call-filters` 是当前 range 的 option authority。选择后 URL 保留所选值，刷新与分享有效深链后选择仍生效；切换 range 后，若 active URL 值不在新的 option 集合中，页面须在构造 calls query 前同时清空控件与 URL，不生成 synthetic option，也不让 ghost filter 在后续扩大 range 时复活；清空过滤器会从 URL 移除对应参数。过滤结果须改变 request/attempt 行集合，不能只改控件外观。**判据**：分别从有效 machine 深链、其它有效深链与带 range-invalid machine/project/profile 的深链加载；前两者保留 URL、控件和值，后者在首次 calls query 前清掉对应控件与 URL，且 query 不含失效值。
- **H6 成本口径不混算**：成本只按 attempt-time 口径统计，request 汇总仍按 request-time；成本按 `exact / estimated / unknown / subscription` 分开呈现，并继续按 currency、funding category、pricing basis 与 authority 区分；公司与个人 subscription 的行归属可区分，subtotal 仍同属 subscription 类别，不把不同币种相加。`unknown` 显示未知而非零；subscription 显示“未报告逐调用收费”而非 `$0`。已知 exact/estimated 项显示金额与依据；不生成 request-level 成本。
- **H7 独立分页**：request 与 attempt 各自有 latest-first、默认 50 行的独立上一页/下一页控制；翻 request 页不改变 attempt 页，反之亦然。最后一页后 Next 禁用，返回上一页恢复前一页；过滤或 range 改变后两张表都回到第一页。
- **H8 来源状态完整**：加载时显示 busy/loading；存在兼容 ledger 时显示 data；某机没有 ledger 时该来源显示明确 missing；ledger 不可读、schema 不兼容、数据无效或 ledger 路径是 dangling symlink 时显示明确 error，不把故障降级成“暂无调用”；旧 exporter 没有 source detail 时显示“未采集”。**判据**：三机分别构造 compatible、missing、旧 exporter/invalid 状态；兼容机数据继续可见，另外两机各自保留明确状态，页面停止 busy 且不把部分覆盖写成全量。
- **H9 请求详情与返回**：点击逻辑请求打开右侧诊断，显示所选 machine/project/request 身份、结果、完整尝试链、usage/latency/cost 与详情自身快照及各来源时刻；成本未知仍显示未知。Escape 关闭详情并返回当前列表。**判据**：从真实多机入口选择一条请求，逐项核对详情身份与所选行，读取其完整尝试及来源时刻，再按 Escape 确认面板关闭、列表仍在。2026-10-06 `25697a3` 的生产样本为 `macstudio` / `aihot` 的一个成功请求、一个 `deepseek-v4.1-flash` attempt、1734ms/2309 token/unknown 成本；该读数不外推生产重试链、attempt-parent 入口或导出。

---

## domain 专属验收

L1 判定为**功能型** data-viz dashboard，非 `~/.claude/skills/product-ux-workflows/references/domain-registry.md` 列出的特殊 domain（游戏等）→ **无 domain 专属验收段**。数据可视化的「能钻取到根因 / 跨视图数据一致」已并入 L2（C2/C3 钻取与一致性、A2/A4 跨视图）。

---

## 已知限制（非 fail 项，使用者知情）

- archive authority 之前的 legacy 区域仍只有“统计不减少”的旧保证，不保证累计完整。同一天、同一 agent、同一 project+model bucket 内，如果一个 session 的原始日志消失，而另一个 session 继续增长并超过消失前页面显示的合计，该 bucket 可能低于两个 session 的真实合计；旧 checker 也可能不再报告 shrink。新 archive authority 区域以 retained event 重建，不适用这一 legacy 限制。
- **UX-003** 原列于此——Explore project 长范围过宽表／标签；2026-09-21 后续反馈移除明细表，改为完整组合排名、标签换行与局部纵向滚动，见 C3/C5。
- **同名项目跨机相加，无同一仓库的证明**。项目标签先按已知 home 前缀归一（macOS 的 `/Users/me/foo` 与 Linux 的 `/home/me/foo` 同为 `~/foo`），随后**标签相同即合并**。带 git remote 的项目以仓库身份记录，跨机合并可靠；非 git 目录的合并只依据路径字面，没有"这是同一份工作"的证明。按机器拆开看（G3）可确认任一行的构成。
