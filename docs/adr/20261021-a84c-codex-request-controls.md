# Codex 账号操作的请求与恢复约束

Accepted 2026-10-21 after one independent decision review. Scope: the single Hub Manager. 用户要求按已讨论的建议修复，并明确本次只能本地验证、不能向服务端发送消息。本轮不部署、不重启生产、不调用真实账号、授权、消息或配额接口。

基线 `0d6cf26` 的身份检查强制刷新 token；发送前配额异常被忽略；所有发送失败和未知结果均启动新进程补查。此前查得的 lindong2 记录是本地 120 秒 timeout、结果未知，不能证明封号原因。此决策只约束本地编排，不宣称降低封号概率已经验证。

## 决定与释放条件

- 普通 `account/read` 使用 `refreshToken=false`，继续核验邮箱与工作区，由官方 CLI 管理必要的令牌刷新。
- 发送前配额请求失败或身份无法核验时停止。相同账号的 `ordinaryUsageAllowed=false`，或目标 Codex 桶已知 `rateLimitReachedType`、`spendControlReached=true`，均禁止发送。百分比、时间经过不证明拒绝或恢复。
- 明确认证、额度、限流及 HTTP 401/403/429 不自动补查。即使未收到 `turn/completed`，同一 thread/turn 的 `error` 通知在 `willRetry=false` 时也保留拒绝依据并暂停，消息仍可为 unknown；不把其他 thread/turn 的通知归给本轮。只有未知结果或传输故障且没有明确拒绝时允许一次配额补查；不重新授权、不重发消息，补查失败不覆盖发送结果。
- profile 保留独立的 authentication、quota、rate_limit、login_rate_limit 原因及创建时间，作用域均是该 profile。新轮仍检查它们。authentication 仅由明确清除登录态解除，下一次仍须重新授权；quota 仅由显式配额查询得到 `ordinaryUsageAllowed=true` 且无拒绝状态解除；未知许可继续暂停。rate/login 暂停由独立本地 `release-rate-limit` 动作解除，只允许之后另行尝试，不证明服务恢复，不发起 CLI 或网络请求，不清 auth/quota，不改原消息或批次结果。
- 初始及恢复 RPC 在同一 Manager 内共享至少 2 秒的启动间隔，等待可取消，保留八个 check/send slots 和一个授权 lane。2 秒是错开启动的本地策略，九次启动至少跨 16 秒，不是 OpenAI 限速或安全线，不覆盖 CLI 内部 HTTP 请求、重试或其他程序。
- 保留安全 thread/turn 标识、最后协议进度和结构化错误。无协议支持的 request ID / Retry-After 不臆造，不存原始消息、headers、token 或输出正文。已知 turn ID 的 timeout 尝试 `turn/interrupt`，最多 2 秒后仍由原 owner 回收进程，消息结果保持 unknown。
- 页面区分查询、发送、暂停与本地解除；发送是实际消息，不保证固定或重置额度窗口。

## 依据、备选与边界

协议依据为本机已缓存 Codex rust-v0.162.1（`092d3acd6bec3e3a14bdc7e7a2810ab628ab759d`）的 `app-server-protocol/src/protocol/v2/account.rs`（ordinary usage、spend control 与 reached type），以及本轮 Context7 对官方 `account/read` 与 `turn/interrupt` 的查询。运行中的 CLI 版本与实际服务行为本次不探测。

仅延长 timeout 不消除无用请求；对所有失败补查会在明确拒绝后继续请求；随机消息、伪装客户端或换身份没有本次因果依据；全面禁用正常发送超出目标。独立本地解除限流暂停，避免为解除本地状态额外强制查配额，或把 usage 许可误称为 HTTP/login 限流恢复。

