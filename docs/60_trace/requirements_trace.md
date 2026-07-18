受众：MASTER
标题：当前需求可追溯矩阵

# 当前需求可追溯矩阵

版本：v3.9
核对日期：2026-07-18
状态来源：[current_architecture_status.json](../../configs/release/current_architecture_status.json)

本文只维护当前 FR → 工程合同 → 生产代码 → 测试/证据 → 下一执行波的关系。旧 R-01～R-192、TASK-0000～0622 和阶段状态已移出当前权威链，可通过 `docs/_history/programs/2026-07/` 与 Git `9b6db346^` 追溯。

## 1. 解释规则

1. PRD 定义 WHAT，不记录实现完成叙事。
2. DevSpec 定义稳定工程合同，不承担任务状态。
3. 架构级原子状态写入 `current_architecture_status.json`；下表的复合 FR 状态只能由这些原子状态和代码审计推导。
4. 自动化测试证明其覆盖的行为，不能单独把 `PARTIAL` 提升为 `WIRED`。
5. 历史 receipt 只对固定 commit、route 和环境成立。
6. 执行顺序只来自 [NEXT_GENERATION_EVOLUTION_EXECUTION.md](../30_devspec/NEXT_GENERATION_EVOLUTION_EXECUTION.md)。

## 2. 当前核心产品能力

| FR | 工程合同 | 生产落点 | 主要验证 | 当前状态 |
|---|---|---|---|---|
| FR-CHAT | DevSpec §2～4 | `host_brain/app/chat_routes.py`、Cortex、Surface | chat/integration、GR-06 固定 receipt | `WIRED_WITH_LIMITS` |
| FR-INGRESS | DevSpec §2、§8 | Secure Ingress、Host/Gateway/Scheduler/Internal adapters | route-matrix contracts、Scheduler/Internal production integration | `WIRED_WITH_OPERATOR_MATRIX_LIMITS` |
| FR-SURFACE | DevSpec §4、§11 | Surface/Reply authority、Life projection | surface/life contracts、writer 审计 | `PARTIAL_PERSONA_PROPOSAL_MISSING` |
| FR-MEMORY、FR-CANON | DevSpec §5 | typed repositories、Canon Apply、recall | memory/canon contracts | `WIRED_WITH_KNOWLEDGE_GAPS` |
| FR-TOOL、FR-CAPABILITY | DevSpec §7 | Capability Broker、Skill/Tool/MCP ports | capability/supply-chain contracts | `PARTIAL_LIFECYCLE` |
| FR-GATEWAY、FR-VOICE、FR-EMBODIMENT | DevSpec §8 | Gateway、Edge contracts、device receipt | Edge/Gateway contracts、环境验收 | `WIRED_DEGRADED_HARDWARE` |
| FR-OBS | DevSpec §9 | journal/outbox/projection/Obs/Operator | event/query/privacy tests | `WIRED_WITH_COMPOSITION_GAPS` |
| FR-PENTEST | DevSpec §7 | Pentest domain runtime + PlanningSearch adapter | security/pentest suites | `WIRED_L3_SHADOW_UNCALIBRATED` |
| FR-PROACTIVE | DevSpec §4、§11 | proactive intent/outbox + Persona feedback paths | proactive contracts、writer 审计 | `PARTIAL_PERSONA_FENCE_WIRED` |
| FR-AGENT-RUNTIME、FR-PLANNING | DevSpec §3、§6 | Governed Runtime、PlanningSearch | T-GR-RUNTIME-01、T-PLAN-* | `PROGRAM_INCOMPLETE` |

## 3. Governed Execution

