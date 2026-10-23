# State — Web 产品系统代理

父目标：产品HTTP(S)请求默认遵循执行主机Chrome系统代理解析；当前项目与harness均修复。整体尚未交付。

## Tasks

- [done] 取证与独立决策：ADR20261023-9c4e；研究和review在 `/Users/lindong/.codex/diagnostics/agent-monitor-system-proxy-{options,decision-review}-20261023.md`。
- [in_progress] 产品resolver/bridge/生命周期、各HTTP消费者接入：先完成隔离代理/源站验证；不发真实账号请求。
- [done] Harness共享产品网络策略及入口已独立审查、提交eea33d17c3386fcd975e62d68d53d31c58f4b066、快进整合及发布，远端main回读一致；两侧已安装AGENTS解析到共享文件。保留个人CLI/Chrome Dev默认，不宣称未来遵循率。
- [pending] 网络页面/CLI说明探测目标、同策略与不同目标出口边界，实际UI验收。
- [pending] 必要回归、独立变更审查、提交整合push、MacStudio部署；部署来自主树实时origin/main，保持账号暂停，分别报告上线与上游未验证。

## 当前执行与开放项

session `01a165ef-51d4-7340-bc3d-feae991b6917`，root PID9171。产品私树与harness私树见plan，均持续占用；产品token aa210a5bac1f4b5e9a76fec25209261c，harness token f6dc71431702439395022f2d6d61f568。普通写入在这两树，保留主树和旧恢复功能树WIP。

以下为实施历史；最新状态以末尾发布记录为准。产品与 harness 实现及必要文档已完成。Electron45.0.0/Chromium156.0.8078.48 在私树 .runtime。真实 Chromium PAC/固定代理对照、Node 10 项 socket 子测试、Python 消费者与账号诊断合计10项通过。全量1123项耗时111.587秒，13项失败；基线7e36446隔离重跑对应19项，同样13项失败，属于既有跨机器状态/前端fixture问题，保留未修。基线证据 /Users/lindong/.codex/diagnostics/agent-monitor-system-proxy-baseline-tests-20261023.log。页面真实浏览器验收含当前/旧记录、390x844无横向溢出，浏览器已关闭。

开放：独立V1实现审查交回4项，均接受待修：HIGH代理TLS protocol_version失败未回退；MEDIUM本地网络前置失败分类、exporter运行身份遗漏proxy_runtime、安装账本非法state。修后做HIGH两问复核。实际Codex消费者、MacStudio部署及账号暂停保留读数待取得；真实系统配置通知端到端尚未核实。

更新：上述4项已修并复验，先取得5个预期失败读数，再跑 network_policy/account_read_diagnostics/quota_refresh/exporter/install 共70项全部通过19.969秒。原reviewer定向4项通过0.539秒，但新增HIGH：本次const proxyTls=secure把TLS1.2客户端证书认证失败作为可回退，Chromium拒绝且备用到达0，桥返回200且备用到达1。新finding 1/1来自本轮修复，触发review-changes修复轮预算；产品停写、未提交、未部署，待用户裁决是否继续定点修复。拟撤回泛化回退，只对能区分的协议/证书校验错误回退，认证或歧义握手错误不换出口；须同时保留protocol_version正例和客户端证书反例。尚未实施，不得当作已验方案。reviewer /root/review_system_proxy_implementation 已结束，报告在会话回传，无独立finding。

用户通过MCP明确选择“继续定点修复 (Recommended)”（本session functions cell167）。授权一轮定点修复、Chrome正反对照及一次定向复核，通过后沿原授权发布部署；不扩全审、不调用真实账号、不解除暂停。产品恢复上述限定工作。

最终定点修复已完成：恢复明确错误集合，protocol_version可回退；TLS1.2/TLS1.3客户端证书拒绝都无备用到达，真实Chrome正反对照先红后绿。原reviewer两问复核通过0.411秒，无新增finding或承重未知；最终70项受影响回归通过19.218秒。进入提交发布/部署，产品当前尚未上线。