权威语料为本仓 ADR、issues/general、README 与 architecture，检索 refreshToken、rate limit、429、恢复、并发、Codex、超时。定向 supersedes [7c3a](20261011-7c3a-account-round-recovery.md) 的统一补查以及 [f7c1](20261008-f7c1-codex-authorization-queue.md) 中已限流 profile 无本地暂停的直接重试。保留 c814/b923 的身份、批次与不重发，e92b 的观测保留及 a72c 的进程回收。历史 ADR 的合成验证和真实观察不能证明本次实现。

沿用原子 profile.json、per-profile flock 与当前/保留批次各一个结果；独立限制原因数有固定上限，不建立增长日志或新数据库。失败仍在用户发起操作的页面持久可见，无新增无人值守任务或 push 告警。行为/安全策略 L1 一轮决策审查，全部七项成立；实现采用一名独立 reviewer。只运行隔离 HOME/state、固定本地 stdio peer 的验证，并通过 OS 沙箱禁止非本地网络。

已知未验证：封号因果、真实供应商容量、真实 CLI 内部重试和风控收益；若服务端持续返回 usage 许可未知，quota 暂停继续存在，不伪称封号。实现和验证结果记录在交付及当前文档中，不以本决策代替完成证明。

## 本地验证与交付边界

2026-10-21，独立实现审查发现一个中等问题：明确拒绝通知之后缺失完成通知时，原实现仍按进程退出补查。已先用三个拒绝样本复现，再修复；另一 thread 的通知作为不应暂停的对照。消息结果保持 unknown，拒绝原因独立保存。其余未处置 findings：无。

131 项相关测试通过，涵盖四类明确拒绝、四种发送前停止场景、正常发送、未知结果、超时、重启、两类本地解除、同源 HTTP、批次及页面行为。全部在隔离 HOME/state、固定本地 stdio peer 下执行；macOS 沙箱禁止非本地网络，非本地测试地址的连接被 PermissionError 拒绝，本机 HTTP 作为可连通对照。启动间隔用 0.08/0.12 秒的本地计时测试覆盖初始与补查，页面模拟批次另外实测默认 2 秒间隔。没有验证供应商真实请求容量。

复跑相关测试（从仓库根目录执行；私有 worktree 可将解释器替换为主 checkout 的 `.venv/bin/python`）：

```sh
/usr/bin/sandbox-exec -p '(version 1)(allow default)(deny network*)(allow network-inbound (local ip "localhost:*"))(allow network-outbound (remote ip "localhost:*"))' .venv/bin/python -B -m unittest tests.test_codex_request_controls tests.test_codex_accounts tests.test_codex_batches tests.test_codex_account_http tests.test_codex_discovery tests.test_upstream_account_contracts tests.test_account_lifecycle tests.test_codex_account_time tests.test_ui_actions tests.test_web_static
```

`tests/fixtures/codex_accounts_ui.py` 提供最多存活 600 秒的本地页面模拟器，使用真实账号面板模块、HTTP 路由和 Manager，但账号、认证数据及 stdio peer 全部合成，未启动生产采集器。用同一沙箱参数运行该文件，访问它打印的本机 URL；`/fixture/stats` 仅展示模拟调用计数，`POST /fixture/stop` 结束进程。浏览器验证覆盖 1280px/390px 两种宽度、四个合成账号：限流本地解除产生零 peer/请求；额度刷新不发送消息并按明确许可解除暂停；下一批三账号各发送一次模拟消息，认证暂停账号零请求；刷新页面仍保留结果，窄屏详情可展开且没有横向溢出。浏览器原生 confirm 对话框工具阻塞，验证中以测试页 confirm stub 接受操作并检查完整确认文案，未宣称原生弹窗交互通过；批次确认取消分支由既有本地 Web 测试覆盖。

交付仅保存在本地独立分支。主 checkout 被本机 launchd 定时采集直接引用，因此本轮不整合主目录、不 push、不部署或重启服务，避免开发结果进入运行环境。真实 OpenAI 授权、消息和配额接口均未调用；上线与真实账号验证留待用户另行安排，不能把本地测试视为封号风险已消除。
