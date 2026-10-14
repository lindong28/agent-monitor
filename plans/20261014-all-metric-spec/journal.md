# Journal — all metric spec

> Append-only decisions and evidence required for safe continuation.

## 2026-10-14 起步

用户明确授权全指标规范对照、修复和文档；项目常设部署权限有效。历史实验 ea8bf9cd 不等于现有产品 main，当前基线22507ab包含实际c6734f5修复，所有缺陷均须重核当前实现。

## 规范反例与分工

成本聚合五状态反例已修后台，test_metric_projection三项通过。source/quota/network独立审计已交回。适格实现按不重叠文件在独立worktree分工，原生Codex R13(high)处理source、R9(medium)处理固定Web/network修复；Cv由明确失败样本与受影响回归核验，低于自行重写Ci。决定包复用原独立reviewer R14；主线程负责跨指标口径、docs、rollup/diagnostics与最终验收。不用wrapper/远端写worker。

## 决定放行

aggregate及repair决定独立review已通过；修正版决定落task-artifacts/repair-decision.md。成本限定为记录值或适用配置估算，不许全历史当前价声明；Codex5h用户MCP明确批准override。网络只加coverage，复用既有字段。生产derived replay仍待独立范围核实。原source agent两次capacity中断且无写入，独立source实现树改由metric_source_repairs承接；不改模型配置绕限制。

## 实现整合与生产预探

source修复92项相关测试通过；零化继承记录cost=0是已证无child消耗，不需要型号价格；entry_count仍是归档记录数。未知边界不删除旧归档。network已补成功信封缺IP反例，缺risk单独保留grade并披露partial。文档16项产物patch已整合。四机部署前读数显示admission4/4而两台source scan不完整，故完整性不得与发布/价格覆盖混同。Web消费者验证复用原worker R9，用实际页面和隔离fixture、禁止生产出口；主线程继续整合及部署准备。

## 最终审查与回归

独立审查发现完整parent prefix假设不能覆盖过滤历史/父文件增长，按上游thread_settings_applied.thread_id恢复其后确定增量，绝不将resume前全部事件零化。新增4方法覆盖过滤×父增长、owner负例、归档冷热强制重读；source96测试通过，独立27项定向复核通过，新增finding无。现行UX5h/成本/网络口径已同步。初次fullsuite7处失败中两处经基线对比确认原有fixture问题，其余为本次接口期望更新；修正测试后最终924项通过。浏览器窄屏排名覆盖文案高度缺陷已修并复验，隔离实例已关闭。进入已授权发布阶段；现有未知不作完整性承诺。
