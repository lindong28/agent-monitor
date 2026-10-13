# Published main deployment

## 目标与授权

用户要求：“以后我每次说允许部署到生产，默认就是用 origin 的 main head 部署，那也就是接受 push 了”；随后明确“就按照你的建议写 plan, 然后执行”。本轮落实跨项目的发布与部署纪律，以 agent-monitor / MacMini 为首个实际部署验证；llm-gateway 接入相同源版本检查并保留双 remote 发布要求。2026-10-13 用户确认 Monitor 未完成 rebase 是本人操作、已无人处理，授权本 session 接手。

授权覆盖完成本次改动所需的普通发布和部署；不含 force push、改写已发布历史、覆盖其他人的 WIP、发布无关未移交提交。生产授权包含目标项目既有发布流程明确要求的 remote；Gateway 的 origin 与 philo-ai 须发布同一提交。配置仓按自身持续发布授权执行。其他项目复用共享流程，不在本轮批量改造。

## 缺口与改进

1. Monitor 主树停在外部 `pull --autostash --rebase` 冲突中，main ref 为已发布的 850bff8，工作 HEAD 是 b8a705d。先保存 rebase 元数据、冲突文件、index、refs 和 state symlink 身份到本机持久目录，再中止这次多余的 rebase，核对恢复结果；不重写已发布 merge 历史。
2. 共享规则仍把部署和 push 许可分开。把用户新授权记入同一份跨 harness AGENTS；在现有工作树流程中补齐 publish → 回读远端 main → 部署顺序及失败处置，不改权限 hook、不改全局 Git 配置。
3. 安装入口没有检查源版本是否已发布。两个项目各自提供自包含的生产入口：核对主 checkout、main、无 Git 中间态、无 tracked WIP，实时查询 origin/main 并要求 HEAD 相等；支持指定本次发布 SHA，防止多机部署漂移。检查失败须在安装/重启前退出。保留原有安装入口的本地开发与 staging 用途，不新增跨产品或 harness 运行时依赖。
4. 部署 checkout 只做 fetch 与 ff-only 同步，不在其内开发或自动 rebase。分叉、冲突、未发布本地提交应转回自有开发树处理；未知归属先确认。部署从已回读的同一 SHA 开始，结束分别报告源码、安装/服务和真实消费读数。

## 实施与验收

- [x] 保存并恢复 Monitor rebase 现场，保留 state。
- [x] 在 ai-agent-config 自有 worktree 修改共享政策/流程；项目自身文档指向生产入口。
- [x] 实现两个项目的生产源检查入口；用隔离本地 Git 仓验证允许已发布 main，以及拒绝 dirty、私树/分支、未发布/落后版本、中间态与指定 SHA 不符；失败路径不执行 installer。
- [x] 独立审查本次政策/部署决定及最终实现；仅修承重缺陷，运行受影响既有测试。
- [x] 提交、整合、普通发布并回读；MacMini 从 origin/main 快进，执行 Monitor 生产入口，核对 CLI、launchd 与实际 collect/export；Gateway 本轮仅发布入口，未执行线上部署。
- [x] 文档记录实际结果、未完成项及归属。工作树清理属于交付收尾，回执保存在本机持久证据目录。

## 隔离、档位与执行

主执行者负责实现与跨仓整合，原生独立 reviewer 负责必要 gate。开发只在各仓 task/published-main-deployment 私树；测试用临时 HOME、Git fixture 与假 installer，不读取 provider 凭据。共享资源部署由主执行者串行操作。

本地 plan/文档准备为 A0/V0；生产入口及权限语义按 R=max（生效后作用于生产与发布授权）、G=standard（需保护现有服务）取 A2/V1，沿用用户已确认的生产 main/push 边界，不扩张到迁移或切流。部署前独立对抗审查、每个改行为有针对性验证；Git 外部调用在真实临时 bare remote 上执行。既有已知测试基线失败另列，不冒称全套通过。

最小真实消费者路径：恢复 checkout 后，经新入口从已发布 main 更新 MacMini CLI/rollup。前置发布身份检查来自用户“一律用 main head”的要求；测试和单次独立审查来自生产入口的档位。其余节点未纳入本次 rollout，禁止把 MacMini 读数外推。

显式版本及项目专门部署契约优先。Gateway 本轮入口接根 install.sh；公司 site.json pin、deploy/manage.py 及 operator 配置同步机制不变。独立决策审查已放行，七项成立；生产入口实测见下述结果。

## 当前状态