| Trace | FR | 合同 | 生产落点 | 证据 | 当前状态 | 下一波 |
|---|---|---|---|---|---|---|
| TRACE-EXEC-01 | FR-ARCH-003 | Host 四路线必须共享 WorkOrderV3/TaskGraph | Cortex、GovernedHostRuntime | GR-05/06 receipt；T-GR-RUNTIME-01 | `WIRED` | NE-01 守卫 |
| TRACE-EXEC-02 | FR-ARCH-003 | Scheduler 必须进入同一 admission | Scheduler governed runtime | production cutover integration；child grant/receipt lineage | `WIRED` | NE-01 已完成切片 |
| TRACE-EXEC-03 | FR-ARCH-003 | Internal Agent 必须进入同一 admission | Internal task runtime | production cutover integration；principal/child grant/Review/receipt lineage | `WIRED` | NE-01 已完成切片 |
| TRACE-EXEC-04 | FR-NLE-002 | 多节点依赖、Attempt、Lease、Retry、受控 fan-out/fan-in、显式结果、整图原子成功与 verified durable cancellation | coordinator/heartbeat/task store；0090～0098；Budget/Operation focused Store；shared finalizer | Budget unique winner及 0090 single-snapshot terminal-authority 重验、single-node post-budget takeover 与 ACCEPTED→Effect restart已有证据；Cancellation/reconciliation仍默认 OFF | `PARTIAL_B5C_DURABLE_CANCELLATION_SHADOW / PRODUCTION_OFF`。general/batch/pre-budget recovery、历史半状态、现实 resolver、部署 key/credential、inventory remediation与 canary仍缺 | NE-01 B5C deployment/canary |
| TRACE-EXEC-04A | FR-NLE-002 | 0095 recovery evidence 不得伪造 revisioned ref 或拖慢普通调度 | `read_scheduler_snapshot(include_graph_outboxes)`；PlanningRecovery；Runtime Event replay | 默认 false；recovery 显式 true 且同事务 exact terminal Outbox；legacy payload 原样使用；仅旧版或替代 Store 未提供可选扩展时 fallback；created_ts=0 对 current/legacy 可重建 candidate 的受限容差，除 timestamp 外全字段 exact；GR-04/Runtime Event tests | `WIRED_0095_COMPAT / PROGRAM_STILL_OFF` | 持续故障矩阵 |
| TRACE-EXEC-04B | FR-ARCH-001、FR-NLE-002 | Task mutation 的中央 Outbox insert/replay 实现必须唯一，结构拆分不得产生第二事务 authority | Outbox/BudgetUsage/OperationCheckpoint/Effect focused mixins；由 `DurableTaskStore` 组合 | 调用方事务复用、MRO/行为测试、`task_repository.py` 4,145 ≤ 4,600 policy | `WIRED_FOCUSED_STORE / ONE_UOW` | 持续结构门禁 |
| TRACE-EXEC-04C | FR-ARCH-003、FR-NLE-002 | Operator TaskGraph 取消必须使用 server-owned Secure Ingress authority，精确绑定 revision，并在失败时给出可证明的副作用确定性 | cancellation authority/runtime/Gateway/BFF/UI | server-owned authority、exact revision、durable intent certainty、default OFF 均有测试 | `WIRED_DEFAULT_OFF / PRODUCTION_OFF` | 部署两类 reconciliation、密钥/凭据与真实 canary；历史半状态/general recovery |
| TRACE-EXEC-04D | FR-NLE-002 | Cancellation Effect 只能凭可证明证据解除 fence；历史修复不得改写旧 authority；独立 Obs 只能只读 canonical DB | Effect reconciliation contract/service/focused Store + 0098；`effect_reconciliation_query_repository.py`；Effect delivery/cancellation；Operator command/read routes | `test_ne01_graph_cancellation_effect_reconciliation.py` 覆盖 PENDING/FINALIZED proof；`tests/integration/test_ne01_obs_effect_reconciliation_read.py` 覆盖 ledger canonical path、`mode=ro + query_only`、flag-off 不触 0098、`obs:task-runtime:read` 窄 scope、generic scope 403、最小 cases/proof 与可签名 challenge。repository 只继承 read/validation mixin，不构造 DurableTaskStore/UoW；旧 payload/hash/fence/evidence不变 | `WIRED_DEFAULT_OFF / CANARY_NOT_RUN / PRODUCTION_OFF` | 在每个目标部署运行 legacy inventory gate/remediation；部署 verification keys、Operator credentials与完整 key lifecycle；运行真实 canary |
| TRACE-EXEC-04E | FR-NLE-002 | 自动接管不得重跑 worker或补造缺失预算事实 | `TaskOperationCheckpointStoreMixin`、`DurableOperationRecoveryRuntime`、`CommittedCheckpointContinuation` | exact COMMITTED Outbox/Attempt/历史 Lease/canonical projection/accepted worker usage；旧 Lease expiry/fence；online takeover integration | `SINGLE_NODE_POST_BUDGET_CHECKPOINT_PROVEN / GENERAL_RECOVERY_INCOMPLETE` | general/batch/pre-budget recovery、历史半状态 |
| TRACE-EXEC-04F | FR-NLE-002 | ACCEPTED 已落盘而 Effect 未落盘时不得无 Effect 完成 Graph | `AcceptedGraphFinalizationRuntime`、Graph+Effect Store UoW | process-loss restart、worker no-rerun、effect id/digest/idempotency replay、tampered projection fail-closed | `PROVEN_NARROW_RESTART_FINALIZATION` | production canary；跨系统 eventual dispatch 仍非单事务 |
| TRACE-EXEC-04G | FR-ARCH-001、FR-ARCH-003、FR-NLE-002 | 生产 Graph admission 与 Acceptance Effect staging 不得重新暴露 standalone Python 写 authority | `HostCommandRepository._admit_normalized()`；`TaskDagStateStoreMixin._insert_graph_from_host_command_in_transaction()`；Graph/Node/Parallel Success canonical acceptance UoW | public `create_graph`、`insert_graph_in_transaction`、`stage_acceptance_effect_intent` 均不存在；Graph hook 仅 admission UoW 1 次；Effect hook 仅三个 canonical UoW 共 6 次；Graph/Node 与 Effect INSERT SQL 各只有一个私有 owner；test fixture 明确 raw/orphan 且无 `host_command_journal`。分批证据：authority/structure 19、HostCommand 1、NLE/Operation/Heartbeat 77、Effect Store 15、Parallel/Settlement/Atomic 45；Cancellation 首跑 29+1 fixture failure 后单 case 通过，Integration 首跑 46+3 fixture failures 后 3 cases 通过，Terminal classification 32；fixture 修复/重命名后的最终关键回归 `26 passed in 35.33s`，不覆盖首轮失败 | `APPLICATION_PYTHON_WRITE_AUTHORITY_SEALED / SQL_DIRECT_BYPASS_PRESENT / NE01_IN_PROGRESS`。SQLite 仍允许 orphan/raw SQL，本轮无 migration/trigger；wave integration `NOT_RUN_PER_WAVE_POLICY`；该切片完成时 full architecture static `FAIL [1,6,10]` | 新 migration/trigger 与回滚演练消除 SQL bypass；NE-01 波次集成 |
| TRACE-EXEC-04H | FR-ARCH-001、FR-NLE-005 | runtime/service-catalog 业务层不得持有私有 SQLite 连接、SQL 或事务；rollout receipt/head 必须原子 | `RuntimeEventStore` readiness port → `RuntimeEventRepository`；rollout Protocol → `SQLiteServiceCatalogRolloutRepository`；`host_startup.py`/ASGI concrete wiring | Context `7 passed`；Catalog unit/contract `15 passed`；Host socket `6 passed`；Repository/Global 独立复跑 `16 passed`（与 15 重叠不相加）；Global SQLite boundary `PASS`；`ACTIVATION_STARTED` receipt + head failure rollback；`_write_lock` RLock 误判更名 `_transition_lock` | `GATE_10_PASS / CURRENT_FULL_STATIC_PASS / TYPED_COMPOSITION_INCOMPLETE / NE01_IN_PROGRESS`。无 migration；该切片当时的基线失败不覆盖当前结果，当前 Gate 1/6/10/11 均已通过；wave integration `NOT_RUN_PER_WAVE_POLICY`；`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA` | 将 concrete wiring 收入 typed app-local composition |
| TRACE-EXEC-04I | FR-ARCH-001、FR-NLE-005 | Obs projection daemon 必须只有一个 app lifespan 启停 owner，mutable ports bundle 不得跨 app 共享 | `ObsApplicationPorts.claim_lifecycle_owner()`；`ObsApplicationContainer.start_projection_jobs()/close()`；`obs_fastapi_app.py::create_obs_app().lifespan` | 生产 `start_projection_jobs()` 调用恰好 1 个且位于嵌套 lifespan；claim→compose→start→yield，finally stop/join/in-flight fence→exact lease release→owned SQLite close→state clear；timeout/rollback close failure 保留原句柄；shared/`dataclasses.replace()` bundle fail closed，独立 app 隔离；无 module-global daemon。首跑 13+1 fixture failure，修正 case 1；关键生命周期 12；daemon 4；最终定向 19，集合重叠不相加 | `GATE_6_PASS / CURRENT_FULL_STATIC_PASS / CONTINUOUS_SLO_INCOMPLETE / NE01_IN_PROGRESS`。无 migration；Gate 1/6/10/11 均已通过；wave integration `NOT_RUN_PER_WAVE_POLICY`；`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA` | `/health` 直接呈现 daemon.running/last_failure；未捕获 renew 等异常的 continuous-SLO supervision |
| TRACE-EXEC-04J | FR-ARCH-001、FR-NLE-005 | Service Catalog application 不得反向依赖 composition，persistence 不得依赖 application 合同 | `service_catalog/startup_coordinator.py`；`contracts/service_catalog.py`；composition/legacy contract 同对象重导出；contracts-only `SQLiteServiceCatalogRolloutRepository` | application→composition 2→0，Catalog persistence→application 1→0；SCC application/composition/persistence→application/persistence；contract/unit 61、真实 Catalog/Host socket 7、边界复跑 7（集合可能重叠）；Edge violation=0、reverse import=0；py_compile/diff-check PASS | `PARTIAL / GATE_1_FAIL / NE01_IN_PROGRESS`。该切片快照仍有 persistence→application 11 条（PlanningSearch 7、Evolution/Lab/Skill/World 4），后续 TRACE-EXEC-04K 清除其中 7 条；无 migration；wave integration `NOT_RUN_PER_WAVE_POLICY`；`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA` | 按 TRACE-EXEC-04K 继续清除反向依赖；完成 typed app-local Composition |
| TRACE-EXEC-04K | FR-ARCH-001、FR-PLANNING | PlanningSearch 持久化只能依赖纯合同包，旧 application 类型路径必须保持兼容重导出而不成为 repository authority | `contracts/planning_search/`；core/benchmark/calibration/code/research 八组合同；code benchmark/calibration/research shadow SQLite repositories；旧路径同对象重导出 | 边界 5；D13/D18/D19/D20 60 passed、1 skipped（Windows symlink 权限）；`__all__`、类型 identity、`isinstance`、序列化形状兼容；7 条 PlanningSearch persistence→application 清零；application→composition=0、Edge violation=0、reverse import=0；py_compile/diff-check PASS | `PARTIAL_CONTRACT_LAYERING_ONLY / GATE_1_FAIL / NE01_IN_PROGRESS`。application/persistence SCC 仍有 Evolution/Lab/Skill/World 4 条；Gate 6/10 PASS；无 migration；wave integration `NOT_RUN_PER_WAVE_POLICY`；`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`；不构成 PlanningSearch 功能或收益完成 | 清除剩余 4 条反向依赖并复跑 Gate 1；NE-02 独立完成生产候选与收益证明 |
| TRACE-EXEC-04L | FR-ARCH-001、FR-NLE-004、FR-NLE-005、FR-ARCH-010 | Evolution、Evolution Lab、Runtime Context、Skill Lifecycle 的持久化共享类型只能来自中立 contracts，repository 不得反向依赖 application | `contracts/evolution.py`、`contracts/evolution_lab.py`、`contracts/runtime_context.py`、`contracts/skill_lifecycle.py`；旧路径同对象重导出；Evolution Governance/Lab、Skill Lifecycle、World Observation repositories | 边界 4；Context/World、Evolution Lab、Skill、Evolution Governance 定向 32；py_compile/diff-check PASS；最后 4 条 persistence→application 清零；fresh Gate 1 issues=[]、SCC=[]、Edge finding/violation=0、reverse imports=[]，Gate 1/6/10 PASS；该切片完成时 full static `failed_gates=[11]`，后续 TRACE-EXEC-04M 关闭 Gate 11 | `GATE1_PASS / CURRENT_FULL_STATIC_PASS / NE01_IN_PROGRESS`。无 migration；wave integration `NOT_RUN_PER_WAVE_POLICY`；`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`；仅证明分层与类型 identity，不构成 NE-04～NE-06 功能、Universal Runtime、PlanningSearch 收益或 Full Architecture 完成 | 完成 typed app-local Composition、旧 authority 清零与 NE-01 波次生产验收；后续波次独立证明功能闭环 |
| TRACE-EXEC-04M | FR-ARCH-001、FR-NLE-005 | ToolBroker 生产构造必须 app-local 且唯一，process locator/container 与未消费 lifecycle provider 不得拥有第二构造 authority | `host_brain/tools/broker.py`；删除 `get_tool_broker`、`tool_broker` process slot 与 `HostLifecycleToolBrokerProvider`；Gate 11 production-service inventory | #89 commit-bound：唯一 constructor count=1；process accessor/slot/dead provider=0；Container 54 fields/4 locks/49 refs，named 45/123、all-form 51；结构/生命周期/bridge/route-token/seed 43 passed；ASGI+Tool/MCP+HTTP+broker+MCP audit+真实 SQLite 98 passed；补充 1 项与 43 重叠；py_compile、policy JSON、whitespace PASS | `GATE_11_PASS / FULL_STATIC_PASS / NE01_IN_PROGRESS`。`failed_gates=[]`；真实 workspace-bound governed executor、完整 typed Composition 与波次验收仍缺；`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA` | 继续清零其余 process-wide locator，并接入真实 executor/canary |
| TRACE-EXEC-05 | FR-NLE-002 | Budget 声明必须被 runtime 消费且高竞争只产生一个超限 authority | budget runtime、`TaskBudgetUsageStoreMixin` | 8/16/32 线程、跨进程、restart/interleave/drift；concurrency=8 时 8 次 CAS conflict 后第 9 次成功 | `CONTENTION_PROVEN / PROGRAM_PARTIAL` | concurrency charge、DEGRADE/REVIEW |
| TRACE-EXEC-06 | FR-NLE-003 | Review token 后自动安全 continuation | ReviewStore、0096 coordination、Review worker、surface runtime、`review:decide` ingress | task-bound PRE/POST、claim takeover、POST no-rerun | `WIRED_0096_TASK_BOUND / CANARY_NOT_RUN` | 部署 credential/canary；不要与 single-node Operation takeover或 general recovery混写 |
| TRACE-EXEC-07 | FR-ARCH-003 | 不得有第二任务真相 | Durable Task/Conversation projections | registry absence contract + code audit | `WIRED` | NE-01 已完成切片 |
| TRACE-EXEC-08 | FR-ARCH-001 | 显式 composition、无 locator | Task container + process service locator + app-local Host lifecycle + process-global Tribe owner fence | Host lifecycle 的具名 47/152→45/124、all-form 52→51、Container 56/4/51，Gate 11 收口前 55/4/50，#89 的具名 45/123、all-form 51、Container 54/4/49，以及前一 ReAct 切片的具名 43/120、all-form 49，均为提交绑定的历史库存。process `get_tool_broker` accessor、`tool_broker` slot 与 dead lifecycle provider 已删除，唯一 ToolBroker 构造位于 app-local `host_brain/tools/broker.py`；视觉切片继续删除 route-local locator/closure writer，且没有新增 process-owned slot。当前机械库存为具名 41/117、all-form 47、Container 54/4/49。lifecycle factory/publish/closing/single-loop 与 Tribe 单活第二实例 fail-closed 证据继续成立；该围栏不证明并发 multi-app Tribe 隔离，heartbeat cache/client、HardState、Tribe bus 与共享 status 仍 process-global | `INCOMPLETE_SERVICE_LOCATOR_REMAINS / STATIC_GATE_PASS / NE01_IN_PROGRESS` | NE-01 |
| TRACE-EXEC-09 | FR-ARCH-001、FR-ARCH-010 | Agent/Verification 路径不得逃逸 workspace | canonical resolver + scoped tool/verification wiring | resolver/tool/verification unit + contract | `D0_WIRED / OWNERSHIP_INCOMPLETE` | NE-01 |
| TRACE-EXEC-09A | FR-ARCH-003、FR-AGENT-RUNTIME | ReAct Skill/Delegate 不得在当前节点内绕过 TaskGraph 直执行 | ReAct registry/loop、Skill executor、Delegate dispatcher | `run_skill` / `dispatch_sub_agent` discovery 为 0；注入 legacy ToolSpec 在低层执行前固定拒绝；其他 Agent Tool authority 不在该结论内 | `RETIRED_FAIL_CLOSED / POSITIVE_PATH_MISSING` | NE-01 新 graph revision 节点 |
| TRACE-EXEC-09B | FR-ARCH-003、FR-AGENT-RUNTIME | Visual Agent HTTP 不得持有 route-local 模型/ClawdBot/Edge 执行 authority | `/v1/agent/run` compatibility route、vision chat format fence、legacy Action Planner seal | 固定 SSE 拒绝、raw loop/client/Ledger writer 退役、非 text 在模型/Memory/Ledger 前拒绝；不等于 Delegate `vision_agent` 或正向 Visual executor | `RETIRED_COMPATIBILITY_ONLY / PRODUCTION_OFF` | NE-01 governed Visual TaskGraph revision |

