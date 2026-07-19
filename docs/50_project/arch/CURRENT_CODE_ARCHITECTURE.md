受众：MASTER
标题：Natsume AI 当前代码架构事实

# Natsume AI 当前代码架构事实

版本：v5.20
核对日期：2026-07-19
原始审计基线：`origin/dev@a6d7ae383`，叠加仓库清理 `f0cd1dfd`；当前事实以本文所在提交的工作树为准
状态：`CODE_FACTS / FULL_ARCHITECTURE_INCOMPLETE`

本文只回答“代码现在是什么”。目标、执行顺序和历史结论分别由 [TARGET_ARCHITECTURE.md](TARGET_ARCHITECTURE.md)、[NEXT_GENERATION_EVOLUTION_EXECUTION.md](../../30_devspec/NEXT_GENERATION_EVOLUTION_EXECUTION.md) 与 [历史程序目录](../../_history/programs/2026-07/README.md) 承担。若本文与代码冲突，以生产代码和生产 composition 为准。

## 1. 结论

当前系统已经建立较完整的持久化、授权、事件、模型、设备和 Host 四路线执行地基，但还不是完整的下一代架构。

| 能力 | 当前定级 | 代码事实 |
|---|---|---|
| Core Infrastructure | `AVAILABLE` | migration、journal、outbox、model attempt、capability broker、Life、Edge、Query 等主干已存在 |
| Host `chat/skill/delegate/plan` | `PENTEST_READONLY_DELEGATE_WIRED / UNIVERSAL_RUNTIME_INCOMPLETE` | 四条路线进入 `GovernedHostRuntime`，Executive 对它们 fail closed。D9 已冻结认证 ingress→Governor→HostCommand lineage；D10 仅让 `pentest_agent` 消费带完整 node grant/Attempt/Lease/Fence 的 invocation，并在调用前重验 node grant；其他 Delegate target 在 admission 前 fail closed |
| Scheduler | `WIRED` | Clock/Manual occurrence 已贯通 `WorkOrderV3 + BudgetPolicy + child grant + Review + Verification + Acceptance + Receipt` |
| Internal Agent | `WIRED` | v2 只在 ingress/executor 兼容边界存在；canonical admission 使用 `WorkOrderV3 + BudgetPolicy + delegate:agent_runtime`，并验证 child grant、Review、Verification、Acceptance、Receipt 与 replay |
| 原生多节点 Durable TaskGraph | `PARTIAL_B5C_DURABLE_CANCELLATION_SHADOW / PRODUCTION_OFF` | 0102+GR06 已把 exact NODE Effect、immutable artifact、`ACCEPTED` 与 Attempt-bound Outbox 合为 canonical 应用 Store UoW。TaskStore 的 public graph/effect staging 写入 API 已删除；生产 Graph 只能从 HostCommand admission 同一 UoW 进入，Effect 只能从 canonical Acceptance UoW 进入。应用 Python 写 authority 定级为 `APPLICATION_PYTHON_WRITE_AUTHORITY_SEALED`，但 SQLite 无新 migration/trigger，raw/orphan SQL 仍可绕过，因此同时保留 `SQL_DIRECT_BYPASS_PRESENT`。general/batch/pre-budget recovery、部署 inventory/key/canary 仍缺 |
| 通用 PlanningSearch | `PARTIAL_D19_ALLOWLISTED_SUBPROCESS_VERIFICATION_EXPERIMENT` | 公共 L1/L2/L3 内核、不可变 Goal/VirtualStateSnapshot、纯 GoalPlanningService、shadow benchmark 合同与 context-owned compiler 已存在；D16 落下 Code 动态纯 shadow catalog，D17/D18 增加实验专用 `PRIVATE_BARE_WORKTREE_TRUSTED_PROCESS_ONLY` 双 worktree、`VERIFIED_CLEAN` 和 0106 preregistered durable ledger；D19 只增加预注册冻结 pytest argv 的受控子进程证据，尚无生产 domain cutover 或收益结论 |
| Host 智能候选规划 | `NOT_IMPLEMENTED` | Host Plan 只对已批准 `planned_steps` 做拓扑合法化，不生成竞争方案 |
| Pentest L3 | `WIRED_SHADOW_ONLY_DURABLE_INJECTION_PARTIAL` | Conditional MCTS 可运行，adapter 强制选择 L1/L2 baseline；0105 的通用 append-only calibration repository 可被显式注入并跨重启重放，physical schema drift 失败关闭，但默认兼容路径仍是内存 ledger；注入库故障只留可观察事件、不改变 baseline，且无 receipt-bound counterfactual/promotion authority |
| Code/Research 收益 | `UNPROVEN` | Code 已有冻结 ref/digest 驱动的动态 pure catalog、trusted-process private-bare 双 worktree、`VERIFIED_CLEAN`/0106 restart replay，以及 D19 受控 pytest 子进程回执；没有 durable historical cohort、受控模型/工具、OS/container sandbox、独立信任域 verifier 或成对收益结论 |
| Budget | `CORE_INGRESS_WIRED / CONTENTION_PROVEN / PROGRAM_PARTIAL` | 8/16/32 线程及跨进程只产生一个 EXCEEDED winner/Outbox，其他 sibling 引用 winner；charge CAS attempt 至少为 `concurrency + 1`，concurrency=8 的第 9 次成功已验证。concurrency 计量与 DEGRADE/REVIEW 未完整执行 |
| Governed Chat Surface 模型预算 | `PARTIAL_D11_GOVERNED_CHAT_SURFACE_MODEL_GUARD` | 仅已 admission 的 Host chat 节点把冻结剩余预算传给 Surface 主模型调用：强制 provider attempt、总 token、输出 token 与 model cost 上限，缺 canonical usage 或超限即拒绝；不覆盖 Thalamus、Delegate、通用工具或全局成本预支 |
| Governed Agent Runtime 模型端口 | `PARTIAL_D15_GOVERNED_AGENT_MODEL_PORT_BRIDGE` | 仅在 CapabilityBroker 已重验 sealed governed authority 后，`codex_runtime` 从 composed `ModelAttemptService` 派生同一 `PromptRegistry` 并交给 `AgentRuntimeExecutor`；缺端口/authority 直接关闭。D16 的 `agent_worker` 只是 simulation-only graph descriptor，不能证明该端口实际调用；写入仍缺 WorkspaceGrant/WorkspaceRuntime，ReAct 也尚未受 `ModelBudgetGuard` 约束 |
| Review/Resume | `WIRED_0096_TASK_BOUND / CANARY_NOT_RUN` | task-bound Review 的批准、拒绝和过期与 continuation 原子提交；composition worker 按原 Host/Internal/Scheduler surface 自动重入。PRE 不制造伪 Attempt，POST 不重跑 worker；独立 `review:decide` policy 已接线，但 Operator credential/UI 和真实 canary未完成，一般 recoverable Attempt 仍未统一在线续跑 |
| 对话副作用 | `POST_ACCEPTANCE_EVENTUAL` | Acceptance 原子写 effect intents；user/assistant/delivery 实际应用不是一个全局事务 |
| Embodiment / Edge receipt | `PARTIAL_D8_DURABLE_EMBODIMENT_AUTHORITY` | Host 0104 让新 outbox 行绑定 canonical DB 派生的 `runtime_scope_id`，同 scope 只有一个持 durable runtime lease/fence 的进程可执行，第二实例失败关闭；enqueue、claim、过期、收口与对账均在同一写事务复验该 authority。精确 Edge session/owner/lease/fence 由 Host 持久保存；替代 runtime 必须在开放路由前 exact reconnect、轮换 Edge fence 并验证新 lease/`fence+1`。Edge 0002 冻结 command owner，receipt query v2 受 owner/current session/lease/fence 和 command/key 精确约束，legacy ownerless 返回 `NOT_FOUND`；`IN_DOUBT` 仅可只读 CAS 对账、绝不重放。loopback TCP/HMAC/SQLite 查询与重启 exact replay 已测；真实设备仍 `NOT_TESTED` |
| Composition | `INCOMPLETE / PARTIAL_HOST_LIFECYCLE_APP_LOCAL` | D1～D8 边界继续成立。每个 Host ASGI app 现拥有独立 `HostLifecycleContainer`；factory/start 成功后才发布，Activity factory 失败不发布 Mode，closing fence 阻断 `begin()`，ResourceManager startup sample 位于唯一 owned loop。Chat/Input、Tool、Scheduler、Guardian heartbeat 与 lifecycle snapshot 读取选中的 app-local 实例，Obs snapshot 只读不懒启。Tribe agents/bus 仍为 process-global，lifespan exact owner fence 只允许一个 live Host、第二个 fail closed，不提供并发 multi-app Tribe 隔离。Host lifecycle 原始 commit-bound 快照为 named47/152→45/124、all-form52→51、Container56/4/51；合并 Tool/MCP 删除后的当前值为 named45/123、all-form51、Container55 fields/4 locks/50 mutable refs。process-wide locator、heartbeat cache/client、HardState、Tribe bus、shared resource status file、真实 Tool/MCP executor、其他 Host/Worker authority 与真实设备效果仍未闭环；NE-01 不得封板 |
| Persona 演化 authority | `CENTRAL_FENCE_WIRED` | `PersonaEngine` 已中央拒绝非迁移写入；stage/milestone、interaction 与 emergent 只返回候选，不改变 trait 或消费 one-time/history；NE-06 proposal/Review/版本激活尚未实现 |
| 长期演化能力 | `PARTIAL_OR_MISSING` | World/Scene/Intent/Guardian、Action Preview 与 Host Catalog authority 已进入生产对象图且 commit-bound 本地 Gate 已通过；长周期目标和后续 Lab、Skill、Knowledge、Identity 闭环仍未完成 |

