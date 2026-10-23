# 系统代理决策输入

## 权威目标与当前边界

用户 2026-10-23 原话：当产品通过 Web UI 给别人使用，所有产生的流量默认遵循和 Google Chrome 一样的代理规则，识别和使用系统代理，多代理 resolution 与 Chrome 保持一致。后续原话：现在就按照以上建议修复相关缺口，agent harness 和当前项目都需要修复，可以自行判断是否创建 plan。

原账号事故仍暂停；本修复不发真实账号请求、不重新登录、不清凭据、不解除暂停。用户授权普通提交、发布和部署。产品现有独立运行边界不允许重新依赖 ai-agent-config/system-config checkout。用户未要求改其个人 CLI 路由或浏览器代理；SSH/rsync 沿原协议配置，不能说 HTTP 系统代理覆盖非 HTTP 协议。

## 决策

采用产品自有的 Chromium system resolver（Electron session，mode=system）及仅监听回环、随机认证的 HTTP/CONNECT bridge；Python HTTP 客户端与 Codex 子进程统一指向此 bridge。HTTP 按完整 URL 解析；CONNECT 按 HTTPS origin 解析（对应 Chrome HTTPS PAC URL sanitization）。TLS 不解密、不新增CA。身份 HOME 保持隔离；子进程显式禁用自身二次系统代理解析，使其采用被注入的产品 bridge。环境代理和个人 current-proxy 不覆盖该 system 策略。

Bridge 按解析结果的候选列表连接，按固定运行版本 Chromium `CanFalloverToNextProxy` 的错误分类和发生阶段映射回退：包含代理DNS/TCP、特定代理TLS、SOCKS连接失败；区分不可回退的SOCKS目标不可达、认证、普通CONNECT拒绝和业务错误。源站TLS错误不是代理TLS错误。应用请求已发送后的未知失败不重放，不在无DIRECT候选时增加直连。解析空串、非法格式或未知协议报可诊断失败，只有显式DIRECT才代表直连。

Chromium负责系统配置/PAC解析与其内部更新，桥负责自己的socket和坏代理状态。每个新HTTP请求/CONNECT重新解析，不复用HTTP代理连接池；坏代理按Chromium的5分钟降序处理。系统代理/网络配置变化须使桥的坏代理状态失效，不能仅比较某个URL的候选字符串宣称覆盖配置变化；实现必须接入实际配置/网络变化观察并在同一长期helper上验证。已经建立的隧道继续到调用方结束或取消，不为切换出口打断并重放业务；helper退出关闭其全部socket。不同进程网络历史不共享，不宣称与用户某个正在运行Chrome profile逐请求IP相同。

本机bridge随机凭据只供本机消费者认证，转发前剥离Proxy-Authorization，绝不作为上游代理或源站凭据。resolver不提供浏览器代理密码；不读取/复制Chrome登录态或系统密码库。本次已确认的目标系统代理未配置认证，先支持无需认证的HTTP/HTTPS/SOCKS代理传输；遇407或代理要求认证时明确报上游代理认证未满足并停止，不自动换出口/直连或重放。此项不宣称支持Chrome全部代理认证机制；若实际运行必需认证，则作为本任务未完成项交用户裁决凭据来源与支持范围，不能以匿名测试宣布所有系统代理可用。

由产品管理 helper 的源码、固定版本运行依赖与生命周期；一个服务进程复用一个，子进程通过注入地址共享，短进程退出回收自己创建的实例。启动失败产生可诊断失败，不静默回到旧个人代理或直连。原账号执行预算、暂停和禁止业务重试不变。macOS为当前生产验收目标；Linux现有CLI/Web兼容需要核headless启动能力后再发布，不能用未测假设覆盖。

Harness增加一个共享产品网络策略正文，约束产品出站策略和实际运行入口验收，接到普通开发、服务与外部交互入口；不新增每次请求审批或LLM判官，不改个人CLI受管代理默认。当前shim已尊重显式respect_system_proxy配置，因此优先以产品显式配置及隔离回归解决，不为显式配置已可满足的行为修改全局shim。

## 备选与证据

