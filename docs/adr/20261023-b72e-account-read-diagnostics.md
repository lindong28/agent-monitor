# Codex 身份读取的安全错误原因

2026-10-23。一轮独立决策审查通过，七项成立；实施前记录。

用户要求诊断并解决网页的 `account/read` 错误，并追问此前是否遗漏具体原因。基线 `f53e6a2` 的 `Rpc.request` 仅保存整数 JSON-RPC 错误码，丢弃上游消息；本次现场记录只有 `-32603`、`checking`、`not_sent`，无法据此区分工作区路由、认证或配置错误。既有 CLI 日志未覆盖本次失败；历史具体原因与账号恢复均未核实。

对 `account/read` 的 `-32603`，精确匹配官方 Codex `0.162.1` 固定提交 `092d3acd6bec3e3a14bdc7e7a2810ab628ab759d` 中 [`WorkspaceRoutingError`](https://github.com/openai/codex/blob/092d3acd6bec3e3a14bdc7e7a2810ab628ab759d/codex-rs/app-server/src/request_processors/account_processor/workspace_routing.rs) 的无参数字符串，派生受控的 `operation.diagnostics.rpc_error_reason` 和中文 `detail`。保留可用整数错误码；未识别的字符串或异常 error 形状明确报告原因未知，绝不保存任意原文。网页沿现有 `detail` 显示原因，无需新增前端状态或另一份翻译表。字段与文案由同一映射派生。

原因字段只用于诊断，不改变 `failure.category`、HTTP 业务状态、身份校验、暂停、限制、重试与恢复。沿现有原子私有 JSON 和 `last_failure` 保存，历史记录不推测回填。原有单机写入者、稳定文件锁、保留槽位与部署拓扑仍适用；每次失败仅增加一个有界枚举字段，无新增写入频次、后台进程或上游请求。

原文全量或截断保存可能泄露凭据，单独保留错误码已证不足，外部模型分类会增加不必要的外传与运行依赖。因此选择固定枚举映射；其覆盖仅限可识别的后续错误，不能证明真实服务恢复。与 [请求约束](20261021-a84c-codex-request-controls.md) 和 [上游预防](20261021-6bd2-upstream-prevention.md) 的安全记录、不自动恢复政策相容，无历史决定 override。

本地验收使用合成身份和 stdio peer，核对已知原因、未知或恶意原文、畸形 error、正常身份对照，以及持久记录、重建 Manager、API 回读与解除暂停后的历史保留。不得借验证触达真实账号；真实账号身份读取另待明确批准。诊断修复与原账号恢复分别交代。