NE-02 D20b 已开始建立独立于通用 DomainBenchmark/ModelAttempt 的 Research shadow 合同：语料 snapshot/manifest/source 精确绑定、`cache_policy=NONE`、两臂独立 execution、结构化 citation verifier 和 `SCRIPTED_REPLAY`/`CANONICAL_ATTEMPT_UNATTESTED` 非收益证据；0107 将 registration、run spec、arm execution 和 terminal receipt 持久化为 append-only SQLite ledger。D20c 冻结的 `ExternalProviderRunAttestationV1` 仍未获得真实 provider cohort，因此不提供事实真值证明或生产影响。NE-01 的 fan-out/atomic 开关还受 `HOST_TASK_RUNTIME_CANARY_RECEIPT` 启动门禁约束，缺 receipt 不得打开。NE-03 当前工作树已由 Host ASGI lifespan 构造唯一 Runtime Context composition：服务端 observation 进入 0111，Scene/Guardian 进入 0112，Surface、Proactive 和 Task admission 冻结同一 Scene identity，节点 Intent 经真实 authority adapter 仲裁并持久化 receipt set。Action Preview 在执行点重开 canonical authority，0113 Host Catalog 从 fresh DB 逐级启动到默认目标 ENFORCE。Guardian 绑定 Host runtime lease/fence，已切流前门不默认读全局 degradation singleton；commit-bound 本地 Gate 与 crash takeover 已通过。这些只能将 NE-03 定级为“生产切流实现、本地验收通过但仍有已知边界”：长周期目标 owner、Bond 上游原始证据、Windows graceful stop、Operator 功能迁移和生产多实例 canary 仍是明确边界。#81 的 0108 Evolution Lab 是 self-attested V1 shadow；#82 的 0109 Skill lifecycle 在真实 authority resolution、原子 UoW、exact replay、跨版本 rollback 和 Host registry cutover 上均未完成；#83 的 0110 Feedback/Knowledge/Identity 只是事件账本，没有切换真实 owner。以上后续资产全部保持 `PARTIAL/LEDGER_ONLY`，不得从 append-only 或测试通过推导生产闭环。

恢复调度边界现在会对 durable TaskGraph snapshot 重新执行全图环检测；发现依赖环时返回 `PLAN_CYCLE` 并失败关闭，绝不忽略依赖或退化为串行执行。该修复收口执行安全边界，但不改变多节点生产 canary 尚未完成的事实。

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

认证 Host Input/Gateway 在 Governor 前将 Secure Ingress principal 冻结为 command principal；`GovernedHostRuntime` 对 direct `chat/skill/delegate` 与 approved-plan 统一冻结 `surface / principal_id / ingress_claim_ref / ingress_audit_ref / input_sha256`。Plan 在首次 repository admission 前以 base request/session/principal 只读预检 parent Governor binding，direct route 也先预检，child grant 派生再复验；因此 principal 漂移不会留下不可执行的初始 Plan 图。同时具有成对 ingress ref 且 Gateway source 才是 `GATEWAY`，其他认证输入为 `HOST_INPUT`，无 ingress 为 `CHAT`；半绑定 ref 或畸形 SHA-256 失败关闭。D10 为 `pentest_agent` 建立唯一启用的 inner delegate adapter：不可变 invocation 带 command、graph/revision/node、attempt、lease/owner/fence、parent/node grant，调用前重验 durable grant，handler 只产生无副作用 Domain Pack/SecureRun handoff，也不写 legacy `skill_exec_log`。其它 target 在 direct/plan admission 前拒绝；这不是通用子 Agent 平台，也没有把已批准步骤的拓扑排序变成候选规划搜索。

关键实现：

- `host_brain/agent_runtime/cortex_runtime.py`
- `host_brain/app/governed_task_adapter.py`
- `host_brain/app/governed_delegate_dispatch.py`
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

应用 Store 的 public `transition_node(ACCEPTED)` 在 0102 存在时已要求有效 artifact，生产三条 acceptance 路径不再拥有第二 writer。本轮进一步删除 `DurableTaskStore.create_graph`、`insert_graph_in_transaction`、`stage_acceptance_effect_intent` 三个 public 入口。唯一生产 Graph hook 是 `_insert_graph_from_host_command_in_transaction()`：它仅由 `HostCommandRepository._admit_normalized()` 调用 1 次，并借用同一 admission transaction。唯一 Effect INSERT hook 是 `_stage_acceptance_effect_intent_in_transaction()`：它仅由 `transition_graph_and_stage_acceptance_effects`、`commit_node_acceptance`、`complete_parallel_graph_success` 三个 canonical UoW 使用，共 6 个生产调用点。Graph/Node INSERT SQL 只存在于前一 hook，Acceptance Effect INSERT SQL 只存在于后一 hook。

这个结果只能定级为 `APPLICATION_PYTHON_WRITE_AUTHORITY_SEALED / SQL_DIRECT_BYPASS_PRESENT`。应用 Python 生产调用面已封口，但 SQLite 仍允许 raw/orphan SQL 构造无 `host_command_journal` 的 Graph/Node/Effect，本轮没有增加 migration 或 trigger。`tests/task_runtime_authority_fixtures.py` 是显式 test-only raw/orphan fixture，不得被引用为 canonical HostCommand admission 或生产单写的证据；也不能据此宣称一般 recovery、production fan-out 或 NE-01 已完成。

TaskStore 切片的定向证据不做重叠汇总，分组如下：authority/structure `19 passed`；HostCommand `1 passed`；NLE/operation/heartbeat `77 passed`；acceptance-effect `15 passed`；parallel/settlement/atomic `45 passed`；terminal classification `32 passed`。Cancellation 首跑为 `29 passed + 1 fixture failed`，修正 fixture 后该 case `1 passed`；integration 首跑为 `46 passed + 3 fixture failed`，修正后 3 个 cases 分别通过。第三个 fixture 的 `node_risk_level` 断点已在 `origin/dev` 复现后修正。波次 integration 为 `NOT_RUN_PER_WAVE_POLICY`；该切片完成时 full architecture static Gate 为 `FAIL [1,6,10]`。

TaskStore 切片之后，NE-01 又关闭了 full architecture static Gate 10。0112 Runtime Context readiness 不再由 `runtime/context_authority_schema.py` 打开私有 SQLite 连接，而由 `RuntimeEventRepository` 持有检查实现并通过 `RuntimeEventStore` readiness port 提供给 runtime。Service Catalog 的连接、SQL 与事务从 `service_catalog/rollout_authority.py` 下沉到 `persistence/SQLiteServiceCatalogRolloutRepository`；authority 只保留不可变合同、repository Protocol 与 Guardian Observer，rollout runner 依赖 Protocol。`ACTIVATION_STARTED` receipt 和 rollout head 由 repository 在同一事务写入，故障注入证明任一写入失败时共同回滚。

