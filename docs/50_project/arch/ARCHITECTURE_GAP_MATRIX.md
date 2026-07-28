受众：MASTER
标题：Natsume AI 当前架构差距矩阵

# Natsume AI 当前架构差距矩阵

版本：v3.46
核对日期：2026-07-28
机器状态：[current_architecture_status.json](../../../configs/release/current_architecture_status.json)
总体状态：`INCOMPLETE`

> 当前活动波为 `NE-08 / DL-06_LIMBIC_LEGACY_AFFECT_WRITE_RETIREMENT_SLICE`。数字生命产品缺口优先于剩余通用架构验收：canonical Life kernel 已可将 typed Appraisal 折叠为 bounded mixed self-affect，且 ASGI Input/Tribe composition 已把 `AffectFusionService` 接入真实代码路径；有 Life authority 时不会解析 compatibility `HostEmotionRuntime`。DL-03 还将 accepted reply 的 provisional relationship episode 与下一次用户输入的 aftereffect 交给同一 canonical Life journal，关系以 workspace + subject 连续，边界、未接住和修复不能被沉默、投递失败或单次道歉自动消解，Surface 只消费可信内部 hand-off。DL-04 已在该 hand-off 后冻结 scope-bound `AffectiveTurnPlanV1`：普通低歧义回合可通过真实 provider stream 逐句输出，句子在可见前先进入 canonical Life intent，模型候选和外部 emotion hint 都不能覆盖 self-affect。DL-05 又在该同一 plan/revision/sync 上持久化 Voice `SPEAK`、Avatar `TRANSITION` 及 `CANCEL/RELEASE` 的 Host protocol；0146 projection 只证明 Host staging，不能证明远端播放或 Avatar 已执行。DL-06 现要求 Scheduler 主动消息显式 `ALLOWED`、无 repair/active episode/self-regulation hold，并以 canonical self-affect 适配既有 impulse/risk 算法；accepted reply 还会原子登记 scope-bound `CompanionProactiveTargetV1`，Scheduler 只读选择 frozen-Scene-compatible target，旧 Bond/legacy affect/fixed template/`last_target` 均无发送权。已完成的 `proactive.gateway` Host/Gateway receipt 现在经 outbox、摘要、scope 与 target 重验后才打开 deterministic `PROACTIVE_MESSAGE` episode；receipt 后崩溃仅以只读扫描补记 relation command，绝不重发。0147 则把低风险 companion profile 置于同一 authority：只有五条独立、跨三天、强且 boundary-safe 的 aftereffect 才能发起五互动 canary，CANARY/ACTIVE identity 真实冻结到文本、TTS 与 Avatar Host plan，第五条合格 canary 后效应会自动 activation，随后只能精确回滚到此版本原定的旧 profile。普通 `0.72` 文本分类不能自动累积此证据。production canonical Tribe authority 现会阻断 Limbic 的 legacy persistence、metabolism、timeline、decay 与 Bond callback，使它们返回 `RETIRED_CANONICAL_LIFE` 而不再形成平行 self-affect；这仍不等于全量旧 writer 清零。Endocrine/BodySense/Motive/Bond queue、Monitor/Review、受治理 Budget stream、连接恢复、真实 Edge receipt、真实模型和真实服务验收均未完成。下文的 NE-07 记录是历史服务栈基础，不能据此把情感数字生命标记为完成。

**0148 修订：** 当前子波已改为 `DL-06_AUTOMATIC_PROFILE_CANARY_ACTIVATION_SLICE`。Host 的
`POST /v1/digital-life/relationship/companion-profile` 已真实接入独立 `host.companion-profile`
Secure Ingress surface；它要求 `companion:profile:write`、exact user/relationship subject 与
tenant/workspace 绑定，仅从 canonical strong aftereffect history 启动 profile CANARY，并把 audit ref
持久化到 immutable head。它不是聊天语义推断、直接 activation 或 Monitor/Review 控件；剩余缺口为
 canary 长期收益、面向用户的 rollback/Review 控件、兼容 writer 清零、provider/Edge 和完整服务栈验收。

**回应反馈修订：** `POST /v1/digital-life/relationship/response-feedback` 已作为
`host.companion-feedback` 独立 Secure Ingress surface 接入 Host。它要求
`companion:feedback:write`、exact user/relationship subject 与 tenant/workspace
绑定，并只接受指向仍为 active provisional episode 的 `life-event://` 或
`life-delivery://` receipt 的四种 bounded outcome。signed `HELPFUL` 写入
receipt-scoped `0.95` aftereffect，且仅可推进既有 profile CANARY；第五条合格 canary 后效应才由 canonical lifecycle 自动 activation；它不能从聊天文本
生成 evidence、创建或指定 activation profile、改写 Soul、consent 或 self-affect。该路径已有真实
SQLite、route、跨 scope、service principal、malformed body 与 restart replay 的定向证据；
它不是 Monitor 用户界面、人工 Review、canary 长期收益、旧 writer 清零或真实服务栈验收。

**签名回滚修订：** `POST /v1/digital-life/relationship/companion-profile/rollback` 已接入独立
`host.companion-profile-rollback` surface。它要求 `companion:profile:rollback`、exact
user/relationship subject、tenant/workspace 和 current active profile identity；body 不含历史 target，
canonical lifecycle 只恢复 active profile 的 frozen predecessor。immutable rollback command 带有
audit-derived `source_control_ref`，故 duplicate、claim-result 中断和重启只重开同一 receipt；stale head、
service principal、cross-scope 与 target injection 均拒绝。这是本地 Host/SQLite 合同证据，不能升级为
Monitor/Review、canary 长期收益、旧 writer cutover 或真实服务栈通过。

**Host 情感运行时切断修订：** production `ProcessApplicationPorts` 与 Host root 已删除
`emotion_runtime_factory`，不再发布 mutable `HostEmotionRuntime` 或 global
`user_emotion_profile`。ASGI 将 lazy Life command provider 同时传给 Input 与 Tribe；二者在
production request 上以 canonical `AffectFusionService` 读取/写入 scoped Life journal，旧 runtime
只可在明确 offline/test compatibility composition 中使用。此为一条 writer 的定向收口，不包含
Limbic、EmotionPersistence、BodySense、Endocrine、Motive、volatile Bond queue、Monitor/Review 或
真实服务栈验收。

NE-07 已开始，但目前只完成隔离外部服务栈预检：三个默认镜像固定 digest，
运行期注入 Neo4j secret，project/container/network/port/data root 全部与
用户长期 `natsume-ext` 栈隔离，真实 `docker compose config --quiet`
通过。提交 `14f015bd` 的独立容器又完成真实写入、重启、持久读取、
更新/查询与清理，三库子 Gate 为 `PASS`，receipt digest 为
`sha256:7af6e6a0…2f71`。Host/Obs/Nginx Monitor/Edge/Gateway、故障和
性能验收尚未运行，不能把该子 Gate 提升为完整架构 `PASS`。

Host/Obs/Monitor 的固定 `runtime/host` 与跨实例误杀缺口已在代码层收敛：
显式 `RuntimeRoot` 隔离 PID、日志、runtime env 和 Nginx prefix，显式模式
的 ForceRestart/stop 不做全局 Host sweep；runtime env 也不再保存 secret
值。首次真实启动暴露 Workspace HMAC 只有明文入口，Host 在短暂健康后退出，
而旧脚本仍返回成功；该次必须记为 `FAIL`。代码已改为 `sec://` 引用解析，
并在 sidecar 启动后连续验证 Host PID/监听/health/readiness 与真实 Obs/
Monitor 端点，失败定向回收 acceptance 根。尚缺修复提交上的真实重跑、
SQLite migration、网络/浏览器回执和重启恢复。
`b6b341be` 的第二次跑次确认 secret ref 已生效、新 Gate 正确失败关闭，又
暴露 Service Catalog 的中间 rollout candidate 被旧首探针提前接受。当前
首探针和最终五轮 Gate 均要求最终 catalog stage、`host.main` owner 与两类
identity digest；不得以 readiness、metrics 或端口监听兜底。第三次真实启动
据此正确失败关闭，rollout ledger 的三个 AUDIT operation 均为
`ROLLED_BACK`，head 为空且 lease 已释放；新阻断是 acceptance Workspace 根
位于源码 worktree 内部。既有 allocator 门禁保持不变，启动器新增独立
`AgentWorkspaceRoot` 和进程启动前的父/子/同目录重叠拒绝，合法外部根才写入
runtime snapshot。`b56d4d78` 的第四次启动确认该修复成立，但显式
provider=disabled 使 Governor/LLM required health 失败，AUDIT 以
`SERVICE_CATALOG_POST_HEALTH_FAILED` durable rollback。当前 health 只把
“非 disabled provider 已选、显式允许 cold start、且 required failure 全为
缺外部凭据”映射为 `DEGRADED`；本地 authority 或混合失败仍是 `FAILED`。
`f48afe1b` 上的第五次真实 Host 进程启动已使五阶段 rollout、Host、Nginx
Monitor 和真实代理路由启动；唯一 Host required failure 是缺 OPENAI 凭据
的 cold start，未伪造模型 PASS。但 Obs projection daemon 的 Life job 因
空源 cursor=0 payload 使用 wall clock 而持续 digest conflict，所以栈整体
仍未通过。loader 已改为同 cursor 的确定性空投影，待重启验证。该跑次还发现
runtime snapshot 过滤密码但
残留 Neo4j 用户名，导致直接复用时在进程前拒绝半套凭据；writer 已改为只
保留原子 `sec://` auth ref，真实 snapshot restart 尚待重跑。
Monitor 依赖还有 8 项 npm audit 告警，因此该项保持
`HOST_ENFORCE_START_PASS / OBS_FIX_AND_RESTART_PENDING`。

第六次 `ForceRestart` 进一步暴露 Host 被强杀后 30 秒 embodiment authority
Lease 无法由 shutdown hook 释放：新 candidate 单次 acquire 失败并退出，
启动器仍空等约 240 秒，旧 Obs/Nginx 留在端口。当前代码只对精确 authority
不可用做 35 秒有界等待，过期后要求更高 fence，live heartbeat 不可抢占；
启动器在替换前先定向停止同 runtime root 的 sidecar，并在首次探针期间检测
launcher 退出。定向集合 `33 passed in 34.32s`，但尚未实栈重跑，因此当前
口径收紧为
`HOST_ENFORCE_START_PASS / CRASH_TAKEOVER_FIX_PENDING_RERUN /
OBS_FIX_PENDING_RERUN`。

NE-01 的剩余差距已从“本地代码/恢复未封口”收窄为 NE-07 部署验收。
当前提交的七路线同库收据全部 `PASS`，24 个去重恢复用例通过；因此实现依赖
允许进入 NE-02。NE-01 仍不是 production `PASS`：signed feature promotion、
真实 HTTP/network/provider、外部 namespace、Docker 服务栈和回滚演练尚未
执行。Budget `DEGRADE/REVIEW` 明确为 proposal-only，未知副作用明确要求
签名对账；二者不再作为需要“自动执行/自动归因”的伪缺口。

一般 mixed-terminal 恢复缺口已从“未证明”缩到部署/路线级边界。真实进程
矩阵现在覆盖 retry+hard failure、Budget overrun 与 Review→deny 在 outer
settlement 前硬退出；新 Host 均为零 resolver/worker replay 和唯一 Graph
terminal。过程中发现并修复了 Budget terminal block 永久进入 survivor 的
缺陷：`BLOCKED` admission 只有仍含 ready/retry/review/execution/cancel
节点时才进入 Host inventory。当前仍缺生产 fan-out 开关 promotion、
Scheduler/Internal/Operator 真实进程 canary、完整 Workspace 终态矩阵与
NE-01 波次集成，因此总体状态不升级。

一般恢复缺口进一步缩小，但没有消失。现在真实进程证据覆盖：
cancellation-bound `COMMIT/RELEASE` 的目标提交后 handler 崩溃；以及
`ACCEPTED` 兄弟与 `RESERVED/RUNNING` 未知兄弟并存时的 fan-out 崩溃、
live defer、expired hold、签名重获、选择性续跑和 terminal zero replay。
生产缺陷也已修正：checkpoint continuation 不再把节点内部 `done` 与最终
Graph `done` 同时暴露给 Host，而是统一交给 parallel settlement。尚缺的是
其它 mixed terminal/Review/Budget 图形、部署签名 key、开关 promotion、
波次 canary 和七路线故障矩阵；因此 `GENERAL_MULTI_NODE_RECOVERY` 仍为
`PARTIAL`，bounded fan-out/cancellation 仍为 `OFF`。

签名 Operation 的 handler 响应丢失缺口已进一步缩小：真实 OS 进程证明
checkpoint `COMMIT` 和无 intent raw-failure `RELEASE` 都能在目标 UoW
提交后、handler 返回前崩溃，并由新进程零 worker 精确重放；终态 Host
再次启动也不重入。0131 case binding 已成为相关集成证据的强制前置条件。
该项只移除两种 action 的 handler-crash 子缺口；cancellation-bound
组合、自动 unknown-effect attribution、部署并发 fan-out 与整波 canary
仍留在 NE-01。

Scheduler 的“先执行 pending、再查询 due”缺口已在当前工作树关闭。
Repository 以一个 SQLite read transaction 冻结两组输入，并生成包含
capture time、limit、顺序与深层 JSON 行的 content-addressed batch；
Clock 在执行前重开且同轮不再查询数据库。七路线 reader 同时补上
Command/WorkOrder/TaskGraph digest 与控制 policy 的独立重开，避免仅凭
surface/route 字符串判 PASS。该项从缺口矩阵移除，但 Scheduler 仍缺一般
unknown-effect/multi-node recovery、全程序 Budget 度量、完整进程重启和
部署 canary。clean SHA `2a88f56e` 的七路线正式收据已重开上述 batch 与
三条 control policy 并通过，但路线状态仍为 `INCOMPLETE`。