1. 复制current-proxy或export HTTPS_PROXY：无法表达PAC、协议差异、绕过与有序多候选，重现当前分叉，否决。
2. 仅启用Codex respect_system_proxy及Python原生库：Codex系统解析只取单一supported候选且SOCKS能力受限，Pythonurllib环境优先，不能共同满足Chromium解析规则，否决作为完整方案。
3. CFNetwork/libproxy自建统一resolver：原生CFNetwork有序候选可用，但与Chromium的PAC、bypass、平台解释存在差异，且要自建兼容规则；相比复用真实Chromium增加语义维护，否决为当前首选。
4. 全部通过Electron net.fetch：可复用完整Chromium传输，但其公开API不提供原始CONNECT隧道；Codex采用此方案需TLS中间人或重写上游URL，改变认证/TLS边界，否决。
5. 开启TUN/改全局系统代理：影响用户其他程序，且不等于产品消费系统代理策略，否决。

官方来源：Chromium net/docs/proxy.md及net/http/proxy_fallback.cc（URL级有序候选、HTTPS PAC URL裁剪、按错误阶段和类别回退、坏代理5分钟降序）；Electron session.resolveProxy/setProxy(system) API。研究报告 `/Users/lindong/.codex/diagnostics/agent-monitor-system-proxy-options-20261023.md` 记录具体来源与未核实项。Context7已查询/electron/electron，确认resolveProxy返回代理字符串、forceReloadProxyConfig语义。2026-10-23受控实验：Electron45.0.0内含Chromium156.0.8078.48，与本机Chrome读到的版本一致；官方darwin-arm64下载120706020字节，SHA256 896c3a50b148e89a158dca08a6996a022ddf39b23aac871e107f5b4087f8caa9校验通过；解压477110077字节。无BrowserWindow的进程退出0、stderr0行；JS启动至解析完成55ms，主进程RSS124583936字节（不含其他子进程，非完整常驻成本）。system模式读到PROXY127.0.0.1:59527；隔离session固定HTTP/HTTPS代理分别返回50101/50102，通配绕过和回环返回DIRECT。未访问任何目标源站/账号；只是解析测试，未证明传输/长期运行或Linux。

当前直接证据：MacStudio部署7e36446，账号11个隔离HOME均无代理文件，运行server无六个proxy env或AGENT_PROXY_ADDR；ip-check正常HOME指定127.0.0.1:59527；主树代码hash与线上一致。此证据支持路径分叉，不证明历史公网IP或封号原因。

## 历史决策与语料

- agent-monitor docs/adr及architecture：检索proxy/代理，命中20261011-7c3a，只为账号app-server使用HTTP Responses，保留无base_url覆盖、端到端认证与禁止WebSocket；本方案兼容。20261012-b97e独立产品边界保留。
- system-config/bin/codex-proxy-shim注释与显式配置分支：个人CLI默认受管代理，用户显式respect_system_proxy让路；本方案不更改个人CLI默认。20260827-cf12 Gemini系统代理逃生只支持一致HTTP/HTTPS是历史能力，不作为Chrome等价依据。
- ai-agent-config docs/adr、claude/AGENTS.md、references、skills、commands、hooks markdown：按system proxy/系统代理/respect_system_proxy/current-proxy检索，命中20261015-48e2 Chrome Dev受管代理（当日观察59527/59528路径差异），仅自动化浏览器入口；产品后台契约不改变该入口。现有remote-command-execution、review-external-service-risk约束非交互环境与隔离验收，继续保留。

## 验证与风险

先建立原隔离HOME缺陷的失败回归；用真实进程、合成HTTP/CONNECT/SOCKS代理及模拟源站验证到达路径，Chromium resolver使用同版本固定配置/PAC对照；测试直连、不同协议、bypass、多候选失败、CONNECT拒绝不回退、配置变化、子进程继承、生命周期。不能只断言env或mock resolver证明Chromium解析能力。

尚未验证：Electron在本机/部署机无窗口运行、Linux headless、runtime占用、所有Codex内部HTTP消费者是否遵循注入代理、TLS代理失败分类。下一步先用不含凭据的受控实验消解，再实施依赖项；如果这些前提失败，返回设计，不缩窄用户目标冒充完成。

档位来源：用户明确Chrome resolution一致和跨项目修复是规范锚；当前真实路径分叉是失败证据。产品出站影响真实服务与认证流量，A2/V1，需独立决策/变更审查；测试仅覆盖本次代理保证与生命周期，不全项目扩张。依赖下载与每个服务的helper成本来自复用Chromium所需能力，生命周期复用与事件驱动减少启动和刷新；实际资源读数待取得，不编造性能承诺。

错误可在本地代理收包与故障用例发现；上线仍保持原pause且不真实账号验证。配置源码可回滚，已发请求不可撤回，因此不把部署当上游实测。具体依赖安装体积与故障面须在落实前核实；如出现用户拥有的新成本取舍则提问，技术细节按既有授权执行。
