# tt-web Issues — 已关闭

`resolved` / `wontfix` 条目从各 domain 文件整条移入（格式不改写），只 grep 查史、不通读。open 条目仍在 `../general.md` / `../ux-issues.md` / `../ux-contract-issues.md`。

---

## [resolved] ISSUE-UX-20260826-8ade — Overview 的 Range 控件未说明只影响页面的一部分

- **Type**: information-architecture
- **Discovered**: 2026-08-26
- **Priority**: medium
- **Observed**: 在真实 `http://127.0.0.1:39001/?range=30d` 依次切换 30d、7d 与 All 后，第三张 Spend 卡和 Cost over time 随 Range 改变；Today、Week、Quota、Top projects this week 与 Model mix this month 保持各自固定窗口。页面只有一个位于顶栏的 Range 控件，没有说明它控制哪些区域。7d 状态下，`Model mix this month` 的 Explore 链接仍携带 `range=7d`。
- **Impact**: 控件位置暗示全页作用域，读者需要反复切换并比较标题才能知道哪些数字受它影响。
- **Reproduce**: 打开 30d Overview，切换到 7d 和 All，逐项比较 Spend、Quota、三个 chart 标题及 Explore 链接。
- **Recommended direction**: 在 Range 控件附近明确其作用域，或把受控区域与固定窗口区域在结构上分开；Explore 链接的 range 语义需与各卡标题共同核对，不能只改说明文字。

- **2026-10-12 复验**：真实 Hub 总览默认 `range=30d` 下，本周目录图的透视链接为 `/explore?x=project&group=none&metric=cost&range=30d`，本月模型图同样传 `range=30d`；实际打开目录链接后仍为 30 天。该行为符合现有契约 A2/B5，因此这里是产品上下文与契约演化问题，不是实现违反契约。建议后续明确“查看同一窗口明细”与“沿用全局范围分析”的区别；本轮只审查，未改变契约。