World Observation 的 `_write_lock` 实际仅是保护 transition 的 `RLock`，静态 Gate 曾把名称误判为业务 SQLite writer；它已更名 `_transition_lock`，没有增加或迁移数据库写 authority。该切片没有新增 migration。`host_startup.py` 与 ASGI 仍负责 concrete `SQLiteServiceCatalogRolloutRepository` wiring，因此这里只能定级为 `GATE_10_PASS / TYPED_COMPOSITION_INCOMPLETE`。定向证据为 Context `7 passed`、Catalog unit/contract `15 passed`、真实 Host socket integration `6 passed`、Repository/Global 独立复跑 `16 passed`（最后一组与 15 项重叠，禁止相加）以及 Global SQLite boundary `PASS`。该切片完成时 full static 为 `FAIL [1,6]`；`origin/dev` 基线为 `FAIL [1,6,10]`。当时剩余 Gate 1 是 application/composition/persistence SCC，Gate 6 是 projection daemon app lifecycle owner；NE-01 仍 `IN_PROGRESS`，wave integration=`NOT_RUN_PER_WAVE_POLICY`、`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。

随后的 Gate 6 切片把 `ObsApplicationContainer` 泛化 `start()` 改成 `start_projection_jobs()`，并以静态合同锁定生产唯一调用点为 `obs_fastapi_app.py` 嵌套 `lifespan`。lifespan 先对 ports bundle 做原子 single-use claim，再 compose app-local daemon、start、yield；finally 的真实关闭序列是 stop/join/in-flight fence、精确 leader lease release、close owned SQLite、clear state。stop timeout 或 rollback close failure 不覆盖原句柄，保留 container/ports 供重试。bundle 内 claim 令两个 app 不能共享同一 ports，`dataclasses.replace()` 继承同一 claim 也不能绕过；两个独立 ports/app 保持隔离。没有 module-global daemon，也没有新 migration。

完整 Obs app 首跑为 `13 passed + 1 test fixture failed`，旧 fixture monkeypatch 仍引用已删除的 `start`；修正后该 case `1 passed`。最终生命周期关键批次为 `12 passed`，daemon cadence/shutdown/serialization/start-single-flight 为 `4 passed`，最终 Gate 6 定向集合为 `19 passed`；这些集合有重叠，禁止相加。full architecture static 当前为 `FAIL [11]`，Gate 1/6/10 均 `PASS`，`origin/dev` 基线仍为 `FAIL [1,6,10]`。Gate 11 ToolBroker production-service inventory 双命中和 NE-01 仍未完成，wave integration=`NOT_RUN_PER_WAVE_POLICY`、`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。后台 daemon 若从未捕获的 renew 等异常退出，`/health` 尚不直接展示 `daemon.running/last_failure`，continuous-SLO supervision 仍未闭环。

Gate 1 的首个分层收口只处理 Service Catalog：`service_catalog/startup_coordinator.py` 成为启动协调器 canonical owner，原 composition 路径仅同对象重导出；稳定合同进入 `contracts/service_catalog.py`，旧类型路径同样仅同对象重导出；`rollout_authority.py` 只保留 Guardian observer，`SQLiteServiceCatalogRolloutRepository` 只依赖稳定 contracts。application→composition 两条和本域 persistence→application 一条依赖已清零，使 SCC 从 application/composition/persistence 缩为 application/persistence。该切片完成时 persistence→application 仍有 11 条：PlanningSearch 7、Evolution/Lab/Skill/World 4。定向证据为 contract/unit `61 passed`、真实 Catalog/Host socket integration `7 passed`、边界复跑 `7 passed`，后两组可能重叠不得相加。

第二个 Gate 1 分层切片新增纯 `host_brain/contracts/planning_search/`，把 core、benchmark、calibration、code、research 八组稳定合同与模型移出 application；旧 `host_brain/planning_search/` 同名模块仅同对象重导出，兼容 `__all__`、类型 identity、`isinstance` 与序列化形状。`code_benchmark_ledger_repository.py`、`planning_search_calibration_repository.py`、`research_shadow_ledger_repository.py` 不再导入 `host_brain.planning_search`，因此本域 7 条 persistence→application 已清零。边界合同 `5 passed`，D13/D18/D19/D20 回归 `60 passed, 1 skipped`，跳过来自 Windows 无 symlink 权限；`py_compile` 和 diff-check 通过。当前 Gate 1 仍 `FAIL`，application/persistence SCC 尚有 Evolution/Lab/Skill/World 4 条反向依赖；application→composition=0、Edge violation=0、reverse import=0，Gate 6/Gate 10 继续 `PASS`。本切片无 migration，只改变持久化合同依赖方向，不证明 PlanningSearch 生产功能或收益，typed Composition、全局 Service Locator 清零与 NE-01 均未完成。

最终 Gate 1 切片又把 Evolution、Evolution Lab、Runtime Context 和 Skill Lifecycle 的持久化共享类型迁入纯 `host_brain/contracts/evolution.py`、`evolution_lab.py`、`runtime_context.py`、`skill_lifecycle.py`。旧 `evolution/contracts.py`、`evolution_lab/contracts.py`、`runtime/context_contracts.py` 保持同对象重导出，`skills/skill_lifecycle.py` 仅保留应用服务并重导出合同；Evolution Governance、Evolution Lab、Skill Lifecycle、World Observation 四个 repository 只导入中立 contracts。最后 4 条 persistence→application 因此归零。fresh Gate 1=`PASS`，`issues=[]`、SCC=`[]`、Edge finding/violation=0、reverse imports=`[]`，Gate 6/10 继续通过；但 #87 组合后的 full architecture static=`FAIL [11]`，因为 ToolBroker production-service inventory 同时命中 `process_runtime_services.py` 与 `tools/broker.py`。边界合同 `4 passed`，Context/World、Evolution Lab、Skill、Evolution Governance 定向 `32 passed`，py_compile/diff-check 通过。本切片无 migration；它只关闭 Gate 1，不等于 typed Composition、Universal Governed Runtime、PlanningSearch 收益或 NE-04～NE-06 生产功能完成，NE-01 仍 `IN_PROGRESS`，wave integration=`NOT_RUN_PER_WAVE_POLICY`、`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。

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

NE-01 两批 focused split 均未新增或放宽 policy 白名单。第二批将 ASGI 生命周期 helper、Obs 响应模型、Degradation policy、Node Grant/Planning codec、Intent Review continuation 和 Guardian contracts/operations 从原 authority owner 中迁出，第三切片再将 Host/Tool app-local 装配迁入 typed composition；七个原文件当前分别为 1492、1303、1894、1495、1281、1336、1247 行，fresh 全仓 mixed-file Gate 已为 `issues=[]`。本轮 claim-result 查询也从 `HostCommandRepository` 抽入 440 行的无状态 mixin，原 repository 为 1387 行；mixin 只复用 owner 传入的 connection/UoW，不构造 Store 或事务。Operator 外层回执通过 ASGI 从同一 app-local OBS composition 注入 checksum-chained Outcome Journal 的只读 lookup；缺少该 authority 时恢复链 fail closed，Journal 仍是唯一 outcome writer。`DurableDagSchedulingRuntime` 当前 1,338 行，`coordinator.py` 268 行；238 行 `AcceptedGraphFinalizationRuntime` 统一 normal/restart/continuation completion，Operation recovery 与 checkpoint continuation 均保持低于 600 行的窄职责。Dag 当前矩阵为 Store 9、Budget 9、Lifecycle 17、AcceptedFinalization 1、NodeProjection 13、NodeExecution 7、Settlement 4、Success 1、Review 4、Cancellation 1、Outbox 1 build/6 publish。静态 mixed-file Gate 清零不等于全架构静态 Gate 通过，更不等于运行闭环：它仍是临时 outer orchestrator，Service Locator 与旧执行 authority 仍在，状态保持 `PARTIAL_B5C_DURABLE_CANCELLATION_SHADOW / PRODUCTION_OFF`。

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

D11 只收紧一条已 admission 的 Host `chat` 垂线：`FrozenNodeInput` 的 active Budget remaining 被投影为不可扩张的 node-bound `ModelBudgetGuard`，再由 `FrozenChatRouteExecutor → Surface main-chain → ModelAttemptService` 消费。该调用禁用 cache 和 streaming，按冻结剩余值限制 deadline、provider attempt、总 token、输出 token 与 `model_cost_micros`；provider 必须返回 `token_count / completion_tokens / model_cost_micros` 三项 canonical 整数。量测缺失、超限、timeout 或 provider 失败都会终止当前 guarded call，不再 retry/fallback；Surface 不会把它降级成 `APPROVE`，并跳过额外的 Spark judge。成功 trace 才会被投影为该 chat worker 的 budget observation。

