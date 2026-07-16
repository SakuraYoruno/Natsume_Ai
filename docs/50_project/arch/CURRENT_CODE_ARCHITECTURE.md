受众：MASTER
标题：Natsume AI 当前代码架构事实

# Natsume AI 当前代码架构事实

版本：v5.6
核对日期：2026-07-16
代码基线：`origin/dev@a6d7ae383`，叠加仓库清理 `f0cd1dfd`
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
| 原生多节点 Durable TaskGraph | `PARTIAL_B5C_DURABLE_CANCELLATION_SHADOW / PRODUCTION_OFF` | 0091 已封口整图成功；0092 新增 exact-revision verified internal cancellation、两阶段 intent/fence/reconciliation/final decision 和 first-writer 竞争语义。accepted NODE effect 继续 delivery，未授权 effect 被 fence。生产 composition 未注入 verifier，且无 HTTP/UI 入口、跨进程自动 reconciliation/takeover/continuation 或 canary |
| 通用 PlanningSearch 内核 | `AVAILABLE` | 领域无关 contracts、ports、L1/L2/L3 算法存在 |
| Host 智能候选规划 | `NOT_IMPLEMENTED` | Host Plan 只对已批准 `planned_steps` 做拓扑合法化，不生成竞争方案 |
| Pentest L3 | `WIRED_SHADOW_UNCALIBRATED` | Conditional MCTS 可运行，但校准非 durable，不能证明 production uplift |
| Code/Research 收益 | `SIMULATION_ONLY` | 只有冻结模拟 fixture；没有真实仓库、真实来源或真实模型 shadow |
| Budget | `CORE_INGRESS_WIRED / PROGRAM_PARTIAL` | Host 四路线、Scheduler 与 Internal 有 durable ledger；并发预算与 DEGRADE/REVIEW 策略未完整执行 |
| Review/Resume | `MANUAL_CONTINUATION` | token 与 checkpoint 已持久化；resume 不会自动重新调度原图 |
| 对话副作用 | `POST_ACCEPTANCE_EVENTUAL` | Acceptance 原子写 effect intents；user/assistant/delivery 实际应用不是一个全局事务 |
| Composition | `INCOMPLETE` | 两批已删除 6 个零消费者槽位和 4 个死 accessor，并收掉第二批 7 个宽泛重导出；排除 6 个 lock 与 `application_ports` 后，模块级 `ProcessServiceContainer` 仍有 53 个可变服务引用及大量 locator |
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
- `host_brain/task_runtime/coordinator.py`
- `host_brain/task_runtime/coordinator_records.py`
- `host_brain/task_runtime/coordinator_receipts.py`
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

迁移 0089 只在 Node 持久化 `retry_not_before_ms`；attempt 次数始终由 Attempt journal 的连续 `attempt_no` 派生，避免第二真相。Coordinator 现在只在 worker resolution/preparation 抛出显式 `RetryableWorkerPreparationError` 且尚无 operation reservation 时调用 `schedule_retry`；retry_count BudgetUsage、Budget、Attempt、Node、Lease 与 Outbox 在同一 `BEGIN IMMEDIATE` 内提交。预算允许时节点进入 `RETRY_PENDING` 并在到期后创建 attempt 2；retry_count 超限时进入 pre-reservation terminal failure 收口。reservation 创建后的异常从不进入自动 retry。

上述 safe-retry 是已接通并由真实 Coordinator fault injection 验证的 contract path。若 `schedule_retry` 已提交但响应丢失，同一确定性调用会严格校验 Attempt、Node、Lease、Graph、Budget、BudgetUsage 与关联 Outbox 后返回原决定，不重复扣减 retry budget；任一状态漂移都会 fail closed。resolver 的 `CancelledError` 不会被普通失败分支吞掉。当前三个生产 resolver（Host registry、Scheduler、Internal）都不会抛 `RetryableWorkerPreparationError`，因此不能写成现实瞬态故障已经自动分类。

普通或重试耗尽的 pre-reservation resolver failure 现在调用 `DurableTaskStore.fail_running_attempt_bundle`：在同一个 `BEGIN IMMEDIATE` 中把 Attempt、Node、Graph、Lease 分别落为 `FAILED`、`FAILED`、`FAILED/BLOCKED`、`RELEASED`，并生成四条同时绑定 attempt 与错误证据的 Outbox。相同调用在提交响应丢失后可精确重放；旧 fence、版本/证据/Outbox 漂移或该 attempt 已存在任意 operation reservation 时一律拒绝。Budget finalize 仍发生在 bundle 之外，因此它不是 Attempt/Node/Graph/Lease/Budget 五者全原子的终态事务。

