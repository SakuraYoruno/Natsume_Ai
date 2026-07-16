受众：MASTER
标题：Natsume AI 当前架构差距矩阵

# Natsume AI 当前架构差距矩阵

版本：v1.9
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
| 多节点 Durable DAG | `PARTIAL_B5C_DURABLE_CANCELLATION_SHADOW / PRODUCTION_OFF` | Coordinator、TaskStore、0090-0099、Budget contention、Operation checkpoint/finalizer tests | Budget unique winner、single-node post-budget takeover、ACCEPTED→Effect restart 与 0099 窄 terminal reconciliation已证明；general/multi-node/pre-budget recovery、现实 resolver、部署 key/credential、目标部署 inventory/remediation 与 canary仍缺 | NE-01 B5C |
| Cancellation Effect 对账 | `WIRED_DEFAULT_OFF / CANARY_NOT_RUN` | 0092 effect fence；0098 case/proof inventory；Effect reconciliation contract/service/store；Operator command + candidates/cases/challenge；integration/unit/contract tests | `task_effect.reconcile` 的 Ed25519 proof exact 绑定 case/intent/effect/fence/Attempt/resolution/actor/Outbox/revision；`NO_SIDE_EFFECT → CANCELLED`，`SIDE_EFFECT_CONFIRMED + receipt → COMMITTED_EVIDENCE`。PENDING late writer 被同事务 fence；FINALIZED correction 只恢复 trusted read。生产 gate 只阻断实际持久化 legacy inventory；仓库无 SQLite 资产，不能证明外部部署已清理，且部署凭据/key lifecycle/canary 未完成 | NE-01 B5C deployment |
| Historical terminal reconciliation | `WIRED_DEFAULT_OFF / CANARY_NOT_RUN / DEPLOYMENT_UNVERIFIED` | 0099 canonical 3 table/4 index/10 trigger readiness、Python case/subject/proof codec、classification、startup inventory、service、Secure Ingress command、主 Host/独立 Obs query-only routes、HTTP→Ed25519→Store | migration schema-only；case/proof/supersession append-only。单次启动最多盘点 10,000 个 terminal Graph；仅唯一 graph-scope exact FAILED bundle 可 system auto。Classifier 要求唯一 0090、完整 0091/0092 validator 与 exact failure Outbox payload refs；结构漂移均 quarantine。未决 case graph-wide 围栏普通 Graph/cancellation request 与 Budget transition/consume/usage/retry，仅 0099 UoW bypass；每条历史替代链最终必须有可信 proof。System proof 以 immutable case 时间跨进程收敛；healthy closure 仅 signed `NONE` 且零业务写。两类查询入口物理 query-only；外部 inventory/key lifecycle/canary未知 | NE-01 B5C deployment |
| Budget 全策略执行 | `CONTENTION_PROVEN / PROGRAM_PARTIAL` | `budget_runtime.py`、focused Store、8/16/32-thread、cross-process、single-snapshot decision drift、concurrency=8 retry test | unique winner/`ALREADY_EXCEEDED` 及 0090 terminal-authority 重验已收口；CAS attempts 至少为 `concurrency + 1`（8 conflicts→9th success）。concurrency 未计量，DEGRADE/REVIEW 与剩余路径未完整 | NE-01 |
| Operation 在线 continuation | `SINGLE_NODE_POST_BUDGET_CHECKPOINT_PROVEN / GENERAL_RECOVERY_INCOMPLETE` | `task_operation_checkpoint_store.py`、`operation_recovery_runtime.py`、`operation_checkpoint_continuation.py`、online takeover tests | exact COMMITTED Outbox/Attempt/历史 Lease/canonical WorkerProjection/accepted BudgetUsage 后可接管且不重跑 worker；COMMIT-before-usage、pre-budget、batch/multi-node、未知副作用与一般 Attempt仍不自动恢复 | NE-01 |
| ACCEPTED→Effect restart | `PROVEN_NARROW_FINALIZATION` | `accepted_graph_finalization.py`、`test_ne01_accepted_effect_finalization_recovery.py` | exact checkpoint下原子补写且幂等；不代表跨系统 Effect dispatch 已全局事务化 | NE-01 |
| Review 自动 continuation | `WIRED_TASK_BOUND / CANARY_NOT_RUN` | `review_runtime.py`、ReviewStore、0096、surface/ingress/takeover tests | task-bound PRE/POST 已接；single-node post-budget Operation takeover是独立窄链。真实 canary、Operator credential/UI 与 general/batch/pre-budget recovery仍缺 | NE-01 已完成切片 |
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

