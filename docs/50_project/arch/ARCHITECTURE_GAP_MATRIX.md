受众：MASTER
标题：Natsume AI 当前架构差距矩阵

# Natsume AI 当前架构差距矩阵

版本：v3.26
核对日期：2026-07-27
机器状态：[current_architecture_status.json](../../../configs/release/current_architecture_status.json)
总体状态：`INCOMPLETE`

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

当前分支的 fresh 全量静态检查继续以本提交 Gate 结果为准。连续 migration head 为 `0135_task_review_denial_settlement.sql`。0132 的 Attempt-bound child Budget 语义保持不变；0133 增加 durable Verification，0134 增加 durable Acceptance/Review 创建，0135 增加 Review denial 原子终态。0130/0131 schema 与 signed reconciliation 保持不变；known interruption marker、0130 case 和两类 Outbox 已合并为同一事务，未决 case不会因后续 Budget/Lease/lifecycle 变化从 current inventory 消失。五节点菱形与八根节点已证明精确 `COMMITTED` checkpoint 的依赖释放和跨 owner 恢复，worker 各调用一次；无限规模、其它 failure/cancellation 组合、自动归因、Workspace 终态和部署 fan-out 仍未完成。

Scheduler 管理 HTTP 的 create/update/delete/enable/disable 已在 `/scheduler` 与 `/host-scheduler` 双前缀统一切入 app-local governed runtime：每个写请求冻结 Secure Ingress method/path/body、definition head 和 claim，生成 `HostCommand(surface=SCHEDULER)` 与不可变 TaskGraph，只有 post-Acceptance `operator.state_control` Effect 可以写 canonical repository；不可变 admin operation receipt 与 claim 之间的崩溃可由新实例精确恢复。路由中的直接 Scheduler writer 为 0，read 路由保持只读，trigger 保持既有 governed occurrence。

当前切片没有自动推断副作用；它只在签名明确证明 `NO_SIDE_EFFECT` 后，把目标节点失败从单节点扩展到多节点图。目标 Operation/Attempt/Node/Lease 在第一 UoW 收口，Graph/Budget 仍由既有 0090 决策在重启 fan-in 时唯一终结，READY sibling 不执行。其它运行期 mutation、其它 failure/cancellation、mutable Scheduler batch、易失 Endocrine 事件缓冲/last state、真实 executor 与波次集成仍未完成；七路线 commit-bound 验收已由当前正式收据关闭，但 NE-01 仍保持 `PARTIAL / IN_PROGRESS`。

Workspace 正向写链现覆盖 Governed Internal 与三条已登记 risk-4 Direct Agent Skill。Skill payload 不能携带 V3/Workspace/Review/fence authority；server 从 frozen Task lineage 重建 WorkOrderV3，worker 只提交 sealed ref/digest，Graph Acceptance 再用先于可见 Effect 的 `agent.workspace_artifact_promote` 调用共享 0101/0116 owner。Accepted handler 重开 immutable admission、projection、Node/Attempt/Lease/NodeArtifact、Preview、Effect 和 seal 后才生成 deterministic accepted HEAD，配置源分支保持不变。真实 SQLite+Git 已验证 Skill handler 的生产 composition 与重启持久读取；既有 exact replay、seal 漂移、lifecycle rollback 证据继续覆盖共享底层。Domain Pack、其它 Skill、一般恢复、handle-level TOCTOU、OS/container tenant 与跨路线 canary 仍在差距清单中。

最新 `dev` 已含 exact Internal 单节点取消子流、Host/Internal lifecycle survivor、Operation 中断持久分类与 governed `REACQUIRE`。当前分支在同一 `task_operation.reconcile` 命令内新增 signed checkpoint `COMMIT`：只接受 canonical worker-projection receipt，读取层与 Operation mutation UoW 双重校验 immutable admission/Graph/Node/Attempt/scope/fingerprint/digest 和旧 Lease expiry。真实 SQLite 证明错误签名、live Lease、投影 UPDATE/DELETE 均拒绝，首次 COMMIT 响应丢失后 exact reopen 仍继续原图且 worker 调用数为 1。该状态不等于部署 key/HTTP canary、一般 unknown-effect、其它取消、多节点/批次恢复、事务化 Chat 或七路线验收完成。

