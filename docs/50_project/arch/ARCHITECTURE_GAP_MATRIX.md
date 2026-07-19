受众：MASTER
标题：Natsume AI 当前架构差距矩阵

# Natsume AI 当前架构差距矩阵

版本：v2.5
核对日期：2026-07-19
机器状态：[current_architecture_status.json](../../../configs/release/current_architecture_status.json)
总体状态：`INCOMPLETE`

本文件是“目标与当前状态如何解释”的唯一入口。历史 receipt 保留原样，但只能证明其声明范围；本矩阵按当前生产代码重新定级。

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
| Host 四路线 governed | `WIRED` | `cortex_runtime.py`、`governed_task_adapter.py`、GR-06 live receipt | 只覆盖 `chat/skill/delegate/plan` | NE-01 守卫回归 |
| Scheduler governed | `WIRED` | Scheduler production cutover integration | `WorkOrderV3`、Budget、child grant、Review、Verification、Acceptance、Receipt 与 replay 已贯通 | NE-01 已完成切片 |
| Internal Agent governed | `WIRED` | Internal production cutover integration | ingress v2 立即编译 V3；Budget、child grant、PRE/POST Review、Verification、Acceptance、Receipt、replay 已贯通；长 Review 的 grant renewal 随 continuation 统一处理 | NE-01 已完成切片 |
| 全 route universal runtime | `PARTIAL` | Cortex governed/read split + Scheduler/Internal production integrations + Operator/Tool first slices | Host 四执行路由、Scheduler、Internal 已 governed；ReAct 嵌套 Skill/Delegate 旧直执行已 fail-closed；Operator 仅 `scheduler.task.trigger` 进入 HostCommand/TaskGraph，36/37 仍阻断；HTTP `/tools/execute` 已进入 TOOL Secure Ingress 和 canonical TaskGraph。Tool live gate 只在 `TaskStore` 单个 SQLite 读事务内原子复验 Graph/Node/Attempt/Lease/Reservation 与 owner/fence/expiry，grant/binding 随后独立 fail-closed；全部通过后 `before_handler` 才写 `PENDING` marker。旧 ToolBroker/MCP direct/callback 已固定拒绝且生产调用为 0，但生产 seed 的 governed executor 仍为 0；普通 ReAct `GovernedToolNode → CapabilityBroker` 仍是独立待审路线，不能由本切片外推为全部 Tool authority 已清零。Tool seed readiness 早于 runtime activation 的 P2 尚未修复：executor=0 当前阻止误报 `READY`，启用真实 executor 前必须统一状态机；真实 MCP provider、完整 Tool/Operator/Gateway surface matrix 仍需迁移和最终验收 | NE-01 |
| Executive authority 退役 | `WIRED` | Cortex ports、retired runtime guard、registry absence contract | 生产入口、legacy SkillDag 执行和进程内 registry 已删除 | NE-01 已完成切片 |
| 多节点 Durable DAG | `PARTIAL_B5C_DURABLE_CANCELLATION_SHADOW / PRODUCTION_OFF` | Coordinator、TaskStore、0090-0102、GR06/Node artifact/worker-input、Budget/continuation/finalizer tests、TaskStore write-authority contract | canonical Node Acceptance 已在一个应用 Store UoW 中冻结 exact NODE Effect set、immutable artifact、`ACCEPTED` 与 Attempt-bound Outbox。`DurableTaskStore.create_graph`、`insert_graph_in_transaction`、`stage_acceptance_effect_intent` 三个 public 写入入口已删除；生产 Graph 私有 hook 只由 `HostCommandRepository._admit_normalized()` 在同一 admission UoW 调用 1 次，Effect 私有 hook 只存在于三个 canonical Acceptance UoW、共 6 个调用点。Graph/Node INSERT SQL 只属于 Graph hook，Effect INSERT SQL 只属于 Effect hook。准确定级为 `APPLICATION_PYTHON_WRITE_AUTHORITY_SEALED / SQL_DIRECT_BYPASS_PRESENT`：SQLite 仍允许 raw/orphan SQL，本轮未增 migration/trigger；general/multi-node/pre-budget recovery、现实 resolver、部署 inventory/key/canary 仍缺 | NE-01 B5C |
| Cancellation Effect 对账 | `WIRED_DEFAULT_OFF / CANARY_NOT_RUN` | 0092 effect fence；0098 case/proof inventory；Effect reconciliation contract/service/store；Operator command + candidates/cases/challenge；integration/unit/contract tests | `task_effect.reconcile` 的 Ed25519 proof exact 绑定 case/intent/effect/fence/Attempt/resolution/actor/Outbox/revision；`NO_SIDE_EFFECT → CANCELLED`，`SIDE_EFFECT_CONFIRMED + receipt → COMMITTED_EVIDENCE`。PENDING late writer 被同事务 fence；FINALIZED correction 只恢复 trusted read。生产 gate 只阻断实际持久化 legacy inventory；仓库无 SQLite 资产，不能证明外部部署已清理，且部署凭据/key lifecycle/canary 未完成 | NE-01 B5C deployment |
| Historical terminal reconciliation | `WIRED_DEFAULT_OFF / CANARY_NOT_RUN / DEPLOYMENT_UNVERIFIED` | 0099 canonical 3 table/4 index/10 trigger readiness、Python case/subject/proof codec、classification、startup inventory、service、Secure Ingress command、主 Host/独立 Obs query-only routes、HTTP→Ed25519→Store | migration schema-only；case/proof/supersession append-only。单次启动最多盘点 10,000 个 terminal Graph；仅唯一 graph-scope exact FAILED bundle 可 system auto。Classifier 要求唯一 0090、完整 0091/0092 validator 与 exact failure Outbox payload refs；结构漂移均 quarantine。未决 case graph-wide 围栏普通 Graph/cancellation request 与 Budget transition/consume/usage/retry，仅 0099 UoW bypass；每条历史替代链最终必须有可信 proof。System proof 以 immutable case 时间跨进程收敛；healthy closure 仅 signed `NONE` 且零业务写。两类查询入口物理 query-only；外部 inventory/key lifecycle/canary未知 | NE-01 B5C deployment |
| Budget 全策略执行 | `CONTENTION_PROVEN / PROGRAM_PARTIAL` | `budget_runtime.py`、focused Store、8/16/32-thread、cross-process、single-snapshot decision drift、concurrency=8 retry test | unique winner/`ALREADY_EXCEEDED` 及 0090 terminal-authority 重验已收口；CAS attempts 至少为 `concurrency + 1`（8 conflicts→9th success）。concurrency 未计量，DEGRADE/REVIEW 与剩余路径未完整 | NE-01 |
| Governed Chat Surface 模型预算 | `PARTIAL_D11_GOVERNED_CHAT_SURFACE_MODEL_GUARD` | `FrozenNodeInput`、`governed_route_executors.py`、`surface_main_chain_adapter.py`、`ModelAttemptService`、production provider adapter | 仅已 admission 的 Host chat 主模型调用消费 frozen remaining：限制 deadline/attempt/total token/output token/model cost，要求 canonical token+cost，缺量测、超限、timeout/失败均拒绝且不 fallback。Thalamus/Cortex 入场前模型、Delegate/子 Agent、通用工具/其他模型、跨 route 成本与全局预支均未覆盖；未报 `model_cost_micros` 不能估算后放行 | NE-01 D11 |
| Governed Agent Runtime 模型端口 | `PARTIAL_D15_GOVERNED_AGENT_MODEL_PORT_BRIDGE` | `executor_registry.py`、`agent_runtime/executor.py`、D15 port-contract tests | sealed governed Skill 经 CapabilityBroker 后，`codex_runtime` 可拿到 composition 的 `ModelAttemptService` 和其 `PromptRegistry`；legacy 直接调用及缺端口关闭。仅端口身份单测：没有真实 provider receipt，`agent_worker` 写入仍缺 WorkspaceGrant/Runtime，ReAct 调用还未进入 `ModelBudgetGuard` 或通用预算账本 | NE-02 D15 |
| Operation 在线 continuation | `SINGLE_NODE_POST_BUDGET_CHECKPOINT_PROVEN / GENERAL_RECOVERY_INCOMPLETE` | `task_operation_checkpoint_store.py`、`operation_recovery_runtime.py`、`operation_checkpoint_continuation.py`、online takeover tests | exact COMMITTED Outbox/Attempt/历史 Lease/canonical WorkerProjection/accepted BudgetUsage 后可接管且不重跑 worker；COMMIT-before-usage、pre-budget、batch/multi-node、未知副作用与一般 Attempt仍不自动恢复 | NE-01 |
| ACCEPTED→Effect restart | `PROVEN_NARROW_FINALIZATION` | `accepted_graph_finalization.py`、`test_ne01_accepted_effect_finalization_recovery.py` | exact checkpoint下原子补写且幂等；不代表跨系统 Effect dispatch 已全局事务化 | NE-01 |
| Review 自动 continuation | `WIRED_TASK_BOUND / CANARY_NOT_RUN` | `review_runtime.py`、ReviewStore、0096、surface/ingress/takeover tests | task-bound PRE/POST 已接；single-node post-budget Operation takeover是独立窄链。真实 canary、Operator credential/UI 与 general/batch/pre-budget recovery仍缺 | NE-01 已完成切片 |
| 对话全副作用原子提交 | `PARTIAL` | Acceptance Effect intents | Acceptance 后 durable eventual；不是 user/assistant/delivery 单事务 | NE-01 |
| 单一任务真相 | `WIRED` | Durable Task projection + registry absence contract | 进程内 registry 与 accessor 已删除；read route 不创建任务 | NE-01 已完成切片 |
| Persona 演化单一 authority | `CENTRAL_FENCE_WIRED` | `PersonaEngine` 运行时拒绝、`T-PERSONA-FENCE-01` 全仓扫描、无副作用候选回归 | legacy writer 已归零；proposal、shadow、Review、版本激活与 rollback 替代链尚未实现 | NE-01 围栏已完成；NE-06 全量替换 |
| 通用 PlanningSearch foundation | `PARTIAL` | `planning_search/`、`orchestration/goal_planning.py`、`planning_graph_compiler.py`、`planning_execution.py`、0106 | Goal/VirtualStateSnapshot、SuccessorCatalog、shadow benchmark 合同与 context-owned immutable compile 已落地；D16 已让 Code 从冻结状态动态生成纯 shadow successor 并贯通 initial/replan/recovery；D17/D18 增加 trusted-process private-bare 双 detached worktree、`VERIFIED_CLEAN` 与 preregistered paired durable ledger，D19 增加冻结 pytest argv 的受信子进程验证、两项真实 test/wall gate、PASS/exit、run/arm/plan/budget 与 restart replay binding。D20a 让 Research 从仅公开的 `source_id/ref/digest` 与有限 hints 动态生成 query/quick-answer/primary/independent/cross-check/conflict/stop 的纯 catalog，并以可信实验根+manifest 的普通文件 loader 隔离私有正文；它未产生生产 domain cutover、真实执行 authority、独立 verifier 或收益证明 | NE-02 |
| Code 动态 shadow 候选 | `PARTIAL_D16_DYNAMIC_CATALOG_SHADOW_ONLY` | `planning_search/code_domain_models.py`、`planning_search/code_domain.py`、D16 unit | 冻结 opaque ref/digest 后可动态产生 inspect→registered patch→unit/rollback；MCTS 仅在冻结 `virtual_model_ready` 时具备 simulation ports，catalog-derived inputs 才可进入 immutable compiler | 没有真实 worktree/ACL、source/model/tool/test/provider 调用、Task admission、历史 paired A/B、receipt 或 production influence；中间 state replan lineage 未设计 | NE-02 |
| Host 候选方案生成 | `MISSING` | `plan_intent_search.py` | 只重排上游已批准步骤，不生成竞争路径 | NE-02 |
| Pentest L1/L2 | `WIRED` | `pentest/agent/agent_loop.py` | 仍需与 durable outcome/calibration 对齐 | NE-02 |
| Pentest Conditional MCTS | `SHADOW` | `planning_search_adapter.py`、virtual world | 根候选固定；不能生成 capability-unlocking 后继；校准非 durable | NE-02 |
| Research 真实收益 | `UNPROVEN` | D20a `ResearchTaskSnapshot`、`ResearchSuccessorCatalog`、`FrozenResearchCorpusLoader` | 公开规划状态没有 dedicated locator/source-body/answer-key/verifier-decision 字段，constraints 拒绝这些命名 key，但任务文字不是内容级 DLP；loader 只可读取可信实验根+manifest 列出的 repository-relative 普通文件并公开 metadata receipt。尚无 snapshot-manifest-document exact handshake、真实来源获取、网页抓取、生产 route/TaskGraph/Host/BrowserWorker 接线、真实模型、独立 verifier、paired cohort、成本/延迟对照、收益统计或 promotion authority；也不是 OS/tenant/sandbox | NE-02 |
| Code 真实收益 | `UNPROVEN` | D16 catalog + D17 trusted-process private-bare paired-worktree + D18 0106 ledger + D19 controlled pytest receipt | D17/D18 已证明正常受信 adapter 流程会在 private clone/双 arm detached worktree 中运行，私有物料清理和 source fingerprint 一致后才产生 `VERIFIED_CLEAN`，并把 preregistration、两臂 evidence/outcome 和 terminal receipt 以 immutable/replay-safe SQLite 账本保存。D19 还实际执行预注册的 shell-free pytest argv，硬拒 paired test-command/wall 超限，且将 PASS/exit 与 run/arm/plan/budget/aggregate-bound 固定到 restart replay；同一主机 child 仍不是 hidden-oracle、网络或 OS 隔离或 hermetic Python，且模型/工具/token/cost 不受该预算治理。仍缺真实历史 cohort/statistical comparison、真实模型/工具、独立信任域 verifier 与 promotion gate | NE-02 |

