受众：MASTER
标题：Natsume AI 当前代码架构事实

# Natsume AI 当前代码架构事实

版本：v5.0
核对日期：2026-07-15
代码基线：`origin/dev@9b6db346`，叠加仓库清理 `f0cd1dfd`
状态：`CODE_FACTS / FULL_ARCHITECTURE_INCOMPLETE`

本文只回答“代码现在是什么”。目标、执行顺序和历史结论分别由 [TARGET_ARCHITECTURE.md](TARGET_ARCHITECTURE.md)、[NEXT_GENERATION_EVOLUTION_EXECUTION.md](../../30_devspec/NEXT_GENERATION_EVOLUTION_EXECUTION.md) 与 [历史程序目录](../../_history/programs/2026-07/README.md) 承担。若本文与代码冲突，以生产代码和生产 composition 为准。

## 1. 结论

当前系统已经建立较完整的持久化、授权、事件、模型、设备和 Host 四路线执行地基，但还不是完整的下一代架构。

| 能力 | 当前定级 | 代码事实 |
|---|---|---|
| Core Infrastructure | `AVAILABLE` | migration、journal、outbox、model attempt、capability broker、Life、Edge、Query 等主干已存在 |
| Host `chat/skill/delegate/plan` | `WIRED` | 四条路线进入 `GovernedHostRuntime`，Executive 对它们 fail closed |
| Scheduler / Internal Agent | `BROKEN` | 仍提交 `AgentWorkOrder`，但 admission 已强制 `WorkOrderV3 + BudgetPolicy` |
| 原生多节点 Durable TaskGraph | `PARTIAL` | 依赖推进、Attempt、Lease、Idempotency、Budget、Review、Verification、Acceptance 已有；节点并发和 retry/backoff 未消费 |
| 通用 PlanningSearch 内核 | `AVAILABLE` | 领域无关 contracts、ports、L1/L2/L3 算法存在 |
| Host 智能候选规划 | `NOT_IMPLEMENTED` | Host Plan 只对已批准 `planned_steps` 做拓扑合法化，不生成竞争方案 |
| Pentest L3 | `WIRED_SHADOW_UNCALIBRATED` | Conditional MCTS 可运行，但校准非 durable，不能证明 production uplift |
| Code/Research 收益 | `SIMULATION_ONLY` | 只有冻结模拟 fixture；没有真实仓库、真实来源或真实模型 shadow |
| Budget | `HOST_WIRED / PROGRAM_PARTIAL` | Host 四路线有 durable ledger；并发预算与 DEGRADE/REVIEW 策略未完整执行 |
| Review/Resume | `MANUAL_CONTINUATION` | token 与 checkpoint 已持久化；resume 不会自动重新调度原图 |
| 对话副作用 | `POST_ACCEPTANCE_EVENTUAL` | Acceptance 原子写 effect intents；user/assistant/delivery 实际应用不是一个全局事务 |
| Composition | `INCOMPLETE` | 仍有模块级 `ProcessServiceContainer` 和大量 `get_*()` service locator |
| Persona 演化 authority | `UNSAFE_DIRECT_WRITERS` | Bond stage/milestone、interaction cycle 与 scheduler-allowed emergent weekly 等 legacy writers 仍可直接 `apply_growth_delta()`，绕过 proposal、shadow、version activation 与 rollback |
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
- `host_brain/orchestration/governed_command.py`
- `host_brain/task_runtime/coordinator.py`
- `host_brain/task_runtime/acceptance_effects.py`
- `host_brain/task_runtime/acceptance_effect_dispatcher.py`
- `host_brain/persistence/task_repository.py`

### 3.1 已接线范围

`chat`、`skill`、`delegate`、`plan` 被 Cortex 识别为 canonical governed route。它们不能回退到 Executive：`host_brain/executive_runtime/runtime.py` 对这四种 route 直接拒绝。

### 3.2 未闭环范围

1. `task_progress`、`recent_turn` 仍可进入 Executive compatibility path。
2. Executive 仍能创建和执行 legacy `SkillDagPlan`，并写入进程内 `LegacyRuntimeTaskRegistry`。
3. Scheduler 与 Internal Agent 虽随 Host lifecycle 启动，但构造的仍是 `AgentWorkOrder`：
   - `host_brain/scheduler/governed_runtime.py::_build_contracts`
   - `host_brain/app/internal_task_runtime.py::_build_contracts`
4. `HostCommandRepository._normalize_admission()` 只接受带显式 `BudgetPolicy` 的 `WorkOrderV3`，因此上述两路首个真实任务会以 `HOST_COMMAND_INVALID` 失败。

## 4. TaskGraph、预算、Review 与副作用

### 4.1 Durable TaskGraph

当前 coordinator 能持久化多节点图、阻断未知依赖和环、推进 `BLOCKED → READY`、创建 Attempt/Lease/Reservation，并在 Verification 与 Acceptance 后完成节点。

仍缺：