operation reservation 建立后，Coordinator 会创建 `DurableExecutionHeartbeat`。后台循环严格先续 Lease、再续 Reservation，Worker 的每个流事件和最终结果都经过 checkpoint；停止并 join 心跳后才允许使用最新 Reservation version 执行 COMMIT。Lease 与 Reservation 续租均写版本化 Outbox，并支持同参调用在“事务已提交但响应丢失”时精确重放。任何续租失败、版本漂移、worker 自发取消或心跳生命周期异常都会产生 `RECOVERY_REQUIRED` 收据，Coordinator 不会 COMMIT Worker 结果；bounded fan-out 会立即停批并 join siblings，被中断的 sibling 可留下 `RUNNING` recovery obligation，后续重入继续返回既有 `RECOVERY_REQUIRED`。这个语义只建立 fail-closed 保护，不会在同一进程内自动 takeover 或即时完成 reconciliation。

Coordinator 现在有两个独立实验能力：`atomic_graph_success_enabled` 决定进程是否能执行 frozen atomic policy，`bounded_fanout_enabled` 决定 scheduler 多节点批次是否实际并发；二者构造默认值均为 `false`，Host、Scheduler、Internal 三处生产 composition 均未传入启用值。Application 在 `repository.admit` 前检查 atomic capability、node-grant service 与 parent grant，任一缺失即拒绝且不写 durable admission。Coordinator 再要求 `node_grant_refs` 精确覆盖整个 admitted Graph。atomic 图即使是单节点或顺序执行也使用 deferred child + outer terminal authority；terminal-leaf 实际并行还必须另开 bounded fan-out。progress buffer/控制槽的反压语义保持 B4 合同。

Coordinator 在任何 Budget activation 或 worker resolution 前调用 `assert_parallel_settlement_decision_ready()`；`0090` 缺表/缺列会以 schema-not-ready fail-fast。B5 成功开关实际生效时还会先调用 `assert_parallel_success_decision_ready()`，要求迁移 0091 完整可用。325 行的 Store-only `ParallelSettlementProjection` 读取 scheduler snapshot、Node 与 Attempt，负责 active re-entry、rejected Budget outcome 和 outcome snapshot 的严格校验；649 行的 `ParallelSettlementRuntime` 共享原 Store、BudgetRuntime 与 RuntimeOutbox，负责 outcome resolution、ownership/CAS conflict 判定和 settlement orchestration。对于 `ACTIVE` 多节点重入，若 durable snapshot 仍含 live Node、active Attempt 或 `ACTIVE` Lease，入口返回 `GOVERNED_EXECUTION_IN_PROGRESS`，不会 resolve 新 worker、改变 Attempt/Lease，也不会抢现有 owner 的 execution authority。结算窗口遇到 ownership fence，或 CAS 后重读发现 active Node/Attempt/Lease，也返回 `IN_PROGRESS`；纯 CAS 且没有 active ownership 事实仍转为 `GOVERNED_DURABLE_STATE_CONFLICT`。

并行 child 使用 deferred shared-terminal 模式：它仍独占自己的 Attempt、Node、Lease、Reservation，并可记录 Budget usage 或把预算标成 `EXCEEDED`，但不得终结共享 Graph，也不得把 Budget 置为 `RELEASED/CONSUMED`。因此普通的 node-local 非接受不会 fail-fast cancel sibling；outer 会等待所有已启动 sibling 到达 durable node-local terminal，再按固定优先级解释整批结果。所有 Coordinator 与 cancellation runtime 发布先经过每个 Coordinator 唯一的 `RuntimeOutbox` asyncio lock；同进程 Host/Internal/Scheduler 共享的 `RuntimeOutboxPublisher` 又以实例级 `threading.Lock` 串行化 list/append/mark drain。跨线程合同用两个 worker 证明共享 publisher 的 peak active drain 为 1，但该锁不覆盖多进程。

live 与 crash/re-entry 不再从瞬时 receipt 拼不同决策：ParallelSettlementProjection/Runtime 从 durable Node、Attempt、Reservation、BudgetUsage 重建完全相同的 canonical `Natsume.task_runtime.parallel_settlement.v2` payload/ref，最终仍由 DurableTaskStore 在唯一 UoW 中提交 Graph、可选 Budget、Outbox 和 decision row；response-loss 只 exact replay。为保证跨批次重入 digest 稳定，outcomes 从整个 admitted Graph 的 durable terminal Nodes 和每节点 latest stable Attempt 全量重建，不只当前 claimable batch；当前 batch 只决定优先级与 selected。`RETRY_PENDING` 是合法的非 selected durable outcome，也可由重入恢复。BUDGET selected `BUDGET_BLOCKED` 绑定 rejected usage；共享超额后的被动 blocked sibling 不要求本地 usage。每次读取 decision row 都验证 digest、精确 Graph/可选 Budget Outbox 与 Attempt lineage，BUDGET 再验证 usage；terminal replay 另校验 Graph target/version，并要求 selected outcome 的 Attempt 仍为该节点 current latest。结构拆分没有把 0091 success 或 0092 cancellation authority并入 0090 Runtime。