0094 lifecycle 已按 fail-closed 收紧：activation rollback 或 `aclose` quiescence 未完成统一返回 `TASK_RUNTIME_LIFECYCLE_RETRY_REQUIRED`，锁死 bind/activate 并只允许 `aclose` 重试。Coordinator admission close、drain、`aclose` 任一失败都不关 Store；只有 Coordinator 与 worker 均静默后才 drain Outbox/关 infrastructure。这改善了故障关闭正确性，不改变 `PARTIAL_B5C_DURABLE_CANCELLATION_SHADOW / PRODUCTION_OFF` 定级。

TaskStore 应用层写 authority 封口的定向证据严格分组记录，不汇总成一个虚构总数：authority/structure `19 passed`；HostCommand `1 passed`；NLE/operation/heartbeat `77 passed`；acceptance-effect `15 passed`；parallel/settlement/atomic `45 passed`；terminal classification `32 passed`。Cancellation 组首跑是 `29 passed + 1 fixture failed`，修正 fixture 后该 case `1 passed`；integration 组首跑是 `46 passed + 3 fixture failed`，修正后 3 个 cases 分别通过。其中第三个 fixture 的 `node_risk_level` 问题已在 `origin/dev` 复现后修正。所有 fixture 修正/重命名后，authority、两个 structure、两个 recovery、两个 cancellation representative cases、durable artifact baseline fixture、dispatcher semantic order 与 HostCommand admission 的最终 key regression 为 `26 passed in 35.33s`；该批次不覆盖前述首跑失败记录。波次 integration 仍为 `NOT_RUN_PER_WAVE_POLICY`。TaskStore 切片当时 full static 为 `FAIL [1,6,10]`；Gate 10 与 Gate 6 随后通过，但当前 fresh 状态为 `FAIL [1,11]`。`tests/task_runtime_authority_fixtures.py` 只能构造无 `host_command_journal` 的 raw/orphan 状态，不是 canonical admission 证据。

