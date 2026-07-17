受众：MASTER
标题：Natsume AI 数据流与回执架构事实

# Natsume AI 数据流与回执架构事实

版本：v1.8

核对日期：2026-07-16

文档类型：`CURRENT_DATA_FLOW / CODE_FACTS`

总体状态：`FULL_ARCHITECTURE_INCOMPLETE`

本文不用理想化的箭头代替生产代码，只描述当前 composition 真正能够触发的数据流、状态流、副作用流和回执边界。架构总览见 [CURRENT_CODE_ARCHITECTURE.md](CURRENT_CODE_ARCHITECTURE.md)，最终目标见 [TARGET_ARCHITECTURE.md](TARGET_ARCHITECTURE.md)，差距见 [ARCHITECTURE_GAP_MATRIX.md](ARCHITECTURE_GAP_MATRIX.md)，实施顺序见 [NEXT_GENERATION_EVOLUTION_EXECUTION.md](../../30_devspec/NEXT_GENERATION_EVOLUTION_EXECUTION.md)。

## 1. 如何读本文

本文对每条数据流使用四种定级：

| 定级 | 含义 | 可否当作当前生产事实 |
|---|---|---|
| `WIRED` | 入口、生产 composition、持久化与回执主链均已接通 | 可以，但仍受表内明示限制约束 |
| `PARTIAL` | 主要资产或部分路径存在，但未形成完整闭环 | 只能按已实现范围引用 |
| `BROKEN` | 表面有 adapter 或 lifecycle wiring，首个真实请求仍会在合同或 admission 处阻断 | 不能宣称可用 |
| `TARGET` | 只存在于目标架构或待实施合同 | 不是当前事实 |

三种容易混淆的语义必须分开：

1. **接受成功**：某个请求已被 inbox 或 Host TaskGraph 持久化，不等于外部副作用已发生。
2. **执行验收成功**：Graph 已通过 Verification/Acceptance，不等于 conversation、Life、Gateway 等后续 effect 已全部提交。
3. **外部效果确认**：Gateway/Edge adapter 已返回对应层级的确认证据。它不能由 Task receipt 或 HTTP 200 替代。

## 2. 全局数据流总览

```mermaid
flowchart TD
    UI["Chat / Host API"] --> Cortex["Cortex + Governor"]
    GW["Gateway 已验证输入"] --> Ingress["Host Secure Ingress"]
    Ingress --> Cortex

    Cortex --> Route{"route"}
    Route -->|chat| Chat["Frozen Chat node"]
    Route -->|skill| Skill["Frozen Skill node"]
    Route -->|delegate| Delegate["Frozen Delegate node"]
    Route -->|plan| Plan["已批准 steps 的拓扑合法化"]

    Chat --> Compile["HostCommand + WorkOrderV3 + TaskGraph"]
    Skill --> Compile
    Delegate --> Compile
    Plan --> Compile
    Compile --> PolicyGate["atomic policy capability + node-grant service\n+ parent grant：admission 前 fail-close"]
    PolicyGate --> Admission["HostCommandRepository 原子 admission"]
    Admission --> Schema["0090 + 0092；atomic 时再校验 0091\nBudget / worker 前 fail-fast"]
    Schema --> Runtime["Durable Task Runtime"]
    Runtime --> Worker["Attempt + Lease + Grant + Budget + Worker"]
    Runtime -.->|"atomic / bounded 独立默认 OFF\n三处生产 composition 未启用"| ShadowFanOut["atomic outer authority\nbounded 时 terminal-leaf 有界 child"]
    ShadowFanOut -.-> NodeLocal["child deferred authority\nAttempt/Node/Lease/Reservation\nBudget usage/EXCEEDED"]
    NodeLocal -.-> Worker
    Worker --> HB["Durable heartbeat\nLease → Reservation"]
    HB --> Checkpoint{"checkpoint 可提交?"}
    Checkpoint -->|是| VA["COMMIT → Verification + Acceptance"]
    Checkpoint -->|否| Recovery["RECOVERY_REQUIRED\n禁止 COMMIT"]
    VA -.-> DurableOutcome["node-local durable outcome\nreceipt ↔ latest Attempt/Node"]
    DurableOutcome -.-> OuterSettle["outer/live + ACTIVE re-entry\ndurable Node/latest Attempt outcomes\nrecovery > Budget > failure > review\n原子 Graph + 可选 Budget + Outbox + 0090 decision"]
    OuterSettle -.-> RevisionedOutbox["0095 Graph exact revision/payload\nID/key 保持升级前稳定\n歧义 producer 事务失败"]
    RevisionedOutbox -.-> RuntimeEvent["新 Event = graph_id@revision\nsource_ref 一 Outbox 一 Event\nlegacy 重投 exact replay"]
    DurableOutcome -.-> AtomicSuccess["atomic policy + 全 Node ACCEPTED\ncanonical projections + exact grant map\n显式 result projection"]
    AtomicSuccess -.-> SuccessUow["单 UoW：Graph COMPLETED + Budget CONSUMED\n两 Outbox + 全图 effect intents + 0091 decision"]
    SuccessUow -.-> RevisionedOutbox
    Runtime -.-> Reentry["crash/re-entry\nACTIVE 多节点 durable snapshot"]
    Reentry -.-> Idle{"live Node / active Attempt / ACTIVE Lease?"}
    Idle -->|"是"| InProgress["GOVERNED_EXECUTION_IN_PROGRESS\n不 resolve worker / 不抢 authority"]
    Idle -->|"否"| OuterSettle
    OuterSettle -.-> Window{"结算 ownership fence\n或 CAS 后重读 active ownership?"}
    Window -->|"是"| InProgress
    Window -->|"纯 CAS"| Conflict["GOVERNED_DURABLE_STATE_CONFLICT"]
    VA --> Receipt["Execution receipt"]
    VA --> Effects["Acceptance effect intents"]
    SuccessUow --> Receipt
    SuccessUow --> Effects
    Effects --> Conversation["Conversation / Life / Memory"]
    Effects --> Delivery["Gateway / Edge 副作用"]

    CancelUI["Task Runtime UI\n选择 exact graph revision"] -.-> CancelBff["同源 Operator BFF\ntask_graph.cancel"]
    CancelBff -.-> CancelGateway["Operator Command Gateway\nobs:write + task-runtime:cancel"]
    CancelOff["feature flag 默认 OFF\nENFORCE + Gateway 可用\n否则启动 fail closed"] -.-> CancelGateway
    CancelGateway -.-> CancelVerify["process-shared Secure Ingress verifier\ncommitted audit/claim + canonical body"]
    CancelVerify -.-> CancelRoute["immutable HostCommand surface\nHost / Internal / Scheduler"]
    CancelRoute -.-> CancelIntent["0092 phase 1 UoW\nintent + Graph BLOCKED + durable fences"]
    LocalRegistry["process-shared LocalGraphExecutionRegistry\nHost / Internal / Scheduler / 0094 worker\nquiescence only，非 durable authority"]
    Runtime -.-> LocalRegistry
    SchedulerWO -.-> LocalRegistry
    InternalWO -.-> LocalRegistry
    CancelIntent -.-> LocalJoin["intent 提交后才 cancel/join\n本进程 child + heartbeat"]
    LocalRegistry -.-> LocalJoin
    LocalJoin -.-> CancelReconcile["active ownership / operation / effect / review\nadvance + reconciliation gate"]
    CancelScan["0094 composition-owned scanner\nrecovery-scoped worker"] -.-> CancelClaim["协调 claim + heartbeat\nexpired takeover / stale fence"]
    CancelClaim -.-> CancelAdmission["HostCommandRepository exact revision\n重建 replay admission\nBEGIN IMMEDIATE 后采样 lease 时钟"]
    CancelAdmission -.-> LocalJoin
    CancelReconcile -.->|"本地 pending 非空或 durable facts 未收敛"| CancelDefer["0094 durable defer\n不伪造业务完成"]
    CancelDefer -.-> CancelScan
    CancelReconcile -.-> CancelFinal["phase 2 UoW\nGraph CANCELLED + Budget + Outbox + decision"]
    CancelFinal -.-> RevisionedOutbox
    CancelFinal -.-> CancelComplete["0092 FINALIZED + decision 后\n才清理 0094 协调行"]
    CancelFinal -.-> Receipt
    CancelRoute -.-> CancelFailure["下游异常后读取 exact durable intent\n无 intent=明确未执行；匹配=可能已执行\n不可判定则保守 uncertain"]

    StartupRecover["startup recover_startup"] -.-> TerminalInventory["0099 inventory：单次最多 10,000 terminal Graph\nappend-only case/proof/supersession；migration 不修业务行"]
    TerminalState["BLOCKED / FAILED / CANCELLED / COMPLETED\n+ unsettled Budget"] -.-> NoSilentRepair["Runtime re-entry 不静默修复"]
    NoSilentRepair -.-> TerminalInventory
    TerminalInventory -.-> ExactAuto["仅 FAILED + exact atomic failure bundle\n+ ACTIVE/EXCEEDED Budget\n显式 default-OFF system auto"]
    TerminalInventory -.-> SignedRepair["BLOCKED failure / legacy CANCELLED\nEd25519 signed task_terminal.reconcile"]
    TerminalInventory -.-> Quarantine["COMPLETED / structural ambiguity\nquarantine only"]

    Scheduler["Scheduler"] --> SchedulerWO["WorkOrderV3 + Budget + child grant"]
    SchedulerWO --> Admission
    Internal["Internal Agent"] --> InternalWO["边界 v2 → WorkOrderV3\nBudget + child grant"]
    InternalWO --> Admission

    classDef wired fill:#dff5e1,stroke:#1b7f3a,color:#102a17;
    classDef partial fill:#fff3cd,stroke:#a87500,color:#332400;
    class UI,GW,Ingress,Cortex,Route,Chat,Skill,Delegate,Compile,Admission,Schema,Worker,HB,Checkpoint,VA,Receipt,Effects,Scheduler,SchedulerWO,Internal,InternalWO,RevisionedOutbox,RuntimeEvent wired;
    class Plan,PolicyGate,Runtime,ShadowFanOut,NodeLocal,DurableOutcome,AtomicSuccess,SuccessUow,Reentry,Idle,InProgress,OuterSettle,Window,Conflict,Recovery,Conversation,Delivery,CancelCaller,CancelVerify,CancelIntent,LocalRegistry,LocalJoin,CancelScan,CancelClaim,CancelAdmission,CancelReconcile,CancelDefer,CancelFinal,CancelComplete,StartupRecover,TerminalInventory,TerminalState,NoSilentRepair,ExactAuto,SignedRepair,Quarantine partial;
```

