# 产品 Web UI 改进 · 2026-10-15

状态：六项实现已发布并完成下述生产抽样验证。用户要求“现在就按照以上建议进行改进”，允许按需要建 plan。输入为 docs/product-spec.md 与 docs/references/product-capability-review-20261015.md；六项建议全部纳入，状态摘要同时覆盖同步面板占屏和原始异常。排除项用户未另外明确给出；项目既有边界仍生效，不修改计费算术、账号动作或采集节奏。

## 交付与安排

1. 接通已有数据：新增未知成本条目指标，透视按模型/目录/机器分组，进入只含未知成本事件的保留会话；详情仍显示完整保留记录。周期比较链接保留对应 custom 日期。
2. 改善读取与操作：共用兼容 HTTP 的复制函数；增加余额多优先；窄屏用同一表格的纵向账号块呈现七字段，桌面仍按列比较；同步状态默认紧凑，摘要点名需关注机器，展开所有卡片；原始异常放技术详情并给基于结构状态的下一步。
3. 验证与交付：已有缺陷先补失败测试；新指标/筛选与排序跑相关测试及代表性新用例。隔离 HOME/state/端口，用真实前端验证；适用实现审查、文档同步、commit、本地整合，按常设授权推送并 ./deploy.py 部署实时 origin/main，再核真实 Hub。

实现由主线程处理（task-routing R4 current/inherit），跨文件状态相连，不拆共享 app.js 写入。独立决策/实现审查取原生 collaboration（R14 gpt-6-astra/high），不使用 wrapper；实际模型生效值以工具回执为限。代码树 /Users/lindong/research/agent-monitor-ui-improvements-20261015，分支 feat/product-ui-improvements-20261015，基线 0ff973409e8ace56265cddb54fab6cd0a17cedd7，由本 session 回收。证据保存 /Users/lindong/task-artifacts/agent-monitor-ui-improvements-20261015/。

## 决策 packet

- 决策：复用聚合计数新增 unknown_cost 指标和 Sessions cost_status=unknown 事件筛选；移动端纵向呈现七字段；机器异常留在紧凑摘要、卡片手动展开，不再自动撑开首屏。HTTP复制、日期链接及余额排序执行已授权方向。
- 权威目标：用户上述原话及上一轮交付六项建议；docs/product-spec.md 六任务；docs/contracts/ux-contract.md 成本未知/零、G1/G2/G4 账号身份、原值与新鲜度。观测不保证账号可用。响应式呈现与异常自动展开约定随此次已授权改进同步更新，不扩大数据或账号权限。
- 备选：独立未知定价报告重复分组与会话；拉全明细后聚合会丢失历史 rollup 覆盖并增加 I/O，故复用计数。仅调列宽/提示横滑不能同时读窄屏关键字段，故改窄屏呈现。把同步面板移到页尾会远离覆盖说明，故保留顶部摘要和现有汇总覆盖提醒。
- 证据：上轮报告有两个HTTP复制失败、390px表334/1040宽度、同步展开KPI y847、无未知筛选/周期链接的真实读数；copyGuideCode已有HTTP回退；rollup.query_grouped已有 entry_count/cost_known_count；server.sessions已有分组前事件过滤。
- 作用域：五页共用同步状态、总览配额/周期链接、Explore/Sessions未知成本路径。unknown_cost = entry_count - cost_known_count，不新增DB schema；cost_status=unknown 只接受 cost_usd is None，已知零不算未知。未知包括缺价格或用量不完整，页面明说；历史聚合与保留明细范围不等同。
- 既有决策语料：本仓 docs/adr、docs/contracts/ux-contract.md、docs/design.md、docs/issues/ux-issues.md；检索 quota、remaining、sync-panel、自动展开、未知定价、copy/clipboard。2026-10-07 b45f 保留七列表格，已有390px横滚读数；2026-10-14 design/Ark应用保留任务结构，已有1440/390检查；G1/G2要求身份/时间可读，G4c要求表列对齐。窄屏改为本次授权的响应式例外，桌面及数值语义不变。计数/归属/归档边界不override。
- 既有实测：上轮报告及旧issue 5453、31c8、a1c2；旧读数不作当前服务健康证据。
- 未验证：新路径未实现、窄屏新布局未观察、复制实际内容待验；账号副作用未被改动，验证不主动发送/授权/删除。
- 档位：可回退UI/查询派生 A1/V1，scope明确且有既有identity/未知成本契约；L1因改变可见行为与URL参数，单reviewer一轮。新增测试依据真实失败及功能要求，不加全量视觉矩阵或新契约体系；生产发布沿已有授权。