TRACE-EXEC-04 的 lifecycle 验收还要求：activation rollback 或 `aclose` quiescence 未完成返回 `TASK_RUNTIME_LIFECYCLE_RETRY_REQUIRED`，bind/activate 必须锁死且只能 `aclose` 重试；Coordinator admission close、drain、`aclose` 任一失败均保持 Store 开放，Coordinator 与 worker 全静默后才 drain Outbox/关 infrastructure。

## 4. Planning Search

| Trace | FR | 合同 | 生产落点 | 证据 | 当前状态 | 下一波 |
|---|---|---|---|---|---|---|
| TRACE-PLAN-01 | FR-PLANNING、FR-ARCH-003、FR-ARCH-010 | 公共无副作用搜索内核与 immutable compile boundary | `planning_search/`、`goal_planning.py`、`planning_graph_compiler.py`、`planning_execution.py` | GR-01、D14、D16 unit；state/context/grant lineage | `WIRED_FOUNDATION / DOMAIN_CUTOVER_INCOMPLETE` | NE-02 |
| TRACE-PLAN-02 | FR-PLANNING、FR-ARCH-003 | Host 从目标生成竞争计划 | `plan_intent_search.py` | 代码审计；T-PLAN-PROD-01 | `MISSING` | NE-02 |
| TRACE-PLAN-03 | FR-PENTEST | Pentest L1/L2 使用公共 service | Pentest adapter/AgentLoop | GR-01 pentest tests | `WIRED` | NE-02 |
| TRACE-PLAN-04 | FR-PENTEST、FR-PLANNING | L3 能按虚拟状态生成后继并 durable 校准 | Pentest virtual world | tests 仅 root candidate/shadow；T-PLAN-CAL-01 | `SHADOW_UNCALIBRATED` | NE-02 |
| TRACE-PLAN-05 | FR-PLANNING、FR-ARCH-010 | Research 真实收益 | synthetic fixture only | GR-02A report；T-PLAN-PROD-01 | `UNPROVEN` | NE-02 |
| TRACE-PLAN-06 | FR-PLANNING、FR-ARCH-010 | Code 动态 pure-shadow planning 与真实收益 | `planning_search/code_domain_models.py`、`planning_search/code_domain.py`、`planning_search/code_benchmark_models.py`、`code_benchmark_sandbox.py`、`code_benchmark_ledger.py`、`code_benchmark_verification_contracts.py`、`code_benchmark_verifier.py`、0106 ledger | D16 dynamic catalog、forged-input/root-policy rejection、MCTS readiness gate、context/replan lineage；D17 trusted-process private-bare dual-worktree/portable ACL/source-fingerprint drift detection；D18 independent preregistration and cleanup-gated terminal-evidence transaction、two-arm evidence、`VERIFIED_CLEAN`、append-only/restart-replay/schema-drift SQLite integration；D19 frozen pytest argv、paired test/wall rejection、`PASS ↔ exit_code=0`、run/arm/plan/budget and pair-total binding、child-start reservation/no same-arm retry、failed-arm terminal rejection、path-free receipt、tampered restart-replay rejection；GR-02A 仍仅 synthetic | `UNPROVEN`：已证明 catalog/compile 纯度、cleanup-gated durable experiment evidence 及受控 pytest command；不能约束恶意同主机测试/adapter，D19 也不是 OS/container/network sandbox、hermetic Python、模型/工具/token/cost gate、hidden verifier 或收益，仍未证明真实 historical cohort/statistical uplift | NE-02 |