这不是全局模型成本预支或通用预算账本完成：Thalamus/Cortex 入场前模型、Delegate/子 Agent、其他模型调用、通用外部工具和跨 route 累计成本仍不在此 guard 内；没有 provider 报告 canonical `model_cost_micros` 时，D11 选择 fail closed，而不是推算价格或伪造已结算成本。

同一 Reservation 的超限竞争现在有唯一 durable authority：8/16/32 线程及跨进程压力下，只有一个 rejected usage 是 `EXCEEDED` winner并拥有版本 transition/Outbox；其他 sibling 是引用该 Outbox 的 `ALREADY_EXCEEDED` observation，不推进版本，也不参与 terminal winner selection。0090 决策读/replay 会在同一 SQLite snapshot 中重新验证完整 authority，而不是只相信 selected usage ID。

限制：

- 非 D11 路径中 provider 未返回的 token/cost 保持 `UNMEASURED`，不做伪精确估算；D11 guarded Surface 调用则直接拒绝缺少 canonical token/cost 的结果；
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

- `SearchRequest`、`SearchCandidate`、`SearchBudget`、`SearchResult`；`VirtualStateSnapshot` 会对 capability/fact/constraint/source refs 计算不可伪造 digest；
- `PlanningGoal`、`SuccessorCatalogPort`、Cognitive Ranking、Virtual Transition、Reward、MCTS Condition ports；
- UCB 选择、rollout、backpropagation、预算和确定性 trace；
- `CalibrationPrediction` / `CalibrationOutcome` / `CalibrationRepository`：shadow-only、JSON-safe 的不可变 evidence 合同；
- `DomainBenchmarkWorkload` / baseline 与 selected arm / verifier receipt / isolation / budget 的 `DomainBenchmarkPort` 合同；该合同强制 `SHADOW_ONLY` 与 `production_influence_enabled=false`；
- 不包含工具执行、网络访问或状态写入端口。

公共算法本身成立，但“算法存在”不等于“生产规划能力已成立”。

### 5.1.1 D14 Goal Planning foundation

`host_brain/orchestration/goal_planning.py` 现在可以把同一领域的 immutable `PlanningGoal + VirtualStateSnapshot + PlanningDomainPorts` 送入唯一的 `PlanningSearchService`。它只返回带 state schema/digest 的纯 `SearchResult`；不编译、准入、持久化、调 worker 或执行工具。失败重规划也只冻结 failure/accepted artifact refs 并产生新的纯 request，Task Runtime 仍必须通过已有的 durable recovery → `PlanningGraphCompiler.replan()` → admission 创建 revision `+1`。

`PlanningGraphCompiler` 新增可选 `PlanningCompilationContext`：新 goal-driven catalog 只能产生 node-level descriptor，不能夹带 budget、approval/review、workspace、idempotency 或 graph metadata。compiler 把 governor-bound context 写入 immutable WorkOrder，并把 VirtualState digest 同时绑定到 selected-plan lineage、`planner_ref`、operation fingerprint、input pack、metadata 与 compilation digest。旧 `planned_steps` compatibility path 仍在，不能因此被误称为 Host 自主候选规划。

这只是通用接口与 immutable compilation foundation：没有 Host chat/plan 生产入口使用 `GoalPlanningService`，没有 Code/Research/Pentest 动态 successor 的 production cutover，没有实际成对 verifier receipt，也没有 MCTS 对真实用户任务的 influence。

### 5.1.2 D16 Code dynamic shadow catalog

`host_brain/planning_search/code_domain_models.py` 把一个 Code 任务冻结为只含 opaque governed ref、digest、目标 artifact、失败测试 ref、预注册 patch 和验证 profile 的 `CodeTaskSnapshot`；`host_brain/planning_search/code_domain.py` 只保留动态 catalog、排序和编译绑定。`CodeSuccessorCatalog` 不读取 `planned_steps`：它从根状态生成 `inspect`，再从已检查状态生成预注册 `patch`，最后生成 `unit verify / rollback`。L2 是确定性虚拟证据排序；只有冻结 `virtual_model_ready=true` 时才向公共内核暴露 transition/reward，因而即使外部要求 `MCTSMode.ALWAYS`，未就绪快照也没有可运行的 L3 端口。编译绑定还会拒绝任意 `execute` root、真实 workspace 字段、未知 root action 或缺失的 `write_files/network` 禁令；唯一允许的 root 策略是 `inspect + generate_report + {strategy: temp_workspace}`。

选择路径随后必须由 `bind_selected_code_inputs()` 再次逐段对照 catalog，才能把唯一的 `approved_candidate_inputs` 写进新的 `PlanningCompilationContext`。它拒绝 raw/file URI、超出 frozen target/test/change bound 的 patch、legacy `intent_draft` 覆盖、goal/state/objective 漂移，以及没有 durable predecessor lineage 的中间虚拟状态。`GovernedPlanningExecutionService`、显式 replan 和 recovery 也会透传该 context，并在搜索前要求 `approval_ref == parent governor grant`。

这不是 Code 执行切流：不会打开 worktree、读取源码、调用模型/工具/测试/provider、admit TaskGraph 或写 workspace；`allowed_path_refs` / `forbidden_path_refs` 只是冻结证据，不是 live ACL。没有历史成对 A/B、独立 verifier receipt、0106 comparison ledger 或 promotion gate，所以仍不能声称 Code 收益或生产 influence。

### 5.1.3 D17–D19 trusted-process private-bare paired-worktree、durable ledger 与受控 pytest 验证

`host_brain/planning_search/code_benchmark_sandbox.py` 不复用 W2 的主 checkout source：它要求服务器注册一个在净化 Git 环境中也干净、且拥有非链接、非共享 `.git` 目录的普通 Git source；source 的 local Git config 与 checkout content 仍是受信输入。在受保护根目录之外用 `git clone --bare --no-local` 建立一次性 private bare repository，设置私有空 hooks path、移除 remote 后，从同一个 frozen commit 给 baseline 与 selected 分别建立 detached worktree。Git 子进程丢弃继承的 `GIT_*` 配置，禁用 system/global config 与交互提示、强制关闭 `core.fsmonitor`，仅允许 `file` transport，并跳过标准 LFS smudge；这只是降低 Git 继承面，不是网络或进程隔离。

workload isolation 是精确六字段 manifest：`sandbox_mode`、`source_ref`、`source_commit`、`path_policy_digest`、`executor_trust` 与 `network_isolation`；任何额外字段（包括潜在主机路径）或旧 `network_enabled` 都会拒绝。governed ref 同时拒绝 `file://` 和 host-path-shaped 值。两个 arm 的 `CodeBenchmarkWorkspace` 只接受唯一的小写 portable repository-relative `read_text/write_text/exists` 路径拼写：拒绝 padding、大小写 alias、Windows trailing-dot/space、reserved device、`.git`、绝对路径、`..` 与 symlink component。private root/artifact 同样拒绝 symlink 与 Windows reparse point；cleanup 对发现的 direct-child link 只 unlink/rmdir link 本身，绝不枚举或递归删除外部目标。tracked binary diff 与 untracked regular-file content 各自进入 receipt digest，ignored artifact 直接失败。runner 清理 private worktree/bare repo 后再次比对受保护 source 的 HEAD、refs、tracked/untracked/ignored status、worktree inventory 与 Git control metadata。

这份 API 是受信 adapter 的机械防错层，并非权限隔离：executor 与 verifier 都在当前 Python process，能传入的只能是仓库自有、受审计的实现；绝不能把 model、skill/plugin 或 hidden-oracle 代码交给它。D17 的口径固定为 `PRIVATE_BARE_WORKTREE_TRUSTED_PROCESS_ONLY` 与 `network_isolation=NOT_ENFORCED`，不是 OS/container/tenant/filesystem/network sandbox。