合同/集成测试覆盖：live 首次构造与 crash/re-entry 重构得到同一 payload/ref、提交响应丢失 exact replay、较早批次 terminal Node 被纳入后续 decision、`RETRY_PENDING` sibling 作为非 selected outcome、Budget selected usage 与被动 blocked sibling 分离、newer selected Attempt replay rejection，以及 decision row/marker corruption fail-closed。

B5 terminal success 不复用 0090 的 failure decision。每个 Accepted 节点必须拥有 latest `SUCCEEDED` Attempt、已 `COMMITTED` 的 operation Reservation receipt，以及由 `WorkerProjectionStore` 冻结的 `Natsume.task_runtime.worker_projection.v1` 投影。读取后重新 canonicalize 并重算 digest，精确校验 command/request、graph/revision、node/attempt、route/target、receipt、node grant 与 `APPROVE`；仅凭一条看似合法的 digest 字符串不能通过。只有 `result_node_id` 对应投影会编译整图 effects 并成为公开 projection；其他 Accepted 叶只提供 outcome/evidence。Skill 同样不能以瞬时 executor 返回值争夺结果 authority。

`DurableTaskStore.complete_parallel_graph_success()` 是 B5 唯一成功 mutation authority。它在同一个 `BEGIN IMMEDIATE` 中将 Graph `ACTIVE → COMPLETED`、共享 Budget `ACTIVE → CONSUMED`，写入不同的 Graph/Budget Outbox、全部 `GRAPH` scope effect intents，以及迁移 0091 的 `Natsume.task_runtime.parallel_success.v1` decision row。decision 使用 `tasksuccess://sha256/...` 引用并绑定 completion policy、所有 Node status/version、Accepted outcome、显式结果 Attempt/receipt、projection digest、effect identity 和两条 Outbox；任何晚故障整体回滚。已完成图的 replay 只读取并复核 0091，不重新编译 effects；若第一次提交和第二次 exact replay 的响应都丢失，Coordinator 重读 Graph terminal authority 后进入同一 completed replay 分支。

对抗验证包含单节点 atomic、串行 atomic、多 terminal roots 反向完成顺序、flag-off 与缺 node-grant service 在 admission 前拒绝、projection narrative/command/grant 篡改、restart after accepted、completed restart 不重编 effects、一次和连续两次 Store 响应丢失。它证明 shadow contract 自洽，不等于生产 canary。

历史 marker 校验存在一个真实 schema 边界：旧 TaskGraph Outbox 只有 `aggregate_id=graph_id`，没有 `graph_revision`。因此完整扫描 `taskbatch` marker 的缺 decision row/错绑只在该 graph ID 仅一个 revision 时安全；多 revision 读取仍会对返回 decision row 的 digest、Outbox、Attempt、usage 做精确校验，但不能完整判断其他 revision 历史 marker 是否缺行。要封闭此缺口，需要 revisioned TaskGraph Outbox aggregate identity。

B5C 在其上新增迁移 0092 和两阶段 Graph cancellation。Application 的内部入口必须通过 authority verifier 将 request、principal、source、reason 绑定到 exact durable Graph revision；当前生产 composition 没有注入 verifier，也没有 HTTP/UI 暴露，所以默认 fail closed。Store phase 1 先持久化 intent，将 Graph 置为 `BLOCKED`，再取消/围栏 Node、Attempt、Lease、Review/token、operation 与未授权 effect；`GraphCancellationRuntime` 只有在该提交后才通过共享 registry cancel/join 本进程 child/heartbeat。execute re-entry 由 Coordinator 薄委托该 Runtime，从 durable intent 继续 advance/finalize；只读 `GraphCancellationProjection` 只持 Store，本地 task registry 不构成 authority。