Cancellation 生产权限接线不会改变该定级。Gateway 认证后的 structured authority 必须由共享 verifier 重新读取 committed Secure Ingress audit/claim，并精确绑定 request/principal/tenant、body digest、action/scopes 与 graph revision；Application 不接受调用方自报 request identity/source。Operator Runtime 按 immutable HostCommand surface 路由到 Host/Internal/Scheduler，异常后用 durable intent 区分明确未执行和可能已执行。该链路有实现与定向测试，但开关仍默认 OFF，且尚无部署凭据与真实 canary。

0098 不把历史数据“就地修好”：migration 只追加 immutable case inventory，proof 也只能追加。repairable REQUESTED case 以 PENDING proof 参与之后新建的 cancellation decision；FINALIZED quarantine 的 correction 必须继续绑定旧 decision ref/payload digest，并且只在 trusted getter/replay 中生效。`graph_revision` 同时进入签名 binding、Outbox 物理列和 case trigger。production readiness 仅审计真实数据库里的 legacy rows；本仓库无 SQLite 资产，所以当前没有任何外部生产库存已清理的证据。

0099 不在 migration 中修 Graph/Budget；它只追加 immutable case/proof/supersession schema，readiness 严格比较 3 table/4 index/10 trigger canonical SQL 并做 Python codec 复核。`recover_startup` 后单次最多 inventory 10,000 个 terminal Graph。System auto 独立默认 OFF，仅消费带唯一 graph-scope Budget 的 exact FAILED bundle，以 immutable case 时间生成跨进程稳定 proof；结构漂移永久 quarantine。Classifier 要求唯一 0090、完整 0091/0092 validator 与 exact failure Outbox refs。已 inventoried 未决 case graph-wide 围栏普通 Graph/cancellation request 与 Budget transition/consume/usage/retry，只有 0099 proof UoW bypass；旧 case 只能被新 current case 替代并最终落到可信 proof。Healthy closure 仅在历史未决 case 且 current classifier healthy 时生成 signed `NONE` 并零写 Graph/Budget/Outbox。`task_terminal.reconcile` 默认 OFF；主 Host/独立 Obs 均物理 query-only。这不是 general/multi-node/pre-budget recovery 或 production canary。

