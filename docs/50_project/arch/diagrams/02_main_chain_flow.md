受众：PROJECT
标题：图 02——Governed Host 请求主链

# 图 02：Governed Host 请求主链

版本：v2.5
核对日期：2026-07-16

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
    POLICY["TaskGraph completion_policy\nlegacy 或显式 ATOMIC_GRAPH_SUCCESS_V1"] --> GATE["atomic capability + node-grant service\n+ parent grant：admission 前校验"]
    GATE --> SCHEMA["0090 + 0092；atomic 时 0091 schema ready?\nBudget / worker 前 fail-fast"]
    SCHEMA --> S["compute_dag_schedule"]
    S --> Q{"selected > 1 且\n有后继/剩余节点或 atomic policy?"}
    Q -->|"否 / 生产默认路径"| SEQ["顺序执行"]
    Q -.->|"bounded_fanout_enabled=true\n默认 OFF，生产未启用"| ACTIVATE["outer 在 spawn 前激活 Graph"]
    ACTIVATE --> LIMIT["BudgetPolicy concurrency 有界 child"]
    LIMIT --> NODE["child deferred authority\nAttempt / Node / Lease / Reservation\nBudget usage / EXCEEDED"]
    LIMIT --> MERGE["progress slots 默认 32，慢消费者反压\n每 child 预留 control slot"]
    NODE --> WAIT["正常非接受不 fail-fast\n等待所有已启动 sibling 落稳"]
    WAIT --> SNAP["live / ACTIVE crash-re-entry\ndurable Node/latest Attempt outcomes\n完整 snapshot + stable Attempt\n零 ACTIVE Lease / 未决 Reservation"]
    REENTRY["重启后 ACTIVE 多节点 Graph"] -.-> LIVE{"live Node / active Attempt / ACTIVE Lease?"}
    LIVE -->|"是"| INPROGRESS["GOVERNED_EXECUTION_IN_PROGRESS\n不 resolve worker / 不抢 authority"]
    LIVE -->|"否"| SNAP
    SNAP --> CANON["整个 admitted Graph durable terminal Nodes\n+ latest stable Attempts / Reservation / BudgetUsage\n含非 selected RETRY_PENDING\n重建 live/re-entry 同一 payload/ref"]
    CANON --> DECIDE{"批次结果"}
    DECIDE_NOTE["优先级：recovery > Budget > failure > review"] -.-> DECIDE
    DECIDE -->|"failure"| FAILED["Graph FAILED\nBudget RELEASED"]
    DECIDE -->|"Budget overrun"| BUDGET["Graph BLOCKED\nBudget RELEASED"]
    DECIDE -->|"post-review"| REVIEW["Graph BLOCKED\nBudget 保持 ACTIVE"]
    DECIDE -->|"全部 ACCEPTED"| SUCCESS_POLICY{"ATOMIC_GRAPH_SUCCESS_V1?"}
    SUCCESS_POLICY -->|"否"| ORDER["按 admission 顺序校验 outcomes\n继续调度"]
    SUCCESS_POLICY -->|"是"| PROJECTION["所有 Node/Attempt/Reservation ACCEPTED\ncanonical projection 重算 digest\n绑定 command/request/route/target/grant"]
    PROJECTION --> RESULT["仅显式 result projection\n编译全图 effects 与公开结果"]
    RESULT --> SUCCESS_UOW["单 UoW：Graph COMPLETED + Budget CONSUMED\n两 Outbox + effects + 0091 decision"]
    SUCCESS_UOW --> OUTBOX_REV
    SUCCESS_UOW --> REPLAY["completed replay 只读 0091\n双响应丢失重读 terminal authority"]
    NODE -->|"Operation Reservation 未决"| RECOVERY["RECOVERY_REQUIRED\nGraph/Budget 不终结"]
    FAILED --> ATOMIC["Graph + 可选 Budget + Outbox\n+ 0090 canonical v2 decision\n单事务 + exact replay"]
    BUDGET --> ATOMIC
    REVIEW --> ATOMIC
    ATOMIC --> MATCH["terminal replay：Graph target + status_version 精确匹配\nReview BLOCKED→FAILED 不泄漏旧 ref"]
    BUDGET --> USAGE["selected=BUDGET_BLOCKED 绑定 rejected usage\n被动 blocked sibling 无需本地 usage"]
    ATOMIC --> OUTBOX_REV["0095 Graph revision + payload\nID/key 升级前后稳定\n歧义 producer 整笔失败"]
    OUTBOX_REV --> PUB["0093 durable Outbox claim\nboot/recovery-scoped 唯一 owner"]
    PUB --> APPEND["逐条：owner + token + expiry + 单调 fence\n幂等 append runtime event"]
    APPEND --> COMPLETE["fenced complete\n失败 release + durable backoff/error"]
    APPEND --> EVENT_REV["新 Event = graph_id@revision\nsource_ref 一 Outbox 一 Event\nlegacy 重投 exact replay"]
    TAKEOVER["后台 worker 按 revisioned aggregate-head wake 自动续投\nexpired claim 可 takeover\n同 revision 严禁越过 predecessor"] -.-> PUB
    TAKEOVER --> DRAIN["canonical total_pending=0 才 DRAINED\nclaim/backoff/blocked backlog 为 DEFERRED"]
    ORDER --> FANIN["依赖均 ACCEPTED 后推进 fan-in"]
    OFF["atomic_graph_success_enabled 与 bounded_fanout_enabled\n独立默认 OFF；三处生产 composition OFF"] -.-> GATE
    CANCEL_UI["Task Runtime UI\nexact Graph revision"] -.-> CANCEL_BFF["同源 Operator BFF\ntask_graph.cancel"]
    CANCEL_BFF -.-> CANCEL_GATE["Operator Command Gateway\nobs:write + task-runtime:cancel"]
    CANCEL_OFF["feature flag 默认 OFF\n非 ENFORCE / Gateway 不可用\n启动 fail closed"] -.-> CANCEL_GATE
    CANCEL_GATE -.-> CANCEL_ENTRY["共享 Secure Ingress verifier\ncommitted audit/claim + canonical body"]
    CANCEL_ENTRY -.-> CANCEL_ROUTE["immutable HostCommand surface\nHost / Internal / Scheduler"]
    CANCEL_ROUTE -.-> CANCEL_INTENT["0092 phase 1 UoW\nintent + Graph BLOCKED + durable fences"]
    CANCEL_ROUTE -.-> CANCEL_UNCERTAIN["下游异常后读 exact durable intent\n无 intent=未执行；匹配=可能已执行\n无法证明则保守 uncertain"]
    CANCEL_INTENT -.-> CANCEL_LOCAL["intent 提交后才 cancel/join\n本地 child + heartbeat"]
    CANCEL_INTENT -.-> CANCEL_RECON["active ownership / operation / effect / review\nadvance + reconciliation gate"]
    CANCEL_SCAN["0094 composition-owned scanner\nrecovery-scoped owner"] -.-> CANCEL_CLAIM["coordination claim + heartbeat\nexpired takeover / stale fence"]
    CANCEL_CLAIM -.-> CANCEL_ADMISSION["HostCommandRepository exact revision\n重建 replay admission"]
    CANCEL_ADMISSION -.-> CANCEL_RECON
    CANCEL_RECON -.->|"durable facts 未收敛"| CANCEL_DEFER["durable defer\n不伪造 cancellation 完成"]
    CANCEL_DEFER -.-> CANCEL_SCAN
    CANCEL_RECON -.-> CANCEL_FINAL["phase 2 UoW\nGraph CANCELLED + Budget + Outbox + decision"]
    CANCEL_FINAL -.-> CANCEL_COMPLETE["0092 FINALIZED + decision 后\n清理 0094 协调行"]
    CANCEL_FINAL -.-> OUTBOX_REV
    EFFECT_RULE["accepted NODE effect 继续 delivery\n其他未授权 NODE/GRAPH effect 被 fence"] -.-> CANCEL_RECON
    FIRST["0091 success / 0092 cancel\nfirst-writer-wins + exact replay"] -.-> CANCEL_INTENT
    CAS["P1：Budget CAS 固定重试上限\nALREADY_EXCEEDED 高竞争语义未收敛"] -.-> NODE
    HISTORY["P1：历史 BLOCKED+failure / FAILED+ACTIVE Budget\n归 reconciliation，不由 ACTIVE 重入自动修复"] -.-> REENTRY
    REVIEW_QUEUE["0096 Review terminal mutation + enqueue\n同一事务；不复制业务 status"] --> REVIEW_CLAIM["claim + heartbeat + expired takeover\nowner/token/expiry/monotonic fence"]
    REVIEW_CLAIM --> REVIEW_ROUTE["immutable HostCommand surface\n重入 Host/Internal/Scheduler"]
    REVIEW_ROUTE --> REENTRY
    PREVIEW["WAITING_APPROVAL 合法 attempt_id=null\nPOST 仍绑定原 SUCCEEDED Attempt"] --> SNAP

    style LIMIT fill:#fff3cd,stroke:#a87500
    style OFF fill:#fff3cd,stroke:#a87500
    style TAKEOVER fill:#d1e7dd,stroke:#177245
    style OUTBOX_REV fill:#d1e7dd,stroke:#177245
    style EVENT_REV fill:#d1e7dd,stroke:#177245
    style CANCEL_ENTRY fill:#fff3cd,stroke:#a87500
    style CANCEL_UI fill:#fff3cd,stroke:#a87500
    style CANCEL_BFF fill:#fff3cd,stroke:#a87500
    style CANCEL_GATE fill:#fff3cd,stroke:#a87500
    style CANCEL_OFF fill:#fff3cd,stroke:#a87500
    style CANCEL_ROUTE fill:#fff3cd,stroke:#a87500
    style CANCEL_UNCERTAIN fill:#fff3cd,stroke:#a87500
    style CANCEL_INTENT fill:#fff3cd,stroke:#a87500
    style CANCEL_LOCAL fill:#fff3cd,stroke:#a87500
    style CANCEL_SCAN fill:#fff3cd,stroke:#a87500
    style CANCEL_CLAIM fill:#fff3cd,stroke:#a87500
    style CANCEL_ADMISSION fill:#fff3cd,stroke:#a87500
    style CANCEL_RECON fill:#fff3cd,stroke:#a87500
    style CANCEL_DEFER fill:#fff3cd,stroke:#a87500
    style CANCEL_FINAL fill:#fff3cd,stroke:#a87500
    style CANCEL_COMPLETE fill:#fff3cd,stroke:#a87500
    style EFFECT_RULE fill:#fff3cd,stroke:#a87500
    style FIRST fill:#fff3cd,stroke:#a87500
    style CAS fill:#fff3cd,stroke:#a87500
    style HISTORY fill:#fff3cd,stroke:#a87500
    style REVIEW_QUEUE fill:#d1e7dd,stroke:#177245
    style REVIEW_CLAIM fill:#d1e7dd,stroke:#177245
    style REVIEW_ROUTE fill:#d1e7dd,stroke:#177245
    style PREVIEW fill:#d1e7dd,stroke:#177245
    style RECOVERY fill:#fff3cd,stroke:#a87500
    style INPROGRESS fill:#fff3cd,stroke:#a87500
