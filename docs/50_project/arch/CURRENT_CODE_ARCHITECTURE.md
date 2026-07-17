受众：MASTER
标题：Natsume AI 当前代码架构事实

# Natsume AI 当前代码架构事实

版本：v5.14
核对日期：2026-07-17
原始审计基线：`origin/dev@a6d7ae383`，叠加仓库清理 `f0cd1dfd`；当前事实以本文所在提交的工作树为准
状态：`CODE_FACTS / FULL_ARCHITECTURE_INCOMPLETE`

本文只回答“代码现在是什么”。目标、执行顺序和历史结论分别由 [TARGET_ARCHITECTURE.md](TARGET_ARCHITECTURE.md)、[NEXT_GENERATION_EVOLUTION_EXECUTION.md](../../30_devspec/NEXT_GENERATION_EVOLUTION_EXECUTION.md) 与 [历史程序目录](../../_history/programs/2026-07/README.md) 承担。若本文与代码冲突，以生产代码和生产 composition 为准。

## 1. 结论

当前系统已经建立较完整的持久化、授权、事件、模型、设备和 Host 四路线执行地基，但还不是完整的下一代架构。

| 能力 | 当前定级 | 代码事实 |
|---|---|---|
| Core Infrastructure | `AVAILABLE` | migration、journal、outbox、model attempt、capability broker、Life、Edge、Query 等主干已存在 |
| Host `chat/skill/delegate/plan` | `WIRED` | 四条路线进入 `GovernedHostRuntime`，Executive 对它们 fail closed |
| Scheduler | `WIRED` | Clock/Manual occurrence 已贯通 `WorkOrderV3 + BudgetPolicy + child grant + Review + Verification + Acceptance + Receipt` |
| Internal Agent | `WIRED` | v2 只在 ingress/executor 兼容边界存在；canonical admission 使用 `WorkOrderV3 + BudgetPolicy + delegate:agent_runtime`，并验证 child grant、Review、Verification、Acceptance、Receipt 与 replay |
| 原生多节点 Durable TaskGraph | `PARTIAL_B5C_DURABLE_CANCELLATION_SHADOW / PRODUCTION_OFF` | 0102+GR06 已把 exact NODE Effect、immutable artifact、`ACCEPTED` 与 Attempt-bound Outbox 合为 canonical 应用 Store UoW，successor input 与 Effect claim 均重验 exact artifact；startup 隔离活动 legacy 空合同/缺 artifact及缺 artifact 的可投递 terminal Effect。应用 ACCEPTED writer 和完成后 standalone Effect insert 已封口；按 scope 的 pre-stage、legacy/internal graph create 与 direct SQL 仍是兼容/物理旁路。general/batch/pre-budget recovery、部署 inventory/key/canary 仍缺 |
| 通用 PlanningSearch 内核 | `AVAILABLE` | 领域无关 contracts、ports、L1/L2/L3 算法存在 |
| Host 智能候选规划 | `NOT_IMPLEMENTED` | Host Plan 只对已批准 `planned_steps` 做拓扑合法化，不生成竞争方案 |
| Pentest L3 | `WIRED_SHADOW_UNCALIBRATED` | Conditional MCTS 可运行，但校准非 durable，不能证明 production uplift |
| Code/Research 收益 | `SIMULATION_ONLY` | 只有冻结模拟 fixture；没有真实仓库、真实来源或真实模型 shadow |
| Budget | `CORE_INGRESS_WIRED / CONTENTION_PROVEN / PROGRAM_PARTIAL` | 8/16/32 线程及跨进程只产生一个 EXCEEDED winner/Outbox，其他 sibling 引用 winner；charge CAS attempt 至少为 `concurrency + 1`，concurrency=8 的第 9 次成功已验证。concurrency 计量与 DEGRADE/REVIEW 未完整执行 |
| Review/Resume | `WIRED_0096_TASK_BOUND / CANARY_NOT_RUN` | task-bound Review 的批准、拒绝和过期与 continuation 原子提交；composition worker 按原 Host/Internal/Scheduler surface 自动重入。PRE 不制造伪 Attempt，POST 不重跑 worker；独立 `review:decide` policy 已接线，但 Operator credential/UI 和真实 canary未完成，一般 recoverable Attempt 仍未统一在线续跑 |
| 对话副作用 | `POST_ACCEPTANCE_EVENTUAL` | Acceptance 原子写 effect intents；user/assistant/delivery 实际应用不是一个全局事务 |
| Composition | `INCOMPLETE / D7C_OBS_APP_ASSEMBLY_AND_SKILL_FAIL_CLOSED_PARTIAL` | D1～D7a 的app-local routes、21个producer、Host五合同lineage、Memory/VTS冷读与0103边界继续成立。D7b接通同app durable roots + canonical ledger有界lineage的Life Body冷聚合，并将Direct Skill的resource/episodic/semantic实例冻结到typed execution ports；声明source-mode为`6/3/8/4-auth/0-unavailable`，失败root仍物化typed unavailable，风险语义与幂等journal受测试守护，缺技能后端在authority后失败关闭。D7c将Obs application/job assembly移入app层，composition→app反向依赖违规归零且无白名单。底层Host source、Chat/Delegate和Acceptance Effect authority仍process-global，约53个非锁可变服务引用、完整Host/Worker composition、真实Edge和全局Service Locator删除仍未完成；NE-01不得封板 |
| Persona 演化 authority | `CENTRAL_FENCE_WIRED` | `PersonaEngine` 已中央拒绝非迁移写入；stage/milestone、interaction 与 emergent 只返回候选，不改变 trait 或消费 one-time/history；NE-06 proposal/Review/版本激活尚未实现 |
| 长期演化能力 | `PARTIAL_OR_MISSING` | World/Scene/Identity 等有资产，但 Evolution Lab、Skill Compiler、Knowledge OS 等未形成生产闭环 |

因此：历史 Core/GR 回执可以证明对应资产存在，不能证明 `Full Architecture DONE`。

## 2. 运行拓扑

| 运行面 | 入口 | 职责 |
|---|---|---|
| Host Main | `host_brain/__main__.py` → `host_brain.app.asgi:app` | 对话、Governor、Task/Agent Runtime、Life、Memory、Pentest、业务 API |
| Observability | `host_brain/obs_main.py` → `host_brain.app.obs_fastapi_app:app` | 只读投影、运维命令 BFF、Monitor UI 后端 |
| Monitor UI | `host_brain/app/monitor_ui/` | Operator 视图与受治理命令入口 |
| Edge RPC | `edge_client/__main__.py` | ASR/TTS、Avatar、Desktop、设备 actuator |
| Unified Gateway | `edge_client/gateway/bootstrap.py` | 平台 webhook、Host 输入回流、回复投递 |
| Desktop Pet | `desktop_pet/main.cjs` | Electron + Live2D 本地呈现 |

共享跨进程合同位于 `natsume_contracts/`。该包被 Host 与 Edge 直接使用，是版本化 API 边界，不是历史兼容目录。

## 3. Host canonical 请求主链

```text
Chat / Host Input / Gateway
  → Secure Ingress
  → Governor decision + grant
  → HostCommand + WorkOrderV3
  → direct graph 或 approved-plan graph
  → HostCommandRepository + DurableTaskStore admission
  → GovernedTaskCoordinator
  → node-bound grant + worker
  → Verification
  → Acceptance
  → Acceptance Effect intents
  → durable dispatcher / receipt / read-only projection
```

关键实现：

