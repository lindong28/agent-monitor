# 主动发现后端性能问题：方案与实施记录

状态：改进已实施并完成下述验证。日期：2026-10-12。本文集中保存本轮缺口、改进方向、范围、顺序、证据与剩余项；通用行为规则只由 ai-agent-config 的 performance-optimization.md 维护。

## 目标与来源

用户要求 Codex 不依赖使用后的主动反馈，能通过后端架构、代码和测试提前发现问题；同意开发时预防、显式性能优化时主动诊断两个时点，随后要求“将以上你提到的缺口和改进建议记录到一个文件，然后按照文件中记录的内容完成改进”。授权包括已讨论的通用三文件、项目入口、可重复诊断、回归及文档同步。用户未提出额外排除项。

## 缺口与对应改进

| 缺口 | 改进 | 本轮验证 |
| --- | --- | --- |
| 性能发现依赖报慢或已经有慢读数 | 开发受影响链路的成本检查自动进入；显式性能请求主动找入口和取得读数 | 核对真实规则入口，并以独立任务样本检查主动发现与普通小改的边界 |
| 局部 SQL / 页面改善被误当完整链路改善 | 沿消费入口追踪共享准入、存储、同步和锁，声明覆盖范围 | 项目架构记录映射真实调用链，诊断调用真实后端函数 |
| 缺少复杂度、时点和频次判断 | 按增长变量和生命周期判断成本，复用既有三步性能决策 | 隔离合成快照规模变化、冷/热 admission、已准备读取与换代的分阶段读数 |
| 业务正确性测试不足以发现阻塞和重复工作 | 对具体结构性风险建立可证伪回归 | 聚合消费者不重新准入/校验；复制和准备暂停时读者仍能推进；反向变异 |
| 已知残余未持续进入后续任务 | 在相关消费者被修改或性能优化时重评共享组件的未结项 | 项目入口连到本记录和现有 issue；历史决定不被页面交付隐含关闭 |

## 实施范围与顺序

1. ai-agent-config：claude/AGENTS.md 只维护触发，claude/references/coding-guidelines.md 增加开发入口，claude/references/performance-optimization.md 统一方法与证据边界。Codex 继续通过同一 AGENTS symlink 消费。与本次决定相关的 ADR/文档索引按仓库要求同步，不复制通用正文。
2. agent-monitor：AGENTS.md、docs/architecture.md 接入项目入口；新增 scripts/benchmark_backend.py，默认只使用隔离合成状态，不连接供应商或生产服务；复用真实 generation、QueryView、SQLite 聚合代码。报告计时、规模和结构检查，执行失败与性能未定量达标分开。可重现的数据规模由参数给出，不设产品延迟 SLA。
3. 扩充 tests/test_query_view.py 与 tests/test_snapshot_contention.py 的相关消费者/规模覆盖，新增诊断工具所需的最小有效测试。复用先前状态屏障和 HTTP 缓存测试，不重写生产实现。
4. 复核文档与工具、记录实际验证、关闭已完成的项目 harness issue。完成私树提交、整合及清理。agent-monitor 不 push；ai-agent-config 按其项目级持续发布授权发布。无生产服务变更。

## 决策审查输入

决定：复用现有性能 owner，接入受影响开发路径和显式优化请求，配套本项目隔离诊断；不新增通用性能 skill、固定全仓压测或持续监控服务。跨项目默认变化取 L1，一轮独立决策审查；实现和诊断判据取一轮独立变更审查。主线程 R4 完成实施，独立 reviewer R14（gpt-6-astra/high）。

替代：仅增加“注意性能”不能提供时点和检查读数；每次功能变更全站压测超出受影响范围；统一毫秒阈值不适用于不同部署；新增独立性能 skill 与既有短入口重复。默认合成状态避免诊断读写真实 provider / state；代价是不能代表生产分布或网络，应由真实任务选入口补测。

证据：本仓 docs/explore-performance.md 记录冷 admission 1.0706 秒、热 admission 0.0015 秒、固定代际 SQL 1–3 ms。现有 QueryView 和复制竞争回归可在错误结构下失败；它们不代表整套后端不存在未知瓶颈。此前 921 项全量测试中的 9 失败/1 错误有基线复现，本轮不扩入其修复。