0095 的 CREATED 回填以 immutable HostCommand evidence 为 authority；ACTIVE 等非 attempt transition 以 deterministic legacy governed Outbox ID/key 重算为 authority，无法解析的自定义 producer 整事务阻断。升级保持原 ID/key，新增 revisioned payload；旧 payload 仅 replay 兼容。readiness/receipt gate 冻结 revision 列、payload trigger、delivery index 与 Runtime Event source unique partial index。旧 journal 不改写，一 Outbox 只允许一 Event，append-success/complete-loss legacy 重投只能 exact replay；ReviewStore/Operator exact revision。这关闭 identity/read-path 缺口，但不改变生产 cancellation、Budget 与 reconciliation 的剩余定级；Review continuation 由后续 0096 切片单独关闭。

0096 task-bound Review continuation 与 single-node canonical COMMITTED Operation continuation 是两条不同的窄链。后者对既有 exact accepted usage 只读核对；若 stable usage 与所有 worker-kind row 完全缺失，则在更高 fence takeover 后使用 Attempt start→Reservation commit 的正持久时间上界幂等结算。冲突/拒绝/漂移 usage、非法时间、multi-node 与 pre-operation 仍 fail closed；worker 不重跑。两者均不改变 cancellation production OFF，也不能把 general/batch/pre-operation recovery写成完成；完整架构仍为 `INCOMPLETE`。

