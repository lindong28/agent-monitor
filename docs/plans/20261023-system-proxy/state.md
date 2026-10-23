# State — Web 产品系统代理

状态：本次授权的产品与 harness 修复、发布、MacStudio 部署及无真实账号的验收已完成。以下边界仍保留，不宣称账号封禁已解决或所有协议完全等价。

## 交付与使用位置

- 产品主体51df8ed267ebbfd85d2179b56023244e1e2422c6及诊断脱敏补丁89b5c6da617888e1b961df4ee53baa48fa3bd4e5已整合、push，远端main回读一致。MacStudio主树在89b5c6d通过deploy.py --expect部署；--no-services，随后仅重启Web。最终文档收尾commit不改变运行代码。
- Harness eea33d17c3386fcd975e62d68d53d31c58f4b066已整合、push；5个任务文件逐字节匹配。~/.claude/AGENTS.md与~/.codex/AGENTS.md解析到共享权威文件，未修改个人CLI或受管浏览器默认。
- MacStudio实际服务 /api/health?asset_watch=1 返回ok=true、stale=false，新instance fbfe40036c74414282c25057d9c137c8。Python3.9、Electron45.0.0/Chromium156.0.8078.48启动检查通过。
- 实际网络API成功：system策略，执行主机macstudio，目标http://ip-api.com/json/，本次IP70.231.54.232，errors=[]。代理环境报告为空，不含内部桥URL或代理认证信息；重启后只观察到14:06启动的新桥进程。未取得旧能力URL的失效请求读数，不将进程更替当作该请求测试。
- 实际浏览器读取网络页并点击“重新检测”；页面明确显示“本次探测IP”、目标、执行主机及不能据此确定账号请求IP的说明。自动化调用须保持相同proxy-bypass参数；不一致参数时会读回about:blank，此异常已通过一致参数消除。任务浏览器已关闭。
- Codex暂停f23c058162ab49d88adfdace97c1be13/cancelled，Claude暂停9807013aff124fa8ab5bd36a8f1ffa63/quotaRefreshFailed，重启前后完全一致、active=false。账号API共7个、busy=0、upstream_pause存在。本轮不触达真实账号、不清凭据、不重新授权。

## 验证与审查

- 受影响network_policy/account_read_diagnostics/quota_refresh/exporter/install共70测试通过19.218秒；脱敏后的ip_check_egress/ip_check_json共23测试通过0.008秒，加载来源为本任务私树，提交blob核对一致。
- Node 10 socket场景覆盖HTTP/CONNECT、顺序回退/坏代理缓存、拒绝不换出口、SOCKS、证书与诊断隔离；真实Chromium对照9组，含PAC/固定配置、绕过、重载、TLS协议错误可回退及TLS1.2/1.3客户端认证拒绝不回退。
- 真实requests/urllib两客户端、127.0.0.1/localhost两主机名共4次HTTPS请求，源站Proxy-Authorization泄露0。真实Codex使用synthetic JWT/private HOME及禁止非loopback网络的OS sandbox，本地拒绝代理收到10次CONNECT，account/read拒绝；无真实上游请求，证明CLI采纳代理，同时说明一个RPC可能包含多次底层连接。
- 独立决策与实现审查完成。一次修复引入TLS认证回退HIGH后按预算停写；用户明确选择“继续定点修复”，授权一轮定点验证及复核。恢复明确错误集合后，原reviewer复核通过、无新增finding。诊断脱敏5类输入独立复核通过。当前未处置的本次审查finding：无。
- 全量1123测试中13失败；基线7e36446隔离重跑相应19项仍13失败，属既有跨机器状态/前端fixture问题，本任务不改。证据：/Users/lindong/.codex/diagnostics/agent-monitor-system-proxy-baseline-tests-20261023.log。未宣称全量通过。
- 两仓无本地CI配置；发布器未核外部服务器配置，不宣称CI通过。

## 已知边界与归属

- 账号封禁原因、历史实际出口、真实账号上游成功均未核实。本轮授权验收排除真实账号请求；解除暂停、重新授权或上游复测需单独具体方案，归后续账号处置，不属于本次代理修复完成条件。
- 原生系统代理配置变更端到端未实测；SystemConfiguration纯通知调用返回false、generation 0→0，不是通过。观察器启停、Chromium重载及路由缓存失效已覆盖；以后变更实际系统配置时由维护者补采。
- Linux Ubuntu24.04仅隔离解压探针：系统库及Chromium sandbox部署前提尚未满足。实验--no-sandbox启动不计生产验收，产品禁止该flag。本次部署目标MacStudio已验收；Linux部署前由部署者补齐前提。
- 歧义TLS认证握手错误保守终止，不宣称全部Chrome传输错误完全等价。SSH/rsync按独立协议边界保留，不由HTTP代理覆盖。以上长期约束见ADR20261023-9c4e。
- Mac短样本冷启动含ps为230ms，3进程RSS合计225936KiB，下载120706020B、安装477110077B；不是长期成本分布。
- Harness发布器曾退出2报告WIP变化；单独核远端与主树一致、任务文件一致、原有两条日志删除及codex/config.toml摘要保持。具体动态untracked变化未定位，未擅自恢复/删除。

## 保全与清理

session 01a165ef-51d4-7340-bc3d-feae991b6917。产品与harness私树仅包含本任务提交；旧account-read-diagnostics树的重新授权WIP及各主树无关WIP保留。远端Linux自有/tmp探针目录已删除，没有安装系统包或改配置；任务浏览器与本地测试进程均已回收。忽略的本地runtime/测试缓存属于可再生验证资产，不纳入Git。
