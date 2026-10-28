# 普通服务日志按容量保留

日期：2026-10-28。采用已独立审查通过的同 inode 维护方案，用途是近期故障排查；允许淘汰最旧普通日志，不改变会话/用量历史及 diagnostic events.jsonl，不承诺保留天数。

## 决定

`runtime/service_logs.py` 只识别当前 stdout/stderr 实际 inode 对应的 `state/server.log`、`hub.log`、`rollup-daemon.log`，在业务导入前给这些 FD 设置 O_APPEND。独立 PIPE 协议、provider DEVNULL、connector 原采集行为保持不变。local start 不再清空 server.log。

CLI Python bootstrap 执行初始化并在正常解释器退出时维护，覆盖现有 rollup、hub-collect 等 collector 命令的开始和结束边界；Web/Hub 直接模块入口也在业务导入前初始化。Web 的既有 `serve_forever` → `service_actions` 每轮调用节流 helper，30 秒内不重复 stat；Web exec 后重新初始化。没有新增进程、线程或定时器，没有改 PID、launchd recipe、owner 校验或重建协议。

默认每段 8 MiB、2 段（当前及 `.1`）；8 MiB 高于决策时本机最大活跃普通日志约6.8MB，是容量起点而非时间窗推算。可在持久 state/service-logs.json 配置 `{"segment_bytes":8388608,"segments":2}`；segment_bytes为正整数，segments为2至100。配置在进程启动时读取，重启服务生效。可选 AGENT_MONITOR_LOG_SEGMENT_BYTES / AGENT_MONITOR_LOG_SEGMENTS 环境值覆盖文件；launchd不保证继承当前终端的环境，常规配置使用持久文件。无效配置只报告维护失效，不阻止业务。

未超额时仅 stat。超额时取得活跃 inode 的非阻塞 flock，只读取最近一个 segment 的 bytes，按64KiB块复制到私有临时文件；成功落盘后轮转编号备份，并 ftruncate 原 inode。多个维护者互斥；原 FD 继续 append，不会因旧offset产生洞。编号备份归本机制，不使用这些保留名称存放人工材料；其他历史/人工命名文件不删除。降低段数后，在下次成功轮转淘汰超过新段数的编号文件；降低段大小后，新生成段使用新大小，已有较旧段随后续轮转淘汰，不声称配置变化立即压缩所有历史段。

容量检查间可暂时超额；collector长任务中间没有新的后台维护调度，结束时再次检查。copytruncate窗口可能丢失并发写入，不能用于严格审计或保证零丢失。临时文件最多一个新段，旧段轮转期间可能短时额外占用一个segment；磁盘满/写入失败不截断当前文件。维护异常最多每5分钟沿现有stderr报一次摘要，继续业务，容量可能继续增长。不能把容量维护成功当作业务/服务健康，也不将截断后的旧字节称作可恢复。

## 替代方案及边界

仅logging handler无法覆盖os.write和继承FD子进程；仅重启处理无法治理长期Web；rename/reopen需要新增协议；常驻FD runner增加进程、背压及生命周期失败面，而当前目标未要求瞬时硬上限。因此采用已存在执行边界的有限copytruncate。基础解释器尚未启动的错误仍沿既有终端/launchd状态观察，不新增启动日志通道。

## 验证

隔离测试覆盖三流识别、原始stdout/stderr、继承子进程、exec后接入、同inode继续append、正常重启保留、真实CLI调度（业务collector替换为本地fixture）、原生serve_forever循环（隔离业务导入与socket selector）、竞争锁、symlink/hardlink拒绝、无效策略及复制失败不退出、段数收缩。没有真实provider、网络、生产服务或HOME/CODEX_HOME覆盖。8MiB尾段复制记录约3ms，10000次空闲check只stat一次；这些是单机合成读数，不代表生产峰值或长期保留窗口。公共操作文档由本任务root同步。

## 硬退出时的临时空间

尾段复制在活跃inode的flock内使用唯一机制保留路径 `.<log-name>.pending`。以O_NOFOLLOW打开，核普通文件、当前uid、单硬链接及路径/FD身份后才截断重用；复制成功再rename进编号段。异常或硬退出允许留下至多一个未完成段，下次维护复用，不再每次创建随机文件；不新增janitor、进程或TTL。该文件不是人工备份位置。未知symlink/hardlink拒绝接管。已存在的旧随机临时文件不按glob清理，本改动尚未部署，测试遗留留在隔离证据目录。

回归先在旧实现连续三次真实os._exit(73)，得到3个临时文件（失败断言3 != 1）；修复后3次硬退出仅1个8字节pending，随后正常维护完成尾段回读并消除pending。symlink/hardlink两种冲突保留目标及活跃日志。定向回归后完整日志套件16项通过；此修复只控制重复失败的临时文件增长，不改变copytruncate并发窗口或检查间超额边界。