Cancellation 生产权限接线不会改变该定级。Gateway 认证后的 structured authority 必须由共享 verifier 重新读取 committed Secure Ingress audit/claim，并精确绑定 request/principal/tenant、body digest、action/scopes 与 graph revision；Application 不接受调用方自报 request identity/source。Operator Runtime 按 immutable HostCommand surface 路由到 Host/Internal/Scheduler，异常后用 durable intent 区分明确未执行和可能已执行。该链路有实现与定向测试，但开关仍默认 OFF，且尚无部署凭据与真实 canary。

0098 不把历史数据“就地修好”：migration 只追加 immutable case inventory，proof 也只能追加。repairable REQUESTED case 以 PENDING proof 参与之后新建的 cancellation decision；FINALIZED quarantine 的 correction 必须继续绑定旧 decision ref/payload digest，并且只在 trusted getter/replay 中生效。`graph_revision` 同时进入签名 binding、Outbox 物理列和 case trigger。production readiness 仅审计真实数据库里的 legacy rows；本仓库无 SQLite 资产，所以当前没有任何外部生产库存已清理的证据。

0099 不在 migration 中修 Graph/Budget；它只追加 immutable case/proof/supersession schema，readiness 严格比较 3 table/4 index/10 trigger canonical SQL 并做 Python codec 复核。`recover_startup` 后单次最多 inventory 10,000 个 terminal Graph。System auto 独立默认 OFF，仅消费带唯一 graph-scope Budget 的 exact FAILED bundle，以 immutable case 时间生成跨进程稳定 proof；结构漂移永久 quarantine。Classifier 要求唯一 0090、完整 0091/0092 validator 与 exact failure Outbox refs。已 inventoried 未决 case graph-wide 围栏普通 Graph/cancellation request 与 Budget transition/consume/usage/retry，只有 0099 proof UoW bypass；旧 case 只能被新 current case 替代并最终落到可信 proof。Healthy closure 仅在历史未决 case 且 current classifier healthy 时生成 signed `NONE` 并零写 Graph/Budget/Outbox。`task_terminal.reconcile` 默认 OFF；主 Host/独立 Obs 均物理 query-only。这不是 general/multi-node/pre-budget recovery 或 production canary。

0095 的 CREATED 回填以 immutable HostCommand evidence 为 authority；ACTIVE 等非 attempt transition 以 deterministic legacy governed Outbox ID/key 重算为 authority，无法解析的自定义 producer 整事务阻断。升级保持原 ID/key，新增 revisioned payload；旧 payload 仅 replay 兼容。readiness/receipt gate 冻结 revision 列、payload trigger、delivery index 与 Runtime Event source unique partial index。旧 journal 不改写，一 Outbox 只允许一 Event，append-success/complete-loss legacy 重投只能 exact replay；ReviewStore/Operator exact revision。这关闭 identity/read-path 缺口，但不改变生产 cancellation、Budget 与 reconciliation 的剩余定级；Review continuation 由后续 0096 切片单独关闭。

0096 task-bound Review continuation 与 single-node canonical COMMITTED Operation continuation 是两条不同的窄链。后者对既有 exact accepted usage 只读核对；若 stable usage 与所有 worker-kind row 完全缺失，则在更高 fence takeover 后使用 Attempt start→Reservation commit 的正持久时间上界幂等结算。冲突/拒绝/漂移 usage、非法时间、multi-node 与 pre-operation 仍 fail closed；worker 不重跑。两者均不改变 cancellation production OFF，也不能把 general/batch/pre-operation recovery写成完成；完整架构仍为 `INCOMPLETE`。