D18 将成功路径收口为：先以 `CodeBenchmarkWorkloadRegistration` 冻结 workload、六字段 isolation 和**声明性** execution budget；再以 `CodeBenchmarkRunSpec` 冻结 baseline/selected 两条 candidate path。这两项 preregistration 是各自独立、append-only 的提交，不承诺与后续运行同一事务。D17 runner 只有在 `finally` 删除 private bare、两个 worktree 和 run root，且受保护 source fingerprint 前后一致时才返回路径无关的 `CodeBenchmarkCleanupReceipt(status=VERIFIED_CLEAN)`。随后 0106 的**terminal-evidence transaction** 才在一个 SQLite transaction 写入两个 arm evidence/outcome、cleanup 和 terminal receipt；所有行不可更新/删除，FK/role/source/policy/cleanup gate 在 SQL 中复核，JSON/digest/对象重放和物理 schema drift 也 fail closed。terminal receipt 只是 `EXPERIMENT_EVIDENCE_ONLY`，不能选 winner 或影响 production。

D19 只在 registration 显式绑定同一 `CodeBenchmarkVerificationPlan` 时生效：计划只接受冻结的 `python -m pytest -q <小写仓库相对测试路径>` argv；两 arm 的最坏测试次数与命令 timeout 总和必须先被 registration 的 `max_test_commands` / `max_wall_time_ms` 覆盖。`ControlledCodeBenchmarkVerifier` 在每个 detached worktree 根目录以 `shell=False` 启动受限子进程，去掉 `PYTHONPATH`、关闭 pytest 自动插件加载和 cache provider，并将执行 argv digest、exit/status、受限 stdout/stderr digest/长度与耗时封为 path-free verifier receipt；策略实际解析的当前 Python 解释器会先被核验，再持久化回冻结的 portable argv digest，因此 receipt 不携带主机路径。`PASS` 必须等于 exit code `0`；同一 arm 会在第一个 child 可能启动前预留冻结 command count，因此 timeout/policy error 也不能在该 pair 内重试并绕过 `max_test_commands`。每条 receipt 都精确绑定 run/arm/plan/budget，D18 terminal reader 不只重验两 arm 合计 test/wall 不超过预注册 paired limit，也要求两个受控 arm/outcome 都成功；不能绕过专用 service 直接把 FAIL 写成 `COMPLETED_CLEAN`。只有持有这样的 receipt 才能在 D17 的 executor/verifier cost 精确相等合同之外加入这两个 D19 server-observed counter。`ControlledCodeBenchmarkExperimentService` 只在两个受控 verifier 都 PASS 且 cleanup verified 时才写 terminal receipt。

它依然未接 Host、TaskGraph、AgentExecutor、WorkspaceGrant 或生产 route；D19 的 child 只是同一主机受信 workspace 的 subprocess，不是 hidden-oracle 或独立信任域。没有 process-tree kill、CPU/内存、container、OS 文件系统、tenant 或网络隔离；本地 source、pytest 测试及用户 site 依赖仍是受信输入，且为了 Windows user-site discovery 保留 `APPDATA`，没有设置 `PYTHONNOUSERSITE`，因此更不能误称 hermetic Python 环境。`max_model_calls`、provider/tool/token/model-cost 和通用 Durable Budget Ledger 没有接入，且没有 durable benchmark lease、crash cleanup recovery/retry worker、真实历史 cohort、统计比较或收益结论，因此仍不能宣称已跑真实模型修复或完成 Code production shadow。

### 5.1.4 D20a Research frozen-corpus pure-shadow boundary

`host_brain/planning_search/research_domain_models.py` 的 `ResearchTaskSnapshot` 只冻结规划可见的任务/corpus governed refs、`source_id`、source ref、content digest 与 authority/relevance/estimated-read-cost hints；它没有 dedicated 本机 locator、source-body、answer-key、verifier-decision、模型客户端或 executor 字段，constraints 会拒绝这些命名 key，但任务文字不是内容级 DLP。`research_domain.py` 根据这份不可变元数据动态生成 `query`、`quick-answer`、`primary-source`、`independent-source`、`cross-check`、`resolve-conflict`、`stop` 的合法虚拟 successor；L2 只按公开 authority/relevance/cost hints 排序。只有快照冻结 `virtual_transition_ready=true` 时，adapter 才给公共内核提供 virtual transition/reward 与 Conditional MCTS 条件；否则没有 L3 rollout ports。选中路径不会读来源、产出回答或进入编译/admission。

`research_benchmark_corpus.py` 把实验输入读取留在另一个受信边界：`FrozenResearchCorpusLoader` 只从一个可信实验根目录下、`FrozenResearchCorpusManifest` 列出的 canonical repository-relative 普通文件读取。它对 root、每个目录 component、目标文件及打开后的 descriptor 做 link/symlink/reparse/type/identity 检查，读前/读后比对大小、文件状态，并在读完后核对 SHA-256 digest；在稳定的受信输入状态中，绝对路径、`..` 遍历、ADS colon component、case alias、root escape、未列 source、link/reparse 或任何元数据/正文漂移都会失败关闭。其 `public_manifest_receipt()` 和 document receipt 只保留 source id/ref/digest、字节数与 authority tier，绝不输出根目录、relative path 或正文。

这不是生产 Research route、TaskGraph、Host、BrowserWorker 或网页抓取接线；没有真实模型、独立 verifier、paired cohort、成本/延迟对照、收益统计、winner/promotion authority，也没有 OS/tenant/sandbox。loader 的根目录和 manifest 都是受信实验输入，其 before/open/after 检查不是 hostile concurrent replacement 的原子 TOCTOU 防线；D20a 也未把 snapshot 与 manifest 的 corpus/source id/ref/digest/tier 精确绑定，不能据此推导来源独立性。该 handshake 属于 D20b。因此 Research benefit 仍为 `UNPROVEN`。

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
4. 0105 的 `SQLitePlanningCalibrationRepository` 已可跨重启保存 immutable prediction/outcome、拒绝 identity/outcome drift，并验证 table/index/FK/trigger 的物理语义；但只有显式注入 `PentestAgentLoop` / adapter 的实例会使用它，默认兼容 ledger 仍在内存。

shadow 对账的还是实际执行的 baseline 根动作，不是 L3 未执行替代路径的真实结果。候选 target 会在生成 fingerprint 前先收口到授权 scope；D13 写入 durable repository 的 outcome 仅是脱敏 `UNVERIFIED_RUNTIME_RECORD` 投影，并不伪装成 Task/Run receipt。库 busy/schema/drift 会产出可观察的 `mcts_durable_calibration_unavailable`，但因为它只是可选 shadow evidence，不能在真实 baseline 执行后抛出中断。因此 adapter 已强制 production influence 为 `DISABLED_UNTIL_DURABLE_PROMOTION`；无论内存或 durable evidence 的阈值如何，均不能改变真实动作。未来必须同时具备 receipt-bound counterfactual evidence、动态 successor 和已批准 promotion gate，才可另行引入受控切流。

### 5.4 Code / Research

`tests/fixtures/planning_search_shadow/` 与 `docs/45_testing/test_reports/gr02a_planning_search_shadow_benchmark_v1.json` 明确声明 `SIMULATION_SHADOW`、`production_benefit_claimed=false`。D14 新增的是配对 workload/arm/outcome 的 shadow contract；D16 补了 Code 动态 catalog 和编译单测；D17 用临时测试仓库实际执行 private-bare clone、baseline/selected 独立 detached worktree、受信文件 API、verifier ref 与 cleanup，并在 cleanup 后复核受保护源的 Git control metadata fingerprint，漂移即失败。D18 将成功的 clean pair 以 0106 workload/spec/arm/cleanup/terminal receipt 分段不可变保存，重启可精确重放。D19 让预注册的固定 pytest 子进程实际在两个 worktree 中运行并 durable round-trip 其 path-free 回执；它没有把同主机 child 或两项测试预算伪装成模型/工具预算或隔离 sandbox。D20a 只补 Research 的公开 metadata catalog 与可信实验根/manifest 的安全普通文件读取，不产生真实来源获取、模型研究结果或 verifier 对照。仍缺真实模型/工具、隔离的 hidden oracle、历史 cohort/statistical comparison 或收益结论；Research benefit 仍为 `UNPROVEN`。

### 5.4.1 D15 governed Agent 模型端口桥