PlanningRecovery 只在自身调用中 opt-in Graph Outbox snapshot，并使用同一读事务内真实 terminal payload ref；普通 scheduler/Operator 无新增扫描。pre-0095 ref 原样进入 failure evidence；只有旧版或替代 Store 未提供可选扩展字段时才合成 fallback，生产 DurableTaskStore 提供 inventory 后，缺行、畸形或歧义均 fail closed。0100 的 append-only supersession、`admit_replan()` 单事务 predecessor 重验+revision+1 admission、response-loss replay/rollback，以及旧 revision 的共享 TaskStore/Review mutation fence 已接通。当前 safe subset 仍只接受完全静止 `FAILED`，并保守拒绝所有历史 Review continuation、所有 Acceptance Effect 和 `BLOCKED`；自动 replan、扩大 quiescence classifier 与生产并发 canary仍缺，故 `revision_plus_one_replan=PARTIAL_DURABLE_SUPERSESSION_SAFE_SUBSET`。`created_ts=0` 的 timestamp 容差可用于 current revisioned 与 legacy unrevisioned 两种可重建 Event candidate，因为两条历史 append 路径都可能用时钟替代零值，但其它字段必须 exact。

## 3. Composition 与隔离

| 目标 | 当前状态 | 代码/证据 | 实际缺口 | 执行波 |
|---|---|---|---|---|
| 显式 Composition Root | `PARTIAL_HOST_LIFECYCLE_APP_LOCAL_SINGLE_LIVE_TRIBE_FENCED` | Task container、HostRoutePorts、ObsRoutePorts、HostObsReadPorts、LifeBodySourceReader、GovernedSkillExecutionPorts、`HostLifecycleContainer`、Tribe owner binding、D3～D7c/lifecycle tests | D1～D7a 的21 producer、app-local routes、Host五合同lineage、Memory/VTS冷读与0103边界继续有效。Life Body与Direct Skill局部隔离、Obs assembly反向依赖清零继续成立。Host lifecycle 仅在 factory/start 成功后发布，Activity factory 失败不发布 Mode，closing fence 阻断 `begin()`，ResourceManager startup sample 使用唯一 owned loop。Chat/Input、Tool、Scheduler、Guardian heartbeat 与 snapshot 读取 app-local实例；Tribe agents/bus 仍是 process-global，只以 exact owner bind/unbind fence 允许一个 live Host并令第二个 fail closed，不提供并发 multi-app Tribe隔离。ToolBroker/MCP direct/callback authority 已退役，但真实 governed executor 未接线。Container 当前55 fields/4 locks/50 mutable refs；底层Host source、Chat/Delegate、Acceptance Effect authority、heartbeat cache/client、HardState、Tribe bus、共享status、完整Host/Worker对象图、真实Edge和全局Service Locator删除仍未完成；不假定部署库已执行0103 | NE-01 |
| 无全局 Service Locator | `MISSING_WITH_CLEANUP_PROGRESS` | process container/services + cleanup contract | 两批已删6个零消费者槽位、4个死accessor与7个宽泛重导出；第三批删5个Obs零参数refresher，Tool/MCP切片再删process-owned accessor/slot并退役ASGI/http_hooks Tool seed/route locator。Host lifecycle原始快照为具名47/152→45/124、all-form 52→51、Container 56/4/51；合并Tool/MCP删除后当前精确库存为具名45 modules/123 names、all-form 51 consumer modules、Container 55 fields/4 locks/50 mutable refs。仍有数十个accessor/依赖文件 | NE-01 |
| 业务层私有 SQLite / Gate 10 | `PASS_GATE_10 / TYPED_COMPOSITION_INCOMPLETE` | `RuntimeEventRepository` readiness、`RuntimeEventStore` port、`SQLiteServiceCatalogRolloutRepository`、rollout Protocol、global SQLite boundary contract | 0112 schema 检查已离开 runtime 私有连接；Catalog 的连接/SQL/事务已归 persistence，`ACTIVATION_STARTED` receipt 与 head 同事务并有共同回滚证据。World Observation `_write_lock` 是 `RLock` 误判，现名 `_transition_lock`。但 `host_startup.py`/ASGI 仍 concrete wiring；未新增 migration。当前 full static `FAIL [1,11]`，相对 `origin/dev` 的 `FAIL [1,6,10]` 已关闭 Gate 10 与后续切片覆盖的 Gate 6；Gate 11 是独立的 ToolBroker production-service inventory mismatch，不是 Gate 10 回归。Gate 1、Gate 11 与 NE-01 仍缺 | NE-01 |
| Obs projection daemon 生命周期 / Gate 6 | `PASS_GATE_6 / CONTINUOUS_SLO_INCOMPLETE` | `ObsApplicationContainer.start_projection_jobs()`、`create_obs_app()` 嵌套 lifespan、bundle-local single-use claim、Gate 6 static contract | 生产唯一启动点完成 claim→compose→start→yield，finally 依次 stop/join/in-flight fence、精确 leader lease release、owned SQLite close、state clear；timeout/rollback close failure 保留原句柄重试。共享 bundle 和 `dataclasses.replace()` derived bundle 都不能绕过 claim，独立 ports/app 保持隔离；无 module-global daemon、无 migration。当前 full static `FAIL [1,11]`：Gate 6 issues=[]，唯一生产启动点属于 Obs 嵌套 lifespan；Gate 1 SCC 和同一 ToolBroker Gate 11 双命中仍在。daemon 未捕获异常退出还未直接进入 `/health` 的 running/last_failure，continuous-SLO supervision 未闭环 | NE-01 |
| Service Catalog 分层 / Gate 1 子切片 | `PARTIAL / GATE_1_FAIL` | `service_catalog/startup_coordinator.py`、`contracts/service_catalog.py`、同对象兼容重导出、contracts-only repository | application→composition 2 条与 Catalog persistence→application 1 条已归零，SCC 由 application/composition/persistence 缩为 application/persistence；该切片完成时仍有 persistence→application 11 条（PlanningSearch 7、Evolution/Lab/Skill/World 4）。61 项 contract/unit、7 项真实 socket integration、7 项边界复跑通过，集合可能重叠；无 migration | NE-01 |
| PlanningSearch 持久化合同分层 / Gate 1 子切片 | `PARTIAL / GATE_1_FAIL` | `contracts/planning_search/`、八组纯合同/模型、旧路径同对象重导出、contracts-only SQLite repositories | code benchmark、calibration、research shadow 仓储的 7 条 persistence→application 已归零，兼容 `__all__`、类型 identity、`isinstance` 与序列化形状保持；边界 5 项、D13/D18/D19/D20 60 项通过，Windows symlink 权限导致 1 项跳过。Gate 1 仍有 application/persistence SCC 与 Evolution/Lab/Skill/World 4 条反向依赖；application→composition=0、Edge violation=0、reverse import=0，Gate 6/10 继续通过。无 migration；不得据此宣称 PlanningSearch 功能/收益、Composition 或 NE-01 完成 | NE-01 |
| 执行巨石拆分 | `PARTIAL_GATE_RED` | `dag_scheduling_runtime.py`、focused Budget/Operation/Effect Store modules、`coordinator.py` | `task_repository.py` 当前 4,358 行；新 Budget/Operation/finalizer/workspace-fencing modules 均 <1,500且无第二 Store authority。全仓仍有 4 个既存未分类 >1,500 行违规，因此不能写 green | NE-01 Lane D 未决 |
| Host/Obs/Worker scope 隔离 | `PARTIAL_APP_LOCAL_SERVICES_SINGLE_LIVE_TRIBE_FENCED` | 独立 ASGI、Host/Obs route ports、query-only repositories、专用 token、双 app Life Body/双 runtime Skill tests、Host lifecycle/Tribe owner tests、NLE boundary scan | Obs route/helper边界与Host六个GET已app-owned；Life Body和Direct Skill局部隔离证据继续成立。Host lifecycle 服务是 app-local，但 Tribe agents/bus 保持 process-global，只支持单活 Host owner fence，第二个 live Host fail closed；并发 multi-app Tribe 隔离不支持。Host source底层authority、Chat/Delegate、Acceptance Effect与大部分Worker对象图仍共享；heartbeat cache/client、HardState、共享status、50个可变service引用、真实Edge、完整Host/Worker composition和全局Service Locator删除未闭环 | NE-01 |