既有决定语料：两仓 docs/adr 与 docs/issues（含 archive）。对 harness 以 performance-optimization、主动性能、性能入口、nonfunctional-requirements 检索，命中 20260830-9f2c（2026-08-30：启动链路 12–34 秒、显式读取正文的四份回答无增量，选择短路由而非新 skill）、20261003-4493（2026-10-03：静态角色有统一 Lighthouse / 扩容处方，改为按任务定档）、20261008-4a2e（2026-10-08：澄清设计依据与实测收益，不增加强制实验）。本轮沿用其方法 owner 和任务范围，新的用户要求补上受影响开发链路的主动检查，不恢复每次功能变更全站审查。历史 HARNESS-20260902-a4e9 已按用户决定 wontfix，仅作为不同批处理场景的历史，不重开、不沿用其全局无仪器断言。本项目 20261008-c4e2 / d102 的实测与残余、20261011-a8d3 的读取视图语义由现有 ADR 保持。

档位来源：用户明确同意上述能力及两时点（rigor 来源③）；本项目已有漏发现与冷/热差异（来源①）。新增检查仅覆盖受影响链路，不据此提高其他功能的非功能目标。未知：未来任意 session 的遵循率、生产延迟分布和全部性能问题的发现率，均不承诺。规则文字检查与有限任务样本分别报告。

## 状态与结果

- [x] 保存已批准范围和缺口。
- [x] 决策审查完成并记录：proactive_perf_decision 七项成立、无 findings；ai-agent-config ADR 20261012-b710。
- [x] 通用入口和正文更新。
- [x] 项目诊断、回归和文档接入。
- [x] 独立变更审查、工具与有限规则行为验证。

本文与实现一同提交；最终提交身份、整合、发布边界和隔离资源清理按本轮交付回执及 Git 记录核对，不在提交前预填成功。

工作树：agent-monitor-proactive-performance（本地 main 基线 0f711de）；ai-agent-config-proactive-performance（基线由该树 Git HEAD 记录）。两树均属本 session，归本 session 清理。原始测量和行为样本保存在本机持久目录 `~/.local/share/agent-monitor-diagnostics/20261012-proactive-performance/`，交付记录在本文，不以临时目录作为唯一证据。

## 以后怎么触发

开发：正常交代后端功能、重构或修复即可，成本检查由通用开发入口触发；用户不必另外提醒性能。只覆盖受影响链路。显式任务：用自然语言要求分析或优化后端性能，不必指定慢接口；分析只交诊断，优化继续实施与验证。规则不是定时后台巡检，没有任务运行时不会自行扫描。