Scheduler、Internal、Operator 的“同一 `delegate` 字符串承载三种隐含执行
语义”断点已在当前启用入口收敛为显式
`ControlRouteExecutionPolicyV1`。政策绑定 command/target/owner、
Review 层级、Budget、verification 和 Effect owner，并在执行、重放、
Review、恢复、Verifier/Acceptance 与 Effect 编译处重开。Operator 保留
Intent admission Review，不能再叠加 TaskNode Review；Scheduler trigger
和 reviewed Internal 继续精确绑定节点 Review。TaskStore/Review 使用同一
clock。同一 Operation version 的 current recovery case 也被约束为唯一，
避免签名 reconciliation 因 Budget 版本推进产生歧义。该项只关闭已启用
控制路线的 policy/replay 漂移；一般
multi-node recovery、整波 integration、部署 canary 和 NE-07 仍是缺口，
因此三路和 NE-01 状态不升级。

Operator 旧库存缺口已经从“23 个 active-but-blocked 命令”改成“23 个
明确退役 ID 的替代路线尚待后续波次完成”。生产 manifest 只有 14 条已治理
命令，blocked 数为 0；退役 ID 不存在于 handler、OpenAPI 或 Monitor 生成
客户端。它们的 replacement authority 由数据合同冻结，BFF 对旧 ID 返回
410 而不进入 Secure Ingress。差距仍然存在于 Edge/Tool/Internal 的扩展
能力及 NE-05/06 的 version activation，但不能再把不存在的写入口统计成
产品能力。后文 `14/37、23 blocked` 只保留为历史证据。

Plan 与 Delegate 的当前 enabled-scope 局部断点已经关闭。
Plan 新增 content-addressed execution policy，要求每次初始执行、重放、
Review continuation、startup/operation recovery 与 Effect 编译都重开
Graph/revision、Graph Budget、节点 policy 和唯一 result effect owner。
Delegate 新增 content-addressed target policy，当前 `pentest_agent` 必须先
durable Review，内部用量精确为零，且 dispatcher/executor 双层拒绝任何
Effect、Outbox 或 delivery claim。真实 SQLite 已证明 Delegate
`WAITING_APPROVAL → continuation → ACCEPTED`，定向集合为 `89 passed`、
四路线集成为 `1 passed`。干净提交 `2a88f56e` 的同库七路线 v3 收据又重开
Plan 的三节点 Attempt/Budget/Effect owner 和 Delegate 的 Review continuation；
矩阵已把两条路线的当前启用范围提升为 `COMPLETE`。其它 Delegate target 与
通用 NE-02 PlanningSearch 收益仍不在该范围内。

Skill 的预算与副作用边界已经在代码层完成穷举分类：canonical Host
Registry 的 93 个条目分为
28 个 inline read-only、13 个 pre-Acceptance proposal-only、3 个
post-Acceptance Workspace 和 49 个 pending typed Effect。只有 44 个
启用条目进入 manifest/search/scheduler/admission；缺 policy、descriptor
漂移与 stale graph 都失败关闭。所有启用 Skill 先消费 Task precharge，
Agent 模型必须提交 canonical measurement，proposal-only 不得投递可见
结果，三条 risk-4 写 Agent 仅经 Acceptance 后 Workspace promotion。
本地代码缺口已清零，干净提交 `c68850f8` 的 commit-bound 同库 v3 收据也
已生成；机器矩阵已把 Skill 当前启用范围的 Budget 与 transactional Effect
两项控制提升为 `PASS`，路线状态为 `COMPLETE`。49 个 blocked 能力不属于
完成范围，也没有取得执行权。

旧 Personality Registry 的 19 个 metadata 仍保留目录用途，但 governed
manifest 为 0：5 个明确 blocked，14 个 `UNCLASSIFIED`。动态 MCP Skill
也固定 blocked。这关闭了通过默认 `SkillMeta` 值重新获得执行权的旁路，
但不等于这些旧能力已迁入 canonical Runtime。

Skill 的双执行 authority 缺口已在代码层收敛：Executive executor 被物理
删除，token-only `SkillLoader` 与 Scheduler metadata 路径只失败关闭，
handler、sealed authority binding 和 Broker dispatch 各只有 app-local
governed executor 一个生产调用者；receipt 写失败不再被吞掉。干净提交
`3d68bd1eca69ad1593454634c2999d07b4d4f2f5` 的 `209 passed`、真实
Universal Host Runtime、static/doc 和交付元数据 Gate 已把
`canonical_execution_authority` 提升为 `PASS`。该路线保持 `COMPLETE`；
NE-01 总状态仍由其余三路和一般恢复缺口约束。

Chat 的模型用量已经从 GR05 假投影改为真实生产等价调用链：frozen route 调用 Surface runtime，Surface 调用 canonical `ModelAttemptService`，terminal provider 返回 usage，Task Runtime 再把同一测量原子记入 Graph aggregate 与 Attempt-bound child ledger。只读验收新增 ModelAttempt→WorkerProjection→parent/child usage 的逐字段校验，replay 也必须保持 provider 调用数不变。真实链路暴露的预算 guard 泄漏和 COMMIT audit path 漂移已经修复。干净提交 `3d68bd1e` 的正式收据已清零该缺口，Chat `budget_enforcement=PASS` 并成为第一条 `COMPLETE` 路线；外部 provider 质量仍不在此证明范围。

七路线缺少同一提交、同一真实数据库证据的断点已经关闭。GR05 在同一 Host Runtime 运行 Chat、Skill、reviewed Delegate、三节点 Plan；剩余驱动顺序调用真实 Scheduler、Internal 和 Operator 应用边界。最新运行绑定干净提交 `2a88f56e2dc7c68d7f37100c6c2d408bc2fdf6a0`，查询同一文件型 SQLite 的 v3 validator 严格重开 admission digest 与三条 control policy，判定七路全部 PASS、migration head=0135，并形成仓库内正式收据。七路 `commit_bound_acceptance` 控制均已清零；Chat、Skill、当前启用 Plan/Delegate 范围为 `COMPLETE`，其它 `PARTIAL` 控制继续使 Scheduler/Internal/Operator 和 NE-01 保持 `INCOMPLETE`。

Review 拒绝后的“多个事务逐步关闭终态”断点已经关闭：0135 把 exact terminal Review status/version/digest、Node、Graph、graph Budget、三条 Outbox、可选 0134 Acceptance/Attempt 与已有 `BLOCKED` decision 写成 append-only settlement。PRE Review 必须零 Attempt，POST Review 必须绑定 `SUCCEEDED` Attempt；提交故障整体回滚，响应丢失或重启只重放 settlement 且 Worker 调用保持不变。七路线提交绑定已通过；尚未清零的是一般 cancellation/recovery 与波次集成。

节点 Acceptance 的“判断存在但没有持久决策”断点已经关闭：0134 把 exact Graph revision、Node、Attempt、规范 judgement digest、evidence 与 Outbox 写成 append-only decision。自动接纳的 decision、artifact、可选 Effect 和 `ACCEPTED` Node 原子提交；需要人工判断时，decision、等待 Node、ReviewItem 与 Node Outbox 原子提交，直接单节点图还同事务进入 `BLOCKED`。重启按 node version 重开该事实，Scheduler snapshot 也暴露它；pending Review INSERT 只有一个共享 SQL owner。后续拒绝终态由 0135 唯一收口。

节点 Verification 的持久化断点已经关闭：0133 把 exact Graph revision、Node、Attempt、规范结果 digest、evidence 与 Node Outbox 写成 append-only decision，Node transition 同事务提交；`VERIFIED` checkpoint 重入只读重开该事实。直接拒绝的 Node、Graph、graph Budget 和 Outbox 也由一个 UoW 收口，响应丢失 exact replay 与终态 identity drift rollback 已用真实 SQLite 证明。多节点拒绝仍交给 0090 唯一 Graph settlement，不形成第二 writer。七路线提交绑定已通过；尚未清零的是一般 failure/cancellation/reconciliation、低层兼容 API 删除与 NE-01 wave integration。

Plan 当前启用路径的节点审批缺口已经清零：`require_confirm` 能力不再被候选生成器丢弃，审批要求从 registry snapshot 经 candidate policy 进入 compiler，并最终精确绑定每个 immutable TaskNode ID。Scheduler 只阻塞目标节点；0096 continuation 消费一次性 Review 后续签尚需运行的 exact grant，同时保留审批前已 accepted projection 的历史 grant authority。缺失策略、字符串布尔、catalog provenance 漂移和续跑 grant 混用都失败关闭。content-addressed policy 与 `2a88f56e` 最新正式收据又关闭 authority、Budget 和事务副作用，故当前 `CAPABILITY_SEARCH_V1` 范围为 `COMPLETE`；一般跨域搜索/收益属于 NE-02，不作为该路线的隐性缺口。

通用 worker 的假验收缺口已经从当前启用路径移除：一个 route-neutral verifier/acceptance 组合分别重开 durable projection，并校验 immutable authority、receipt/COMMIT、WorkOrder/context 与输出合同。未知自然语言条件进入 Review；Plan 合成节点还必须消费 digest-bound 前驱 artifacts。Delegate/Plan 的 `verification` 原子控制因此为 `PASS`。生产唯一启用的 `pentest_agent` 又以无模型/工具/嵌套派发/Effect 端口的只读 adapter 和 exact-zero、完整 Task-lineage-bound usage attestation，把当前目标的 Delegate `budget_enforcement` 提升为 `PASS`；外层一次 canonical dispatch 仍正常预扣。0132 已关闭“多节点共用一条不可归因 graph Budget”的当前断点，七路线 commit-bound acceptance 也已 PASS；一般 Delegate、七路线 Budget policy、Review/recovery、事务副作用以及 Plan 的通用候选生成仍为 `PARTIAL/MISSING`，路线总状态不升级。

最新 locator inventory（2026-07-25）为生产 consumer/import/module/field/lock/ref 全 0。container、facade、environment 与旧 wiring 已物理删除；ASGI/CLI fresh 构造 ports 并显式传入 Host root，pytest-only bridge 不进入生产对象图。`HostAuthorityRuntimeOwner` 继续持有 canonical ledger、Memory、Model 与 Capability 图；ResourceManager status 已由 0123 Host-fenced Runtime Event journal 持久化。`TribeRuntimeScope` fresh 持有 state registry、Host registry、event bus 与 23-Agent catalog。scope bind 不再发布进程级 active context；lifespan/background execution 与 HTTP request 通过 `ContextVar` 选择 exact owner，两个重叠 Tribe execution 返回不同 Agent/registry，同 execution 跨 scope、active execution 解绑与 stale inherited context 均失败关闭。23 个 Agent backing singleton/setter 与三类 shared runtime fallback 已物理归零；无 exact context 稳定拒绝。Chat 的本地 assistant/LifeMemory 投影屏障和七路线 commit-bound acceptance 已清零，但 bus、完整 Host multi-app canary、外部 Chroma/Neo4j namespace、Token/MCP durable restart、其它外部资源恢复、完整 Worker/Obs Composition、路线总控制与跨服务 delivery 验收仍未清零，不能据此证明通用 Delegate 或 NE-01 已完成。

Host 情感连续状态和回复形态额度的 module-global authority 也已归零：一个 Host root 精确拥有一个 `HostEmotionRuntime`，一个 Input composition 精确拥有一个 `ResponseFormDecider`；replay/render 归一化不推进状态。该项关闭的是进程内对象所有权与并发计数缺口，不是 durable 情感画像、长期人格演化、完整 multi-Host canary 或 NE-01 封板。

本文件是“目标与当前状态如何解释”的唯一入口。历史 receipt 保留原样，但只能证明其声明范围；本矩阵按当前生产代码重新定级。

当前分支的 fresh 全量静态检查继续以本提交 Gate 结果为准。连续 migration head 为 `0146_affective_expression_delivery.sql`。0132～0135 的 Task Budget/Verification/Acceptance/Review denial 语义保持不变；0136 新增 PlanningSearch cohort evidence/proposal；0137 新增 runtime-fenced 长期目标 event/head；0138～0140 新增 proposal-only trusted Lab、redaction 与 sanitized dataset；0141～0142 新增 Skill/version activation authority；0143～0146 新增七海的 affect、关系、文本及 Voice/Avatar Host protocol projection，均不改变 Task execution authority。

Scheduler 管理 HTTP 的 create/update/delete/enable/disable 已在 `/scheduler` 与 `/host-scheduler` 双前缀统一切入 app-local governed runtime：每个写请求冻结 Secure Ingress method/path/body、definition head 和 claim，生成 `HostCommand(surface=SCHEDULER)` 与不可变 TaskGraph，只有 post-Acceptance `operator.state_control` Effect 可以写 canonical repository；不可变 admin operation receipt 与 claim 之间的崩溃可由新实例精确恢复。路由中的直接 Scheduler writer 为 0，read 路由保持只读，trigger 保持既有 governed occurrence。

当前切片没有自动推断副作用；它只在签名明确证明 `NO_SIDE_EFFECT` 后，把目标节点失败从单节点扩展到多节点图。目标 Operation/Attempt/Node/Lease 在第一 UoW 收口，Graph/Budget 仍由既有 0090 决策在重启 fan-in 时唯一终结，READY sibling 不执行。其它运行期 mutation、其它 failure/cancellation、mutable Scheduler batch、易失 Endocrine 事件缓冲/last state、真实 executor 与波次集成仍未完成；七路线 commit-bound 验收已由当前正式收据关闭，但 NE-01 仍保持 `PARTIAL / IN_PROGRESS`。

