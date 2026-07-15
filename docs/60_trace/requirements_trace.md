受众：MASTER
标题：当前需求可追溯矩阵

# 当前需求可追溯矩阵

版本：v3.4
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
| TRACE-EXEC-04 | FR-NLE-002 | 多节点依赖、Attempt、Lease、Retry、受控 fan-out/fan-in、显式结果、整图原子成功与 verified durable cancellation | coordinator/heartbeat/task store；0090/0091/0092 journals；graph cancellation contract；worker projection/task composition | B4/0090 与 B5/0091 保留 whole-Graph settlement、canonical projection 和 exact replay。B5C 以 0092 增加 exact-revision verified internal entry、phase-1 intent/fence、reconciliation gate 与 phase-2 Graph/Budget/Outbox/decision；accepted NODE effect 继续 delivery，其他未授权 effect 被 fence，0091 success 与 cancel first-writer-wins。request/finalize response-loss exact replay；execute re-entry 可继续既有 intent | `PARTIAL_B5C_DURABLE_CANCELLATION_SHADOW / PRODUCTION_OFF`。生产 composition 未注入 verifier，无 HTTP/UI 或跨进程自动 reconciliation/takeover/continuation；atomic/bounded/cancellation 与 canary 均 OFF。revisioned 0090、历史半提交、跨进程 Outbox、Budget 高竞争和统一 Review 仍缺 | NE-01 B5C production wiring |
| TRACE-EXEC-05 | FR-NLE-002 | Budget 声明必须被 runtime 消费 | budget runtime/usage ledger | Host/Scheduler/Internal integrations | `PARTIAL_CORE_INGRESS` | NE-01 |
| TRACE-EXEC-06 | FR-NLE-003 | Review token 后自动安全 continuation | ReviewStore/review runtime | Review tests/UI | `PARTIAL_MANUAL` | NE-01 |
| TRACE-EXEC-07 | FR-ARCH-003 | 不得有第二任务真相 | Durable Task/Conversation projections | registry absence contract + code audit | `WIRED` | NE-01 已完成切片 |
| TRACE-EXEC-08 | FR-ARCH-001 | 显式 composition、无 locator | Task container + process service locator | dead-slot/re-export cleanup contract（53 = 60 fields - 6 locks - application_ports）；static scan/code audit | `INCOMPLETE_53_SERVICE_REFS` | NE-01 |
| TRACE-EXEC-09 | FR-ARCH-001、FR-ARCH-010 | Agent/Verification 路径不得逃逸 workspace | canonical resolver + scoped tool/verification wiring | resolver/tool/verification unit + contract | `D0_WIRED / OWNERSHIP_INCOMPLETE` | NE-01 |

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