上图的核心结论是：

- Host `chat` / `skill` / `delegate` / `plan` 四路已经进入共用 Governed Runtime，不再允许失败后退回 Executive 执行。
- Plan 已能编译为多节点 TaskGraph，但当前只排序 Governor 已批准的 steps，不会从目标生成多个竞争方案。
- Task Runtime 当前为 `PARTIAL_B5C_DURABLE_CANCELLATION_SHADOW / PRODUCTION_OFF`。Budget 高竞争唯一 winner、single-node post-budget checkpoint continuation、ACCEPTED→Effect restart 和 0099 窄 terminal reconciliation 已有证据；general/multi-node/pre-budget recovery、外部 deployment inventory/remediation、现实 resolver、部署凭据、完整 key lifecycle 和真实 canary仍缺，atomic/bounded/cancellation 均保持 OFF。
- 0095 的 CREATED 由 immutable HostCommand evidence 回填，ACTIVE 等由 deterministic legacy ID/key 重算；升级保持 ID/key，新增 revisioned payload。旧 payload 仅 replay 兼容；source-ref 围栏收敛 legacy 重投。`created_ts=0` 容差可用于 current revisioned 与 legacy unrevisioned 两种可重建 candidate，但除 timestamp 外必须全字段 exact。PlanningRecovery 显式 opt-in 同事务 Graph Outbox inventory 并使用真实 terminal ref；默认 snapshot 不扫描 Outbox。Operator/Review exact revision。
- Lifecycle 未完成统一返回 `TASK_RUNTIME_LIFECYCLE_RETRY_REQUIRED`并锁死 bind/activate，只保留 `aclose` 重试。Coordinator admission close/drain/aclose 或 worker quiescence 任一失败都不关 Store；Coordinator 与 worker 都静默后才 drain Outbox/关 infrastructure。
- Scheduler 与 Internal Agent 均已通过真实 occurrence/admission；Internal 的 v2 只在 ingress/executor 兼容边界存在，canonical lineage 使用 V3。
- Acceptance 只在同一事务中生成 effect intents；下游副作用使用持久队列最终一致，不是全局单事务。

## 3. 入口与路由分流

### 3.1 Host 对话入口

[cortex_runtime.py](../../../host_brain/agent_runtime/cortex_runtime.py) 把 `chat` / `skill` / `delegate` / `plan` 定义为 canonical governed route。数据流是：

```text
normalized request
  → route decision
  → Governor APPROVE + grant
  → GovernedHostRuntime.supports(route)
  → immutable command/graph contracts
```

这四路有两个关键 fail-closed 规则：

1. Governor 未批准不进入 worker。
2. Governed Runtime 不可用或不支持 route 时，返回 `GOVERNED_RUNTIME_UNAVAILABLE` / `GOVERNED_ROUTE_UNSUPPORTED`，不回退 Executive。

canonical route 不会在 graph Acceptance 之前写入 user turn。`task_progress` 和 `recent_turn` 是独立的 durable projection/read route：Governor 批准后持久化可见对话轮次，直接读取 Task 或 conversation projection，不创建 Task/Attempt，也不进入 Executive。

### 3.2 Gateway / Host Input 入口

Gateway 不直接调用 Host worker。它先完成签名验证、规范化与 durable inbox 接收，再由 worker 单次回报 Host `/v1/host/input`。Host 的 [ingress.py](../../../host_brain/input_runtime/ingress.py) 验证 secure claim 与幂等键，然后经 [cortex_dispatch.py](../../../host_brain/input_runtime/cortex_dispatch.py) 进入同一 Cortex/Governor 链。

Gateway 的 intake ack 只表示“已持久接收”。Gateway 只在 Host 返回可验证的 Host input receipt 后才把 inbox 标记为完成。该回执要绑定 source、body digest 和 idempotency digest，不仅检查 HTTP 200。

Host 内的 Canon/Tool/Bond HTTP 与对应 OBS command adapter 现在从同一个 app-local frozen `HostRoutePorts` 解析依赖；router factory 在 app 构造时闭包绑定，运行期不再通过 module setter 或 `process_services` fallback 改写三类 provider。因此同解释器多 Host app 的请求不会因后构造 app 而串到另一对象图。该数据流边界仅覆盖三类 route；其余 OBS/Scheduler/Review/Life/Tribe locator 尚未迁移。

### 3.3 入口状态表

| 来源 | 当前入口 | 进入 canonical TaskGraph | 状态 | 边界 |
|---|---|---:|---|---|
| Host Chat/API | Cortex | 是 | `WIRED` | 仅四条 canonical route |
| Gateway | durable inbox → Host Input | 是 | `WIRED` | 可能提交时间不明的请求进 uncertain DLQ，不盲目重试 |
| Scheduler | governed scheduler adapter | 是 | `WIRED` | Clock/Manual occurrence，完整 child grant/Review/Verification/Acceptance/Receipt |
| Internal Agent | internal task adapter | 是 | `WIRED` | 边界 v2 清除 caller authority 后编译 V3；`delegate:agent_runtime` child grant 验证后才调用 executor |
| Read projection | `task_progress` / `recent_turn` | 否 | `WIRED_READ_ONLY` | 读取 durable projection，不拥有任务状态 |
| Executive | retired guard | 否 | `RETIRED` | 无生产调用、无 SkillDag 执行、无内存 registry |

## 4. Host 四路 Governed Runtime

[governed_task_adapter.py](../../../host_brain/app/governed_task_adapter.py) 是 Host 四路的生产 adapter。它把 route 产物编译为 `HostCommand + WorkOrderV3 + TaskGraph`，再交给 [governed_command.py](../../../host_brain/orchestration/governed_command.py) 与 `GovernedCommandApplication`。[governed_task_authority.py](../../../host_brain/app/governed_task_authority.py) 独立承担无状态的 exact replay、lineage 与 fail-closed 校验：chat/delegate/skill 先走共同 binding，只有 skill 叠加 direct-skill 专属 metadata，避免 replay 路径因错误复用 validator 被拒绝。

