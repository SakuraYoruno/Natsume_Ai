受众：MASTER
标题：当前需求可追溯矩阵

# 当前需求可追溯矩阵

版本：v3.6
核对日期：2026-07-16
状态来源：[current_architecture_status.json](../../configs/release/current_architecture_status.json)

本文只维护当前 FR → 工程合同 → 生产代码 → 测试/证据 → 下一执行波的关系。旧 R-01～R-192、TASK-0000～0622 和阶段状态已移出当前权威链，可通过 `docs/_history/programs/2026-07/` 与 Git `9b6db346^` 追溯。

## 1. 解释规则

1. PRD 定义 WHAT，不记录实现完成叙事。
2. DevSpec 定义稳定工程合同，不承担任务状态。
3. 架构级原子状态写入 `current_architecture_status.json`；下表的复合 FR 状态只能由这些原子状态和代码审计推导。
4. 自动化测试证明其覆盖的行为，不能单独把 `PARTIAL` 提升为 `WIRED`。
5. 历史 receipt 只对固定 commit、route 和环境成立。
6. 执行顺序只来自 [NEXT_GENERATION_EVOLUTION_EXECUTION.md](../30_devspec/NEXT_GENERATION_EVOLUTION_EXECUTION.md)。

## 2. 当前核心产品能力

| FR | 工程合同 | 生产落点 | 主要验证 | 当前状态 |
|---|---|---|---|---|
| FR-CHAT | DevSpec §2～4 | `host_brain/app/chat_routes.py`、Cortex、Surface | chat/integration、GR-06 固定 receipt | `WIRED_WITH_LIMITS` |
| FR-INGRESS | DevSpec §2、§8 | Secure Ingress、Host/Gateway/Scheduler/Internal adapters | route-matrix contracts、Scheduler/Internal production integration | `WIRED_WITH_OPERATOR_MATRIX_LIMITS` |
| FR-SURFACE | DevSpec §4、§11 | Surface/Reply authority、Life projection | surface/life contracts、writer 审计 | `PARTIAL_PERSONA_PROPOSAL_MISSING` |
| FR-MEMORY、FR-CANON | DevSpec §5 | typed repositories、Canon Apply、recall | memory/canon contracts | `WIRED_WITH_KNOWLEDGE_GAPS` |
| FR-TOOL、FR-CAPABILITY | DevSpec §7 | Capability Broker、Skill/Tool/MCP ports | capability/supply-chain contracts | `PARTIAL_LIFECYCLE` |
| FR-GATEWAY、FR-VOICE、FR-EMBODIMENT | DevSpec §8 | Gateway、Edge contracts、device receipt | Edge/Gateway contracts、环境验收 | `WIRED_DEGRADED_HARDWARE` |
| FR-OBS | DevSpec §9 | journal/outbox/projection/Obs/Operator | event/query/privacy tests | `WIRED_WITH_COMPOSITION_GAPS` |
| FR-PENTEST | DevSpec §7 | Pentest domain runtime + PlanningSearch adapter | security/pentest suites | `WIRED_L3_SHADOW_UNCALIBRATED` |
| FR-PROACTIVE | DevSpec §4、§11 | proactive intent/outbox + Persona feedback paths | proactive contracts、writer 审计 | `PARTIAL_PERSONA_FENCE_WIRED` |
| FR-AGENT-RUNTIME、FR-PLANNING | DevSpec §3、§6 | Governed Runtime、PlanningSearch | T-GR-RUNTIME-01、T-PLAN-* | `PROGRAM_INCOMPLETE` |

## 3. Governed Execution

