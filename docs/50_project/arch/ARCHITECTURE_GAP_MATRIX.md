受众：MASTER
标题：Natsume AI 当前架构差距矩阵

# Natsume AI 当前架构差距矩阵

版本：v1.5
核对日期：2026-07-16
机器状态：[current_architecture_status.json](../../../configs/release/current_architecture_status.json)
总体状态：`INCOMPLETE`

本文件是“目标与当前状态如何解释”的唯一入口。历史 receipt 保留原样，但只能证明其声明范围；本矩阵按当前生产代码重新定级。

## 1. 状态定义

| 状态 | 含义 |
|---|---|
| `WIRED` | 生产 composition 与真实调用路径均已接线，关键失败模式有证据 |
| `PARTIAL` | 有真实实现或局部接线，但缺少范围、策略或恢复语义 |
| `BROKEN` | 看似接线，但真实首次调用会违反当前合同或失败 |
| `UNSAFE_DIRECT_WRITERS` | 一个或多个生产 writer 绕过目标要求的 proposal、Review、版本激活或 rollback authority |
| `CENTRAL_FENCE_WIRED` | 旧写入已在最终 authority 边界中央拒绝并有静态门禁，但替代的受治理写入闭环尚未建立 |
| `SHADOW` | 只观察/模拟，不影响生产选择 |
| `PLACEHOLDER` | 只有占位策略、字段或空壳接口 |
| `MISSING` | 没有满足目标语义的实现 |
| `HISTORICAL_PASS` | 仅对固定提交与固定范围成立的历史证据 |

## 2. 执行与规划

