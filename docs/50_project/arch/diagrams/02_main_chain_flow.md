受众：PROJECT
标题：图 02——Governed Host 请求主链

# 图 02：Governed Host 请求主链

版本：v2.3
核对日期：2026-07-15

本文画的是 `chat/skill/delegate/plan` canonical route。它替换了旧图中“所有请求都由 Executive Runtime 决策并直接后处理”的错误主链。

## 请求总览

```mermaid
flowchart TD
    REQ["HTTP / Host input request"]
    ROUTE["FastAPI adapter\nchat route / input route"]
    AIR["Airlock / PII / Secure Ingress"]
    INTENT["Intent draft"]
    GOV["Official Governor decision"]
    CORTEX["Cortex route gate"]
    SUPPORT{"route in\nchat/skill/delegate/plan?"}
    GR["GovernedHostRuntime"]
    BUILD["HostCommand + WorkOrderV3 + BudgetPolicy"]
    PLAN{"route=plan?"}
    SEARCH["approved-step search\n+ graph compiler"]
    DIRECT["direct route graph builder"]
    ADMIT["HostCommandRepository admission"]
    TASK["GovernedTaskCoordinator"]
    NODE["Node + Attempt + Lease + Budget + Grant"]
    WORK["Frozen worker"]
    VERIFY["Verification"]
    ACCEPT["Acceptance"]
    EFFECT["Durable effect intents"]
    DISPATCH["AcceptanceEffectDispatcher"]
    SSE["SSE / reply projection"]
    READ["Durable read projection\ntask_progress / recent_turn"]
    REJECT["Unsupported route\nfail closed"]

    REQ --> ROUTE --> AIR --> INTENT --> GOV --> CORTEX --> SUPPORT
    SUPPORT -->|"yes"| GR --> BUILD --> PLAN
    PLAN -->|"yes"| SEARCH --> ADMIT
    PLAN -->|"no"| DIRECT --> ADMIT
    ADMIT --> TASK --> NODE --> WORK --> VERIFY --> ACCEPT --> EFFECT --> DISPATCH --> SSE
    SUPPORT -->|"read route"| READ --> SSE
    SUPPORT -->|"unsupported"| REJECT

    style GR fill:#d7f5df,stroke:#22863a
    style TASK fill:#d7f5df,stroke:#22863a
    style READ fill:#d7f5df,stroke:#22863a
    style REJECT fill:#ffd9d9,stroke:#b31d28
```

## Task 节点时序

```mermaid
sequenceDiagram
    participant H as GovernedHostRuntime
    participant R as HostCommandRepository
    participant T as DurableTaskStore
    participant C as GovernedTaskCoordinator
    participant B as DurableExecutionHeartbeat
    participant W as Frozen Worker
    participant E as Effect Dispatcher

    H->>R: admit(command, WorkOrderV3, graph)
    R->>T: persist graph revision and nodes
    H->>C: execute_stream(graph)
    C->>T: claim READY node / create Attempt and Lease
    C->>T: reserve Budget
    alt pre-reservation resolver failed
        C->>T: atomic Attempt/Node/Graph/Lease terminal bundle + 4 Outboxes
        C-->>H: failed receipt
    else resolver ready
        C->>T: reserve operation idempotency
        C->>B: start + attach reservation
        C->>W: frozen node input + node-bound grant
        loop worker execution
            B->>T: renew Lease, then Reservation
            W-->>C: execution event / final result
            C->>B: checkpoint
        end
        C->>B: stop and join
        alt heartbeat failed
            C-->>H: RECOVERY_REQUIRED; never COMMIT
        else heartbeat healthy
            C->>T: COMMIT with latest Reservation version
            C->>C: verify(result)
            alt verified and accepted
                C->>T: commit Acceptance + effect intents
                C-->>H: accepted execution receipt
                H->>E: dispatch_graph(graph_id, revision)
                E->>T: commit or retain retryable effect receipt
            else review required
                C->>T: persist checkpoint + WAITING_REVIEW
            end
        end
    end
```

## 默认关闭的 fan-out 影子路径