- **Resolved 2026-10-12**: 首页明确顶部范围只影响区间成本、趋势与周期比较；两张固定图按实际周/月日期进入 custom 透视。真实 7d 首页的本月模型图进入 10月1—12日，再到同窗口会话。部署为 MacStudio `f3c9301`；来源、真实交互与读数见 [运维记录](../../operations/services.md#2026-10-12-web-workflow-deployment)。

## [resolved] ISSUE-UX-20261012-6bc4 — 网络诊断未标明被检测的服务端机器

- **Observed**: 2026-10-12，隔离浏览器访问真实 Hub `/network`，页面写“仅本机，这些诊断只针对本机，不跨机合并”，并列出“本机时区”；页面没有服务端机器名或 Hub 身份。此轮只读已有报告，没有强制刷新网络检测。
- **Expected / Impact**: 契约 G5 要求明示检测的是打开页面的中心机器。多机用户从笔记本访问 Hub 时，容易把服务端出口、DNS 与时区当成浏览器所在机器的结果。
- **Reproduce**: 从另一台设备访问 Hub 的网络页，尝试仅从正文确认检测目标；对照总览中可见的机器身份。
- **Recommendation / Priority**: 优先明确“检测目标：<服务端机器>（Hub）”，保留检测时间；支持选择其他机器属于另一个功能建议，本条不要求新增探测能力。优先级中。
- **Evidence / Owner**: 本轮真实页面文本与截图位于本地 `~/.local/share/agent-reports/agent-monitor-ui-review-20261012-worker/evidence/network.{json,png}`；源码佐证 `web/network.html` 与 `web/app.js:renderNetwork`。后续 agent-monitor UI 维护任务处理，本轮未修复。

- **Resolved 2026-10-12**: 真实网络页显示“检测目标：服务端主机 macstudio”，并明确不检测浏览器设备、不跨机合并。部署为 MacStudio `f3c9301`；来源、真实交互与读数见 [运维记录](../../operations/services.md#2026-10-12-web-workflow-deployment)。

## [resolved] ISSUE-UX-20261012-7ad2 — LLM 调用加载后丢失 Gateway 覆盖范围说明

- **Observed**: 2026-10-12，真实 Hub `/llm-calls` 健康且有记录时，范围标签为“全部已纳入机器”，说明只有快照来源与“Claude Code 与 Codex 的会话用量在总览和透视页”；没有明示本页仅覆盖经各机 LLM Gateway 的调用。
- **Expected / Impact**: 契约 G5/H1 明确要求说明 Gateway 数据源以及未接入项目不在覆盖范围。新用户可能将“全部已纳入机器”理解为这些机器上的全部 LLM 请求。
- **Reproduce**: 打开真实调用页，等待请求表加载完，阅读顶部范围说明；不要以 HTML 初始占位中的“Gateway 调用”作为最终可见证据。
- **Source**: `web/llm-calls.html` 初始 Gateway 标签被 `web/llm-calls.js:setTerminal` 替换成机器范围；全部来源可用且有请求时，状态标题也不含 Gateway。
- **Recommendation / Priority**: 机器覆盖和调用来源分别保留说明，明确未接入 Gateway 的请求不在本页；无需扩大采集范围。优先级中。
- **Evidence / Owner**: 本轮真实页面记录位于本地 `~/.local/share/agent-reports/agent-monitor-ui-review-20261012-worker/evidence/llm-calls.{json,png}`。后续 agent-monitor UI 维护任务处理，本轮未修复。

- **Resolved 2026-10-12**: 真实调用列表加载后显示“Gateway 调用 · 全部已纳入机器”，保留不含 Claude Code/Codex 全部会话的限定。部署为 MacStudio `f3c9301`；来源、真实交互与读数见 [运维记录](../../operations/services.md#2026-10-12-web-workflow-deployment)。

## [resolved] ISSUE-PRODUCT-20261012-38f1：产品场景与 Web UI 的下一步改进候选

- **Scope / Status**: 2026-10-12 对照 README 的三类目标用户与七个场景，读取当前源码并使用真实 Hub 五个主页面。本条为建议清单，不是新增承诺、既有合同违约或已批准实施的计划；本轮不改产品行为。
- **Version / Evidence**: 取证时本地主线 `0447ab3d141d99588f0ac23dcb574d1fde8f488b`；在线四份 HTML（index/explore/sessions/network）与三份 JS（app/pivot/llm-calls）逐字节匹配该 checkout；该读数不证明全部后端代码一致。浏览器记录保存在本地 `~/.local/share/agent-reports/agent-monitor-ui-review-20261012-worker/`，包含真实交互报告；原始记录可能含个人账号、项目或网络信息，不应直接用作开源演示材料。

| 候选 | 本轮依据与边界 | 建议下一动作 |
| --- | --- | --- |
| 连通成本归因与会话调查 | 透视有目录／模型／机器分组和排行，但排行没有到会话的链接；会话列表有相应过滤，详情及带筛选返回可用。目前需重新组装查询；源码 `pivot.js:renderRanking` 也只生成展示行 | 优先设计透视结果到会话列表的带范围与维度跳转。只传语义相同的维度；不把 Claude/Codex 源日志 ID 冒充 Gateway 请求关联 |
| 首次使用的接入说明 | 五页导航直接进入指标／数据；机器配置、采集来源和 Gateway 前置条件主要在 README 与配置文件。当前实测为已有数据实例，不是干净安装，不能据此声称新用户安装失败 | 面向开源采用者，先提供页面内可发现的接入状态和使用说明，区分未配置、未采集、无匹配、来源失败；是否建设设置向导另定，不默认增加复杂配置 UI |
| 成本复盘的同周期比较 | 总览有固定今日／本周与范围趋势，透视支持日期、指标和维度；本轮查阅的总览／透视控件及实现未提供上一等长周期增减比较 | 作为后续功能候选，先明确两个周期的覆盖率与价格口径，再考虑成本、token、调用量的并列比较；不能把部分采集变化当作消费变化 |
| 多账号的快速判断 | 实测已有服务商过滤、搜索、历史账号、零匹配／清空，以及失败详情；能力存在。首页还需在多条账号读数间人工比较 | 后续评估按剩余额度、重置时间、读数新鲜度排序，或提供需要关注的摘要；摘要不替代逐账号原始读数，不自动登录或发送 |
| 调用筛选选项的等待 | 真实点击获取筛选选项后，一次 20 秒等待内未完成，后续短观测仍显示正在加载；请求详情另一路径能打开。未取得完成耗时、请求终态或根因，不能称筛选永久不可用 | 后续调用页性能任务以同一真实过滤入口补取完成事件与服务端耗时，沿用已有按需加载设计；不因为等待就改为每次打开页面全量预载 |

- **Related / Disposition**: 窗口跳转错位复验补入 `ux-issues.md` 的 `ISSUE-UX-20260826-8ade`（符合当前 A2/B5、需演化）；网络目标身份和调用来源说明分别记 `ISSUE-UX-20261012-6bc4`、`ISSUE-UX-20261012-7ad2`。建议实施顺序为范围说明 → 跨页追溯与开源接入说明 → 复盘与账号判断增强。具体新增功能由产品负责人确认后进入实施，本轮仅记录评估。
- **Coverage limits**: 独立桌面浏览器、真实部署、五主页面；深入配额搜索／历史、透视筛选、会话详情返回与一条成功请求诊断。未执行真实账号登录、发送、取消、删除、主动采集刷新、全量导出；未验证全新安装、移动端、失败／多次重试请求、长期轮询、全部筛选组合或所有机型。无产品修复或部署。

- **2026-10-12 实施进度（38f1）**：用户已授权处理建议，私树实现范围说明、固定窗口钻取、事件维度会话追溯、只读接入页、完整日比较与配额排序。隔离浏览器在 1440×1000 / 390×844 检查布局，并走通图卡→透视→会话→详情返回；实际 Hub 部署与完整选项终态待本轮后续验证，尚不关闭本条。141 项定向测试覆盖两机、两模型会话、空/未知/零/混合成本、5h/7d 排序、正常/失败来源以及重复与失效选项请求；不代表完整安装或账号操作验收。

- **Resolved 2026-10-12**: 已实现并部署范围说明、日期钻取、事件过滤会话追溯、只读接入页、等长完整周期比较和配额排序。真实跨机详情传输发现截断后补 gzip 协商，同一 6348 条记录由 3195995 bytes 压到216141 bytes，浏览器约3.09秒显示并返回原筛选；未裁剪记录。MacBook 当前采集成功，旧锁超时未复现；完整筛选可完成但仍约30秒，成本另转 ISSUE-CALLS-20261012-5f26。用户确认的 UI 能力已交付；不外推干净安装、真实账号动作或服务长期稳定性。

## [resolved] ISSUE-TTWEB-20260928-72ab：项目归属阻断文案容易被理解为用量未留存

- **Type**: misleading-status
- **Priority**: medium
- **Discovered**: 2026-09-28，MacMini 41 个 source-path blocker 的只读调查；详见 [运维调查记录](../../operations/services.md)。
- **Description**: `rollup_identity.py` 旧文案笼统称“new usage is not written”。本次核对 60,450 条事件在归档与 Hub 快照保留且摘要一致；暂停的是这些路径的项目归属聚合，项目无关用量仍参与处理。提示未区分归属未决与统计事件未写入。该读数不证明全部历史总额完整，也不能将缺项目行的挂牌估算叫作损失。
- **Recommendation**: 后续状态文案修复须说明项目归属聚合受影响、哪些用量仍保留，并按真实恢复能力给动作；不得承诺 strict pin 命令能修复 `pin_candidate=NULL` 的这批路径。以实际 blocked 路径核对提示与项目／非项目读数。
- **Owner / Status**: ai-agent-config 维护者；本轮只记录，未改代码或 CLI 文案，不随明细读取优化解除 blocker。恢复能力、归属映射与权威日前历史政策另需相应范围授权。
- **Resolved**: 2026-09-28，`72491193` 已修复 `rollup_identity.py` 的范围文案并新增受支持的 reviewed 恢复入口。随后获批在 MacMini `996e09c96f4e150e7d935da61e53fe365a0eb513` 实际应用 41 条身份；apply 前后 archive／rollup SHA 相同，常规投影 46 桶增加 21,863 条，38,587 条仍冻结。Hub blocked=0、主动刷新 requested=completed=1；真实 Explore 已显示审定 SJTU 项目及估算成本 USD 3008.1725。上方 Owner/Status 保留发现时记录；本条现已关闭，不宣称全部历史重建。证据与边界见[本轮运维记录](../../operations/services.md)，原件包括 `macmini-production-final.json`、`sync-after-apply-latest.json`、`production-project-browser.json`。

## [resolved] ISSUE-FLEET-20260915-f2b9：各机器的 llm-gateway checkout 与 tt-web 的 `gateway-runtime.sha256` pin 漂移，export 被拒

- **Type**: bug
- **Priority**: high
- **Discovered**: 2026-09-15，hub 页 `macbook` 刷新失败；本机 `tt-web export --version` 直接给出 `Export refused: independent Gateway runtime does not match the exporter dependency pin`（exit 2）。macbook 的 `~/research/llm-gateway` 在 `c087c1c`（2026-09-14 19:04），比 pin 所对应的版本新；macstudio / macmini 仍匹配 pin，所以只有 macbook 被拒。
- **Description**: pin 的设计是"依赖更新须经审查后再改 pin"，但没有任何机制在 gateway 更新时提醒同步 tt-web，也没有机制阻止只更新一台机器的 gateway；结果是 hub 上该机数据陈旧，而在本轮修复前页面只显示 `exit status 2`。
- **Notes**: 处置要用户定：① 审查 `c087c1c` 起的 gateway 改动并更新 pin，然后把三台的 gateway checkout 拉到同一版本；② 把 macbook 的 gateway 回退到 pin 对应版本。已在 `docs/references/known-risks.md` 记为会复发的环境风险（第 3 条）。
    **2026-09-15 补充读数**（用户选了 ①，动手前实测）：四台机器当时跑着三个 gateway 版本——macstudio / macmini `610c13e`（= pin）、tencent `8b4d7de`（生产 gateway，9/14 经 bundle 部署，ledger schema 5）、macbook `c087c1c`。`c087c1c` 把 ledger schema 从 5 升到 6（新增 `caller_username`），tt-web 在 macbook 上跑测试因此有 6 个基线失败（`test_gateway_v5_reader` ×2 "invalid gateway row fields"、`test_statistics_snapshot` 6≠5、`test_llm_attempts` 公开投影字段表、`test_aggregators` sqlite 站点普查、`test_exporter` pin）——所以 ① 不是改一个哈希。
- **Resolved**: 2026-09-15。tt-web 侧 `dbe1edc`（schema 6 字段集、pin `23397f2f…`、四个测试改 6、过期普查项删除）；机器侧按「先 reader 后 writer」：三台 gateway checkout 经 bundle ff 到 `c087c1c` → tt-web 更新（macstudio 部署副本 `main-20260915-dbe1edc`、macmini ff 到 `dbe1edc`、tencent 以部署 commit `a831a34` 覆盖 `tt-web/` 与 `claude/statusline-usage.py`）→ 三台 gateway 各跑 `install.sh` 重启，`status.sh` 均 `Gateway ready: true`，账本 `schema_meta` 均为 6。**验证**：hub 强制刷新一轮后四台全部 `reachable`、`stale=false`，`/api/llm-calls?range=7d` 合并 macbook v5 + 其余 v6 快照共 4804 条请求；Today cost 由漏算时的 `$52.77` 变为 `$680.50`。macbook 自己的 gateway 服务未重启（仍写 v5，reader 兼容），由用户决定。根因（gateway 更新与 pin 无联动）未改，仍靠 `known-risks.md` 第 3 条的检测与顺序。

## [resolved] ISSUE-UX-SYNC-20261006-b52c：调用页新增诊断与导出待部署后同步真实 UX 契约

- **Discovered**: 2026-10-06，ADR `20261006-d83e` 的本地实现与定向验证完成。
- **Priority**: medium
- **Owner**: 本次主线程已取得发布/部署许可并部署 `25697a3`；真实 E2E 执行者继续提供导出文件读数，文档 writer 仅同步已获真实观察的行为。
- **Pending**: 真实 7d 页面已显示 50 行/409,355 请求，`macstudio` / `aihot` 一条请求的完整单次尝试、未知成本、四来源时刻及 Escape 返回已观察；attempt-parent 已解析回同一 aihot 父请求和完整单次尝试，导出待补丁后文件复验。隔离 fixture 的两台机器、三个项目、108 请求/110 尝试及窄屏/SPA 结果见 ADR `20261006-d83e`，不冒充生产全路径或性能验收。
- **Progress**: 项目 `aihot` 筛选后为 369,757 匹配请求；当前 range 只有 macmini/macstudio 机器选项，macbook 失效值按 H5 清除。已核验原契约 writer 登记的工作树不存在、其进程当前 cwd 在 ai-agent-config，monitor 主树契约无 WIP；本次获授权 writer 因此已将真实请求入口行为同步到 H9，H1–H8 保留。
- **Completion**: 从真实多机 `/llm-calls` 补齐全匹配 JSON 导出的文件复验终态，保留各来源时间与缺失/未知/零差异，再按已验证范围同步 H10 及必要问题记录。当前不把在飞导出写成通过，也不修改 `ux-contract-issues.md`。

2026-10-06 生产导出取得失败终态：HTTP 200、618,330,927 bytes、252,832ms，但实际下载文件只有 4 bytes 的 `null`。前端 `apiJSON` 吞掉 `response.json()` 失败后返回 null，导出再序列化该值并显示成功；解析失败的底层原因尚未证明，不将 V8 字符串限额写成已确认根因。这是本次新增 HIGH，导出验收与 H10 同步暂不完成。

修复由本次 UI 单元负责：本地导出成功路径已改为直接下载 `response.blob()`，不解析或重新序列化整个 JSON；错误 HTTP、Blob 读取失败保留明确错误并恢复按钮。新增红测试在修复前实际读到 `null` 下载，修复后交互测试 7 项通过，覆盖响应 Blob 原样使用、HTTP/读取失败、旧查询及 SPA/新导出迟到响应。定向复核已通过，补丁 `0b85ade` 已发布，MacStudio 部署进行中，真实文件复验仍待完成，不称生产故障已修复；后端大导出等待继续归另一个后端 session。

- **Resolved**: 2026-10-06，修复 `0b85ade` 已部署 MacStudio（PID15184）；请求/attempt-parent 两入口与首次请求 Escape 返回已取得真实读数，H9 已同步。真实 aihot/7d requests JSON 下载618,330,927 bytes，`jq --stream`完整解析369,757 items，与页面matching一致且超过50行；projects仅aihot、machines仅macstudio，kind=requests、format=json，SHA256 `f3a86647d327c661745b0626c12fd4f0550a4bbcce1dee8da3146fcf89831316`。H10仅据本次生产requests导出建立，attempts导出仍只有隔离fixture覆盖。本条关闭的是新增诊断/requests导出与对应契约同步，以上Pending/Progress为处理期间历史，后端等待、既有断言与导航问题不随此关闭。文件复验材料为本机 `monitor-calls-phase42-20261006/production-export-file.json` 和 `production-export-download.json`，关键读数已保存在本条与运维文档。

## [resolved] ISSUE-CALLS-20261006-6d4a：调用页真实 Hub 的长等待与来源快照故障仍需后端处理

- **Discovered**: 2026-10-06，调用页 UI 改造前的真实入口观察。
- **Priority**: medium
- **Owner**: 用户指定的另一个 agent-monitor 后端优化/断言修复 session；本单元记录、不修复。
- **Attribution**: 基线独立、非边界。
- **Evidence**: 四个已纳入来源、498,194 请求/469,818 尝试；一次 `/api/llm-calls-page` 浏览器资源耗时 45,390ms，后续一次 fetch 失败；macstudio 来源显示 unsupported gateway snapshot，观测时间仍旧。此单次读数不表示长期平均或由 UI 变更引起，来源故障与既有 `ISSUE-EXPORT-20260915-e3a1` 的版本兼容边界相关，尚未证明同一根因。
- **Next**: 在真实 Hub 入口核对服务版本、admitted snapshot 身份及失败来源，分解读取等待后复测；新详情/导出使用既有完整 audit reader，本次不宣称获得 Gateway indexed UI 的性能保证。调用页布局和隔离 fixture 验证不能替代此项。

2026-10-06 发布 `25697a3` 并重启既有 Hub 后，主线程从真实 7d 页面读到 50 行、409,355 匹配请求并打开一条完整详情；列表/详情资源各一次读数为 39.916s/36.493s。此处只追加生产等待证据，不作跨时提速比较，亦不据部署健康与单条详情宣称来源快照故障已修复。归属继续为用户另一个后端 session。

2026-10-08 用户授权本性能 session 实施优化。现场确认 MacStudio 的 Gateway 已是 schema 10，而 monitor 仍通过只列出 3–8 的旧 fingerprint 字典拒绝快照；其余三个来源已更新到 10 月 8 日。已在本地补齐 9/10 精确字段校验，并将详情改为在 SQLite 中选定请求后再解码完整尝试链；同一生产快照副本的详情 profile 从 23.783s 到 1.220s/1.001s，隔离浏览器两条请求三次打开为 0.930s/0.947s/0.925s。此 issue 保持 open：真实 Hub 的部署、快照恢复和部署后响应验收尚待用户许可；执行归本 session。列表仍用完整读取，其后续优化留在本 issue，不以详情优化代表列表已改善。完整读数与测试边界见 [运维记录](../../operations/services.md#2026-10-08-snapshot-compatibility-and-detail-performance-local-validation)。

2026-10-08 用户明确批准后，`a94dfa9` 已推送并部署 MacStudio。schema 10 新快照已纳入，真实调用页显示 10 月 8 日 10:46:19 的观测时间及当日请求，旧快照拒绝问题在该来源恢复。性能只完成部分改善：旧请求重复 HTTP 读取 1.961s；新快照两条请求的三次详情渲染为 14.569s/4.094s/16.586s，列表一次 29.149s。另一个只读 probe 取得 MacStudio generation 用时 16.044s，其他三来源 0.019–0.214s；此耗时含获取与校验，尚未将锁等待单独计时。发布期锁内保留检查/复制/校验仍是剩余等待的候选，后续应先分解此段再决定是否缩短临界区，保留租约、身份与历史保留语义。该残余项及列表优化归 agent-monitor 后端维护者，已记本 issue，本轮不改发布协议；不以本地副本约 1s 宣称线上等待完全消除。后续自动轮次还读到 MacBook rollup-lock timeout，保留旧快照，根因未诊断；本次仅获 MacStudio 部署许可。详见 [部署记录](../../operations/services.md#2026-10-08-snapshot-compatibility-and-targeted-details-release)。

2026-10-08 第二轮按用户要求继续定位：在同一份 MacStudio 生产快照副本（369,775 requests / 369,825 attempts，原文件 1,519,538,176 bytes）上，完整历史保留检查耗时 27.051s，是原发布锁覆盖的耗时步骤。现将其移到锁外，并用既有租约保护历史、在锁内重核 current 指针；冷 generation 校验也在取得租约后移出机器锁。修改后的实际发布持锁阶段 1.230s，含复制、校验与指针切换，整个替换仍为 28.842s。相同传输 manifest 的完整验证由两次减为一次。详情 SQL 的四个查询条件原来均为全表扫描；导出时添加可选索引后，同一请求的原始行读取从 0.950s 降至 0.000250 / 0.000124 / 0.000103s，内容逐字段相同；一次建索引 2.074s，文件增加 40,824,832 bytes。索引不改变版本化表、payload 或既有读取兼容性。

隔离 headless 浏览器使用这份带索引的副本，实际点击两条 success 请求、三次详情渲染为 1.046s / 0.017s / 1.009s（首次、重复、发布期间切换）；最后一次发生在另一进程执行 31.364s 保留检查期间，未等到发布完成。机器/项目、历史候选与完整单次尝试均已读到，成本 unknown 保持未知。此样本仅覆盖本地副本的两个请求，不代表生产或尾延迟。列表一次仍为 16.587s，其完整历史投影没有在本轮改写；继续归本 issue 的 agent-monitor 后端维护范围。第二轮改动尚未部署，线上复验须取得本轮 push / MacStudio 更新许可；不将上一轮针对 `2933060+a94dfa9` 的许可扩大到新提交。

第二轮回归边界：受影响八个测试模块共 89 个方法，得到 7 个失败断言，集中在 5 个旧 schema 假设的测试方法；在改动前 `bf28f21` 的独立归档、同一 Python 3.13 / Gateway / 隔离 HOME 下，这 5 个方法同样产生 7 个失败断言。它们属于既有 Gateway 测试漂移（基线独立），本轮不改其断言，也不宣称全套测试全绿。最终定向 61 个方法通过（详情 fixture 为 2 台来源、3 个项目；当前 schema 8/9/10；并发与串行、冷与热 admission；切指针前后两种崩溃位置、同进程和跨进程租约）。新增测试覆盖保留检查期间读取、竞争发布重检、冷校验租约与 GC、四种索引查询计划；既有真实子进程崩溃恢复和跨进程租约测试保留。诊断子代理及一次恢复均被登录令牌刷新失败阻断，没有独立诊断产出；本轮结果来自执行者测量与回归检查，生成后 gate 采用可直接捕获上述回归的自动检查，不宣称独立外审。原始读数与实际测试加载路径/文件摘要保存在本机 `~/.codex/task-artifacts/monitor-contention-20261008/`。

2026-10-08 第二轮已获用户许可并将 `c37ce00` 推送、部署到 MacStudio；带四个详情索引的 schema 10 快照已发布，页面读到推进后的观测时间。首次详情 1.624s，但后续两条请求的三次打开仍为 15.096s / 0.113s / 12.909s，另一次重复为 1.358s；不能结案或宣称等待稳定消除。30d 列表两次 43.557s / 67.886s。现场独立进程冷调用详情 1.013s；同机成对真实 HTTP / 独立函数三次为 4.968s/1.007s、0.044s/0.012s、0.053s/0.007s。Hub 原生线程采样显示大量 JSON/GC 工作及主线程等待 Python 执行锁，支持继续诊断后台全量处理与 HTTP 共进程的争用，但该采样并非与两次慢点击同时取得，不作精确因果结论。本性能 session 继续承担此残余诊断。最终同步还读到 MacBook rollup-lock timeout 复现、MacMini 导出报 database or disk is full；这两项属来源刷新运维，未包含在 MacStudio-only 服务变更许可内。完整现场身份、读数及边界见 [第二轮部署记录](../../operations/services.md#2026-10-08-indexed-details-and-publication-contention-release)。

第三轮已在隔离分支实现 Hub 同步进程隔离，保留原有校验、历史、身份与刷新状态。相同生产副本、2 条详情、每种方式 80 次 HTTP 读取覆盖完整保留检查：线程模式中位/最大 0.00799s/1.17950s，进程模式 0.00530s/0.01040s；保留检查分别 26.550s/26.500s。受影响 5 模块的 77 个测试方法通过；测试断言吞异常的缺口已修并以反向对照验证。独立审查遗留项是新 generation 的父进程冷 admission 校验（缓存不跨进程），本轮不取消此校验，归本性能 issue 后续真实入口验收。线上效果仍未核实，下一动作是用户批准本轮具体提交的 push / MacStudio 部署后，由本 session 更新并复验；30d 列表及另外两台来源故障仍未结案。测试期间旧 mock 错位曾误触发真实导出，终止情况、未核实副作用与修复边界已记入 [第三轮本地验证](../../operations/services.md#2026-10-08-sync-process-isolation-local-validation)。

第三轮已获明确许可并将 `e155aec` 推送、部署到 MacStudio。新 generation 已发布并由浏览器读到；真实详情前 3 次为 0.612s/0.131s/0.070s，新 generation 后为 8.132s/0.044s。等待已改善但尚不稳定，30d 列表仍为 37.773s；独立诊断确认四个全量 HTTP 入口仍在父进程解码并投影全量历史，现有 Gateway 缓存/索引不能直接用于这条多来源快照路径。下一阶段可隔离这四个入口及其 JSON 编码，须明确新增进程的内存/并发取舍；本 session 已完成定位，待用户裁决该取舍后实施，不把热详情结果充当冷读结案。

MacMini 旧快照原因已进一步定位：数据卷仅剩 261MiB，12 个遗留 export 临时目录共 6.17GiB；现有 `find /tmp` 在 macOS 不遍历入口符号链接。修复为只跟随入口链接的 `find -H /tmp`，原有 owner/name/age/depth 限制不变；合成目录红绿对照及 14 项同步测试完成。修复尚未发布，MacMini 未手动清理，归用户的新 push / 部署与清理许可；原始快照和锁不在清理范围。MacBook 本轮已恢复成功刷新。现场身份、读数与验证事故边界见 [第三轮部署记录](../../operations/services.md#2026-10-08-sync-process-isolation-release-and-remaining-bottlenecks)。

2026-10-08 随后获批推送并部署 `a18f5ea`，现有 reaper 清除过期导出后，MacMini 可用空间恢复到 6.1GiB，成功发布 `04:29:47Z` 的新 generation，Hub 于 `04:30:54Z` 报成功、`stale=false`。快照恢复已由实际同步 API 核实；统计元数据仍显示 1 个 blocked source，不外推为全部来源完整。用户另外批准了四个全量 HTTP 入口进程隔离与最多 2 个并发任务，归本 session 实现和本地验证；具体提交的 push / 部署仍须单独授权。

第四轮本地实现已完成：四个全量 HTTP 入口及 JSON 编码在独立 worker 执行，上限 2，详情绕过其排队。相同真实副本、2 条交替详情的 HTTP 对照：列表期间最大等待 1.393s→0.00806s，完整导出期间 4.223s→0.01479s；列表与 618,351,695-byte 导出均逐字节一致。35 个受影响测试方法通过，输入维度和采样内存见 [本地验证](../../operations/services.md#2026-10-08-full-history-http-isolation-local-validation)。这些读数不证明线上冷详情或列表算法已解决；下一项为具体提交的 push / MacStudio 部署许可，执行与真实浏览器复验仍由本 session 承担。

第四轮已获明确许可将 `04d90cb` 推送并部署 MacStudio。真实浏览器在 4 个请求上打开详情 7 次，分别为 83.9/27.9/46.4/80.0/27.9/527.8/39.5ms，覆盖全量查询并发、重复/切换、1/2 attempts 及 generation 换代；新快照由 `04d90cb` 导出并已显示推进的观测时间。四台来源最后一次同步均成功，MacMini 已恢复更新但元数据仍含 1 个 blocked source。本轮详情等待与快照恢复交付完成；本 issue 因全量列表仍慢而保留 open：首次列表 38.195s，并发显式页面查询 75.504s（含可能的排队）。列表算法、持续资源成本及更广冷读刻画归 agent-monitor 后端维护者后续处理，本轮不扩展到 Gateway 重写。现场身份、全部读数与边界见 [第四轮部署记录](../../operations/services.md#2026-10-08-full-history-http-isolation-release)。

第五轮按用户选择“请求优先，附加功能按需加载”完成本地实现：初始请求页不再解码完整历史以计算总数、成本、独立 attempts 或完整筛选选项。可选时间索引在导出时构建；选中条件、完整请求链、精确排序与多机 cursor 语义保留。单来源隔离副本的真实浏览器列表热读为 8.8/11.2ms，翻页 10.9ms；手动全量统计仍为 16.386s、选项 13.971s，不能宣称这些算法也已提速。106 个受影响测试方法中 105 通过，1 个旧 schema 断言在未改 main 同样失败。审查发现的时间游标与初始点击丢筛选两项已修并回归。具体范围、来源与读数见 [本地验证](../../operations/services.md#2026-10-08-request-first-list-local-validation)。当前仍未部署，issue 保持 open；新提交的 push / MacStudio 更新重启须用户许可，获批后的部署及真实入口复验由本性能 session 执行。大时间桶、稀疏 attempt 筛选仍可能扫描大量行，当前不宣称所有筛选都达到毫秒级。

第五轮 `a06da5e` 已获批推送并部署 MacStudio，新索引快照已发布并被页面读取；真实列表仍为约 7.7–10.1s，不能以本地毫秒级结果结案。按真实来源分解发现 MacMini 旧快照单次约 7.0s，主要等待落在无 child 索引时的逐请求重复扫描。已补齐候选请求批量读取；同一已租约保护的四来源、固定时间、完整响应不变，独立读取由 9.078s 降至 0.855s，4 项列表回归通过。这是独立只读进程的候选代码测量，尚未部署该补丁；新提交发布及 MacStudio 更新许可归用户，获批后的执行与真实页面验收仍归本 session。详见 [发布与旧来源后续记录](../../operations/services.md#2026-10-08-request-first-release-and-legacy-source-follow-up)。

2026-10-08 结案：用户批准“请求优先，附加功能按需加载”后，`218bf2f` 已推送并部署 MacStudio。真实四来源页面 30d 列表读取 822.4ms，翻页渲染 866.1ms；两个同时列表读取 964.4/965.4ms，7d/aihot 筛选 423.9ms，详情 24.6ms。首屏 50 条请求可读，未加载总数明确标注。此前 schema 兼容、快照恢复与详情工作保留以上逐轮证据；本轮完成普通列表打开，未把全量分析算法也说成已加速。该分析按用户选择保留显式加载；非穷尽样本不承诺所有冷读或稀疏筛选的上界。独立的 MacBook 间歇 rollup-lock timeout 另归 ISSUE-SYNC-20261008-b74e，不掩盖在本结案中。完整现场读数见 [最终交付记录](../../operations/services.md#2026-10-08-request-list-performance-delivery)。

---

## [resolved] HARNESS-20261012-36ec：后端性能发现依赖用户报慢，共享冷路径残余未形成主动检查

- Type: coverage gap
- Priority: medium
- Discovered: 2026-10-12，用户在 Explore 优化交付后要求分析为什么此前后端优化未提前消除这些问题。
- Owner: agent-monitor 开发流程维护者；通用入口候选归 ai-agent-config 的 performance-optimization / coding-guidelines owner。本轮只诊断并记账，不修改通用规则。
- Scope: 本项目端到端性能发现和开发预防；不等于所有旧性能优化无效，也不证明所有历史 session 都漏读了规则。

### 当前证据

`docs/adr/20261008-c4e2-sync-process.md` 已明确记下 process-local validation cache 的残余：父进程账号记忆或首次 HTTP 请求仍会校验新 generation。`20261008-d102-query-process.md` 也明确区分进程隔离与算法提速。此前调用列表交付只覆盖其具名用途，不足以证明 Explore 或全部后端链路已优化。2026-10-11 Explore 诊断得到冷 admission 1.0706 秒、同进程第二次 0.0015 秒、固定代际聚合 SQL 约 1–3 ms；完整证据和边界见 `docs/explore-performance.md`。这支持共享请求准入成本没有被那几项局部优化消除，不反推此前完整执行轨迹。

开发时可通过调用链和复杂度发现全量文件校验进入请求、锁覆盖复制等候选，再以最小测试证实。当前 `tests/test_query_view.py` 明确断言 HTTP 不重新 admission / validation，`tests/test_snapshot_contention.py` 冻结复制后检查读取能否继续；这些机制测试比单纯增加业务断言数量更能阻止同类退化。它们仍不能证明未知性能问题已被全面发现。

### 规则检索与缺口

本轮读取 `~/.claude/AGENTS.md` 性能入口、`references/performance-optimization.md`、`references/coding-guidelines.md` 和 `skills/review-changes/SKILL.md`。前者触发原句为“用户明确指出慢、卡或明显延迟、要求优化性能，或当前任务的真实入口读数已显示主导等待时”；性能正文要求“从用户或真实消费者实际使用的入口取得端到端读数”。它们已覆盖报慢后的诊断，不能把问题笼统归为缺少性能原则。当前读到的 coding-guidelines 有有界并发和可观察验证，但未给上述同步/存储改动明确接入按生命周期检查成本的要求。这里只判断已读入口，不断言全 harness 没有仪器。

检索命令：`rg -n '主动.*性能|性能.*主动|冷.*热|复杂度|主导等待|性能问题|20260830-4b84' /Users/lindong/research/ai-agent-config/docs/issues/harness-issues.md /Users/lindong/research/ai-agent-config/docs/issues/archive/closed.md /Users/lindong/.claude/AGENTS.md /Users/lindong/.claude/skills/task-workflows/references/nonfunctional-requirements.md /Users/lindong/.claude/references/rigor-tiers.md`。中央仓历史命中 `HARNESS-20260902-a4e9`，它讨论批调用遥测，2026-10-08 已按用户裁决 wontfix 且注明未证明修复；本条是新的项目具体证据，不重开该历史条目、不沿用其中已撤回的全局无仪器断言。项目检索 `rg -n 'performance|性能|cold|admission|27.051|全量' docs/issues/general.md docs/issues/archive/closed.md` 命中旧调用页逐轮验证与结案范围，核对其具体覆盖而非把结案当全后端通过。

### 建议方向与处置

建议在涉及请求、采集、发布和存储的开发中检查本次受影响的完整消费路径：工作量随什么增长、何时发生、是否争用读取资源；识别到具体风险后写能使错误结构报红的测试。显式性能优化则从具名入口主动取得冷/热、新代际、后台任务重叠与代表性数据规模读数，并复用既有性能决策正文。共享组件的已知残余应在后续涉及其消费者时重新评估，不能由一个页面的交付隐含关闭。无需每次提交全站压测、强制多轮外审或无读数重写数据库。

下一动作：对齐用户期望后，由上述 owner 分别落实开发入口和项目回归接入；验证原 Explore 类路径能在用户反馈之前被发现，并保留普通局部改动不被扩大为全项目性能工程的合法情况。本轮未运行 harness 行为实验，不能宣称主动发现能力已修复；架构、规则和历史记录的诊断已完成，待实施项保持 open。

### 2026-10-12 关闭记录

用户批准两时点与文件范围后，通用入口和性能正文已改，项目成本地图、隔离诊断及回归已接入；完整变更、13 项最终测试、两种反向变异、三个只读设计响应及测量边界见 `docs/proactive-performance.md`。关闭的是已识别的入口和项目诊断能力缺口，不宣称未来所有 session 都会遵循。复发判据：相关后端开发仍只看局部 SQL/业务正确性、显式优化仍等待用户指定慢接口，或普通文案小改被扩成全仓压测；发生时用具名任务轨迹重开核查。原始诊断段保留为当时记录。

## [closed] ISSUE-EXPORT-20260915-e3a1：gateway ledger 一段读不了，整台机器的 export 就失败，用量与配额一起停更

- **Type**: bug
- **Priority**: high
- **Discovered**: 2026-09-15，hub 页显示 `tencent-webserver-china` 刷新失败；在该机手动跑 `~/.local/bin/tt-web export --out <tmp>/bundle --cached-quota` 复现，exit 1，末行 `llm_attempts.ProjectionError: Audit ledger schema is unsupported`
- **Description**: `statistics_snapshot._gateway_snapshot()` 对 ledger 只有 `missing` / `available` 两态，`_validate_schema` 抛出即让 `write_snapshot()` 整个失败，于是 `exporter.export_bundle()` 不产出 snapshot——该机的 Claude Code / Codex 用量、配额读数全部停在上一代，只因为 LLM Gateway 的 ledger schema 比这台机器上的 tt-web 新。契约 H8 要求"ledger 不可读 / schema 不兼容时显示明确 error"，但这个错误在 export 阶段就把整份快照否掉了，页面永远走不到 H8 那一态。
- **Notes**: 修法方向是给 gateway 段一个 `error` 态（`state: "error"`, `reason`），usage 段照常写入，hub 的 LLM Calls 按 H8 显示该机 ledger 错误。这是跨 `statistics_snapshot` / `llm_attempts` 读取端 / server 的行为决策，当轮未做。**这台机器的即时修复是更新其 tt-web checkout**——2026-09-15 已做（部署 commit `a831a34`，导出恢复，见 `archive/closed.md` f2b9），本条只剩设计缺陷本身：下一次 gateway 与 tt-web 版本再错开时，同样会整台停更。

- **Resolution (2026-10-12, source only)**: Remove Gateway collection and imports from Monitor; isolated import/export with a forbidden Gateway package passes. Legacy v1 agent usage remains readable. Live deployment/cutover is tracked in docs/product-boundary-separation.md and is not claimed here.

## [resolved] ISSUE-UX-20261015-7e42 — HTTP Hub 会话详情复制 ID 失败

- **Observed / Priority**: 2026-10-15，MacStudio HTTP Hub 的两个独立浏览器、两个不同会话详情点击“复制 ID”，均显示“复制失败，请选中 ID 复制”；Medium，有手动选中绕行路径。
- **Evidence**: 页面 `isSecureContext=false`、`navigator.clipboard=undefined`；同环境 guide 复制命令显示“已复制”，但未回读系统剪贴板内容。线上 `26150c3` 与本地 `cfcf0aa` 的运行代码一致。原始证据在 `/Users/lindong/task-artifacts/agent-monitor-capabilities-20261015/` 的 `root-copy-id.txt`、`evidence/copy-id-after.txt`、`evidence/guide-copy.txt`；[能力复查](../../references/product-capability-review-20261015.md) 保留版本与范围。
- **Owner / Next**: agent-monitor Web 维护者；详情复制复用已支持 HTTP 的兼容路径，并在同一入口验证复制结果。本轮仅审查，不实施。

- **Repair 2026-10-15**: 用户授权落实六项建议，代码修复与隔离页面验证完成；见 [交付记录](../../../plans/20261015-product-ui-improvements/plan.md)。生产发布与 HTTP 剪贴板内容回读分别记录，不以单元测试代替。

## [resolved] ISSUE-UX-20261007-31c8 — 采集错误缺少面向用户的处置摘要

- **Observed**: 2026-10-07，macbook 本地 39001 的真实 `/sessions` 显示 `macmini` 刷新失败、保留四小时前数据，原因直接为 `sqlite3.OperationalError: database or disk is full`；`macstudio` 保留 28 天前数据，原因直接为 `ValueError: unsupported gateway snapshot`。页面已经说明旧数据仍在使用，但没有相应的可读处置摘要。这是中心记录的源侧错误，未独立诊断远端根因。
- **Recommendation**: 先给“哪台机器的数据未更新、旧到何时、下一步检查什么”，原异常保留在技术详情。不能只美化错误而声称采集恢复；实际磁盘/快照问题另需服务维护与该环境授权。
- **Owner / Scope**: agent-monitor 后续内容优化及运维任务；本轮修复 Codex 读数展示，不修改远端服务。

- **Recheck 2026-10-15**: 真实 guide 又显示 rollup lock timeout，已有“使用上次数据”提示但没有可读处置摘要；随后曾观察机器恢复，不据此认定持续故障。证据 `agent-monitor-capabilities-20261015/evidence/guide-content.txt`，本轮仅记录。

- **Repair 2026-10-15**: 用户授权落实六项建议，代码修复与隔离页面验证完成；见 [交付记录](../../../plans/20261015-product-ui-improvements/plan.md)。生产发布与 HTTP 剪贴板内容回读分别记录，不以单元测试代替。

## [resolved] ISSUE-UX-20260915-a1c2 — 同步面板一出问题就把首屏整个占掉，最该看的数字被推到一屏之下

- **Type**: information-architecture
- **Discovered**: 2026-09-15，真实 hub 页 `http://macstudio:39001/`，1440×900 / 1024×900 / 720×900 三档
- **Observed**: 4 台机器里 2 台刷新失败时面板自动展开（设计如此），高 524px（1024 宽）/ 492px（720 宽）；Spend 标题落到 y=601 / 651，Quota 标题 y=743 / 1017，而当天唯一需要立刻行动的事实——Codex Pro 账号 7d 已用 100%、`almost out`——那一行顶边在 y=1060（1440 宽）/ 1152 / 1426，三档都在首屏之外。四张机器卡片各带一行 `v2 full-history project-independent usage totals` / `v2 full-history hybrid totals (208 legacy date/agent/model buckets)`，这是 totals 口径的内部标识（对应 `generation_totals_basis`），读者无法据它做任何事；`History since 2026-09-01` 与下方 `Data updated` / `Last reached` / `Last attempt` 四个时间戳并排，要先分辨哪个才是"数据多旧"。
- **Impact**: 读者打开页面是来看"今天花了多少、配额还剩多少"；一出故障，首屏变成运维诊断面板，回答那两个问题的区域全部需要滚动。
- **Reproduce**: 任一远端 export 失败时打开 Overview，不滚动，读 Spend 与 Quota。
- **Recommended direction**: 三个方向都改变既有契约 G1/G2 的版面承诺，需用户裁决而非 agent 自定：① 面板仍在顶部但只展开出问题的那几张卡片、健康机器折叠成一行；② 面板整体移到 Spend / Quota 之下，顶部只留 `N/M machines included · 2 machines need attention` 一行摘要（点击跳转）；③ 保留位置，但把 totals 口径行移进 hover/title。本轮已把"哪几台没算进来"直接写到 Spend 区（见 ux-contract-issues 2026-09-15 条），部分缓解①的动机，但 Quota 那行仍在一屏之下。
- **2026-09-15 更新**：③ 已由文案中文化那轮**以更强的方式**执行——`v2 full-history …` 口径行不是移进 hover，而是整块删除（用户裁定）。因此上面 **Observed** 里"四张机器卡片各带一行 `v2 full-history …`"这段描述已不再成立，卡片也随之变矮；但首屏挤占这个本体问题未解，①②仍待裁决。

- **Recheck 2026-10-15**: 同步面板展开时 KPI 顶边约 y847；1600×900、1280×720、1024×576 CSS viewport 三个样本首屏均无完整 KPI。仅为布局读数，不是实际浏览器缩放或全部场景视觉结论；证据 `agent-monitor-capabilities-20261015/evidence/zoom80.txt`、`zoom100.txt`、`zoom125.txt`。本轮复用本条、不另起重复 issue。

- **Repair 2026-10-15**: 用户授权落实六项建议，代码修复与隔离页面验证完成；见 [交付记录](../../../plans/20261015-product-ui-improvements/plan.md)。生产发布与 HTTP 剪贴板内容回读分别记录，不以单元测试代替。

## [resolved] ISSUE-UX-20260826-5453 — 768px 下 Quota 的机器归属与更新时间默认不可见

- **Type**: responsive-usability
- **Discovered**: 2026-08-26
- **Priority**: medium
- **Observed**: 在真实页面 768×900 下，整页 `scrollWidth=clientWidth=768`，但 Quota 表容器 `clientWidth=736`、`scrollWidth=1040`、`scrollLeft=0`。默认视口只能看到部分 Machines，Updated 完全在右侧；容器可横向滚动，但没有持续可见的提示。
- **Impact**: 窄屏回访用户无法一眼同时确认 quota、机器归属与读数新鲜度，这三者正是判断当前账号是否可用的同一决策单位。
- **Reproduce**: 以 768×900 打开 Overview，滚到 Quota Used，在不横向滚动时读取 Machines 与 Updated。
- **Recommended direction**: 重新评估窄屏列优先级或给出明确的受控滚动提示；必须保留 provider、账号、quota、机器归属与更新时间之间的可关联性，不能仅隐藏列。

- **Recheck 2026-10-15**: 390×844 下配额容器 334px、内容 1040px，5h/7d/机器/更新列分别从 x420/548/676/865 开始，默认不可同时读；真实横滑可达，1440px 桌面七列同时可读。复用本条，不称数据缺失。证据 `agent-monitor-capabilities-20261015/evidence/narrow-layout.txt`、`agent-monitor-capabilities-20261015/root-quota-narrow-left.png`；本轮仅审查。

- **Repair 2026-10-15**: 用户授权落实六项建议，代码修复与隔离页面验证完成；见 [交付记录](../../../plans/20261015-product-ui-improvements/plan.md)。生产发布与 HTTP 剪贴板内容回读分别记录，不以单元测试代替。

## [resolved] ISSUE-PRODUCT-20261015-4c28: Web capability handoffs need fewer manual steps

- Owner / Scope: product and Web maintainers; enhancements proposed by the 2026-10-15 [product capability review](../../references/product-capability-review-20261015.md), not newly approved requirements or implementation.
- Unknown pricing: Overview shows unknown-entry counts but Explore has no unknown-pricing filter or direct distribution/drill-through entry. Connect the count to model/project attribution and retained detail while preserving the aggregate/detail retention boundary.
- Period comparison: the two explicit date ranges and deltas have no direct Explore links. Start with date-preserving links; richer attribution is a separate increment.
- Account selection: existing sort choices favor low remaining quota, upcoming resets or recent observations; add a high-remaining direction if implementing this selection improvement. Keep identity and freshness visible; observations do not guarantee usability.
- Disposition: recorded only. Six core tasks already have support; these reduce manual work rather than establish missing whole-product capabilities. No product changes or deployment in this audit.

- **Repair 2026-10-15**: 用户授权落实六项建议，代码修复与隔离页面验证完成；见 [交付记录](../../../plans/20261015-product-ui-improvements/plan.md)。生产发布与 HTTP 剪贴板内容回读分别记录，不以单元测试代替。

## [resolved] HARNESS-20261016-8f3a：界面写作优化仍遗漏重复信息与内容布局

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


Resolved 2026-10-16：按用户批准实施信息分工与层级，八项呈现修改见 [6b21](../../adr/20261016-6b21-reader-information.md)。同次修改 ai-agent-config 的五份共享写作/阅读指令；保留五模板与原审核层级，未来模型遵循率未量。真实浏览器读取当前生产 API 的只读预览，在 1440/1024/390 三种宽度检查机器、操作详情和配额；历史8行时间合并，操作11卡不再常显旧配额。1024横滚300px首次暴露 table overflow 阻断 sticky，修正后账号列左缘244px等于容器左缘；7d会话入口实际跳至总览同range并展开4机详情，Guide共享4机展开也已读取。58项受影响测试通过；3项基线旧文案断言失败另记general原条目。证据目录 `/Users/lindong/task-artifacts/monitor-information-20261016/`。此时部署尚待执行，生产验收与最终提交身份由同目录 release-receipt.md 接续，预览不冒充生产。无账号写操作。

## [resolved] HARNESS-20261016-reading-redundancy：全站阅读复验漏掉同一行的条件性重复

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

### Resolved 2026-10-16

用户授权按建议修复后，实施单机器同源条件去重，新增相反关系回归，并在真实数据预览中按整行而非单字段完成宽窄屏阅读、筛选和历史展开检查；具体结果见 [配额行内信息关系复验](../../references/ui-reading-review-20261015.md#2026-10-16-配额行内信息关系复验)。执行验收缺口在本案例闭合；原共享原则保留，不以关闭本项目条目声称通用遵循率问题已解决。生产发布验收记录见该节所指回执。


## [resolved] HARNESS-20261016-reading-structure：配额阅读优化保留既有列结构，未充分比较信息重组

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


### 2026-10-16 获授权实施后的处置

本条原诊断和未实施描述保留为历史。通用指引已落在 ai-agent-config 的 ui-copy 单一 owner，review-ui 直接引用；按更新正文实跑后，产品完成列职责重组及展开时间交互，50 项受影响测试与四宽度真实数据预览通过。记录见 [阅读复验](../../references/ui-reading-review-20261015.md#2026-10-16-配额列职责重组与-harness-实跑)。产品问题和指引候选已实施；未来泛化收益未测，不由关闭本条推导。

## [closed] HARNESS-20261016-8d72：阅读宽度优化误将文档实例上限迁移到控制台

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

- Resolution (2026-10-17): 用户授权后，先修订 ai-agent-config 的 design-ui、reading-instance 及 web-ui-observation，再显式读取候选版本实施本项目。移除应用外壳上限，保留块内文字规则；网络卡片按实际容器宽度排为一/二/四列。improve-skill 实跑在四列起点发现 12px 的取值区越界，修复长状态换行并补充共享观察规则。5 页面 × 6 视口的预览无整页横溢出；网络内部另核 10 个宽度，含断点两侧。1920px main 1324→1684，2560px main 1324→2324；49 项既有 Web 测试通过。证据：`/Users/lindong/task-artifacts/monitor-width-recovery-20261017/`。这些是已知问题修复读数，未证明未来 agent 自主发现率或阅读速度；生产部署与真实入口回读另见该目录最终 release-receipt.md。
