# tt-web Issues — 已关闭

`resolved` / `wontfix` 条目从各 domain 文件整条移入（格式不改写），只 grep 查史、不通读。open 条目仍在 `../general.md` / `../ux-issues.md` / `../ux-contract-issues.md`。

---

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