此前 Host 的 `agent_worker → codex_runtime` Skill 路径虽然把 `ModelAttemptService` 放入 governed execution context，却直接构造裸 `AgentRuntimeExecutor`，所以会在首次模型调用前以 `AGENT_MODEL_RUNTIME_NOT_COMPOSED` 失败。D15 仅在 CapabilityBroker 已验证 sealed task authority 后，才由私有 executor adapter 读取 composition 提供的 `ModelAttemptService`，并从同一服务对象派生 `PromptRegistry`；调用方不能在 payload 中注入服务，缺 authority、模式或端口统一以 `AGENT_RUNTIME_PORTS_NOT_COMPOSED` 关闭。

这不是 Code 写入切流。D16 编译出的 `agent_worker` 节点仍只是 shadow-only descriptor；`agent_worker` 的默认 `runtime_repo` 写 WorkOrder 仍没有 task-bound `WorkspaceRuntime/WorkspaceGrant` 注入，会在 Workspace Policy 的模型调用前关闭；AgentExecutor 的 ReAct `ModelAttempt` 也没有接入 D11 的 `ModelBudgetGuard`。因此本切片的正向证据只覆盖 read-only `agent_inspect` 类端口接线与纯 Code catalog/compile 单测，尚无真实模型调用、真实代码修复、预算 receipt 或生产 canary。

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
| Host embodiment dispatch | app-lifespan `HostEmbodimentRuntime` + `EmbodimentOutboxRepository` | 没有 module default；outbox 按 runtime scope 过滤，同 scope 由 durable Host lease/fence 选出唯一 writer，Edge reconnect credential 持久保存；lifespan 独立 heartbeat 续租，不依赖默认关闭的 governed Scheduler |
| Edge durable receipt | Edge SQLite receipt store + HMAC/nonce receipt query v2 | owner/current session/lease/fence 与 command/idempotency key 精确绑定；legacy ownerless 返回 `NOT_FOUND`；`IN_DOUBT` 只读查询后用 identity+digest CAS 收口，相同 digest 重查不重复落审计，`NOT_FOUND`/失败不重放 |
| Edge physical effect | Edge actuator + durable receipt | loopback TCP/HMAC/SQLite/restart replay 已测，但真实硬件/设备效果仍 `NOT_TESTED` |
| Operator read | `QueryProjectionStore` | Query Plane 以 projection 为主 |
| Task/recent read routes | Durable Task projection + conversation history | 只读，不创建平行任务状态 |

## 7. Composition 现状

`ProcessTaskRuntimeContainer` 是较好的显式 typed composition：它拥有 migration、TaskStore、ReviewStore、CommandRepository、EventStore 与 lifecycle。

Host ASGI 必须先让该 container 完成 canonical migration/readiness，再构造 `QueryProjectionStore`、具身 runtime 与其余依赖这些 schema 的应用对象；fresh database 因此会先得到 canonical migration 的真实结果，不会被 projection 的次生 schema 错误抢先遮蔽。具身 runtime 的 durable authority 由 lifespan 自有 heartbeat 续租，不能依赖默认关闭的 governed Scheduler；shutdown 先 cancel/join heartbeat，再精确释放 runtime lease。任一步失败都发生在 lifespan yield 前，路由不会开放。

但全局对象图尚未收敛：

- `host_brain/composition/process_service_container.py` 在模块级创建 `_PROCESS_SERVICE_CONTAINER`；
- 两轮静态调用图清理已删除 `retrieval_orchestrator`、`retrieval_pipeline`、`context_compressor`、`episodic_memory_graph`、`consolidation_trigger`、`skill_composer` 6 个生产零消费者槽位及 4 个死 accessor；
- 第二轮同时删除 `process_services` 上 7 个无仓内消费者的宽泛重导出和两处 `retrieval_pipeline` 测试 reset；
- container 已删除 ActivityMonitor/ResourceManager 的 2 个全局 slot 与 2 个 lock，也删除 process-owned MCP Broker slot；当前共有 55 个字段、4 个 lock，排除 lock 与 `application_ports` 后仍有 50 个可变 service 引用。Host lifecycle 原始 commit-bound 快照的 56/4/51 仅作历史，不是合并后的当前值；
- `process_services.py` 与 `process_runtime_services.py` 仍暴露数十个 `get_*()` accessor；
- 数十个生产 Python 文件仍直接或间接依赖该 locator；
- `host_brain.app` 仍承担部分真实 service factory 和 wiring。

D1 已先关闭路由级最直接的多 app 串扰：`HostRoutePorts` 是每个 FastAPI app 捕获的 frozen provider bundle，Canon/Tool/Bond 均由 `create_*_router(ports)` 构造；原 module setter/global provider 和 Tool fallback 到 `process_services` 已删除。同一 bundle 还经 Security composition 传入 Canon/Tool/Bond 的 OBS command adapter。双/三 Host app 交错请求、构造和关闭均只命中自己的 fake service。这个结论仅覆盖三类路由；OBS 的 Capability Forge、Scheduler/Review runtime lookup、Life/Tribe 与其余 process locator 仍在。

D2 把独立 Obs 的 `create_obs_app()`、`ObsApplicationPorts` 和 `ObsApplicationContainer` 变成显式应用级 scope。D3 最初注册19个 producer callback（2 core + 17），并先迁移17个生产端点。D4 将 BioOps 8、Life 3、Operator live 4、summary/runtime/resource 4，共19个 GET 的 `read_process_projection` 链改为必需显式 store 参数；36个已识别生产投影端点（35 HTTP + 1 WebSocket）、已识别stats查询与sessions/prompt response cache接入当前app的QueryProjectionStore/View或app-owned cache。D5 又修复此前漏扫的5个 `/api/ui/*` GET，以及Security runtime/assets/incidents、Skill Catalog stats、Life Evolution snapshot、Semantic Recall stats、Governance snapshot共7个实际挂载GET；双Obs app/双SQLite测试证明这些路由不交叉读取。6个 reconciliation 端点另从 app.state 使用显式 repository authority。

每个 `ObsApplicationContainer` 自有的 app-local Projection daemon 保持 single-flight、关闭 join 与 retry handles；模块级 `_default_daemon/register_projection_job` 兼容链和导入期注册已删除。当前21个producer的source-mode为 `DURABLE_READ=6 / STATIC_READ=3 / DURABLE_READ_PARTIAL=8 / AUTHENTICATED_HOST_READ=4 / TYPED_UNAVAILABLE=0`，21/0与认证声明都不是运行成功率。四个认证job分别物化Capability Forge、Semantic Recall、Governance与Organ/Tribe；Semantic/Governance各自读取独立versioned endpoint/type/cache/evidence和故障域。Life Body现在是partial producer：它只有在同app runtime/overview/insights/trace roots都健康且未过期、canonical ledger有界查询可用时写健康快照，否则写typed `SOURCE_UNAVAILABLE`。8个partial及认证来源的运行态失败可能性使health保持`DEGRADED`。

Host为每个lifespan生成一个五合同共享的`source_instance_id`，mode/capability/semantic/governance/runtime各自维护锁内单调`source_revision`。Obs通过通用Query Projection SQLite的`compare_and_upsert`单事务CAS持久当前lineage，并从immutable journal读取完整历史epoch；同epoch的revision、observed、evidence不得回滚，同revision payload不得漂移，任意已退役epoch重放都会拒绝，不受固定窗口限制。合同仅递归校验精确名为`timestamp`或以`*_ts_ms`、`*_at_ms`结尾的嵌套时间不晚于Host observed；Obs再以本地接收时钟允许最多5秒skew、拒绝超过120秒的正证据，并将freshness夹到本地接收时间。Host composition、Host route和Obs receiver各执行公共序列化与合同复验；合同不是字段白名单，Organ `key_metrics`另使用逐agent显式字段及数值/布尔allowlist。Transport固定五条路径、禁代理/重定向、限制1 MiB，并以一个monotonic总deadline覆盖DNS解析、连接、响应头和分块正文。真实loopback已覆盖五来源传输，但不代表部署长期健康。