Workspace 正向写链现覆盖 Governed Internal 与三条已登记 risk-4 Direct Agent Skill。Skill payload 不能携带 V3/Workspace/Review/fence authority；server 从 frozen Task lineage 重建 WorkOrderV3，worker 只提交 sealed ref/digest，Graph Acceptance 再用先于可见 Effect 的 `agent.workspace_artifact_promote` 调用共享 0101/0116 owner。Accepted handler 重开 immutable admission、projection、Node/Attempt/Lease/NodeArtifact、Preview、Effect 和 seal 后才生成 deterministic accepted HEAD，配置源分支保持不变。真实 SQLite+Git 已验证 Skill handler 的生产 composition 与重启持久读取；既有 exact replay、seal 漂移、lifecycle rollback 证据继续覆盖共享底层。Domain Pack、其它 Skill、一般恢复、handle-level TOCTOU、OS/container tenant 与跨路线 canary 仍在差距清单中。

最新 `dev` 已含 exact Internal 单节点取消子流、Host/Internal lifecycle survivor、Operation 中断持久分类与 governed `REACQUIRE`。当前分支在同一 `task_operation.reconcile` 命令内新增 signed checkpoint `COMMIT`：只接受 canonical worker-projection receipt，读取层与 Operation mutation UoW 双重校验 immutable admission/Graph/Node/Attempt/scope/fingerprint/digest 和旧 Lease expiry。真实 SQLite 证明错误签名、live Lease、投影 UPDATE/DELETE 均拒绝，首次 COMMIT 响应丢失后 exact reopen 仍继续原图且 worker 调用数为 1。该状态不等于部署 key/HTTP canary、一般 unknown-effect、其它取消、多节点/批次恢复、事务化 Chat 或七路线验收完成。

Edge command 的旧全局 client owner、startup 注册和 Tribe/内部直推调用点已归零；首条正向 Obs 路径现把 `edge.somatic + emotion_state_command` 经 Secure Ingress、risk-4 Review、TaskGraph、Acceptance Effect 与 terminal Preview 写入 durable embodiment outbox，并支持 exact replay 与只读 receipt reconciliation。连同 Operation、独立 general Effect、Task/Review control、Scheduler/Host mode-policy state control，以及 0137 长期目标激活/撤销，当前 Operator 为 `16 active / 23 retired / 39 known / blocked=0`；更多 Edge command type 及真实 Edge/Gateway 服务仍是缺口。Scheduler HTTP 以独立 surface 计数，不虚增 Operator 库存。

## 1. 状态定义

| 状态 | 含义 |
|---|---|
| `WIRED` | 生产 composition 与真实调用路径均已接线，关键失败模式有证据 |
| `PARTIAL` | 有真实实现或局部接线，但缺少范围、策略或恢复语义 |
| `BROKEN` | 看似接线，但真实首次调用会违反当前合同或失败 |
| `UNSAFE_DIRECT_WRITERS` | 一个或多个生产 writer 绕过目标要求的 proposal、Review、版本激活或 rollback authority |
| `CENTRAL_FENCE_WIRED` | 旧写入已在最终 authority 边界中央拒绝并有静态门禁，但替代的受治理写入闭环尚未建立 |
| `SHADOW` | 只观察/模拟，不影响生产选择 |
| `PLACEHOLDER` | 只有占位策略、字段或空壳接口 |
| `MISSING` | 没有满足目标语义的实现 |
| `HISTORICAL_PASS` | 仅对固定提交与固定范围成立的历史证据 |

## 2. 执行与规划

| 目标 | 当前状态 | 代码/证据 | 实际缺口 | 执行波 |
|---|---|---|---|---|
| Host `chat/skill/plan/delegate` | `ENABLED SCOPES COMPLETE` | `cortex_runtime.py`、`governed_task_adapter.py`、`2a88f56e` v3 receipt | Chat、当前启用 Skill、`CAPABILITY_SEARCH_V1` Plan 和只读 `pentest_agent` Delegate 已有完整声明控制与提交绑定证明；其它 Skill/Delegate target 不计入启用范围，通用跨域 PlanningSearch 进入 NE-02 | NE-01 / NE-02 |
| Host 图 survivor continuation | `PARTIAL_LIFECYCLE_PAGINATED_EXACT_REENTRY` | `host_startup_graph_continuation.py`、`host_command_recovery_inventory.py`、`periodic_durable_continuation.py`、`governed_task_adapter.py`、`HostCommandRepository.list_nonterminal_admission_page()`、Host unit + 跨进程真实 SQLite integration | 完整 RuntimeGroup/Acceptance Effect owner 就绪后，repository 在每个只读事务中按 Host surface 和 `CREATED/ACTIVE/BLOCKED` 联结 admission/Graph；foreign surface 不进入 Host 页。immutable `(created_at_ms, command_id)` keyset cursor 绑定 surface digest，消费者验证页序、页尾 cursor、重复 command/Graph、每页与整轮上限；第 101 个 survivor 会进入下一页，不再令整批恢复停摆。独立进程写入 101 个 admission 后，新 Runtime 全部重开。boot pass 后 timer/wake 重用同一 continuation，live Lease/竞争只 `DEFERRED`，持久 Lease 到期后可在线再扫。已签名的多节点 `NO_SIDE_EFFECT` 节点失败可由 0090 重入终结；自动 unknown-effect 归因、其它 failure/cancellation 和一般 batch recovery仍缺，不能提升七路线、NE-01 或 production canary | NE-01 |
| Internal/Scheduler/Operator 控制面 survivor | `PARTIAL_RUNTIME_GROUP_PAGINATED_EXACT_REENTRY` | `composition/runtime_group.py`、`app/internal_startup_admission.py`、三类 governed runtime 的 `continue_startup_admission`、`internal_runtime_composition.py`、控制面 SQLite integration | Host scan 后、Scheduler clock 前，RuntimeGroup 以独立 surface digest 分页恢复三类控制 admission；surface/target 只能映射唯一 runtime，scanner 不合成 reconciliation Operation。App 注入 continuation/supervisor factory，Composition 无反向 App import；Internal startup 执行职责已拆分且未扩巨石白名单。mandatory scan 并发去重，周期 supervisor 由 composition root 持有，shutdown 先 join 活动 scan 再关闭 surface。101 条两页路由、三条真实 SQLite admission、并发 boot 与无 cancellation shutdown已验证。Internal 专用 sealed-checkpoint authority 保留；真实 worker 跨进程终结、一般 unknown-effect/failure/cancellation 和 wave integration 仍缺 | NE-01 |
| Host worker 进程重启 | `PARTIAL_SAFE_BOUNDARIES_OS_PROCESS_PROVEN` | `host_startup_graph_continuation.py`、`governed_task_adapter.py`、`preoperation_attempt_recovery.py`、`operation_checkpoint_continuation.py`、`test_ne01_host_worker_process_restart.py` | 真实进程硬退出后，新 Host 对 PRE-OPERATION 使用同 Attempt 高 fence 单次执行，对 COMMITTED checkpoint 零 worker replay；顺序依赖图保持已验收 predecessor，只恢复 child，第三进程零重放。真实 SQLite 的 Attempt/Lease/Operation/Verification/Acceptance/Artifact/Graph 已核对。worker 为受控生产协议 adapter；并发 fan-out、RESERVED unknown-effect、签名 reacquisition、其它失败/取消、控制面真实 worker、wave integration 和 canary 仍缺 | NE-01 |
| production Skill 工作单冻结 | `PARTIAL / DIRECT_AND_APPROVED_PLAN_NODE` | `governed_host_contracts.py`、`governed_production_payload.py`、`planning_execution.py`、`governed_frozen_skill_runtime.py`、`skill_execution.py`、GR03d/D16/GR05 定向测试 | `codex_production` 的 profile/default 与 V2 adapter payload 已在 direct `skill` 和 selected Plan node 的 admission 期按 node 冻结，并以 digest、root/graph/node/revision、scope/fingerprint、Review/approval lineage 重验；Plan 只接受 approved candidate input 与 immutable binding，caller-supplied frozen payload 失败关闭，replan 按新 revision 重建。执行期不再重新调用 normalizer；该 legacy adapter 不拥有 TaskGraph authority。签名多节点无副作用失败已有共享 runtime 证据；其它恢复类别、全路线 Budget/Receipt 与 commit-bound acceptance 仍缺 | NE-01 |
| Scheduler governed | `PARTIAL / ADMIN_AND_OCCURRENCE_GOVERNED` | Scheduler operation/clock owners、`governed_operator_state_control_runtime.py`、accepted state-control handler、双前缀 route cutover tests | occurrence 保持 post-Acceptance handler、immutable result ledger 与 0117 DUE owner/fence。管理 create/update/delete/enable/disable 统一经 Scheduler HostCommand/TaskGraph/Review/Acceptance；operation journal 先提交、claim completion 后提交，崩溃重启只读重开。路由直接 writer=0，提交绑定已通过。mutable batch freezing、通用未知外部效果人工归因、wave integration 与 canary 仍缺 | NE-01 |
| Internal Agent governed | `PARTIAL / INTERNAL_WRITE_PROMOTION_AND_SIGNED_RAW_FAILURE_CLAIM_BEFORE_CLEANUP` | `workspace_write_promotion.py`、`internal_raw_failure_claim_result.py`、`internal_artifact_terminal_reconciliation.py`、`task_operation_failure_reconciliation_store.py`、`internal_agent_sealed_checkpoint_recovery.py`、0116、0130、真实 SQLite/Git integration | sealed recovery 与既有 signed cancellation terminal 保持原 authority，提交绑定已通过。具备 exact Workspace authority 的 write 已密封 bounded manifest/binary patch，Acceptance 生成 deterministic accepted HEAD 并经 0116 晋升；一般 unknown effect、取消交叉、其它 failure/cancellation、Workspace 其它终态、handle-level TOCTOU 与 OS/container isolation 仍缺 | NE-01 |
| Agent Skill / 单写节点 Plan Workspace | `PARTIAL / DIRECT_AND_SINGLE_PLAN_WRITER_SUCCESS_FAILURE_CANCELLATION_LOCAL_PROVEN` | `governed_skill_work_order.py`、`governed_frozen_skill_runtime.py`、`plan_workspace_acceptance_effect.py`、`skill_artifact_terminal_reconciliation.py`、`workspace_terminal_cancellation_authority.py`、`agent_workspace_acceptance_effect.py`、`accepted_workspace_artifact.py`、真实 SQLite+Git integration | `agent_worker/self_heal/self_evolve` 冻结 registry risk 4 和 durable Review；caller V3/Workspace/Review/Lease authority 拒绝。Direct success/failure/post-execution Review cancellation 与 Plan 单写节点整图成功、下游失败、下游 post-review 取消均有真实证据。Direct 取消要求 Review decision/current `CANCELLED` Review、`COMMITTED` Operation receipt、finalized intent/decision、当前 Graph/Node 版本和零 Effect；Plan 取消要求 accepted writer 三快照同版本和零 Effect。raw `CANCELLED` 状态不再授权删除。源 HEAD 不动且正常路径再重启零重放。其它 terminal matrix、多 Workspace、Domain Pack、其它 Skill、完整进程/network canary 与整路线 receipt 仍缺 | NE-01 |
| 本切片结构债务 | `STATIC_PASS / PROGRAM_PARTIAL` | Acceptance/ASGI/Scheduler focused owner 保持原 writer、lifespan 与 repository UoW authority | 该历史静态通过不关闭旧 execution authority、一般恢复、七路线或 canary 缺口；生产 Service Locator 已由后续切片物理清零 | NE-01 |

