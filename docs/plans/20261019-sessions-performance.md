# Sessions 加载优化

用户目标（2026-10-19）：通过 performance-workflows optimize 缩短 http://macstudio:39001/sessions 从打开到列表真实可见、可操作的等待，保持功能、数据口径和新鲜度，持续优化并部署复验。未给定数值 SLA。

基线 e9fdc3fe4ba19280732d80961d23d91f2bb858f7；私树 codex/sessions-performance-20261019。主树未跟踪 state 保留。执行 current/uncertain、task-routing R4，沿用当前配置；浏览器取证异常交原生 collaboration 独立诊断。

## 证据

MacBook→MacStudio 默认30d API：TTFB 4.584s、总计4.869s（identity传输）。独立 Chrome 1440×1000、直连：30d 浏览器冷/热到首行真实可见、可点击且五个筛选启用分别4622.7/4516.5ms，7d热1471.6ms，各1样本。API后渲染33–42ms，静态启动约250ms。就绪探针经隐藏链接、禁用筛选及1500ms API交付延迟对照，并实际点击首行进入详情。固定四源副本位于 ~/.local/share/agent-monitor-diagnostics/20261019-sessions/snapshots，代际在 manifest.json。私树 cProfile：216538次完整事件解码、200256次复制和目录规范化、1073会话、4.830s；read_admitted_usage 2.857s、collect 1.917s、会话计算0.111s。含profiler开销，不作为生产收益。

## 决策与实施进度

直接列表字段投影已撤回：独立审查复现合法摘要、非法 service_context 可以经直接首次发布/准入进入 CurrentGeneration，正常导入与当前完整读取会拒绝；活跃 lease 本身不能证明 statistics 已完整验证。反例和处置在持久诊断目录 decision-review.md，不扩大为发布机制加固。

已实施两项固定行为优化（L0）：目录规范化每来源每不同目录一次，只在值改变时替换对象；每次完整读取内部，相邻完全相同且已成功校验的 usage metadata 复用验证结果，成本是否为空参与键，标量字段仍逐条验证。验证变化与失败不跨请求复用。原投影 RED 测试已替换为真实重复成本与非法字段拒绝测试。API、schema、关系、轮询和同步频率不变。

固定四源、新旧 server 完整对象比较覆盖 7d/30d/all/custom、unknown、agent、machine、两种排序共8组，目录优化后全部一致；其中30d 2.97→2.26秒。metadata 优化后 profile 3.144秒对比基线4.830秒，仍有全窗口解码成本。这些是离线同数据读数，不是部署收益。

按来源、文件签名和窗口复用摘要的 L1 决策已获单轮独立审查放行（七条成立），见 [d8f4](../adr/20261019-d8f4-sessions-result-reuse.md)，原始 packet/review 在诊断目录 cache-decision.md / cache-decision-review.md。已实现最多8项的缓存，只存完整校验后计算的结果，不引入 TTL、持久写入、后台预热或新线程。初始8MiB编码内容限额经实测调整到16MiB，见下文。每次重新准入；窗口边界、未来候选、时间回拨、来源移除、新代际单来源重算、查询与响应隔离均有真实导入快照测试；3并发/6请求输出相同。热命中不能替代冷读验收。

备选：只减少复制留下主要解码成本；SQL聚合会改写算术顺序与关系语义；TTL缓存有新鲜度风险；后台全量预热增加生命周期与内存。已撤回的投影不再实施；本方案用明确失效源减少重复频次，同时保留首读验证。

边界：_admitted_connection要求CurrentGeneration和存活lease，完整字段验证继续保留。旧v1/v2/v3与v4读取契约保留；standalone没有跨请求复用。docs/performance-20261013.md及architecture.md记录每请求完整解码的实现，完整性约束继续遵守。

最新固定副本比较：基线、优化冷、优化热8组完整API对象全部一致。30d为2.921/2.227/0.00185秒，7d为0.929/0.640/0.00048秒；all为12.717/8.799/9.179秒，完整历史仍有主要残余，不能外推默认窗口收益。缓存冷/热是进程结果复用，与浏览器HTTP缓存分开。

容量归因与再优化：all四源JSON共10,673,846字节，初始8MiB使macbook/macmini互相淘汰。L0调整编码内容上限16MiB、仍8项后，同一数据all冷8.804秒、热0.014秒，随后读30d再读all仍0.014秒，全部对象一致，保留内容11,821,123字节；32项受影响测试重新通过。此独立进程全历史首读峰值RSS1,615,937,536字节，含完整事件物化，不能把编码内容上限说成RSS上限；本任务未降低首次全历史物化的内存/时间增长阶。源码与测试除这一容量常数外无审查后逻辑修改。

现有全量回归1027项/126.4秒，10条失败均在原始e9fdc3f独立archive、同interpreter与隔离HOME复现（7个测试方法、含4个同方法子案例）：旧unknown_cost字段断言及旧UI文案。未改这些无关断言。Sessions相关32项通过；新旧版本、日期、unknown/zero、关系、lease、缓存时序和并发样本按对应测试覆盖，非任意输入证明。原始日志 tests-full.log / tests-baseline-failures.log。

档位：缓存决策L1（跨请求复用），实现V1独立审查，本地A0；生产沿既有deploy.py与常设授权。新增验证来源是用户报慢和明确保持口径/新鲜度；不新增SLA、监控或契约。

## 状态

已完成首轮实现、决策审查、完整回归与基线失败归因。单轮独立V1变更审查覆盖12个文件，32项隔离测试通过，正式findings与承重未知均无；报告 changes-review.md。两条SQLite ResourceWarning未定位创建处，只保留未归因观察，不宣称生产泄漏。首轮 adae377 已整合、push并通过MacStudio主树deploy.py安装。安装器保留旧进程，首次两次浏览器读数仍4.8/5.1秒；重启后重复打开0.192秒，启动后的首读6.698秒，全历史首读12.972秒。不能以热命中宣称冷读目标完成。无用户待办。

后续冷读优化：索引窗口查询把完整payload写入SQLite临时排序B-tree；改为索引先选position，外层按position主键顺序取payload，保留相同候选、顺序和完整验证。固定macbook副本189365条，原/新原始序列SHA256相同，读取0.529→0.127秒；macmini13530条同样相等。L0固定行为实现手段，可回退单条查询；变更gate不另起reviewer，已有完整对象差分及32项受影响测试直接检查回归。最新8组完整对象仍与独立冻结基线相等，30d冷2.048秒、热0.00182秒，all冷8.546秒、热0.01408秒；离线结果非生产收益。此后续查询优化待发布、部署与数据更新后的消费者验收。

恢复入口：私树 /Users/lindong/research/.worktrees/agent-monitor-sessions-performance-20261019；诊断目录 /Users/lindong/.local/share/agent-monitor-diagnostics/20261019-sessions，compare_sessions.py 已冻结基线 server 与 statistics reader，避免差分自证。浏览器测量 browser/measure.mjs，binding browser/browser.json；自有 headless Chrome 有退出上界，超过15:44需核存活后再复用或重建。原生 reviewer projection_decision / sessions_changes_review 已完成，browser_measurement 已交回浏览器。现有单份记录可恢复状态，未提升 Long-task mode。
