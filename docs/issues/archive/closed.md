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