final cancellation 只有在 active ownership 与 operation/effect/review 不确定性全部 reconciliation 后才以一个 UoW 提交 Graph `CANCELLED`、Budget 释放/取消、Outbox 和 canonical decision。operation reconciliation proof 在动态 evidence 构造和终态 getter 中都会重验结构、digest、key 与签名；同 revision 的 Review create/approve/resume 在事务内被 0092 intent fence。`ACCEPTED` Node + `SUCCEEDED` Attempt 已确认的 NODE effect 保留 delivery authority；其他 NODE effect 与未完成 Graph 的 GRAPH effect 被 fence。0091 success 与 0092 cancellation first-writer-wins；请求响应丢失只允许四字段 authority 完全一致的 exact replay，publisher 故障不会阻止 durable intent 后的本地 cancel/join。基础设施 shutdown/disconnect cancellation 不会自动写成业务取消。

该路径仍不能默认启用。当前定级是 `PARTIAL_B5C_DURABLE_CANCELLATION_SHADOW / PRODUCTION_OFF`：B5C 合同、Store 和重入路径已有证据，但生产 authority provider/受控入口/canary 与跨进程自动 reconciliation/takeover/continuation 未完成。B4/0090、B5/0091 语义继续有效；revisioned 0090 marker identity、跨进程 Outbox claim、Budget 高竞争、历史半提交和统一 `WAITING_APPROVAL` 仍是缺口。

执行层的代码结构门禁仍为红，但已有十四刀真实下降。TaskStore 主文件已满足既有 ceiling；Coordinator 的 records/receipts/cancellation payload helpers 是无 authority 的纯叶子模块。`RuntimeOutbox` 独占每个 Coordinator 的 publisher/factory/limit/发布锁，Coordinator、GraphCancellationRuntime 与 ParallelSettlementRuntime 的联合矩阵仍为 27 个 build、31 个 publish、58 个调用和 5 个 best-effort publish；`LocalGraphExecutionRegistry` 独占本地 task map/锁并以 `track()` 覆盖完整 stream，取消在锁外 bounded wait/gather。615 行的 `GraphCancellationRuntime` 与 286 行的只读 `GraphCancellationProjection` 保持冻结边界；325 行的 Store-only `ParallelSettlementProjection` 和 649 行的 `ParallelSettlementRuntime` 已接管 0090 settlement read/validation 与 runtime orchestration，TaskStore 仍是唯一 UoW mutation authority。四个服务均不反向持有 Coordinator 或 callback，也没有新增锁。`coordinator.py` 从 5,860 降到 3,808 行。当前 Gate 唯一静态问题仍是该文件未分类且超过 1.5k；不得提高 allowlist 上限，下一阶段继续拆 node lifecycle 与 DAG scheduling。Retry promotion 和 claim 仍是两个事务，这些 stateful leaf 也不提供跨进程 Outbox claim、pending-intent scanner、takeover 或 continuation。

Schema readiness 在 Budget 写入前检查，retry schedule/promotion 仍同事务校验 ACTIVE graph/graph-level Budget，backoff 节点可原子取消。若 0089 的物理事务已提交但 migration receipt 丢失，runner 只有在冻结 checksum、列约束与完整索引形状全部成立时才补记 receipt，否则 fail closed。

`ProcessTaskRuntimeContainer` 的生产启动顺序现为 migration → `task_store.recover_startup` → publisher 构造 → recovery outbox publish → 稍后 runtime activation；恢复抛错会在开放路由前终止构造。ASGI 通过 `execution_owner_id()` 把配置逻辑 owner 与当前 container 的 `recovery_id` 摘要绑定，得到每次构造/启动唯一且不泄漏原 recovery id 的 execution owner。该接线保证启动扫描及恢复事件先于 admission，也避免新进程复用旧进程 owner 身份；它不代表在线 takeover、reconciliation completion 或自动 continuation 已完成。

仍缺：

- B5C durable cancellation 仍是 production-off shadow；0092、verified internal entry、两阶段 fence/final decision 已实现，但生产 verifier/入口/canary 未接线；
- 历史 `BLOCKED + failure Node` 或 `FAILED + ACTIVE Budget` 半提交状态仍待 reconciliation；`WAITING_APPROVAL` 仍非统一 optional-attempt observation；
- legacy TaskGraph Outbox 缺 `graph_revision`，多 revision 的完整 `taskbatch` marker 历史扫描仍待 revisioned Outbox identity；
- execute re-entry 可继续既有 durable cancellation intent，但没有跨进程自动 scanner/worker 去完成 reconciliation、takeover 与 continuation；
- 同进程共享 publisher 已串行，跨进程 Outbox drain 仍缺 durable claim/CAS 或单 writer；
- 生产 resolver 尚未把现实瞬态准备故障映射到显式 marker；promotion 与 claim 也不是同一决策事务；
- Budget 并发 CAS 仍有固定重试上限与 `ALREADY_EXCEEDED` 语义缺口；reservation 后 worker/commit 失败的即时 reconciliation 也未闭环；
- 周期 Lease/Reservation heartbeat 已接，但仍缺 mid-attempt 在线 takeover、自动 continuation；长等待后的 grant renewal/derivation 未闭环。