## 成本与验证边界

新计数在既有 _daily_rows 遍历内常数计算；会话筛选在既有已读事件上判断，不额外请求详情。排序只处理已加载账号，布局复用DOM。无新扫描、后台任务或采集。仍有既有全范围汇总/会话读取成本，不声称整个后端优化。

最小验收：HTTP复制成功/失败；已知零/未知混合分组；unknown/all会话可分且详情完整；两期日期导航保留；余额多/少与未知末尾；390/1440下身份+额度+时间可读；同步正常/失败摘要可分且不自动挤压KPI。隔离合成状态与上线消费者读数分开记录。

## 进展

- 已完成：上轮真实取证、当前源检查、独立树创建。
- 决策已放行：独立 reviewer 七判据成立；见 docs/adr/20261015-9e31-product-ui-handoffs.md，补充保留 a7c2 的 Sessions 严格总额规则。计划其余安排由主线程自查，不增加计划外审轮。
- 已验证：HTTP复制回归 RED（实际值 undefined）→共用回退后 GREEN；guide成功/失败兼容测试通过。余额多优先及未知末尾定向测试通过。同步自动展开新回归先取到 RED。
- 六项实现与生产抽样验证已完成，具体范围及保留边界见下方交付记录。

## 实现与验收记录

六项均已实现：复制 ID 共用 HTTP 回退、配额余额多优先、≤900px 七字段账号块、紧凑同步摘要及技术详情、未知成本追查、两期日期链接。未知条目计数保留 `entry_count` 口径，不是付费 attempt 总数；Sessions 严格总额及完整详情不变。

相关 unittest 七模块 106 项通过（2026-10-15，私树源码、主仓解释器）；HTTP 复制和同步自动展开回归先取得 RED。已知零/未知、过滤匹配/不匹配、2 个机器来源有区分样本。测试报告有已有临时 SQLite ResourceWarning，未改其连接生命周期，不作为本次失败。测试未访问真实 provider 凭据。

独立实现审查发现窄屏 grid 覆盖未知分组关闭态的 HIGH，修复后 reviewer 定向复核 390/1440 × closed/open/closed 通过；新增及未处置 findings 无，承重未核实项无。另为未知分组的高用量摘要显式标明 5h/7d，避免移动布局依赖桌面列位置。

真实隔离浏览器使用本次完整 HTML/JS/CSS、合成 API 与真实 rollup/Sessions 计算。深查：总览未知链接、两期比较、同步摘要/卡片/技术详情、账号排序、窄屏七字段；相邻回归：历史/未知分组折叠、返回列表筛选及完整会话详情。排除：账号登录/发送/移除与配置写入，本轮未改这些动作；未覆盖：全部生产机器状态与服务商操作、真实系统剪贴板内容。

