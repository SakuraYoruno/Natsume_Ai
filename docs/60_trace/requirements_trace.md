受众：MASTER
标题：当前需求可追溯矩阵

# 当前需求可追溯矩阵

版本：v3.9
核对日期：2026-07-18
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
| TRACE-EXEC-04 | FR-NLE-002 | 多节点依赖、Attempt、Lease、Retry、受控 fan-out/fan-in、显式结果、整图原子成功与 verified durable cancellation | coordinator/heartbeat/task store；0090～0098；Budget/Operation focused Store；shared finalizer | Budget unique winner及 0090 single-snapshot terminal-authority 重验、single-node post-budget takeover 与 ACCEPTED→Effect restart已有证据；Cancellation/reconciliation仍默认 OFF | `PARTIAL_B5C_DURABLE_CANCELLATION_SHADOW / PRODUCTION_OFF`。general/batch/pre-budget recovery、历史半状态、现实 resolver、部署 key/credential、inventory remediation与 canary仍缺 | NE-01 B5C deployment/canary |
| TRACE-EXEC-04A | FR-NLE-002 | 0095 recovery evidence 不得伪造 revisioned ref 或拖慢普通调度 | `read_scheduler_snapshot(include_graph_outboxes)`；PlanningRecovery；Runtime Event replay | 默认 false；recovery 显式 true 且同事务 exact terminal Outbox；legacy payload 原样使用；仅旧版或替代 Store 未提供可选扩展时 fallback；created_ts=0 对 current/legacy 可重建 candidate 的受限容差，除 timestamp 外全字段 exact；GR-04/Runtime Event tests | `WIRED_0095_COMPAT / PROGRAM_STILL_OFF` | 持续故障矩阵 |
| TRACE-EXEC-04B | FR-ARCH-001、FR-NLE-002 | Task mutation 的中央 Outbox insert/replay 实现必须唯一，结构拆分不得产生第二事务 authority | Outbox/BudgetUsage/OperationCheckpoint/Effect focused mixins；由 `DurableTaskStore` 组合 | 调用方事务复用、MRO/行为测试、`task_repository.py` 4,145 ≤ 4,600 policy | `WIRED_FOCUSED_STORE / ONE_UOW` | 持续结构门禁 |
| TRACE-EXEC-04C | FR-ARCH-003、FR-NLE-002 | Operator TaskGraph 取消必须使用 server-owned Secure Ingress authority，精确绑定 revision，并在失败时给出可证明的副作用确定性 | cancellation authority/runtime/Gateway/BFF/UI | server-owned authority、exact revision、durable intent certainty、default OFF 均有测试 | `WIRED_DEFAULT_OFF / PRODUCTION_OFF` | 部署两类 reconciliation、密钥/凭据与真实 canary；历史半状态/general recovery |
| TRACE-EXEC-04D | FR-NLE-002 | Cancellation Effect 只能凭可证明证据解除 fence；历史修复不得改写旧 authority；独立 Obs 只能只读 canonical DB | Effect reconciliation contract/service/focused Store + 0098；`effect_reconciliation_query_repository.py`；Effect delivery/cancellation；Operator command/read routes | `test_ne01_graph_cancellation_effect_reconciliation.py` 覆盖 PENDING/FINALIZED proof；`tests/integration/test_ne01_obs_effect_reconciliation_read.py` 覆盖 ledger canonical path、`mode=ro + query_only`、flag-off 不触 0098、`obs:task-runtime:read` 窄 scope、generic scope 403、最小 cases/proof 与可签名 challenge。repository 只继承 read/validation mixin，不构造 DurableTaskStore/UoW；旧 payload/hash/fence/evidence不变 | `WIRED_DEFAULT_OFF / CANARY_NOT_RUN / PRODUCTION_OFF` | 在每个目标部署运行 legacy inventory gate/remediation；部署 verification keys、Operator credentials与完整 key lifecycle；运行真实 canary |
| TRACE-EXEC-04E | FR-NLE-002 | 自动接管不得重跑 worker或补造缺失预算事实 | `TaskOperationCheckpointStoreMixin`、`DurableOperationRecoveryRuntime`、`CommittedCheckpointContinuation` | exact COMMITTED Outbox/Attempt/历史 Lease/canonical projection/accepted worker usage；旧 Lease expiry/fence；online takeover integration | `SINGLE_NODE_POST_BUDGET_CHECKPOINT_PROVEN / GENERAL_RECOVERY_INCOMPLETE` | general/batch/pre-budget recovery、历史半状态 |
| TRACE-EXEC-04F | FR-NLE-002 | ACCEPTED 已落盘而 Effect 未落盘时不得无 Effect 完成 Graph | `AcceptedGraphFinalizationRuntime`、Graph+Effect Store UoW | process-loss restart、worker no-rerun、effect id/digest/idempotency replay、tampered projection fail-closed | `PROVEN_NARROW_RESTART_FINALIZATION` | production canary；跨系统 eventual dispatch 仍非单事务 |
| TRACE-EXEC-05 | FR-NLE-002 | Budget 声明必须被 runtime 消费且高竞争只产生一个超限 authority | budget runtime、`TaskBudgetUsageStoreMixin` | 8/16/32 线程、跨进程、restart/interleave/drift；concurrency=8 时 8 次 CAS conflict 后第 9 次成功 | `CONTENTION_PROVEN / PROGRAM_PARTIAL` | concurrency charge、DEGRADE/REVIEW |
| TRACE-EXEC-06 | FR-NLE-003 | Review token 后自动安全 continuation | ReviewStore、0096 coordination、Review worker、surface runtime、`review:decide` ingress | task-bound PRE/POST、claim takeover、POST no-rerun | `WIRED_0096_TASK_BOUND / CANARY_NOT_RUN` | 部署 credential/canary；不要与 single-node Operation takeover或 general recovery混写 |
| TRACE-EXEC-07 | FR-ARCH-003 | 不得有第二任务真相 | Durable Task/Conversation projections | registry absence contract + code audit | `WIRED` | NE-01 已完成切片 |
| TRACE-EXEC-08 | FR-ARCH-001 | 显式 composition、无 locator | Task container + process service locator | dead-slot/re-export cleanup contract（53 = 60 fields - 6 locks - application_ports）；static scan/code audit | `INCOMPLETE_53_SERVICE_REFS` | NE-01 |
| TRACE-EXEC-09 | FR-ARCH-001、FR-ARCH-010 | Agent/Verification 路径不得逃逸 workspace | canonical resolver + scoped tool/verification wiring | resolver/tool/verification unit + contract | `D0_WIRED / OWNERSHIP_INCOMPLETE` | NE-01 |

