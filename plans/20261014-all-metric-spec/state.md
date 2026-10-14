# State — all metric spec

- [completed] T1 Web/CLI 指标枚举；inventory.md 与 operational-metrics-audit.md。
- [completed] T2 规范论证：metrics.md/metrics-operations.md、7条ADR、现行UX口径及导航已整合；上游缺规范部分显式保留未知。
- [completed] T3 root树已整合cost aggregation/Web/source/quota/network/diagnostics/orphan修复；独立审查发现的copied fork HIGH已修，定向复审通过。隔离浏览器三页、两视口及真实交互完成。
- [in_progress] T4 首次发布8d94501已main/push/四机部署、备份及cached-quota重算，Hub纳入4/4，真实浏览器通过。重算发现paginated subagent首次用量被过度保守跳过；已按实际六版本固定上游证明零起点、完成红绿及68项相关测试，版本增量定向复审中，随后跟进发布和重算。

## Ownership
本树锁 token ecbc73ab6bbc40e086ccce6885a1a7ac；root session 01a12f16-04f9-77c0-b6f4-72c8a06b99ab。main state及Ark worktree不动。source/web/network/docs四棵本任务私树均已交接root、锁释放；不删除仍唯一未整合内容。

## Open Issues
- 最终回归 full-tests-final.log：924 tests/103.928s/OK；测试环境单一Mac+Python3.13，不外推全部平台。浏览器报告 browser/report.md：成本4状态、配额2状态、网络2状态、视口2种；非笛卡尔穷举。
- 首次replay source errors为MacBook147、Mini106、Studio3、Tencent0；其中146/19/3的六种实际版本已核上游zero baseline，须用跟进发布实际复验，不能据版本面称已全恢复。剩余Claude不可比较观测2条、Mini copied prefix86及identity blocker1记录ISSUE-METRIC-20261014-716c。
- 四机SQLite备份quick_check通过；原state symlink/lock inode不变；Hub重启前11账号busy=0，部署后health与六个真实资产一致；sync_all(quota_refresh=False)返回4机新SHA且无错误。明细均在task artifact。
- worker shell/edit hook双身份问题已追加到中央harness私树agent-config-metric-issue-20261014的HARNESS-20261009-ed14；按纯harness issue政策保留未提交，不称根因已修。
- 剩余科学边界：Claude全入口最终usage落盘、历史tariff provenance、全部fork/revert/fallback model，不得宣告全部已证明。
- copied-fork owner checkpoint之前的未知prefix仍保留archive+source_error，不能宣称全部历史费用已校正。

## Decisions
aggregate及repair决定独立审查通过；Codex5h契约更新用户MCP已明确批准。root已整合doc-spec.patch/source-repairs-complete.patch/web-metric-fixes.patch。任务artifact /Users/lindong/task-artifacts/all-metric-spec-20261014/。
