# State — all metric spec

- [completed] T1 Web/CLI 指标枚举；inventory.md 与 operational-metrics-audit.md。
- [completed] T2 规范论证：metrics.md/metrics-operations.md、7条ADR、现行UX口径及导航已整合；上游缺规范部分显式保留未知。
- [completed] T3 root树已整合cost aggregation/Web/source/quota/network/diagnostics/orphan修复；独立审查发现的copied fork HIGH已修，定向复审通过。隔离浏览器三页、两视口及真实交互完成。
- [in_progress] T4 最终完整回归924 tests通过；独立变更审查通过。正在准备commit、main、push、四机备份部署及消费者验证。

## Ownership
本树锁 token ecbc73ab6bbc40e086ccce6885a1a7ac；root session 01a12f16-04f9-77c0-b6f4-72c8a06b99ab。main state及Ark worktree不动。source/web/network/docs四棵本任务私树均已交接root、锁释放；不删除仍唯一未整合内容。

## Open Issues
- 最终回归 full-tests-final.log：924 tests/103.928s/OK；测试环境单一Mac+Python3.13，不外推全部平台。浏览器报告 browser/report.md：成本4状态、配额2状态、网络2状态、视口2种；非笛卡尔穷举。
- 四机预探已完成：两台main22507ab，两台3380278；state/locks及authority已保存predeployment-readout。MacBook/MacMini source_error各1，MacMini blocker1，inventory worker只读定向核因中。
- 生产replay需先做具体备份/authority范围决定审查，不可删除raw/history。Hub重启前busy账号尚未核实。
- worker shell/edit hook双身份问题已真实出现；拒绝原文在network-identity-rejection，需要按harness issue capture持久登记，不能称根因已修。
- 剩余科学边界：Claude全入口最终usage落盘、历史tariff provenance、全部fork/revert/fallback model，不得宣告全部已证明。
- copied-fork owner checkpoint之前的未知prefix仍保留archive+source_error，不能宣称全部历史费用已校正。

## Decisions
aggregate及repair决定独立审查通过；Codex5h契约更新用户MCP已明确批准。root已整合doc-spec.patch/source-repairs-complete.patch/web-metric-fixes.patch。任务artifact /Users/lindong/task-artifacts/all-metric-spec-20261014/。
