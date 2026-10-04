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