| route | 冻结输入 | worker 主行为 | 当前状态 | 未完成语义 |
|---|---|---|---|---|
| `chat` | prompt、conversation context、reply channel | 调用 Surface Reply，输出冻结 worker projection | `WIRED` | conversation 与 delivery 是 Acceptance 后最终一致 |
| `skill` | target skill、arguments、capability scope | 验证 node-bound grant，执行 frozen governed skill，校验 durable skill receipt | `WIRED` | 只证明冻结 skill vertical slice，不等于所有 legacy skill 路径已删除 |
| `delegate` | 精确 `target_id`、上下文和超时 | 经 frozen delegate port 调用指定 sub-agent | `WIRED` | worker 不得从 context 重新选 route 或 target |
| `plan` | Governor 已批准 `planned_steps/steps` | 拓扑合法化 → selected path → immutable multi-node graph | `PARTIAL` | 不生成竞争计划，不做真实 successor search，不完成动态 replan |

### 4.1 Chat

Chat worker 经 [route_execution_ports.py](../../../host_brain/app/route_execution_ports.py) 组装 `SurfaceReplyInput`，使用已批准的输入与 recall packet 调用 Surface Reply。worker 本身不获得 Governor 或 Gateway delivery authority；交付权由 Acceptance Effect Compiler 从已 admission 的 command 重建。

因此正确语义不是“生成文字后立即写对话并发送”，而是：

```text
frozen chat execution
  → worker projection
  → verification
  → graph acceptance
  → graph-scoped conversation/life/delivery effect intents
```

### 4.2 Skill

Direct Skill worker 使用每节点 child grant，从 durable grant ledger 加载授权，再执行 frozen governed skill。之后它记录 governed skill execution，校验 durable receipt projection，并返回 worker commit 和可计量 budget usage。

这条链建立了 `command → graph/node → attempt → child grant → worker receipt` lineage。Executive legacy SkillDag 生产执行和进程内 registry 已删除；后续约束重点是把 Internal 与领域兼容入口全部迁入同一 lineage。

### 4.3 Delegate

Delegate worker 仅接受冻结的 `target_id`。[route_execution_ports.py](../../../host_brain/app/route_execution_ports.py) 会拒绝上下文中企图带入的 route/planning authority，并在 dispatcher 返回后再次校验 target。

这保证了 worker 执行的是上游冻结决定，不是在 worker 内部重新选择 agent。它不代表 child agent 内部的所有工作也已转换为同一 TaskGraph。

### 4.4 Plan

[plan_intent_search.py](../../../host_brain/orchestration/plan_intent_search.py) 只接受 `route=plan`，从 Governor 已批准的 `planned_steps/steps` 构造候选，检查 `skill` / `delegate` / `chat` 节点与依赖，选出完整合法的拓扑顺序。[planning_execution.py](../../../host_brain/orchestration/planning_execution.py) 把 search result 交给 [planning_graph_compiler.py](../../../host_brain/orchestration/planning_graph_compiler.py)，编译成不可变 TaskGraph。

当前 Plan **不会**：

- 从目标生成多个实质不同的候选方案；
- 根据虚拟状态动态生成 successor；
- 在真实 Code/Research 场景中证明 Graph Search 或 MCTS 收益；
- 在 worker 失败时自动生成 revision + 1 并继续调度。

显式 failure-replan metadata 可以构造 graph revision + 1，但这只是合同能力，不是完整的自动重规划闭环。

## 5. TaskGraph 状态流与回执

### 5.1 Admission

[host_command_repository.py](../../../host_brain/persistence/host_command_repository.py) 的 admission 同时绑定：

- immutable `HostCommand`；
- 显式 `WorkOrderV3`；
- 显式 `BudgetPolicy`；
- runtime assignment；
- TaskGraph 及 revision。

它在事务内写入 durable state 并生成 `TASK_GRAPH_CREATED` / `HOST_COMMAND_ADMITTED` outbox 事件。非 `WorkOrderV3` 或没有 BudgetPolicy 的请求不会被自动升级，而是 fail closed。

若图声明可选 `ATOMIC_GRAPH_SUCCESS_V1`，`GovernedCommandApplication` 会在调用 repository admission 前确认 Coordinator capability、node-grant service 和 parent Governor grant；不支持的生产进程不会先留下半份 admission。admission 后派生的 node grant map 必须精确覆盖全部 TaskNode，Coordinator 不接受缺项或多项。

### 5.2 Coordinator

[coordinator.py](../../../host_brain/task_runtime/coordinator.py) 对可运行节点进行：

```text
claim runnable node
  → create/load Attempt
  → acquire Lease + fence
  → reserve idempotency + budget
  → PRE review if required
  → execute worker with node-bound grant
  → verification
  → POST acceptance review if required
  → atomic freeze exact NODE effects + immutable result artifact
  → accept node + append Attempt-bound Node Outbox
  → progress dependants
  → finalize graph receipt
```