```

0090/B4 与 0091/B5 的既有语义保持不变。0093 已收口 Outbox 跨进程投递；0094 收口 pending cancellation coordination；0095 收口 revisioned Graph Outbox/Event/Operator identity；0096 收口 task-bound Review continuation；0097 收口 node-grant batch 跨进程唯一性。ReviewStore 的终态 mutation 与 coordination enqueue 原子提交，worker 按原 surface 重入；PRE 不造 Attempt，POST 不重跑 worker。credential 覆盖 budget window，但 READY 与首次 worker 校验仍受 live claim fence 约束，随后由 Task lease 管已开始执行。0092 intent/decision 仍是取消业务 authority；operation/effect/review cancellation facts 未 reconciliation 时只 durable defer。Cancellation 的共享生产 verifier、Gateway/BFF/UI 与 exact surface 路由已接线但默认 OFF；部署凭据与真实 canary仍缺。Review 决策已有独立 `review:decide` policy，但真实 canary 也未完成。

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
3. `PARTIAL_B5C_DURABLE_CANCELLATION_SHADOW / PRODUCTION_OFF`：0091 atomic success、0092 two-phase cancellation、0093 delivery、0094 cancellation coordination、0095 revisioned identity、0096 Review continuation 与 0097 grant uniqueness 已有证据。Cancellation Operator authority/入口为 `WIRED_DEFAULT_OFF`；取消业务 reconciliation、历史修复、Budget 高竞争、部署凭据和真实 canary尚未完成。
4. Acceptance effect 是可恢复的最终一致，不是所有外部系统共享一个事务。
5. 启动按 migration/readiness → `recover_startup` → recovery outbox publish → outbox worker → surface runtime activation → cancellation/Review workers 排序，恢复异常 fail closed；配置逻辑 owner 会派生为每次 container construction 唯一的 execution owner。0093/0094/0096 coordination 都支持过期 takeover，但 0094 未收敛 operation/effect 事实时只 durable defer，不能据此宣称完整业务 reconciliation 已完成。

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