| Trace | FR | 合同 | 生产落点 | 证据 | 当前状态 | 下一波 |
|---|---|---|---|---|---|---|
| TRACE-EXEC-01 | FR-ARCH-003 | Host 四路线必须共享 WorkOrderV3/TaskGraph | Cortex、GovernedHostRuntime | GR-05/06 receipt；T-GR-RUNTIME-01 | `WIRED` | NE-01 守卫 |
| TRACE-EXEC-02 | FR-ARCH-003 | Scheduler 必须进入同一 admission | Scheduler governed runtime | production cutover integration；child grant/receipt lineage | `WIRED` | NE-01 已完成切片 |
| TRACE-EXEC-03 | FR-ARCH-003 | Internal Agent 必须进入同一 admission | Internal task runtime | production cutover integration；principal/child grant/Review/receipt lineage | `WIRED` | NE-01 已完成切片 |
| TRACE-EXEC-04 | FR-NLE-002 | 多节点依赖、Attempt、Lease、Retry、受控 fan-out/fan-in、显式结果、整图原子成功与 verified durable cancellation | coordinator/heartbeat/task store；0090/0091/0092 journals；0093 Outbox delivery；0094 cancellation continuation；0095 revisioned Outbox/payload/Event/Review/Operator；0096 Review continuation；Secure Ingress cancellation verifier；Operator Gateway/BFF/UI；migration runner deterministic digest；worker projection/composition | 0092 提供 exact-revision two-phase cancellation，0093 收口跨进程 delivery，0094 提供 cancellation scanner/claim/takeover/continuation。0095 以 decision/intent/attempt/unique-revision、HostCommand CREATED evidence 或 deterministic legacy governed ID/key 重算精确回填；自定义歧义 producer 整事务失败。0096 将 task-bound Review 终态决定与 continuation 原子提交。Cancellation Operator authority/入口为 `WIRED_DEFAULT_OFF`：三运行时共享 verifier，Gateway/BFF/UI 精确提交 revision，异常后读取 exact durable intent 判定副作用确定性。0092 仍是 cancellation 业务 authority | `PARTIAL_B5C_DURABLE_CANCELLATION_SHADOW / PRODUCTION_OFF`。cancellation 业务 reconciliation 未解决时只 defer；历史修复、Budget 高竞争、部署凭据、真实 canary和一般 recoverable Attempt continuation 仍缺 | NE-01 B5C production wiring |
| TRACE-EXEC-04A | FR-NLE-002 | 0095 recovery evidence 不得伪造 revisioned ref 或拖慢普通调度 | `read_scheduler_snapshot(include_graph_outboxes)`；PlanningRecovery；Runtime Event replay | 默认 false；recovery 显式 true 且同事务 exact terminal Outbox；legacy payload 原样使用；仅旧版或替代 Store 未提供可选扩展时 fallback；created_ts=0 对 current/legacy 可重建 candidate 的受限容差，除 timestamp 外全字段 exact；GR-04/Runtime Event tests | `WIRED_0095_COMPAT / PROGRAM_STILL_OFF` | 持续故障矩阵 |
| TRACE-EXEC-04B | FR-ARCH-001、FR-NLE-002 | Task mutation 的中央 Outbox insert/replay 实现必须唯一，结构拆分不得产生第二事务 authority | `TaskOutboxDeliveryStoreMixin`、`TaskOperationReconciliationStoreMixin`；由 `DurableTaskStore` 组合 | helper/method identity 归属 focused mixin、调用方事务复用、既有 MRO/行为测试、`task_repository.py` 4,470 ≤ 4,600 policy | `WIRED_STRUCTURE_ONLY / SEMANTICS_UNCHANGED` | 持续结构门禁 |
| TRACE-EXEC-04C | FR-ARCH-003、FR-NLE-002 | Operator TaskGraph 取消必须使用 server-owned Secure Ingress authority，精确绑定 revision，并在失败时给出可证明的副作用确定性 | `graph_cancellation_authority.py`、`runtime_group.py`、Host/Internal/Scheduler runtime、`task_graph.cancel` manifest/Gateway/BFF/UI、ASGI feature gate | authority 对 committed audit/claim、request/principal/tenant、body digest、action/scopes 的篡改拒绝；settled claim bearer 重用拒绝；三个 surface exact 路由；无 intent 明确未执行、匹配 intent 可能已执行、不可证明保守 uncertain；任意依赖异常不得伪造 OBS 封闭确定性；默认 OFF；非 ENFORCE/Gateway 不可用时启动 fail closed；见 `test_ne01_graph_cancellation_secure_operator_path.py`、`test_ne01_graph_cancellation_operator_authority.py`、`test_ne01_graph_cancellation_operator_routing.py`、`test_obs_domain_command_ports.py`、AW-07 Gateway/ASGI composition 与 UI gateway tests | `WIRED_DEFAULT_OFF / PRODUCTION_OFF` | 补齐业务对账、历史修复、Budget 高竞争、部署凭据与真实 canary |
| TRACE-EXEC-04D | FR-NLE-002 | Cancellation Effect 只能凭可证明证据解除 fence；普通失败不得冒充 `NO_SIDE_EFFECT` | `task_acceptance_effect_delivery.py`、`task_graph_cancellation_request.py`、`task_graph_cancellation_finalization.py`、Effect codec | 取消前 `FAILED`、取消后 in-flight `FAILED` 均保持 `RECONCILIATION_REQUIRED`；仅 `COMMITTED + durable receipt` 自动成为 `COMMITTED_EVIDENCE`；旧 `FAILED_EVIDENCE` 兼容可读；Operation 签名对账 service/composition/OBS/public-key injection 为 `WIRED_DEFAULT_OFF`。PENDING 仅 exact virgin tuple可 `CANCELLED`；canonical API不可生成 fenceable retry tuple，direct-SQL legacy/corruption fixture证明它 fail closed、request response-loss exact replay、二次 advance仍 pending、effect digest稳定且 direct finalize被拒。正常 public API另证明未 ACCEPTED effect不可 claim、accepted NODE retry effect不受 cancellation fence | `PARTIAL_FAIL_CLOSED / PRODUCTION_OFF` | 完成签名 Effect 对账与旧 `FAILED_EVIDENCE`、历史 retry-PENDING + CANCELLED fence的审计/修复/隔离；FINALIZED replay不得自动重写 immutable evidence；部署 Operation 凭据并跑 canary |
| TRACE-EXEC-05 | FR-NLE-002 | Budget 声明必须被 runtime 消费 | budget runtime/usage ledger | Host/Scheduler/Internal integrations | `PARTIAL_CORE_INGRESS` | NE-01 |
| TRACE-EXEC-06 | FR-NLE-003 | Review token 后自动安全 continuation | ReviewStore、0096 coordination、Review worker、surface runtime、`review:decide` ingress | 原子 mutation/enqueue、claim takeover、PRE/POST、poison isolation、Attempt/Lease hand-off、0097 grant uniqueness、denial/legacy replay 与 shutdown tests | `WIRED_0096_TASK_BOUND / CANARY_NOT_RUN` | 部署 Operator credential并跑真实 canary；一般 recoverable Attempt 另行收口 |
| TRACE-EXEC-07 | FR-ARCH-003 | 不得有第二任务真相 | Durable Task/Conversation projections | registry absence contract + code audit | `WIRED` | NE-01 已完成切片 |
| TRACE-EXEC-08 | FR-ARCH-001 | 显式 composition、无 locator | Task container + process service locator | dead-slot/re-export cleanup contract（53 = 60 fields - 6 locks - application_ports）；static scan/code audit | `INCOMPLETE_53_SERVICE_REFS` | NE-01 |
| TRACE-EXEC-09 | FR-ARCH-001、FR-ARCH-010 | Agent/Verification 路径不得逃逸 workspace | canonical resolver + scoped tool/verification wiring | resolver/tool/verification unit + contract | `D0_WIRED / OWNERSHIP_INCOMPLETE` | NE-01 |