| 能力 | 当前事实 | 定级 |
|---|---|---|
| 持久 admission | command/work order/assignment/graph 原子绑定 | `WIRED` |
| 依赖推进 | 能从 `BLOCKED` 推进到 `READY`，拒绝未知依赖与环 | `WIRED` |
| Attempt / Lease / Fence | durable 状态与 fencing 已存在 | `WIRED` |
| Idempotency | 有 reservation、commit 和 conflict 语义 | `WIRED` |
| Budget | Host 四路、Scheduler、Internal 有预留、计量和结算；8/16/32 线程与跨进程只有一个 `EXCEEDED` winner/Outbox，其他 sibling 为引用 winner 的 `ALREADY_EXCEEDED` | `CONTENTION_PROVEN / PROGRAM_PARTIAL` |
| Review | PRE/POST waiting、durable token/checkpoint、拒绝/过期、0096 continuation coordination | `WIRED_SLICE` |
| Resume | 消费 token 后由 composition worker 按原 HostCommand surface 自动重调度；PRE 无伪 Attempt，POST 不重跑 worker | `WIRED_0096` |
| B0/B1 调度决策 | Coordinator 从 durable snapshot 消费稳定 READY/fan-in/retry due 决策 | `WIRED_PARTIAL_RUNTIME` |
| 原子 claim | ACTIVE graph + Budget concurrency 下原子写 Attempt/Node/Lease/Heartbeat/Outbox，并支持 exact replay | `WIRED` |
| Node Acceptance / result artifact | 0102+GR06 将 production normal/checkpoint/Review 的 exact NODE Effect set、`ACCEPTED`、artifact 与 Attempt-bound Node Outbox 放入一个 UoW；response loss 只能 exact replay，Effect claim 必须重验同 lineage artifact。后继只消费 compiler-owned、同 graph/revision 的全部前驱 refs。新 admission 拒绝空合同，startup 隔离 active legacy 空合同/缺 artifact和缺 exact artifact 的可投递 terminal legacy Effect。应用低层 ACCEPTED writer 已封口；pre-stage、legacy/internal graph create 与 direct SQL 仍存在 | `UNIFIED_APPLICATION_STORE_UOW_WIRED / PRESTAGE_COMPATIBILITY_PRESENT / SQL_DIRECT_BYPASS_PRESENT` |
| Completion policy | TaskGraph v1 可选 `ATOMIC_GRAPH_SUCCESS_V1` 必须显式 sink result；legacy 多 sink 无 result。PlanningGraphCompiler 默认 legacy，只有显式请求/重规划继承才 atomic | `B5_DURABLE_POLICY_WIRED_SHADOW` |
| DAG 并发 | `atomic_graph_success_enabled` 与 `bounded_fanout_enabled` 独立默认 OFF，三处生产 composition 未启用。atomic 可单节点/串行 outer 封口；bounded 才允许 terminal-leaf 实际有界并发 | `PARTIAL_B5C_DURABLE_CANCELLATION_SHADOW` |
| 并行合流 | progress 用 `fanout_event_buffer_size` 个 semaphore slot（默认 32、非正值拒绝）限制在途事件，并为每个 child 预留独立 control 容量；size=1 慢消费者仍会反压 child，自发 `CancelledError` 也不会因 progress 满而丢失监督信号。fan-in 只在依赖 `ACCEPTED` 后执行 | `SHADOW_BOUNDED_FLOW` |
| 并行 child authority | deferred child 只拥有 Attempt/Node/Lease/Reservation 与 Budget usage/`EXCEEDED`；不能终结 Graph 或把 Budget 置为 `RELEASED/CONSUMED`。正常非接受等待所有已启动 sibling 到 durable node-local terminal，不再 fail-fast | `SHADOW_NODE_LOCAL_AUTHORITY` |
| Admission / grant 门禁 | atomic capability、node-grant service、parent grant 在 repository admission 前校验；Coordinator 直入要求 exact node grant map | `B5_FAIL_CLOSED_SHADOW` |
| Schema 前置门禁 | Budget/worker 前验证 `0090` 与 `0092`；atomic policy 还验证 `0091` | `B5C_FAIL_FAST_WIRED` |
| 共享终态 authority | live/re-entry 从 durable Node/Attempt/Reservation/BudgetUsage 重建同一 payload/ref；outcomes 全量覆盖 admitted Graph durable terminal Nodes/latest stable Attempts（含非 selected `RETRY_PENDING`），当前 batch 仅定优先级/selected。ownership/CAS 后 active ownership 映射 `IN_PROGRESS`，纯 CAS为 conflict | `B4_LIVE_AND_REENTRY_OUTER_WIRED` |
| 并行终态证据 | pre-reservation failure、Budget overrun、post-review、Review denial ref fence 与 crash re-entry failure-over-review/live authority preservation 均有集成证据，包含结算提交响应丢失 exact replay；伪 receipt/旧 Attempt lineage、digest/payload/Outbox/replay 漂移被拒绝 | `INTEGRATION_AND_STORE_CONTRACT_PROVEN` |
| 未决副作用恢复 | 自动 continuation 仅接受 single-node canonical COMMITTED checkpoint：COMMITTED Reservation + 唯一 COMMITTED Outbox + 原 Attempt/历史 Lease/fence + hash receipt + canonical WorkerProjection + 唯一 ACTIVE graph Budget。已有 exact accepted worker usage 时只读核对；stable usage 与所有 worker-kind row 完全缺失时，旧 Lease 到期后先以新 fence 接管，再按 Attempt start→Reservation commit 的正持久时间上界幂等结算。并发、响应丢失、超限和重入均不重跑 worker；冲突/拒绝/漂移 usage、非法时间、pre-operation、batch/multi-node 或不确定副作用仍 fail closed | `SINGLE_NODE_CANONICAL_COMMITTED_CHECKPOINT_PROVEN / GENERAL_RECOVERY_INCOMPLETE` |
| crash/re-entry 终态 | `ACTIVE` 多节点 Graph 从 durable Node/latest Attempt 复原 outcomes；`BLOCKED/FAILED/CANCELLED/COMPLETED + 未结 Budget` 不由 Runtime 重入静默修复，改由 0099 immutable case/proof/supersession authority 分类 | `B4_ACTIVE_REENTRY_WIRED_TERMINAL_REPAIR_EXPLICIT` |
| 0099 历史终态对账 | schema-only migration；readiness 比较 3 table/4 index/10 trigger canonical SQL 并做 Python case/subject/proof 复核；`recover_startup` 后单次最多 inventory 10,000 个 terminal Graph。仅 `FAILED + exact atomic failure bundle + 唯一 graph-scope ACTIVE/EXCEEDED Budget` 可由显式 default-off system auto；child/missing/multi Budget、PENDING/CONSUMED 或 Graph/Node/Review/Attempt/Lease/并行终态不一致只 quarantine。0090 唯一且 0091/0092 完整验证；未决 case graph-wide 围栏普通 Graph/cancellation request 与 Budget transition/consume/usage/retry，只有 0099 UoW bypass；每个历史 case 的 supersession chain 最终须可信 proof，healthy closure 只能 signed `NONE` 且零业务写 | `WIRED_DEFAULT_OFF / CANARY_NOT_RUN / PRODUCTION_OFF` |
| 决策记录 | canonical payload 与 Graph、可选 Budget、Outbox 同事务写 `0090`。selected `BUDGET_BLOCKED` 绑定 rejected usage；共享超额后的被动 blocked sibling 不要求本地 rejected usage。每个 row 读取校验 digest、精确 Graph/可选 Budget Outbox 与 Attempt，BUDGET 另校验 usage；terminal replay 还校验 Graph target/version | `B4_CANONICAL_DECISION_JOURNAL_WIRED` |
| marker 历史扫描 | 0095 令所有 `TASK_GRAPH` Outbox 精确绑定现存 revision，完整 taskbatch marker 缺行/错绑扫描按 `graph_id + revision` 覆盖 multi-revision 历史；decision getter 同时复核 exact Outbox revision | `WIRED_0095_REVISIONED_GRAPH_OUTBOX` |
| failure replan evidence | scheduler snapshot 默认不扫 Outbox；PlanningRecovery 显式 opt-in，在同一读事务选择唯一 terminal Graph Outbox 的真实 payload ref。0100 append-only supersession 与 `admit_replan()` 单事务重新核验 same-root `FAILED` predecessor、精确 Outbox 和静止 facts，再原子写 revision+1 admission/supersession；共享 Store/Review fence 阻止旧 revision 再 mutation/continuation。response loss exact replay，故障注入全回滚。当前保守拒绝 `BLOCKED`、所有历史 Review continuation 与所有 Acceptance Effect，自动 replan/生产 canary仍缺 | `PARTIAL_DURABLE_SUPERSESSION_SAFE_SUBSET` |
| PRE Review 观察 | `WAITING_APPROVAL` 已纳入统一 0090 outcome；合法无 worker Attempt，receipt/decision 使用 `attempt_id=null`，其它状态仍强制真实 Attempt | `WIRED_0096_OPTIONAL_ATTEMPT` |
| atomic 成功 UoW | 全 Node Accepted 后单事务提交 Graph COMPLETED、Budget CONSUMED、两 Outbox、全图 effect intents 与 0091 decision；结果只取显式 result 的 canonical projection | `B5_SHADOW_WIRED` |
| legacy ACCEPTED→Effect 重启 | Node ACCEPTED、Graph ACTIVE、Effect 未落盘时，共享 `AcceptedGraphFinalizationRuntime` 从 exact Operation checkpoint/canonical WorkerProjection 重建 Effect，并原子提交 Graph+Effect；篡改 fail closed，重放不新增 Effect，worker 不重跑 | `PROVEN_NARROW_RESTART_FINALIZATION` |
| projection / replay | projection 重算 digest并绑定 command/request/route/target/graph/node/attempt/receipt/grant；completed replay 只读 0091、不重编 effects；双响应丢失重读 terminal authority | `B5_ADVERSARIAL_PROVEN_SHADOW` |
| Durable Graph 取消 | `task_graph.cancel`、Operation/Effect/terminal reconciliation 均经默认关闭入口。0098 处理 Effect 不确定性；启用 cancellation 时，0099 production gate阻断 current terminal anomaly、缺失 trusted proof/supersession closure 的历史 case与任何 quarantine，但不阻断 terminal remediation Operator 本身。外部 deployment inventory、凭据/key lifecycle/canary未知 | `B5C_WIRED_DEFAULT_OFF_PRODUCTION_OFF` |
| Outbox 并发发布 | 0093 持久化 owner/token/expiry/单调 fence 并支持 takeover。0095 按 `type + id + revision` 隔离 head；CREATED 用 HostCommand evidence，ACTIVE 等用 deterministic legacy ID/key 重算，无法解析则迁移阻断。升级保持 Outbox ID/key，新增 `@revision` payload；旧 payload 仅 replay 兼容。新 Event=`graph_id@revision`，旧 journal 经 source Outbox exact read；unique partial source-ref 防重复投影，Operator/Review exact revision。publisher 继续 claim→幂等 append→fenced complete；`DRAINED` 仅在 total_pending=0 | `WIRED_0093_CLAIM_0095_REVISION_IDENTITY` |
| Budget 并发 CAS | winning rejected usage 独占 `ACTIVE→EXCEEDED` 与唯一 Outbox；sibling只追加 `ALREADY_EXCEEDED`并引用 winner。0090 decision 写/读/replay 在单一读快照中重验 Reservation 精确版本、sole winner Outbox、Attempt/Graph lineage与全部 sibling；charge attempts 至少为 `max(configured retries, concurrency + 1)`；concurrency=8 时连续 8 次 conflict 后第 9 次成功；线程/跨进程/restart/interleave 已验证 | `UNIQUE_WINNER_AND_ADMITTED_WAVE_RETRY_PROVEN` |
| Retry/backoff | Coordinator 仅对 reservation 前显式 marker 重试；并行 deferred child 使用 domain-separated node-only usage identity 与 `schedule_retry_node_outcome`，保持 Graph ACTIVE，提交响应丢失 exact replay 不重复扣费；预算拒绝只落 Budget `EXCEEDED` 与 node-local terminal，再交 outer 裁决。生产 resolver 尚无现实 marker 来源 | `PARTIAL_B4_SAFE_RETRY` |
| Pre-reservation failure | 普通/耗尽 failure 通过 `fail_running_attempt_bundle` 原子落 Attempt FAILED、Node FAILED、Graph FAILED/BLOCKED、Lease RELEASED 和四条 attempt/evidence-bound Outbox；已有 reservation 时拒绝，Budget finalize 在 bundle 外 | `WIRED_NARROW_BOUNDARY` |
| 执行心跳 | reservation 后后台先续 Lease 再续 Reservation；流事件和最终结果均 checkpoint，停止/join 后才用最新 version COMMIT。续租同参响应丢失可精确重放 | `WIRED` |
| 心跳失败 | 未形成 exact post-budget checkpoint 时返回 `RECOVERY_REQUIRED` 且不 COMMIT；只有上表 single-node 窄窗可 takeover。0096 Review continuation是另一条 task-bound 协调链；两者都不代表 asyncio 能撤销外部副作用，也不代表 general recovery 完成 | `FAIL_CLOSED_WITH_NARROW_TAKEOVER` |