| 全 route universal runtime | `CHAT + SKILL + PLAN + DELEGATE COMPLETE / THREE INCOMPLETE / SEVEN_ROUTE_GATE_ACTIVE` | v2 coverage manifest + `2a88f56e` v3 receipt + Cortex/Scheduler/Internal/Operator integrations | fail-closed Gate 精确覆盖七路并要求 commit SHA、migration head 与 route receipt 一致。当前启用四路已完整；Scheduler clock batch/control policy 已提交绑定，Internal 与 Operator 仍有各自部分控制。真实 MCP provider、视觉 governed executor、完整 Tool/Operator/Gateway matrix 与七路线 canary 均未完成 | NE-01 |
| Edge Operator 正向链 | `FIRST_GOVERNED_SLICE_WIRED / REAL_EDGE_CANARY_MISSING` | `governed_operator_edge_support.py`、`accepted_edge_embodiment_handler.py`、`acceptance_effects.py`、`HostEmbodimentRuntime`、真实 SQLite integration | 只允许 `edge.somatic + emotion_state_command`；Review 前零 outbox，Acceptance 后唯一 durable `set_expression`，duplicate exact replay 不重发，过期 claim 只读查 receipt。更多 Edge action、真实 Edge/Gateway 网络、设备和 NE-07 full-stack 未验收；记录型 async port 不是物理 canary | NE-01 / NE-07 |
| Executive authority 退役 | `RETIRED_FAIL_CLOSED / PRODUCTION_OFF` | Cortex ports、retired runtime/app guards、registry absence contract、legacy planning retirement contract、NE-05 Forge cutover contract | 生产入口、`route_planning`、`conversation_execution`、application `composition`、application port wiring、legacy diagnostic direct-skill matrix、legacy SkillDag worker 执行和进程内 registry 已删除；Cortex direct reply/post-process compatibility branch、Executive 内 Forge override call 与独立 Forge approval/runtime-match authority 均已删除，纯 SkillDag adapter 仅为零生产调用的兼容合同。cycle 必须以 `PLAN_CYCLE` 阻断，兼容投影只能返回空 batch | 旧执行/Forge authority 已清零；真实外部 canary 与全程序验收仍待 NE-07 |
| 多节点 Durable DAG | `PARTIAL / SIGNED_NODE_FAILURE_PLUS_0090_AND_GENERAL_SHAPE_CHECKPOINT_RECOVERY_LOCAL_PROVEN / PRODUCTION_FANOUT_GATE_OFF` | Coordinator、TaskStore、0090-0102、0130、checkpoint/failure reconciliation、parallel settlement、真实 SQLite 双/三 owner、four-root、五节点菱形、八根节点与双根失败 integration、0124/0125 SQL authority | canonical Node Acceptance 与 Graph 成败复用 0091/0090 UoW。双 checkpoint、shared-EXCEEDED missing、four-root mixed settlement、五节点 fan-in/fan-out 与八根节点 batch 均按 immutable graph order 收口，worker 不重跑。签名 `NO_SIDE_EFFECT` 失败先只关闭目标节点，再由 0090 在重启时关闭 Graph/Budget；双根并发额度1证明 READY sibling 零调用、v4 receipt 稳定、0090 Outbox 篡改失败关闭，并发额度2的双失败证明非 selected 节点仍由 decision outcomes 覆盖。0130 继续持久化 conflict/unknown/cancellation crossing。无限规模、其它 failure/cancellation、自动归因、部署 fan-out receipt、inventory/key/canary 仍缺 | NE-01 |
| Cancellation Effect 对账 | `WIRED_DEFAULT_OFF / CANARY_NOT_RUN` | 0092 effect fence；0098 case/proof inventory；Effect reconciliation contract/service/store；Operator command + candidates/cases/challenge；integration/unit/contract tests | `task_effect.reconcile` 的 Ed25519 proof exact 绑定 case/intent/effect/fence/Attempt/resolution/actor/Outbox/revision；`NO_SIDE_EFFECT → CANCELLED`，`SIDE_EFFECT_CONFIRMED + receipt → COMMITTED_EVIDENCE`。PENDING late writer 被同事务 fence；FINALIZED correction 只恢复 trusted read。生产 gate 只阻断实际持久化 legacy inventory；仓库无 SQLite 资产，不能证明外部部署已清理，且部署凭据/key lifecycle/canary 未完成 | NE-01 B5C deployment |
| General Acceptance Effect 对账 | `SIGNED_GENERAL_ACCEPTANCE_EFFECT_RECONCILIATION_LOCAL_PROVEN / DEFAULT_OFF` | 0128 evidence/case/proof；`governed_effect_reconciliation_runtime.py`；Accepted handler；focused Store；真实 SQLite integration | `GENERAL_UNKNOWN_EFFECT` 不依赖 cancellation intent。Secure Ingress admission 前验签并 append evidence；WorkOrder/TaskGraph/Review/Effect 只持 ref/digest；风险 4 Review、Preview、Verification 和外层 Acceptance 后重开并重新验签，再在 canonical UoW 中写 signed proof、target Effect 与 Outbox。Review 前零 proof，exact replay、drift 和 UPDATE/DELETE 围栏已证明。该路线不自动判断副作用，也不替代 cancellation-bound 模式 | 部署 key/credential、HTTP/network canary、联合 readiness 与 NE-01 wave integration |
| Operator Task/Review 控制 | `LOCAL_REAL_SQLITE_TARGETED_PASS / DEFAULT_CANCELLATION_OFF / PROGRAM_PARTIAL` | `governed_operator_control_*`、`operator_control_acceptance_effect.py`、`accepted_operator_control_handler.py`、Review Store/command service、真实 SQLite integration | `task_graph.cancel` 与 `task_review.approve/reject/resume` 已删除 Obs 直接 writer；四者独立进入 HostCommand/TaskGraph，并只在 terminal Preview 与外层 Acceptance 后修改原 Task/Review authority。取消必须到 `FINALIZED` 才提交外层 Effect；approve 只公开 public token ref，普通 bearer 不落库，重开 Store 后 Operator resume 仍一次性。历史 approve/cancel exact replay 零目标重入 | Graph cancellation 仍默认 OFF；完整进程重启、部署 credential、HTTP/network canary、wave integration 与剩余 `23/37` Operator 命令未完成 |
| Operator Scheduler/Mode 状态控制 | `LOCAL_REAL_SQLITE_TARGETED_PASS / PROGRAM_PARTIAL` | `governed_operator_state_control_runtime.py`、`operator_state_control_acceptance_effect.py`、`AcceptedOperatorStateControlHandler`、Scheduler repository、0129 Host policy journal | Operator 的 `scheduler.task.enable/disable` 与 `sys_mode.auto_rules.update/event.submit` 各自进入 HostCommand/TaskGraph；Scheduler HTTP create/update/delete/enable/disable 复用同一 accepted effect，但使用独立 `SCHEDULER` surface 和精确 HTTP authority。Review/Preview/Verification/Acceptance 后才写 canonical owner；operation exact replay、1001-event mode-policy replay、tamper、released fence、append fault rollback和 claim-gap restart 已验证 | 完整 Host 进程重启、部署 credential、真实 HTTP/network canary、一般恢复和 wave integration 未完成 |
| Historical terminal reconciliation | `AUTO_EXACT_ON / LOCAL_SQLITE_CANARY_PASS / OPERATOR_OFF / HTTP_CANARY_NOT_RUN / DEPLOYMENT_UNVERIFIED` | 0099 readiness、classifier、startup inventory、真实 SQLite canary、Secure Ingress command、主 Host/独立 Obs query-only routes | 隔离 canonical 双 case restart 已证明 exact FAILED 自动 proof + Budget release，signed-required CANCELLED 不改写并阻断 readiness；完整 cancellation 依赖束用临时 verification-only 公钥通过 preflight。签名 Operator HTTP、部署 inventory/key lifecycle、Operation/Effect canary、general/multi-node recovery仍未完成 | NE-01 B5C deployment |
| Budget 全策略执行 | `BLOCK_RUNTIME_FAIL_CLOSED / ATTEMPT_CHILD_RESERVATION_AND_PARENT_CONTENTION_LOCAL_PROVEN / KNOWN_INTERRUPTION_CLASSIFIED / PROGRAM_PARTIAL` | `budget_runtime.py`、`task_node_budget_store.py`、`task_node_budget_usage_bridge.py`、`task_budget_usage_store.py`、`node_execution_runtime.py`、0132 migration、真实 SQLite parent/child/retry/restart tests、`test_ne01_budget_policy_runtime_boundary.py` | Graph Budget 保持唯一 aggregate ceiling；每次 Attempt 在 READY claim 与 Lease 同一事务获得 exact child reservation。普通 usage、唯一 EXCEEDED winner、terminal sibling observation 与 retry 在同一 UoW 推进父子账本，响应丢失/重启只重开同一 binding；旧 Attempt 按历史 usage 回填且不恢复容量。concurrency 是 active Lease/child reservation 的可释放容量，不是累计 usage。正数 `time_ms` 与模型 guard 继续读取 canonical remaining。合同层 DEGRADE/REVIEW 在首次 Store/Outbox mutation 前稳定拒绝，不再被静默当成 BLOCK；尚缺全部 cancellation/release 组合、预算例外或 replacement revision 驱动的真实 DEGRADE/REVIEW、program-wide 七路线 policy、一般 unknown-effect recovery 与真实 provider canary | NE-01 |
| Governed Chat Surface 模型预算 | `PASS_FORMAL_CLEAN_SHA` | `ModelAttemptService`、WorkerProjection、parent/child Budget、`3d68bd1e` v3 receipt | Host Chat 主模型调用消费 frozen remaining，缺量测、超限、缓存或 drift 均失败关闭；正式收据证明 ModelAttempt→WorkerProjection→父子账本和 replay。Cortex 准入前模型、其它路线和外部模型质量不属于该控制 | NE-01 |
| Governed Agent Runtime 模型端口 | `PARTIAL_TASK_BOUND_MULTICALL_BUDGET_WIRED / REACT_SKILL_DELEGATE_FAIL_CLOSED` | `executor_registry.py`、`model_runtime/governed_budget.py`、`agent_runtime/executor.py`、`agent_runtime/agent_executor.py`、Agent budget/ReAct tests | sealed governed Skill 经 CapabilityBroker 后取得 composition 的 ModelAttempt ports，并保留 canonical Budget JSON；read-only V2 WorkOrder 由 trusted Task context 重建为 V3。Internal 与 Skill Agent loop 的每次请求共享逐次扣减的 node-bound guard，legacy 直调及缺 Budget/usage/端口关闭。普通 Tool action、真实 provider acceptance、逐工具 TaskNode/Receipt、revision compiler、wave integration 与 commit-bound 七路线仍未闭环 | NE-01/NE-02 |
| Governed 普通 Skill 模型预算 | `PARTIAL_DECLARED_MODEL_SKILL_TYPED_PORT_WIRED` | `skills/registry.py`、`model_runtime/governed_skill_budget.py`、`app/governed_skill_execution.py`、`governed_frozen_skill_runtime.py`、`test_ne01_governed_skill_model_budget.py` | 普通 Skill 不再自动取得裸 ModelAttempt authority；只有 `model_budget_required` 声明能从 ACTIVE Task Budget 派生 typed port。每次调用缩减 remaining，服务端 attestation 绑定 request/Budget/Graph/Node/Attempt，外层重验后才进入 WorkerBudgetUsage。当前正向接线为 `emergent_growth_weekly`；缺量测、cache、重复、超限和 lineage drift 均失败关闭 | 真实 provider、其它模型 Skill inventory、legacy 非 governed 调用、Persona proposal/activation、program-wide coverage 与 wave integration 未完成 |
| Operation / Attempt 在线 continuation | `ATOMIC_INTERRUPTION_RECOVERY_HOLD_LOCAL_PROVEN / SIGNED_MULTINODE_NODE_FAILURE_LOCAL_PROVEN / GENERAL_SHAPE_CHECKPOINT_RECOVERY / 0131_CASE_BOUND_AMBIGUITY_RESOLUTION / GENERAL_RECOVERY_INCOMPLETE` | failure/checkpoint reconciliation、`operation_interruption_case.py`、`dag_scheduling_runtime.py`、0090、0130 hold codec/support/store、0131 binding、governed Operation handler、真实 SQLite/Git integration | 已知中断在一个 SQLite UoW 写 `RECOVERY_REQUIRED`、Operation Outbox、0130 case 与 case Outbox；case INSERT 故障使 marker 整体回滚。Runtime 只读重开同事务 case，worker Budget settlement 与 Lease/lifecycle 后续变化不使未对账 case消失；Graph cancellation 用 intent-bound successor替换 current unknown projection，同时保留两条历史。签名 `REACQUIRE/COMMIT/RELEASE` 保持既有 mutation authority；UNKNOWN/CANCELLATION current hold 必须经 0131 exact case-bound evidence、risk-4 Review 与 Acceptance，handler 在 mutation 后确认 case non-current。五节点菱形与八根节点 checkpoint 可跨 owner 续接。任意未绑定 external receipt、未签名普通 `RELEASE` 仍拒绝；checkpoint conflict quarantine。部署 key/HTTP canary、其它 failure/cancellation、无限规模、Workspace 终态和 wave integration仍缺 | NE-01 |
| ACCEPTED→Effect restart | `PROVEN_NARROW_FINALIZATION` | `accepted_graph_finalization.py`、`test_ne01_accepted_effect_finalization_recovery.py` | exact checkpoint下原子补写且幂等；不代表跨系统 Effect dispatch 已全局事务化 | NE-01 |
| 通用 Acceptance Effect 对账 | `PARTIAL / INDEPENDENT_CONVERSATION_EFFECTS_AND_AUTHENTICATED_SURFACE_QUERY` | `acceptance_effect_handlers.py`、0118、Preference repository、Gateway reply query、dispatcher、Guardian observer、tests | store gate缺失/非bool失败关闭零写；配置false/malformed env→bool false→`DISABLED`零event/profile。启用时event/append-only child profile/receipt同UoW，sealed-child trigger + full-set reconcile拒绝篡改；推荐反馈严格按admitted-time、exact conversation/session与canonical delivery digest绑定。Gateway从同SQLite payload校验effect_id，`NOT_FOUND`保持`UNKNOWN`。一般unknown-effect人工归因、commit-bound evidence、wave/canary仍缺 | NE-01 |
| Review 自动 continuation | `WIRED_TASK_BOUND / POISON_STARVATION_CLOSED / ATOMIC_DENIAL_TERMINAL_LOCAL_PROVEN / CANARY_NOT_RUN` | `review_runtime.py`、ReviewStore、0096、0135、`review_continuation_poison.py`、`ReviewContinuationGuardianObserver`、surface/ingress/takeover/500-prefix tests | task-bound PRE/POST 已接；损坏协调行在自己的事务中稳定分类、版本化退避并形成一个 durable Guardian incident，500 条坏前缀后健康行可在下一轮前进。拒绝/过期/取消 claim 只传 exact node identity、无 worker grant；0135 原子终结并支持 exact replay。尚缺独立 terminal DLQ/Operator 修复 UI、真实 canary、Operator credential、一般 cancellation/recovery 与 multi-node/batch 自动 recovery | NE-01 当前切片 |
| 对话全副作用提交 | `PARTIAL / LOCAL_PROJECTION_BARRIER_PASS / EXTERNAL_EVENTUAL` | Acceptance Effect 顺序 + exact Life intent/receipt barrier + Gateway reconciliation | assistant turn 与 LifeMemory 已成为外部 reply 的 durable 前置条件；失败/篡改/重启保持 UNKNOWN。外部 delivery 仍是幂等 eventual，不是跨服务单事务；Chat commit-bound acceptance、波次与真实网络仍缺 | NE-01 |
| 单一任务真相 | `WIRED` | Durable Task projection + registry absence contract | 进程内 registry 与 accessor 已删除；read route 不创建任务 | NE-01 已完成切片 |
| Persona 演化单一 authority | `CENTRAL_FENCE_WIRED` | `PersonaEngine` 运行时拒绝、`T-PERSONA-FENCE-01` 全仓扫描、无副作用候选回归 | legacy writer 已归零；proposal、shadow、Review、版本激活与 rollback 替代链尚未实现 | NE-01 围栏已完成；NE-06 全量替换 |
| 通用 PlanningSearch foundation | `PARTIAL_RULED_OBJECTIVE_CAPABILITY_SEARCH / D20C_ACCEPTANCE_PENDING_EXTERNAL` | `tribe/thalamus_agent.py`、`governor/schemas.py`、`planning_search/`、`orchestration/goal_planning.py`、`orchestration/capability_plan_search.py`、`planning_graph_compiler.py`、`planning_execution.py`、0106/0107/0121 | Goal/VirtualStateSnapshot、SuccessorCatalog、shadow benchmark 合同与 context-owned immutable compile 已落地；Thalamus 只对明确 Research/调研与 Frontend/可用性计划产生 server-owned mode/tag/risk，编译后经 Governor schema/binding，Host 才从 composed Registry 冻结至少两项 host/免确认/低风险/标签匹配能力，L1/L2/条件 L3 在虚拟态选中两项不同 Skill 的有界顺序路径，再生成 `approved_candidate_inputs` 并编译 `skill → skill → chat` immutable TaskGraph。显式 `failure_replan` 仅在 canonical `FAILED` predecessor 与 evidence 重开后准入同 root 的 revision `+1`；不会自动触发或按失败自适应换候选。仅建议、禁止工具、普通 Chat/通用目标、Code/Pentest 不会进入此执行路径。D16～D20c 的 Code/Research 仍为 pure shadow/实验资产；D20c 仍未接入真实 credential/provider、prompt/workload、独立 verifier、cohort、收益或 promotion authority | NE-02 |
| Code 动态 shadow 候选 | `PARTIAL_D16_DYNAMIC_CATALOG_SHADOW_ONLY` | `planning_search/code_domain_models.py`、`planning_search/code_domain.py`、D16 unit | 冻结 opaque ref/digest 后可动态产生 inspect→registered patch→unit/rollback；MCTS 仅在冻结 `virtual_model_ready` 时具备 simulation ports，catalog-derived inputs 才可进入 immutable compiler | 没有真实 worktree/ACL、source/model/tool/test/provider 调用、Task admission、历史 paired A/B、receipt 或 production influence；中间 state replan lineage 未设计 | NE-02 |
| Host 候选方案生成 | `PARTIAL_RULED_OBJECTIVE_CAPABILITY_SEARCH_V1` | `tribe/thalamus_agent.py`、`governor/schemas.py`、`orchestration/capability_plan_search.py`、`planning_search/capability_domain.py`、`app/governed_task_adapter.py` | 明确 Research/调研和 Frontend/可用性计划由 server-owned rule 填入 mode/tag/risk，Thalamus 编译与 Governor 严格 schema 都保留并绑定它们；Host 再读取 app-composed Registry 的非执行 metadata，拒绝 legacy steps、少于两项竞争能力、确认型/越风险/越标签候选和伪造 selected path；搜索在虚拟态中选择两项不同能力，第二项依赖第一项，综合节点依赖两项并以 immutable `skill → skill → chat` 图入场；显式 failure-replan 使用 canonical durable recovery 创建同 root revision `+1` | 仅建议/禁止工具、通用目标/Chat、Code/Pentest 仍不切流；自动或 failure-adaptive replan、真实模型/收益证据和 production promotion 仍未完成 | NE-02 |
| Host 能力规划风险 authority | `LOCAL_TARGETED_PASS / NE-02_PARTIAL` | `orchestration/capability_plan_search.py`、`governor_grant.py`、`test_ne02_capability_plan_search.py` | 请求风险只能收紧：effective ceiling=`min(intent, Governor approved binding)`；grant ref 被冻结到 capability virtual state，缺失/畸形 binding risk 在纯搜索前失败关闭 | 当前仅约束 Research/Frontend capability catalog；不代表一般 Plan、跨域候选、真实收益或 promotion | NE-02 |
| Pentest L1/L2 | `WIRED` | `pentest/agent/agent_loop.py` | 仍需与 durable outcome/calibration 对齐 | NE-02 |
| Pentest Conditional MCTS | `SHADOW_DYNAMIC_SUCCESSOR_LOCAL_TARGETED_PASS` | `l1_graph_search.py`、`planning_search_adapter.py`、`virtual_planning_world.py`、0105 calibration | executable root 只含当前真实 gate 通过动作；同一 current-state policy 生成但缺 capability 的已登记动作进入 virtual-only successor catalog，并仅在纯 transition 解锁后展开。catalog digest/count 写入可选 durable shadow prediction，L3 仍不能影响真实选择。30 次授权实验、verified execution receipt、安全统计和 promotion Gate 尚未完成 | NE-02 |
| Research 真实收益 | `D20C_ACCEPTANCE_PENDING_EXTERNAL / BENEFIT_UNPROVEN` | D20a `ResearchTaskSnapshot`、`ResearchSuccessorCatalog`、`FrozenResearchCorpusLoader`、D20b `ResearchCitationVerifier`、D20c `AttestedExternalResearchAdapter`、`ResearchPairedExecutionRunner`、`ResearchBenefitCohortEvaluator`、生产 transport identity、canonical `model_attempt_log` lookup、0107/0121 ledger owner | 公开规划状态没有 dedicated locator/source-body/answer-key/verifier-decision 字段，constraints 拒绝这些命名 key，但任务文字不是内容级 DLP；loader 只可读取可信实验根+manifest 列出的 repository-relative 普通文件并公开 metadata receipt。D20b 对 snapshot/manifest/source id/ref/digest/tier 做 exact handshake，并用每次新建的 loader 校验 citation 的 UTF-8 byte span/digest；D20c 新 RunSpec 的 order 只由 owner 在事务内 CSPRNG 生成，raw cohort key 不入账本，历史规格只读；runner 只按这份实际顺序运行、保存 private prompt/output 于进程内并在 fresh verifier 后写 0107 evidence；prompt digest、工具/token/成本/时延与 aggregate budget 均需精确匹配。真实 provider adapter 在实际调用点生成不含原始 URL 的 endpoint digest 和 external-HTTPS decision；D20c 要求 result 与重开的 audit 同时绑定该 identity，本地/私网/CLI/未知 transport 及历史无 identity receipt 都不能计收益。新增纯 cohort 协议固定 24 项、三主题、本地/Web、独立 hidden verifier、10,000 次 paired bootstrap，以及质量/citation/严重错误/含 verifier 成本/p95 门槛；失败保留原因，通过也只 proposal-only。仍没有 cohort SQLite owner、真实 provider credential execution、production prompt/workload、来源获取、网页抓取、生产 route/TaskGraph/Host/BrowserWorker 接线、真实 24 项运行、收益报告持久化或 promotion authority；本地 model double 和纯 evaluator 都不算收益证据，也不是 OS/tenant/sandbox | NE-02 |
| Code 真实收益 | `FIXED_20_PROTOCOL_LOCAL_TARGETED_PASS / REAL_COHORT_UNPROVEN` | D16 catalog + D17 private-bare paired-worktree + D18/D19 terminal evidence + `code_benefit_cohort*` | 固定 20 项合同要求同 source/model/provider/profile/budget、owner-randomized 两臂、独立 fresh worktree/模型/hidden/static execution；收益为通过率 +10pp，或非劣 5pp 且成本 -15%，并强制 hidden/static/diff-risk 非退化。D19 失败 pytest 现与 PASS 一样在 verified cleanup 后持久化真实 terminal observation，避免选择偏差。定向 15 项通过；仍没有 cohort owner/migration、真实外部模型、20 项 evidence、独立部署 verifier 或 promotion gate | NE-02 |
| Pentest 30 次校准与 L3 promotion | `FIXED_30_PROTOCOL_LOCAL_TARGETED_PASS / REAL_COHORT_AND_ACTIVATION_MISSING` | 动态 successor adapter + `pentest_calibration_cohort*` + `promotion_gate.py` | 纯合同已固定 30 项授权/验证 evidence、当前 environment/scope/manifest/facts/policy/state/candidate 派生 catalog、MAE/容差/成本/p95 与三类零违规 Gate；跨域 Gate 必须再有 Research/Code 至少一域收益且只产生强制 Review proposal。定向 7 项通过。仍缺统一 append-only owner/migration、真实 30 次运行、真实非 Pentest 收益、Review/version activation/rollback；L3 继续 shadow | NE-02 |