TRACE-EXEC-04 的 lifecycle 验收还要求：activation rollback 或 `aclose` quiescence 未完成返回 `TASK_RUNTIME_LIFECYCLE_RETRY_REQUIRED`，bind/activate 必须锁死且只能 `aclose` 重试；Coordinator admission close、drain、`aclose` 任一失败均保持 Store 开放，Coordinator 与 worker 全静默后才 drain Outbox/关 infrastructure。

## 4. Planning Search

| Trace | FR | 合同 | 生产落点 | 证据 | 当前状态 | 下一波 |
|---|---|---|---|---|---|---|
| TRACE-PLAN-01 | FR-PLANNING、FR-ARCH-003、FR-ARCH-010 | 公共无副作用搜索内核 | `planning_search/` | GR-01 unit | `WIRED` | NE-02 |
| TRACE-PLAN-02 | FR-PLANNING、FR-ARCH-003 | Host 从目标生成竞争计划 | `plan_intent_search.py` | 代码审计；T-PLAN-PROD-01 | `MISSING` | NE-02 |
| TRACE-PLAN-03 | FR-PENTEST | Pentest L1/L2 使用公共 service | Pentest adapter/AgentLoop | GR-01 pentest tests | `WIRED` | NE-02 |
| TRACE-PLAN-04 | FR-PENTEST、FR-PLANNING | L3 能按虚拟状态生成后继并 durable 校准 | Pentest virtual world | tests 仅 root candidate/shadow；T-PLAN-CAL-01 | `SHADOW_UNCALIBRATED` | NE-02 |
| TRACE-PLAN-05 | FR-PLANNING、FR-ARCH-010 | Research 真实收益 | synthetic fixture only | GR-02A report；T-PLAN-PROD-01 | `UNPROVEN` | NE-02 |
| TRACE-PLAN-06 | FR-PLANNING、FR-ARCH-010 | Code 真实收益 | synthetic fixture only | GR-02A report；T-PLAN-PROD-01 | `UNPROVEN` | NE-02 |

## 5. Digital Life 与长期演化