真实消费者补证：有效格式的synthetic JWT后，实际产品Rpc启动真实Codex，在OS sandbox禁止非loopback网络条件下，本地拒绝代理收到10次CONNECT，account/read最终discoveryUnauthorized；证明该CLI确实采纳传入代理，也说明一个RPC可有多次底层连接，不是10次真实账号请求。无外发，子进程已关闭。requests/urllib两客户端、127.0.0.1/localhost两主机名共4次实际HTTPS请求通过，源站Proxy-Authorization泄露0。SystemConfiguration纯通知调用返回false、generation 0→0，未改真实配置，原生变更端到端仍未核实，不当成通过。

额外真实Codex隔离探针：产品Rpc启动真实CLI，synthetic HOME/token，macOS sandbox禁止非loopback网络；initialize成功，account/read未产生本地代理CONNECT（0次），因此不能证明实际Codex网络采纳，仍未核实。临时源站和CLI已回收。

Harness发布器退出2，报告远端包含目标但操作期间WIP发生变化；已单独回读实际origin/main及本地HEAD均eea33d17，5个任务文件逐字节匹配commit，两条已有日志删除保持，codex/config.toml SHA256仍c94a82e41cd10d9993bce87756937f605c4418475fe03aefdc12f2c389f98867。整合时guard确认tracked WIP及untracked路径保留；发布期间具体动态未跟踪变化未定位，不擅自恢复/删除。没有本地CI配置，未宣称外部CI通过。

Linux Ubuntu24.04 在远端自有 /tmp/agent-monitor-system-proxy-probe-01a165ef-51d4-7340-bc3d-feae991b6917 解压runtime和apt包，未安装系统包/改配置。补齐依赖后，仅实验 --no-sandbox 可启动，生产不可使用该flag；目标机还需系统库与Chromium sandbox部署前提。Linux观察器启动/关闭通过，未修改真实代理配置。此临时目录待清理。Mac短样本冷启动含ps 230ms，3进程RSS合计225936KiB；下载120706020B、安装477110077B，非长期成本分布。

MacStudio当前Python3.9；Codex暂停f23c058162ab49d88adfdace97c1be13/cancelled，Claude暂停9807013aff124fa8ab5bd36a8f1ffa63/quotaRefreshFailed；7账号busy0。部署计划 --no-services 安装后仅重启Web，不启动collector或解除暂停。旧恢复功能WIP不纳入。

账号暂停最后确认id `f23c058162ab49d88adfdace97c1be13`；不得因为重启或新HOME解除。原account/read错误证据与未发布重新授权WIP保持原归属，不纳入代理提交。

## 发布与上线记录（恢复锚点）

产品主体51df8ed267ebbfd85d2179b56023244e1e2422c6已提交、整合、push并在MacStudio通过deploy.py --expect部署；--no-services，随后仅重启Web。Python3.9、Electron45/Chromium156启动检查成功。实际 /api/health?asset_watch=1 为ok且stale=false；网络探测成功，执行主机macstudio，system策略，本次ip-api.com出口70.231.54.232。这不代表账号目标出口。7账号busy=0、暂停保留；未执行真实账号请求。Linux自有临时探针目录已删除，未修改系统包或配置。

上线发现proxy_envs会输出内部桥能力URL，现已定点脱敏：隐藏内部URL，其他代理只保留scheme/host/port。23项相关测试通过，独立V1复核5类输入通过，无新增finding。该补丁待提交发布、重启以失效旧能力、真实API与页面复验。此处覆盖前文“产品未上线”、Linux目录待清理及早期无效Codex探针的状态；有效synthetic JWT探针才是CLI消费者证据。

剩余已知边界：全量既有13失败不在本修复范围；原生系统配置变更端到端未实测；Linux生产sandbox前提未完成；歧义TLS认证错误保守终止，不宣称所有Chrome传输错误完全等价。详见ADR和上述证据。本任务未修复账号封禁或执行重新授权。