Edge command 的旧全局 client owner、startup 注册和 Tribe/内部直推调用点已归零；首条正向 Obs 路径现把 `edge.somatic + emotion_state_command` 经 Secure Ingress、risk-4 Review、TaskGraph、Acceptance Effect 与 terminal Preview 写入 durable embodiment outbox，并支持 exact replay 与只读 receipt reconciliation。连同 Operation、独立 general Effect、Task/Review control，以及 Scheduler/Host mode-policy state control，当前 Operator 为 `14/37`；更多 command type、其余 `23/37` 个 Operator 命令及真实 Edge/Gateway 服务仍是缺口。Scheduler HTTP 以独立 surface 计数，不虚增 Operator 库存。

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
| production Skill 工作单冻结 | `PARTIAL / DIRECT_AND_APPROVED_PLAN_NODE` | `governed_host_contracts.py`、`governed_production_payload.py`、`planning_execution.py`、`governed_frozen_skill_runtime.py`、`skill_execution.py`、GR03d/D16/GR05 定向测试 | `codex_production` 的 profile/default 与 V2 adapter payload 已在 direct `skill` 和 selected Plan node 的 admission 期按 node 冻结，并以 digest、root/graph/node/revision、scope/fingerprint、Review/approval lineage 重验；Plan 只接受 approved candidate input 与 immutable binding，caller-supplied frozen payload 失败关闭，replan 按新 revision 重建。执行期不再重新调用 normalizer；该 legacy adapter 不拥有 TaskGraph authority。签名多节点无副作用失败已有共享 runtime 证据；其它恢复类别、全路线 Budget/Receipt 与 commit-bound acceptance 仍缺 | NE-01 |
| Scheduler governed | `PARTIAL / ADMIN_AND_OCCURRENCE_GOVERNED` | Scheduler operation/clock owners、`governed_operator_state_control_runtime.py`、accepted state-control handler、双前缀 route cutover tests | occurrence 保持 post-Acceptance handler、immutable result ledger 与 0117 DUE owner/fence。管理 create/update/delete/enable/disable 统一经 Scheduler HostCommand/TaskGraph/Review/Acceptance；operation journal 先提交、claim completion 后提交，崩溃重启只读重开。路由直接 writer=0，提交绑定已通过。mutable batch freezing、通用未知外部效果人工归因、wave integration 与 canary 仍缺 | NE-01 |
| Internal Agent governed | `PARTIAL / INTERNAL_WRITE_PROMOTION_AND_SIGNED_RAW_FAILURE_CLAIM_BEFORE_CLEANUP` | `workspace_write_promotion.py`、`internal_raw_failure_claim_result.py`、`internal_artifact_terminal_reconciliation.py`、`task_operation_failure_reconciliation_store.py`、`internal_agent_sealed_checkpoint_recovery.py`、0116、0130、真实 SQLite/Git integration | sealed recovery 与既有 signed cancellation terminal 保持原 authority，提交绑定已通过。具备 exact Workspace authority 的 write 已密封 bounded manifest/binary patch，Acceptance 生成 deterministic accepted HEAD 并经 0116 晋升；一般 unknown effect、取消交叉、其它 failure/cancellation、Workspace 其它终态、handle-level TOCTOU 与 OS/container isolation 仍缺 | NE-01 |
| Direct Agent Skill Workspace | `PARTIAL / REVIEWED_SKILL_CODEX_WORKSPACE_PROMOTION_LOCAL_PROVEN` | `governed_skill_work_order.py`、`governed_frozen_skill_runtime.py`、`agent_workspace_acceptance_effect.py`、`accepted_workspace_artifact.py`、Acceptance compiler/dispatcher、真实 SQLite+Git integration | `agent_worker/self_heal/self_evolve` 冻结 registry risk 4 和 durable Review；caller V3/Workspace/Review/Lease authority 拒绝。231 行 pure validator 与 1499 行 canonical compiler 分责且未扩 allowlist；sealed WorkerProjection proposal 只能在 Graph Acceptance 后提升，promotion Effect 先于 visible Effects。Domain Pack、其它 Skill、一般 failure/cancellation、完整进程/network canary 与整路线 receipt 仍缺 | NE-01 |
| 本切片结构债务 | `STATIC_PASS / PROGRAM_PARTIAL` | Acceptance/ASGI/Scheduler focused owner 保持原 writer、lifespan 与 repository UoW authority | 该历史静态通过不关闭旧 execution authority、一般恢复、七路线或 canary 缺口；生产 Service Locator 已由后续切片物理清零 | NE-01 |