- `host_brain/agent_runtime/cortex_runtime.py`
- `host_brain/app/governed_task_adapter.py`
- `host_brain/app/governed_task_authority.py`
- `host_brain/orchestration/governed_command.py`
- `host_brain/secure_ingress/graph_cancellation_authority.py`
- `host_brain/composition/runtime_group.py`
- `host_brain/observability/command_ports.py`
- `host_brain/app/observability_runtime/domain_command_ports.py`
- `host_brain/app/monitor_ui/src/features/execution/taskRuntimeApi.js`
- `host_brain/task_runtime/coordinator.py`
- `host_brain/task_runtime/coordinator_records.py`
- `host_brain/task_runtime/coordinator_receipts.py`
- [`host_brain/task_runtime/node_execution_runtime.py`](../../../host_brain/task_runtime/node_execution_runtime.py)
- [`host_brain/task_runtime/node_execution_projection.py`](../../../host_brain/task_runtime/node_execution_projection.py)
- [`host_brain/task_runtime/node_execution_records.py`](../../../host_brain/task_runtime/node_execution_records.py)
- [`host_brain/task_runtime/parallel_success_runtime.py`](../../../host_brain/task_runtime/parallel_success_runtime.py)
- [`host_brain/task_runtime/parallel_success_records.py`](../../../host_brain/task_runtime/parallel_success_records.py)
- `host_brain/task_runtime/parallel_settlement_projection.py`
- `host_brain/task_runtime/parallel_settlement_runtime.py`
- `host_brain/task_runtime/graph_cancellation_records.py`
- `host_brain/task_runtime/graph_cancellation_runtime.py`
- `host_brain/task_runtime/graph_cancellation_projection.py`
- `host_brain/task_runtime/runtime_outbox.py`
- `host_brain/task_runtime/local_graph_execution_registry.py`
- `host_brain/task_runtime/acceptance_effects.py`
- `host_brain/task_runtime/acceptance_effect_dispatcher.py`
- `host_brain/persistence/task_repository.py`
- `host_brain/persistence/task_outbox_delivery_store.py`
- `host_brain/persistence/task_dag_state_store.py`
- `host_brain/persistence/task_ready_claim_store.py`
- `host_brain/persistence/task_acceptance_effect_staging.py`
- `host_brain/persistence/task_acceptance_effect_delivery.py`
- `host_brain/persistence/task_decision_integrity.py`
- `host_brain/persistence/task_graph_cancellation_request.py`
- `host_brain/persistence/task_graph_cancellation_finalization.py`
- `host_brain/persistence/task_parallel_success_store.py`
- `host_brain/persistence/task_parallel_settlement_store.py`
- `host_brain/persistence/task_parallel_state.py`
- `host_brain/persistence/task_record_codec.py`
- `host_brain/persistence/task_retry_mutation_store.py`
- `host_brain/persistence/task_retry_replay_store.py`
- `host_brain/persistence/task_atomic_failure_store.py`
- `host_brain/persistence/task_store_types.py`

### 3.1 已接线范围

`chat`、`skill`、`delegate`、`plan` 被 Cortex 识别为 canonical governed route。它们不能回退到 Executive：`host_brain/executive_runtime/runtime.py` 对这四种 route 直接拒绝。

### 3.2 Read route 与退役边界

`task_progress` 直接读取 `DurableTaskStatusProjection`，`recent_turn` 直接读取 durable conversation window；两者只产生已持久化的可见对话结果，不创建 Task、Attempt 或第二套执行 authority。未知 legacy route 在 Cortex 以 `RUNTIME_ROUTE_UNSUPPORTED` fail closed。

Executive 已从生产 Cortex application ports 删除。旧 `_execute_explicit_skill_dag`、进程内任务 registry、factory、accessor 和相关生产写入均已删除；`executive_runtime/runtime.py` 只保留拒绝边界与孤立 helper，不再是生产路由器。

Scheduler 与 Internal Agent 均已有真实任务证据。Internal 会先剥离调用方伪造的 lineage/approval authority，把 ingress v2 编译为 canonical `WorkOrderV3`；只有 `delegate:agent_runtime` child grant 完整验证后，才向既有 executor 投影兼容对象。前置 Review 不执行 handler，执行结果要求 Review 时进入 `WAITING_ACCEPTANCE_REVIEW`，不会误 ACCEPT。

## 4. TaskGraph、预算、Review 与副作用

### 4.1 Durable TaskGraph

当前 coordinator 能持久化多节点图、阻断未知依赖和环、推进 `BLOCKED → READY`、创建 Attempt/Lease/Reservation，并在 Verification 与 Acceptance 后完成节点。`TaskGraph.result_node_id` 与 `completion_policy` 是兼容 v1 可选字段。缺省/`LEGACY_NODE_TERMINAL_V1` 保留旧语义：单 sink 可显式 result，多 sink 必须保持无 result；`ATOMIC_GRAPH_SUCCESS_V1` 必须显式绑定一个当前 revision sink。admission、durable identity 和 planner semantic/shadow comparison 都校验二者，不能从完成顺序推断结果 authority。`PlanningGraphCompiler` 默认 legacy，只有调用方显式请求或失败重规划继承前一 revision 才生成 atomic policy。

B0 的 `task_runtime/dag_scheduler.py` 是无副作用调度决策：它稳定选择 READY、依赖满足、retry due/waiting 节点，严格 fan-in，并受 concurrency/occupied slots 限制。B1 的 store/coordinator 子集已让 Coordinator 从 durable snapshot 消费该决策，并由 TaskStore 在 `BEGIN IMMEDIATE` 中原子提交 Attempt、Node、Lease、初始 Heartbeat 和 Outbox；claim 响应丢失可在 authority/TTL/outbox 均未漂移时精确返回原 Lease。

迁移 0089 只在 Node 持久化 `retry_not_before_ms`；attempt 次数始终由 Attempt journal 的连续 `attempt_no` 派生，避免第二真相。`DurableNodeExecutionRuntime` 只在 worker resolution/preparation 抛出显式 `RetryableWorkerPreparationError` 且尚无 operation reservation 时调用 `schedule_retry`；retry_count BudgetUsage、Budget、Attempt、Node、Lease 与 Outbox 在同一 `BEGIN IMMEDIATE` 内提交。预算允许时节点进入 `RETRY_PENDING` 并在到期后创建 attempt 2；retry_count 超限时进入 pre-reservation terminal failure 收口。reservation 创建后的异常从不进入自动 retry。

上述 safe-retry 是已接通并由真实 Coordinator fault injection 验证的 contract path。若 `schedule_retry` 已提交但响应丢失，同一确定性调用会严格校验 Attempt、Node、Lease、Graph、Budget、BudgetUsage 与关联 Outbox 后返回原决定，不重复扣减 retry budget；任一状态漂移都会 fail closed。resolver 的 `CancelledError` 不会被普通失败分支吞掉。当前三个生产 resolver（Host registry、Scheduler、Internal）都不会抛 `RetryableWorkerPreparationError`，因此不能写成现实瞬态故障已经自动分类。

普通或重试耗尽的 pre-reservation resolver failure 由 `DurableNodeLifecycleRuntime` 编排并调用同一个 `DurableTaskStore.fail_running_attempt_bundle`：在同一个 `BEGIN IMMEDIATE` 中把 Attempt、Node、Graph、Lease 分别落为 `FAILED`、`FAILED`、`FAILED/BLOCKED`、`RELEASED`，并生成四条同时绑定 attempt 与错误证据的 Outbox。Runtime 对普通响应异常只以同一 callable 和同一 arguments 精确回放一次，第二次失败转 durable conflict，且没有 sequential fallback；旧 fence、版本/证据/Outbox 漂移或该 attempt 已存在任意 operation reservation 时一律拒绝。TaskStore 仍是唯一 mutation/UoW authority。Budget finalize 仍发生在 bundle 之外，因此它不是 Attempt/Node/Graph/Lease/Budget 五者全原子的终态事务。

所有 Task mutation 共用的 Outbox 落库与 response-loss exact replay 校验实现归属 `TaskOutboxDeliveryStoreMixin`；BudgetUsage contention 与 Operation checkpoint read 分别归属 focused mixin。它们都只借用主 Store 的同一事务/连接；`DurableTaskStore` 仍是唯一 composition、connection 与 UoW authority。主 repository 当前 4,358 行，仍低于 4,600 行上限。

operation reservation 建立后，`DurableNodeExecutionRuntime` 会创建 `DurableExecutionHeartbeat`。后台循环严格先续 Lease、再续 Reservation，Worker 的每个流事件和最终结果都经过 checkpoint；停止并 join 心跳后才允许使用最新 Reservation version 执行 COMMIT。续租失败、版本漂移、worker 自发取消或心跳生命周期异常都会产生 `RECOVERY_REQUIRED`，不会提交未经证明的结果。

当前自动 takeover 是严格窄窗，不是上述任意 `RECOVERY_REQUIRED` 都能续跑。`TaskOperationCheckpointStoreMixin` 必须一次读出 exact `COMMITTED` Reservation、唯一 `OPERATION_COMMITTED` Outbox、原 Attempt、历史 Lease/owner/fence 与 hash receipt；`DurableOperationRecoveryRuntime` 再验证 canonical WorkerProjection 的 command/request/graph/node/attempt/route/target/grant，以及唯一 ACTIVE graph Budget。Worker usage 被明确分为 `EXACT_ACCEPTED / MISSING / CONFLICT`：EXACT 只读核对；MISSING 必须连同 stable usage id 完全无 worker-kind 冲突，先以新 owner/fence 接管同一 Attempt，再用 `reservation.updated_ts_ms - attempt.started_ts_ms` 的正持久时间上界幂等结算；CONFLICT、拒绝/漂移行、零/非法时间全部阻断。只支持 single-node canonical COMMITTED checkpoint，worker 不重跑，并由 `CommittedCheckpointContinuation` 继续 Verification/Acceptance；并发、settlement 响应丢失、超限和再次重入均已验证。pre-operation、multi-node/batch、非 canonical projection 或不确定副作用仍返回 recovery required。因此准确定级是 `SINGLE_NODE_CANONICAL_COMMITTED_CHECKPOINT_PROVEN / GENERAL_RECOVERY_INCOMPLETE`。