0094 lifecycle 已按 fail-closed 收紧：activation rollback 或 `aclose` quiescence 未完成统一返回 `TASK_RUNTIME_LIFECYCLE_RETRY_REQUIRED`，锁死 bind/activate 并只允许 `aclose` 重试。Coordinator admission close、drain、`aclose` 任一失败都不关 Store；只有 Coordinator 与 worker 均静默后才 drain Outbox/关 infrastructure。这改善了故障关闭正确性，不改变 `PARTIAL_B5C_DURABLE_CANCELLATION_SHADOW / PRODUCTION_OFF` 定级。

TaskStore 应用层写 authority 封口的历史定向证据仍严格分组记录，不汇总成虚构总数：authority/structure `19 passed`；HostCommand `1 passed`；NLE/operation/heartbeat `77 passed`；acceptance-effect `15 passed`；parallel/settlement/atomic `45 passed`；terminal classification `32 passed`。Cancellation 组首跑是 `29 passed + 1 fixture failed`，修正 fixture 后该 case `1 passed`；integration 组首跑是 `46 passed + 3 fixture failed`，修正后 3 个 cases 分别通过。所有 fixture 修正/重命名后的 key regression 为 `26 passed in 35.33s`；该批次不覆盖前述首跑失败记录。本结构切片 fresh 主证据另为去重的 36 文件 `265 passed in 210.19s`、0 fail/skip；早期 61/73/102/54/14 不得与其相加。当前 full static=`PASS`、`failed_gates=[]`；波次 integration 仍为 `NOT_RUN_PER_WAVE_POLICY`，raw/orphan fixture 不是 canonical admission 证据。

Cancellation 生产权限接线不会改变该定级。Gateway 认证后的 structured authority 必须由共享 verifier 重新读取 committed Secure Ingress audit/claim，并精确绑定 request/principal/tenant、body digest、action/scopes 与 graph revision；Application 不接受调用方自报 request identity/source。Operator Runtime 按 immutable HostCommand surface 路由到 Host/Internal/Scheduler，异常后用 durable intent 区分明确未执行和可能已执行。该链路有实现与定向测试，但开关仍默认 OFF，且尚无部署凭据与真实 canary。

0098 不把历史数据“就地修好”：migration 只追加 immutable case inventory，proof 也只能追加。repairable REQUESTED case 以 PENDING proof 参与之后新建的 cancellation decision；FINALIZED quarantine 的 correction 必须继续绑定旧 decision ref/payload digest，并且只在 trusted getter/replay 中生效。`graph_revision` 同时进入签名 binding、Outbox 物理列和 case trigger。production readiness 仅审计真实数据库里的 legacy rows；本仓库无 SQLite 资产，所以当前没有任何外部生产库存已清理的证据。

0099 不在 migration 中修 Graph/Budget；它只追加 immutable case/proof/supersession schema，readiness 严格比较 3 table/4 index/10 trigger canonical SQL 并做 Python codec 复核。`recover_startup` 后单次最多 inventory 10,000 个 terminal Graph。System auto 独立默认 OFF，仅消费带唯一 graph-scope Budget 的 exact FAILED bundle，以 immutable case 时间生成跨进程稳定 proof；结构漂移永久 quarantine。Classifier 要求唯一 0090、完整 0091/0092 validator 与 exact failure Outbox refs。已 inventoried 未决 case graph-wide 围栏普通 Graph/cancellation request 与 Budget transition/consume/usage/retry，只有 0099 proof UoW bypass；旧 case 只能被新 current case 替代并最终落到可信 proof。Healthy closure 仅在历史未决 case 且 current classifier healthy 时生成 signed `NONE` 并零写 Graph/Budget/Outbox。`task_terminal.reconcile` 默认 OFF；主 Host/独立 Obs 均物理 query-only。这不是 general/multi-node/pre-budget recovery 或 production canary。

0095 的 CREATED 回填以 immutable HostCommand evidence 为 authority；ACTIVE 等非 attempt transition 以 deterministic legacy governed Outbox ID/key 重算为 authority，无法解析的自定义 producer 整事务阻断。升级保持原 ID/key，新增 revisioned payload；旧 payload 仅 replay 兼容。readiness/receipt gate 冻结 revision 列、payload trigger、delivery index 与 Runtime Event source unique partial index。旧 journal 不改写，一 Outbox 只允许一 Event，append-success/complete-loss legacy 重投只能 exact replay；ReviewStore/Operator exact revision。这关闭 identity/read-path 缺口，但不改变生产 cancellation、Budget 与 reconciliation 的剩余定级；Review continuation 由后续 0096 切片单独关闭。

0096 task-bound Review continuation、canonical COMMITTED Operation continuation（包含 scheduler-owned 的多节点 exact checkpoint）、exact operation-free persisted Attempt takeover、signed `RECOVERY_REQUIRED + canonical WorkerProjection → COMMITTED` 与当前 signed no-intent raw-failure `RELEASE` 是不同窄链。新生产Graph的成功/失败由0091/0090原子收口，但这不等于通用恢复完成。raw-failure 窄链只证明零投影/Effect、过期 Lease、单节点/单 Budget 的 `NO_SIDE_EFFECT` 原子终态；多节点仍只对 `EXACT_ACCEPTED / EXACT_REJECTED / MISSING` 证明同Attempt、更高fence且不重跑worker的 continuation。任意 external receipt、`CONFLICT`、general batch、一般 RELEASE、部署 key 与 wave canary仍未完成，完整架构保持`INCOMPLETE`。