PlanningRecovery 只在自身调用中 opt-in Graph Outbox snapshot，并使用同一读事务内真实 terminal payload ref；普通 scheduler/Operator 无新增扫描。pre-0095 ref 原样进入 failure evidence；只有旧版或替代 Store 未提供可选扩展字段时才合成 fallback，生产 DurableTaskStore 提供 inventory 后，缺行、畸形或歧义均 fail closed。0100 的 append-only supersession、`admit_replan()` 单事务 predecessor 重验+revision+1 admission、response-loss replay/rollback，以及旧 revision 的共享 TaskStore/Review mutation fence 已接通。当前 safe subset 仍只接受完全静止 `FAILED`，并保守拒绝所有历史 Review continuation、所有 Acceptance Effect 和 `BLOCKED`；自动 replan、扩大 quiescence classifier 与生产并发 canary仍缺，故 `revision_plus_one_replan=PARTIAL_DURABLE_SUPERSESSION_SAFE_SUBSET`。`created_ts=0` 的 timestamp 容差可用于 current revisioned 与 legacy unrevisioned 两种可重建 Event candidate，因为两条历史 append 路径都可能用时钟替代零值，但其它字段必须 exact。

## 3. Composition 与隔离

| 目标 | 当前状态 | 代码/证据 | 实际缺口 | 执行波 |
|---|---|---|---|---|
| 显式 Composition Root | `PARTIAL` | `ProcessTaskRuntimeContainer` | 任务容器良好，但整体仍有模块全局 registry | NE-01 |
| 无全局 Service Locator | `MISSING_WITH_CLEANUP_PROGRESS` | process container/services + cleanup contract | 两批已删 6 个零消费者槽位、4 个死 accessor 和第二批 7 个宽泛重导出；仍有 60 个总字段/53 个可变服务引用及数十个 accessor/依赖文件 | NE-01 |
| 执行巨石拆分 | `PARTIAL_GATE_RED` | `dag_scheduling_runtime.py`、focused Budget/Operation/Effect Store modules、`coordinator.py` | `task_repository.py` 当前 4,145 行；新 Budget/Operation/finalizer modules 均 <1,500且无第二 Store authority。全仓仍有 3 个既存未分类 >1,500 行违规，因此不能写 green | NE-01 Lane D 未决 |
| Host/Obs/Worker scope 隔离 | `PARTIAL` | 独立 ASGI、部分 typed ports、Effect/terminal query-only repositories | Obs terminal三 GET 已 query-only + scoped，exact-binding round-trip已接；lifecycle/provider 仍跨全局对象图共享 | NE-01 |
| Workspace ownership | `PARTIAL_W0_FAIL_CLOSED` | `workspace_paths.py`、`workspace_policy.py`、`agent_tools.py`、W0/resolver/verification tests | 写能力 WorkOrder 在 durable grant 前只允许 runtime-owned temp root；`runtime_repo`/裸 path/git URL/自定义 root 拒绝。工具 authority 取 run-mode 与 allow/forbid 交集；默认 subprocess Python 和未实现 shell 不再暴露为隔离执行器。仍缺 per-task durable owner/lease、唯一 worktree、cleanup continuation/receipt、OS/tenant 边界与 handle-level TOCTOU 加固 | NE-01 |
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
Budget contention authority                  UNIQUE_WINNER_PROVEN / POLICY PARTIAL
Operation recovery                           SINGLE_NODE_POST_BUDGET_PROVEN / GENERAL INCOMPLETE
Accepted Effect restart finalization         PROVEN_NARROW
Historical terminal reconciliation           WIRED_DEFAULT_OFF / CANARY_NOT_RUN / DEPLOYMENT_UNVERIFIED
Persona evolution authority                  CENTRAL_FENCE_WIRED / PROPOSAL PIPELINE MISSING
Generic planning kernel                      AVAILABLE
Generic intelligent planning                 INCOMPLETE
Cross-domain production benefit              UNPROVEN
Explicit composition                         INCOMPLETE
Long-term evolution program                  NOT IMPLEMENTED AS A WHOLE
Full Architecture                            INCOMPLETE
```

当前执行只允许从 [NEXT_GENERATION_EVOLUTION_EXECUTION.md](../../30_devspec/NEXT_GENERATION_EVOLUTION_EXECUTION.md) 的 NE-00～NE-07 推进；不得复活旧 M49～M57 或 TASK-xxxx 状态作为调度依据。