启动恢复数据流为 migration → TaskStore 0093/0094/0095/0098/0099 与 Review 0096 readiness → `task_store.recover_startup` → 0099 单次最多 10,000 个 terminal Graph inventory → 可选且默认关闭的 exact system auto → publisher → recovery outbox publish → Outbox worker → surface runtime activation → cancellation/Review workers。0099 inventory 是显式历史终态分类，不是 general recovery；单节点 post-budget Operation takeover仍发生在 exact request re-entry，batch/multi-node/pre-budget 不会被启动流程自动修复。

Budget 的 token/cost 在 provider 没有返回真实计量时保持 `UNMEASURED`，不做伪精确估算。concurrency charge 和超限后的完整 `DEGRADE` / `REVIEW` 分支还没有闭环。

### 5.3 Scheduler 与 Internal 已接通

[scheduler/governed_runtime.py](../../../host_brain/scheduler/governed_runtime.py) 已构造 `WorkOrderV3 + BudgetPolicy`，以 `delegate:<worker_key>` 冻结 TaskNode，并在 handler 前验证 durable child grant。真实测试已贯通 Attempt、Budget consumed、Review waiver、Verification、Acceptance、Receipt 与 replay。

[internal_task_runtime.py](../../../host_brain/app/internal_task_runtime.py) 在认证 ingress 接受兼容 `AgentWorkOrder`，清除调用方伪造的 lineage/approval authority 后立即构造 `WorkOrderV3 + BudgetPolicy`。TaskNode 冻结为 `delegate:agent_runtime`；worker 只有在 durable child grant 完整验证后才向既有 Agent executor 投影兼容对象。

因此实际流程是：

```text
authenticated Internal ingress
  → Secure Ingress principal + Governor grant
  → strip caller authority; compile WorkOrderV3 + positive BudgetPolicy
  → HostCommandRepository atomic admission
  → TaskNode delegate:agent_runtime + child-grant verification
  → PRE Review if required
  → existing Agent executor compatibility projection
  → durable result + Verification + Acceptance / POST Review
  → execution receipt + replay
```

前置 Review 不执行 handler，且在尚无 worker receipt 时保持 ingress claim result 未绑定；执行结果声明 `review_required` 或内部 Acceptance 为 `REVIEW` 时进入 `WAITING_ACCEPTANCE_REVIEW`，即使已有 pre-acceptance worker receipt 也不得提前完成 ingress。0096 后，ReviewStore 的终态 mutation 与 continuation enqueue 同事务；worker 按 surface 重入并通过 completion hook 收口 Host effect 或 Internal/Scheduler secure-ingress result。续跑 credential 覆盖 budget time window 加 child TTL、最多 24 小时；READY 与首次 worker entry 分别校验 live claim 和 exact Attempt/Lease。拒绝/过期使用 durable decision ref，历史 POST receipt 只有 exact lineage CAS 才能替换。

## 6. Acceptance Effect 与对话提交语义

[acceptance_effects.py](../../../host_brain/task_runtime/acceptance_effects.py) 在 graph 验收后从受信 worker projection 编译 graph-scoped effects。当前主要 effect 类型是：

1. `conversation.user_turn`；
2. `life.foreground_completed`；
3. 在存在 reply channel/target 时的 `surface.reply_delivery`。

worker 不能提供这些 effect 的 authority 或 idempotency key。Compiler 只使用已接纳 HostCommand 和 graph 事实重建，避免 worker 扩权。

Legacy completion 的崩溃重入也经过同一编译/落库边界。`AcceptedGraphFinalizationRuntime` 不信任 terminal replay 的任意 mapping，而是要求 Store 返回 exact COMMITTED Operation checkpoint，并验证历史 Lease、Attempt SUCCEEDED、COMMITTED Outbox evidence、hash receipt 与 canonical WorkerProjection lineage；随后才在一个 UoW 中提交 Graph COMPLETED 和 Effect intents。测试覆盖 ACCEPTED 已提交但 Effect 为空的进程退出、重启无 worker replay、重复执行 Effect 幂等，以及篡改 projection 时零 Effect/Graph 不完成。

[acceptance_effect_dispatcher.py](../../../host_brain/task_runtime/acceptance_effect_dispatcher.py) 使用 durable queue、lease 和 fence 按以下语义顺序提交：

取消并发下必须区分“成功证据”和“失败不确定性”：`COMMITTED + durable receipt` 是正向证据，可自动解除 cancellation fence；普通 handler/transport `FAILED` 可能发生在外部副作用之后，不能推导 `NO_SIDE_EFFECT`。因此取消前已失败及取消后 in-flight 失败都保持 `RECONCILIATION_REQUIRED`。0098 不改写这些 Effect/fence 行，而是追加 immutable case/proof；PENDING proof 的 `NO_SIDE_EFFECT` 或 `SIDE_EFFECT_CONFIRMED + receipt` 只影响之后的新 cancellation decision。对已 FINALIZED 历史 decision，correction 只在 trusted read 时复核原 decision ref/payload digest，旧 decision、fence、evidence 均保持原字节。迟到 commit/fail/reschedule 与已有 PENDING proof 冲突时在同一事务拒绝。

Obs 读取不借用 Host 进程内 Task container：`obs_fastapi_app` 仅在 `HOST_NLE_OPERATOR_VIEWS_ENABLED=true` 时，从 ledger canonical DB path 创建只继承 read/validation mixin 的 `EffectReconciliationQueryRepository`，数据库连接强制 `mode=ro + query_only=ON`。开关关闭时不触发 0098；开启后 candidates/cases/challenge 只接受 `obs:task-runtime:read`，通用 Operator read scope 无权读取。cases/proof 为最小投影，challenge 把离线签名所需 exact subject 编码为 canonical-JSON base64url，并附 SHA-256 供传输后复核。

0099 terminal reconciliation 在主 Host 与独立 Obs 都提供 `candidates/cases/challenge` 三条 GET，并要求 `obs:task-runtime:read`；两处都只构造同 canonical SQLite 的 `mode=ro + query_only=ON` reader，禁止 fallback 到完整 `DurableTaskStore`。Cases 只给最小摘要，subject snapshot 不出库。Challenge 从 OperatorQueryGateway request context 取得认证 principal，接收 `attestation_ref/key_id/issued_ts_ms`，在 authority 已绑定 key ID 时拒绝不一致请求；权威 builder 用 case、current subject 与确定性 Graph/Budget Outbox 生成 Store 实际验证的 final canonical bytes和 `command_request`。HTTP → 真实 Ed25519 → Store round-trip 已通过，但外部 key lifecycle/canary 未验证。

D4 后独立 Obs 同时保留三条必须区分的数据流：

```text
create_obs_app
  → ObsApplicationPorts
  → 19 registered producer callbacks（registration，不是运行成功证明）
      → 16 declared non-TYPED_UNAVAILABLE（非运行成功证明）
          → 16 durable/static/app-owned source callbacks
          → 0 process-local module-cache coupled callbacks
          → 7 DURABLE_READ_PARTIAL（缺 target 时仍写 typed unavailable）
      → 3 TYPED_UNAVAILABLE
          → durable SOURCE_UNAVAILABLE
  → app-local ProjectionJobDaemon
  → QueryProjectionStore
  → /health DEGRADED（missing=0，但 typed-unavailable=3、partial=7）

36 identified production projection endpoints (35 HTTP + 1 WebSocket)
and identified projection/stats queries / sessions + prompt cache
  → request.app.state.obs_route_ports
  → app-local QueryProjectionStore / ProjectionLedgerView / response single-flight
  → 0 Obs-route consumers of compatibility global query helpers

19 D4-migrated production GET routes
  → read_process_projection(explicit app-local store, ...)
  → no process store discovery
```