项目入口：先读 [后端成本地图](architecture.md#backend-performance-checks) 确认消费者及残余，再选用下面的诊断与相关测试。合成诊断不能替代真实页面/API；需要声称生产收益时仍须在同一实际入口取证。

## 可重复诊断

在项目根运行：

```sh
LLM_GATEWAY_ROOT="$HOME/research/llm-gateway" .venv/bin/python scripts/benchmark_backend.py
LLM_GATEWAY_ROOT="$HOME/research/llm-gateway" .venv/bin/python scripts/benchmark_backend.py --rows 100 1000 --padding-mib 0 4 --repeat 3
```

脚本在自身临时 HOME 和显式临时 generation/config 中创建 v2 快照，调用真实 admission、QueryView 和 grouped aggregate；不启动 HTTP、SSH、provider 或常驻服务。发布模块按项目既有合同导入 Gateway reader 源码，因此须显式设置 `LLM_GATEWAY_ROOT` 为实际 checkout（上例为本机位置）；不设时隔离 HOME 下找不到默认 checkout，会明确失败，不回到真实 HOME。无 `--state` / 生产目标参数。场景串行，共用磁盘与验证缓存时并发会污染读数。stdout 是 JSON 原始样本；需要留证时由调用者重定向到自己的持久目录。退出 0 只表示测量完成，不表示达成性能 SLA。

默认两个聚合行数 × 两个 padding 体积，单机器、schema v2。padding 是额外 SQLite blob，单独改变文件体积，不冒充真实请求历史。每种组合分别测应用缓存冷准入、热准入、冷准备、已准备读取、新代际准备和新代际读取；准备阶段 `input_tokens=null`，读取返回可核对的合成总量。应用冷仅清除进程的验证缓存，不清 OS 缓存。每个样本报告秒数、准入/完整校验/digest 次数及 digest 字节数；计时包含计数包装开销，不作裸函数极限速度。

回归入口：`tests.test_backend_benchmark`、`tests.test_query_view`、`tests.test_snapshot_contention`。使用本仓解释器、隔离 HOME/state；导入 HTTP 模块时按现有依赖合同指定独立的 `LLM_GATEWAY_ROOT`。前端屏障、缓存与 quota 测试继续复用已有测试，不因为增加诊断命令重新跑全站验收。

## 验证记录

2026-10-12，初轮相关模块执行 22 项测试通过（包含导入的既有 fixture 用例）。最终版本按具名测试类执行 13 项通过，覆盖工具、QueryView、HTTP 连接与 snapshot contention，实际模块来源和摘要保存在 `final-tests.json`。最终工具 CLI 覆盖聚合行数 2/1000、padding 0/1 MiB、代际 1/2、重复读取 2 次；prepared 查询 digest 为 0，冷校验实际 digest 为 1 且字节数与该代快照一致，验证同一计数器能报正负结果。四个服务函数消费者覆盖两个代际、每代两次；overview 的 quota/Hub 旁路被隔离，仅断言统计读取路径，不冒充完整 Hub HTTP 验证。复制暂停覆盖两种 payload 体积；测试中的有限等待用于证伪锁依赖，不是用户延迟 SLA。

反向变异（未改源文件）：请求重新 admission 时四次子场景均报 `HTTP did admission`；准备持有读者锁时读取等待报 `TimeoutError`。原实现相应用例通过，说明测试能区分这两类错误结构。原始输出在 `mutations.json`。

有限行为样本：三个 fresh-context Codex agent 分别显式读取同一份候选规则和合成代码，返回只读设计。日期筛选开发主动识别每请求全量读/hash/parse 与共享锁，提出按换代准备且保留失效语义；未指定接口的优化请求自行选择 overview/explore/publish 及成本读数；标题拼写修改只改常量，不扩成性能审计。三个场景各一份响应符合预期。目标是本轮候选指令在具名设计样本中的行为，没有旧版对照，也没有完整开发执行或默认自动加载实验，不能推导规则导致了改善、跨会话遵循率或生产收益。

身份：候选 performance 正文 SHA256 `94019619932f4b4e91f36fab29d385e6c47fa68da4365de61e24676e5e058625`；完整候选文件与场景的摘要在 `scenario-identity.json`，三个 agent 回报匹配。各样本的未知日期契约、实际写入者、真实入口耗时只是合成题的缺失输入，不是生产 findings；标题拼写错误是刻意保留的范围对照，由本记录保留，不修改生产代码。

默认诊断最终运行：macOS arm64，Python 3.13.12；行数 100/1000 × padding 0/4 MiB，单机器、v2、两代。表中为毫秒中位数，除换代准备仅一个样本外，每格三个样本。完整六阶段原始值与环境在 `benchmark.json`；执行源码摘要在 `verified-source.json`。

| 聚合行数 | padding MiB | 冷准入+查询 | 热准入+查询 | 已准备查询 | 换代准备 | 换代后查询 |
| --- | --- | --- | --- | --- | --- | --- |
| 100 | 0 | 1.357 | 0.721 | 0.479 | 1.179 | 0.509 |
| 100 | 4 | 2.669 | 0.700 | 0.512 | 2.404 | 0.469 |
| 1000 | 0 | 7.260 | 2.457 | 2.226 | 4.908 | 2.260 |
| 1000 | 4 | 8.664 | 2.503 | 2.243 | 6.295 | 2.225 |

四组中的已准备读取均为 admission/validation/digest 0 次；冷准备与换代准备仍支付完整校验。行数增长仍增加聚合读取成本，这是保留的已知工作量，不意味着现在应无条件重写聚合实现。1000 行的第二代快照较第一代大 4096 字节，脚本分别报告两代大小，防止错比。上述样本不建立生产 SLA。

审查：独立决策七项成立；独立变更审查通过，Gateway 依赖说明与第二代大小字段窄修正复核通过，findings 无。README/CHANGELOG、项目架构和 issue 同步完成。未处置 findings：无。本轮没有改变常驻服务或生产运行源码，不产生新的部署收益主张。未覆盖的真实生产分布、全历史查询瓶颈、未来自动遵循率继续按上文边界报告，相关任务由当次执行 agent 取证，不要求用户先定位慢点。