Monitor rebase 已中止，主树恢复 850bff8；tracked clean，state inode 102763913 与目标不变。现场及 history.bundle 已验证保存在 ~/.local/state/published-main-deployment-20261013/rebase-backup，无 autostash。共享规则提交 4db4cb5a 已快进整合并发布 origin/main。

两项目新入口各 16 项测试通过；Monitor 既有 installer 9 项与 Gateway install_health 3 项通过。tracked 变化取值 3、中间态取值 7、非 main 状态取值 2、版本关系覆盖相等/未发布/远端前进/分叉；隔离 HOME 与真实本地 bare remote，不读取 provider 存储。移除 remote equality 的变异副本造成 3 项失败，证明该检查可被测试捕获；不据此声称并发写入或所有运行环境已覆盖。独立 reviewer 复跑 Monitor 16 项通过。最终 gate 放行，无 HIGH/CRITICAL 或承重未知。

审查将部署 Git driver 的有效 V floor 取 V2：真实隔离 Git 路径覆盖解析与比较，只有 installer 为无副作用替身；真实服务效果见本轮 rollout 读数。无新增长期服务，源检查失败同步显示且不启动 installer，不增加后台告警机制。检查只在部署前发生，一次远端 ref 查询，不扫描业务数据库或进入请求热路径。

唯一 MEDIUM finding：assume-unchanged / skip-worktree 可隐藏 tracked 修改，两种 flag 各一个实测样本会漏检。真实 MacMini 本轮 174 个 tracked 条目均 H，没有触发前提；当前不阻塞。已向用户异步询问是否加拒绝检查，未获答复前不扩大修复；两个 README 记录该限制，owner 为项目维护者，后续动作为实现 flag 拒绝并增加对应回归。独立 findings 无。

## 执行结果

Monitor 6e09a52 已整合并发布 origin/main；Gateway 3faed1f 已整合，origin/main 与 philo-ai/main 回读一致。MacMini 通过新 deploy.py 完成源检查及安装，CLI export --version 返回完整 6e09a52 SHA；launchd 仍从主 checkout 执行 collect，间隔 120 秒，运行次数 38 → 39，最终 not running、无 pid、last exit code=0。state symlink inode 154502586 及目标不变。实际 cached-quota export 成功，schema_version=2、exporter_commit=6e09a52e5f5ab217ea83a517a1f6dde315df6b7b、generated_at=2026-10-13T06:26:07.941476Z；不据缓存额度声称新的 provider 读数。

本轮仅 MacMini rollout，Gateway 公司版本 pin 与现有服务未改。44 项相关测试通过，不是全套测试声明；历史测试基线问题不在本次修复范围。唯一待裁决项仍为上述隐藏 index 标记补强，未获答复前按已记录的无标记环境交付；无其他未处置 finding。

部署证据须先有已发布 commit 才能取得，因此结果记录单独提交，之后再次发布并把 MacMini 快进至最新文档提交；最终 SHA 与回执保存在 ~/.local/state/published-main-deployment-20261013/，不通过反复把当前文档提交 SHA 写回自身制造提交循环。实际运维记录见 docs/operations/services.md 的 published-main deployment 小节。

## 中立复核后的修复（2026-10-13）

用户明确要求立即修复两项缺口，此前的索引标记待裁决状态结束。复核发现个人配置仓的两个 adapter 仍调用旧 install.sh，原改进没有覆盖真实消费者；两个生产 wrapper 也会放过带隐藏索引标记的修改。该结论修正前述“无其他未处置 finding”，历史归档保留当时记录。

修复限定为：两个 deploy.py 自动拒绝 assume-unchanged / skip-worktree；配置仓的默认完整安装接入 deploy.py；Gateway 在写运行配置之前先做 source preflight；检查缺失或拒绝时不回退。Monitor 显式 --no-services 保留 staging，Gateway --config-only 保留原语义。不开新服务、不增加产品间依赖，不改公司显式 pin，不自动处置 Git 标记或 WIP。

已先取得失败测试，再实现修复：两个产品各 17 项源检查测试通过（新增两种 flag × clean/dirty 四组合），配置仓 Monitor adapter 5 项、Gateway personal config 10 项通过，共 49 项。测试使用隔离 HOME 和临时仓；产品源检查用真实 Git，安装器为无副作用替身。独立复核另核真实 adapter → deploy.py → 假 installer 的完整链路。发布与 MacMini 实装后取得的最终 SHA/消费回执仍保存在同一持久证据目录；不把本地测试当生产验证。