| 目标 | 当前状态 | 代码/证据 | 实际缺口 | 执行波 |
|---|---|---|---|---|
| Host 四路线 governed | `WIRED` | `cortex_runtime.py`、`governed_task_adapter.py`、GR-06 live receipt | 只覆盖 `chat/skill/delegate/plan` | NE-01 守卫回归 |
| Scheduler governed | `WIRED` | Scheduler production cutover integration | `WorkOrderV3`、Budget、child grant、Review、Verification、Acceptance、Receipt 与 replay 已贯通 | NE-01 已完成切片 |
| Internal Agent governed | `WIRED` | Internal production cutover integration | ingress v2 立即编译 V3；Budget、child grant、PRE/POST Review、Verification、Acceptance、Receipt、replay 已贯通；长 Review 的 grant renewal 随 continuation 统一处理 | NE-01 已完成切片 |
| 全 route universal runtime | `PARTIAL` | Cortex governed/read split + Scheduler/Internal production integrations | Host 四执行路由、Scheduler、Internal 已 governed；`task_progress/recent_turn` 已转只读投影；完整 Operator/Gateway surface matrix 仍需最终验收 | NE-01 |
| Executive authority 退役 | `WIRED` | Cortex ports、retired runtime guard、registry absence contract | 生产入口、legacy SkillDag 执行和进程内 registry 已删除 | NE-01 已完成切片 |
| 多节点 Durable DAG | `PARTIAL_B5C_DURABLE_CANCELLATION_SHADOW / PRODUCTION_OFF` | Coordinator、ParallelSuccessRuntime/Records、ParallelSettlementRuntime/Projection、TaskStore、0090/0091/0092 decisions、0093 Outbox delivery claim、0094 cancellation continuation store/worker、0095 revisioned Graph Outbox/Event/Operator identity、graph cancellation contract/store tests、worker projection store | B5A/B5B 成功封口继续有效；0093 已收口 Task Runtime Outbox 跨进程投递。0094 新增 composition-owned pending cancellation scanner，以及 owner/token/expiry/单调 fence/version 绑定的 claim、heartbeat、defer、expired takeover；四类变更在 `BEGIN IMMEDIATE` 后才采样时钟。Host/Internal/Scheduler Coordinator 与 worker 共用 process-local registry 作 quiescence 判断，但它不是 durable authority；worker 从 HostCommandRepository 按 exact Graph revision 重建 admission 再重入既有 Runtime，未显式获得 `local_pending` 时必须 `cancel_and_join`，本地非静默时不得 finalize。0094 只保存协调元数据，0092 intent/decision 仍是唯一业务真相；本地非静默或 operation/effect/review 未收敛时只 durable defer。0095 要求 Graph Outbox 绑定现存 revision，歧义历史回填整笔失败；投递按 type+id+revision 独立保序，multi-revision marker 扫描、Runtime Event `graph_id@revision` 与 Operator exact revision 已闭环。activation 失败反向回滚，shutdown 有界；拒绝取消则保持 Store 开放并 fail closed。生产 composition 仍未注入 cancellation authority verifier，且无 HTTP/UI 入口或 canary。历史半提交、Budget 高竞争、统一 Review 与完整业务 reconciliation 仍缺 | NE-01 B5C |
| Budget 全策略执行 | `PARTIAL` | `task_runtime/budget_runtime.py` + Host/Scheduler/Internal integrations | core ingress 已覆盖；concurrency 未计量，DEGRADE/REVIEW 分支与剩余 Domain/Operator 路径未完整 | NE-01 |
| Review 自动 continuation | `PARTIAL` | `review_runtime.py`、ReviewStore、UI | resume 只消费 token，不自动重调度原图 | NE-01 |
| 对话全副作用原子提交 | `PARTIAL` | Acceptance Effect intents | Acceptance 后 durable eventual；不是 user/assistant/delivery 单事务 | NE-01 |
| 单一任务真相 | `WIRED` | Durable Task projection + registry absence contract | 进程内 registry 与 accessor 已删除；read route 不创建任务 | NE-01 已完成切片 |
| Persona 演化单一 authority | `CENTRAL_FENCE_WIRED` | `PersonaEngine` 运行时拒绝、`T-PERSONA-FENCE-01` 全仓扫描、无副作用候选回归 | legacy writer 已归零；proposal、shadow、Review、版本激活与 rollback 替代链尚未实现 | NE-01 围栏已完成；NE-06 全量替换 |
| 通用 PlanningSearch 算法 | `WIRED` | `host_brain/planning_search/` | 内核可用，但不代表领域规划收益 | NE-02 |
| Host 候选方案生成 | `MISSING` | `plan_intent_search.py` | 只重排上游已批准步骤，不生成竞争路径 | NE-02 |
| Pentest L1/L2 | `WIRED` | `pentest/agent/agent_loop.py` | 仍需与 durable outcome/calibration 对齐 | NE-02 |
| Pentest Conditional MCTS | `SHADOW` | `planning_search_adapter.py`、virtual world | 根候选固定；不能生成 capability-unlocking 后继；校准非 durable | NE-02 |
| Research 真实收益 | `MISSING` | 仅 synthetic fixture | 未跑真实来源、模型、verifier、成本/延迟对照 | NE-02 |
| Code 真实收益 | `MISSING` | 仅 synthetic fixture | 未跑隔离 worktree、真实测试/verifier 和 baseline 对照 | NE-02 |

0094 lifecycle 已按 fail-closed 收紧：activation rollback 或 `aclose` quiescence 未完成统一返回 `TASK_RUNTIME_LIFECYCLE_RETRY_REQUIRED`，锁死 bind/activate 并只允许 `aclose` 重试。Coordinator admission close、drain、`aclose` 任一失败都不关 Store；只有 Coordinator 与 worker 均静默后才 drain Outbox/关 infrastructure。这改善了故障关闭正确性，不改变 `PARTIAL_B5C_DURABLE_CANCELLATION_SHADOW / PRODUCTION_OFF` 定级。

0095 的 CREATED 回填以 immutable HostCommand evidence 为 authority；ACTIVE 等非 attempt transition 以 deterministic legacy governed Outbox ID/key 重算为 authority，无法解析的自定义 producer 整事务阻断。升级保持原 ID/key，新增 revisioned payload；旧 payload 仅 replay 兼容。readiness/receipt gate 冻结 revision 列、payload trigger、delivery index 与 Runtime Event source unique partial index。旧 journal 不改写，一 Outbox 只允许一 Event，append-success/complete-loss legacy 重投只能 exact replay；ReviewStore/Operator exact revision。这关闭 identity/read-path 缺口，但不改变生产 cancellation、Budget、Review continuation 与 reconciliation 的剩余定级。

