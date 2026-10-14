# Upstream contract arguments

审查日期：2026-10-14。实现基线：`eb6e797e30413469051eb5b1d7a4020539c0fedf`。本文件逐项论证账号操作、身份、存储、发布、隔离和运行生命周期，并补充 [指标规范](metrics.md) 的四项来源边界；不改变产品契约，不代表修复或生产验收。

证据目录：`/Users/lindong/task-artifacts/upstream-contract-audit-20261014/`，分为 `accounts/`、`storage/`、`history/`、`runtime/`，保存固定源码、上游原文、反例脚本和原始读数。本文件保留可迁移的结论、代码定位和上游链接。隔离实验使用本机解释器和临时数据，没有真实登录、发消息、读取生产凭据、重启服务或重放生产历史。代码定位以基线为准。

“成立”限定所列前提；“反例”证明全称主张不成立，不给出生产发生率；“未核实”保留缺失前提。API 标准、固定版本生产方实现、项目业务定义分别标注，不把测试期望当独立规范，也不把 API 文档当作 CLI 私有持久化格式保证。

## 1. Codex 登录、发送、取消状态机

固定生产方为 Codex `rust-v0.162.1`，SHA `092d3acd6bec3e3a14bdc7e7a2810ab628ab759d`。来源：[outgoing response ID](https://github.com/openai/codex/blob/092d3acd6bec3e3a14bdc7e7a2810ab628ab759d/codex-rs/app-server/src/outgoing_message.rs#L700)、[登录处理](https://github.com/openai/codex/blob/092d3acd6bec3e3a14bdc7e7a2810ab628ab759d/codex-rs/app-server/src/request_processors/account_processor.rs#L734)、[turn状态](https://github.com/openai/codex/blob/092d3acd6bec3e3a14bdc7e7a2810ab628ab759d/codex-rs/app-server-protocol/src/protocol/v2/turn.rs#L33)、[turn启动](https://github.com/openai/codex/blob/092d3acd6bec3e3a14bdc7e7a2810ab628ab759d/codex-rs/app-server/src/request_processors/turn_processor.rs#L652)。业务结果定义沿用账号操作与恢复ADR，不从上游通知名称猜业务成功。

| 具体问题 | 实现与推导 | 结论边界 |
| --- | --- | --- |
| 请求/通知交错会否错认确认？ | `Rpc.request`递增ID、只收相同ID，其余消息入pending；登录匹配loginId，turn完成同时匹配threadId/turn.id | 当前每Rpc串行request前提下成立；不声明任意并发共享Rpc可行 |
| 登录成功就可以发送？ | 完成通知检查success，之后重新account/read和本地身份核对 | 状态机有再次核验；身份解析自身差异见下一节 |
| turn/completed就一定成功？ | 上游可completed/interrupted/failed；项目只把status=completed算succeeded | 检查正确；不证明输出内容恰好OK、七天窗口重置或配额恢复 |
| 无确认能否自动重发？ | 上游先启动turn才回响应；项目写turn/start前持久化unknown，batch retry仅取not_sent，异常补查不重发 | 保守策略成立；一次显式turn/start不等于底层attempt恰好一次或服务端exactly-once |
| 取消是否撤回执行/消耗？ | 项目设置取消event并终止自有进程，不是等待协议turn/interrupt确认；保留message_status，明示不能撤回消耗 | 本地停止不证明服务端未执行；现有结果表达符合此边界 |
| 消息成功与配额查询失败如何组合？ | 成功消息后quota失败为partial；恢复不覆盖原message状态 | 分离成立。reset时间、成功消息和查询成功是三件事，不能互证 |

状态机是固定源码论证，没有真实登录/发送。针对429的精确文本特判不是跨版本结构化错误保证，不外推到未来CLI。逐行来源、对应项目行号与前提见 `accounts/audit.md`。

## 2. 账号身份与凭据归属

Codex普通ChatGPT认证的 [get_account_id](https://github.com/openai/codex/blob/092d3acd6bec3e3a14bdc7e7a2810ab628ab759d/codex-rs/login/src/auth/manager.rs#L614) 使用当前TokenData.account_id；[email解析](https://github.com/openai/codex/blob/092d3acd6bec3e3a14bdc7e7a2810ab628ab759d/codex-rs/login/src/token_data.rs#L174) 先top-level再namespaced profile。managed-agent另一分支的fallback不能拿来改写普通认证语义。

| 具体问题 | 项目输出与上游差异 | 结论/修正方向 |
| --- | --- | --- |
| stored workspace与JWT claim冲突谁优先？ | `parsers.accounts.codex_account`用JWT覆盖stored；合法冲突输入中项目=claim-other，上游=selected；相等对照均selected | 已证优先级不同。可能错误展示/阻断正确登录，也存在验证身份与实际selected workspace不同的条件路径；未证真实错发。应按对应auth mode生产方语义，不无条件拿claim补身份 |
| 邮箱只在namespaced profile中？ | 上游能读出；项目label=null，后续身份校验会拒绝 | 已证漏读；增加同上游fallback，保留严格身份核验 |
| workspace/user/member/email是否同义？ | 上游各自分离；项目quota_identity用workspace+email区分成员 | 这是项目业务键，能区分不同邮箱，不证明邮箱不变或永久一一对应user；本轮不改变身份schema |
| Claude自定义配置目录是否一致？ | metadata固定HOME/.claude.json；file helper用CLAUDE_CONFIG_DIR，忽略secure-storage覆盖；Keychain固定service、没有account selector | 已证选择器不一致。合成default/custom同时存在时解析default-account、凭据路径却custom；没有读真实Keychain，不能据此声称泄漏 |
| 读到token是否足以认定归属？ | `quota_refresh`用同一token先profile核accountUuid，再读usage；不匹配/401/403不贴目标账号 | account UUID绑定成立；同UUID跨organization的usage计量契约未核实，不凭organization字段存在宣布串账 |

Claude依据为实际2.1.296二进制，SHA256 `c9b5341637becbd423ddffc5b254afb645682a3868cb708bbc6cc0e7bb419937`，配合当前 [authentication文档](https://code.claude.com/docs/en/authentication)。固定二进制的getGlobalClaudeFile→Tt选择legacy路径或CLAUDE_CONFIG_DIR下metadata；Lw/j$支持secure-storage目录和目录SHA256前8位service后缀；security调用同时带service/account。固定偏移及摘录在 `accounts/audit.md`，不伪造公开源码URL。

Codex身份/邮箱及Claude选择器进入 `ISSUE-IDENTITY-20261014-7a62`；同账户跨组织和邮箱稳定性保留为证据/业务边界。修复归对应身份维护者，生产发生率和历史身份迁移未执行。

## 3. SQLite、导出和 archive/rollup 恢复

上游依据：[SQLite immutable](https://www.sqlite.org/uri.html#uriimmutable)、[WAL](https://www.sqlite.org/wal.html)、[isolation](https://www.sqlite.org/isolation.html)、[VACUUM INTO](https://www.sqlite.org/lang_vacuum.html#vacuuminto)、[Python 3.13 sqlite3 事务上下文](https://docs.python.org/3.13/library/sqlite3.html#sqlite3-connection-context-manager)。隔离实测 Python 3.13.12 / SQLite 3.50.4，不外推为其他机器版本。

| 具体问题 | 规范与实现链 | 判断与条件 |
| --- | --- | --- |
| 打开前后无 WAL 能否推出 immutable 安全？ | `rollup._open_read_only` 在普通只读失败后检查 WAL，再 immutable 打开；SQLite 要求整个连接寿命内文件不变 | 不能。注入首开失败后，真实 writer 提交2，普通连接读2，fallback仍读1。首开失败是注入，未证明本平台自然触发 |
| 当前消费者是否因此竞态？ | `_check_rollup`、migration 持 sibling rollup 锁；Web 默认查询 leased generations；显式 mutable `db_path` 接口自身无锁 | 当前主要链路在合作 writer 同锁、generation不原地改写时成立。无锁 helper 反例不能证明线上 race；尚未建立真实无锁 mutable consumer |
| 非零 WAL 是否等于未 checkpoint？ | `_wal_has_frames` 用 size>0；SQLite checkpoint 通常复用 WAL、不截短 | 诊断语义不准。FULL 返回 `[0,2,2]`，WAL仍8272 bytes；保守拒绝可能产生 false indeterminate，不证明漏读或损坏 |
| VACUUM INTO 能否保证导出？ | `exporter._vacuum_into_locked` 持应用锁、生成不存在的输出并fsync；统计扩展在未公开文件中单事务写入，再校验/同步/发布 | 单库快照成立；完整bundle依赖外层发布顺序。未完成输出不能提前公开。隔离helper integrity_check=ok、input=11只核本机运行 |
| archive/rollup是否跨库原子？ | `UsageArchive.collect` 自己BEGIN/commit；`rollup._run_locked` 在另一DB提交 | 不是跨库事务，但有重放恢复。archive提交后、rollup写入前注入异常，retained=1，随后从archive重放得input=11；不能推广为所有身份/authority错误都可恢复 |

`_open_read_only` 注释所称特定 SQLite 版本不创建 shm，本轮读取的官方 WAL/release 文档尚未建立对应版本保证，故不以注释补足 immutable 前提。详细 caller 分类和实验见 `storage/report.md`；问题进入 `ISSUE-STORAGE-20261014-2b31`。

附加版本风险：本机3.50.4在官方 [WAL-reset bug](https://www.sqlite.org/wal.html#the_wal_reset_bug) 影响范围，修复为3.51.3+及3.50.7/3.44.6回补。触发需要同一WAL DB多连接特定write/checkpoint时序；应用锁是减轻条件。本轮未证明项目触发、未核各部署版本、未升级依赖，不判生产损坏或紧急故障。存储维护者后续核实际版本和checkpoint并发面。

## 4. Generation、多机查询、租约与回放

上游依据：[POSIX.1-2024 rename](https://pubs.opengroup.org/onlinepubs/9799919799/functions/rename.html)、[fsync](https://pubs.opengroup.org/onlinepubs/9799919799/functions/fsync.html)、[Linux flock](https://man7.org/linux/man-pages/man2/flock.2.html)。Linux文档不能单独证明macOS/NFS的全部行为；本论证限定合作式本地文件锁，未论证设备断电或网络文件系统。

| 具体问题 | 实现推导 | 判断与读数 |
| --- | --- | --- |
| current/previous多文件原子性 | 准备完整副本后持久pending(old,new)，原子切current，再补previous/清pending；恢复按current==old/new分流 | 不是一次多文件原子操作，而是日志恢复。切换前后两个相位真实子进程退出，恢复分别旧10/新20 tokens，pending均清除；只覆盖2相位 |
| 读指针到加lease是否会被GC？ | `admit_generation` 在共享GC锁内读指针并取得代的共享lease；GC持排他GC锁且须取得代的排他lease | 合作读者成立。旧代持lease时保留、释放后可GC；不保护外部删除锁inode或直接改DB |
| QueryView是否提前关闭旧代？ | 取view/增加readers同mutex；retired且readers=0才close；多查询使用leased immutable DB或显式事务 | 按所有权成立，无须SQL全程持view mutex。独立给helper传mutable path无同等保证 |
| 多机是否同一时刻？ | admission逐机取稳定代，QueryView固定这组lease | 各机快照稳定，不是分布式同时刻事务。观察时间按机器解释；既有业务未要求同时采集，不新增事务要求 |
| 旧bundle能否倒退？ | installer核manifest/digest/identity后publish；statistics extension核时间/保留，legacy无此扩展；current幂等、previous拒绝、GC后ID可重建 | 真实legacy installer接受A10→B20→C30→A10。不能外推含统计扩展的旧代；未证明正常调度自然送旧包，也未找到禁止所有显式legacy回退的独立业务契约，列能力边界 |
| published_at是不是切换时刻？ | `_prepare_generation` 在copy、校验、等锁前采样，切current时不重采；server用于last_sync/contact/age | 已证时间语义偏差。隔离copy阶段时钟前进60秒，字段比切换早60秒；未测生产误差。应统一准备开始/实际发布时间与消费者语义 |

保证依赖稳定锁inode、generation不原地修改、文件/目录fsync成功和错误正常上抛。进程恢复不代表APFS/设备断电已验。legacy freshness与发布时间进入 `ISSUE-GENERATION-20261014-f086`；前者需要明确兼容导入策略，后者需修正采样或字段定义。详见 `storage/report.md`。

## 5. 配置/工具隔离与HTTP请求边界

固定Codex的 [config loader](https://github.com/openai/codex/blob/092d3acd6bec3e3a14bdc7e7a2810ab628ab759d/codex-rs/config/src/loader/mod.rs#L270) 仍读取system config，并在后续组合managed/MDM层；隔离HOME/CODEX_HOME只隔离对应用户目录，不排除系统层。本轮没有读取实际系统配置，故不宣称线上加载了某项设置。

[environments字段](https://github.com/openai/codex/blob/092d3acd6bec3e3a14bdc7e7a2810ab628ab759d/codex-rs/app-server-protocol/src/protocol/v2/turn.rs#L211) 的空数组禁止environment access；[工具注册](https://github.com/openai/codex/blob/092d3acd6bec3e3a14bdc7e7a2810ab628ab759d/codex-rs/core/src/tools/spec_plan.rs#L1220) 的PlanHandler仅依赖update_plan_enabled，clock/sleep等也有独立条件。因此空environment不等于零工具。项目还独立关闭shell/web/hooks/apps/plugins等；这些限制不能全归因于空environment。

`Rpc.receive`拒绝server method+id请求，只证明拒绝该客户端请求通道；内部工具执行不是必经此通道。MCP注册存在，但本轮未完成其有效绑定与实际可达性证明，不声称账号操作已执行任意MCP工具。隔离准确表述与“零工具”保证差距进入 `ISSUE-ISOLATION-20261014-40ac`；未来修复先明确需要禁止的能力，不自动绕过上游managed policy。

HTTP依据：[RFC9110 §9.2.1](https://www.rfc-editor.org/rfc/rfc9110.html#section-9.2.1)、[WHATWG Fetch](https://fetch.spec.whatwg.org/#http-cors-protocol)。README规定可信个人网络，账号Origin检查不等于客户端认证；本轮不新增公网服务/鉴权要求。

`Handler._handle_codex_accounts` 检查Host/Origin/Sec-Fetch/自定义头；restart分支不经过该检查，GET/HEAD refresh共同进入 `refresh_endpoint`。隔离真实HTTP handler、仅替换最终动作后：

| 请求 | 状态与动作读数 |
| --- | --- |
| 跨源Origin账号POST | 403，动作0 |
| 跨源Origin、text/plain、无账号自定义头restart POST | 200，重启调度1 |
| 跨源Origin refresh HEAD | 200，强制刷新1 |
| 同源restart POST | 200，重启调度1 |
| 未知POST route | 404，动作0 |

已证服务端写入口边界不一致，且HEAD触发显式动作与safe-method语义不符。没有用真实浏览器/生产重启证明每个浏览器均可利用；CORS/PNA/Local Network Access可能限制发送，响应不可读也不能反推动作没发生。建议统一现有写操作Origin边界、主动refresh改非safe method；记录 `ISSUE-HTTP-20261014-b6d4`。

`_serve_static` 做路径规范化，并有意允许web/vendor指向已安装资产。因此依赖可信checkout/vendor，不是对任意symlink目标的OS隔离；本轮未证明远端能改写该symlink，不直接判目录逃逸漏洞。

## 6. 进程、SSH/rsync、launchd与Git部署

依据：[POSIX kill](https://pubs.opengroup.org/onlinepubs/9799919799/functions/kill.html)、[wait](https://pubs.opengroup.org/onlinepubs/9799919799/functions/wait.html)、[Python subprocess](https://docs.python.org/3.13/library/subprocess.html)、[OpenSSH](https://man.openbsd.org/ssh.1)、[Apple launchd](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html)、[Git ls-remote](https://git-scm.com/docs/git-ls-remote)、[ls-files](https://git-scm.com/docs/git-ls-files)。Git网页直接下载失败后经Exa读取正文。

| 具体问题 | 规范与实现链 | 判断与条件 |
| --- | --- | --- |
| kill -0能否认定PID属于Monitor？ | 只检查存在/权限；CLI is_running/stop只读PID，不核启动身份 | 不能。隔离PID文件指向另建sleeper，真实stop函数令其SIGTERM退出(-15)，却输出stopped/exit0；未操作用户进程。需在signal前验证稳定归属 |
| group kill是否回收所有后代？ | supervised_worker用start_new_session；_stop对组TERM/wait/KILL；finally只在leader未退出时_stop | 正常leader退出后的后代保证不成立：fixture leader退出0、同组后代仍活。实际worker通常join线程，未证明生产自然遗留，也不覆盖自行setsid的后代 |
| 同PID exec如何终止worker？ | stdin pipe是生命周期通道，非继承FD在exec关闭，watch_parent遇EOF后signal本组；server先清账号/quota/QueryView | 依赖watcher已启动及写端无外部继承。已读server路径未显式调用sync_process.shutdown，不能以其docstring作证；实际兜底EOF。未证明所有exec/后代时序 |
| SSH超时是否等于远端作业结束？ | sync共享monotonic deadline，BatchMode/ConnectTimeout和严格host/temp形态，finally有界清理；subprocess timeout杀本地直接子进程 | 本地有界不等于远端终止回执；未制造真实远端超时。/tmp age reaper不是活跃作业lease，不能证明全部远端派生进程结束 |
| rsync完成是否等于业务接受？ | installer进一步核manifest/digest/source binding/schema/retention | 分层成立。传输成功不证明新鲜度；digest不是独立签名认证，源可信仍依赖SSH及配置绑定 |
| launchd loaded是否可用？ | 核plist/loaded owner后bootout/bootstrap；明确非零最多3次，timeout不盲重试；输出区分loaded/reachability | 输出边界正确，没有自动事务回滚保证。bootstrap失败可已卸旧job；未操作真实launchd，未承诺零停机 |
| main==origin/main是否足够？ | deploy.preflight核primary/main/中间态/dirty/隐藏flags，live ls-remote精确SHA，往返后重查本地 | 源身份检查按Git语义成立，仅限检查时点。-v lowercase/S检出隐藏标记；不是文件系统锁。runtime依赖、服务与页面需另验，deploy已说明source check≠health |

PID反例及后代/远端终止边界进入 `ISSUE-PROCESS-20261014-3e90`，后两项不自动要求新增远端supervisor或更高可用性机制。发布仍按 [服务操作](../operations/services.md)，本轮不触发部署。

## 7. 四项指标来源边界

| 原始问题 | 对照生产方与实现后的结论 | 仍未知及后续归属 |
| --- | --- | --- |
| Claude JSONL是否总有最终usage？ | **未建立此保证。** 2.1.296主UI/SDK的LKt在stop_reason=null时暂缓写；runForkedAgent/subagent/background经Ghe直接入队，队列可先序列化不完整usage，后来内存更新不改已写字符串；Monitor递归扫描subagents且接受该观察 | sidechain允许同uuid再追加；未排除会话后续复制/导入/动态callback补写，所以不称永久低估。需完整CLI离线stream重放或闭合所有写入回边才能证明终态。解析/来源维护者负责，不从1猜出丢失的9 |
| Codex fork/revert是否正确？ | 六版本zero-baseline范围沿用已有固定源；普通byte-bounded paginated revert新建物理rollout、保留logical ID和旧已付费记录。独立账本10+10、退回首10再调用5，parser得25、1 logical session、3 keys，符合已发生消耗定义 | 只核0.162.1普通revert、1层祖先、2物理文件，不外推nested/缺父/跨版本全部形态；不把撤销上下文当退费。遗留无边界历史继续source error |
| quota limit_id是否始终Codex？ | **不是。** 主动legacy视图优先codex、缺失取第一个桶；项目map.codex or legacy可把other91当Codex。被动生产链逐桶发RateLimits→latest_rate_limits→TokenCount，项目取最后且丢limit_id，codex21→other91得到91 | 应按目标桶选最新；目标缺失为未知。无ID旧格式兼容需对应生产规则。nullable accountId拒绝正确，但“请更新CLI”未获上游因果支持，不可回填本地身份；记录quota issue |
| 历史价格、fallback和服务模式能否还原？ | requested/effective settings模型不等于实际server model；六个已查Codex版本明确不持久化ModelReroute，usage无actual model。价格cache的fetched_at、bundled verified_at不是effective period；Claude speed/geo等未进入归一记录/公式 | 只能报记录金额/名义估算，不是历史账单。reroute同费率可仍金额一致，不能推断既有金额必错。补实际模型、模式、有效期需要上游信息，不靠算术恢复 |

Claude证据与§2同一二进制。直接摘取LN/LKt/queue函数、独立设provider最终output=10：sidechain drain-before-final保存1，after-final保存10，main-held保存10；精确基线parser分别1/10/10且source_errors均空。覆盖writer2种、顺序2种、场景3个、版本1个，是局部生产代码时序反例，**不是完整CLI终态E2E**。三条实时循环的delta不补写，但appendEntry允许同uuid再追加；source_errors=0只能表示未检测到已定义错误，不证明来源最终完整。SDK [cost tracking](https://code.claude.com/docs/en/agent-sdk/cost-tracking) 与 [session storage](https://code.claude.com/docs/en/agent-sdk/session-storage) 本身不足以替代这条持久化论证。

Codex依据：[revert_thread](https://github.com/openai/codex/blob/092d3acd6bec3e3a14bdc7e7a2810ab628ab759d/codex-rs/thread-store/src/local/revert_thread.rs#L105)、[ServerModel处理](https://github.com/openai/codex/blob/092d3acd6bec3e3a14bdc7e7a2810ab628ab759d/codex-rs/core/src/session/mod.rs#L3946)、[transient policy](https://github.com/openai/codex/blob/092d3acd6bec3e3a14bdc7e7a2810ab628ab759d/codex-rs/rollout/src/policy.rs#L160)。旧版policy固定SHA：0.147.0=`be6e8eac029b183056b7e4402879f15d2c85f61b`，0.152.0=`316795b3cf2a45e90d121d9f46499d4658b2645c`，0.153.4=`3d2ee51ca2d5db578f328aa75e20aa22c0197c9a`，0.154.0=`6b9826e3aa83b1a5947db50f4332cb9c65f1b340`，0.158.0=`064c6b8c737f5b41d171fdda80bd9ef10ad06eb3`。这是离散版本集合，不是连续版本范围；旧版只补核mismatch和policy，不宣称其revert全验。

配额依据：[多桶协议](https://github.com/openai/codex/blob/092d3acd6bec3e3a14bdc7e7a2810ab628ab759d/codex-rs/app-server-protocol/src/protocol/v2/account.rs#L331)、[legacy选择](https://github.com/openai/codex/blob/092d3acd6bec3e3a14bdc7e7a2810ab628ab759d/codex-rs/app-server/src/request_processors/account_processor.rs#L1233)、[多桶header/event解析](https://github.com/openai/codex/blob/092d3acd6bec3e3a14bdc7e7a2810ab628ab759d/codex-rs/codex-api/src/rate_limits.rs#L27)。主动目标桶存在/缺失2态，被动codex-only/other-last2态均有离线读数；未查生产其他桶频率。`quota_refresh`与`codex_accounts.quota_reading`同选择表达式，机器刷新只核源码，未起真实app-server。

价格依据：2026-10-14 [官方pricing](https://platform.claude.com/docs/en/about-claude/pricing)、[fast mode](https://platform.claude.com/docs/en/build-with-claude/fast-mode)。当日Opus4.8 input标准5、fast10 USD/百万token；合成100万input、0output、无cache/recorded cost，项目两模式均5。此1模型×2模式、1provider、1日期反例证明缺少实际模式感知；标准名义估算仍自洽，不等于发现实际账单少算5美元。SDK记录cost也是客户端估算；不能因recorded优先就叫权威账单。

Claude终态、reroute及价格来源补充到既有 `ISSUE-METRIC-20261014-716c`；配额桶及nullable身份文案进入 `ISSUE-QUOTA-20261014-c5e7`。完整生产方路径、固定二进制偏移及复现见 `history/report.md`、`accounts/audit.md`。

## 8. 覆盖和维护

runtime既有定向套件为 `tests.test_codex_account_http`、`tests.test_account_lifecycle`、`tests.test_sync_process`、`tests.test_production_deploy`：30项，隔离HOME、-B，17.818秒，无失败；不替代规范论证，也未覆盖全部新反例。HTTP方法2种、Origin2种、路由4种；进程场景2种；存储重放位置3种、lease状态2种、死亡相位2种。解释器/SQLite/OS各1值；checkpoint模式、fallback注入点、archive失配点、publication延迟各1值，不外推跨平台/全部时序/生产通过。

更新时按具体问题找到对应版本生产方，再更新实现映射和结论。动态官方页面记录日期，CLI私有格式以固定源码/二进制身份为准。问题与后续归属在 [general issues](../issues/general.md)。本轮完成逐项审查及文档，产品修复、生产发生率、依赖升级与新部署均未执行。
