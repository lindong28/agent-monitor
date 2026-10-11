# Codex 账号批次：传输、失败原因与配额保留

Status: accepted, 2026-10-11. 用户要求先记录发现与建议，再按记录实施；本文件同时记录实施与验收结果。

## 授权与边界

用户原话：「将你发现的缺口和建议的改进记录到文件。然后按记录的内容进行改进」。范围为 agent-monitor 账号操作、配额展示及 MacStudio 部署；项目 AGENTS.md 已授予部署与所需重启许可。保持账号身份、机器快照、批次消息结果及不自动重发规则。Git push 未授权。补查两个账号配额属于改进范围；真实发送样本另获具体许可。

开发树 `/Users/lindong/research/agent-monitor-account-round-gaps`，分支 `fix/account-round-gaps-20261011`，基线 `ddd27a6`；本 session 负责整合和清理。测试使用隔离 HOME/state 和合成 stdio peer。

## 发现与证据

证据：MacStudio `/api/codex-accounts`、账号私有 logs_2.sqlite 只读查询和部署源码对照。批次 `76470490-0fe7-4d5c-b2f6-4566abb34671` 开始于 2026-10-11 22:54:41 UTC+8；远端源码 `2018322`。本地已有子进程回收补丁尚未在远端，下面涉及的发送/配额逻辑一致。

| 缺口 | 直接读数 | 改进目标 |
| --- | --- | --- |
| 传输耗尽时间预算 | 11 个账号各经历 5 次 WebSocket 超时重试；9 个成功账号均记录 HTTP fallback。lindong4 22:56:41 回退 HTTP，22:56:44.645 触发 120 秒超时，发送结果未知 | 消除短消息路径无效传输等待，不无限延时或重发 |
| 终态错误丢失 | lindong6 message_status=failed；代码丢弃 turn.error，替换为固定文案 | 保存受控错误类别和失败阶段，不暴露上游自由文本 |
| 最新配额未进入展示 | lindong6 before 已是 100%，last_quota 仍为 12:42 的 16%；前后端都只选 after/last_quota | 所有成功配额观测按原时间选择整份最新读数 |
| 发送失败没有补查 | 两账号 quota_status=not_read，无 after | 失败/未知后有界补查，保留原消息结果 |

发送前重置：lindong4 为 2026-10-18 12:41:59 UTC+8，lindong6 为 11:58:40。100% 是观测，不能作为 lindong6 服务端最终错误的证明。WebSocket 底层故障点未确定；旧的具体终态错误未持久化，无法事后恢复。

## 待审方案与备选

1. 仅为隔离账号 app-server 注册 `agent-monitor-http` provider（name=OpenAI、requires_openai_auth=true、wire_api=responses、supports_websockets=false，无 base_url / env_key 覆盖），不改用户全局 CLI、模型或固定消息。先核部署 CLI 真正采用配置；保留 120 秒发送预算。只增大 timeout 仍会每次多等近两分钟；改全局代理作用面更大且无底层归因；减少重试仍先走已观察失败的路径。
2. operation 增加可选安全 failure（失败阶段、受控类别、可用 HTTP 状态码）；类别只来自结构化 codexErrorInfo 或本地异常。未知错误仍为未知，不复制 message/additionalDetails 或任意字符串。前端显示安全解释，保留本轮发送结果。
3. operation/batch_result 的 before 和 after 均参与 last_quota 和前端的整份最新观测选择。失败不刷新旧时间，新响应的 null 不以旧字段填充。不保留无限操作历史，不写机器账号 memory。
4. 消息已尝试但失败/未知时，最多补查一次配额；只查配额，不重发、不申请设备授权。失效 RPC 先关闭，新 RPC 使用已存登录且重新验证邮箱/workspace。初始化、身份和配额各有 20 秒上界；取消不恢复，身份不符不查询配额；补查成功不改变原发送状态。仅复用失效 RPC 不能覆盖断连；自动重发违背原有消息语义；只提示用户手动补查保留了本次已授权修复的缺口。
5. 部署后仅刷新 lindong4/lindong6 配额并核批次结果不变、时间更新、页面真实可见。先无消息配置探针，真实发送样本获具体许可后执行。

## 契约、来源与验证

权威语料：本仓 docs/adr、README、architecture、ux-contract；检索 before/after/last_quota、turn/completed、timeout、replay、HTTP、error。c814（2026-10-07）规定身份校验/结果分离/不重发，b923（2026-10-07）规定固定批次及仅重试未发送，a62e（2026-10-07）规定整份读数/机器同时间优先，e92b（2026-10-08）规定配额持久保留，f7c1（2026-10-08）规定授权串行与不泄露错误。a72c 的子进程登记/取消/回收所有权同样适用于补查 RPC。各 ADR 记录合成测试/浏览器证据，不能证明本次真实传输；没有 supports_websockets 的既有决策。方案扩展配额保留到 before，并保持上述安全约束。

Context7 `/openai/codex` 返回官方 model-provider-info/core 源码：supports_websockets 控制 Responses WebSocket，旧 feature flags 已移除。错误类型来源是官方 app-server-protocol TurnError/CodexErrorInfo；最终核部署 CLI 的实际配置和协议。