`AcceptedGraphFinalizationRuntime` 被 normal Node、上述 continuation 与 outer DAG 共享。它关闭了另一个独立崩溃窗：Node 已 `ACCEPTED`、Graph 仍 `ACTIVE`、Effect 尚未落盘时，重启从 exact checkpoint 与 canonical WorkerProjection 重建 Effect，并调用既有 `transition_graph_and_stage_acceptance_effects` 原子提交 Graph+Effect。测试证明 worker 不重跑、Effect identity/digest/idempotency 不漂移；篡改 projection 时 Graph 保持未完成且 Effect 不产生。

0102 `task_runtime_node_artifact` 现在是 Node 结果权威，GR06 NODE Effect 也已并入 canonical `commit_node_acceptance()`。normal、COMMITTED checkpoint continuation 与 Review approve 在同一个 SQLite writer transaction 中冻结 exact NODE Effect 集（可为空）、写 immutable artifact、推进 Node `ACCEPTED` 并追加绑定 exact Attempt 的 Node Outbox；任何 effect set、artifact、CAS、judgement 或 Outbox 漂移整笔回滚，响应丢失只能逐项重验后精确 replay。Effect dispatcher 在 0102 存在时还会命中并重新验证同 graph/revision/node/Attempt 的 artifact，旧 `SUCCEEDED Attempt` 不能借用另一个 Attempt 的 Acceptance。compiler 生成的 dependency result refs 不能由 planner 伪造，READY/执行/恢复前只解析同 revision 的全部前驱 artifact，`FrozenNodeInput` 再校验 ref 集合与 payload digest。新 HostCommand/Task admission 拒绝空 `output_contract`；`ProcessTaskRuntimeContainer` 在 recovery 前校验 0102 asset/schema checksum，并把非终态 active legacy 空合同/缺 artifact，以及缺 exact artifact 的可投递 terminal legacy Effect 隔离为 reconciliation required。

应用 Store 的 public `transition_node(ACCEPTED)` 在 0102 存在时已要求有效 artifact，生产三条 acceptance 路径不再拥有第二 writer。但这仍不是数据库物理全封闭：Acceptance 前 standalone Effect pre-stage 仍是兼容入口，低层 `create_graph()` 仍服务 legacy/internal 状态构造，SQL 也没有跨 Effect/artifact/Node/Outbox 的强制 trigger。准确定级是 `UNIFIED_APPLICATION_STORE_UOW_WIRED / PRESTAGE_COMPATIBILITY_PRESENT / SQL_DIRECT_BYPASS_PRESENT`，不能据此宣称一般 recovery 或 production fan-out 完成。

Coordinator 现在有两个独立实验能力：`atomic_graph_success_enabled` 决定进程是否能执行 frozen atomic policy，`bounded_fanout_enabled` 决定 scheduler 多节点批次是否实际并发；二者构造默认值均为 `false`，Host、Scheduler、Internal 三处生产 composition 均未传入启用值。Application 在 `repository.admit` 前检查 atomic capability、node-grant service 与 parent grant，任一缺失即拒绝且不写 durable admission。Coordinator 再要求 `node_grant_refs` 精确覆盖整个 admitted Graph。atomic 图即使是单节点或顺序执行也使用 deferred child + outer terminal authority；terminal-leaf 实际并行还必须另开 bounded fan-out。progress buffer/控制槽的反压语义保持 B4 合同。

Coordinator 在任何 Budget activation 或 worker resolution 前调用 `assert_parallel_settlement_decision_ready()`；`0090` 缺表/缺列会以 schema-not-ready fail-fast。B5 成功开关实际生效时还会先调用 `assert_parallel_success_decision_ready()`，要求迁移 0091 完整可用。当前 338 行的 Store-only `ParallelSettlementProjection` 读取 scheduler snapshot、Node 与 Attempt，负责 active re-entry、rejected Budget outcome 和 outcome snapshot 的严格校验；当前 657 行的 `ParallelSettlementRuntime` 共享原 Store、BudgetRuntime 与 RuntimeOutbox，负责 outcome resolution、ownership/CAS conflict 判定和 settlement orchestration。对于 `ACTIVE` 多节点重入，若 durable snapshot 仍含 live Node、active Attempt 或 `ACTIVE` Lease，入口返回 `GOVERNED_EXECUTION_IN_PROGRESS`，不会 resolve 新 worker、改变 Attempt/Lease，也不会抢现有 owner 的 execution authority。结算窗口遇到 ownership fence，或 CAS 后重读发现 active Node/Attempt/Lease，也返回 `IN_PROGRESS`；纯 CAS 且没有 active ownership 事实仍转为 `GOVERNED_DURABLE_STATE_CONFLICT`。

并行 child 使用 deferred shared-terminal 模式：它仍独占自己的 Attempt、Node、Lease、Reservation，并可记录 Budget usage 或把预算标成 `EXCEEDED`，但不得终结共享 Graph，也不得把 Budget 置为 `RELEASED/CONSUMED`。因此普通的 node-local 非接受不会 fail-fast cancel sibling；outer 会等待所有已启动 sibling 到达 durable node-local terminal，再按固定优先级解释整批结果。所有 Coordinator 与 cancellation runtime 发布先经过每个 Coordinator 唯一的 `RuntimeOutbox` asyncio lock；同进程 Host/Internal/Scheduler 共享的 `RuntimeOutboxPublisher` 又以实例级 `threading.Lock` 串行化 claim/append/complete drain。跨线程合同用两个 worker 证明共享 publisher 的 peak active drain 为 1；跨进程排他、过期接管和 stale-owner 拒绝由 0093 durable claim/fence 承担，而不是该锁。

live 与 crash/re-entry 不再从瞬时 receipt 拼不同决策：ParallelSettlementProjection/Runtime 从 durable Node、Attempt、Reservation、BudgetUsage 重建完全相同的 canonical `Natsume.task_runtime.parallel_settlement.v2` payload/ref，最终仍由 DurableTaskStore 在唯一 UoW 中提交 Graph、可选 Budget、Outbox 和 decision row；response-loss 只 exact replay。为保证跨批次重入 digest 稳定，outcomes 从整个 admitted Graph 的 durable terminal Nodes 和每节点 latest stable Attempt 全量重建，不只当前 claimable batch；当前 batch 只决定优先级与 selected。`RETRY_PENDING` 是合法的非 selected durable outcome，也可由重入恢复。BUDGET selected `BUDGET_BLOCKED` 绑定 rejected usage；共享超额后的被动 blocked sibling 不要求本地 usage。每次读取 decision row 都验证 digest、精确 Graph/可选 Budget Outbox 与 Attempt lineage，BUDGET 再验证 usage；terminal replay 另校验 Graph target/version，并要求 selected outcome 的 Attempt 仍为该节点 current latest。结构拆分没有把 0091 success 或 0092 cancellation authority并入 0090 Runtime。

Budget usage 高竞争已从主 repository 拆入 732 行 `TaskBudgetUsageStoreMixin` 并关闭唯一性缺口。一个 winning rejected usage 执行唯一 `ACTIVE → EXCEEDED`、推进版本并拥有唯一 Outbox；其他并发 sibling只追加 `ALREADY_EXCEEDED`并引用 winner。0090 Budget decision 的写入、读取与 replay 复用同一 terminal authority 校验；public reader 以显式只读事务冻结单一 SQLite snapshot，逐一复核 Reservation 精确版本、sole winner Outbox、Attempt/Graph lineage和 passive sibling，避免写入正确但读侧接受漂移。`DurableBudgetRuntime._charge` 不再固定最多 4 次 CAS，而以 `max(configured retries, policy concurrency + 1)`覆盖 admitted writer wave；concurrency=8/连续 8 次 conflict 后第 9 次成功已有单测。8/16/32 线程、跨进程、interleave、restart/replay 与 drift/corruption tests已通过；这不代表 concurrency 维度已经运行时计量，也不补齐 `DEGRADE/REVIEW`。

合同/集成测试覆盖：live 首次构造与 crash/re-entry 重构得到同一 payload/ref、提交响应丢失 exact replay、较早批次 terminal Node 被纳入后续 decision、`RETRY_PENDING` sibling 作为非 selected outcome、Budget selected usage 与被动 blocked sibling 分离、newer selected Attempt replay rejection，以及 decision row/marker corruption fail-closed。