| 全 route universal runtime | `CHAT + SKILL + PLAN + DELEGATE COMPLETE / THREE INCOMPLETE / SEVEN_ROUTE_GATE_ACTIVE` | v2 coverage manifest + `2a88f56e` v3 receipt + Cortex/Scheduler/Internal/Operator integrations | fail-closed Gate 精确覆盖七路并要求 commit SHA、migration head 与 route receipt 一致。当前启用四路已完整；Scheduler clock batch/control policy 已提交绑定，Internal 与 Operator 仍有各自部分控制。真实 MCP provider、视觉 governed executor、完整 Tool/Operator/Gateway matrix 与七路线 canary 均未完成 | NE-01 |
| Edge Operator 正向链 | `FIRST_GOVERNED_SLICE_WIRED / REAL_EDGE_CANARY_MISSING` | `governed_operator_edge_support.py`、`accepted_edge_embodiment_handler.py`、`acceptance_effects.py`、`HostEmbodimentRuntime`、真实 SQLite integration | 只允许 `edge.somatic + emotion_state_command`；Review 前零 outbox，Acceptance 后唯一 durable `set_expression`，duplicate exact replay 不重发，过期 claim 只读查 receipt。更多 Edge action、真实 Edge/Gateway 网络、设备和 NE-07 full-stack 未验收；记录型 async port 不是物理 canary | NE-01 / NE-07 |
| Executive authority 退役 | `PARTIAL_RETIRED_FAIL_CLOSED / PRODUCTION_OFF` | Cortex ports、retired runtime/app guards、registry absence contract、legacy planning retirement contract | 生产入口、`route_planning`、`conversation_execution`、application `composition`、application port wiring、legacy diagnostic direct-skill matrix、legacy SkillDag worker 执行和进程内 registry 已删除；Cortex direct reply/post-process compatibility branch 与 Executive 内 Runtime Capability Forge route override call 已删除，纯 SkillDag adapter 仅为零生产调用的兼容合同。cycle 必须以 `PLAN_CYCLE` 阻断，兼容投影只能返回空 batch，不能忽略 dependency 伪造串行执行。独立 Forge `runtime-match` 仍存在 | NE-01 部分负向收口；不等于通用搜索或 NE-01 完成 |
| 多节点 Durable DAG | `PARTIAL / SIGNED_NODE_FAILURE_PLUS_0090_AND_GENERAL_SHAPE_CHECKPOINT_RECOVERY_LOCAL_PROVEN / PRODUCTION_FANOUT_GATE_OFF` | Coordinator、TaskStore、0090-0102、0130、checkpoint/failure reconciliation、parallel settlement、真实 SQLite 双/三 owner、four-root、五节点菱形、八根节点与双根失败 integration、0124/0125 SQL authority | canonical Node Acceptance 与 Graph 成败复用 0091/0090 UoW。双 checkpoint、shared-EXCEEDED missing、four-root mixed settlement、五节点 fan-in/fan-out 与八根节点 batch 均按 immutable graph order 收口，worker 不重跑。签名 `NO_SIDE_EFFECT` 失败先只关闭目标节点，再由 0090 在重启时关闭 Graph/Budget；双根并发额度1证明 READY sibling 零调用、v4 receipt 稳定、0090 Outbox 篡改失败关闭，并发额度2的双失败证明非 selected 节点仍由 decision outcomes 覆盖。0130 继续持久化 conflict/unknown/cancellation crossing。无限规模、其它 failure/cancellation、自动归因、部署 fan-out receipt、inventory/key/canary 仍缺 | NE-01 |
| Cancellation Effect 对账 | `WIRED_DEFAULT_OFF / CANARY_NOT_RUN` | 0092 effect fence；0098 case/proof inventory；Effect reconciliation contract/service/store；Operator command + candidates/cases/challenge；integration/unit/contract tests | `task_effect.reconcile` 的 Ed25519 proof exact 绑定 case/intent/effect/fence/Attempt/resolution/actor/Outbox/revision；`NO_SIDE_EFFECT → CANCELLED`，`SIDE_EFFECT_CONFIRMED + receipt → COMMITTED_EVIDENCE`。PENDING late writer 被同事务 fence；FINALIZED correction 只恢复 trusted read。生产 gate 只阻断实际持久化 legacy inventory；仓库无 SQLite 资产，不能证明外部部署已清理，且部署凭据/key lifecycle/canary 未完成 | NE-01 B5C deployment |
| General Acceptance Effect 对账 | `SIGNED_GENERAL_ACCEPTANCE_EFFECT_RECONCILIATION_LOCAL_PROVEN / DEFAULT_OFF` | 0128 evidence/case/proof；`governed_effect_reconciliation_runtime.py`；Accepted handler；focused Store；真实 SQLite integration | `GENERAL_UNKNOWN_EFFECT` 不依赖 cancellation intent。Secure Ingress admission 前验签并 append evidence；WorkOrder/TaskGraph/Review/Effect 只持 ref/digest；风险 4 Review、Preview、Verification 和外层 Acceptance 后重开并重新验签，再在 canonical UoW 中写 signed proof、target Effect 与 Outbox。Review 前零 proof，exact replay、drift 和 UPDATE/DELETE 围栏已证明。该路线不自动判断副作用，也不替代 cancellation-bound 模式 | 部署 key/credential、HTTP/network canary、联合 readiness 与 NE-01 wave integration |
| Operator Task/Review 控制 | `LOCAL_REAL_SQLITE_TARGETED_PASS / DEFAULT_CANCELLATION_OFF / PROGRAM_PARTIAL` | `governed_operator_control_*`、`operator_control_acceptance_effect.py`、`accepted_operator_control_handler.py`、Review Store/command service、真实 SQLite integration | `task_graph.cancel` 与 `task_review.approve/reject/resume` 已删除 Obs 直接 writer；四者独立进入 HostCommand/TaskGraph，并只在 terminal Preview 与外层 Acceptance 后修改原 Task/Review authority。取消必须到 `FINALIZED` 才提交外层 Effect；approve 只公开 public token ref，普通 bearer 不落库，重开 Store 后 Operator resume 仍一次性。历史 approve/cancel exact replay 零目标重入 | Graph cancellation 仍默认 OFF；完整进程重启、部署 credential、HTTP/network canary、wave integration 与剩余 `23/37` Operator 命令未完成 |
| Operator Scheduler/Mode 状态控制 | `LOCAL_REAL_SQLITE_TARGETED_PASS / PROGRAM_PARTIAL` | `governed_operator_state_control_runtime.py`、`operator_state_control_acceptance_effect.py`、`AcceptedOperatorStateControlHandler`、Scheduler repository、0129 Host policy journal | Operator 的 `scheduler.task.enable/disable` 与 `sys_mode.auto_rules.update/event.submit` 各自进入 HostCommand/TaskGraph；Scheduler HTTP create/update/delete/enable/disable 复用同一 accepted effect，但使用独立 `SCHEDULER` surface 和精确 HTTP authority。Review/Preview/Verification/Acceptance 后才写 canonical owner；operation exact replay、1001-event mode-policy replay、tamper、released fence、append fault rollback和 claim-gap restart 已验证 | 完整 Host 进程重启、部署 credential、真实 HTTP/network canary、一般恢复和 wave integration 未完成 |
| Historical terminal reconciliation | `AUTO_EXACT_ON / LOCAL_SQLITE_CANARY_PASS / OPERATOR_OFF / HTTP_CANARY_NOT_RUN / DEPLOYMENT_UNVERIFIED` | 0099 readiness、classifier、startup inventory、真实 SQLite canary、Secure Ingress command、主 Host/独立 Obs query-only routes | 隔离 canonical 双 case restart 已证明 exact FAILED 自动 proof + Budget release，signed-required CANCELLED 不改写并阻断 readiness；完整 cancellation 依赖束用临时 verification-only 公钥通过 preflight。签名 Operator HTTP、部署 inventory/key lifecycle、Operation/Effect canary、general/multi-node recovery仍未完成 | NE-01 B5C deployment |
| Budget 全策略执行 | `ATTEMPT_CHILD_RESERVATION_AND_PARENT_CONTENTION_LOCAL_PROVEN / KNOWN_INTERRUPTION_CLASSIFIED / PROGRAM_PARTIAL` | `budget_runtime.py`、`task_node_budget_store.py`、`task_node_budget_usage_bridge.py`、`task_budget_usage_store.py`、`node_execution_runtime.py`、0132 migration、真实 SQLite parent/child/retry/restart tests | Graph Budget 保持唯一 aggregate ceiling；每次 Attempt 在 READY claim 与 Lease 同一事务获得 exact child reservation。普通 usage、唯一 EXCEEDED winner、terminal sibling observation 与 retry 在同一 UoW 推进父子账本，响应丢失/重启只重开同一 binding；旧 Attempt 按历史 usage 回填且不恢复容量。正数 `time_ms` 与模型 guard 继续读取 canonical remaining。尚缺全部 cancellation/release 组合、concurrency 作为可消费维度、DEGRADE/REVIEW、program-wide 七路线 policy、一般 unknown-effect recovery 与真实 provider canary | NE-01 |
| Governed Chat Surface 模型预算 | `PASS_FORMAL_CLEAN_SHA` | `ModelAttemptService`、WorkerProjection、parent/child Budget、`3d68bd1e` v3 receipt | Host Chat 主模型调用消费 frozen remaining，缺量测、超限、缓存或 drift 均失败关闭；正式收据证明 ModelAttempt→WorkerProjection→父子账本和 replay。Cortex 准入前模型、其它路线和外部模型质量不属于该控制 | NE-01 |
| Governed Agent Runtime 模型端口 | `PARTIAL_TASK_BOUND_MULTICALL_BUDGET_WIRED / REACT_SKILL_DELEGATE_FAIL_CLOSED` | `executor_registry.py`、`model_runtime/governed_budget.py`、`agent_runtime/executor.py`、`agent_runtime/agent_executor.py`、Agent budget/ReAct tests | sealed governed Skill 经 CapabilityBroker 后取得 composition 的 ModelAttempt ports，并保留 canonical Budget JSON；read-only V2 WorkOrder 由 trusted Task context 重建为 V3。Internal 与 Skill Agent loop 的每次请求共享逐次扣减的 node-bound guard，legacy 直调及缺 Budget/usage/端口关闭。普通 Tool action、真实 provider acceptance、逐工具 TaskNode/Receipt、revision compiler、wave integration 与 commit-bound 七路线仍未闭环 | NE-01/NE-02 |
| Governed 普通 Skill 模型预算 | `PARTIAL_DECLARED_MODEL_SKILL_TYPED_PORT_WIRED` | `skills/registry.py`、`model_runtime/governed_skill_budget.py`、`app/governed_skill_execution.py`、`governed_frozen_skill_runtime.py`、`test_ne01_governed_skill_model_budget.py` | 普通 Skill 不再自动取得裸 ModelAttempt authority；只有 `model_budget_required` 声明能从 ACTIVE Task Budget 派生 typed port。每次调用缩减 remaining，服务端 attestation 绑定 request/Budget/Graph/Node/Attempt，外层重验后才进入 WorkerBudgetUsage。当前正向接线为 `emergent_growth_weekly`；缺量测、cache、重复、超限和 lineage drift 均失败关闭 | 真实 provider、其它模型 Skill inventory、legacy 非 governed 调用、Persona proposal/activation、program-wide coverage 与 wave integration 未完成 |
| Operation / Attempt 在线 continuation | `ATOMIC_INTERRUPTION_RECOVERY_HOLD_LOCAL_PROVEN / SIGNED_MULTINODE_NODE_FAILURE_LOCAL_PROVEN / GENERAL_SHAPE_CHECKPOINT_RECOVERY / 0131_CASE_BOUND_AMBIGUITY_RESOLUTION / GENERAL_RECOVERY_INCOMPLETE` | failure/checkpoint reconciliation、`operation_interruption_case.py`、`dag_scheduling_runtime.py`、0090、0130 hold codec/support/store、0131 binding、governed Operation handler、真实 SQLite/Git integration | 已知中断在一个 SQLite UoW 写 `RECOVERY_REQUIRED`、Operation Outbox、0130 case 与 case Outbox；case INSERT 故障使 marker 整体回滚。Runtime 只读重开同事务 case，worker Budget settlement 与 Lease/lifecycle 后续变化不使未对账 case消失；Graph cancellation 用 intent-bound successor替换 current unknown projection，同时保留两条历史。签名 `REACQUIRE/COMMIT/RELEASE` 保持既有 mutation authority；UNKNOWN/CANCELLATION current hold 必须经 0131 exact case-bound evidence、risk-4 Review 与 Acceptance，handler 在 mutation 后确认 case non-current。五节点菱形与八根节点 checkpoint 可跨 owner 续接。任意未绑定 external receipt、未签名普通 `RELEASE` 仍拒绝；checkpoint conflict quarantine。部署 key/HTTP canary、其它 failure/cancellation、无限规模、Workspace 终态和 wave integration仍缺 | NE-01 |
| ACCEPTED→Effect restart | `PROVEN_NARROW_FINALIZATION` | `accepted_graph_finalization.py`、`test_ne01_accepted_effect_finalization_recovery.py` | exact checkpoint下原子补写且幂等；不代表跨系统 Effect dispatch 已全局事务化 | NE-01 |
| 通用 Acceptance Effect 对账 | `PARTIAL / INDEPENDENT_CONVERSATION_EFFECTS_AND_AUTHENTICATED_SURFACE_QUERY` | `acceptance_effect_handlers.py`、0118、Preference repository、Gateway reply query、dispatcher、Guardian observer、tests | store gate缺失/非bool失败关闭零写；配置false/malformed env→bool false→`DISABLED`零event/profile。启用时event/append-only child profile/receipt同UoW，sealed-child trigger + full-set reconcile拒绝篡改；推荐反馈严格按admitted-time、exact conversation/session与canonical delivery digest绑定。Gateway从同SQLite payload校验effect_id，`NOT_FOUND`保持`UNKNOWN`。一般unknown-effect人工归因、commit-bound evidence、wave/canary仍缺 | NE-01 |
| Review 自动 continuation | `WIRED_TASK_BOUND / ATOMIC_DENIAL_TERMINAL_LOCAL_PROVEN / CANARY_NOT_RUN` | `review_runtime.py`、ReviewStore、0096、0135、`review_denial_lifecycle.py`、`task_review_denial_store.py`、surface/ingress/takeover tests | task-bound PRE/POST 已接；拒绝/过期/取消 claim 只传 exact node identity、无 worker grant。0135 将 Review/Node/Graph/graph Budget/Outbox 原子终结并支持 response-loss exact replay；single-node canonical COMMITTED Operation takeover 与 operation-free persisted Attempt takeover 是两条独立窄链。真实 canary、Operator credential/UI、一般 cancellation/recovery 与 multi-node/batch 自动 recovery 仍缺 | NE-01 当前切片 |
| 对话全副作用提交 | `PARTIAL / LOCAL_PROJECTION_BARRIER_PASS / EXTERNAL_EVENTUAL` | Acceptance Effect 顺序 + exact Life intent/receipt barrier + Gateway reconciliation | assistant turn 与 LifeMemory 已成为外部 reply 的 durable 前置条件；失败/篡改/重启保持 UNKNOWN。外部 delivery 仍是幂等 eventual，不是跨服务单事务；Chat commit-bound acceptance、波次与真实网络仍缺 | NE-01 |
| 单一任务真相 | `WIRED` | Durable Task projection + registry absence contract | 进程内 registry 与 accessor 已删除；read route 不创建任务 | NE-01 已完成切片 |
| Persona 演化单一 authority | `CENTRAL_FENCE_WIRED` | `PersonaEngine` 运行时拒绝、`T-PERSONA-FENCE-01` 全仓扫描、无副作用候选回归 | legacy writer 已归零；proposal、shadow、Review、版本激活与 rollback 替代链尚未实现 | NE-01 围栏已完成；NE-06 全量替换 |
| 通用 PlanningSearch foundation | `PARTIAL_RULED_OBJECTIVE_CAPABILITY_SEARCH / D20C_ACCEPTANCE_PENDING_EXTERNAL` | `tribe/thalamus_agent.py`、`governor/schemas.py`、`planning_search/`、`orchestration/goal_planning.py`、`orchestration/capability_plan_search.py`、`planning_graph_compiler.py`、`planning_execution.py`、0106/0107/0121 | Goal/VirtualStateSnapshot、SuccessorCatalog、shadow benchmark 合同与 context-owned immutable compile 已落地；Thalamus 只对明确 Research/调研与 Frontend/可用性计划产生 server-owned mode/tag/risk，编译后经 Governor schema/binding，Host 才从 composed Registry 冻结至少两项 host/免确认/低风险/标签匹配能力，L1/L2/条件 L3 在虚拟态选中两项不同 Skill 的有界顺序路径，再生成 `approved_candidate_inputs` 并编译 `skill → skill → chat` immutable TaskGraph。显式 `failure_replan` 仅在 canonical `FAILED` predecessor 与 evidence 重开后准入同 root 的 revision `+1`；不会自动触发或按失败自适应换候选。仅建议、禁止工具、普通 Chat/通用目标、Code/Pentest 不会进入此执行路径。D16～D20c 的 Code/Research 仍为 pure shadow/实验资产；D20c 仍未接入真实 credential/provider、prompt/workload、独立 verifier、cohort、收益或 promotion authority | NE-02 |
| Code 动态 shadow 候选 | `PARTIAL_D16_DYNAMIC_CATALOG_SHADOW_ONLY` | `planning_search/code_domain_models.py`、`planning_search/code_domain.py`、D16 unit | 冻结 opaque ref/digest 后可动态产生 inspect→registered patch→unit/rollback；MCTS 仅在冻结 `virtual_model_ready` 时具备 simulation ports，catalog-derived inputs 才可进入 immutable compiler | 没有真实 worktree/ACL、source/model/tool/test/provider 调用、Task admission、历史 paired A/B、receipt 或 production influence；中间 state replan lineage 未设计 | NE-02 |
| Host 候选方案生成 | `PARTIAL_RULED_OBJECTIVE_CAPABILITY_SEARCH_V1` | `tribe/thalamus_agent.py`、`governor/schemas.py`、`orchestration/capability_plan_search.py`、`planning_search/capability_domain.py`、`app/governed_task_adapter.py` | 明确 Research/调研和 Frontend/可用性计划由 server-owned rule 填入 mode/tag/risk，Thalamus 编译与 Governor 严格 schema 都保留并绑定它们；Host 再读取 app-composed Registry 的非执行 metadata，拒绝 legacy steps、少于两项竞争能力、确认型/越风险/越标签候选和伪造 selected path；搜索在虚拟态中选择两项不同能力，第二项依赖第一项，综合节点依赖两项并以 immutable `skill → skill → chat` 图入场；显式 failure-replan 使用 canonical durable recovery 创建同 root revision `+1` | 仅建议/禁止工具、通用目标/Chat、Code/Pentest 仍不切流；自动或 failure-adaptive replan、真实模型/收益证据和 production promotion 仍未完成 | NE-02 |
| Host 能力规划风险 authority | `LOCAL_TARGETED_PASS / NE-02_PARTIAL` | `orchestration/capability_plan_search.py`、`governor_grant.py`、`test_ne02_capability_plan_search.py` | 请求风险只能收紧：effective ceiling=`min(intent, Governor approved binding)`；grant ref 被冻结到 capability virtual state，缺失/畸形 binding risk 在纯搜索前失败关闭 | 当前仅约束 Research/Frontend capability catalog；不代表一般 Plan、跨域候选、真实收益或 promotion | NE-02 |
| Pentest L1/L2 | `WIRED` | `pentest/agent/agent_loop.py` | 仍需与 durable outcome/calibration 对齐 | NE-02 |
| Pentest Conditional MCTS | `SHADOW` | `planning_search_adapter.py`、virtual world | 根候选固定；不能生成 capability-unlocking 后继；校准非 durable | NE-02 |
| Research 真实收益 | `D20C_ACCEPTANCE_PENDING_EXTERNAL / BENEFIT_UNPROVEN` | D20a `ResearchTaskSnapshot`、`ResearchSuccessorCatalog`、`FrozenResearchCorpusLoader`、D20b `ResearchCitationVerifier`、D20c `AttestedExternalResearchAdapter`、`ResearchPairedExecutionRunner`、canonical `model_attempt_log` lookup、0107/0121 ledger owner | 公开规划状态没有 dedicated locator/source-body/answer-key/verifier-decision 字段，constraints 拒绝这些命名 key，但任务文字不是内容级 DLP；loader 只可读取可信实验根+manifest 列出的 repository-relative 普通文件并公开 metadata receipt。D20b 对 snapshot/manifest/source id/ref/digest/tier 做 exact handshake，并用每次新建的 loader 校验 citation 的 UTF-8 byte span/digest；D20c 新 RunSpec 的 order 只由 owner 在事务内 CSPRNG 生成，raw cohort key 不入账本，历史规格只读；runner 只按这份实际顺序运行、保存 private prompt/output 于进程内并在 fresh verifier 后写 0107 evidence；prompt digest、工具/token/成本/时延与 aggregate budget 均需精确匹配。外呼前 adapter 要求 execution ID 的 audit 为空，已有审计或 lookup 故障均 fail-closed 至 reconciliation，随后才接受 complete canonical model result 的非缓存单 attempt、精确 route/output，并重开 audit started/terminal pair 后把完整 attestation 嵌入 receipt。0107 terminal receipt/replay 重新对齐 registration、RunSpec、两条 arm 的 plan/model/verifier/evidence digest。D20c 没有真实 provider credential execution、production prompt/workload、来源获取、网页抓取、生产 route/TaskGraph/Host/BrowserWorker 接线、独立信任域 verifier、paired cohort、成本/延迟对照、收益统计或 promotion authority；本地 model double 不算 provider evidence，也不是 OS/tenant/sandbox | NE-02 |
| Code 真实收益 | `UNPROVEN` | D16 catalog + D17 trusted-process private-bare paired-worktree + D18 0106 ledger + D19 controlled pytest receipt | D17/D18 已证明正常受信 adapter 流程会在 private clone/双 arm detached worktree 中运行，私有物料清理和 source fingerprint 一致后才产生 `VERIFIED_CLEAN`，并把 preregistration、两臂 evidence/outcome 和 terminal receipt 以 immutable/replay-safe SQLite 账本保存。D19 还实际执行预注册的 shell-free pytest argv，硬拒 paired test-command/wall 超限，且将 PASS/exit 与 run/arm/plan/budget/aggregate-bound 固定到 restart replay；同一主机 child 仍不是 hidden-oracle、网络或 OS 隔离或 hermetic Python，且模型/工具/token/cost 不受该预算治理。仍缺真实历史 cohort/statistical comparison、真实模型/工具、独立信任域 verifier 与 promotion gate | NE-02 |

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
| Host 情感与回复形态运行时 | `APP_LOCAL_OWNER_WIRED / DURABILITY_AND_CANARY_MISSING` | `HostEmotionRuntime`、`emotion_runtime_factory`、Input/Tribe composition、`ResponseFormDecider` | 旧情绪 Timeline/vector/previous-tag/profile/diary throttle 与 reply decider module globals/getters 为 0；同 Host 三消费者 identity 一致、重叠 Host 隔离、replay 不写、语音额度原子计数已有定向证据。状态仍为 Host epoch 内存，未做 durable Feedback/Identity、真实多 Host 进程 canary或 NE-01 波次集成 | NE-01 / NE-06 |
| 业务层私有 SQLite / Gate 10 | `PASS_GATE_10 / CURRENT_FULL_STATIC_PASS` | `RuntimeEventRepository` readiness、`RuntimeEventStore` port、`SQLiteServiceCatalogRolloutRepository`、rollout Protocol、global SQLite boundary contract | 0112 schema 检查已离开 runtime 私有连接；Catalog 的连接/SQL/事务已归 persistence，`ACTIVATION_STARTED` receipt 与 head 同事务并有共同回滚证据。当前 full static 通过，但 typed Composition 与 NE-01 仍缺 | NE-01 |
| Obs projection daemon 生命周期 / Gate 6 | `APP_LOCAL_SUPERVISION_WIRED / DURABLE_SLO_AND_CANARY_MISSING` | `ObsApplicationContainer.start_projection_jobs()/health_snapshot()`、`ProjectionJobDaemon.health_snapshot()`、`create_obs_app()` 嵌套 lifespan 与 `/health`、bundle-local single-use claim、Gate 6 static contract | owner/lifecycle 边界保持；普通 job/lease 故障被类型化并继续运行，恢复清零连续失败，进程控制异常进入 `FAILED` 且显式 close 前拒绝重启；`/health` 输出 secret-free wave/failure/recovery 状态。尚无 durable SLO journal、Guardian acknowledgement、部署 canary 或波次集成，不能提升为 Projection/NE-01 完成 | NE-01 |
| Service Catalog 分层 / Gate 1 子切片 | `HISTORICAL_PARTIAL / SUBSEQUENTLY_CLOSED` | `service_catalog/startup_coordinator.py`、`contracts/service_catalog.py`、同对象兼容重导出、contracts-only repository | application→composition 2 条与 Catalog persistence→application 1 条已归零，SCC 由 application/composition/persistence 缩为 application/persistence；该切片完成时仍有 persistence→application 11 条（PlanningSearch 7、Evolution/Lab/Skill/World 4），现已由后续合同切片清零。61 项 contract/unit、7 项真实 socket integration、7 项边界复跑通过，集合可能重叠；无 migration | NE-01 |
| PlanningSearch 持久化合同分层 / Gate 1 子切片 | `HISTORICAL_PARTIAL / SUBSEQUENTLY_CLOSED` | `contracts/planning_search/`、八组纯合同/模型、旧路径同对象重导出、contracts-only SQLite repositories | code benchmark、calibration、research shadow 仓储的 7 条 persistence→application 已归零，兼容 `__all__`、类型 identity、`isinstance` 与序列化形状保持；边界 5 项、D13/D18/D19/D20 60 项通过，Windows symlink 权限导致 1 项跳过。该切片快照仍有 application/persistence SCC 与 Evolution/Lab/Skill/World 4 条反向依赖，现由最终合同切片清零。无 migration；不得据此宣称 PlanningSearch 功能/收益、Composition 或 NE-01 完成 | NE-01 |
| 最终 persistence 合同分层 / Gate 1 | `HISTORICAL_PASS_GATE_1_ONLY / FULL_STATIC_FAIL_GATE_11 / PROGRAM_INCOMPLETE` | `contracts/evolution.py`、`contracts/evolution_lab.py`、`contracts/runtime_context.py`、`contracts/skill_lifecycle.py`、旧路径同对象重导出、四个 contracts-only repository | 最后 4 条 persistence→application 已清零；该切片 commit-bound Gate 1 issues/SCC/reverse imports 为空、Edge finding/violation=0，Gate 6/10 继续通过。但 #87 组合后的 full static=`FAIL [11]`，Gate 11 为 ToolBroker production-service inventory 双命中。边界 4、相关定向 32、py_compile/diff-check 通过；无 migration。只证明 Gate 1 分层和类型 identity，不证明 typed Composition、Universal Runtime、PlanningSearch 收益或 NE-04～NE-06 生产闭环 | NE-01 继续实施 |
| ToolBroker 构造 authority / Gate 11 | `PASS_GATE_11 / CURRENT_FULL_STATIC_PASS` | `app/tool_runtime_composition.py`、`tools/broker.py`、Gate 11 inventory 与 static policy | process accessor/slot/provider 保持为0，唯一 app-local constructor count=1；fresh 全仓静态 Gate 无未决项。真实 workspace-bound governed executor、全局 locator 清零与完整 Host/Worker composition 仍缺 | NE-01 继续实施 |
| 执行巨石拆分 | `STATIC_GATE_PASS / RUNTIME_PROGRAM_PARTIAL` | `dag_scheduling_runtime.py`、focused Budget/Operation/Effect Store modules、`coordinator.py`、本轮 Acceptance/ASGI/Scheduler focused modules | `task_repository.py` 保持既有 cohesive policy；本轮三个 owner 已降到各自阈值内。967行 Budget rejection writer、444行只读 terminal resolver与93行纯 route helper职责分离，`new_mixed_over_800=[]`；没有第二 Store authority或白名单扩张。静态债务关闭不代表运行时缺口关闭 | NE-01 Lane D 结构完成、运行闭环未决 |
| Host/Obs/Worker scope 隔离 | `PARTIAL_APP_LOCAL_SERVICES_DURABLE_HOST_MODE_AND_RESOURCE_STATUS_TRIBE_FALLBACK_RETIRED` | 独立 ASGI、typed route ports、query-only repositories、HardState/ModePolicy owners 0115/0129、ResourceManager 0123 journal、app-local `AcceptanceEffectRuntime`、Host lifecycle/Tribe catalog tests | Host lifecycle、mode/history/policy、ResourceManager status 与 Tribe 23-Agent production identity 已按 app/runtime scope 所有，Tribe compatibility context 已 execution-local 且无上下文 fallback 归零；ResourceManager shared JSON authority 为 0。Scheduler 管理 runtime 也由 ASGI typed provider 显式注入。Endocrine、Host source、Chat/Delegate 与大部分 Worker 对象图仍共享或易失；`23/37` 个 Operator 写入口、其它外部资源恢复、真实 Edge 与完整 Host/Worker composition 未闭环 | NE-01 |