PlanningRecovery 只在自身调用中 opt-in Graph Outbox snapshot，并使用同一读事务内真实 terminal payload ref；普通 scheduler/Operator 无新增扫描。pre-0095 ref 原样进入 failure evidence；只有旧版或替代 Store 未提供可选扩展字段时才合成 fallback，生产 DurableTaskStore 提供 inventory 后，缺行、畸形或歧义均 fail closed。0100 的 append-only supersession、`admit_replan()` 单事务 predecessor 重验+revision+1 admission、response-loss replay/rollback，以及旧 revision 的共享 TaskStore/Review mutation fence 已接通。当前 safe subset 仍只接受完全静止 `FAILED`，并保守拒绝所有历史 Review continuation、所有 Acceptance Effect 和 `BLOCKED`；自动 replan、扩大 quiescence classifier 与生产并发 canary仍缺，故 `revision_plus_one_replan=PARTIAL_DURABLE_SUPERSESSION_SAFE_SUBSET`。`created_ts=0` 的 timestamp 容差可用于 current revisioned 与 legacy unrevisioned 两种可重建 Event candidate，因为两条历史 append 路径都可能用时钟替代零值，但其它字段必须 exact。

旧 Internal 87/35/3/62/46/4 批次只作被当前UoW切片包含或重叠的历史，不得相加，也不得继续称为fresh。当前证据只采用最终去重集中批次并绑定其提交；远程 CI 为 `NOT_RUN_ACCOUNT_QUOTA`，此证据不得提升为 NE-01、七路线或完整架构完成。

## 3. Composition 与隔离

最新切片仅收口 Chat retrieval 的读取依赖：Host scope 显式提供会话历史、偏好构造器和绑定 ledger/Life memory/Bond Neo4j 的 recall factory，helper 不再回读 Service Locator。它不改变“全局 Service Locator 仍存在”或“Chat Acceptance 尚未闭环”的缺口定级。

受治理 Chat 已新增两个显式、lifecycle-bound 的窄端口；缺失时稳定拒绝，不回退 singleton。后续切片又让端口绑定 exact Host-owned Tribe Agent catalog，production provider 不再读取 singleton getter。生产 Service Locator 已物理清零，但这不替代 TaskGraph acceptance、generic Delegate、并发 multi-Host compatibility context 或七路线验收。

| 目标 | 当前状态 | 代码/证据 | 实际缺口 | 执行波 |
|---|---|---|---|---|
| 显式 Composition Root | `PARTIAL_PRODUCTION_LOCATOR_PHYSICALLY_RETIRED_TRIBE_AGENT_CATALOG_APP_LOCAL_RESOURCE_STATUS_DURABLE` | typed Host/Agent/Context/Startup ports、Host lifecycle/Authority/HardState/Bond/Memory/Governance owners、Tool Runtime、Tribe owner binding/catalog、ResourceManager 0123 journal | 生产 locator module/field/lock/ref=`0/0/0/0`；23-Agent production identity 已按 Host scope 隔离，ResourceManager shared JSON authority 为 0，缺端口失败关闭。外部 namespace、Token/MCP durable restart、其它外部资源恢复、并发 compatibility context、legacy Executive generic delegate、真实 workspace-bound executor和完整 Host/Worker/Obs 对象图仍未完成 | NE-01 |
| Host mode durable authority | `WIRED_DURABLE_MODE_AND_POLICY_AUTHORITY / OPERATOR_VERTICAL_SLICE_PARTIAL / CANARY_NOT_RUN` | 0115/0129、HardState/ModePolicy contracts 与 repositories、governed Operator runtimes、ASGI runtime binding | 两个聚合均有 migration/schema drift、event/head atomicity、active lease/fence、CAS、exact replay、tamper/fault rollback与1001+ replay证据。`sys_mode.switch`、rules/event 已经 canonical Secure Ingress、typed HostCommand、TaskGraph、Review、Acceptance Effect 到各自 owner；auto-rules、last-interaction 与 event outcome 已持久。当前仍缺完整 Host 进程 restart、commit-bound acceptance、NE-01 wave integration和production canary；不能把这些 vertical slice 提升为 Operator plane、lifecycle 或 NE-01 完成 | NE-01 |
| 无全局 Service Locator | `PRODUCTION_PHYSICALLY_RETIRED / TEST_BRIDGE_ONLY` | fresh ports + app-local Authority owner + pytest-only compatibility bridge | 生产 consumer/import/module/field/lock/ref 全为 0，fresh 生产进程不加载测试桥。该项关闭；ResourceManager status 的后续独立关闭由 0123 证明。外部 namespace、其它外部资源恢复、事务化 Chat、全量实例隔离和 generic Delegate 是独立未决项 | NE-01 |
| Host 情感与关系运行时 | `DL06_AUTOMATIC_PROFILE_CANARY_ACTIVATION_SLICE / HOST_EXPRESSION_PROTOCOL_STAGED / LEGACY_WRITERS_REMAIN` | `AffectFusionService`、0143/0144/0145/0146/0147 Life command/CAS/replay、`RelationshipLifecycleService`、`CompanionProfileLifecycleService`、`CompanionPermissionIngress`、`LifeOutboxDispatcher`、`companion_proactive`、`AffectiveTurnPlanner`、`affective_expression_delivery`、Input/ASGI composition、Surface native sentence stream、兼容 `HostEmotionRuntime`、`ResponseFormDecider` | 生产 Input 以 workspace/person/session scope 写 observation，appraisal 写唯一 canonical self-affect；只要 fusion 注入就不解析 `HostEmotionRuntime`。accepted reply 后才原子写 provisional relationship episode 与 `CompanionProactiveTargetV1`，下一用户输入以 opaque digest 写 aftereffect。可信 hand-off 会冻结 `CompanionAct/ExpressionPlan/AffectiveTurnPlan`；Scheduler 主动决策要求 explicit `ALLOWED`、无 repair/active episode/self-regulation hold，并只向纯 impulse adapter 提供 canonical affect/relationship/timezone；read-only target registry 还必须与 frozen Scene scope 匹配，旧 Bond/legacy affect/fixed template/`last_target` 无发送权。0147 profile 生命周期从 canonical aftereffect 重开五条独立跨日、strong/boundary-safe evidence 后，五次合格 canary interaction 会自动 activate，且只允许 current profile 的 exact rollback target；其 ID/digest/revision 会进入真实下一轮 text/Voice/Avatar plan 与 Host delivery intent。生产普通文本 classifier 的 `0.72` 无法启动或推进 canary。`host.companion-contact` Secure Ingress 现把 strict `ALLOWED/DENIED` control 绑定 `companion:contact:write`、exact user principal、tenant 和 canonical Life receipt；natural language、模型、Bond、emotion 或跨 scope body 无法写许可，duplicate/restart 只重开同一 command。完成的 `proactive.gateway` receipt 经过 outbox、payload/authorization digest、scope 与 target identity 重验才追加 `relationship_proactive_delivered`；startup/tick recovery 只读重开 receipt，不能重新调用 transport。`HOST_*_STAGED` 与 Gateway accepted 都不是 physical device/platform receipt；真实 Edge/TTS/VTS、usage stream、连接恢复、Limbic/BodySense/Endocrine/Motive、volatile Bond queue、旧 relationship projection、签名 profile rollback、Monitor/Review control 和真实多 Host/模型/设备 canary 仍未清零或未完成 | DL-06 / DL-07 |
| 业务层私有 SQLite / Gate 10 | `PASS_GATE_10 / CURRENT_FULL_STATIC_PASS` | `RuntimeEventRepository` readiness、`RuntimeEventStore` port、`SQLiteServiceCatalogRolloutRepository`、rollout Protocol、global SQLite boundary contract | 0112 schema 检查已离开 runtime 私有连接；Catalog 的连接/SQL/事务已归 persistence，`ACTIVATION_STARTED` receipt 与 head 同事务并有共同回滚证据。当前 full static 通过，但 typed Composition 与 NE-01 仍缺 | NE-01 |
| Obs projection daemon 生命周期 / Gate 6 | `APP_LOCAL_SUPERVISION_WIRED / DURABLE_SLO_AND_CANARY_MISSING` | `ObsApplicationContainer.start_projection_jobs()/health_snapshot()`、`ProjectionJobDaemon.health_snapshot()`、`create_obs_app()` 嵌套 lifespan 与 `/health`、bundle-local single-use claim、Gate 6 static contract | owner/lifecycle 边界保持；普通 job/lease 故障被类型化并继续运行，恢复清零连续失败，进程控制异常进入 `FAILED` 且显式 close 前拒绝重启；`/health` 输出 secret-free wave/failure/recovery 状态。尚无 durable SLO journal、Guardian acknowledgement、部署 canary 或波次集成，不能提升为 Projection/NE-01 完成 | NE-01 |
| Service Catalog 分层 / Gate 1 子切片 | `HISTORICAL_PARTIAL / SUBSEQUENTLY_CLOSED` | `service_catalog/startup_coordinator.py`、`contracts/service_catalog.py`、同对象兼容重导出、contracts-only repository | application→composition 2 条与 Catalog persistence→application 1 条已归零，SCC 由 application/composition/persistence 缩为 application/persistence；该切片完成时仍有 persistence→application 11 条（PlanningSearch 7、Evolution/Lab/Skill/World 4），现已由后续合同切片清零。61 项 contract/unit、7 项真实 socket integration、7 项边界复跑通过，集合可能重叠；无 migration | NE-01 |
| PlanningSearch 持久化合同分层 / Gate 1 子切片 | `HISTORICAL_PARTIAL / SUBSEQUENTLY_CLOSED` | `contracts/planning_search/`、八组纯合同/模型、旧路径同对象重导出、contracts-only SQLite repositories | code benchmark、calibration、research shadow 仓储的 7 条 persistence→application 已归零，兼容 `__all__`、类型 identity、`isinstance` 与序列化形状保持；边界 5 项、D13/D18/D19/D20 60 项通过，Windows symlink 权限导致 1 项跳过。该切片快照仍有 application/persistence SCC 与 Evolution/Lab/Skill/World 4 条反向依赖，现由最终合同切片清零。无 migration；不得据此宣称 PlanningSearch 功能/收益、Composition 或 NE-01 完成 | NE-01 |
| 最终 persistence 合同分层 / Gate 1 | `HISTORICAL_PASS_GATE_1_ONLY / FULL_STATIC_FAIL_GATE_11 / PROGRAM_INCOMPLETE` | `contracts/evolution.py`、`contracts/evolution_lab.py`、`contracts/runtime_context.py`、`contracts/skill_lifecycle.py`、旧路径同对象重导出、四个 contracts-only repository | 最后 4 条 persistence→application 已清零；该切片 commit-bound Gate 1 issues/SCC/reverse imports 为空、Edge finding/violation=0，Gate 6/10 继续通过。但 #87 组合后的 full static=`FAIL [11]`，Gate 11 为 ToolBroker production-service inventory 双命中。边界 4、相关定向 32、py_compile/diff-check 通过；无 migration。只证明 Gate 1 分层和类型 identity，不证明 typed Composition、Universal Runtime、PlanningSearch 收益或 NE-04～NE-06 生产闭环 | NE-01 继续实施 |
| ToolBroker 构造 authority / Gate 11 | `PASS_GATE_11 / CURRENT_FULL_STATIC_PASS` | `app/tool_runtime_composition.py`、`tools/broker.py`、Gate 11 inventory 与 static policy | process accessor/slot/provider 保持为0，唯一 app-local constructor count=1；fresh 全仓静态 Gate 无未决项。真实 workspace-bound governed executor、全局 locator 清零与完整 Host/Worker composition 仍缺 | NE-01 继续实施 |
| 执行巨石拆分 | `STATIC_GATE_PASS / RUNTIME_PROGRAM_PARTIAL` | `dag_scheduling_runtime.py`、focused Budget/Operation/Effect Store modules、`coordinator.py`、本轮 Acceptance/ASGI/Scheduler focused modules | `task_repository.py` 保持既有 cohesive policy；本轮三个 owner 已降到各自阈值内。967行 Budget rejection writer、444行只读 terminal resolver与93行纯 route helper职责分离，`new_mixed_over_800=[]`；没有第二 Store authority或白名单扩张。静态债务关闭不代表运行时缺口关闭 | NE-01 Lane D 结构完成、运行闭环未决 |
| Host/Obs/Worker scope 隔离 | `PARTIAL_APP_LOCAL_SERVICES_DURABLE_HOST_MODE_AND_RESOURCE_STATUS_TRIBE_FALLBACK_RETIRED` | 独立 ASGI、typed route ports、query-only repositories、HardState/ModePolicy owners 0115/0129、ResourceManager 0123 journal、app-local `AcceptanceEffectRuntime`、Host lifecycle/Tribe catalog tests | Host lifecycle、mode/history/policy、ResourceManager status 与 Tribe 23-Agent production identity 已按 app/runtime scope 所有，Tribe compatibility context 已 execution-local 且无上下文 fallback 归零；ResourceManager shared JSON authority 为 0。Scheduler 管理 runtime 也由 ASGI typed provider 显式注入。Endocrine、Host source、Chat/Delegate 与大部分 Worker 对象图仍共享或易失；`23/37` 个 Operator 写入口、其它外部资源恢复、真实 Edge 与完整 Host/Worker composition 未闭环 | NE-01 |

Host lifecycle/HardState/ModePolicy、ResourceManager 0123 journal、生产 locator 退役、Tribe shared-runtime/Agent catalog app-local 与无上下文 fallback 归零收窄了对象图，但仍不证明其它外部资源恢复、namespace 隔离或完整 Composition。当前 Operator 为 `16 active / 23 retired / 39 known / blocked=0`；易失 Endocrine 事件缓冲/last state、易失 bus、真实 workspace-bound executor、真实 Edge canary与完整 Worker/Obs scope未清零。Scheduler CRUD HTTP 直接 writer 已归零，但仅是独立切片。`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`，NE-01 和完整架构仍未完成。