TRACE-EXEC-04 的 lifecycle 验收还要求：activation rollback 或 `aclose` quiescence 未完成返回 `TASK_RUNTIME_LIFECYCLE_RETRY_REQUIRED`，bind/activate 必须锁死且只能 `aclose` 重试；Coordinator admission close、drain、`aclose` 任一失败均保持 Store 开放，Coordinator 与 worker 全静默后才 drain Outbox/关 infrastructure。

## 4. Planning Search

| Trace | FR | 合同 | 生产落点 | 证据 | 当前状态 | 下一波 |
|---|---|---|---|---|---|---|
| TRACE-PLAN-01 | FR-PLANNING、FR-ARCH-003、FR-ARCH-010 | 公共无副作用搜索内核与 immutable compile boundary | `planning_search/`、`goal_planning.py`、`planning_graph_compiler.py`、`planning_execution.py` | GR-01、D14、D16 unit；state/context/grant lineage | `WIRED_FOUNDATION / DOMAIN_CUTOVER_INCOMPLETE` | NE-02 |
| TRACE-PLAN-02 | FR-PLANNING、FR-ARCH-003 | Host 从目标生成竞争计划 | `plan_intent_search.py` | 代码审计；T-PLAN-PROD-01 | `MISSING` | NE-02 |
| TRACE-PLAN-03 | FR-PENTEST | Pentest L1/L2 使用公共 service | Pentest adapter/AgentLoop | GR-01 pentest tests | `WIRED` | NE-02 |
| TRACE-PLAN-04 | FR-PENTEST、FR-PLANNING | L3 能按虚拟状态生成后继并 durable 校准 | Pentest virtual world | tests 仅 root candidate/shadow；T-PLAN-CAL-01 | `SHADOW_UNCALIBRATED` | NE-02 |
| TRACE-PLAN-05 | FR-PLANNING、FR-ARCH-010 | Research 真实收益 | synthetic fixture only | GR-02A report；T-PLAN-PROD-01 | `UNPROVEN` | NE-02 |
| TRACE-PLAN-06 | FR-PLANNING、FR-ARCH-010 | Code 动态 pure-shadow planning 与真实收益 | `planning_search/code_domain_models.py`、`planning_search/code_domain.py`、`planning_search/code_benchmark_models.py`、`planning_search/code_benchmark_sandbox.py`、`code_benchmark_ledger.py`、0106 ledger | D16 dynamic catalog、forged-input/root-policy rejection、MCTS readiness gate、context/replan lineage；D17 trusted-process private-bare dual-worktree/portable ACL/source-fingerprint drift detection；D18 preregistration、two-arm evidence、`VERIFIED_CLEAN`、append-only/restart-replay/schema-drift SQLite integration；GR-02A 仍仅 synthetic | `UNPROVEN`：已证明 catalog/compile 纯度及受信 adapter 的 cleanup-gated durable experiment evidence；不能约束恶意同进程 executor/verifier，declared budget 也不是 hard gate，仍未证明真实 historical cohort/statistical uplift、OS/container/network sandbox、model/tool/test command、hidden verifier 或收益 | NE-02 |

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