- 390×844：配额容器 client/scroll 均 334px，页面宽390px无横向溢出；默认同步面板 closed，摘要点名 fixture-offline；今日成本 y548。七标签、10/82 与90/82的不同窗口值及观测时间可关联阅读。历史身份、最后观测说明可展开，未知分组 closed 两行 none/0px → open grid/418.3125px → closed none。
- 1440×1000：桌面仍七列。CSS视口模拟固定1280×1000物理窗口的80/100/125%缩放（视口1600×1250、1280×1000、1024×800），14px文字屏幕尺寸为11.2/14/17.5px，99px账号行对应79.2/99/123.75px；不是原生浏览器缩放。899/901px两侧分别grid/table-row，均无页面横溢，901px保留局部表格滚动。未声称全断点覆盖。
- 总览“追查1条未知成本”→ Explore custom当日、unknown_cost按模型列出unknown=1/known-zero=0 → Sessions保留日期及model、cost_status=unknown，只含12tokens/1条 → 详情显示同会话两条（未知和已知零）；返回保留筛选。
- 比较两期链接分别为2026-10-08至14、2026-10-01至07，点击前一期后Explore两个日期输入一致。合成比较为7天窗口。
- 排序显示 high-balance(10%已用)、low-balance(90%)、unknown-balance；账号分区保留。错误卡片先显示未纳入影响及检查方向，展开技术详情可读合成 TimeoutError。
- 复制按钮真实点击显示“已复制”；HTTP无Clipboard API的成功/失败及实际传入ID由回归测试核实。隔离Chrome剪贴板读权限为denied，工具回读/键盘粘贴未取得内容，因此系统剪贴板实际内容仍未核实，生产按钮反馈另验。

可见性与关系层：390px账号块的身份、双窗口、机器与时间按纵向完整呈现，1440px保持跨账号列对齐；截图人工自评未见文字遮挡。小屏账户块比桌面占更多纵向空间，换得无需横滑的字段关联。截图和原始辅助 fixture 在 `/Users/lindong/task-artifacts/agent-monitor-ui-improvements-20261015/`，不包含生产凭据。fixture 只用于验收、30分钟自停，收尾由本session回收。

发布状态：实现提交 `b525bfb2940f37a60d88d9b62323f78efbf2002d` 已快进整合主线并推送，发布区间包含此前完成的产品spec与能力审查文档。MacBook、MacStudio均从主checkout按实时origin/main执行 `./deploy.py --expect`，退出0，原state symlink保留。安装器保留现有web进程，现场发现stale=true后在无busy账号时通过既有restart入口完成重启；两端health均ok=true/stale=false。未改MacMini/Tencent部署，它们的exporter协议不受本轮UI派生改变影响。

生产消费者读数（2026-10-15）：同一自有浏览器普通刷新，实际出现新排序、未知成本链接及新的紧凑状态；HTTP详情 `isSecureContext=false` / `navigator.clipboard=undefined` 点击复制后显示“已复制”。30d总览15544条未知→Explore按模型列出10136/5321/77/8/1/1→首模型会话筛选unknown、列表48个会话→详情可打开。数量是当时快照，不承诺刷新后恒定。390px实测配额client/scroll/table均334px、七标签可读，同步仍closed；真实账号发送/授权/移除未执行。

缓存升级读数：发布前同浏览器普通reload的app.js/styles.css transferSize均300（重验证），发布后观察到新CSS decodedBodySize=58115及新控件/行为。发布时既有asset watcher也可能触发重载，缺少逐响应完整网络记录，严格N-1→N缓存链条标为uncovered；实际消费新版已核实，不以全新冷浏览器冒称暖缓存升级通过。

收尾：本轮相关代码71项最终复验通过；此前后端与投影合计106项读数适用于未再改动的计算实现。review阻塞已消除，无未处置产品finding；系统剪贴板内容与上述严格缓存链条保持未核实。截图、部署原始日志及最终SHA回执保存在 `/Users/lindong/task-artifacts/agent-monitor-ui-improvements-20261015/`，本文件后续仅文档同步的提交也按origin/main重新部署。

工具侧另发现CLI 0.39.1与daemon 0.27.0不一致，采用已安装0.27.0绝对路径完成验证；正式记录为harness仓 `HARNESS-20261015-8d24`，提交 `96f19bcac561f6b9a3ab3d2cd1aeb04c134a1d1e`，该问题不在本轮产品修复范围。其主树39行既有WIP已三路合并恢复，保全证据在上述目录的harness-wip中。