Host lifecycle/HardState/ModePolicy、ResourceManager 0123 journal、生产 locator 退役、Tribe shared-runtime/Agent catalog app-local 与无上下文 fallback 归零收窄了对象图，但仍不证明其它外部资源恢复、namespace 隔离或完整 Composition。当前 Operator 为 `14/37`，剩余 `23/37` 个写入口、易失 Endocrine 事件缓冲/last state、易失 bus、真实 workspace-bound executor、真实 Edge canary与完整 Worker/Obs scope未清零。Scheduler CRUD HTTP 直接 writer 已归零，但仅是独立切片。production canary/wave integration=`NOT_RUN_PER_WAVE_POLICY`，`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`，NE-01 和完整架构仍未完成。

Gate 10 的证据不能和其它批次相加：Runtime Context `7 passed`；Catalog unit/contract `15 passed`；Host socket integration `6 passed`；Repository/Global 独立复跑 `16 passed`，其中最后一组与 15 项有重叠。Global SQLite boundary=`PASS`。

Gate 6 的证据仍按原始重叠批次记录，Gate 6 owner/lifecycle 边界保持。该切片先前基线为 `FAIL [1,6,10]`，后续 Gate 1/6/10/11 在各自提交绑定快照关闭，本轮 owner 拆分再关闭 Gate 3；当前 fresh 全仓为 `PASS`、`issues=[]`。NE-01 与完整架构项目仍未完成；wave integration=`NOT_RUN_PER_WAVE_POLICY`，`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。
| Workspace ownership | `PARTIAL_W2_INTERNAL_AND_REVIEWED_AGENT_SKILL_PROMOTION / GENERAL_CROSS_ROUTE_INCOMPLETE` | 0101+0116、Workspace repositories/runtime、`workspace_write_promotion.py`、`governed_skill_work_order.py`、`accepted_workspace_artifact.py`、protected artifact、真实 SQLite+Git integration | exact graph epoch、frozen source、独立 worktree、Lease/handoff/takeover/cleanup 保持；Internal 与三条已登记 Direct Agent Skill 均能在 Acceptance 后晋升。write seal 绑定 bounded safe paths 与 binary patch；accepted HEAD 不移动源 branch，replay、tamper、lifecycle rollback 与 Runtime reconstruction 已验证。Internal signed terminal 仍 claim-before-cleanup。一般 unknown effect、其它 failure/cancellation、Domain Pack/其它 Skill、逐操作 fence/强杀、handle-level TOCTOU 及 OS/container/tenant 隔离未完成，canary 未跑 | NE-01 |
| 外部 Skill 安全接收 | `PARTIAL_NON_ATOMIC_NO_RUNTIME_RESOLVER` | Signed Intake、Quarantine、Supply Chain、0109 lifecycle ledger | rollback 已重开目标版本自己既有 `ACTIVATED` evidence，未独立激活或跨 skill target 失败关闭；但 URI 未查询真实 authority，状态、evidence 与 terminal receipt 仍非原子，exact replay 仍有缺陷，Host registry 未切流 | NE-05 |

## 4. 数字生命与长期演化

| 目标 | 当前状态 | 代码/证据 | 实际缺口 | 执行波 |
|---|---|---|---|---|
| World Model | `PARTIAL_PRODUCTION_OBSERVATION_WIRED_NOT_PREDICTIVE` | 0111、`RuntimeWorldObservationApplication`、`WorldResolver` | Host lifespan 中以 server-owned principal/Bond/Emotion/environment 追加并重开 World，显式 expiry、competing refs 与 captured-head 封口已有；Bond 原始证据 owner 与预测世界模型仍缺 | NE-03 |
| Scene Engine | `PARTIAL_THREE_CONSUMERS_PRODUCTION_WIRED_LOCAL_ACCEPTANCE_PASSED` | 0112、`RuntimeContextApplicationService`、`SceneResolver` | caller 不能指定最终 scene/constraints，Surface、Proactive、Task admission 冻结同一 revision/digest；八类场景 commit-bound Gate 已通过，多实例 canary 待收口 | NE-03 |
| Intent Arbiter | `PARTIAL_PRODUCTION_ADMISSION_WIRED_RESTRICT_ONLY_LOCAL_ACCEPTANCE_PASSED` | `ProductionIntentAuthorityPort`、`DurableIntentAdmissionVerifier`、`IntentArbitrationV1` | graph/context/action/grant/policy 由重开 authority 证明，节点/graph receipt set 与 durable Review continuation 已接 Task Runtime；长周期目标 owner 仍缺 | NE-03 |
| Action Preview / Shadow | `PARTIAL_PRODUCTION_AUTHORITY_WIRED_LOCAL_ACCEPTANCE_PASSED` | `runtime/action_preview_authority.py`、`runtime/action_preview_resolver.py`、Task/Acceptance/Life/terminal transport gates | lifespan-owned resolver 重开 Command/Task/Attempt/Lease/Grant/Context/Scene/Guardian，derived scope/args 受 descriptor 约束；终端正向覆盖 Acceptance surface Gateway、Scheduler-derived Life Edge/Gateway/VTS/MateEngine 以及 Operator sys-mode、terminal、Edge、Operation/Effect reconciliation、`task.operator_control` 与 `operator.state_control` 的 typed terminal proof。旧任务/effect、剩余 `23/37` 个直写 Operator、reply-performance/Gateway voice/Avatar 直通失败关闭；后者和 Operator 其余功能正向迁移属于 NE-01 | NE-03→NE-01 |
| Runtime Guardian / Service Catalog | `PARTIAL_HOST_LIFESPAN_PROBE_STATE_AND_ENFORCE_TARGET_WIRED` | 0112、0113、`runtime/runtime_guardian.py`、`service_health_heartbeat.py`、`service_catalog/host_startup.py` | ASGI Guardian 绑定 Host lease/fence；`ServiceHealthHeartbeat` 按 Host lifespan 独占 task/keepalive/observer/probe cache/HTTP transport，scope close 不得影响其他 Host；未注入 observer 的 compatibility probe 只写自己的兼容 cache，不能回退到全局 degradation reducer。独立 Obs health projection/probe registry 已停止读取任何跨进程不可见 cache，改为 durable ledger/projection；兼容顶层 helper 不属于 Host production authority。Catalog fresh DB 逐级真实启动五阶段且默认目标 ENFORCE，operation/receipt/head、crash takeover 可恢复并由 SQLite 状态机保护；Windows graceful stop 与部署多实例 canary 仍缺 | NE-03→NE-07 |
| Evolution Lab | `SELF_ATTESTED_SHADOW_NO_PRODUCTION_CALLER` | 0108 V1 ledger、runner | dataset digest 未独立重算、verifier 可补 hash、隔离可绕过且无 production composition | NE-04 |
| Counterfactual | `LEDGER_CONTRACT_ONLY` | V1 ExperimentReport | 没有可信独立双臂、sandbox/effect audit 或 promotion authority | NE-04 |
| Capability Maturity | `PARTIAL_NE05_PRODUCTION_FAIL_CLOSED` | scorecard/provider/Governor adapter + production IntentDecisionEngine wiring | 生产门禁默认开启且缺失/伪造 scorecard 最高风险；仍缺 durable promotion/degradation authority | NE-05 |
| Capability Forge | `PARTIAL_LEGACY_RUNTIME_OVERRIDE_REMAINS` | `app/capability_forge.py`、Obs `runtime-match` route | Executive 内 call site 已删除，但服务仍能生成/批准 blueprint、维护 canary 状态并输出 runtime route override；这违反 Forge 仅生成 EvolutionProposal 的目标，必须在 NE-05 退役/改为 proposal-only | NE-05 |
| Skill Compiler | `PARTIAL_NE05_DETERMINISTIC_DRAFT` | `host_brain/skills/skill_compiler.py`、`SkillDraftV1`、NE-05 tests | typed draft 与确定性 digest 已有；SBOM/signature request、sandbox、activation 全链由独立 lifecycle 继续补齐 | NE-05 |
| Experience Feedback | `LEDGER_ONLY_NO_ACTIVATION_AUTHORITY` | 0110 V1 ledger、Prefrontal adapter | APPLIED 不切 versioned policy/persona；Lab/Review 未解析；旧 caller 仍待替换 | NE-06 |
| Knowledge OS | `LEDGER_ONLY_NO_ACTIVE_AUTHORITY` | `KnowledgeEntryV1`、0110 namespace ledger | active winner、cursor、supersedes/deprecation/dedup/conflict 与旧 reader cutover 均缺失 | NE-06 |
| Identity Snapshot | `PARTIAL` | `release/identity_snapshot.py` | 合同丰富，但 gate 默认关闭，未由 release 主链生成/持久化 | NE-06 |
| Identity Migration | `LEDGER_ONLY_NO_ACTIVATION_AUTHORITY` | `IdentityMigrationPlanV1`、0110 identity event ledger | ACTIVE 不切 snapshot owner，ROLLED_BACK 不恢复 exact previous version，transition identity 不支持重复 release | NE-06 |

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
Operation recovery                           SIGNED_MULTINODE_NODE_FAILURE_PLUS_0090_PROVEN / GENERAL INCOMPLETE
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