```mermaid
flowchart TD
    S["compute_dag_schedule"] --> Q{"selected > 1 且\n仍有未 ACCEPTED 后继/剩余节点?"}
    Q -->|"否 / 生产默认路径"| SEQ["顺序执行"]
    Q -.->|"bounded_fanout_enabled=true\n默认 OFF，生产未启用"| ACTIVATE["outer 在 spawn 前激活 Graph"]
    ACTIVATE --> LIMIT["BudgetPolicy concurrency 有界 child"]
    LIMIT --> NODE["child deferred authority\nAttempt / Node / Lease / Reservation\nBudget usage / EXCEEDED"]
    LIMIT --> MERGE["progress slots 默认 32，慢消费者反压\n每 child 预留 control slot"]
    NODE --> WAIT["正常非接受不 fail-fast\n等待所有已启动 sibling 落稳"]
    WAIT --> SNAP["receipt ↔ durable Node/latest Attempt\n完整 node status/version + stable Attempt\n零 ACTIVE Lease / 未决 Reservation\ntaskbatch://sha256/&lt;digest&gt;"]
    SNAP --> DECIDE{"批次结果"}
    DECIDE -->|"failure"| FAILED["Graph FAILED\nBudget RELEASED"]
    DECIDE -->|"Budget overrun"| BUDGET["Graph BLOCKED\nBudget RELEASED"]
    DECIDE -->|"post-review"| REVIEW["Graph BLOCKED\nBudget 保持 ACTIVE"]
    DECIDE -->|"全部 ACCEPTED"| ORDER["按 admission 顺序校验 outcomes\n继续调度"]
    NODE -->|"Operation Reservation 未决"| RECOVERY["RECOVERY_REQUIRED\nGraph/Budget 不终结"]
    FAILED --> ATOMIC["Graph + 可选 Budget + Outbox\n单事务 + exact replay"]
    BUDGET --> ATOMIC
    REVIEW --> ATOMIC
    ATOMIC --> PUB["Outbox publish\nCoordinator asyncio lock + 共享 publisher 实例锁"]
    ORDER --> FANIN["依赖均 ACCEPTED 后推进 fan-in"]
    P0["P0：terminal-leaf batch 与最终成功终态\n仍走顺序路径"]
    P0 -.-> SEQ
    P1["P1：跨进程 Outbox drain\n仍缺 durable claim/CAS"] -.-> PUB
    CANCEL["外层取消：cancel + join child / heartbeat\n无 durable cancel fence"] -.-> LIMIT
    CAS["P1：Budget CAS 固定重试上限\nALREADY_EXCEEDED 高竞争语义未收敛"] -.-> NODE

    style LIMIT fill:#fff3cd,stroke:#a87500
    style P0 fill:#ffd9d9,stroke:#b31d28
    style P1 fill:#fff3cd,stroke:#a87500
    style CANCEL fill:#fff3cd,stroke:#a87500
    style CAS fill:#fff3cd,stroke:#a87500
    style RECOVERY fill:#fff3cd,stroke:#a87500
```

实验证据证明 concurrency=2 的 root 真重叠、concurrency=1 保持串行、size=1 时慢消费者会反压 child；每 child 的保留 control slot 还保证自发 `CancelledError` 不会让 supervisor 永久挂起。正常 pre-reservation failure 不再取消仍运行的 sibling；待所有 sibling 落稳后，outer 单次提交 Graph `FAILED` + Budget `RELEASED`，且首次结算响应丢失只 exact replay、不重复 Outbox。Budget overrun 与 post-review 也有对应证据。receipt↔latest Attempt/Node、完整快照及 Lease/Reservation fence 共同约束裁决；post-reservation 未决副作用保持 `RECOVERY_REQUIRED`。但该图只描述 live batch：crash/re-entry 若已看到 FAILED/review Node 仍走旧分步终态路径，taskbatch 也只有摘要而无 canonical payload。该图不是 production wiring；terminal-leaf、durable cancel、自动 recovery continuation 与跨进程 Outbox claim 均未闭环。

## Plan 分支的真实边界

```mermaid
flowchart LR
    GOAL["Governor-approved plan intent"]
    STEPS["planned_steps / steps"]
    ORDER["PlanningSearchService\nlegal topology selection"]
    GRAPH["immutable TaskGraph"]

    GOAL --> STEPS --> ORDER --> GRAPH
    MISSING["目标 → 多套竞争方案\n当前未实现"]
    MISSING -."TARGET".-> ORDER

    style MISSING fill:#ffd9d9,stroke:#b31d28
```

当前 Host Plan 不会新增步骤、发明工具组合或比较多套方案。搜索内核存在，但这里主要用于选择完整合法的拓扑顺序。

## 当前例外与缺口

1. `task_progress`、`recent_turn` 已走 durable projection，不创建 Task；未知 route 直接拒绝。
2. Scheduler 与 Internal 已接入同一 canonical admission；Internal v2 只保留在授权验证后的 executor 兼容边界。
3. `PARTIAL_B3_OUTER_FANIN_TERMINAL` 仅在默认关闭、生产未启用的单次 live 非 terminal-leaf fan-out 中，由 outer 原子提交可结算 failure/Budget/review 的 Graph、可选 Budget `RELEASED` 与 Outbox；crash/re-entry 仍走旧路径，`taskbatch` 仅摘要。terminal-leaf 并行、durable cancel fence、跨进程 Outbox claim/CAS、Budget CAS 高竞争语义、built-in retry marker、online takeover、即时 reconciliation 与 Review continuation 尚未完成。
4. Acceptance effect 是可恢复的最终一致，不是所有外部系统共享一个事务。
5. 启动按 migration → `recover_startup` → recovery outbox publish → runtime activation 排序，恢复异常 fail closed；配置逻辑 owner 会派生为每次 container construction 唯一的 execution owner。这不代表自动 continuation、在线 takeover 或即时 reconciliation 已完成。

## 代码锚点

- HTTP 装配：`host_brain/app/app_factory.py`、`host_brain/app/chat_routes.py`、`host_brain/app/chat_handler.py`
- Cortex 分流：`host_brain/agent_runtime/cortex_runtime.py`
- Governed Host adapter：`host_brain/app/governed_task_adapter.py`
- Direct contracts：`host_brain/orchestration/host_route_contracts.py`
- Plan search/compiler：`host_brain/orchestration/plan_intent_search.py`、`planning_graph_compiler.py`
- Admission：`host_brain/orchestration/planning_admission.py`、`host_brain/persistence/host_command_repository.py`
- Task execution：`host_brain/task_runtime/coordinator.py`、`host_brain/task_runtime/heartbeat.py`、`host_brain/task_runtime/dag_scheduler.py`
- Read projection：`host_brain/agent_runtime/read_routes.py`
- Node grant：`host_brain/orchestration/node_grants.py`
- Acceptance effects：`host_brain/task_runtime/acceptance_effects.py`、`acceptance_effect_dispatcher.py`
- Retired Executive guard：`host_brain/executive_runtime/runtime.py`