19个 producer callback 执行路径不调用 `process_services`。Security Immune只接受满足最小schema和时效的隐私过滤证据，证据freshness与lease/journal操作时钟分离；摘要在ledger failure/truncation时整体失败关闭，Forge只作为字段级unavailable；operator拒绝空Gateway健康对象，任一target写失败会令job失败；Runtime从durable Life与operator projections构造最小partial snapshot，缺Life槽位/readiness不冒充live，也不调用Agent/模型。Forge、Life Body、Organ三个整job仍 unavailable；sys-mode/history、真实Edge和Host endpoints未闭环。15个直接词法边界、三个非路由兼容helper定义与legacy daemon仍在；不能把部分source recovery写成完整Query Plane。

```text
conversation.user_turn
  → life.foreground_completed
  → surface.reply_delivery
```

某个较早 effect 未提交时，当次 graph dispatch 不继续提交后续 effect。失败会保留原因并按队列策略 reschedule；这不是一个跨所有存储与外部系统的原子事务。

### 6.1 Conversation / Life / Memory 实际提交链

```mermaid
flowchart LR
    Accept["Graph Acceptance"] --> Intents["事务内写 effect intents"]
    Intents --> UserTurn["durable user turn"]
    UserTurn --> LifeCmd["Life foreground command"]
    LifeCmd --> Reducer["Life reducer: event + snapshot + projection intents"]
    Reducer --> Projection["surface.post_process projection"]
    Projection --> Assistant["durable assistant turn"]
    Projection --> Memory["LifeMemory admit_chat"]
    Intents --> Delivery["ordered reply delivery effect"]
    LifeCmd -. effect order gate .-> Delivery

    Legacy["Noncanonical compatibility path"] -. create_task soft failure .-> LegacyPost["legacy post-process"]
    PersonaCandidate["Bond / Interaction / Emergent candidates"] --> PersonaFence["PersonaEngine central authority fence"]
    PersonaFence -. reject legacy write .-> Persona["Production Persona traits"]
    PersonaMigration["Explicit audited migration"] --> Persona
    Scene["SceneDecision"] -. flag default off .-> Surface["Surface / Proactive eligibility"]

    classDef wired fill:#dff5e1,stroke:#1b7f3a,color:#102a17;
    classDef partial fill:#fff3cd,stroke:#a87500,color:#332400;
    classDef unsafe fill:#fde2e2,stroke:#b42318,color:#3d0b08;
    class Accept,Intents,UserTurn,LifeCmd,Reducer,Projection,Assistant,Memory wired;
    class Delivery,Legacy,LegacyPost,Scene,Surface partial;
    class PersonaCandidate,PersonaFence partial;
    class PersonaMigration,Persona wired;
```

[acceptance_effect_handlers.py](../../../host_brain/app/acceptance_effect_handlers.py) 分别调用 durable conversation writer、提交 `LifeCommand` 和进入 Gateway delivery。[life_projection_handlers.py](../../../host_brain/app/life_projection_handlers.py) 再由 Life projection 触发 `_async_post_process`。

[postprocess.py](../../../host_brain/agent_runtime/postprocess.py) 在 Life projection mode 下对 assistant turn 和 LifeMemory admission 使用 durable-required 语义；失败会向上抛出，不当作成功。它随后在该 mode 提前返回，避免重复执行 persona/tribe mutation 和 VTS/TTS。

因此 canonical 对话的准确定级是 `POST_ACCEPTANCE_DURABLE_EVENTUAL`：

- graph Acceptance 与 effect intents 原子绑定；
- user turn、Life command、assistant turn、Memory 和 delivery 分阶段持久提交；
- 队列可重试并保存状态；
- 不存在把上述所有操作包进单个数据库事务的语义。

## 7. Gateway 双向链路

```mermaid
sequenceDiagram
    participant P as Platform
    participant G as Gateway Durable Runtime
    participant H as Host Secure Ingress
    participant T as Host Task Runtime
    participant E as Acceptance Effect Dispatcher
    participant A as Platform Adapter

    P->>G: signed webhook
    G->>G: verify + normalize + persist inbox
    G-->>P: intake ack
    G->>H: signed /v1/host/input (single attempt)
    H->>T: Governor + HostCommand + graph
    T-->>H: authenticated Host execution receipt
    H-->>G: host-input-result receipt + digests
    G->>G: complete inbox

    T->>E: accepted reply effect
    E->>G: HMAC /v1/gateway/reply + delivery_id
    G->>G: persist outbox + lease/fence
    G->>A: frozen outbound payload
    A-->>G: confirmed evidence or uncertain outcome
    G-->>E: queued/sent state with outbox identity
```

### 7.1 Inbound

[delivery_runtime.py](../../../edge_client/gateway/delivery_runtime.py) 对 inbound 先持久化、后 dispatch；worker 使用 lease/fence claim inbox。[input_reporter.py](../../../edge_client/channels/input_reporter.py) 对 Host 使用签名身份和幂等头，并验证 Host receipt 与 body/idempotency digest。

一旦 dispatch 已开始却无法确定 Host 是否已提交，Gateway 不会盲目重发，而会把记录转入 uncertain DLQ。这是 at-most-once 副作用与不确定性之间的显式取舍。

### 7.2 Outbound reply

accepted reply effect 经 [gateway_delivery.py](../../../host_brain/input_runtime/gateway_delivery.py) 调用 [edge_transport.py](../../../host_brain/channels/edge_transport.py) 的 `submit_gateway_reply`，使用稳定 `delivery_id` 和 HMAC 发送 `/v1/gateway/reply`。Gateway server 把 payload 放入 durable outbox，并以 platform + conversation 作 ordering key。

[outbound.py](../../../edge_client/gateway/outbound.py) 在 dispatch 前验证并冻结 payload。只有 adapter 返回确认证据时才能写入 ACK；在 send boundary 之后发生异常或返回 false 会被定级为 `MAY_HAVE_COMMITTED` / uncertain，不会伪造成功。

### 7.3 两种“成功”

| 回应 | 证明什么 | 不证明什么 |
|---|---|---|
| Gateway `queued/accepted` | 冻结 payload 已进 durable outbox | 平台用户已收到 |
| Gateway `sent/ACKED` | platform adapter 已返回按合同可接受的确认证据 | 不同平台对“最终呈现”的所有业务语义 |

## 8. Edge 设备与实体副作用

Host 只创建版本化 `EmbodimentCommand`，不导入设备 driver。[embodiment.py](../../../host_brain/channels/embodiment.py) 把 avatar、voice、desktop 等意图发送给 Edge；Edge 是唯一物理 actuator。

当前存在两条 Host 提交方式：

| 方式 | 流程 | 状态 |
|---|---|---|
| Host durable embodiment outbox | enqueue → lease/fence → Edge RPC → durable receipt / `IN_DOUBT` | `WIRED` |
| async direct submit | 取得 durable Edge session lease/fence → Edge RPC → Edge 持久 claim → device effect → receipt | `PARTIAL` |

[embodiment_outbox_repository.py](../../../host_brain/persistence/embodiment_outbox_repository.py) 管理 Host outbox 的 `PENDING → DISPATCHING → completed/IN_DOUBT`。[embodiment_runtime.py](../../../edge_client/gateway/embodiment_runtime.py) 在 Edge 端用 command/idempotency key 去重；若进程崩溃时命令处于 `DISPATCHING` 但没有 receipt，恢复后标记 `IN_DOUBT`，不盲目重执行物理动作。

边界是：Edge 端的持久化与回执已成立，但并非每个 Host embodiment 调用都先写 Host Acceptance Effect Outbox。因此“所有外部副作用均统一通过 Host effect outbox”仍是 `TARGET`，不是当前事实。

## 9. Memory、Persona 与 Scene

### 9.1 Memory read

[chat_retrieval_helpers.py](../../../host_brain/app/chat_retrieval_helpers.py) 使用 [surface_memory_recall.py](../../../host_brain/persona/surface_memory_recall.py) 规划 recall，在多个 typed store 上执行 MUST/MAY/NO 范围的查询、覆盖与证据检查，最终压缩为 `MemoryRecallPacket` 交给 Surface Reply。

对话模型消费的是 packet，不是未经约束的全库原始行。这条 read path 已接通，但仍是调用期检索，不等于 Knowledge OS 或可演化知识层已实现。

### 9.2 Memory write

canonical 写路径是：

```text
Graph Acceptance
  → Acceptance Effect Dispatcher
  → durable user turn
  → Life foreground command
  → Life projection
  → durable assistant turn + LifeMemory.admit_chat
```