## 5. Digital Life 与长期演化

| Trace | FR | 目标合同 | 当前落点 | 当前状态 | 下一波 |
|---|---|---|---|---|---|
| TRACE-EVO-01 | FR-NLE-004 | World facts/provenance/TTL/conflict 进入 Context | 0111、`RuntimeWorldObservationApplication`、`WorldResolver` | `PARTIAL_PRODUCTION_OBSERVATION_WIRED_NOT_PREDICTIVE` | NE-03 本地 Gate |
| TRACE-EVO-02 | FR-NLE-004 | Scene aggregate/transition/策略 | 0112、`RuntimeContextApplicationService`、`SceneResolver` | `PARTIAL_THREE_CONSUMERS_PRODUCTION_WIRED` | NE-03 本地 Gate |
| TRACE-EVO-03 | FR-NLE-004 | user/task/life/relationship/authority 仲裁 | `ProductionIntentAuthorityPort`、`DurableIntentAdmissionVerifier` | `PARTIAL_PRODUCTION_ADMISSION_WIRED_RESTRICT_ONLY` | NE-03 本地 Gate；长周期 goal owner 后续 |
| TRACE-EVO-04 | FR-NLE-004 | 高风险 action 统一 preview/shadow | `ProductionActionPreviewAuthorityResolver`、Action Preview RuntimeEvent、terminal transport gates | `PARTIAL_PRODUCTION_AUTHORITY_WIRED_LOCAL_ACCEPTANCE_PASSED` | Operator Governed route 属 NE-01 |
| TRACE-EVO-05 | FR-NLE-005 | 统一 health/degradation/recovery/SLO 与 Catalog rollout | RuntimeGuardian、0112、0113、Host Service Catalog startup | `PARTIAL_HOST_LIFESPAN_AND_ENFORCE_TARGET_WIRED_LOCAL_ACCEPTANCE_PASSED` | Windows graceful stop 与部署 canary 后续 |
| TRACE-EVO-06 | FR-ARCH-010 | Scenario/Experiment Lab | 0108 V1 ledger + self-attested runner | `SHADOW_UNTRUSTED_EVIDENCE` | NE-04 |
| TRACE-EVO-07 | FR-ARCH-010 | Counterfactual replay | V1 report contract | `CONTRACT_ONLY_NO_TRUSTED_RUNNER` | NE-04 |
| TRACE-EVO-08 | FR-NLE-005、FR-ARCH-007 | Maturity 成为默认 restrict-only 门禁 | scorecard + disabled adapter | `PARTIAL` | NE-05 |
| TRACE-EVO-09 | FR-ARCH-007、FR-ARCH-010 | Skill Compiler 与 signed lifecycle | deterministic draft + intake/quarantine + 0109 ledger | `PARTIAL_NON_ATOMIC_NO_RUNTIME_RESOLVER` | NE-05 |
| TRACE-EVO-10 | FR-ARCH-010 | 经验只以 proposal 回流 | 多条局部 feedback；T-PERSONA-PROPOSAL-01 待实现 | `PARTIAL_BROKEN_PATH` | NE-06 |
| TRACE-EVO-11 | FR-ARCH-005、FR-ARCH-010 | Knowledge provenance/version/conflict | Memory/Retrieval/Canon 基础 | `MISSING_KNOWLEDGE_OS` | NE-06 |
| TRACE-EVO-12 | FR-NLE-005 | Identity snapshot 接 release | pure contract | `PARTIAL_DEFAULT_OFF` | NE-06 |
| TRACE-EVO-13 | FR-ARCH-004、FR-ARCH-010 | Identity migration/regression/rollback | 无 executor | `MISSING` | NE-06 |
| TRACE-EVO-14 | FR-NLE-005、FR-ARCH-010 | Persona trait 只能由 proposal→Lab→Review→版本激活写入 | legacy writer 已中央拒绝，`T-PERSONA-FENCE-01` 已接入；当前入口只形成无副作用候选 | `FENCE_COMPLETE / PROPOSAL_MISSING` | NE-01 围栏完成；NE-06 由 TRACE-EVO-10 全量替换 |

