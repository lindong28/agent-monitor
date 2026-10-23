# 浏览器设备作为批次出站主机

2026-10-23 用户要求只改变代理规则所在机器，从服务端改为浏览器所在设备，随后授权实施。保留 Chromium system 解析、账号暂停、发送前出口展示及 CN 红色确认。独立决策审查一轮、两次针对 F1 的补证复核后放行；没有未处理 finding。

采用前台 `agent-monitor connect --server URL`，复用既有 Python/Electron 运行时。通过回环 popup 精确绑定页面与设备，控制能力与浏览器操作能力分离；凭据留在服务器，上游 TLS 不解密。hub 仍只适用于可信私人网络；HTTP 控制面没有额外加密，HTTPS hub 使用 TLS。本机连接断开后停止通道，不切回服务器或另一设备。具体使用入口见 README，运行契约见 architecture。

与替代方案相比：复制代理配置不能表达客户端的 DIRECT、回环代理或 PAC 上下文；纯网页代发需要适配现有 Codex 原始 TLS/RPC 传输和跨源读取，并非已证明所有免安装方法都不可行；SSH 反向转发要求用户具备相应服务端登录条件；扩展加 native messaging 增加浏览器扩展部署。本方案依赖浏览器设备安装产品客户端，不将这一前提隐藏成网页自动读取系统设置。

审查发现初稿仅放行两个预览域名会改变基线功能。官方 Codex 0.162.1 固定提交 `092d3acd6bec3e3a14bdc7e7a2810ab628ab759d` 的 `app-server/src/request_processors/account_processor/workspace_routing.rs` 接受动态 HTTPS workspace origin；`model-provider/src/workspace_routing.rs` 替换业务主机及端口，测试包含 `custom_port`。因此撤回未实施白名单，保留合法 CONNECT 目标范围；浏览器不能直接建立任意目标通道。`model-provider-info/src/lib.rs` 的默认业务地址、`login/src/auth/manager.rs` 刷新地址及 account processor 的额度地址支持常规默认目标，但不证明两个目标覆盖全部未来连接。源码来源：[固定版本](https://github.com/openai/codex/tree/092d3acd6bec3e3a14bdc7e7a2810ab628ab759d/codex-rs)。

出口缓存仍为每设备/目标至少60秒，并尊重 Retry-After；用户未答复的强制刷新选项不被当作取消此保护的批准。确认回执绑定本轮选择和设备；生产与回归证据记在本任务计划。单独登录、配额和公共网络诊断保持服务端规则并在界面说明。本次用户修订只覆盖批次，没有把跨项目 harness 默认扩大为客户端代理。

初步隔离证据：两设备通过两个不同受控代理双向传输262144字节，关闭其中一路后连接拒绝而另一路有效；浏览器能力访问控制接口的3种动作均403。HTTP无回执/选择变化/回执重用均拒绝。真实 headless Chromium 从 `http://macbook` 页面完成回环配对；CN IP与警告 computed color为 `rgb(196, 43, 43)`，取消0提交，确认1提交，预览API失败禁用发送，部分未知有明确选项。浏览器点击配对时自动化工具回执超时，但随后页面配对状态与预览动作成功；未将该工具退出状态算作动作成功。未运行真实供应商trace或账号RPC，未证明未来业务出口恒定。