非 canonical compatibility path 仍可以用 `asyncio.create_task()` 启动 legacy post-process，某些失败只降级记录。因此 Memory 不能简化标记为“全入口强持久”；对 Host 四路的准确说法是 `WIRED_POST_ACCEPTANCE_EVENTUAL`。

### 9.3 Persona

[persona_engine.py](../../../host_brain/persona/persona_engine.py) 已把 `apply_growth_delta()` 变为中央 fail-close 兼容围栏。所有非迁移调用都会返回稳定的 `PERSONA_GROWTH_AUTHORITY_CLOSED`，且不会修改 trait 或写入变更日志。

[persona_growth.py](../../../host_brain/persona/persona_growth.py) 的 stage/milestone、interaction cycle，以及 [emergent_growth_engine.py](../../../host_brain/persona/emergent_growth_engine.py) 的 emergent weekly 当前只生成经过 drift guard 的 candidate；它们不会消费 one-time 标志、weekly cumulative、change history 或 feedback buffer。显式历史迁移只能通过带 `migration_id`、actor 和 evidence 的 `apply_migration_growth_delta()` 写入，并产生结构化审计。

`T-PERSONA-FENCE-01` 会拒绝生产代码重新调用 legacy writer、在迁移命名空间之外构造迁移 authority，或绕过引擎直接修改 `_user_traits`。当前定级为 `CENTRAL_FENCE_WIRED`：旧直写已归零，但 `proposal → Lab/shadow → Review → version activation → rollback` 尚待 NE-06 实现，因此人格长期演化仍不可用。

[prefrontal_agent.py](../../../host_brain/tribe/prefrontal_agent.py) 对 `PersonaGrowthTracker.on_interaction_cycle()` 的调用参数与真实签名不匹配，`TypeError` 会被吞掉/降级。该 experience-feedback 分支是 `PARTIAL_BROKEN_PATH`，不能作为人格演化已闭环的证据。

### 9.4 Scene

[scene_policy.py](../../../host_brain/persona/scene_policy.py) 会构造过期、只读、限制性的 `SceneDecision`。它已被 [surface_runtime/eligibility.py](../../../host_brain/surface_runtime/eligibility.py) 和 [proactive_voice.py](../../../host_brain/persona/proactive_voice.py) 消费，用于约束 Surface 表达和主动性 eligibility。

但生产 feature flag `HOST_LIFE_PROJECTION_ENABLED` 默认为关闭，当前也没有 canonical Scene aggregate、transition journal 或统一 Task context authority。因此定级是 `PARTIAL_DEFAULT_OFF`，不是全局 Scene Engine。

| 能力 | 读路径 | 写路径 | 定级 |
|---|---|---|---|
| Memory | typed recall → compressed packet → Surface | Acceptance → Life projection → assistant/Memory | `WIRED_POST_ACCEPTANCE_EVENTUAL` |
| Persona | Surface/relationship 等多处读 | legacy 写入中央拒绝；仅显式审计迁移可写 | `CENTRAL_FENCE_WIRED` |
| Scene | Surface/Proactive eligibility | 无 canonical scene write API | `PARTIAL_DEFAULT_OFF` |
| World Model | read-only fact projection | 无生产 write API | `PARTIAL` |

## 10. 回执与证据分层

系统中的 receipt 不是一张可通用替换的“成功证明”。每一层只对自己的责任边界负责。

| 证据/回执 | 生成者 | 权威范围 | 不可替代 |
|---|---|---|---|
| Secure ingress claim/audit | Host/Gateway ingress | 身份、签名、nonce、body digest | Task execution receipt |
| Gateway inbox intake ack | Gateway durable runtime | inbound 已持久接收 | Host 已执行 |
| Host admission outbox | HostCommandRepository | command/work order/graph 已绑定 | worker 或 graph 已完成 |
| Node worker receipt/projection | frozen worker | 某一 node attempt 的执行结果 | graph Acceptance |
| Graph execution receipt | Task Runtime | graph revision 已通过或被拒绝 | conversation/delivery effect 已提交 |
| Acceptance Effect receipt | Effect Dispatcher | 某个 graph-scoped effect 已提交 | 其他 effects 已提交 |
| Gateway adapter ACK | Gateway outbound | adapter 已提供确认证据 | 平台端所有终端呈现语义 |
| Edge embodiment receipt | Edge actuator runtime | 某个设备命令的 durable 处理结果 | 任意其他 Task/effect 的成功 |

只读 projection 和 Operator UI 必须从上述 durable 事实派生，不得自行提升为新的执行真相。

## 11. 失败、重试与不确定性

| 失败位置 | 当前行为 | 安全属性 | 缺口 |
|---|---|---|---|
| Host command admission 前 | fail closed，不创建可执行 graph | 不先写 canonical user turn | Scheduler/Internal 合同仍会在此阻断 |
| Worker 执行前 | Lease/fence、idempotency reservation、budget/review 约束 | 降低重复执行 | 现实 resolver marker、batch/pre-budget recovery 未完整 |
| Verification/Acceptance | 拒绝 node/graph，不生成已批准副作用 | worker 不直接获得对外 authority | 自动 revision + 1 replan 未闭环 |
| Acceptance effect | durable queue 保留状态并 reschedule | graph result 与 effect state 可分辨 | 不是全局原子提交 |
| Gateway dispatch 开始后无回执 | uncertain DLQ，不盲目重发 | 优先避免重复外部副作用 | 需要 Operator 对账/处置 |
| Edge device dispatch 中崩溃 | 恢复为 `IN_DOUBT`，不盲目重执行 | 防止重复物理动作 | 需要外部状态核对 |
| 历史 terminal Graph + 未结 Budget | Runtime re-entry 不隐式修复；0099 inventory 后 exact auto / signed / quarantine 三分，未决 case graph-wide 围栏普通 Graph/cancellation request 与 Budget transition/consume/usage/retry，仅 0099 UoW bypass；旧 case 通过 supersession 链最终收敛到可信 proof | 不把 child/missing/multi Budget 或任一完整终态漂移伪装成可恢复；healthy closure 仅 signed `NONE` 且零业务写 | 外部 inventory、key lifecycle、remediation 与 canary 未知 |
| Legacy post-process | 某些路径异步启动并降级失败 | 不阻断 legacy reply | 没有 canonical 路径的强语义 |

## 12. 目标数据流（不是当前现状）

本节全部标记为 `TARGET`。它用于约束后续重构，不能与上文已接线事实混写。

```text
All ingress sources
  → Secure Ingress + Identity + Scope + Governor
  → HostCommand + Intent/Scene/World Context
  → domain-neutral PlanningSearch over virtual state
  → immutable TaskGraph revision
  → parallel/retry-capable Durable Task Runtime
  → Verification + Acceptance
  → transactional effect intents
  → all external writers through durable outbox
  → immutable scoped receipts
  → read-only projections
```

从当前流程到目标流程的必要变更是：

