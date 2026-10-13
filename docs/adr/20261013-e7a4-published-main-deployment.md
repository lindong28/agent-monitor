# Published main deployment

- Date: 2026-10-13
- Status: accepted
- Source: 用户明确要求生产部署默认使用 origin/main HEAD，并包含必要的普通 push；要求写 plan 后实施。
- Review: 独立决策审查七项成立，未决 findings 无。

默认生产部署先完成任务提交、整合和授权发布，回读 origin/main 后锁定本次 SHA，再让部署 checkout 快进到该提交。显式指定版本及项目专门部署契约优先；不改变他人 WIP、已发布历史和归属边界。共享许可和操作纪律由 ai-agent-config 的 claude/AGENTS.md 与 concurrent-plan-isolation.md 拥有；产品不在运行时依赖它。

仅改说明不能拦住误安装；改变所有 install 用法会妨碍 staging；共享 harness 运行库会引入产品依赖。因此两个产品各自拥有自包含的生产 wrapper，运行原 installer 前检查 checkout 和远端版本；普通安装行为保留。wrapper 不是操作系统隔离，部署期间仍须协调 checkout 写入；源码检查通过不表示服务已经更新或真实消费者已核验。

本轮实际 rollout 限 agent-monitor 的 MacMini。Gateway 新入口接根 install.sh 的个人部署；公司 deploy/manage.py、site.json 显式 pin 及 operator 配置同步机制保持原契约。未验证的生产效果必须单独报告，不能外推。