B5 terminal success 不复用 0090 的 failure decision。每个 Accepted 节点必须拥有 latest `SUCCEEDED` Attempt、已 `COMMITTED` 的 operation Reservation receipt，以及由 `WorkerProjectionStore` 冻结的 `Natsume.task_runtime.worker_projection.v1` 投影。读取后重新 canonicalize 并重算 digest，精确校验 command/request、graph/revision、node/attempt、route/target、receipt、node grant 与 `APPROVE`；仅凭一条看似合法的 digest 字符串不能通过。只有 `result_node_id` 对应投影会编译整图 effects 并成为公开 projection；其他 Accepted 叶只提供 outcome/evidence。Skill 同样不能以瞬时 executor 返回值争夺结果 authority。

`ParallelSuccessRuntime` 负责 0091 整图成功的 read/validation/orchestration，但 `DurableTaskStore.complete_parallel_graph_success()` 仍是 B5 唯一成功 mutation authority。Runtime 只持同一 Store、共享 RuntimeOutbox/BudgetRuntime、projection reader 与共享 NodeExecutionProjection；纯 `parallel_success_records.py` 只拥有 route-authority 投影。Store 在同一个 `BEGIN IMMEDIATE` 中将 Graph `ACTIVE → COMPLETED`、共享 Budget `ACTIVE → CONSUMED`，写入不同的 Graph/Budget Outbox、全部 `GRAPH` scope effect intents，以及迁移 0091 的 `Natsume.task_runtime.parallel_success.v1` decision row。decision 使用 `tasksuccess://sha256/...` 引用并绑定 completion policy、所有 Node status/version、Accepted outcome、显式结果 Attempt/receipt、projection digest、effect identity 和两条 Outbox；任何晚故障整体回滚。已完成图的 replay 只读取并复核 0091，不重新编译 effects；若第一次提交和第二次 exact replay 的响应都丢失，ParallelSuccessRuntime 重读 Graph terminal authority 后进入同一 completed replay 分支。

对抗验证包含单节点 atomic、串行 atomic、多 terminal roots 反向完成顺序、flag-off 与缺 node-grant service 在 admission 前拒绝、projection narrative/command/grant 篡改、restart after accepted、completed restart 不重编 effects、一次和连续两次 Store 响应丢失。它证明 shadow contract 自洽，不等于生产 canary。

0095 已封闭历史 marker 的 revision 身份边界。`task_runtime_outbox.graph_revision` 对 `TASK_GRAPH` 必须是指向现存 Graph 的正整数；0098 只为 `GRAPH_CANCELLATION_EFFECT_RECONCILIATION` 增加同样的正整数 revision，并要求 case ID、revisioned payload ref 与 immutable inventory 物理一致；其他 aggregate 必须为 `NULL`。列的物理形状固定为 `INTEGER / nullable / no default / non-PK`。迁移接受 0090/0091/0092 decision、0092 intent、Attempt lineage、graph ID 唯一 revision，以及 HostCommand 的两种 exact authority：immutable `command_id` evidence 绑定正常 multi-revision CREATED；Migration Runner 注册 deterministic `natsume_sha256_hex`，以 revision-bound command ID 重算升级前五段 governed digest，同时匹配 legacy `outbox_id + idempotency_key`，绑定 ACTIVE 等非 attempt transition。升级本身不改变 Outbox ID/key，`graph_revision` 只是新增精确维度，因此 response-loss replay 不断裂。多个来源冲突或自定义 producer 历史行仍无法唯一归属时，整笔迁移事务失败而不猜测。0090 `taskbatch` 的缺 decision row/错绑扫描现在按 `graph_id + graph_revision` 完整覆盖多 revision 历史，所有 decision getter 也复核 Graph Outbox 的 exact revision。

B5C 在其上新增迁移 0092 和两阶段 Graph cancellation。`task_graph.cancel` 已接入 Operator Command Gateway、同源 BFF 与 Task Runtime UI，payload 精确绑定 `graph_id + graph_revision + reason_code`，权限固定为 `obs:write + task-runtime:cancel`。Gateway 认证后的 server-owned authority 携带 durable audit/claim、request/principal/tenant、认证 body digest、action/scopes；同一 `SecureIngressGraphCancellationAuthorityVerifier` 注入 Host/Internal/Scheduler，重读 committed 且未写入 terminal `claim_result_ref` 的 authority 并核对 canonical body 后才以 `audit_ref` 签发 request identity，settled authority 不可复用为 bearer。Application 不接受调用方自报 request identity/source。Store phase 1 先持久化 intent，将 Graph 置为 `BLOCKED`，再取消/围栏 Node、Attempt、Lease、Review/token、operation 与未授权 effect；`GraphCancellationRuntime` 只有在该提交后才通过共享 registry cancel/join 本进程 child/heartbeat。异常确定性只有经 cancellation handler 翻译为 OBS 封闭端口异常后才可进入 NOT_EXECUTED，任意依赖异常保持 uncertain。execute re-entry 由 Coordinator 薄委托该 Runtime，从 durable intent 继续 advance/finalize；只读 `GraphCancellationProjection` 只持 Store，本地 task registry 不构成 authority。0096 的 Review continuation 是另一条独立协调链，不取得 0092 cancellation authority。

`GovernedRuntimeGroup` 先按 exact revision 读取 immutable HostCommand surface，再把命令路由回 Host、Internal 或 Scheduler owner。若下游抛错，它重读 exact revision 的 0092 intent；只有 request、audit identity、OPERATOR、reason、graph/revision 全匹配时才报告可能已有副作用，无 intent 则明确按未执行拒绝，读取失败或畸形事实保守标记不确定。Task Runtime UI 随后刷新 durable detail，不把命令返回直接显示成 `CANCELLED`。

final cancellation 只有在 active ownership 与 operation/effect/review 不确定性全部 reconciliation 后才以一个 UoW 提交 Graph `CANCELLED`、Budget 释放/取消、Outbox 和 canonical decision。operation reconciliation proof 在动态 evidence 构造和终态 getter 中都会重验结构、digest、key 与签名；同 revision 的 Review create/approve/resume 在事务内被 0092 intent fence。`ACCEPTED` Node + `SUCCEEDED` Attempt 已确认的 NODE effect 保留 delivery authority；其他 NODE effect 与未完成 Graph 的 GRAPH effect 被 fence。0091 success 与 0092 cancellation first-writer-wins；请求响应丢失只允许四字段 authority 完全一致的 exact replay，publisher 故障不会阻止 durable intent 后的本地 cancel/join。基础设施 shutdown/disconnect cancellation 不会自动写成业务取消。

Acceptance Effect 的终态不是对称证据。`COMMITTED` 必须带 durable receipt，可自动把 `RECONCILIATION_REQUIRED` 收口为 `COMMITTED_EVIDENCE`；一般 handler/transport `FAILED` 只说明没有取得成功 receipt，外部副作用仍可能已发生。因此取消请求观察到既有 `FAILED`，或取消后 in-flight handler 落为 `FAILED`，都继续等待显式对账，不能按 reason code 猜测。0098 将旧 `FAILED_EVIDENCE`、legacy retry-PENDING+CANCELLED fence 与 finalized 历史异常冻结成 immutable case inventory；PENDING proof 只能追加新 decision evidence，FINALIZED correction 只作为 trusted read overlay。`PENDING` 的完整 durable tuple仍区分 virgin 与 retry epoch：只有 attempt/fence/version 全 0 且 reason/error 均空才可逻辑取消，混合 tuple先由 codec 判为 corruption。Production claim 仅授予 `SUCCEEDED + ACCEPTED` NODE 或 `COMPLETED` GRAPH；前者在 cancellation 中保留 authority并被排除，后者已终态，所以 fenceable retry PENDING 只表示 legacy/imported/cross-table corruption。请求路径对它保守阻断；旧 decision/fence/evidence 始终不可变。

Operation 与 Effect 签名对账当前均为 `WIRED_DEFAULT_OFF / CANARY_NOT_RUN`：受限 service 由 ProcessTaskRuntimeContainer 构造，ASGI 注册进 Operator Runtime state，scoped OBS command 绑定认证 actor，TaskStore 只注入 Ed25519 公钥验证 authority；两个独立 feature flag 默认关闭，开启 cancellation 时必须同时开启，启动还要求 Secure Ingress `ENFORCE` 和可执行 Gateway。0098 Effect proof exact 绑定 case/intent/effect/fence/Attempt/resolution/actor/Outbox/revision；部署密钥/凭据、完整 key lifecycle 和 canary 未完成前 cancellation 继续 `PRODUCTION_OFF`。