Gate 10 的证据不能和其它批次相加：Runtime Context `7 passed`；Catalog unit/contract `15 passed`；Host socket integration `6 passed`；Repository/Global 独立复跑 `16 passed`，其中最后一组与 15 项有重叠。Global SQLite boundary=`PASS`。

Gate 6 的证据仍按原始重叠批次记录，Gate 6 owner/lifecycle 边界保持。该切片先前基线为 `FAIL [1,6,10]`，后续 Gate 1/6/10/11 在各自提交绑定快照关闭，本轮 owner 拆分再关闭 Gate 3；当前 fresh 全仓为 `PASS`、`issues=[]`。NE-01 与完整架构项目仍未完成；wave integration=`NOT_RUN_PER_WAVE_POLICY`，`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。
| Workspace ownership | `PARTIAL_DIRECT_AND_SINGLE_PLAN_WRITE_SUCCESS_FAILURE_CANCELLATION_LIFECYCLE / GENERAL_CROSS_ROUTE_INCOMPLETE` | 0101+0116、Workspace repositories/runtime、`workspace_write_promotion.py`、`governed_skill_work_order.py`、`plan_workspace_acceptance_effect.py`、`skill_artifact_terminal_reconciliation.py`、parallel-success UoW、protected artifact、真实 SQLite+Git integration | exact graph epoch、frozen source、独立 worktree、Lease/handoff/takeover/cleanup 保持；Internal 与三条已登记 Direct Agent Skill 均能在 Acceptance 后晋升。Plan 可含至多一个 reviewed risk-4 非结果写节点：成功只在整图 Acceptance 原子暂存 Effect；下游失败仅在重开 exact Acceptance/artifact/Operation/projection 且证明零 Effect 后跨 runtime ABANDON/CLEAN；下游 post-review 取消还必须重开 immutable BLOCKED intent、可信 CANCELLED decision，并证明 accepted writer 三快照同版本。源 branch 不动。其它 Plan failure/cancellation/unknown-effect 组合、多 Workspace 并发写、Domain Pack/其它 Skill、逐操作 fence/强杀、handle-level TOCTOU 及 OS/container/tenant 隔离未完成，canary 未跑 | NE-01 |
| 外部 Skill 安全接收 | `CODE_COMPLETE_LOCAL_WAVE_PASS / ACCEPTANCE_PENDING_NE07` | Signed Intake、Quarantine、Supply Chain、0141 V2 lifecycle authority、Host lifecycle-bound Registry | 真实重开 0138 Lab、proposal、ReviewItem 与 Supply Chain 证据；activation evidence、maturity、active projection、operation、receipt 同一 SQLite UoW，exact replay 先查 operation，rollback 使用目标旧版本证据。Host 只解析批准的 `builtin-skill-alias.v1`，不扫描目录、不 import 外部 Python | 真实外部 bundle/provider canary 与 NE-07 全栈未运行 |

## 4. 数字生命与长期演化

| 目标 | 当前状态 | 代码/证据 | 实际缺口 | 执行波 |
|---|---|---|---|---|
| World Model | `PARTIAL_PRODUCTION_OBSERVATION_AND_GOAL_OWNER_WIRED_NOT_PREDICTIVE` | 0111、0137、`RuntimeWorldObservationApplication`、`SQLiteLongTermGoalAuthority`、`WorldResolver` | Host lifespan 中以 server-owned principal/Bond/Emotion/environment/active-goal 追加并重开 World，TTL 与 `SOURCE_REVOKED` 显式 expiry、competing refs 与 captured-head 封口已有；Bond 原始证据 owner、预测世界模型与部署 canary仍缺 | NE-03 |
| Scene Engine | `PARTIAL_THREE_CONSUMERS_PRODUCTION_WIRED_LOCAL_ACCEPTANCE_PASSED` | 0112、`RuntimeContextApplicationService`、`SceneResolver` | caller 不能指定最终 scene/constraints，Surface、Proactive、Task admission 冻结同一 revision/digest；八类场景 commit-bound Gate 已通过，多实例 canary 待收口 | NE-03 |
| Intent Arbiter | `PARTIAL_PRODUCTION_ADMISSION_AND_GOAL_OWNER_WIRED_RESTRICT_ONLY_LOCAL_WAVE_PASS` | `ProductionIntentAuthorityPort`、`DurableIntentAdmissionVerifier`、`IntentArbitrationV1`、0137 | graph/context/action/grant/policy/current goal identity 由重开 authority 证明，节点/graph receipt set 与 durable Review continuation 已接 Task Runtime；冻结后 goal 漂移失败关闭。部署 canary仍缺 | NE-03 |
| Action Preview / Shadow | `PARTIAL_PRODUCTION_AUTHORITY_WIRED_LOCAL_ACCEPTANCE_PASSED` | `runtime/action_preview_authority.py`、`runtime/action_preview_resolver.py`、Task/Acceptance/Life/terminal transport gates | lifespan-owned resolver 重开 Command/Task/Attempt/Lease/Grant/Context/Scene/Guardian，derived scope/args 受 descriptor 约束；终端正向覆盖 Acceptance surface Gateway、Scheduler-derived Life Edge/Gateway/VTS/MateEngine 以及 Operator sys-mode、terminal、Edge、Operation/Effect reconciliation、`task.operator_control` 与 `operator.state_control` 的 typed terminal proof。旧任务/effect、剩余 `23/37` 个直写 Operator、reply-performance/Gateway voice/Avatar 直通失败关闭；后者和 Operator 其余功能正向迁移属于 NE-01 | NE-03→NE-01 |
| Runtime Guardian / Service Catalog | `PARTIAL_HOST_LIFESPAN_PROBE_STATE_AND_ENFORCE_TARGET_WIRED` | 0112、0113、`runtime/runtime_guardian.py`、`service_health_heartbeat.py`、`service_catalog/host_startup.py` | ASGI Guardian 绑定 Host lease/fence；`ServiceHealthHeartbeat` 按 Host lifespan 独占 task/keepalive/observer/probe cache/HTTP transport，scope close 不得影响其他 Host；未注入 observer 的 compatibility probe 只写自己的兼容 cache，不能回退到全局 degradation reducer。独立 Obs health projection/probe registry 已停止读取任何跨进程不可见 cache，改为 durable ledger/projection；兼容顶层 helper 不属于 Host production authority。Catalog fresh DB 逐级真实启动五阶段且默认目标 ENFORCE，operation/receipt/head、crash takeover 可恢复并由 SQLite 状态机保护；Windows graceful stop 与部署多实例 canary 仍缺 | NE-03→NE-07 |

0137 当前定向证据分开记录，不伪加总：长期目标 repository/World revocation
为 `9 passed in 7.55s`，Production Intent/Context 为
`59 passed in 114.77s`，Obs/Security/Release 为
`62 passed in 29.05s`；真实 Governed Operator 激活→撤销→exact replay
为 `1 passed in 115.13s`。全部 `test_ne03_*` 单元/集成文件又以一次完整
运行得到 `271 passed, 2 warnings in 332.52s`。部署多实例 canary、
Windows graceful stop 与 NE-07 真实服务栈尚未运行，因此本表保持
`PARTIAL / IN_PROGRESS`。
| Evolution Lab | `LOCAL_ACCEPTANCE_PASS / PROPOSAL_ONLY / PROGRAM_INCOMPLETE` | 0108/0122 只读；0138 trusted ledger；0139 namespace repair；0140 sanitized dataset UoW；专用 Docker executor、typed composition、query-only Obs/Monitor | raw trace 不落库；六个真实 digest-pinned container arm、响应丢失 exact reopen、unknown-effect reconciliation、0140 restart/tamper/rollback 和只读 Operator 展示已通过；clean-commit suite 绑定 `b68a8b9dd` 与 receipt `020c8fc0…`，exact replay 不增长 Lab 表或容器；没有 activation authority | NE-07 只验真实全栈 |
| Counterfactual | `LOCAL_WAVE_CODE_PASS / PROPOSAL_ONLY` | 0138 trusted report + `CounterfactualPairV2` + 三类 Golden service + query-only Operator projection | outcome/evidence/cost/latency/risk/verification 的真实隔离 baseline→candidate 比较已产出；Operator 可只读展示 arm diff 与 proposal，不提供激活；正式 commit-bound aggregate 仍待实现提交后生成 | NE-04 |
| Capability Maturity | `V2_AUTHORITY_WIRED_FAIL_CLOSED_LOCAL_WAVE_PASS` | scorecard/provider/Governor adapter、0141 maturity decision、Host lifecycle Registry | 生产门禁默认开启；缺失/伪造 scorecard 最高风险，新能力最高 L1/L2，active version 超过静态风险上限会令 Host 启动失败关闭 | NE-07 外部 canary |
| Capability Forge | `PROPOSAL_ONLY / RUNTIME_AUTHORITY_RETIRED` | `app/capability_forge.py`、Obs 只读 projection、NE-05 static contract | approval、rollback、runtime result 和 route override 公共 authority 为 0；运行统计固定 `authority=PROPOSAL_ONLY`、applied=0，只能生成 proposal | NE-06 消费 proposal；不得恢复平行 route authority |
| Skill Compiler | `CODE_COMPLETE_DETERMINISTIC_UNSIGNED_BUNDLE_LOCAL_WAVE_PASS` | `host_brain/skills/skill_compiler.py`、`SkillDraftV1`、`SkillBundleCompilationV1`、NE-05 tests | Markdown/API/script/accepted trace 生成确定性 draft、inventory、CycloneDX SBOM、签名申请与 content-addressed bundle；不保存 raw thought、正文或私钥，不拥有 activation | NE-07 真实外部签名/bundle canary |
| Experience Feedback | `CODE_COMPLETE_LOCAL_WAVE_PASS / ACCEPTANCE_PENDING_NE07` | 0142、Task acceptance decorator、Input/Tribe/Scheduler proposal adapters、V2 activation service | APPLIED 已查询真实 Lab/Review 并切 versioned Persona/表达/反馈 policy；错误持久化并投影 Obs。真实部署与全栈故障验收未运行 | NE-07 |
| Knowledge OS | `CODE_COMPLETE_LOCAL / DEFAULT_SHADOW / ACCEPTANCE_PENDING_NE07` | `KnowledgeEntryV2`、`DecisionRecordV1`、0142 active projection、Host semantic recall decorator | active winner、cursor、supersedes/deprecation/dedup/conflict/TTL 已完成；真实 Chroma/Neo4j shadow compare、CANARY→ENFORCE 与重启验收仍缺 | NE-07 |
| Identity Snapshot | `CODE_COMPLETE_LOCAL_RELEASE_GATE_DEFAULT_ENFORCE` | `release/identity_snapshot.py`、0142 snapshot/active projection | 显式关闭会 fail closed；真实 release pair 还未在部署服务栈执行 | NE-07 |
| Identity Migration | `CODE_COMPLETE_LOCAL_WAVE_PASS / ACCEPTANCE_PENDING_NE07` | `IdentityMigrationPlanV2`、`release/identity_activation.py`、0142 decision/operation/receipt | no-change、重大差异阻断、真实 evidence binding、canonical owner switch 与 exact previous rollback 已本地证明；真实发布演练未运行 | NE-07 |

## 5. 历史证据的正确解释

| 证据 | 可证明 | 不能证明 |
|---|---|---|
| Core Release 22/22 | 对应提交的基础设施与选定 Gate 通过 | 当前全架构、所有生产 route 或演化能力完成 |
| GR-06 live receipt | 在旧提交与旧 migration head 上，Host `chat/skill/delegate/plan` 可产生 durable lineage | 当前七路线完成、Scheduler/Internal/Operator、当前 migration、legacy route 删除或所有副作用单事务；v2 Gate 明确禁止用它满足 `commit_bound_acceptance` |
| GR-02A benchmark | 公共算法能解决冻结的 synthetic trap | Research/Code 生产 uplift、真实成本或安全切流 |
| D20a～D20c Research catalog / corpus-loader / citation / model evidence tests | 公开 metadata 没有 dedicated locator/source-body/answer-key/verifier-decision 字段，constraints 拒绝命名 key；受信实验根的 manifest 文件读取会在稳定输入中拒绝路径、ADS colon/case alias、link/reparse、未列来源与摘要漂移。D20b 在每次验证新建 loader，重算 snapshot/manifest/source id/ref/digest/tier handshake，并拒绝非 UTF-8 边界或错误 digest 的 citation span。D20c 只把 canonical 模型服务的完整、非缓存、单成功 attempt 与 token/成本/provider latency 变为内嵌 attestation，并要求重开同一 request 的 canonical audit pair；cache、retry、audit 或 measurement drift 均拒绝 | 真实 Research outcome、credential/provider execution、独立信任域 verifier、paired cohort、成本/延迟或收益 |
| Pentest calibration tests | 纯虚拟世界确定性与粗 reward 对账 | L3 替代路径真实收益或跨 run 稳定校准 |
| M48 Identity tests | snapshot/diff/review/rollback 合同成立 | 生产 release 已生成并执行人格迁移 |

## 6. 总体裁决

```text
Core Infrastructure                         HISTORICAL_PASS / REUSABLE
Host chat-skill-delegate-plan runtime        WIRED
Scheduler governed runtime                   WIRED
Internal governed runtime                    WIRED
Universal execution scope                    PARTIAL
Budget contention authority                  UNIQUE_WINNER_PROVEN / POLICY PARTIAL
Operation recovery                           RESERVED_UNKNOWN_EFFECT_SIGNED_OS_PROCESS_REACQUIRE_PLUS_SIGNED_MULTINODE_FAILURE_PROVEN / GENERAL INCOMPLETE
Accepted Effect restart finalization         PROVEN_NARROW
Historical terminal reconciliation           WIRED_DEFAULT_OFF / CANARY_NOT_RUN / DEPLOYMENT_UNVERIFIED
Persona evolution authority                  CENTRAL_FENCE_WIRED / PROPOSAL PIPELINE MISSING
Generic planning kernel                      AVAILABLE
Generic intelligent planning                 INCOMPLETE
Cross-domain production benefit              UNPROVEN
Explicit composition                         INCOMPLETE
Long-term evolution program                  NOT IMPLEMENTED AS A WHOLE
Full Architecture                            INCOMPLETE
```

当前执行只允许从 [NEXT_GENERATION_EVOLUTION_EXECUTION.md](../../30_devspec/NEXT_GENERATION_EVOLUTION_EXECUTION.md) 的 NE-00～NE-07 推进；不得复活旧 M49～M57 或 TASK-xxxx 状态作为调度依据。

`RESERVED` 未知副作用现已证明跨进程 `NO_SIDE_EFFECT → REACQUIRE`：
启动前后的原始 Lease状态都受唯一最高 fence 校验，签名替代 Lease只绕过
Host 发现层预筛选，DAG Runtime 仍拥有最终 worker authority。通用
scheduler snapshot 只携带 Store 验签后的最小 execution-fence 投影，不
携带原始证明或业务身份。该项关闭一条恢复死锁，不代表签名
`COMMIT/RELEASE`、所有 cancellation/failure 或通用并发恢复完成。

## 7. 当前 Direct Skill 取消缺口修正

Direct Skill 在 post-execution Acceptance Review 中取消的 Workspace 终态已由
“状态组合允许删除”提升为“持久事件链授权删除”。repository 现在必须重验
唯一 finalized intent/decision 的 ref 与 digest、当前 Graph/Node 的
status/version，以及该 Attempt 的 Acceptance Effect 数为零。真实 SQLite、
Git、ReviewStore、Secure Ingress 和 fresh Runtime 已证明正常
`ABANDONED → CLEANED`，同时证明已有 Effect 时保持 `BLOCKED + ACTIVE
Workspace`。去重受影响集合为 `68 passed in 117.23s`。

该项只关闭 Direct Skill 的一个精确取消矩阵。一般 cancellation、
unknown-effect、多 Workspace、波次 integration 和真实服务栈仍缺，因此
`Universal execution scope`、Workspace 与 Full Architecture 的总体定级均
保持 `PARTIAL / INCOMPLETE`。

## 8. 当前 Direct Skill 签名失败缺口修正

Direct Skill 在密封 Workspace stage 后、提交 checkpoint 前中断的单节点
失败路径，现已复用 canonical Operation failure settlement，而不是新增
Workspace 自有任务状态机。TaskStore 负责 Ed25519 验签和原子
Operation/Task/Lease/Budget/Outbox 结算；Skill terminal reconciler 负责
route 语义；`workspace_terminal_failure_authority.py` 只在最终 ABANDON
前重验持久结构、digest、error ref、六类 Outbox 和零下游 authority。

正常的真实 SQLite+Git 路径会 `ABANDONED → CLEANED`，Outbox、Effect 或
signature 篡改均保持 `BLOCKED + ACTIVE`，仅伪造终态字段也被 repository
拒绝。该证据把 Workspace ownership 的 Direct Skill 单节点
`RELEASE/NO_SIDE_EFFECT` 失败子集从缺失推进为本地实证，但没有覆盖一般
multi-node/batch failure、其它 unknown-effect/cancellation、多 Workspace、
波次 integration 或服务栈，故矩阵总状态仍为 `PARTIAL`。

## 9. 当前 Plan 多节点失败缺口修正

| 能力 | 修正前 | 当前证据 | 仍缺 |
|---|---|---|---|
| Plan 写节点 pre-checkpoint failure | stage 留存但 Workspace 只接受单节点六 Outbox 终态 | 真实三节点 Graph：签名节点失败、fresh 0090 Graph/Budget 结算、Workspace 清理、零 sibling replay | 多写节点与一般 batch |
| 0090 历史读取 | 单行 helper 无法表达 Review→resume→failure | 全部历史 decision/Outbox canonical 校验，唯一 current-version terminal 选择 | 大规模并发/部署 canary |
| Workspace Outbox ownership | 假设六条均由 Operation failure UoW 产生 | Node 四条由 failure UoW；Graph/Budget 两条由 0090 decision 拥有 | unknown-effect 与跨 Workspace |

本批把一个明确的 multi-node Plan failure 子集从缺失推进为本地实证，但
bounded fan-out、并行 writer、一般 multi-node/batch recovery、Composition
清零、NE-01 wave integration 和真实服务栈仍缺。因此 Universal Runtime、
Workspace 和整体架构继续为 `PARTIAL / INCOMPLETE`。

## 13. NE-02 固定 cohort authority 差距

| 能力 | 当前证据 | 仍缺 | 状态 |
|---|---|---|---|
| 统一持久 owner | 0136 单一 SQLite UoW 持有 24/20/30 registration、member、report、proposal | 部署 namespace/canary | `CODE_COMPLETE_LOCAL` |
| 来源真实性 | Research/Code 重开 0107/0106 terminal；Pentest 重开 0105 与三类 receipt authority | 真实 provider transport、授权 lab、独立 verifier 部署 | `ACCEPTANCE_PENDING_EXTERNAL` |
| exact replay | response loss/restart 不再次调用 resolver 或执行 arm | 跨主机存储故障演练 | `LOCAL_PASS` |
| 收益 Gate | 固定统计、完整 evidence-set digest、失败报告保留 | 24 Research、20 Code、30 Pentest 真实 cohort | `UNPROVEN` |
| L3 promotion | 仅生成 Review-required、无 activation/prod influence proposal | Review、版本 activation、rollback、生产 canary | `SHADOW_ONLY` |

因此“durable cohort owner missing”已从缺口中删除，但“真实收益已证明”和
“L3 已生产启用”仍是明确缺口。最终波次集合
`169 passed, 1 Windows privilege skip` 只关闭本地代码/持久化阶段；远程 CI
因账户额度为 `NOT_RUN_ACCOUNT_QUOTA`，不能冒充 PASS。

### 当前结构门禁纠偏

| 项目 | 状态 | 证据 | 剩余边界 |
|---|---|---|---|
| 已合并 atomic authority 与旧结构基线漂移 | `CLOSED_LOCAL` | 30 个 unit structure 文件 `120 passed`；Review denial、Plan writer terminal、registry 注入和当前 Store MRO 均按实际 owner 审计。Retry writer 751 行、Operation checkpoint owner 206 行，未抬阈值或 allowlist | 持续结构 Gate；不等同 NE-01 wave、canary 或 Full Architecture 完成 |

## cleanup Guardian 与通用多节点 Budget 增量

| 缺口 | 本切片前 | 当前证据 | 仍缺 |
|---|---|---|---|
| cleanup 故障持久性 | health 只在 worker 内存，替换进程看不到故障 | 单个连续 incident 写入 runtime-scoped Guardian；replacement process 成功后恢复，exact replay no-op | OS/process supervisor 自动拉起与部署 canary |
| cleanup 隐私与降级 | 公开 health 已脱敏，但 durable observer 未接线 | opaque random digest 不由 path/error/credential 派生；projection 失败强制 DEGRADED | 完整运行栈故障注入 |
| 多节点时间预算 | 单节点 guard 与特定 Plan 交叉终态已有证据 | 一般两节点后继只取得实际剩余时间；timeout 写 rejected usage + recovery hold | DEGRADE/REVIEW、任意 batch/unknown-effect |
| 多节点并发预算 | graph claim 有并发调度证据，child ledger 断言不足 | 首个 ready claim 持有 `concurrency=1` child reservation，第二节点保持 READY | 全路线取消/释放与生产 canary |
| Composition root | `asgi.py` 再次逼近 1400 行 Gate | Agent immune health handler 抽入聚焦 wiring，Gate 不放宽 | 继续拆除其它混合职责 |

定向回归为 `48 passed in 21.13s`，无 migration。该表只移除“本地一般
time/concurrency 基础证据”和“Guardian durable cleanup incident”两个具体
缺口，不把 program-wide Budget、Workspace 或 NE-01 提升为完成。

## 13. Workspace cleanup 监督缺口修正

| 能力 | 修正前 | 当前证据 | 仍缺 |
|---|---|---|---|
| worker health | worker 内存中有 snapshot，但 Host/Monitor 无生产读取 | app-local provider 接入 `/health`、`/ready`，unhealthy 明确降级 | RuntimeGuardian durable incident 关联 |
| 敏感边界 | raw last report/error detail 可能被直接复制 | 只公开状态、计数、时间和 error type | NE-07 Monitor 实际展示 |
| shutdown retry | inner quiesce 保留 handle，outer shutdown 后续仍可能撤销它 | 同一 handle 两次 bounded close；耗尽立即 preserve-and-stop | OS supervisor 跨进程自动重启 |
| partial startup | Task Runtime 非 live 时可能误认为无 authority | cleanup close 耗尽同样保留唯一 handle | 强杀后的 orphan 扫描 |

因此 `cleanup worker health route and automatic shutdown retry orchestration`
从 Workspace missing 清单移除。跨进程 supervisor、一般 crash-left inventory、
OS/container isolation、波次集成和 NE-07 仍缺，NE-01 保持 `IN_PROGRESS`。

## 12. 只读 runtime_repo 共享检出缺口修正

| 能力 | 修正前 | 当前证据 | 仍缺 |
|---|---|---|---|
| read-only source | inspect WorkOrder 直接取得 Host live `REPO_ROOT` | exact `HEAD^{commit}` tracked-tree 临时快照，source ref 写入 result | 非 Git source 的统一只读快照 |
| WIP 隔离 | dirty/untracked 与并发开发状态对 Agent 可见 | archive 不包含 dirty/untracked 或 `.git` | OS/container/tenant 隔离 |
| 错误写入 containment | 依赖逻辑工具白名单保护共享 checkout | fixture 故意修改 snapshot，source 内容不变 | 恶意进程级越界防护 |
| 生命周期 | live checkout 无需清理但长期暴露 | executor 返回前显式清理 snapshot | 崩溃遗留临时目录启动扫描 |

因此 `runtime_repo retirement for read-only inspection` 从 Workspace missing
清单移除，但“强只读文件系统”“租户隔离”与一般 crash cleanup 仍缺。
Workspace、NE-01 和整体架构继续为 `PARTIAL / INCOMPLETE`。

## 10. Plan post-COMMIT Budget rejection 缺口修正

| 能力 | 修正前 | 当前证据 | 仍缺 |
|---|---|---|---|
| committed writer 超预算 | child 已有 checkpoint，但 Workspace terminal 不识别 0090 Budget rejection | exact FAILED child + unique rejected usage + current 0090 BUDGET 可重开并清理 | 其它 route/terminal matrix |
| 多 owner winner 恢复 | settlement 要求 winner 必须出现在当前进程 outcome tuple | winner 已终态时从 durable facts 重建；未终态时等待其 graph-order recovery | 跨主机/部署压力 canary |
| 部分 Graph 结算 | winner 先于后续 sibling 关闭时会尝试提前写 0090 | 任一 in-flight Node/Attempt/Lease 使共享 settlement 延后 | 一般 unbounded fan-out |
| Workspace 防篡改 | child terminal 与 Graph/Budget authority 未组合 | 上层 reader 与 repository 最终 CAS 都重开 exact authority；删除 current 0090 Outbox 后绕过 reconciler 直调也保持 `BLOCKED + ACTIVE`，abandoner 零调用 | 多 Workspace 原子提交 |

本批关闭一个真实 Plan writer 子集，并修复可在干净 `dev` 复现的多 owner
恢复竞态；去重定向证据为 `53 passed in 138.76s`，Workspace authority
合同集另为 `24 passed in 67.50s`。后续切片已补一般两节点
time/concurrency 基础证据；完整 DEGRADE/REVIEW、其它失败/取消/
unknown-effect、wave integration 与 NE-07 仍缺，所以
Budget、Workspace、Universal Runtime 和整体架构继续为
`PARTIAL / INCOMPLETE`。

## 11. 签名失败 × 时间预算拒绝交叉缺口修正

| 能力 | 修正前 | 当前证据 | 仍缺 |
|---|---|---|---|
| 双 authority 终态 | 签名节点失败只识别 0090 `FAILURE + FAILED`，预算已 `EXCEEDED` 时无法重开 | 稳定 node-failure kind 精确组合 signed failure 与 0090 `BUDGET + BLOCKED`，不改变既有 receipt identity | 其它 route/terminal 交叉组合 |
| Budget winner 绑定 | Graph/Node 状态可见，但不足以证明同一 Attempt 导致超额 | selected outcome 同时绑定失败 `error_ref`、唯一 `usage_id`、child reservation 和父/子 usage；后续已补一般两节点 time/concurrency 基础证据 | 完整 DEGRADE/REVIEW 与 program-wide coverage |
| Workspace 最终删除 | 上层即使识别组合终态，仓储 CAS 仍不认识 Graph `BLOCKED` | repository 在最终事务独立重开 0090/winner/child usage，篡改 Outbox 时不调用 abandoner | 多 Workspace 原子提交 |
| route 隔离 | 放宽 Graph `BLOCKED` 可能误放行 Direct Skill | artifact classifier 仅允许 Plan 的已验证 BLOCKED 形状；单节点 Direct Skill 继续强制 Graph `FAILED` | 其它受控写 Skill |

真实六路径 Plan Workspace 回归为 `6 passed in 39.93s`，相邻回归为
`46 passed in 137.82s`，两组独立报告。本批关闭单一“seal 后、
checkpoint 前签名失败且 time budget 超限”的 Plan writer 路径，无
migration。其它失败/取消/unknown-effect 组合、一般预算策略、多
Workspace、wave integration 与 NE-07 仍缺，因此 NE-01、Budget、
Workspace 和整体架构继续为 `PARTIAL / INCOMPLETE`。