Host source closure只在ASGI lifespan内预热、捕获并在finally清空；它底层仍读取`get_ledger/get_capability_* / get_semantic_recaller/require_process_tribe_runtime`等process authority，所以不是多Host全对象图隔离。Capability Forge durable proposal由ledger直接执行newest-per-proposal查询。缺token、Host或有效证据时相关文档保持`SOURCE_UNAVAILABLE`；mode history只在当前Host进程内，Organ只是allowlist后的瞬态证据而非Life authority。D7b的`LifeBodySourceReader`不读取这些Host closure，而是聚合同一app已物化的四个公开root和`LifeBodyQueryRepository`有界lineage；两app两SQLite、Host ledger关闭后的冷读、root missing/invalid/stale及content/private字段不落盘均有回归。Ledger-stats按公开时间列与durable行身份倒序读取最近5001行并物化最新5000行；`truncated`只标记更旧尾部，不再使高流量表永久503。D6 Memory/VTS冷读与0103边界继续有效；压缩率、蒸馏失败率和Edge物理回执覆盖仍明确不可测。callback仍不得调用`process_services`。

D7 静态扫描现有 `obs_routes*.py` 时，`process_services` 与 `_get_ledger` 的直接词法引用均为0；这个数字只描述该文件集合，不是全仓Service Locator计数。第三个 NE-01 清理切片又删除 `obs_routes_task_runtime`、`health_device_projection`、`process_state_projection`、`projection_runtime` 与 `session_query` 中无生产调用者的零参数 refresher/locator import。Tool/MCP 切片删除 ASGI 对 process-owned MCP Broker 的取用并从 ASGI/http_hooks 退役 Tool seed/route locator；Host lifecycle 切片原始快照又把具名 `from host_brain.composition.process_services import ...` 从47/152降到45/124。合并两项删除后的 fresh AST 当前值为45 modules/123 imported names，计入全部 import 形式仍有51 consumer modules；历史的47/147和45/124分别绑定各自基线，不能冒充 combined current。`capability_stats_projection`、`domain_state_projection`、`projection_ledger_view`、`remaining_query_projection`已删除全局兼容reader/factory/source fallback，生产保留的是显式`*_from_store` reader和由composition传入store/ledger/source的job。Host的`/v1/digital-life/world/snapshot`、`/health/canon_sync`、`/startup`、`/v1/memory/context/debug`、`/v1/memory/health`、`/v1/memory/events/recent`从Host lifespan持有的Query Projection Store读取；mode service经`HostRoutePorts`，Life action-preview的trusted context/token validator经`ObsRoutePorts`注入，缺端口失败关闭，preview仍不执行动作。没有挂载消费者的core ledger compatibility handlers已删除，不保留伪route port。Host app factory强制接收typed `ObsRoutePorts`；Process/Tribe全局ports不再捕获特定app closure。模块级daemon与导入期注册继续保持删除，app-local `ProjectionJobDaemon`保留。

D7b还把Direct Skill worker的resource manager、episodic store和semantic recaller从执行期全局getter改为Host lifespan捕获的实例快照；两个Governed runtime交错执行、关闭其中一个以及全局poison替换都不能改变另一个端口绑定。Durable node grant与child intent先验证，随后按Skill语义预检所需后端；缺依赖返回稳定失败，不进入低层executor，也不生成accepted或成功Skill receipt。Life Body把prompt injection/high-risk以及Gateway/capability风险映射为病理、免疫状态和诊断动作；根事实未变时沿用source identity，仅刷新freshness。D7c又把`obs_application.py`和`obs_projection_jobs.py`移入app层，NLE composition→app违规从85降为0且没有新增allowlist。这个证明只覆盖Direct Skill和Obs assembly：Chat/Delegate的dispatcher、`PROCESS_LIFE_PROVIDER`、Host source底层authority和其余Worker对象图仍未迁移。

Host lifecycle 切片进一步以 `HostLifecycleContainer` 统一同一 ASGI app 的 monitor/resource/mode owner。只有 factory/start 全部成功才发布；Activity factory 失败不发布 Mode；`aclose()` 先设置 closing fence，关闭期间新 `begin()` 必须失败；ResourceManager startup sample 进入其唯一 owned loop thread。`http_hooks.py` 不再直接导入 `process_services`；Chat/Input、Tool、Scheduler、Guardian heartbeat 与 lifecycle snapshot 由 composition 读取选中的 app-local provider，Obs runtime snapshot 只读已发布 monitor，缺失时返回 unavailable 而不懒启。Tribe application ports 在 Host lifespan 内通过 process-global exact owner bind/unbind fence 绑定；同一进程仅允许一个 live Host，第二个 Host fail closed，顺序 unbind 后可重新绑定，但并发 multi-app Tribe 隔离不受支持。全局 `set_activity_monitor` / `set_resource_manager` 及 2 slot + 2 lock 已删除。该切片原始 commit-bound 清单从 named47/152降为45/124，all-form consumer modules从52降为51，Container为56/4/51；合并Tool/MCP删除后当前精确值为named45/123、all-form51、Container55 fields/4 locks/50 mutable refs。这仍只是 Host lifecycle cluster，不覆盖 process-wide locator、heartbeat cache/client、HardState、Tribe bus、shared resource status file、真实Tool/MCP executor或完整 Worker scope。

D8 把 Host 具身 authority 收口为 ASGI lifespan 的 `HostEmbodimentRuntime`，但“app-owned”不表示同库可并行：ASGI 必须先通过 Task Runtime migration gate，再构造依赖 schema 的 Life authority 和 Query Projection。0104 令 outbox 新行绑定 canonical DB 派生的不可变 `runtime_scope_id`，同 scope 的 durable runtime lease/fence 同时只授予一个进程，第二实例在取得 authority 时失败关闭，历史无 scope 行也不会被新实例猜测 claim。Host 在当前 runtime fence 下持久保存精确 Edge `session/owner/lease/fence`，正常替代进程以同身份重启时必须先轮换 live Edge fence；旧 Host fence 不能更新该 credential。NE-03 在这个持久队列前增加 terminal ActionPreview 重开：当前只有携带 exact proof 的 Life VTS/MateEngine 与 Gateway/Edge 出站可继续入队，旧 reply-performance、Gateway voice、chat cue 和其他没有派生证明的直接调用失败关闭，尚未完成正向迁移。合法新入队报告 `PENDING / DURABLY_QUEUED`，精确重复回放既有状态，只有既有 `APPLIED` receipt 才可令 `physical_effect_verified=true`，`voice_started/vts_started` 仍不能由 admission 推导。

Edge 0002 在 command admission 时冻结 owner；HMAC+nonce 的 read-only receipt query v2 在同一 authority 判断中校验 owner、current session、lease、fencing token 与 `command_id/idempotency_key`，legacy ownerless command 返回 `NOT_FOUND`。`FOUND` 才返回精确 durable receipt，`NOT_FOUND` 不泄漏 receipt 且不会执行命令。Host 对过期 `DISPATCHING` 先转 `IN_DOUBT`，随后只读查询；只有 identity+receipt-digest CAS 命中才落入已知结果，相同 digest 重查不重复推进状态或 observer，`NOT_FOUND`、传输或校验故障保持不确定，绝不重放。非 loopback HTTP 在建立 client 前拒绝，远端必须 HTTPS。真实 child Edge 的 loopback TCP/HMAC/SQLite、receipt query 与重启 exact replay 已有测试；minimal RPC profile 故意没有 MateEngine/设备，物理效果仍 `NOT_TESTED`。ReAct 已不再发现或执行嵌套 `run_skill`/`dispatch_sub_agent`；旧 ToolSpec 被注入时也在 low-level executor 前失败。Operator 当前只有 `scheduler.task.trigger` 进入 governed runtime，36/37 仍阻断。HTTP `/tools/execute` 已由 HMAC TOOL Secure Ingress 接入 HostCommand、immutable TaskGraph、durable Review/一次性 resume、node grant、Budget/Attempt/Lease、exact Tool binding、app-local governed execution port、Verification/Acceptance/Receipt；0114 令 canonical Worker Projection 接受 `tool` route。真实 SQLite 集成只用显式 `SAFE_LOCAL_REPLAY` 测试 adapter 证明 core chain，生产 seed 因没有 workspace-bound executor 和一般 reconciliation adapter而全部 fail closed，health 为 `DEGRADED / FAIL_CLOSED_NO_GOVERNED_EXECUTOR`。原始 `ToolBroker.execute()` 与 `execute_bound()` 都固定拒绝；Frozen Tool Worker 先在单个 SQLite 读事务内原子复验 Graph/Node/Attempt/Lease/Reservation 与 owner/fence/expiry，再分阶段复验 node grant 和 exact binding，全部通过后 `before_handler` 才写 `PENDING` marker；三段不是同一原子区间。`tools.MCPAdapter` 只同步元数据并固定拒绝 direct call；process-owned accessor/slot 已删除，兼容 `MCPToolBroker` 不保留 CapabilityBroker、Token 或 handler；MCP Skill bridge 默认关闭并清 stale `mcp::*`。只读 registry/scorecard 保留，但真实 MCP provider 尚未编译为 governed Tool node。普通 ReAct 的 `GovernedToolNode → CapabilityBroker` 是独立待审路线，本切片不能外推为全部 Tool authority 已清零。Tool seed readiness 早于 runtime activation 的 P2 仍未修复。合并后 `ProcessServiceContainer` 为55 fields/4 locks/50 mutable refs；剩余 legacy direct embodiment/Tool adapter、真实 governed Tool/MCP executor、完整 Host/Worker 对象图和全局 Service Locator 删除均未完成。因此 D8 不是完整 Query Plane、Composition 或 NE-01完成。

