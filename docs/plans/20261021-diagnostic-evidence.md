# 异常诊断信息修复

用户目标（本会话原话）：检查并修复这个项目中影响正常使用的异常所缺少的诊断信息。范围是异常产生、保存和调查回读；不调整暂停、重试、账号操作或配额语义，不重放真实供应商请求。基线为 `6d965218cc605b9802b26149b4c67fa7c49cd10e`。

## 决策与证据

采用既有有界状态与诊断输出保存安全的原始失败上下文：机器配额的 pause 保存首次失败阶段、受控错误类别、协议状态／进程退出码（存在时）、账号安全标识及代码身份；后续暂停消息从该记录派生。解除保留在既有 last_pause，旧暂停明确显示原始诊断未记录。Hub 跳过消息指出触发机器和服务，并保留该轮错误引用。机器快照的 schema 与配额字段不变。

备选是仅增加进程日志，不能保证小时级后续暂停仍可定位首次失败；或新增事件数据库，需要额外索引与保留机制。现有两个 provider JSON、stable flock 和原子替换足以保留当前及上次暂停，增长有界，无新服务或后台扫描。状态是权威，页面错误字符串和 CLI status 是派生读取；不保存原始异常文本、上游 body、token、cookie、请求参数或 stderr。

实测依据：MacBook Codex pause 2026-10-21 16:55:59 +08:00、MacStudio Claude/Codex pause 17:01:30/17:01:50，只含 quotaRefreshFailed；配额缓存已变成暂停提示。实时 Hub 顺序在 macbook 后跳过其他三台；同账号本地会话配额时间仍更新。这些读数不证明最初是认证、网络或上游故障。当前源码 quota_refresh.py:101、exporter.py:396、quota_sync.py:304 对应丢失与传播边界。

既有约束：2026-10-21 ADR `20261021-6bd2-upstream-prevention.md` 保守暂停、串行 combined round、手动解除及保留失败；当时记录本地隔离测试，未证明真实供应商行为。本次保持恢复策略。`20261021-a84c-codex-request-controls.md`、README Local upstream pauses、架构中 upstream_controls 和 quota display 的职责保持。检索语料为本仓 ADR、README、architecture，符号为 upstream_controls、quotaRefreshFailed、refresh_error、diagnostics；不涉及跨仓契约。

广度检查另确认三项：账号 API 未预期异常只返回通用 500；sync_all 把 SSH 准备、导出及 rsync 超时全部压为 timeout；ip-check 启动 OSError 丢失类型与 errno。分别补入既有有界 diagnostics 事件及可关联 ID、既有错误字符串中的固定阶段/预算、启动类型/errno。保留安全源码帧，不保存异常值或局部变量。正常校验和取消不一律报警。

独立决策评审 `/root/diagnostic_decision_review` 已对上述四条链路单轮放行，七判据均成立，无 blocker/应修/未消解项；复核既有 `20261013-b4d8-runtime-performance.md` 的有界 diagnostics 作为复用载体。现场最初错误、实现与部署效果仍未核实，不以评审替代验证。

决策 L1：新增持久诊断字段与读取行为，独立单轮评审。实现 A1/V1：有界本地文件可回退，但跨进程错误运输需 review；TDD 只覆盖已观察缺口，来源为用户本轮请求和上述现场失败。性能：只在失败转换时记录有界对象；不新增每次页面读的扫描或外部请求。新增失败面限于诊断保存，写失败必须保留原业务错误并说明保存失败；不新增 push 告警，本次只增强既有异常调查入口。

## 验证与边界

隔离 HOME/state、受控 stdio/HTTP/SSH 替身：原始失败 → 持久暂停 → 再次请求不派发 → 新 Controls 读取 → status／既有页面错误回读 → resume 留存。正常成功对照、旧状态兼容、恶意异常文本不泄露、诊断写失败不覆盖原因、Hub 跳过与失败区分。服务异常用本地 handler 与既有 diagnostics 回读验证。受影响既有测试和必要 Web 读者检查，最终独立变更 review。发布按主仓既定流程，旧 pause 不自动清除，真实上游恢复未验证。

## 进度

- 已完成：现场链路定位、私树与运行状态隔离、只读广度审计、决策评审、实现与排障文档。
- 诊断回归最初在未改源码时 5 项全部失败；实施后受影响组 141 tests OK，配额展示／独立查询／生命周期／Web Node 回归组 117 tests OK。一次测试入口缺少 `PYTHONPATH=tests`，修正运行环境后对应测试成功。原有 SQLite ResourceWarning 仍可出现，不据此宣称资源生命周期全面通过。
- 独立变更审查 `/root/monitor_failure_review` 单轮发现 3 项 MEDIUM：新 `add_note` 与 Python 3.10 不兼容；父 reader 启动失败被 exporter 改成通用错误；watchdog 最终输出漏 failure。均已修复，原异常类型与无派发时不暂停语义保持；启动 errno 13/24 经真实 exporter 写入既有缓存，deadline/delayed_start 两个真实隔离进程时序保存 timeout 原因。修后受影响组 58 tests OK（14.899 秒）；原 reviewer 定向复核 9 tests OK，三项放行，无新增 finding／承重未知／未处置项。实际 Python 3.10 二进制尚未运行，兼容判断限于移除不支持的 API。
- 测试采用主仓 `.venv/bin/python`、隔离 HOME、`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests`；实际导入的 diagnostics、quota_refresh、quota_sync、server、sync、upstream_controls 均来自本私树。未访问真实供应商。独立评审按 task-routing R14、原生 collaboration、独立 context 执行；实现由主线程处理。
- 原审输入以 base + 完整 patch 保存在 `/Users/lindong/task-artifacts/agent-monitor-diagnostic-evidence-20261021/review-input.patch` 及摘要清单；发布与部署回执同目录保留。待完成：提交整合发布、四机部署与实际入口核查。旧 pause 不清除，历史首因不可恢复。