| Trace | FR | 目标合同 | 当前落点 | 当前状态 | 下一波 |
|---|---|---|---|---|---|
| TRACE-EVO-01 | FR-NLE-004 | World facts/provenance/TTL/conflict 进入 Context | `runtime/world_model.py` | `PARTIAL_READ_PROJECTION` | NE-03 |
| TRACE-EVO-02 | FR-NLE-004 | Scene aggregate/transition/策略 | `persona/scene_policy.py` | `PARTIAL_DEFAULT_OFF` | NE-03 |
| TRACE-EVO-03 | FR-NLE-004 | user/task/life/relationship/authority 仲裁 | Governor + ActionArbiter | `MISSING_UNIFIED_ARBITER` | NE-03 |
| TRACE-EVO-04 | FR-NLE-004 | 高风险 action 统一 preview/shadow | action preview + Obs | `PARTIAL_ELIGIBILITY_ONLY` | NE-03 |
| TRACE-EVO-05 | FR-NLE-005 | 统一 health/degradation/recovery/SLO | degradation/heartbeat/catalog | `PARTIAL` | NE-03 |
| TRACE-EVO-06 | FR-ARCH-010 | Scenario/Experiment Lab | branch policy only | `PLACEHOLDER` | NE-04 |
| TRACE-EVO-07 | FR-ARCH-010 | Counterfactual replay | 无 | `MISSING` | NE-04 |
| TRACE-EVO-08 | FR-NLE-005、FR-ARCH-007 | Maturity 成为默认 restrict-only 门禁 | scorecard + disabled adapter | `PARTIAL` | NE-05 |
| TRACE-EVO-09 | FR-ARCH-007、FR-ARCH-010 | Skill Compiler 与 signed lifecycle | forge/intake/quarantine | `MISSING_COMPILER` | NE-05 |
| TRACE-EVO-10 | FR-ARCH-010 | 经验只以 proposal 回流 | 多条局部 feedback；T-PERSONA-PROPOSAL-01 待实现 | `PARTIAL_BROKEN_PATH` | NE-06 |
| TRACE-EVO-11 | FR-ARCH-005、FR-ARCH-010 | Knowledge provenance/version/conflict | Memory/Retrieval/Canon 基础 | `MISSING_KNOWLEDGE_OS` | NE-06 |
| TRACE-EVO-12 | FR-NLE-005 | Identity snapshot 接 release | pure contract | `PARTIAL_DEFAULT_OFF` | NE-06 |
| TRACE-EVO-13 | FR-ARCH-004、FR-ARCH-010 | Identity migration/regression/rollback | 无 executor | `MISSING` | NE-06 |
| TRACE-EVO-14 | FR-NLE-005、FR-ARCH-010 | Persona trait 只能由 proposal→Lab→Review→版本激活写入 | legacy writer 已中央拒绝，`T-PERSONA-FENCE-01` 已接入；当前入口只形成无副作用候选 | `FENCE_COMPLETE / PROPOSAL_MISSING` | NE-01 围栏完成；NE-06 由 TRACE-EVO-10 全量替换 |

## 6. 下一代执行波映射

| 执行波 | 主要 FR | 必须关闭的 Trace |
|---|---|---|
| NE-00 | 文档治理 | 当前权威链和机器状态 |
| NE-01 | FR-NLE-002～003、FR-NLE-005、FR-ARCH-001～003、FR-ARCH-010 | TRACE-EXEC-01～08；TRACE-EVO-14 safety fence |
| NE-02 | FR-ARCH-003、FR-ARCH-010、FR-PENTEST | TRACE-PLAN-01～06 |
| NE-03 | FR-NLE-004～005、FR-ARCH-004 | TRACE-EVO-01～05 |
| NE-04 | FR-ARCH-010 | TRACE-EVO-06～07 |
| NE-05 | FR-NLE-005、FR-ARCH-007、FR-ARCH-010 | TRACE-EVO-08～09 |
| NE-06 | FR-ARCH-004、FR-ARCH-005、FR-ARCH-010、FR-NLE-005 | TRACE-EVO-10～14（含 direct writer replacement） |
| NE-07 | 全部 | T-NE-FINAL-01；所有 `BROKEN/MISSING/UNPROVEN/UNSAFE` 清零并重新验收 |

## 7. 证据边界

- `docs/45_testing/test_reports/gr06_universal_governed_runtime_live_acceptance_20260715.json`：Host 四路线固定验收。
- `docs/45_testing/test_reports/gr02a_planning_search_shadow_benchmark_v1.json`：synthetic simulation，不是生产收益。
- `docs/50_release/core/**`：Core Infrastructure 固定提交发布证据。
- `docs/50_release/m48/**`：M48 历史快照，不能推导当前状态。
- `docs/30_devspec/evidence/m48b/**`：M48-B 固定范围证据。

新增证据必须绑定 source commit、环境、route、dataset、预算、失败尝试和原始 receipt；禁止只写总结性 PASS。
