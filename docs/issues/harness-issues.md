# Harness issues

## [open] HARNESS-20261012-36ec：后端性能发现依赖用户报慢，共享冷路径残余未形成主动检查

- Type: coverage gap
- Priority: medium
- Discovered: 2026-10-12，用户在 Explore 优化交付后要求分析为什么此前后端优化未提前消除这些问题。
- Owner: agent-monitor 开发流程维护者；通用入口候选归 ai-agent-config 的 performance-optimization / coding-guidelines owner。本轮只诊断并记账，不修改通用规则。
- Scope: 本项目端到端性能发现和开发预防；不等于所有旧性能优化无效，也不证明所有历史 session 都漏读了规则。

### 当前证据

`docs/adr/20261008-c4e2-sync-process.md` 已明确记下 process-local validation cache 的残余：父进程账号记忆或首次 HTTP 请求仍会校验新 generation。`20261008-d102-query-process.md` 也明确区分进程隔离与算法提速。此前调用列表交付只覆盖其具名用途，不足以证明 Explore 或全部后端链路已优化。2026-10-11 Explore 诊断得到冷 admission 1.0706 秒、同进程第二次 0.0015 秒、固定代际聚合 SQL 约 1–3 ms；完整证据和边界见 `docs/explore-performance.md`。这支持共享请求准入成本没有被那几项局部优化消除，不反推此前完整执行轨迹。

开发时可通过调用链和复杂度发现全量文件校验进入请求、锁覆盖复制等候选，再以最小测试证实。当前 `tests/test_query_view.py` 明确断言 HTTP 不重新 admission / validation，`tests/test_snapshot_contention.py` 冻结复制后检查读取能否继续；这些机制测试比单纯增加业务断言数量更能阻止同类退化。它们仍不能证明未知性能问题已被全面发现。

### 规则检索与缺口

本轮读取 `~/.claude/AGENTS.md` 性能入口、`references/performance-optimization.md`、`references/coding-guidelines.md` 和 `skills/review-changes/SKILL.md`。前者触发原句为“用户明确指出慢、卡或明显延迟、要求优化性能，或当前任务的真实入口读数已显示主导等待时”；性能正文要求“从用户或真实消费者实际使用的入口取得端到端读数”。它们已覆盖报慢后的诊断，不能把问题笼统归为缺少性能原则。当前读到的 coding-guidelines 有有界并发和可观察验证，但未给上述同步/存储改动明确接入按生命周期检查成本的要求。这里只判断已读入口，不断言全 harness 没有仪器。

检索命令：`rg -n '主动.*性能|性能.*主动|冷.*热|复杂度|主导等待|性能问题|20260830-4b84' /Users/lindong/research/ai-agent-config/docs/issues/harness-issues.md /Users/lindong/research/ai-agent-config/docs/issues/archive/closed.md /Users/lindong/.claude/AGENTS.md /Users/lindong/.claude/skills/task-workflows/references/nonfunctional-requirements.md /Users/lindong/.claude/references/rigor-tiers.md`。中央仓历史命中 `HARNESS-20260902-a4e9`，它讨论批调用遥测，2026-10-08 已按用户裁决 wontfix 且注明未证明修复；本条是新的项目具体证据，不重开该历史条目、不沿用其中已撤回的全局无仪器断言。项目检索 `rg -n 'performance|性能|cold|admission|27.051|全量' docs/issues/general.md docs/issues/archive/closed.md` 命中旧调用页逐轮验证与结案范围，核对其具体覆盖而非把结案当全后端通过。

### 建议方向与处置

建议在涉及请求、采集、发布和存储的开发中检查本次受影响的完整消费路径：工作量随什么增长、何时发生、是否争用读取资源；识别到具体风险后写能使错误结构报红的测试。显式性能优化则从具名入口主动取得冷/热、新代际、后台任务重叠与代表性数据规模读数，并复用既有性能决策正文。共享组件的已知残余应在后续涉及其消费者时重新评估，不能由一个页面的交付隐含关闭。无需每次提交全站压测、强制多轮外审或无读数重写数据库。

下一动作：对齐用户期望后，由上述 owner 分别落实开发入口和项目回归接入；验证原 Explore 类路径能在用户反馈之前被发现，并保留普通局部改动不被扩大为全项目性能工程的合法情况。本轮未运行 harness 行为实验，不能宣称主动发现能力已修复；架构、规则和历史记录的诊断已完成，待实施项保持 open。