PlanningRecovery 只在自身调用中 opt-in Graph Outbox snapshot，并使用同一读事务内真实 terminal payload ref；普通 scheduler/Operator 无新增扫描。pre-0095 ref 原样进入 failure evidence；只有旧版或替代 Store 未提供可选扩展字段时才合成 fallback，生产 DurableTaskStore 提供 inventory 后，缺行、畸形或歧义均 fail closed。`created_ts=0` 的 timestamp 容差可用于 current revisioned 与 legacy unrevisioned 两种可重建 Event candidate，因为两条历史 append 路径都可能用时钟替代零值，但其它字段必须 exact。

## 3. Composition 与隔离

| 目标 | 当前状态 | 代码/证据 | 实际缺口 | 执行波 |
|---|---|---|---|---|
| 显式 Composition Root | `PARTIAL` | `ProcessTaskRuntimeContainer` | 任务容器良好，但整体仍有模块全局 registry | NE-01 |
| 无全局 Service Locator | `MISSING_WITH_CLEANUP_PROGRESS` | process container/services + cleanup contract | 两批已删 6 个零消费者槽位、4 个死 accessor 和第二批 7 个宽泛重导出；仍有 60 个总字段/53 个可变服务引用及数十个 accessor/依赖文件 | NE-01 |
| 执行巨石拆分 | `PARTIAL_GATE_GREEN` | [`dag_scheduling_runtime.py`](../../../host_brain/task_runtime/dag_scheduling_runtime.py)、[`dag_scheduling_records.py`](../../../host_brain/task_runtime/dag_scheduling_records.py)、[`parallel_success_runtime.py`](../../../host_brain/task_runtime/parallel_success_runtime.py)、[`node_execution_runtime.py`](../../../host_brain/task_runtime/node_execution_runtime.py)、`task_repository.py`、TaskStore focused persistence modules、`coordinator.py` 与 `core_architecture_policy.json` | 已完成十八刀。第十八刀新增 1,172 行 `DurableDagSchedulingRuntime` 和 38 行纯 Records，从冻结 HEAD 65219 迁出 execute_stream（原 988 行）、reentered、supports 与 run_claimed_node，规范化/精确 AST hash 冻结。Runtime 仅注入同 Store、共享 Outbox/Budget/Lifecycle/NodeExecution/Projection/0090/0091/Cancellation/Review、flags/reader/buffer，无 Coordinator callback、新锁或第二 persistence authority；矩阵为 Store9/Budget9/Lifecycle18/NodeProjection12/NodeExecution6/Settlement4/Success1/Review4/Cancellation1/Outbox 1 build+6 publish+0 best-effort，全局 Outbox 仍为 27/31/58/5。Coordinator 降至 257 行，仅余 composition 与公开 facade/registry track；mixed-file Gate 继续 green。0094 另增加跨进程 cancellation coordination，不改变结构刀数，也不代表 operation/effect 业务 reconciliation 完成；整体继续 PARTIAL/PRODUCTION_OFF | NE-01 Lane D |
| Host/Obs/Worker scope 隔离 | `PARTIAL` | 独立 ASGI 与部分 typed ports | lifecycle 与 provider 仍跨全局对象图共享 | NE-01 |
| Workspace ownership | `PARTIAL_D0_PATH_FENCE` | `agent_runtime/workspace_paths.py`、resolver/tool/verification tests | scoped 路径安全已统一；仍缺 per-task durable owner、`runtime_repo` 退役、隔离 worktree、OS/tenant 边界与 handle-level TOCTOU 加固 | NE-01 |
| 外部 Skill 安全接收 | `PARTIAL` | Signed Intake、Quarantine、Supply Chain | 只完成验证/隔离接收；staged activation/rollback 未闭环 | NE-05 |

## 4. 数字生命与长期演化