- claimable 节点当前串行执行，没有 DAG 级 fan-out 并发；
- 已有 attempt 后不会创建下一 attempt，`retry_policy.max_attempts` 与 backoff 没有成为运行时行为；
- Scheduler/Internal 修复 WorkOrder 后仍需补 node-bound child grant、Budget 与 Review composition。

### 4.2 Budget

`host_brain/task_runtime/budget_runtime.py` 和 TaskStore usage ledger 已在 Host 四路线执行预扣、计量和结算。真实计量覆盖时间、dispatch、显式 provider token/cost、ModelAttempt retry 与工具步数。

限制：

- provider 未返回的 token/cost 保持 `UNMEASURED`，不做伪精确估算；
- concurrency 字段没有运行时 charge；
- 超限后的 `DEGRADE`、`REVIEW` 尚未形成完整分支，当前核心路径主要阻断；
- Scheduler/Internal 尚未进入这条可用主链。

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
| Scheduler | `SchedulerDefinitionRepository` 等 | 持久层存在，governed execution admission 仍坏 |
| Review | `ReviewStore` | token 持久化，continuation 非自动 |
| Runtime Event / Outbox | canonical SQLite journal/outbox | 关键事件与投影可恢复 |
| Model invocation | `ModelAttemptService` | 统一 route/attempt/trace 主干 |
| Capability execution | `CapabilityBroker` + execution journal | 签名和授权主干存在 |
| Digital Life | Life command/event/reducer/projection | 单核资产已建立 |
| Memory / Canon | typed repositories + governed apply | projection/cache 不应成为 writer |
| Edge physical effect | Edge actuator + durable receipt | 真实硬件完整验收仍受环境限制 |
| Operator read | `QueryProjectionStore` | Query Plane 以 projection 为主 |
| Legacy task compatibility | `LegacyRuntimeTaskRegistry` | 仍由 Executive 写入，待删除 |

## 7. Composition 现状

`ProcessTaskRuntimeContainer` 是较好的显式 typed composition：它拥有 migration、TaskStore、ReviewStore、CommandRepository、EventStore 与 lifecycle。

但全局对象图尚未收敛：

- `host_brain/composition/process_service_container.py` 在模块级创建 `_PROCESS_SERVICE_CONTAINER`；
- container 约有 61 个可变 service 槽位；
- `process_services.py` 与 `process_runtime_services.py` 暴露约 63 个 `get_*()` accessor；
- 约 79 个生产 Python 文件直接或间接依赖该 locator；
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
| Persona Growth | `persona_growth.py`、Bond stage、Evolution Orchestrator、emergent growth skill | stage/milestone/interaction/emergent 多条生产可达或已注册路径可直接调用 `apply_growth_delta()`，形成绕过 Review/版本激活/回滚的 legacy writers |
| Knowledge OS | Memory、Retrieval、Canon、Markdown KB | 缺统一 provenance、版本、过期、冲突与 Decision Record 生命周期 |
| Identity | `release/identity_snapshot.py` | snapshot/diff/review/rollback 合同存在；gate 默认关闭，未形成真实 migration executor |
| Runtime Guardian | health、degradation、heartbeat、service catalog | degradation/recovery evidence 已接部分生产链；Catalog startup coordinator 未进生产 composition、默认 `AUDIT`，前门与 SLO owner 不完整 |
| Workspace Isolation | `agent_runtime/workspace_policy.py` | 有执行器接线，但仍允许任意 path/`runtime_repo`，字符串前缀边界存在误判风险；不是 OS/tenant/worktree 强隔离 |

## 9. 仓库边界

- 顶层 `data/`、`flows/`、`skills/`、`integrations/` 已删除。
- 可变运行数据进入 `runtime/`，测试/验收输出进入被忽略的 `artifacts/`。
- 内置 Skill 代码保留在 `host_brain/skills/`；外部 bundle 只能经过签名接收与隔离，不能靠目录扫描激活。
- 跨进程合同保留在 `natsume_contracts/`。

## 10. 当前最高优先级缺口

1. 先中央 fail closed 所有 Persona 直接 trait writer（stage、milestone、interaction、emergent 及后续新增调用点），禁止任何演化绕过 proposal、Review、版本激活与 rollback。
2. 修复 Scheduler/Internal 的 `WorkOrderV3 + Budget + node grant + Review` admission。
3. 为 Durable DAG 实现真实 retry/backoff、并发调度与自动 continuation。
4. 删除 Executive SkillDag 与 `LegacyRuntimeTaskRegistry` 的剩余生产写入。
5. 删除全局 Service Locator，建立 Host/Obs/Worker 显式 composition scopes。
6. 让 PlanningSearch 从真实目标生成可竞争后继，并以 Research/Code production shadow 证明收益。
7. 在上述执行地基闭环后，再推进 Evolution Lab、Skill Compiler、Knowledge OS 与 Identity Migration。

这些缺口的唯一活动执行顺序见 [NEXT_GENERATION_EVOLUTION_EXECUTION.md](../../30_devspec/NEXT_GENERATION_EVOLUTION_EXECUTION.md)。