1. 守护 B5C cancellation、Operation/Effect reconciliation、0098 append-only/trusted-overlay 与 0099 append-only terminal case/proof/supersession 合同；每个历史 case 链必须最终收敛到可信 proof。在目标部署执行 inventory/remediation，补齐 credential/key lifecycle 和 production canary。0099 只关闭窄历史终态，不得被泛化成通用恢复。
2. 持续守护已通过的 0099 主 Host/独立 Obs query-only 边界与 final-binding round-trip（server-bound actor、key mismatch rejection、time/ref、deterministic Outbox、command request），并保持 system auto 默认 OFF、唯一 graph-scope exact-only 与结构 quarantine 分类；持续守护 0096 `WAITING_APPROVAL` optional-attempt observation。
3. 持续守护 0095 revisioned TaskGraph Outbox：歧义回填必须整笔事务失败，Graph/non-Graph trigger/readiness 必须 exact，multi-revision marker 扫描、revision 内顺序、revision 间独立推进、Runtime Event 与 Operator exact read 必须持续通过；跨进程 drain authority 继续保留 0093 expired takeover、stale fence 和 append/complete 响应丢失回归矩阵。
4. 将可证明无副作用的现实 resolver 瞬态来源映射到现有 safe-retry marker；持续守护已完成的 Budget unique-winner 合同，再完成 promotion+claim 单事务、历史半状态 reconciliation，以及 general/batch/pre-budget recovery。single-node post-budget takeover 不得被泛化。
5. 对已接通的 0096 Resume continuation 做跨进程故障矩阵和 canary：原子 enqueue、到期 sweep、claim takeover/stale fence、精确 surface 路由、POST no-rerun、grant renewal 与关闭 quiescence。
6. 从 objective/context 生成多个可比较计划，为 L1/L2/L3 提供真实虚拟 successor 与 durable calibration。
7. 失败重规划必须自动生成同一 graph ID 的 revision + 1，而不修改旧图或在 worker 内偷偷重规划。
8. 所有 Gateway、Edge、Memory、Knowledge、Persona 写操作统一从 Acceptance Effect Outbox 获取 authority 与幂等身份。
9. 保持 Persona 中央围栏，把无副作用 candidate 接入 proposal、shadow、Review、版本激活和可回滚流程，禁止恢复 direct writer。
10. 建立默认启用、有单 writer 和 transition journal 的 Scene/World/Intent context，并使其变成 TaskGraph 输入事实。
11. W0 继续在 D0 canonical path fence 之上熔断未授权写入。W1/0101 把逻辑 workspace 精确绑定到 graph epoch/root WorkOrder/owner/configured-root/allow-list。W2 的认证 Internal 写路线再执行 `server-derived workspace_id → exact source commit freeze → instance/PID lease → namespace worktree → heartbeat scope → Agent tools → verification → ACTIVE 留待后续决策`；顺序 node/attempt 通过 fence handoff，过期实例只能 takeover，cleanup 先进入 durable pending再写不可变 receipt，启动同步最多续清 1 条，剩余 backlog 由后台 worker逐行处理。Provisioning/allocator/cleanup adapter 的 filesystem/Git callback 在 workspace/source 跨进程锁内共享一个 lease-bounded deadline，SQLite 只做 callback 前后 exact authority 短读和独立状态 CAS；heartbeat 可并行推进 version，后验 fence/status/expiry 漂移不得返回成功。Agent tool 只在动作间协作取消，Verification 只在整体启动前验活；二者不在逐操作 fence 内，也不能被强杀。默认缺 secret 时在 provider 注入前失败关闭。ACTIVE promotion/abandon 尚无生产触发，文件句柄/reparse-point 与 OS/container/tenant 隔离也未解决。

## 13. 代码证据索引

| 数据流 | 主要生产证据 |
|---|---|
| Cortex/Governor 与四路分流 | [cortex_runtime.py](../../../host_brain/agent_runtime/cortex_runtime.py)，[governed_task_adapter.py](../../../host_brain/app/governed_task_adapter.py) |
| Host command admission | [governed_command.py](../../../host_brain/orchestration/governed_command.py)，[host_command_repository.py](../../../host_brain/persistence/host_command_repository.py) |
| TaskGraph runtime | [coordinator.py](../../../host_brain/task_runtime/coordinator.py)，[dag_scheduler.py](../../../host_brain/task_runtime/dag_scheduler.py)，[task_repository.py](../../../host_brain/persistence/task_repository.py) |
| TaskNode result artifact | [node_artifact.py](../../../host_brain/contracts/agent_runtime/node_artifact.py)，[task_node_artifact_store.py](../../../host_brain/persistence/task_node_artifact_store.py)，[node_execution_runtime.py](../../../host_brain/task_runtime/node_execution_runtime.py)，[governed_workers.py](../../../host_brain/task_runtime/governed_workers.py) |
| 0099 historical terminal reconciliation | [terminal_state_reconciliation.py](../../../host_brain/task_runtime/terminal_state_reconciliation.py)，[task_terminal_reconciliation_store.py](../../../host_brain/persistence/task_terminal_reconciliation_store.py)，[terminal_reconciliation_query_repository.py](../../../host_brain/persistence/terminal_reconciliation_query_repository.py) |
| Budget contention / narrow recovery | [task_budget_usage_store.py](../../../host_brain/persistence/task_budget_usage_store.py)，[task_operation_checkpoint_store.py](../../../host_brain/persistence/task_operation_checkpoint_store.py)，[operation_recovery_runtime.py](../../../host_brain/task_runtime/operation_recovery_runtime.py)，[accepted_graph_finalization.py](../../../host_brain/task_runtime/accepted_graph_finalization.py) |
| Plan search/compiler | [plan_intent_search.py](../../../host_brain/orchestration/plan_intent_search.py)，[planning_execution.py](../../../host_brain/orchestration/planning_execution.py)，[planning_graph_compiler.py](../../../host_brain/orchestration/planning_graph_compiler.py) |
| Scheduler/Internal | [scheduler/governed_runtime.py](../../../host_brain/scheduler/governed_runtime.py)，[internal_task_runtime.py](../../../host_brain/app/internal_task_runtime.py)（均已接通 canonical admission） |
| Read projection / Executive retirement | [read_routes.py](../../../host_brain/agent_runtime/read_routes.py)，[cortex_runtime.py](../../../host_brain/agent_runtime/cortex_runtime.py)，[runtime.py](../../../host_brain/executive_runtime/runtime.py) |
| Workspace 路径围栏与 W2 Internal worktree | [workspace_paths.py](../../../host_brain/agent_runtime/workspace_paths.py)，[workspace_repository.py](../../../host_brain/persistence/workspace_repository.py)，[workspace_operation_fencing.py](../../../host_brain/persistence/workspace_operation_fencing.py)，[workspace_allocator.py](../../../host_brain/agent_runtime/workspace_allocator.py)，[workspace_runtime.py](../../../host_brain/agent_runtime/workspace_runtime.py)，[workspace_policy.py](../../../host_brain/agent_runtime/workspace_policy.py)，[agent_workspace.py](../../../host_brain/composition/agent_workspace.py) |
| Acceptance effects | [acceptance_effects.py](../../../host_brain/task_runtime/acceptance_effects.py)，[acceptance_effect_dispatcher.py](../../../host_brain/task_runtime/acceptance_effect_dispatcher.py)，[acceptance_effect_handlers.py](../../../host_brain/app/acceptance_effect_handlers.py) |
| Life/Memory post-process | [digital_life/service.py](../../../host_brain/digital_life/service.py)，[life_projection_handlers.py](../../../host_brain/app/life_projection_handlers.py)，[postprocess.py](../../../host_brain/agent_runtime/postprocess.py) |
| Gateway inbound/outbound | [delivery_runtime.py](../../../edge_client/gateway/delivery_runtime.py)，[input_reporter.py](../../../edge_client/channels/input_reporter.py)，[ingress.py](../../../host_brain/input_runtime/ingress.py)，[gateway_transport_repository.py](../../../host_brain/persistence/gateway_transport_repository.py)，[outbound.py](../../../edge_client/gateway/outbound.py) |
| Edge embodiment | [embodiment.py](../../../host_brain/channels/embodiment.py)，[embodiment_outbox_repository.py](../../../host_brain/persistence/embodiment_outbox_repository.py)，[embodiment_runtime.py](../../../edge_client/gateway/embodiment_runtime.py) |
| Memory recall | [chat_retrieval_helpers.py](../../../host_brain/app/chat_retrieval_helpers.py)，[surface_memory_recall.py](../../../host_brain/persona/surface_memory_recall.py) |
| Persona/Scene/World | [persona_engine.py](../../../host_brain/persona/persona_engine.py)，[persona_growth.py](../../../host_brain/persona/persona_growth.py)，[scene_policy.py](../../../host_brain/persona/scene_policy.py)，[surface_runtime/eligibility.py](../../../host_brain/surface_runtime/eligibility.py)，[runtime/world_model.py](../../../host_brain/runtime/world_model.py) |

## 14. 封板口径

本文可以支持的结论是：

```text
Host canonical four-route data flow        WIRED
Durable TaskGraph execution                PARTIAL
Scheduler governed admission               WIRED
Internal governed admission                WIRED
Conversation effects                       POST_ACCEPTANCE_DURABLE_EVENTUAL
Gateway durable transport                  WIRED_WITH_UNCERTAIN_DLQ
Edge as sole physical actuator             WIRED
All Host external effects via one outbox    NOT YET TRUE
Memory canonical write path                 WIRED_EVENTUAL
Persona evolution authority                CENTRAL_FENCE_WIRED / PROPOSAL PIPELINE MISSING
Workspace authority                        PARTIAL_W2_INTERNAL_GIT_WORKTREE_WIRED / DEFAULT FAIL CLOSED / STRONG ISOLATION MISSING
Scene context                               PARTIAL_DEFAULT_OFF
Full Architecture                           INCOMPLETE
```

因此，当前不应使用“全入口统一”、“通用智能规划已完成”、“所有副作用全局原子提交”或“人格已可安全长期自主演化”作为封板结论。