0099 已将历史 terminal Graph/Budget 半状态改为默认关闭的 append-only case/proof/supersession reconciliation，而不是未实现的笼统缺口。Readiness 严格比较 3 table/4 index/10 trigger 的 canonical `sqlite_master` SQL，并由 Python 复核 case/subject/proof。未决 current case 在整个 Graph/revision 上围栏普通 Graph/cancellation/Budget transition/consume/usage/retry writer，只有 0099 proof UoW 可 bypass。Classifier 要求唯一 0090 decision，复用完整 0091/0092 validator，并校验 exact failure Outbox payload refs。System auto 用 immutable case 时间生成稳定 proof identity 并跨进程收敛；`TERMINAL_HEALTHY_CLOSURE` 只在历史未决 case 且 current classifier healthy 时生成，固定为 signed `NONE` 且零 Graph/Budget/Outbox 写入。它仍只是窄 reconciliation，不是 general recovery。

Dedicated `obs_fastapi_app` 的 Effect GET 不构造 `DurableTaskStore`，而是在 `HOST_NLE_OPERATOR_VIEWS_ENABLED=true` 时用 ledger canonical DB path 构造 `EffectReconciliationQueryRepository`。该 repository 仅继承 read/validation mixin，连接强制 SQLite `mode=ro + query_only=ON`，没有 mutation/UoW surface；false 时不触发 0098 readiness。Operator Query Gateway 对 candidates/cases/challenge 精确要求 `obs:task-runtime:read`，`obs:operator:read` 被拒绝；cases/proof 是最小投影，exact subject 通过 canonical-JSON base64url + SHA-256 `signing_material` 在 privacy serializer 后保持可签名。生产集成证据为 `tests/integration/test_ne01_obs_effect_reconciliation_read.py`。

0094 由 `ProcessTaskRuntimeContainer` 拥有 composition worker 与一个 process-shared `LocalGraphExecutionRegistry`。Host、Internal、Scheduler Coordinator 和 worker 的 `GraphCancellationRuntime` 共用该 registry 确认本进程 quiescence，但 durable authority 仍只属于 0092 intent/decision。0094 readiness/codec 锁定 exact frozen columns，额外 `status` 等业务字段一律拒绝；`intent_ref` 物理 identity 必须是 `TEXT NOT NULL PRIMARY KEY`，不允许 SQLite rowid PK NULL/复合 FK 豁免留下幽灵行。worker 以 recovery-scoped owner 扫描并 claim/heartbeat/defer/takeover；这四类变更在取得 `BEGIN IMMEDIATE` 写锁后才采样时钟。它按 exact Graph revision 重建 HostCommand admission 并重入现有 Runtime；若未显式传入 `local_pending`，Runtime 必须先对共享 registry 执行 `cancel_and_join`，本地 pending 非空时不得 advance/finalize，worker 只能 durable defer。仅在 0092 intent `FINALIZED` 且 decision 存在后才 complete/清理 0094 协调行。

该路径仍不能默认启用。当前定级是 `PARTIAL_B5C_DURABLE_CANCELLATION_SHADOW / PRODUCTION_OFF`：Cancellation、Operation/Effect/terminal reconciliation 的生产入口均已接线但默认关闭，真实 canary 未运行。Budget contention 与 0099 窄历史终态机制已证明，但目标部署 inventory/remediation、general/batch/pre-budget recovery、部署密钥/凭据和完整 key lifecycle 未完成。

执行层 focused split 未新增 policy 白名单；全仓 mixed-file Gate 仍因 `governed_task_adapter.py`、`host_command_repository.py`、`review_continuation_store.py`、`scheduler/governed_runtime.py` 四个既存未分类 >1,500 行文件为 RED，`asgi.py` 保持在门禁线内。`DurableDagSchedulingRuntime` 当前 1,338 行，`coordinator.py` 268 行；238 行 `AcceptedGraphFinalizationRuntime` 统一 normal/restart/continuation completion，Operation recovery 与 checkpoint continuation 均保持低于 600 行的窄职责。Dag 当前矩阵为 Store 9、Budget 9、Lifecycle 17、AcceptedFinalization 1、NodeProjection 13、NodeExecution 7、Settlement 4、Success 1、Review 4、Cancellation 1、Outbox 1 build/6 publish。它仍是临时 outer orchestrator，状态保持 `PARTIAL_B5C_DURABLE_CANCELLATION_SHADOW / PRODUCTION_OFF`。

Schema readiness 在 Budget 写入前检查，retry schedule/promotion 仍同事务校验 ACTIVE graph/graph-level Budget，backoff 节点可原子取消。若 0089 的物理事务已提交但 migration receipt 丢失，runner 只有在冻结 checksum、列约束与完整索引形状全部成立时才补记 receipt，否则 fail closed。

`ProcessTaskRuntimeContainer` 的生产启动顺序现为 migration → TaskStore 0093/0094/0095/0098/0099 与 Review 0096 readiness → `task_store.recover_startup` → 0099 bounded inventory → 可选 default-off exact auto → publisher 构造 → recovery outbox publish → Outbox 后台 worker → surface runtime activation → cancellation 与 Review continuation workers；恢复抛错会在开放路由前终止构造。ASGI 通过 `execution_owner_id()` 把配置逻辑 owner 与当前 container 的 `recovery_id` 摘要绑定，得到每次构造/启动唯一且不泄漏原 recovery id 的 execution owner。Coordinator 已激活但 worker start 失败时，activation 会尝试反向 stop/join worker 与 close Coordinator；只有全部成功才 unbind，回滚失败保留 bound handle 供 `aclose` 重试，Runtime Group 同样只移除关闭成功的 child。shutdown 给 worker 有界 grace，超时后 cancel 并有界 join；如协程抑制取消，容器有界 fail closed 且不关闭 Task/Command Store，供下次重试。Container 构造 Operation/Effect/terminal reconciliation service，并按显式公钥环向共享 TaskStore 注入 verification-only verifier；ASGI 仅在各自独立开关启用时注册 service，cancellation 开启强制要求 Operation/Effect gate 并通过 0099 production readiness。Outbox、cancellation coordination 与 Review continuation 都支持过期接管；未部署 proof 的 Effect 仍会让 0094 durable defer。

任何 activation rollback 或 `aclose` quiescence 未完成都以 `TASK_RUNTIME_LIFECYCLE_RETRY_REQUIRED` 锁死后续 bind/activate，只保留 `aclose` 重试路径。Coordinator admission close、drain、`aclose` 任一失败都不关 Store；必须等 Coordinator 与 continuation worker 同时静默，才 drain Outbox 并关闭其余 infrastructure。

仍缺：

- B5C durable cancellation 仍是 production-off shadow；0092、两阶段 fence/final decision、共享生产 verifier、Operator Gateway/BFF/UI 与三 surface 路由已实现，但开关默认 OFF，部署凭据/真实 canary 未完成；
- 历史 `BLOCKED + failure Node` 或 `FAILED + ACTIVE/EXCEEDED Budget` 已进入 0099 exact/signed/quarantine 窄 reconciliation，但外部部署 inventory/remediation 仍未知；0096 已把 `WAITING_APPROVAL` 作为无 Attempt 的合法统一 outcome observation；
- 0095 已补齐 stable-ID Graph revision/payload、deterministic 历史回填、多 revision marker、source-fenced Runtime Event/Review/Operator exact read；仍需持续守护不可解析 producer 整事务失败、legacy payload/event replay、零重复投影、receipt-loss、trigger/index readiness 与多 revision 回归矩阵；
- 0094 scanner/worker 已跨进程 claim、heartbeat、defer、expired takeover 并重入既有 cancellation runtime；Operation/Effect 签名对账均为 `WIRED_DEFAULT_OFF / CANARY_NOT_RUN`，未部署 proof 的 Effect 仍会持续 defer；
- 0093 已以 durable claim/CAS 和可关闭后台 worker 收口跨进程 Outbox drain，仍需保持 expiry 自动 takeover、stale fence、同 aggregate 顺序、非空 backlog 不得误报 DRAINED 和 append/complete 响应丢失故障矩阵；
- 生产 resolver 尚未把现实瞬态准备故障映射到显式 marker；promotion 与 claim 也不是同一决策事务；
- Budget 高竞争唯一 winner/`ALREADY_EXCEEDED` 已收口；仍缺 concurrency 维度计量与完整 `DEGRADE/REVIEW` 策略；
- 周期 Lease/Reservation heartbeat 已接，0096 已完成 Review continuation；single-node post-budget checkpoint 可安全 takeover，但 general/batch/pre-budget recovery 与一般 recoverable Attempt continuation仍缺。

