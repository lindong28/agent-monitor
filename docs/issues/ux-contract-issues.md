# UX Contract Issues

`docs/contracts/ux-contract.md` 的演化候选。契约本身基于真实端到端观察建立、不由 agent 静默改（见 `~/.claude/references/docs-organization-protocol.md` §4.6），自由 session 发现的候选先记在这里，由用户经 `/custom:create-ux-contract` 处理。

---

## [open] ISSUE-UX-CONTRACT-20260928-4f72：已发布统计进入当前页面的时点缺少明确契约

- **Type**: expansion
- **Priority**: medium
- **Discovered**: 2026-09-28，主线程在真实 Hub Overview 页面点击 `#refresh`；约 117 秒后 DOM 今日金额仍为 `35.58`，同页 `overview?range=30d&sync=0` 在 0.463 秒返回 `40.90`。样本为一个页面、一次按钮刷新和一次同页 API 对照，不覆盖四页、冷热状态或改后表现。
- **Description**: 已 admitted 的数据可用不代表当前页面已显示它。用户选择分钟级自动更新；[ADR 4f72](https://github.com/lindong28/ai-agent-config/blob/3d61487d5386dfe9bb6ba7d26807005bbf0bd7f2/docs/adr/20260928-4f72-ttweb-progressive-freshness.md) 的可见页定时重读、按 generation 变化逐步显示、主动轮先统计后配额及来源年龄展示已实现并经用户明确批准部署到生产 Hub（远端 `287a6aa0`，来源 `69deac92`），四页有界生产验证已完成（每页一条行程，初始与后续读取各至少两次）。本条为演化候选，不直接改写 hard contract，也不宣称既有契约全部满足。
- **Post-change observation**: 主线程通过仅供 agent 验证的代理将新前端连到原生产 Hub；一次真实 Overview 刷新在 27.460 秒将今日金额 `77.70` 更新为 `81.60`，按钮当时仍刷新中，之后回到刷新终态，终态秒数未精确采集。仅覆盖 Overview 一页／一次刷新，不覆盖新后端与其他页面；与改前例子不是同输入 A/B，不报告倍数。
- **Production observation**: 新生产版本四页各完成一条行程。Overview 在 observer +38499 ms 显示新金额且 busy，+197805 ms 回 idle（按钮 +230 ms 进入 busy，包含旧轮与补排轮）。Explore 保留 `7d/codex`；Sessions 同步重读后保留 `7d/codex/101–200`；LLM Calls 保留 `7d/machine=macbook`，同 query 后续读取后仍显示该机的 23 请求，抽查前三行机器均为 macbook。后台 manual 完成时 requested=completed=1、四机尝试成功，随后自动轮恢复。各请求时延与输入范围详见 ADR 4f72；明细页仍有长等待，不以该行程覆盖宣称全页快速或全部故障行为均已验证。
- **Recommendation**（L1 → L2 成对）：候选承诺为“可见数据页自动读取已发布结果，逐机统计不等全轮结束，用户筛选与翻页不因后台读取丢失，来源年龄和失败不被刷新动作改写”。验证时在 Overview、Explore、Sessions、LLM Calls 各用真实入口保留所选 range／filters／paging，让一台机器先发布、另一台仍在运行，观察前者进入当前视图；覆盖可见停留及重新 focus／visible。另以点击时已有旧轮的场景确认后续轮被保留，统计阶段完成不把配额阶段或按钮承诺提前完成；检查失败旧值仍显示原观察时间。仅凭 API 返回新值不能认定页面承诺通过。
- **Owner / Next**: 实现、定向复核、生产部署与四页有界生产验证已完成。Hard contract 的候选采纳留待明确契约对齐；长期稳定性与完整故障矩阵是本轮未覆盖边界，不新增本轮验收待办。Sessions／LLM 全快照解析瓶颈记于 ADR 4f72，归 ai-agent-config 维护者后续性能任务，本轮不实现。

## [open] B3 的 meta 行示例形态与实现不符，且先于中文化就已不符

- **Type**: drift
- **Priority**: low
- **Discovered**: 2026-09-15，页面中文化任务中按 §4.6 跟随路径同步契约时发现。**不在那次授权范围内**（用户批准的是文案翻译与精简，不是这条 meta 行的形态），故记此处而非直接改契约。
- **Description**: B3 写「**粒度在面板副标题 meta 行可读**（形如 `<range> · <day|week|month> buckets · historical rollup`）」。实测该串从未出现过——中文化**前后**各取一次 `document.body.innerText`，`buckets · historical rollup` 命中数都是 `0`；实际渲染是中文化前 `Last 30 days · Daily`、中文化后 `近 30 天 · 按天`（`app.js` 的 `costMeta.textContent = \`${rangeSummaryLabel(...)} · ${granularityLabel(...)}\``）。所以这是一处**先于本次变更就存在**的漂移，不是中文化引入的。
    契约的**可验证主张**（粒度可读、90d→day / 1y→week / 2y·All→month）仍然成立，失配的只有括号里那个「形如」示例。
- **Recommendation**（L1 → L2 成对）：把 B3 的示例形态改写成实际渲染形态 `<range 中文名> · <按天|按周|按月>`；验证条件：range 取 `90d` / `1y` / `2y` 各加载一次 Overview，`#cost-over-time-meta` 的文本分别以「按天」「按周」「按月」结尾，且前半段等于该 range 的中文名。需用户确认取哪一侧——也可能是实现漏掉了 `historical rollup` 这段出处标注，那样就该改实现而非改契约。

## [open] Overview 在 2026-09-15 新增的三处用户可见行为尚未进契约

- **Type**: expansion
- **Priority**: medium
- **Discovered**: 2026-09-15，用户要求按读者视角评审 `http://macstudio:39001/` 并改进；改动在本机 standalone 实例 `http://127.0.0.1:39001/` 端到端观察过，hub 实例尚未部署新代码
- **Description**: 本轮按 §4.6 第 4 行（有端到端观察、缺对这次具体行为变化的显式批准）落此处，契约正文未动。三处新行为：
    1. **Spend 区的覆盖说明**（关联 B2 / G2）：任一 admitted 机器 `stale` 或 `unreachable` 时，`Spend` 标题下出现一行 `Newer usage from <machine> (data as of <time>) … is not included yet, so these totals may be lower than actual spend.`（原因不在这句里——它在机器卡片上，`stale` 可能只是代龄超过 6 小时而非刷新失败）；全部机器正常时该行隐藏、不占位。被排除（未 admitted）的机器不出现在这句里——它从未计入，coverage 分子已说明。
    2. **图表库加载失败的可见提示**（关联 A5）：`/web/vendor/chart.umd.min.js` 未加载时，每个图表框内出现 `Chart unavailable: … Run tt-web/install.sh on the serving checkout, then reload.`，不再空白。
    3. **机器卡片的失败原因**（关联 G2）：远端 export 命令非零退出时，`Refresh failed:` 后面是 exporter stderr 的最后一行（如 `Export refused: … dependency pin`、`llm_attempts.ProjectionError: Audit ledger schema is unsupported`），不再是 `Command '[…]' returned non-zero exit status N.`；stderr 为空时写 `<程序名> exited with status N`。
    5. **LLM Calls 的请求投影多了 `caller_username`**（同日跟进 gateway `c087c1c` / ledger schema 6）：`/api/llm-calls` 每条 request 带该字段（旧快照为 `null`），`/api/llm-call-filters` 的 `request_dimensions` 多 `caller_usernames`，API 接受 `caller_username` 过滤参数；页面**尚未**显示该列、也没有对应下拉。H2 的请求行字段清单与 H5 的过滤器清单需要用户对齐：要不要在页面上露出它，以及旧机器（schema 5 快照）该显示 `—` 还是隐藏。
    4. 金额格式：所有 `money()` 输出带千分位（`$46,736.41`）。契约 B2b 的示例数字（`109625.05` 等）是 API 原值不是页面显示值，不受影响；但 L2 里若有按页面文本比对金额的测试，须按新格式读。
- **Recommendation**（L1 → L2 成对）：
    - B2 追加承诺「Spend 总额未计入某台 admitted 机器的新数据时，Spend 区内给出该机与其数据截止时间」；验证条件：把一台 admitted 机器置为不可达后刷新，`#spend-coverage` 可见且点名该机与其 `generated_at`；恢复后该元素 `hidden`。
    - A5 补一条边界：「图表库不可加载时图表框显示明确提示，不空白」；验证条件：让 `/web/vendor/chart.umd.min.js` 返回 404 后加载 Overview，三个 `.chart-box` 各含一个 `.chart-empty-state` 且文字含 `chart.umd.min.js`。
    - G2 的「失败原因」明确为「远端 exporter 自己的最后一行 stderr」；验证条件：让远端 export 以非零退出并在 stderr 末行打印可辨认句子，卡片 `Refresh failed:` 后即该句、且不含 `Command '[`。

## [open] 契约的产品形态只认 Web dashboard，`tt-web network` 这条终端入口落在契约覆盖面之外

- **Type**: coverage-gap
- **Priority**: medium
- **Description**: L1 把产品形态定为「本机 localhost Web dashboard」，功能全景表按页面（`/`、`/explore`、`/sessions`、`/network`）组织，验收段 A–G 也全部以页面为单位。2026-08-07 新增的 `tt-web network` 使 `/network` 那份诊断多了一条终端入口：同一份快照、同一套结论，但呈现形态与验收判据都不是页面式的。按现行契约做一次完整 UX 测试不会覆盖到它。

    需要用户对齐的点（不由 agent 自行裁定）：
    - **产品形态**是否从「Web dashboard」扩为「Web dashboard + 一组 CLI 入口」。若扩，`tt-web rollup --check` / `machines accept` / `export` 等既有子命令是否一并进契约——它们同样是 user-observable 且此前也不在覆盖面内，只做 `network` 一条会留下同类不一致。
    - **G5「本机专属页面标明范围」**是否推广为通道无关的判据。该命令的输出已声明「仅本机」，但 G5 现行措辞锚在「页面上有可读的本机标注」，字面不覆盖终端输出。
    - **新增一条 verdict 作用域判据**：总体 verdict 只能覆盖本轮真正取得观测的维度。这不是 CLI 专属——`/network` 页面存在同一缺口（见下条），所以判据该定在契约层而非命令层。

## [open] `/network` 页面的 verdict banner 会把未取得观测的维度算作已验证

- **Type**: correctness
- **Priority**: medium
- **Description**: `verdict` 由 `ip_check.collect_all()` 按「IPv6 泄漏 / CN DNS / 风险分 ≥ 70 / 时区不一致」四路信号计算，任一未命中即落到 `low`。但**查询失败与"查过且正常"在这个计算里不可区分**：2026-08-07 实测 `proxycheck.io` 读超时，`risk` 仍返回一个 dict（`score: null`，失败文本埋在 `display` 里，`errors` 为空数组），`verdict` 照常输出 `low`，banner 显示「Low risk for Claude use」。Risk 卡片确实会显示 `not queried` 警示，但**总体结论并未声明该维度不在覆盖内**，而使用者读 banner 的用途正是"我不用再查了"。

    `tt-web network` 已按「结论作用域不超过证据作用域」处理这一情形（列出未取得观测的维度并在结论上加限定），页面尚未同步。判据若按上一条进契约，页面这一侧需一并对齐——两个入口共用同一份 `/api/network` 快照，结论口径不该分叉。

## [open] Overview 默认层级改为“当前状态优先”，旧契约仍要求历史与解释常驻

- **Type**: evolution
- **Priority**: high
- **Description**: 用户在 2026-08-26 要求复用 `cli-output-review-principles.md` 与 `human-facing-message-principles.md` 优化、简化 `http://127.0.0.1:39001/?range=30d`。本轮按 [ADR 20260826-c80f](https://github.com/lindong28/ai-agent-config/blob/3d61487d5386dfe9bb6ba7d26807005bbf0bd7f2/docs/adr/20260826-c80f-tt-web-overview-information-hierarchy.md) 实现了 L1 演化：Overview 默认直接显示 Spend 美元结果、全部在用账号配额与同步健康；`remembered` 账号统一放进默认关闭的 `Past accounts (N)`，机器名/时间戳/失败后果继续留在既有 sync details。Spend 不再常驻 Today / Week / range token 总数，Week 改为 `Since Monday 00:00 · GMT+8`，range 只在覆盖不完整或未知时显示限定语；`Cost over time` meta 改为 `Last 30 days · Daily` 一类读者语言。账号记忆、最后读数、逐账号删除、刷新、generation admission、配额百分比语义和低余量阈值均未改变。

    现行 contract 的 B1、B2、B2b、G4、G4a、G4b、G4e 仍描述 Week 完整起止、token 常驻、remembered 行常驻与旧同步摘要；本轮实现不把这些旧条款当作通过证据。后续经 `/custom:create-ux-contract` 对齐时，需要决定是接受当前演化，还是恢复其中某一类常驻信息。

    后续对 All machines details 的真实页面复查又发现，G2 要求每卡常驻 `Included in All.`、可达状态与五类时间，会让三台健康机器重复父级 `N/N machines included · Up to date`，并要求读者解析五个近义时间。按 [ADR 20260826-5153](https://github.com/lindong28/ai-agent-config/blob/3d61487d5386dfe9bb6ba7d26807005bbf0bd7f2/docs/adr/20260826-5153-tt-web-machine-status-progressive-detail.md) 提出进一步 L1 演化候选：健康态只显示机器名、本机标记、单一 `up to date`、`Data updated` 与 `History since`；刷新、联系失败、数据陈旧、未检查、从未有可用数据和 cleanup failure 按实际状态增量显示相应诊断时间与对用户的影响。它 supersedes G2 的“每卡常驻 Included in All 和五类时间”字面要求，但保留机器纳入、正交状态可分、异常影响可读的目标。

    这项候选的 L2 是：renderer fixture 分别覆盖 healthy、refreshing + unreachable + stale、recent unreachable、never excluded、cleanup failed、尚未检查的 unknown，以及 reachable / unknown 上的 terminal attempt failure；healthy 必须含 `up to date / Data updated / History since` 且不含重复纳入、可达与三个同步诊断字段；复合异常必须同时保留刷新失败、数据陈旧、相应时间和仍使用旧数据的影响；never 明确不纳入且没有可用数据；cleanup 明确数据仍可用；尚未检查的 unknown 明确仍使用上一份数据、本 server process 尚未检查，有历史联系时间时显示 `Last reached`，无真实尝试时间时不显示 `Last attempt`；terminal attempt failure 必须让父级进入 `need attention` 并自动展开，显示失败状态、真实尝试时间和原因，不得退回 `up to date` 或“尚未检查”。真实隔离入口另验证 Overview / Explore 的健康态与 1440px / 768px details；无法自然取得的异常态只记 fixture 证据，不冒充 E2E。

    首次连接尚未获准时，异常详情另提出一项 L1 文案演化候选：沿用卡片已有的 `not included` 与 `refresh failed` 状态，不再重复这些标签，也不向读者暴露 `TOFU`、machine slot、generation 或 pinning 术语；正文直接要求确认该机器的 SSH target，运行 `tt-web machines accept <machine>`，再刷新。对应 L2 同时从真实 source exception 注入 renderer fixture，断言页面保留机器名、SSH target 与逐机 accept 命令且不含上述内部术语；真实 30d 页面还需自然落到该失败终态并逐卡读到相同动作。

    **L2 候选验收读数**：

    - `1440×900`、sync details 关闭：改前 Quota 表 y=`327–902`（575px）、第一张图 y=`987`、文档高 `1670`；改后 Quota 表 y=`311–547`（236px）、第一张图 y=`632`、文档高 `1315`。默认仍有 3 条 `in_use` 行；4 条 `remembered` 行存在但可见数为 0。
    - `768×900`、sync details 关闭：改前 Quota 表 y=`621–1210`、第一张图 y=`1295`、文档高 `2312`；改后分别为 y=`617–905`、y=`990`、文档高 `2007`，document-wide horizontal overflow = 0。
    - 展开 `Past accounts (4)` 后 4 条历史行与 4 个删除按钮都可见；删除一条后摘要当场变为 `(3)`，删除最后一条后 toggle、history body 与 remembered 行同时消失。只有 remembered、没有 in-use 的 fixture 仍显示折叠组，不落“不可用”空态。
    - 7d 显示 `Last 7d cost` + `Last 7 days · Daily` 且 coverage 副文案隐藏；All 显示 `All-history cost` + `Since 2026-04-21` + `All history · Monthly`。Explore 真实隔离入口显示 `3/3 machines included · Up to date`，逐机详情仍有 3 台机器及其时间戳/后果。
    - 历史 toggle 的可见更新延迟为 cold `34.4ms` / hot `37.5ms`，阴性对照为 `no_change`；冷导航观测到 LCP `68ms`（只记读数，不设阈值）。`page-acceptance` 的隐藏 toggle 阴性注入 exit 1，正常入口的 3 张 Spend 卡、3 条 in-use 行、1 个 history toggle 与 1 张主图全部进入过视口并 exit 0。
    - 同口径 `page-repetition --min-chars 12`：改前整行重复字面占已统计正文 `11.7%`、最坏重复 6 次；改后为 `2.02%`、最坏重复 2 次（`window reset`）。
