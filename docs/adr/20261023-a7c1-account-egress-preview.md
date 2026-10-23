# 账号发送前的目标出口预览

2026-10-23。用户要求“开始新一轮”之前显示出口IP和地区，中国红色。独立L1决策审查一次，补齐公共诊断缓存交界后两问复核放行；未处置finding无。当前主线程实施（task-routing R4，继承模型），独立审查R14 gpt-6-astra/high。

发送前使用确认对话框，先向chatgpt.com与auth.openai.com各自的/cdn-cgi/trace做无账号凭据探测，通过产品共享系统代理。分别展示目标、IP、国家/地区、观测时间，CN红色并有文字标记。不是通用ip-api探测的外推，也不是未来连接出口保证，不声称覆盖CLI全部隐藏辅助域名。无重定向和自动重试，源站拒绝、挑战、非法返回或超时为未知；未知时保留用户明确继续的能力，中国红色不新增禁止发送政策。现有额度警告、账号选择/CAS和暂停不改变；取消预览不提交批次。

仅显式打开预览触发请求，不随账号数扩大，不在页面轮询自动探测。两个目标独立缓存成功和失败，合并并发，60秒内或更长Retry-After内复用，明确缓存与观测时间，无force绕过。每目标受控子进程总预算15秒，最多两个并行（固定目标只有两个），共享代理helper；没有凭据、账号RPC、持久业务数据写入或解暂停。预览也可在暂停时单独打开。

排除普通公网查询替代业务出口：PAC可能按目标分流；排除account/read探测：会使用真实账号且不提供IP回显；排除强制固定出口：改变系统代理语义且仍不能固定远端代理的出口。匿名trace可用性与未来路由稳定性尚未实测；真实验证需具体批准，默认隔离模拟。官方CLI0.162.1 model-provider-info/src/lib.rs的CHATGPT_CODEX_BASE_URL为https://chatgpt.com/backend-api/codex，login/src/server.rs的DEFAULT_ISSUER为https://auth.openai.com；项目Rpc强制ChatGPT认证并隔离HOME。Cloudflare的trace说明：https://developers.cloudflare.com/fundamentals/reference/cdn-cgi-endpoint/ 。域名不支持该端点时显示未知，不改用其他目标伪造确定结果。

验证覆盖CN/非CN/IPv6/非法内容/拒绝/超时、失败缓存与并发合并、真实页面取消0发送/确认1发送和冻结选择。部署只重启Web，保持所有账号暂停；代码与UI可回退，不改账号数据。

实施验收：89项相关测试通过；独立实现审查发现独立预览动态加载失败缺少提示（LOW，依附），补齐notice后6项UI动作测试通过。独立reviewer在隔离HOME实跑18项相关测试通过。真实隔离浏览器使用6个合成账号、2个目标，CN与US两种地区的计算颜色分别为rgb(196,43,43)与rgb(31,35,40)；取消提交0次，确认提交1次且包含原6个ID及fixture-old CAS，Escape不增加提交。2种宽度（1280、375）下查看对话框，375宽时无水平溢出；部分未知、整次失败与账号暂停时的独立预览可见。真实上游trace与账号RPC均未执行，不能据本地读数声称当前公网出口或账号可用。实现审查未处置finding与独立finding均无；上游可用性留为未核实边界。