### 4.2 Budget

`host_brain/task_runtime/budget_runtime.py` 和 TaskStore usage ledger 已在 Host 四路线、Scheduler 与 Internal Agent 执行预扣、计量和结算。真实计量覆盖时间、dispatch、显式 provider token/cost、ModelAttempt retry 与工具步数。

同一 Reservation 的超限竞争现在有唯一 durable authority：8/16/32 线程及跨进程压力下，只有一个 rejected usage 是 `EXCEEDED` winner并拥有版本 transition/Outbox；其他 sibling 是引用该 Outbox 的 `ALREADY_EXCEEDED` observation，不推进版本，也不参与 terminal winner selection。0090 决策读/replay 会在同一 SQLite snapshot 中重新验证完整 authority，而不是只相信 selected usage ID。

限制：

- provider 未返回的 token/cost 保持 `UNMEASURED`，不做伪精确估算；
- concurrency 字段没有运行时 charge；
- 超限后的 `DEGRADE`、`REVIEW` 尚未形成完整分支，当前核心路径主要阻断；
- Scheduler 与 Internal Agent 已进入该主链；其 program-wide Budget 策略仍受本节其余限制。

### 4.3 Review/Resume

`ReviewStore`、一次性 token、subject/version/checkpoint 绑定以及 PRE/POST review 状态均存在。Host、Internal、Scheduler coordinator 能进入 `WAITING_APPROVAL` 与 `WAITING_ACCEPTANCE_REVIEW`。迁移 0096 的纯协调表在 `CONSUMED/REJECTED/EXPIRED` task-bound Review mutation 的同一事务内入队；composition-owned worker 以 durable claim/heartbeat/defer/expired takeover 重新进入 immutable HostCommand 指定的原 surface，并在 Node 离开相应 waiting checkpoint 后才完成协调。Review status/token 仍只由 0055 ReviewStore 拥有，Task/Attempt 仍只由 TaskStore 拥有。

PRE Review 不创建伪 worker Attempt：0090 outcome 与公开 receipt 对 `WAITING_APPROVAL` 使用 `attempt_id=null`，其余状态仍要求真实 Attempt。POST continuation 复用原 `SUCCEEDED` Attempt，不重跑 worker。等待超过原 child-grant TTL 时，Application 只能凭 consumed Review 与当前 live continuation claim 派生短期 renewal；worker entry 再验证 owner、claim-token digest、expiry 与 fence，claim 漂移后旧 renewal 立即失效。Review route 与 Operator command 都经 process-owned `TaskReviewCommandService` 写同一 Store，wake 仅是事务提交后的延迟提示。

自动续跑只覆盖 canonical task-bound PRE/POST Review；非 Task subject 仍只返回 decision/token 结果，不会被 0096 worker误当作 TaskGraph 调度请求。Operator UI 必须按该范围展示，不能把局部 `automatic_resume=true` 解释成任意 Review 都会执行任务。

### 4.4 Conversation 与 Delivery

canonical route 在 graph Acceptance 时原子写入 effect intents，避免 admission 前先落 user turn。之后 user turn、Life、assistant/Memory projection 与 delivery 由 durable dispatcher 分别提交和重试。因此正确语义是“Acceptance 后最终一致”，不是“所有对话与投递在一个数据库事务中原子完成”。

Task Runtime Outbox 已由 0093 建立跨进程 durable claim/CAS，0095 则把 Graph aggregate 身份补成 exact revision。claim 持久化 owner、token、expiry 与单调递增 fencing token，过期后才允许 takeover；predecessor/head 查询按 `aggregate_type + aggregate_id + graph_revision` 隔离，同一 Graph revision 的旧 version 未完成时新 version 不得越过，不同 revision 可独立推进。`TASK_GRAPH` Outbox 必须绑定现存 `(graph_id, revision)`，非 Graph Outbox 不得携带 revision，insert/update trigger、readiness 和 codec 都 fail closed。所有生产 Graph producer 都显式写 revision；Runtime Event 使用 `graph_id@revision` 作为投影 aggregate key，Operator 按 exact revision 读取，不把多个 revision 混为一条事件流。`RuntimeOutboxPublisher` 逐条执行 claim、幂等 event append 与 fenced complete，append 失败则 fenced release 并落 backoff/error；composition 以当次 boot/construction 的 `recovery_id::outbox` 作为唯一 owner，并在 startup publish 后启动可关闭后台 worker。worker 根据 aggregate-head backlog 的最早 available/claim-expiry 时间和有界轮询自动续投；close 与前台 publish 临界区线性化，不允许 close 返回后继续触及 Store。显式 drain 只有 canonical total_pending=0 才返回 DRAINED，仍有 claim/backoff/blocked predecessor 时返回 DEFERRED。`list_pending_outbox` 保持完整 PENDING inventory，ready eligibility 只属于 claim/backlog API。Readiness 是每个 Store 实例的缓存 mode；正常热路径不重扫 schema/全表，只有显式诊断 force-revalidate 例外。Event/claim complete 已提交后的 backlog 读失败会脱敏降级，不会造假失败。0094 另行实现 pending cancellation scanner/coordination；它不复用 Outbox claim，也不取得 0092 业务 authority。

0095 的新 Graph `payload_ref` 是 `taskstore://event/TASK_GRAPH/{graph_id}@{revision}/{version}`；durable codec/replay 为历史行保留旧 ref 兼容，但新 insert/update trigger 一律拒绝 unrevisioned ref。0095 不改写 pre-0095 Runtime Event journal。新写入的 Graph Event 使用 `graph_id@revision`，exact-revision 查询同时通过 immutable `source_ref → task_runtime_outbox.graph_revision` 找回旧式 `aggregate_id=graph_id` 事件并按 cursor 去重排序。`uq_runtime_event_task_outbox_source` 是只覆盖 `source_kind=TASK_STORE_OUTBOX` 的 unique partial index，一条 Outbox 只能投影一条 journal；升级前 event append 已提交但 Outbox complete 未确认的重投，只有 existing event 与 current 或 checksum-reconstructable legacy event 全字段一致时才 exact replay，否则 fail closed。Operator 的 Review 查询也把 revision 传给 ReviewStore，由后者按 canonical、URL-escaped `task-node://<graph>/<revision>/...` subject prefix 精确过滤；Task/Review/Event 三路读模型因此不会跨 revision 混读。0095 readiness/receipt gate 同时冻结 payload trigger 与 source unique partial index。

`created_ts_ms=0` 不再被 `or now()` 改写；source timestamp 确为零时，current revisioned 与 legacy unrevisioned 两种可重建 candidate 都允许既有 journal 使用任意非负时间，因为两条历史 append 路径都可能用时钟替代零值，但除 timestamp 外的全部字段仍 exact。PlanningRecovery 的 evidence 也不再默认合成 Graph ref：`read_scheduler_snapshot` 的 `include_graph_outboxes` 默认 `false`，普通 scheduler/Operator 无额外扫描；failure recovery 显式设为 `true`，在同一只读事务读取 exact revision Outbox，并选取唯一 terminal status/version row 的真实 payload ref。仅旧版或替代 Store 未提供可选扩展字段时才合成 revisioned ref；生产 DurableTaskStore 提供 inventory 后，类型错误、缺行或歧义均返回 durable drift。0100 建立 append-only supersession，`HostCommandRepository.admit_replan()` 在同一写事务重验 same-root `FAILED` predecessor、terminal Outbox 和完整静止事实，随后原子 admission revision+1 并写 supersession。共享 Store/Review 围栏拒绝被替代 revision 的普通 mutation/continuation；response loss exact replay，fault injection 证明全回滚。当前仍只开放保守子集：`BLOCKED`、任意历史 Review continuation、任意 Acceptance Effect 全拒绝，自动 replan 和生产并发 canary未完成，状态为 `PARTIAL_DURABLE_SUPERSESSION_SAFE_SUBSET`。

## 5. PlanningSearch 的真实边界

### 5.1 公共内核

`host_brain/planning_search/` 已提供：

- `SearchRequest`、`SearchCandidate`、`SearchBudget`、`SearchResult`；
- Candidate Expansion、Cognitive Ranking、Virtual Transition、Reward、MCTS Condition ports；
- UCB 选择、rollout、backpropagation、预算和确定性 trace；
- 不包含工具执行、网络访问或状态写入端口。

公共算法本身成立，但“算法存在”不等于“生产规划能力已成立”。

### 5.2 Host Plan