资源：实施主线程（R4，继承模型/effort）；行为与状态决定 L1，一名独立 reviewer 一轮；实现 V1，一轮 review-changes。有界账号操作 A1；已有部署授权覆盖服务变更。TDD 来源为已观察缺陷，不加全新框架。告警自检：点击触发、有界、原页面持久显示失败，无新增后台调度或 push 通道。

验收：成功/失败/未知/取消、已知/未知结构化错误、观测时间顺序与 null、跨操作/重启、同 workspace 不同邮箱、实际 turn/start 次数不增加。账号卡和配额表分别核实。记录真实版本、时间、消息结果、配额和耗时；单元测试与 live 证据分开。

## 执行状态

- 已完成：诊断记录、独立开发树、Context7 配置及协议查询。
- 已完成：决策审查、实际配置核验、定点修复和回归、变更审查。
- 已完成：MacStudio 部署、两个账号真实配额补查及页面验证。代码提交、本地 main 整合及隔离树清理的最终读数随交付说明。
- 未核实：WebSocket 底层故障点、lindong6 旧终态错误、真实 HTTP 发送改善。

### 决策与失败证据

独立决策审查七项通过（2026-10-11）；要求补查失败保留原 turn 失败、替换 RPC 遵循 a72c，均纳入实现。实际 Codex 0.162.1 拒绝覆盖 reserved `openai`；专用 alias 的 config/read 已读得 requires_openai_auth=true、supports_websockets=false、base_url=null、env_key=null。官方 model-provider-info 的 is_openai 按 name=OpenAI 判断，supports_codex_backend_routes 接受空 base_url；没有改用第三方服务。

部署 CLI generate-ts 提供 TurnError/CodexErrorInfo 结构；只保留已知枚举和整数 HTTP 状态，不保留 message/additionalDetails。红测试：账号测试 23 项中 9 个失败读数（含参数分支），Web 配额测试 1 个失败，分别命中缺失 before、恢复、结构化失败及传输配置。既有成功/身份拒绝等其余样本保持通过。

变更审查一轮通过，无 findings / 承重未核实项。账号相关 69 项测试和配额 Web 测试 1 项通过；reviewer 独立复跑账号 24 项及 Web 1 项通过。覆盖消息成功/失败/未知/取消四态、结构化已知/未知错误、operation/batch_result 两种保留来源、新旧观测及 null、补查成功/失败/身份拒绝/取消。真实 HTTP 消息耗时未测，不以合成测试替代。

### 部署与真实验收（2026-10-11 23:25 UTC+8）

MacStudio 在原 `2018322` 历史上应用已完成的子进程回收前置改动（`51310a3`）和本次修复（`e459642a59f7cc048050f0424e206cca2681cc55`），不带入其它本地历史。服务适配保留现有 QUOTA_REFRESH.shutdown；codex_accounts.py、web/app.js、server.py 均与本地已验证源码 SHA256 一致。部署候选通过 69 项账号、1 项配额 Web、2 项生命周期、7 项同步进程测试。远端既有配额测试仍把套餐读成第 2 列，修正为实际第 3 列后通过；未修改页面列序。

切换前账号 busy=0；快进后受控 restart 成功，health ok=true/stale=false，实例 `12da8f43ad7045aeb45b26f0368e0284`，Web signature `cf7a0ff812757d0d`。state symlink、目标及账号锁 inode 不变，重启前后批次记录完全一致。没有 Git push。

仅两个账号并发 quota refresh，一轮合计 4.272 秒：

| 账号 | 七天已用 | observed_at（UTC） | 下次重置（UTC+8） | 保留的原消息结果 |
| --- | --- | --- | --- | --- |
| lindong4@philoai.xyz | 0% | 2026-10-11T15:25:18.555059+00:00 | 2026-10-18 12:41:59 | unknown |
| lindong6@philoai.xyz | 100% | 2026-10-11T15:25:18.528920+00:00 | 2026-10-18 11:58:40 | failed |

两次 refresh_only=true、message_status=not_sent；原 operation 完整移入 batch_result，批次不变。独立 headless 浏览器经同一 Hub 的 Tailscale 地址实际展开账号面板及历史配额，两个消费者显示 0%/100% 和 23:25:18 查询时间；汇总为「本轮发送成功 9/11 · 结果未知 1 · 发送失败 1 · 下次重置时间已知 11/11」。配额操作显示完成不代表原消息成功。

证据目录：`/Users/lindong/.local/share/agent-monitor-diagnostics/20261011-account-round/`（deployment.json、live-before.json、quota-refresh.json、browser-cards.json、browser-table.json、截图）。这里记录的是一轮两个真实账号的配额查询，不是 HTTP 消息发送性能样本；没有重发任何消息。实际短消息端到端改善尚未验证；旧 lindong6 终态错误没有原始持久记录，无法补回；WebSocket 底层故障未定位，但短消息已改为绕开该传输路径。未处置代码 findings：无。