Host lifecycle 最新收口只证明上述 app-local service 与单活 Tribe 围栏，不证明全局 Composition：Activity factory failure 不发布 Mode，closing 后禁止 `begin()`，ResourceManager startup sample 归属唯一 owned loop。`http_hooks.py` locator import=0，全局 setter 和 2 slot + 2 lock 已删；Tool/MCP direct/callback authority 也已失败关闭。Host lifecycle 原始 commit-bound 统计 45/124、51、56/4/51 继续保留为历史；合并后的当前值是 named 45/123、all-form 51、Container 55/4/50。状态仍是 `PARTIAL / NE-01_IN_PROGRESS`，full architecture Gate 未通过：process-wide locator、heartbeat cache/client、HardState、Tribe bus、shared resource status file、真实 Tool/MCP executor与完整 Worker scope 未清零。

Gate 10 的证据不能和其它批次相加：Runtime Context `7 passed`；Catalog unit/contract `15 passed`；Host socket integration `6 passed`；Repository/Global 独立复跑 `16 passed`，其中最后一组与 15 项有重叠。Global SQLite boundary=`PASS`。

Gate 6 的证据也按原始批次记录：完整 Obs app 首跑 `13 passed + 1 test fixture failed`，修正旧 `start` monkeypatch 后该 case `1 passed`；最终生命周期关键批次 `12 passed`；daemon cadence/shutdown/serialization/start-single-flight `4 passed`。批次重叠，禁止相加。Gate 6 issues=[] 且 `start_projection_jobs()` 生产调用点恰好一个、属于嵌套 `lifespan`。当前 full architecture static=`FAIL [1,11]`，`origin/dev` 基线=`FAIL [1,6,10]`；Gate 1 SCC、同一 ToolBroker Gate 11 双命中与 NE-01 仍未完成。两切片均无 migration，wave integration=`NOT_RUN_PER_WAVE_POLICY`，`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。
| Workspace ownership | `PARTIAL_W2_INTERNAL_GIT_WORKTREE_WIRED / DEFAULT_FAIL_CLOSED` | 0101、`workspace_repository.py`、`workspace_operation_fencing.py`、`workspace_allocator.py`、`workspace_runtime.py`、`composition/agent_workspace.py`、Internal/ASGI/W0/W1/W2 tests | Exact graph epoch authority、服务端派生 ID、冻结 source commit、独立 Git worktree、实例/PID 租约、独占 scope、heartbeat、handoff/takeover、可信 cleanup receipt、启动同步 1 条+后台 pending retry 与失败关闭生命周期已接入认证 Internal Agent；focused fencing 模块只约束 provisioning/allocator/cleanup adapter 的外部 callback，用 workspace/source 跨进程锁、共享 deadline及前后短事务避免占用 SQLite writer，且未给大文件 Gate 增加例外。Agent tool 仅动作间取消，Verification 仅启动前验活；二者尚无逐操作 fence或强杀。缺稳定 HMAC secret 时写入关闭，canary 未跑。仍缺 promotion/abandon、accepted checkpoint HEAD、Domain Pack/Codex runtime、cleanup health、OS/container/tenant 与 handle-level TOCTOU | NE-01 |
| 外部 Skill 安全接收 | `PARTIAL_NON_ATOMIC_NO_RUNTIME_RESOLVER` | Signed Intake、Quarantine、Supply Chain、0109 lifecycle ledger | URI 未查询真实 authority；状态、evidence 与 terminal receipt 非原子；exact replay/rollback evidence 有缺陷；Host registry 未切流 | NE-05 |

## 4. 数字生命与长期演化

| 目标 | 当前状态 | 代码/证据 | 实际缺口 | 执行波 |
|---|---|---|---|---|
| World Model | `PARTIAL_PRODUCTION_OBSERVATION_WIRED_NOT_PREDICTIVE` | 0111、`RuntimeWorldObservationApplication`、`WorldResolver` | Host lifespan 中以 server-owned principal/Bond/Emotion/environment 追加并重开 World，显式 expiry、competing refs 与 captured-head 封口已有；Bond 原始证据 owner 与预测世界模型仍缺 | NE-03 |
| Scene Engine | `PARTIAL_THREE_CONSUMERS_PRODUCTION_WIRED_LOCAL_ACCEPTANCE_PASSED` | 0112、`RuntimeContextApplicationService`、`SceneResolver` | caller 不能指定最终 scene/constraints，Surface、Proactive、Task admission 冻结同一 revision/digest；八类场景 commit-bound Gate 已通过，多实例 canary 待收口 | NE-03 |
| Intent Arbiter | `PARTIAL_PRODUCTION_ADMISSION_WIRED_RESTRICT_ONLY_LOCAL_ACCEPTANCE_PASSED` | `ProductionIntentAuthorityPort`、`DurableIntentAdmissionVerifier`、`IntentArbitrationV1` | graph/context/action/grant/policy 由重开 authority 证明，节点/graph receipt set 与 durable Review continuation 已接 Task Runtime；长周期目标 owner 仍缺 | NE-03 |
| Action Preview / Shadow | `PARTIAL_PRODUCTION_AUTHORITY_WIRED_LOCAL_ACCEPTANCE_PASSED` | `runtime/action_preview_authority.py`、`runtime/action_preview_resolver.py`、Task/Acceptance/Life/terminal transport gates | lifespan-owned resolver 重开 Command/Task/Attempt/Lease/Grant/Context/Scene/Guardian，derived scope/args 受 descriptor 约束；终端正向覆盖 Acceptance surface Gateway 与 Scheduler-derived Life Edge/Gateway/VTS/MateEngine。旧任务/effect、37 个直写 Operator、reply-performance/Gateway voice/Avatar 直通失败关闭；后者和 Operator 功能正向迁移属于 NE-01 | NE-03→NE-01 |
| Runtime Guardian / Service Catalog | `PARTIAL_HOST_LIFESPAN_AND_ENFORCE_TARGET_WIRED_LOCAL_ACCEPTANCE_PASSED` | 0112、0113、`runtime/runtime_guardian.py`、`service_catalog/host_startup.py` | ASGI Guardian 绑定 Host lease/fence；Catalog fresh DB 逐级真实启动五阶段且默认目标 ENFORCE，operation/receipt/head、crash takeover 可恢复并由 SQLite 状态机保护；Windows graceful stop 与部署多实例 canary 仍缺 | NE-03→NE-07 |
| Evolution Lab | `SELF_ATTESTED_SHADOW_NO_PRODUCTION_CALLER` | 0108 V1 ledger、runner | dataset digest 未独立重算、verifier 可补 hash、隔离可绕过且无 production composition | NE-04 |
| Counterfactual | `LEDGER_CONTRACT_ONLY` | V1 ExperimentReport | 没有可信独立双臂、sandbox/effect audit 或 promotion authority | NE-04 |
| Capability Maturity | `PARTIAL_NE05_PRODUCTION_FAIL_CLOSED` | scorecard/provider/Governor adapter + production IntentDecisionEngine wiring | 生产门禁默认开启且缺失/伪造 scorecard 最高风险；仍缺 durable promotion/degradation authority | NE-05 |
| Capability Forge | `PARTIAL` | `app/capability_forge.py` | 能生成/批准 blueprint 和 canary 路由，不会生成新能力实现 | NE-05 |
| Skill Compiler | `PARTIAL_NE05_DETERMINISTIC_DRAFT` | `host_brain/skills/skill_compiler.py`、`SkillDraftV1`、NE-05 tests | typed draft 与确定性 digest 已有；SBOM/signature request、sandbox、activation 全链由独立 lifecycle 继续补齐 | NE-05 |
| Experience Feedback | `LEDGER_ONLY_NO_ACTIVATION_AUTHORITY` | 0110 V1 ledger、Prefrontal adapter | APPLIED 不切 versioned policy/persona；Lab/Review 未解析；旧 caller 仍待替换 | NE-06 |
| Knowledge OS | `LEDGER_ONLY_NO_ACTIVE_AUTHORITY` | `KnowledgeEntryV1`、0110 namespace ledger | active winner、cursor、supersedes/deprecation/dedup/conflict 与旧 reader cutover 均缺失 | NE-06 |
| Identity Snapshot | `PARTIAL` | `release/identity_snapshot.py` | 合同丰富，但 gate 默认关闭，未由 release 主链生成/持久化 | NE-06 |
| Identity Migration | `LEDGER_ONLY_NO_ACTIVATION_AUTHORITY` | `IdentityMigrationPlanV1`、0110 identity event ledger | ACTIVE 不切 snapshot owner，ROLLED_BACK 不恢复 exact previous version，transition identity 不支持重复 release | NE-06 |

## 5. 历史证据的正确解释

| 证据 | 可证明 | 不能证明 |
|---|---|---|
| Core Release 22/22 | 对应提交的基础设施与选定 Gate 通过 | 当前全架构、所有生产 route 或演化能力完成 |
| GR-06 live receipt | 当时 Host `chat/skill/delegate/plan` 可产生 durable lineage | Scheduler/Internal、legacy route 删除、所有副作用单事务 |
| GR-02A benchmark | 公共算法能解决冻结的 synthetic trap | Research/Code 生产 uplift、真实成本或安全切流 |
| D20a Research catalog / corpus-loader tests | 公开 metadata 没有 dedicated locator/source-body/answer-key/verifier-decision 字段，constraints 拒绝命名 key；受信实验根的 manifest 文件读取会在稳定输入中拒绝路径、ADS colon/case alias、link/reparse、未列来源与摘要漂移 | snapshot-manifest-document exact handshake、真实 Research outcome、模型/独立 verifier、paired cohort、成本/延迟或收益 |
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
Operation recovery                           SINGLE_NODE_POST_BUDGET_PROVEN / GENERAL INCOMPLETE
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
