# Web 产品按执行主机系统代理选路

Status: accepted, 2026-10-23。用户明确要求当前产品和 agent harness 同时修复：Web UI 产生的流量默认采用系统代理，多代理解析与 Google Chrome 一致。独立决策审查一轮、三项应修经两问复核消解后放行；这不代表实现或部署已经通过。

## 事实与选择

7e36446 的账号子进程隔离 HOME 后没有个人 current-proxy 文件，MacStudio 服务进程也没有代理变量；ip-check 则读正常 HOME 的 current-proxy，额度和 urllib 各有自己的选路。此证据证明路径分叉，不证明历史请求公网IP或封禁原因。原账号暂停保持，不用真实账号验收本修复。

选择产品自有 Electron Chromium system resolver，加只监听回环、随机认证的 HTTP/CONNECT bridge。Python HTTP 客户端和 Codex 子进程消费同一桥；账号HOME继续隔离，Codex显式关闭其二次系统解析以消费桥。HTTP按完整URL、HTTPS CONNECT按origin解析，后者符合Chrome的HTTPS PAC URL裁剪。保留端到端TLS，不做MITM、不新增CA、不复制浏览器登录态。SSH/rsync仍按原协议配置。

复制环境变量/current-proxy不能表达PAC与有序候选；Codex0.162.1原生系统解析只返回单一支持候选，且不支持SOCKS；CFNetwork/libproxy需另实现Chrome语义；Electron net公共接口没有原始双向隧道socket，不能直接替代Codex传输；全局TUN会影响其他程序。因此在已调查候选中复用Chromium解析满足目标且保持现有认证边界。

## 必须兑现的边界

- 按固定Chromium版本的proxy_fallback.cc及错误发生阶段映射回退，区分代理建连/TLS/SOCKS故障与目标不可达、认证、普通CONNECT拒绝和业务错误。业务已发送后的未知失败不重放。空串、非法格式和未知协议不能变成DIRECT；无DIRECT候选不得自行直连。
- Chromium维护系统/PAC解析；桥维护其socket与5分钟坏代理降序表。新HTTP请求/CONNECT重新解析。系统/网络变化必须使桥自有状态失效，不能仅比较一个URL的候选字符串冒充配置世代观察。既有隧道继续到结束或取消，不为切换出口打断重放；退出清理全部自有socket。
- 本地Proxy-Authorization只在桥认证时使用，转发前剥离。解析器不提供上游代理凭据；不自动读取密码库。未满足的上游认证明确失败，不换出口、不直连。当前配置未声明认证；如果实际必需认证而实现不能支持，该项仍是未完成工作，交用户裁决凭据来源和支持范围，不能声称所有系统代理已可用。

TLS实现边界：Node将TLS1.2客户端证书拒绝折叠为一般握手失败，无法仅凭该错误还原Chromium的认证状态。桥只对明确的代理证书校验和协议版本错误回退；含认证歧义的握手失败停止，不换出口。受控Chrome对照覆盖protocol_version回退及TLS1.2/TLS1.3客户端证书拒绝不回退；这不构成全部TLS错误的等价证明。2026-10-23用户已批准定点修复与该正反对照，未批准放行认证失败换出口。
- 一服务复用一helper，后代共享；短进程回收自己创建的helper。启动/解析失败可诊断，不回到旧的隐式选路。网络页报告探测目标与时间，查询站点IP不外推业务目标IP。

## 验证与代价

本机无窗口短时解析实验：Electron45.0.0/Chromium156.0.8078.48，与本机Chrome版本相同；HTTP/HTTPS分流、通配绕过及回环解析正确。下载120706020字节，SHA256校验通过；解压477110077字节；JS启动至解析55ms，主进程RSS124583936字节，不含辅助进程且不是长期成本。没有访问目标源站或账号。

实施期必须验证实际传输与子进程、代理回退/拒绝、配置变化、生命周期、部署机和Linux运行条件；未验证不能由上述解析实验代替。实际连接使用受控代理/源站；真实账号请求仍不在本次默认验证范围。保留20261011-7c3a的HTTP Responses/no-WS、20261012-b97e的独立产品边界，以及个人CLI和受管Chrome Dev既有默认。

设计输入及来源见[决策输入](../plans/20261023-system-proxy/decision-input.md)，执行状态见[计划](../plans/20261023-system-proxy/plan.md)。本ADR不把未发生的收益或部署写成事实。