`host_brain/orchestration/plan_intent_search.py` 从 Governor 已批准的 `planned_steps/steps` 构造候选。搜索只能选择完整、合法的拓扑顺序，不能新增节点、改变工具组合或比较不同方案。

它证明的是：

```text
approved steps → immutable graph → governed execution
```

它没有证明：

```text
objective → competing plans → risk/cost/value search → selected plan
```

### 5.3 Pentest

Pentest L1/L2 已接入公共 service，L3 使用纯虚拟 transition/reward，真实工具执行不在 rollout 内发生。但当前 L3 有四个限制：

1. 根节点前先用真实 `PreActionGate` 过滤候选；
2. virtual expander 只排列根节点已可执行的同一批候选；
3. 无法表达“A 获得 capability 后才解锁 B”的后继生成；
4. calibration ledger 是内存对象，不能跨 run 形成可靠证据。

shadow 对账的还是实际执行的 baseline 根动作，不是 L3 未执行替代路径的真实结果。因此 production influence 必须保持 `NOT_ACCEPTED`。

### 5.4 Code / Research

`tests/fixtures/planning_search_shadow/` 与 `docs/45_testing/test_reports/gr02a_planning_search_shadow_benchmark_v1.json` 明确声明 `SIMULATION_SHADOW`、`production_benefit_claimed=false`。当前证据没有真实仓库、真实 Research 来源、真实 model call 或真实 verifier。

## 6. 状态与 authority

| 状态域 | 当前权威写入/存储 | 备注 |
|---|---|---|
| Host command / Task / Attempt | `HostCommandRepository`、`DurableTaskStore` | canonical 四路线权威 |
| Scheduler | Scheduler repositories + Durable Task Runtime | occurrence 与 Task lineage 均持久化，真实 governed execution 已接通 |
| Review | `ReviewStore` + 0096 `ReviewContinuationStore` | token/decision 为唯一业务真相；task-bound continuation 自动、可 takeover，PRE 无伪 Attempt，POST 不重跑 worker |
| Runtime Event / Outbox | canonical SQLite journal/outbox | 关键事件与投影可恢复 |
| Model invocation | `ModelAttemptService` | 统一 route/attempt/trace 主干 |
| Capability execution | `CapabilityBroker` + execution journal | 签名和授权主干存在 |
| Digital Life | Life command/event/reducer/projection | 单核资产已建立 |
| Memory / Canon | typed repositories + governed apply | projection/cache 不应成为 writer |
| Edge physical effect | Edge actuator + durable receipt | 真实硬件完整验收仍受环境限制 |
| Operator read | `QueryProjectionStore` | Query Plane 以 projection 为主 |
| Task/recent read routes | Durable Task projection + conversation history | 只读，不创建平行任务状态 |

## 7. Composition 现状

`ProcessTaskRuntimeContainer` 是较好的显式 typed composition：它拥有 migration、TaskStore、ReviewStore、CommandRepository、EventStore 与 lifecycle。

但全局对象图尚未收敛：

- `host_brain/composition/process_service_container.py` 在模块级创建 `_PROCESS_SERVICE_CONTAINER`；
- 两轮静态调用图清理已删除 `retrieval_orchestrator`、`retrieval_pipeline`、`context_compressor`、`episodic_memory_graph`、`consolidation_trigger`、`skill_composer` 6 个生产零消费者槽位及 4 个死 accessor；
- 第二轮同时删除 `process_services` 上 7 个无仓内消费者的宽泛重导出和两处 `retrieval_pipeline` 测试 reset；
- container 当前共 60 个字段；排除 6 个 lock 与 `application_ports` 后仍有 53 个可变 service 引用；
- `process_services.py` 与 `process_runtime_services.py` 仍暴露数十个 `get_*()` accessor；
- 数十个生产 Python 文件仍直接或间接依赖该 locator；
- `host_brain.app` 仍承担部分真实 service factory 和 wiring。

D1 已先关闭路由级最直接的多 app 串扰：`HostRoutePorts` 是每个 FastAPI app 捕获的 frozen provider bundle，Canon/Tool/Bond 均由 `create_*_router(ports)` 构造；原 module setter/global provider 和 Tool fallback 到 `process_services` 已删除。同一 bundle 还经 Security composition 传入 Canon/Tool/Bond 的 OBS command adapter。双/三 Host app 交错请求、构造和关闭均只命中自己的 fake service。这个结论仅覆盖三类路由；OBS 的 Capability Forge、Scheduler/Review runtime lookup、Life/Tribe 与其余 process locator 仍在。

D2 把独立 Obs 的 `create_obs_app()`、`ObsApplicationPorts` 和 `ObsApplicationContainer` 变成显式应用级 scope。D3 最初注册19个 producer callback（2 core + 17），并先迁移17个生产端点。D4 将 BioOps 8、Life 3、Operator live 4、summary/runtime/resource 4，共19个 GET 的 `read_process_projection` 链改为必需显式 store 参数；36个已识别生产投影端点（35 HTTP + 1 WebSocket）、已识别stats查询与sessions/prompt response cache接入当前app的QueryProjectionStore/View或app-owned cache。D5 又修复此前漏扫的5个 `/api/ui/*` GET，以及Security runtime/assets/incidents、Skill Catalog stats、Life Evolution snapshot、Semantic Recall stats、Governance snapshot共7个实际挂载GET；双Obs app/双SQLite测试证明这些路由不交叉读取。6个 reconciliation 端点另从 app.state 使用显式 repository authority。

每个 `ObsApplicationContainer` 自有的 app-local Projection daemon 保持 single-flight、关闭 join 与 retry handles；模块级 `_default_daemon/register_projection_job` 兼容链和导入期注册已删除。当前21个producer的source-mode为 `DURABLE_READ=6 / STATIC_READ=3 / DURABLE_READ_PARTIAL=8 / AUTHENTICATED_HOST_READ=4 / TYPED_UNAVAILABLE=0`，21/0与认证声明都不是运行成功率。四个认证job分别物化Capability Forge、Semantic Recall、Governance与Organ/Tribe；Semantic/Governance各自读取独立versioned endpoint/type/cache/evidence和故障域。Life Body现在是partial producer：它只有在同app runtime/overview/insights/trace roots都健康且未过期、canonical ledger有界查询可用时写健康快照，否则写typed `SOURCE_UNAVAILABLE`。8个partial及认证来源的运行态失败可能性使health保持`DEGRADED`。

Host为每个lifespan生成一个五合同共享的`source_instance_id`，mode/capability/semantic/governance/runtime各自维护锁内单调`source_revision`。Obs通过通用Query Projection SQLite的`compare_and_upsert`单事务CAS持久当前lineage，并从immutable journal读取完整历史epoch；同epoch的revision、observed、evidence不得回滚，同revision payload不得漂移，任意已退役epoch重放都会拒绝，不受固定窗口限制。合同仅递归校验精确名为`timestamp`或以`*_ts_ms`、`*_at_ms`结尾的嵌套时间不晚于Host observed；Obs再以本地接收时钟允许最多5秒skew、拒绝超过120秒的正证据，并将freshness夹到本地接收时间。Host composition、Host route和Obs receiver各执行公共序列化与合同复验；合同不是字段白名单，Organ `key_metrics`另使用逐agent显式字段及数值/布尔allowlist。Transport固定五条路径、禁代理/重定向、限制1 MiB，并以一个monotonic总deadline覆盖DNS解析、连接、响应头和分块正文。真实loopback已覆盖五来源传输，但不代表部署长期健康。

Host source closure只在ASGI lifespan内预热、捕获并在finally清空；它底层仍读取`get_ledger/get_capability_* / get_semantic_recaller/require_process_tribe_runtime`等process authority，所以不是多Host全对象图隔离。Capability Forge durable proposal由ledger直接执行newest-per-proposal查询。缺token、Host或有效证据时相关文档保持`SOURCE_UNAVAILABLE`；mode history只在当前Host进程内，Organ只是allowlist后的瞬态证据而非Life authority。D7b的`LifeBodySourceReader`不读取这些Host closure，而是聚合同一app已物化的四个公开root和`LifeBodyQueryRepository`有界lineage；两app两SQLite、Host ledger关闭后的冷读、root missing/invalid/stale及content/private字段不落盘均有回归。Ledger-stats按公开时间列与durable行身份倒序读取最近5001行并物化最新5000行；`truncated`只标记更旧尾部，不再使高流量表永久503。D6 Memory/VTS冷读与0103边界继续有效；压缩率、蒸馏失败率和Edge物理回执覆盖仍明确不可测。callback仍不得调用`process_services`。

