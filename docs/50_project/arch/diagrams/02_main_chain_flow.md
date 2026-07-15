受众：PROJECT
标题：图 02——Governed Host 请求主链

# 图 02：Governed Host 请求主链

版本：v2.1
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
    participant W as Frozen Worker
    participant E as Effect Dispatcher

    H->>R: admit(command, WorkOrderV3, graph)
    R->>T: persist graph revision and nodes
    H->>C: execute_stream(graph)
    C->>T: claim READY node / create Attempt and Lease
    C->>T: reserve Budget and idempotency
    C->>W: frozen node input + node-bound grant
    W-->>C: execution events and result
    C->>C: verify(result)
    alt verified and accepted
        C->>T: commit Acceptance + effect intents
        C-->>H: accepted execution receipt
        H->>E: dispatch_graph(graph_id, revision)
        E->>T: commit or retain retryable effect receipt
    else review required
        C->>T: persist checkpoint + WAITING_REVIEW
    else failed
        C->>T: persist failed Attempt and evidence
    end
```

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
3. B1 store/coordinator 子集已接 decision、原子 claim、到期 promotion、store retry deadline/cancel 和 concurrency fence；生产失败尚未进入 retry，真正并行、retry budget、heartbeat/takeover 与 Review continuation 尚未完成。
4. Acceptance effect 是可恢复的最终一致，不是所有外部系统共享一个事务。

## 代码锚点

- HTTP 装配：`host_brain/app/app_factory.py`、`host_brain/app/chat_routes.py`、`host_brain/app/chat_handler.py`
- Cortex 分流：`host_brain/agent_runtime/cortex_runtime.py`
- Governed Host adapter：`host_brain/app/governed_task_adapter.py`
- Direct contracts：`host_brain/orchestration/host_route_contracts.py`
- Plan search/compiler：`host_brain/orchestration/plan_intent_search.py`、`planning_graph_compiler.py`
- Admission：`host_brain/orchestration/planning_admission.py`、`host_brain/persistence/host_command_repository.py`
- Task execution：`host_brain/task_runtime/coordinator.py`、`host_brain/task_runtime/dag_scheduler.py`
- Read projection：`host_brain/agent_runtime/read_routes.py`
- Node grant：`host_brain/orchestration/node_grants.py`
- Acceptance effects：`host_brain/task_runtime/acceptance_effects.py`、`acceptance_effect_dispatcher.py`
- Retired Executive guard：`host_brain/executive_runtime/runtime.py`
