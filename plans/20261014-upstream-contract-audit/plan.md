# 上游契约逐项正确性论证

> Completed audit — 2026-10-14; Git delivery receipt is stored with the task artifacts.

范围：用户要求逐项论证六类对象及四项已有指标证据边界。基线 eb6e797；只审查、隔离反例和必要文档，不修改产品行为、不触发真实账号操作、不部署。

验收：每个具体问题有对应版本的上游/业务契约、实现链、成立条件、判断与残留证据；明确反例须可复核，缺失证据不当通过；完整结论进入 docs/references/upstream-contracts.md，必要问题入账。

执行：账号/身份/配额桶由 metric_quota_network_audit；存储/generation由all_metrics_inventory；历史来源由metric_source_repairs，均只写仓外独立artifact。root负责Web/配置隔离、进程/同步/部署、承重复核及文档。

调度：广度只读取证适用delegation-policy；三个独立子任务可由具体源码和反例低成本复核，复用原生agent及现有配置；root处理跨域综合。无新增付费调用或生产实验。

收尾：文档核对、适用review、显式路径本地commit、整合main；本轮不包含生产修复或发布。

交付：`docs/references/upstream-contracts.md` 已覆盖六类对象和四项来源问题，逐项标出规范、代码链、成立条件、反例与未知；新增七条产品issue，更新既有来源issue和指标/架构导航。源码没有改动。隔离读数、固定上游副本、三份子任务报告保留在原artifact目录；执行与整合回执见该目录 `delivery.json`。本轮没有发布或部署。