| 目标 | 当前状态 | 代码/证据 | 实际缺口 | 执行波 |
|---|---|---|---|---|
| World Model | `PARTIAL` | `runtime/world_model.py`、query repository | 只读事实投影，`write_api_available=false` 且 Life projection 默认未启用；不是预测/规划 world model，也未成为 canonical context resolver | NE-03 |
| Scene Engine | `PARTIAL_DEFAULT_OFF` | `persona/scene_policy.py`、Surface/Proactive 消费接线 | `HOST_LIFE_PROJECTION_ENABLED` 生产默认关闭；缺统一 scene aggregate、transition journal、TTL/re-evaluation owner | NE-03 |
| Intent Arbiter | `MISSING` | Governor + ActionArbiter 仅覆盖部分语义 | 没有统一合并 user/task/life/relationship/long-term goal 的仲裁层 | NE-03 |
| Action Preview / Shadow | `PARTIAL` | `governor/action_preview.py`、Obs route | dry-run 合同存在；生产无 trusted authority provider，端点仅 eligibility、固定不执行；没有统一 shadow runner | NE-03 |
| Runtime Guardian | `PARTIAL` | health、degradation、heartbeat、service catalog | degradation/recovery evidence 真实；Catalog startup coordinator 未进生产 composition、默认 `AUDIT`，前门与长期 SLO owner 不完整 | NE-03 |
| Evolution Lab | `PLACEHOLDER` | `evolution_lab/policy.py` | 只有分支前缀校验，无 scenario/experiment store/runner/promotion | NE-04 |
| Counterfactual | `MISSING` | Planning benchmark 不能替代 | 无替代路径 replay、decision comparator 与 outcome evidence | NE-04 |
| Capability Maturity | `PARTIAL` | scorecard/provider/Governor adapter | 只读投影；门禁默认关闭，缺 durable promotion/degradation authority | NE-05 |
| Capability Forge | `PARTIAL` | `app/capability_forge.py` | 能生成/批准 blueprint 和 canary 路由，不会生成新能力实现 | NE-05 |
| Skill Compiler | `MISSING` | 无 document/trace/script compiler | 缺 typed draft、测试、SBOM、签名、sandbox、activation 全链 | NE-05 |
| Experience Feedback | `PARTIAL_BROKEN_PATH` | response/persona/recommendation feedback | 多条局部链；Prefrontal 调用参数与 `on_interaction_cycle()` 签名不符且异常被吞；没有统一 proposal/review/rollback pipeline | NE-06 |
| Knowledge OS | `MISSING` | Memory、Retrieval、Canon 是基础资产 | 缺 provenance/version/staleness/conflict/decision lifecycle | NE-06 |
| Identity Snapshot | `PARTIAL` | `release/identity_snapshot.py` | 合同丰富，但 gate 默认关闭，未由 release 主链生成/持久化 | NE-06 |
| Identity Migration | `MISSING` | 无 migration executor | 缺计划、隔离迁移、人格回归、用户确认、激活/回滚闭环 | NE-06 |

## 5. 历史证据的正确解释

| 证据 | 可证明 | 不能证明 |
|---|---|---|
| Core Release 22/22 | 对应提交的基础设施与选定 Gate 通过 | 当前全架构、所有生产 route 或演化能力完成 |
| GR-06 live receipt | 当时 Host `chat/skill/delegate/plan` 可产生 durable lineage | Scheduler/Internal、legacy route 删除、所有副作用单事务 |
| GR-02A benchmark | 公共算法能解决冻结的 synthetic trap | Research/Code 生产 uplift、真实成本或安全切流 |
| Pentest calibration tests | 纯虚拟世界确定性与粗 reward 对账 | L3 替代路径真实收益或跨 run 稳定校准 |
| M48 Identity tests | snapshot/diff/review/rollback 合同成立 | 生产 release 已生成并执行人格迁移 |

## 6. 总体裁决

```text
Core Infrastructure                         HISTORICAL_PASS / REUSABLE
Host chat-skill-delegate-plan runtime        WIRED
Scheduler governed runtime                   WIRED
Internal governed runtime                    WIRED
Universal execution scope                    PARTIAL
Persona evolution authority                  CENTRAL_FENCE_WIRED / PROPOSAL PIPELINE MISSING
Generic planning kernel                      AVAILABLE
Generic intelligent planning                 INCOMPLETE
Cross-domain production benefit              UNPROVEN
Explicit composition                         INCOMPLETE
Long-term evolution program                  NOT IMPLEMENTED AS A WHOLE
Full Architecture                            INCOMPLETE
```

当前执行只允许从 [NEXT_GENERATION_EVOLUTION_EXECUTION.md](../../30_devspec/NEXT_GENERATION_EVOLUTION_EXECUTION.md) 的 NE-00～NE-07 推进；不得复活旧 M49～M57 或 TASK-xxxx 状态作为调度依据。
