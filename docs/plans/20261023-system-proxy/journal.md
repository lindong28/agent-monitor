# Journal — Web 产品系统代理

## 2026-10-23 [decision]

用户明确授权当前项目与harness缺口修复。决策review原生独立agent `/root/review_system_proxy_decision`：首轮F1错误分类、F2桥状态失效、F3两跳认证隔离应修；补契约后两问复核放行，无新问题。不能用全部实现尚未完成否决方向，也不能用方案放行证明交付。ADR20261023-9c4e承接。

## 2026-10-23 [fact]

Codex0.162.1系统解析只保留首个supported候选、SOCKS unsupported；仅改respect_system_proxy开关不满足多候选。Electron公开net API不提供raw tunnel；resolveProxy空字符串可能代表失败，不能当DIRECT。Chrome HTTPS PAC剥离path/query，CONNECT origin够用；现有产品明确HTTP/noWS，通用WSS不能靠CONNECT猜scheme。

## 2026-10-23 [fact]

无账号Electron实验只解析，不请求源站：下载120706020字节sha已核、解压477110077字节，主RSS124583936字节、JS启动到解析55ms，退出0且无stderr。system返回127.0.0.1:59527；fixed HTTP/HTTPS分别50101/50102、通配bypass/loopback DIRECT。这不是传输、Linux、完整常驻成本或真实业务验收。