## 6. 下一代执行波映射

| 执行波 | 主要 FR | 必须关闭的 Trace |
|---|---|---|
| NE-00 | 文档治理 | 当前权威链和机器状态 |
| NE-01 | FR-NLE-002～003、FR-NLE-005、FR-ARCH-001～003、FR-ARCH-010 | TRACE-EXEC-01～09B；TRACE-EVO-14 safety fence |
| NE-02 | FR-ARCH-003、FR-ARCH-010、FR-PENTEST | TRACE-PLAN-01～06 |
| NE-03 | FR-NLE-004～005、FR-ARCH-004 | TRACE-EVO-01～05 |
| NE-04 | FR-ARCH-010 | TRACE-EVO-06～07 |
| NE-05 | FR-NLE-005、FR-ARCH-007、FR-ARCH-010 | TRACE-EVO-08～09 |
| NE-06 | FR-ARCH-004、FR-ARCH-005、FR-ARCH-010、FR-NLE-005 | TRACE-EVO-10～14（含 direct writer replacement） |
| NE-07 | 全部 | T-NE-FINAL-01；所有 `BROKEN/MISSING/UNPROVEN/UNSAFE` 清零并重新验收 |

## 7. 证据边界

- `docs/45_testing/test_reports/gr06_universal_governed_runtime_live_acceptance_20260715.json`：Host 四路线固定验收。
- `docs/45_testing/test_reports/gr02a_planning_search_shadow_benchmark_v1.json`：synthetic simulation，不是生产收益。
- `docs/50_release/core/**`：Core Infrastructure 固定提交发布证据。
- `docs/50_release/m48/**`：M48 历史快照，不能推导当前状态。
- `docs/30_devspec/evidence/m48b/**`：M48-B 固定范围证据。

新增证据必须绑定 source commit、环境、route、dataset、预算、失败尝试和原始 receipt；禁止只写总结性 PASS。