Edge session/owner 不是默认的进程字符串：Host 从 normalized canonical DB path 的稳定 hash 派生二者。同一 durable store 的替代进程先取得新的 Host runtime fence，再读取持久化的精确 Edge credential，以原 identity 重连并查询历史 receipt；不同 store 有不同 identity，不会彼此争用默认 lease；两个 app 指向同一 store 时只有 durable Host lease winner 能访问该 Edge authority，另一个失败关闭。

所以当前更准确的说法是“集中式 Service Locator”，不是“显式 Composition Root 已完成”。

## 8. 长期演化资产

| 目标能力 | 已有资产 | 当前限制 |
|---|---|---|
| World Model | 0111、`persistence/world_observation_journal.py`、`runtime/world_observation_application.py`、`runtime/world_resolver.py` | Host lifespan 中的服务端 observation application 以 lease/source authority 追加事实，Resolver 重开 captured head；输入仍依赖既有 Bond/Emotion 投影，也仍不是预测/规划模型 |
| Scene Policy | 0112、`runtime/scene_resolver.py`、`runtime/context_application.py` | Scene 由重开的 World + app-local Guardian 推导，Surface/Proactive/Task admission 冻结同一 identity；旧 `scene_policy.py` 定义仍保留但无这三处生产构造调用 |
| Intent Arbiter | `IntentFrameV1`、`runtime/production_intent_authority.py`、`runtime/intent_admission.py` | 生产 adapter 重开 graph/context/action/grant/policy，TaskGraph admission 持久化节点/graph receipt set；只收紧不授权，长周期目标 owner 仍缺，Preview 待最终 Gate |
| Action Preview | `runtime/action_preview_authority.py`、`runtime/action_preview_resolver.py`、执行/terminal front doors | 重开 Command/Task/Attempt/Lease/Grant/Context/Scene/Guardian 与 Acceptance claim；derived terminal 递归重开 origin Attempt/Lease，descriptor 只收紧；Acceptance Gateway 仅允许 exact minimal text，拒绝未绑定 `raw_extra`/媒体/二次变换；公开具身 submit/enqueue 也必须 exact proof。旧任务/effect、37 个 Operator 直写、reply-performance/Gateway voice/Avatar 直通失败关闭，后三者待 NE-01 正向迁移 |
| Obs Preview / Shadow | `governor/action_preview.py`、Obs route | Obs 端点仍是单独的 dry-run eligibility 视图，固定 `would_execute=false`；它不替代 Host lifespan 的 canonical authority，也没有统一 shadow runner |
| Capability Maturity | scorecard/provider/Governor adapter、production composition fail-closed wiring | production IntentDecisionEngine 默认启用 restrict-only policy；缺 durable promotion/degradation lifecycle |
| Capability Forge | `app/capability_forge.py` | 仍只产生 proposal/gap candidate；不得绕过 Lab/Review/0109 lifecycle，Skill Compiler 仍只产 SkillDraft |
| Evolution Lab | 0108 V1 ledger、self-attested runner | 有隔离合同和账本，但 dataset/verifier/effect 证据不可信且无生产 composition；V1 永久不具备 promotion eligibility |
| Feedback | response/persona/recommendation feedback + 0110 proposal ledger | `FeedbackProposalV1` 已可持久化 STAGED/APPLIED/ROLLED_BACK；Prefrontal 参数漂移已修复，旧增长 caller 尚未全部转为 proposal adapter |
| Persona Growth | `persona_growth.py`、Bond stage、Evolution Orchestrator、emergent growth skill | legacy 入口已被 `PersonaEngine` 中央围栏和静态扫描封禁；当前只产生经 drift guard 检查的无副作用 candidate，尚未接入 proposal/Review/版本激活 |
| Knowledge OS | `KnowledgeEntryV1`、0110 namespace event ledger | active winner、cursor、supersedes/deprecation/dedup/conflict resolution 与生产 retrieval 均未接线 |
| Identity | `release/identity_snapshot.py`、`IdentityMigrationPlanV1`、0110 event ledger | ACTIVE/ROLLED_BACK 仅是事件名；现有 snapshot owner 未切换，exact previous identity 未恢复 |
| Runtime Guardian / Catalog | 0112、0113、`runtime/runtime_guardian.py`、`service_catalog/host_startup.py` | Host lifespan 绑定 runtime lease/fence；fresh DB 逐级启动五阶段并保留 ENFORCE 候选；commit-bound 本地 Gate 与 crash takeover 已通过，显式 legacy adapter、Windows graceful stop 与部署多实例 canary 仍未收口 |
| Workspace Isolation | `agent_runtime/workspace_paths.py`、`agent_runtime/workspace_policy.py`、`agent_runtime/workspace_allocator.py`、`agent_runtime/workspace_runtime.py`、`composition/agent_workspace.py`、`persistence/workspace_repository.py`、`persistence/workspace_operation_fencing.py` | D0/W0/W1 继续生效。W2 为认证 Internal Agent 写 admission 派生 graph workspace，显式配置 secret 后冻结 exact source commit并创建独立 Git worktree；实例/PID lease、同 workspace 独占 scope、heartbeat、顺序 handoff、过期 takeover、cleanup receipt、启动同步 1 条+后台 pending continuation 与 ASGI quiescence 已接线。focused fencing 模块只让 provisioning/allocator/cleanup adapter 的外部 Git/filesystem callback 由 workspace/source 锁串行，在共享 deadline 内运行并在前后 exact authority 短事务间完成，不持有 SQLite writer；后验漂移只返回失败。Agent tool 只在动作间协作取消，Verification 只在整体启动前验活；二者没有逐操作 fence或强杀。默认缺 secret 时写入 fail closed，canary 未跑。状态仅为 `PARTIAL_W2_INTERNAL_GIT_WORKTREE_WIRED`，且没有 promotion/abandon、accepted checkpoint HEAD、Domain/Skill 接线、OS/container/tenant 与 handle-level TOCTOU 防护 |

### NE-06 生产证据链状态

0110 `evolution_governance_ledger` 把 Feedback、Knowledge、Identity 的 proposal 与事件行分开持久化，`PrefrontalAgent` 也已改用有界 interaction_stats 调用 `PersonaGrowthTracker`。复审确认这只是 V1 ledger：Lab/Review/regression URI 未查询真实 authority，状态机允许非法顺序，分页和 repeated release identity 不完整，APPLIED/ACTIVE/ROLLED_BACK 都没有修改真实 owner。它不能作为生产激活或 exact rollback 证据。

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
6. 删除全局 Service Locator，建立 Host/Obs/Worker 显式 composition scopes；把 D8 未覆盖的 legacy direct embodiment adapter 迁入 app-owned runtime，并在真实设备环境单独验证物理效果，不能把 queued ack 或 Edge SQLite receipt 当作设备成功。
7. 为 Persona 的无副作用 candidate 保持中央 fail-close，NE-06 再接入 proposal、Review、版本激活与 rollback，不恢复直接 writer。
8. 让 PlanningSearch 从真实目标生成可竞争后继，并以 Research/Code production shadow 证明收益。
9. 在上述执行地基闭环后，再推进 Evolution Lab、Skill Compiler、Knowledge OS 与 Identity Migration。

这些缺口的唯一活动执行顺序见 [NEXT_GENERATION_EVOLUTION_EXECUTION.md](../../30_devspec/NEXT_GENERATION_EVOLUTION_EXECUTION.md)。