D7 静态扫描现有 `obs_routes*.py` 时，`process_services` 与 `_get_ledger` 的直接词法引用均为0；这个数字只描述该文件集合，不是全仓Service Locator计数。`capability_stats_projection`、`domain_state_projection`、`projection_ledger_view`、`remaining_query_projection`已删除全局兼容reader/factory/source fallback，生产保留的是显式`*_from_store` reader和由composition传入store/ledger/source的job。Host的`/v1/digital-life/world/snapshot`、`/health/canon_sync`、`/startup`、`/v1/memory/context/debug`、`/v1/memory/health`、`/v1/memory/events/recent`从Host lifespan持有的Query Projection Store读取；mode service经`HostRoutePorts`，Life action-preview的trusted context/token validator经`ObsRoutePorts`注入，缺端口失败关闭，preview仍不执行动作。没有挂载消费者的core ledger compatibility handlers已删除，不保留伪route port。Host app factory强制接收typed `ObsRoutePorts`；Process/Tribe全局ports不再捕获特定app closure。模块级daemon与导入期注册继续保持删除，app-local `ProjectionJobDaemon`保留。

D7b还把Direct Skill worker的resource manager、episodic store和semantic recaller从执行期全局getter改为Host lifespan捕获的实例快照；两个Governed runtime交错执行、关闭其中一个以及全局poison替换都不能改变另一个端口绑定。Durable node grant与child intent先验证，随后按Skill语义预检所需后端；缺依赖返回稳定失败，不进入低层executor，也不生成accepted或成功Skill receipt。Life Body把prompt injection/high-risk以及Gateway/capability风险映射为病理、免疫状态和诊断动作；根事实未变时沿用source identity，仅刷新freshness。D7c又把`obs_application.py`和`obs_projection_jobs.py`移入app层，NLE composition→app违规从85降为0且没有新增allowlist。这个证明只覆盖Direct Skill和Obs assembly：Chat/Delegate的dispatcher、`PROCESS_LIFE_PROVIDER`、Host source底层authority和其余Worker对象图仍未迁移。`ProcessServiceContainer`仍有60个字段，排除6个锁和`application_ports`后约53个可变服务引用；真实Edge进程、设备和物理回执没有验收证据。因此D7c不是完整Query Plane、Composition或NE-01完成。

所以当前更准确的说法是“集中式 Service Locator”，不是“显式 Composition Root 已完成”。

## 8. 长期演化资产

| 目标能力 | 已有资产 | 当前限制 |
|---|---|---|
| World Model | `runtime/world_model.py`、`WorldModelQueryRepository` | 只是只读事实投影，`write_api_available=false`；当前默认未启用 Life projection，也不是预测/模拟/规划模型 |
| Scene Policy | `persona/scene_policy.py`，Surface/Proactive 消费接线存在 | 生产默认受 `HOST_LIFE_PROJECTION_ENABLED=false` 关闭；仍无统一场景 aggregate、transition journal 与任务上下文合同 |
| Intent Arbiter | Governor、`action_arbiter.py` 可复用 | 没有合并 user/task/life/relationship/long-term goal 的统一仲裁实现 |
| Action Preview / Shadow | `governor/action_preview.py`、Obs route | dry-run 合同真实存在，但生产没有 trusted authority provider；端点仅做 eligibility 判断，固定 `would_execute=false`，也没有统一 shadow runner |
| Capability Maturity | scorecard/provider/Governor adapter | policy 默认关闭；缺 durable promotion/degradation lifecycle |
| Capability Forge | `app/capability_forge.py` | 是 gap proposal/canary 服务，不是从文档/轨迹生成 SkillBundle 的 Skill Compiler |
| Evolution Lab | `evolution_lab/policy.py` | 只有分支前缀守卫，尚无 scenario runner、experiment store 或 promotion gate |
| Feedback | response/persona/recommendation feedback 资产 | 没有统一 proposal pipeline；Prefrontal 调用 `on_interaction_cycle()` 的参数与真实签名不符，`TypeError` 被吞掉 |
| Persona Growth | `persona_growth.py`、Bond stage、Evolution Orchestrator、emergent growth skill | legacy 入口已被 `PersonaEngine` 中央围栏和静态扫描封禁；当前只产生经 drift guard 检查的无副作用 candidate，尚未接入 proposal/Review/版本激活 |
| Knowledge OS | Memory、Retrieval、Canon、Markdown KB | 缺统一 provenance、版本、过期、冲突与 Decision Record 生命周期 |
| Identity | `release/identity_snapshot.py` | snapshot/diff/review/rollback 合同存在；gate 默认关闭，未形成真实 migration executor |
| Runtime Guardian | health、degradation、heartbeat、service catalog | degradation/recovery evidence 已接部分生产链；Catalog startup coordinator 未进生产 composition、默认 `AUDIT`，前门与 SLO owner 不完整 |
| Workspace Isolation | `agent_runtime/workspace_paths.py`、`agent_runtime/workspace_policy.py`、`agent_runtime/workspace_allocator.py`、`agent_runtime/workspace_runtime.py`、`composition/agent_workspace.py`、`persistence/workspace_repository.py`、`persistence/workspace_operation_fencing.py` | D0/W0/W1 继续生效。W2 为认证 Internal Agent 写 admission 派生 graph workspace，显式配置 secret 后冻结 exact source commit并创建独立 Git worktree；实例/PID lease、同 workspace 独占 scope、heartbeat、顺序 handoff、过期 takeover、cleanup receipt、启动同步 1 条+后台 pending continuation 与 ASGI quiescence 已接线。focused fencing 模块只让 provisioning/allocator/cleanup adapter 的外部 Git/filesystem callback 由 workspace/source 锁串行，在共享 deadline 内运行并在前后 exact authority 短事务间完成，不持有 SQLite writer；后验漂移只返回失败。Agent tool 只在动作间协作取消，Verification 只在整体启动前验活；二者没有逐操作 fence或强杀。默认缺 secret 时写入 fail closed，canary 未跑。状态仅为 `PARTIAL_W2_INTERNAL_GIT_WORKTREE_WIRED`，且没有 promotion/abandon、accepted checkpoint HEAD、Domain/Skill 接线、OS/container/tenant 与 handle-level TOCTOU 防护 |

## 9. 仓库边界

- 顶层 `data/`、`flows/`、`skills/`、`integrations/` 已删除。
- 可变运行数据进入 `runtime/`，测试/验收输出进入被忽略的 `artifacts/`。
- 内置 Skill 代码保留在 `host_brain/skills/`；外部 bundle 只能经过签名接收与隔离，不能靠目录扫描激活。
- 跨进程合同保留在 `natsume_contracts/`。

## 10. 当前最高优先级缺口

1. 守护 B5C cancellation、Operation/Effect reconciliation、0098 immutable overlay 与 0099 case/proof/supersession、graph-wide fence、canonical schema/codec 合同；对目标部署盘点并修复/隔离 legacy inventory，补齐密钥生命周期、Operator 凭据与生产故障矩阵；此前不启用 canary。
2. 持续守护 0099 stable system proof、healthy closure 零写入、0090 唯一与 0091/0092 完整 validator；持续守护 0096 PRE optional-attempt、POST no-rerun 与 claim-fenced continuation。
3. 维持 0093/0095 故障矩阵：deterministic legacy identity、升级 ID/key 稳定、revisioned/legacy payload、trigger/index readiness、source-ref 一对一、append-success/complete-loss exact replay、零重复 Event、per-revision 顺序和 Event/Review/Operator exact read；不得把 delivery claim 误当成 0092 cancellation claim。
4. 为 safe-retry contract path 接入可证明无副作用的现实 resolver 瞬态来源；持续守护 Budget contention 合同，并完成 promotion+claim 单事务、历史半状态 reconciliation，以及 general/batch/pre-budget recovery。single-node post-budget takeover 不得被泛化为这一项已完成。
5. 对 0096 Review continuation 做跨进程故障矩阵与 canary，确保 enqueue 原子性、过期扫描、takeover/stale fence、surface 路由、grant renewal 与关闭顺序持续成立。
6. 删除全局 Service Locator，建立 Host/Obs/Worker 显式 composition scopes。
7. 为 Persona 的无副作用 candidate 保持中央 fail-close，NE-06 再接入 proposal、Review、版本激活与 rollback，不恢复直接 writer。
8. 让 PlanningSearch 从真实目标生成可竞争后继，并以 Research/Code production shadow 证明收益。
9. 在上述执行地基闭环后，再推进 Evolution Lab、Skill Compiler、Knowledge OS 与 Identity Migration。

这些缺口的唯一活动执行顺序见 [NEXT_GENERATION_EVOLUTION_EXECUTION.md](../../30_devspec/NEXT_GENERATION_EVOLUTION_EXECUTION.md)。