### 4.2 Budget

`host_brain/task_runtime/budget_runtime.py` 和 TaskStore usage ledger 已在 Host 四路线、Scheduler 与 Internal Agent 执行预扣、计量和结算。真实计量覆盖时间、dispatch、显式 provider token/cost、ModelAttempt retry 与工具步数。

限制：

- provider 未返回的 token/cost 保持 `UNMEASURED`，不做伪精确估算；
- concurrency 字段没有运行时 charge；
- 超限后的 `DEGRADE`、`REVIEW` 尚未形成完整分支，当前核心路径主要阻断；
- Scheduler 与 Internal Agent 已进入该主链；其 program-wide Budget 策略仍受本节其余限制。

### 4.3 Review/Resume

`ReviewStore`、一次性 token、subject/version/checkpoint 绑定以及 PRE/POST review 状态均存在。Host coordinator 能进入 `WAITING_APPROVAL` 与 `WAITING_ACCEPTANCE_REVIEW`。

当前 resume API 只消费 token 并返回 continuation 信息，不会自动重新调度原 TaskGraph；Operator UI 也明确显示 `automatic_resume=false`。

### 4.4 Conversation 与 Delivery

canonical route 在 graph Acceptance 时原子写入 effect intents，避免 admission 前先落 user turn。之后 user turn、Life、assistant/Memory projection 与 delivery 由 durable dispatcher 分别提交和重试。因此正确语义是“Acceptance 后最终一致”，不是“所有对话与投递在一个数据库事务中原子完成”。

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
| Review | `ReviewStore` | token 持久化，continuation 非自动 |
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
| Workspace Isolation | `agent_runtime/workspace_paths.py`、`agent_runtime/workspace_policy.py` | D0 canonical resolver 已封闭受管文件 I/O/list、截图、Verification cwd/argv 与 git cwd 的绝对路径、遍历、同前缀兄弟目录和已解析 link 逃逸；仍允许任意 workspace path/`runtime_repo`，无 durable owner，也不是 OS/tenant/worktree 强隔离；校验到打开之间仍有 handle-level TOCTOU 风险 |

## 9. 仓库边界

- 顶层 `data/`、`flows/`、`skills/`、`integrations/` 已删除。
- 可变运行数据进入 `runtime/`，测试/验收输出进入被忽略的 `artifacts/`。
- 内置 Skill 代码保留在 `host_brain/skills/`；外部 bundle 只能经过签名接收与隔离，不能靠目录扫描激活。
- 跨进程合同保留在 `natsume_contracts/`。

## 10. 当前最高优先级缺口

1. 为 B5C 注入生产 authority provider 与受控入口，实现跨进程 pending-intent scanner/worker、reconciliation/takeover/continuation，并完成取消与 0091 success 竞争的生产故障矩阵；此前不启用 canary。
2. 将历史 `BLOCKED + failure Node` / `FAILED + ACTIVE Budget` 半提交修复归入 reconciliation，并把 `WAITING_APPROVAL` 收敛为统一 optional-attempt observation。
3. 为跨进程 Outbox drain 建立 durable claim/CAS 或单 writer；同进程 publisher 实例锁不能替代该 authority。
4. 为 safe-retry contract path 接入可证明无副作用的现实 resolver 瞬态来源，收敛 Budget CAS/`ALREADY_EXCEEDED` 并发语义，并完成 promotion+claim 单事务、reservation 后即时 reconciliation 与 mid-attempt online takeover。
5. 为长时间 Review continuation 明确 child-grant renewal/derivation 时机并自动重调度。
6. 删除全局 Service Locator，建立 Host/Obs/Worker 显式 composition scopes。
7. 为 Persona 的无副作用 candidate 保持中央 fail-close，NE-06 再接入 proposal、Review、版本激活与 rollback，不恢复直接 writer。
8. 让 PlanningSearch 从真实目标生成可竞争后继，并以 Research/Code production shadow 证明收益。
9. 在上述执行地基闭环后，再推进 Evolution Lab、Skill Compiler、Knowledge OS 与 Identity Migration。

这些缺口的唯一活动执行顺序见 [NEXT_GENERATION_EVOLUTION_EXECUTION.md](../../30_devspec/NEXT_GENERATION_EVOLUTION_EXECUTION.md)。
