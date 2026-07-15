受众：PROJECT
标题：图 04——Planning、Task Runtime 与 Legacy 边界

# 图 04：Planning、Task Runtime 与 Legacy 边界

版本：v3.0
核对日期：2026-07-15

文件名保留为 `04_executive_layers.md` 以兼容旧链接，但本图不再把 Executive 描述成 canonical 三层主链。当前 authority 是 Governed Runtime；Executive 只承担尚未删除的 compatibility route。

## 当前分界

```mermaid
flowchart TD
    INTENT["Approved intent"]
    ROUTE{"canonical route?"}

    subgraph Canonical["Canonical governed path"]
        HS["Host route contracts / Plan search"]
        GC["PlanningGraphCompiler"]
        AD["Durable admission"]
        TR["GovernedTaskCoordinator"]
        WK["Frozen worker"]
        AC["Verification / Acceptance"]
        HS --> GC --> AD --> TR --> WK --> AC
    end

    subgraph Legacy["Compatibility path — 待退役"]
        EX["executive_runtime/runtime.py"]
        SD["legacy SkillDagPlan"]
        REG["LegacyRuntimeTaskRegistry\nprocess memory"]
        EX --> SD --> REG
    end

    INTENT --> ROUTE
    ROUTE -->|"chat/skill/delegate/plan"| HS
    ROUTE -->|"non-canonical compatibility"| EX

    style Canonical fill:#eefbf1,stroke:#22863a
    style Legacy fill:#fff3cd,stroke:#b08800
```

`executive_runtime/runtime.py` 会拒绝显式 `chat/skill/delegate/plan` 直接进入 legacy path。只要旧 SkillDag 执行和内存 registry 仍有生产写入，就不能宣布 Executive authority 已完全删除。

## Planning Search 分层

```mermaid
flowchart LR
    REQ["SearchRequest\nobjective + frozen context + budget"]
    L1["L1 candidate expansion"]
    L2["L2 cognitive ranking"]
    COND{"L3 condition met?"}
    L3["Conditional MCTS\nvirtual transitions only"]
    RESULT["SearchResult + trace"]
    COMP["TaskGraph compiler"]
    EXEC["Durable execution"]

    REQ --> L1 --> L2 --> COND
    COND -->|"no"| RESULT
    COND -->|"yes"| L3 --> RESULT
    RESULT --> COMP --> EXEC
```

| 领域 | 当前材料来源 | 当前结论 |
|---|---|---|
| Host Plan | Governor 已批准的 `planned_steps/steps` | 只做合法拓扑选择，未生成竞争方案 |
| Pentest | 授权范围内的 action candidates 与虚拟世界 | L1/L2 接线；L3 shadow 未完成 durable calibration |
| Research | 冻结模拟 fixture | 没有真实来源、模型和 verifier 对照 |
| Code | 冻结模拟 fixture | 没有真实 worktree、测试和成本对照 |

## Scheduler/Internal 故障位置

```mermaid
flowchart LR
    SCH["Scheduler/Internal trigger"]
    OLD["AgentWorkOrder"]
    ADMIT["HostCommand admission\nrequires WorkOrderV3 + BudgetPolicy"]
    FAIL["HOST_COMMAND_INVALID"]

    SCH --> OLD --> ADMIT --> FAIL

    style OLD fill:#ffd9d9,stroke:#b31d28
    style FAIL fill:#ffd9d9,stroke:#b31d28
```

修复不能通过放宽 admission 接受旧合同完成。正确方向是让 Scheduler/Internal 生成完整 WorkOrderV3、TaskGraph、Budget、node grant、Review 和 receipt lineage。

## Graph revision

```mermaid
stateDiagram-v2
    [*] --> Revision1: compile initial plan
    Revision1 --> Attempt1: execute node
    Attempt1 --> Revision1: accepted
    Attempt1 --> FailedEvidence: failed
    FailedEvidence --> Revision2: governed replan
    Revision2 --> Attempt2: execute replacement graph
    Revision1 --> [*]: immutable history
    Revision2 --> [*]: immutable history
```

失败重规划必须保留旧 revision 和失败证据。修改原 graph、在 worker 内追加隐藏步骤、或让 Executive 另建一个无 lineage 的 plan 都会破坏审计。

## 代码锚点

- 公共搜索：`host_brain/planning_search/`
- Host Plan adapter：`host_brain/orchestration/plan_intent_search.py`
- Graph compiler：`host_brain/orchestration/planning_graph_compiler.py`
- Replan/recovery：`host_brain/orchestration/planning_recovery.py`、`failure_replan_intent.py`
- Durable coordinator：`host_brain/task_runtime/coordinator.py`
- Executive guard/legacy：`host_brain/executive_runtime/runtime.py`、`skill_dag.py`
- Legacy registry：`host_brain/app/runtime_task_registry.py`
- Scheduler contracts：`host_brain/scheduler/governed_runtime.py`
- Internal contracts：`host_brain/app/internal_task_runtime.py`

## 目标删除条件

只有当 compatibility route 已迁移、legacy SkillDag 生产调用为零、LegacyRuntimeTaskRegistry 只剩历史读取或被删除，并且所有入口共享同一 Task lineage 时，Executive 才能从架构图中移除。
