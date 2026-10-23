# Web 产品系统代理修复

> **Long-task mode** — 已完成，2026-10-23；最终交付与验证边界见state.md。
> 进度状态：`./state.md`；决策日志：`./journal.md`。
> 实施及 compact 后先读 state/journal；完成前实际执行本计划适用 verify。

用户要求（2026-10-23）：Web UI 产品产生的流量默认识别并使用系统代理，多代理 resolution 与 Google Chrome 保持一致；当前项目与 agent harness 的缺口均需修复。用户已授权实施、提交、普通发布与所需部署，真实上游账号验证仍需具体方案批准。

## 交付目标与边界

请求按执行主机的系统代理规则选路，覆盖浏览器外的产品 HTTP(S) 调用、认证、额度与隔离子进程；不把浏览器所在机器的代理当服务器配置。系统允许的 DIRECT 与配置读取失败区分，不能自行加入直连兜底。SSH/rsync 是单独协议边界，保留现有 SSH 配置，不宣称由 HTTP 代理覆盖。

网络诊断与业务调用消费同一策略，报告目标与观测时间；IP 查询请求的公网 IP 不外推成所有业务目标的出口。保留账号暂停，不触达真实账号、不清凭据、不改变额度与历史。先前未发布的重新授权改动保留在原独立工作树，不纳入本次修复。

## 当前事实与实施期未知

- agent-monitor 基线 7e364465a113ed6df4172c85877b806b5fb06133。账号 HOME 隔离丢失个人代理文件，父进程无代理变量，shim 默认关闭系统代理。
- ip-check 读取 current-proxy；quota_refresh 的 Codex 使用普通 HOME；urllib 使用自己的环境/系统回退。现有实现没有统一策略。
- Chromium 按 URL 解析有序候选，含绕过、PAC、自动发现及失败状态；Codex respect_system_proxy 的能力不能未经验证视为等价。
- 技术选型已过独立决策审查：[20261023-9c4e](../../adr/20261023-9c4e-system-proxy.md)。采用Chromium system resolver和透明桥；传输、配置通知、部署条件与资源成本仍需实现验证。

## 工作单元与验证

| 单元 | 用户结果 | 状态与退出条件 |
| --- | --- | --- |
| 解析与传输选择 | 同目标同系统配置得到 Chrome 兼容路由 | 已完成；Chromium对照、受影响回归与MacStudio启动验收，边界见state |
| 产品接入 | 所有相关 HTTP 调用采用产品策略，隔离 HOME 不影响网络策略 | 已完成；本地模拟目标与代理验证，真实系统配置变更端到端未实测 |
| 诊断与 UI | 网络页能解释探测目标、代理来源和业务选路边界 | 已完成；保留旧读数与未知语义，隔离及线上浏览器验收 |
| Harness | 后续产品开发按系统代理契约建设、实际服务入口验收 | 已完成；共享规范与入口、shim边界已审、发布并核实安装入口 |
| 交付 | 已审成果发布并在目标环境生效 | 已完成；隔离测试、独立审查、发布部署及暂停保留已核，上游未验证 |

最小真实消费者路径：隔离服务经真实子进程启动入口向本地模拟上游发送，代理接收记录与 Chrome 对照可以区分正确/错误选路；其前置仅为可用解析器与必要适配。账号真实上游不属于本次默认验收。

## 隔离与恢复

session 01a165ef-51d4-7340-bc3d-feae991b6917；主进程 PID 9171。

- 产品树：`/Users/lindong/research/.worktrees/agent-monitor-system-proxy-20261023`，分支 `fix/system-proxy-web-20261023`，由本 session 创建并取得持续占用。
- Harness 树：`/Users/lindong/research/.worktrees/ai-agent-config-system-proxy-20261023`，分支 `fix/web-system-proxy-20261023`，基线 `4c9d7cee29f2c263c532e4a1a12dcb5cd1539e14`，由本 session 创建并取得持续占用。主树已有不属于本任务的 WIP，保留。
- 原恢复功能树：`/Users/lindong/research/.worktrees/agent-monitor-account-read-diagnostics`，仍有本 session 未提交的重新授权实现，保留不混入本次发布。
- 测试使用独立 HOME/state、临时端口及模拟上游；不导入生产凭据。
- 只读候选研究 `/root/system_proxy_options`，报告 `/Users/lindong/.codex/diagnostics/agent-monitor-system-proxy-options-20261023.md`。

保障：生产网络策略变化与跨项目默认取 L1 独立决策审查；实现按生产影响 V1，并对已观察到的隔离 HOME 失效留失败回归。部署 R=A2，沿常设授权与独立对抗审查；不以本地可逆编辑降低生效后的影响判断。具体实现未定，不提前扩展全项目验证。
