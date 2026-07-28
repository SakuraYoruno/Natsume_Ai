受众：MASTER
标题：当前需求可追溯矩阵

# 当前需求可追溯矩阵

> 当前库存纠偏：历史 trace 中的 `14/37、23 blocked` 只绑定当时提交。现行 Operator manifest 为 `16 active / 23 retired / 39 known / blocked=0`；退役 ID 不得计作产品能力。生产 locator consumer/import/module/field/lock/ref 全为 0。Scheduler create/update/delete/enable/disable 管理 HTTP 已作为独立 `SCHEDULER` surface 进入 governed TaskGraph，双前缀路由的直接 writer 为 0。Governed Internal write 已能密封 frozen-base delta、生成 deterministic accepted HEAD 并经 0116 晋升，源分支保持不变；Domain Pack 与其它 Skill write 仍未接通。Chat 本地 projection barrier 与七路线 commit-bound 收据已闭合；真实网络/设备、部署验签 key、完整 Host 进程重启、外部 namespace 和完整 Worker/Obs composition 仍待 NE-07 验收。

版本：v5.17
核对日期：2026-07-28
状态来源：[current_architecture_status.json](../../configs/release/current_architecture_status.json)

本文只维护当前 FR → 工程合同 → 生产代码 → 测试/证据 → 下一执行波的关系。旧 R-01～R-192、TASK-0000～0622 和阶段状态已移出当前权威链，可通过 `docs/_history/programs/2026-07/` 与 Git `9b6db346^` 追溯。

TRACE-DL00（用户观察与 self-affect 分离）：`foreground_completed` 可以写入
脱敏 `user_affect_observation.v0`，但不得把用户 VAD 直接混入 canonical Life
的 `affect.valence/arousal`；Life tick 只累计本次新产生的未互动时间，Motive
willingness 映射必须随意愿升高单调降低 withdraw。代码位于
`digital_life/domain_reducer.py` 与 `persona/motive_engine.py`，覆盖为
`test_aw04_life_domain_reducer.py`、`test_dl00_motive_willingness.py` 与
`test_dl00_emotional_baseline_manifest.py`。72 条合成多轮轨迹已冻结，但真人
盲评、真实模型、语音和 Avatar 证据均为 `NOT_RUN`；状态为
`LOCAL_TARGETED_PASS / EMOTIONAL_DIGITAL_LIFE_PARTIAL`。

TRACE-DL01（唯一 Affective Life Kernel）：`UserAffectObservationV1`、
`EventAppraisalV1`、`SelfAffectStateV2`、`EmotionEpisodeV1`、
`MoodStateV1`、`NeedStateV1` 与 `RegulationDecisionV1` 进入既有
`LifeCommand → life_event_journal → CAS snapshot → replay` 链。只有
`affective_event_appraised` 能创建 canonical self-affect；snapshot 最多
保留 4 个 active episode、8 个 component 和 16 个 evidence ref，raw text、
audio、image 和 model-chain 键必须拒绝。0143 的
`affective_life_event_v1` 只读 projection 只能由 immutable Life event
trigger 写入。覆盖为 `test_dl01_affective_life_kernel.py` 与
`test_dl01_affective_life_kernel.py` integration；1002 个真实 SQLite event
restart/replay digest 一致。状态为
`CODE_COMPLETE_LOCAL / INGRESS_NOT_WIRED / LEGACY_COMPATIBILITY_WRITERS_REMAIN`。

TRACE-DL02（多模态观察与 Appraisal）：`AffectFusionService` 位于
`host_brain/digital_life/affect_fusion.py`，由
`input_runtime_composition.py` 和生产 `app/asgi.py` 的 Life command provider
接入 Host Input。它将文本（低置信度）、自述、声学和视觉的脱敏结构化观察写入
0143 Life journal；raw text/audio/image/model-chain 不可入账，视觉/声学/文本
观察分别在 3/5/30 秒后失效。冲突来源保留 ref，深路径最多给出两个 appraisal
候选且最多一次重试；只有选定的 `affective_event_appraised` 能写七海自身
`CARE/CONCERN/CAUTION` 等 self-affect。`emotion_context.py` 仅接受带内部
provenance marker 的 fused context，外部 `affective_life` 注入不能冒充
canonical state。覆盖为 `test_dl02_affect_fusion.py` 与
`test_dl02_affect_fusion_restart.py`，包含 user/self 不镜像、TTL、冲突、scope、
重复 request、Input composition 和真实 SQLite restart/replay。当前为
`CODE_COMPLETE_LOCAL / HOST_INGRESS_WIRED / LEGACY_COMPATIBILITY_WRITERS_REMAIN /
EMOTIONAL_DIGITAL_LIFE_PARTIAL`；尚未运行真实 Host、模型、Edge、TTS 或 Avatar。

当前 NE-01 trace 追加本地代码封口证据：
`08a172da → migration 0142 七路线 canonical SQLite receipt
sha256:161b860f…`，并继承 `27b032f1e` 的 24 个跨进程/Workspace 恢复用例。
该链只把后续实现依赖
推进到 NE-02；production feature promotion、真实网络/provider/Docker 与
回滚仍指向 NE-07，完整架构状态继续 `INCOMPLETE`。

## 1. 解释规则

1. PRD 定义 WHAT，不记录实现完成叙事。
2. DevSpec 定义稳定工程合同，不承担任务状态。
3. 架构级原子状态写入 `current_architecture_status.json`；下表的复合 FR 状态只能由这些原子状态和代码审计推导。
4. 自动化测试证明其覆盖的行为，不能单独把 `PARTIAL` 提升为 `WIRED`。
5. 历史 receipt 只对固定 commit、route 和环境成立。
6. 执行顺序只来自 [NEXT_GENERATION_EVOLUTION_EXECUTION.md](../30_devspec/NEXT_GENERATION_EVOLUTION_EXECUTION.md)。

TRACE-EXEC-08AW（并发 mixed-terminal 进程恢复与 survivor 终态过滤）：
retryable+hard failure、Budget overrun、post-Acceptance Review 必须在
outer settlement 前崩溃后由新 Host 从 durable outcome 唯一收口，resolver
和 worker 调用均为 0。`BLOCKED` 图只有仍含 ready/retry/review/execution/
cancel 节点时才能进入 Host recovery inventory；已释放 Budget 且仅余终态/
结构性阻断节点不得成为永久 survivor。首轮 `2 passed, 1 failed` 找到预算
终态泄漏，修复后新增矩阵及相邻 Host startup 集合为
`14 passed in 46.30s`，旧并发结算回归为 `12 passed in 49.41s`；旧夹具
遗漏 mandatory ReviewRuntime 的问题已修正。状态为
`LOCAL_OS_PROCESS_PASS / FEATURE_DEFAULT_OFF / NE01_IN_PROGRESS`。

TRACE-EXEC-08AV（取消终态与混合 fan-out 跨进程恢复）：取消绑定的签名
`COMMIT/RELEASE` 在目标 mutation 后、handler 返回前崩溃时必须 exact
replay，并由独立 durable cancellation continuation 唯一收口。多节点
checkpoint re-entry 不得同时公开 Node 与 Graph 两个 `done`；一个兄弟已
`ACCEPTED`、另一个停在 `RESERVED/RUNNING` 时，live Lease 必须 defer，
过期后必须持久 current hold，只有 exact case-bound Ed25519 `REACQUIRE`
才能以 higher fence 只重跑未知兄弟和后继 join。当前 Windows spawn/SQLite
新增 `3 passed in 32.25s`，独立相邻回归 `16 passed in 83.15s`。该 trace
保持 `LOCAL_PASS / FEATURE_DEFAULT_OFF / NE01_IN_PROGRESS`，不代表部署 key、
生产 canary、整波 integration 或 NE-07 已完成。

TRACE-EXEC-08AU（Scheduler sweep 冻结与控制 policy 验收）：Clock 必须在
第一个 worker 前从一个 SQLite read transaction 取得 pending CLOCK
occurrence 和 newly-due definition，按 capture time/limit/order/deep JSON
生成 `scheduler-clock-batch://sha256`，消费方重验且同轮禁止第二次 due
query。Universal acceptance reader 必须严格解析 admission 三个版本化
payload、核对 digest、重开 control policy，同时禁止 raw payload 进入收据。
Scheduler/Internal/Operator 各缺失、重复或篡改 policy 时路线失败；Chat/
Skill/Plan/Delegate 携带控制 policy 同样失败。证据入口为
`test_ne01_scheduler_clock_batch.py`、`test_aw02_scheduler_clock.py`、
`test_aw02_scheduler_repository.py`、`test_aw02_scheduler_production_cutover.py`、
`test_universal_route_acceptance.py` 与
`test_ne01_universal_remaining_route_acceptance.py`。核心集合 `45 passed`，
最终受影响集合 `100 passed`，Gate 1～14 与统一 delivery gate 通过；
clean SHA `2a88f56e` 的七路线收据也为 PASS，三个控制 policy count 各为
1。它只升级本项局部控制，FR 复合状态不升级。

TRACE-EXEC-08AT（Scheduler/Internal/Operator 控制执行政策）：三类 surface
复用 `route=delegate` 时必须携带 content-addressed
`ControlRouteExecutionPolicyV1`，绑定 exact command/target/owner、
Review mode、有限 Budget、durable WorkerProjection verification 和
Graph-Acceptance-only Effect。Policy/ref 必须覆盖 WorkOrder input/
metadata、Budget、WorkOrder/TaskNode input refs，并在 initial/replay/
Review/recovery/verifier/acceptance/effect 编译重开。Operator 只使用
durable Intent admission Review；Scheduler trigger 与 reviewed Internal
可使用 TaskNode Review，双 Review 必须拒绝。Review 与 TaskStore 使用同一
canonical clock。同一 Operation version 只允许一个 current nonterminal
recovery case。证据入口为
`test_gr05_direct_host_route_contracts.py`、
`test_aw02_internal_agent_runtime_cutover.py`、
`test_ne01_governed_operator_scheduler_trigger.py`、
`test_governed_operator_replay_attribution.py`、
`test_gr06_acceptance_effect_compiler.py` 与 Operator reconciliation
集成。当前结论仅为
`ENABLED_CONTROL_TRANSPORT_POLICY_WIRED / NE01_IN_PROGRESS`。

TRACE-EXEC-08AU（Operator 旧命令物理退役）：active command manifest 必须
只包含已经进入 HostCommand/TaskGraph 的命令，禁止继续公布永久 blocked
伪能力。`RetiredOperatorCommandV1` 冻结 23 个旧 ID、替代 authority 和
目标波次；active 14 与 retired 23 必须互斥，blocked set 必须为 0。
backend registry/domain handler、OpenAPI 与生成式 Monitor client 均不得
路由 retired ID；认证 BFF 只能返回 410 replacement receipt，Monitor
必须在 fetch 前拒绝。Avatar/Mate 替代为 Edge，Tool/Hunt 替代为 governed
Tool 或 Internal/Scheduler，Forge/Canon/Bio/Bond 等待 NE-05/06 version
activation。证据入口为 `test_obs_domain_command_ports.py`、
`test_ne03_action_preview_front_doors.py`、
`test_aw07_operator_auth_transport.py` 和
`operatorCommandGateway.test.js`。结论为
`ACTIVE_14_GOVERNED / BLOCKED_ZERO / RETIRED_23 /
REPLACEMENT_AND_NE01_INCOMPLETE`。

TRACE-EXEC-08AQ（Skill 唯一执行 authority）：已经准入的 frozen Skill node
只能由 `app/governed_skill_execution.py` 执行；该模块是
`SkillMeta.handler`、sealed authority binding 与 Broker dispatch 的唯一
生产调用者。Executive executor 必须物理不存在，`SkillLoader` 和 Scheduler
metadata 入口只能失败关闭。缺 exact node grant/Task context 必须在 registry
前拒绝，receipt 写失败必须向上游传播。证据入口为
`test_ne01_skill_authority_single_owner.py`、Loader/legacy authority 合同、
GR05 真实 Universal Host Runtime 与机械 AST inventory。干净提交
`3d68bd1eca69ad1593454634c2999d07b4d4f2f5` 的 `209 passed`、真实
GR05、static/doc 与交付元数据 Gate 将结论固化为
`CANONICAL_EXECUTION_AUTHORITY_PASS`。

TRACE-EXEC-08AR（Skill 预算与副作用完备分类）：canonical Host Registry 的 93 个
Skill 必须由 exact `skill_id/tool/action` 分成 28 个 inline read-only、
13 个 pre-Acceptance proposal-only、3 个 post-Acceptance Workspace 和
49 个 blocked pending Effect。只有 44 个启用条目可进入 manifest、
PlanningSearch、Scheduler 与 admission；stale graph 在执行点再次拒绝。
全部启用节点做 Task precharge，Agent 模型提交 canonical measurement，
proposal-only 不投递用户可见 Effect，三条写 Agent 只走 Acceptance 后
Workspace promotion。证据为 `test_ne01_skill_execution_policy.py`、
`test_ne01_governed_skill_model_budget.py`、
`test_ne01_governed_agent_workspace_acceptance.py`、GR05 与 registry/
scheduler/search 回归。当前结论为
干净提交 `c68850f859048be955c15366e33ada3a917d9b12` 的同库 v3 收据已把
Budget 与 transactional Effect 控制提升为 `PASS`，故当前 44 个启用项的
Skill 路线为 `COMPLETE`；NE-01 仍为 `IN_PROGRESS`，49 个 blocked 能力
不计完成。
独立 Personality Registry 的 19 个 metadata 必须保持 manifest=0（5 个
blocked、14 个 unclassified），动态 MCP Skill 也必须 blocked。

TRACE-EXEC-08AS（Plan/Delegate 不可变执行政策）：启用 Plan 必须在
TaskGraph admission 前冻结 content-addressed execution policy，绑定
graph/revision/result、atomic completion、Graph Budget digest、selected
node bindings、当前 Skill Registry risk/Review/execution policy 与 effect
mode。initial、replay、Review continuation、startup/operation recovery 和
Effect 编译都必须重开；中间 Skill 不得拥有 post-Acceptance Effect。启用
Delegate 必须冻结 target/adapter/review/budget/effect policy；当前唯一
`pentest_agent` 在零 Attempt 时进入 durable Review，resume 后仍重验 policy
与 exact-zero usage，并拒绝 Effect/Outbox/delivery claim。证据入口为
`test_ne02_capability_plan_search.py`、`test_gr04_planning_execution.py`、
`test_gr05_direct_host_route_contracts.py`、
`test_ne01_governed_delegate_dispatch.py`、
`test_gr05_frozen_route_executors.py` 和真实 SQLite
`test_gr05_universal_host_runtime.py`。工作树证据为定向 `93 passed`、
四路线 `1 passed`；clean-SHA v3 receipt 尚待重跑，故结论为
`LOCAL_IMPLEMENTATION_PASS / PLAN_DELEGATE_COMMIT_BOUND_RECHECK_PENDING /
ROUTES_INCOMPLETE`。

## 2. 当前核心产品能力

| FR | 工程合同 | 生产落点 | 主要验证 | 当前状态 |
|---|---|---|---|---|
| FR-CHAT | DevSpec §2～4 | `chat_routes.py`、Cortex、Acceptance handlers、Life projection barrier、Surface | exact intent/restart/tamper、GR05 production-order integration | `LOCAL_PROJECTION_BARRIER_PASS / ROUTE_INCOMPLETE` |
| FR-INGRESS | DevSpec §2、§8 | Secure Ingress、Host/Gateway/Scheduler/Internal adapters | route-matrix contracts、Scheduler/Internal production integration | `WIRED_WITH_OPERATOR_MATRIX_LIMITS` |
| FR-SURFACE | DevSpec §4、§11 | Surface/Reply authority、Life projection | surface/life contracts、writer 审计 | `PARTIAL_PERSONA_PROPOSAL_MISSING` |
| FR-MEMORY、FR-CANON | DevSpec §5 | typed repositories、Canon Apply、recall | memory/canon contracts | `WIRED_WITH_KNOWLEDGE_GAPS` |
| FR-TOOL、FR-CAPABILITY | DevSpec §7 | Capability Broker、Skill/Tool/MCP ports | capability/supply-chain contracts | `PARTIAL_LIFECYCLE` |
| FR-GATEWAY、FR-VOICE、FR-EMBODIMENT | DevSpec §8 | Gateway、Edge contracts、device receipt | Edge/Gateway contracts、环境验收 | `WIRED_DEGRADED_HARDWARE` |
| FR-OBS | DevSpec §9 | journal/outbox/projection/Obs/Operator | event/query/privacy tests | `WIRED_WITH_COMPOSITION_GAPS` |
| FR-PENTEST | DevSpec §7 | Pentest domain runtime + PlanningSearch adapter | security/pentest suites | `WIRED_L3_SHADOW_UNCALIBRATED` |
| FR-PROACTIVE | DevSpec §4、§11 | proactive intent/outbox + Persona feedback paths | proactive contracts、writer 审计 | `PARTIAL_PERSONA_FENCE_WIRED` |

TRACE-EXEC-08AM（通用 Worker 独立证据与 Plan 真实产物流）：Chat/Skill/Delegate/Plan/Tool/Operator 的已启用通用 worker 必须由 route-neutral Verifier 与 Acceptance 分别重开 durable WorkerProjection；command/request、graph revision、node、attempt、route/target、node grant、WorkOrder/context、receipt digest、完整 COMMIT 与 output contract 任一漂移都失败关闭。只有固定结构 predicate 可自动 PASS，任意其它验收文字必须进入 durable Review。Plan final Chat 必须实际消费已验收前驱 TaskResult artifact，并携带 artifact/receipt/payload digest bundle attestation。证据为 `test_ne01_governed_worker_evidence.py`、`test_ne01_node_input_artifact_attestation.py`、`test_gr05_frozen_route_executors.py`、真实 SQLite `test_gr05_universal_host_runtime.py` 与最新 `ne01_scheduler_clock_control_route_acceptance_20260727.json`。结论为 `ENABLED_DELEGATE_PLAN_CONTROLS_COMPLETE / NE01_INCOMPLETE`。

TRACE-EXEC-08AN（Plan 节点审批与续签 Grant）：Registry 的 `require_confirm` 必须进入 frozen capability snapshot，不能以过滤候选代替治理；Planning compiler 必须将 candidate policy 重绑定为覆盖每个 immutable TaskNode ID 的政策。Scheduler 只阻塞要求审批的节点，缺失/畸形/无来源政策失败关闭。0096 continuation 消费 exact Review 后，已 accepted predecessor 使用 immutable projection 的历史 grant，尚未执行节点使用 claim-fenced 续签 grant，worker 不重跑。`test_ne02_capability_plan_search.py` 与真实 SQLite `test_ne01_plan_node_review_runtime.py` 加上既有自动 continuation/restart/retry/Universal Host 回归证明该边界。结论为 `PLAN_ENABLED_ROUTE_NODE_GRANT_AND_REVIEW_RESUME_PASS / PLAN_AND_NE01_INCOMPLETE`；graph-scope Budget、一般搜索/恢复、事务副作用和 commit-bound acceptance 不升级。

TRACE-EXEC-08AO（持久 Acceptance 与 Review 原子创建）：0134 要求每个 `ACCEPTED/WAITING_ACCEPTANCE_REVIEW` transition 都有 exact Graph/Node/Attempt-bound append-only Acceptance decision。自动接受必须将 decision、artifact、可选 Effect、Node 与 Outbox 放入一个 UoW；非 `ACCEPT` 必须将 decision、等待 Node、pending ReviewItem 与 Outbox 放入一个 UoW，直接单节点图还共同提交 Graph block。ReviewStore 与 TaskStore 共用唯一 pending INSERT writer；重启按 node version 重开 decision，不重跑 worker/judge。证据入口为 `test_ne01_durable_acceptance_review.py`、`test_ne01_durable_node_artifacts.py`、`test_gr03a_governed_durable_dag.py`、`test_task_node_acceptance_store_structure.py` 与真实 0133→0134 migration。结论为 `DURABLE_ACCEPTANCE_REVIEW_CREATION_LOCAL_PROVEN / DENIAL_TERMINAL_CLOSED_BY_0135 / NE01_INCOMPLETE`。

TRACE-EXEC-08AP（Review denial 原子终态）：0135 要求 exact terminal Review status/version/digest、Node、Graph、graph Budget、三条 Outbox 和 append-only settlement 在一个 `BEGIN IMMEDIATE` UoW 同成同败。PRE Review 必须零 Attempt；POST Review 必须绑定 exact 0134 Acceptance、`SUCCEEDED` Attempt 和可选已有 0090 `BLOCKED` decision。response loss/restart 只可重开 settlement 与既有 Worker receipt，不得签发 Grant、创建 Attempt 或重跑 Worker。证据入口为 `test_ne01_review_denial_terminal_uow.py`、`test_gr03a_governed_durable_dag.py`、`test_task_review_denial_store_structure.py` 与真实 0134→0135 migration。结论为 `ATOMIC_REVIEW_DENIAL_TERMINAL_LOCAL_PROVEN / GENERAL_CANCELLATION_RECOVERY_SEVEN_ROUTE_ACCEPTANCE_AND_NE01_INCOMPLETE`。
| FR-AGENT-RUNTIME、FR-PLANNING | DevSpec §3、§6 | Governed Runtime、PlanningSearch | T-GR-RUNTIME-01、T-PLAN-* | `PROGRAM_INCOMPLETE` |

## 3. Governed Execution

NE-01 Composition 的 Context 子切片 inventory `3/15/6、Container48/2/45` 只作历史；现行值见 TRACE-EXEC-08AA，生产 locator 全维度为 0。该证据不能将外部 namespace、Cortex、NE-06 activation、Chat/Tribe 视为生产完成。

| Trace | FR | 合同 | 生产落点 | 证据 | 当前状态 | 下一波 |
|---|---|---|---|---|---|---|
| TRACE-EXEC-01 | FR-ARCH-003 | 七条生产路线必须共享 HostCommand/TaskGraph 并有 commit-bound 验收 | Cortex、GovernedHostRuntime、Scheduler、Internal、Operator | v2 coverage Gate；历史 GR-05/06 receipt | `INCOMPLETE / ALL_SEVEN` | NE-01 |
| TRACE-EXEC-02 | FR-ARCH-003 | Scheduler 必须进入同一 admission，真实 handler 只能在 Acceptance 后执行，Clock occurrence 必须由 durable owner/fence 唯一调度 | Scheduler governed runtime、immutable operation plan、shared Acceptance Effect dispatcher、0117 Clock retry authority、immutable actual-result ledger | worker 仅生成 `scheduler.operation.execute`；Graph Verification/Acceptance 与 Effect staging 同 TaskStore UoW。0117 在 occurrence admission UoW 原子执行 DUE→OCCURRENCE claim transfer；执行 lifecycle renew/heartbeat，过期 lease/stale fence/第二 owner拒绝，actual failure time 计算 backoff；单元/合同/durable-UoW 19 项、47.53 秒 | `PARTIAL / DURABLE_CLOCK_OCCURRENCE_FENCE`；definition/admin、mutable batch freezing、unknown-effect 人工归因、wave integration 与 canary仍缺 | NE-01 |
| TRACE-EXEC-03 | FR-ARCH-003 | Internal Agent 必须进入同一 admission；raw artifact 不得公开；canonical result 只能在 Acceptance 后写，且失败清理不得早于 durable claim | Internal task runtime、periodic continuation、terminal artifact reconciler、`internal_raw_failure_claim_result.py`、`internal.agent.result_commit`、0116 Workspace lifecycle、`workspace_write_promotion.py` | frozen worker、executor、Review 与 Workspace 直接消费 WorkOrderV3；sealed checkpoint continuation 不重跑 Agent。未 sealed cancellation 继续使用自己的两类签名终态。当前 exact raw failure 只从无 cancellation intent 的 signed `RELEASE/NO_SIDE_EFFECT/no receipt` 六聚合终态生成独立 result-ref；terminal worker 必须先 CAS Secure Ingress claim，再 cleanup 真实 Git worktree/stage，response loss exact reopen，claim outage 与 tamper 均零清理。具备 exact Workspace authority 的 Internal write 会密封 bounded manifest/binary patch，Acceptance 生成 deterministic accepted HEAD 并保持源分支不变 | `PARTIAL / INTERNAL_WRITE_PROMOTION_AND_SIGNED_RAW_FAILURE_CLAIM_BEFORE_CLEANUP`；一般 unknown effect、其它 failure/cancel/budget claim closeout、multi-node 与 general `settle_worker` crash bundle仍缺；Domain/Skill/Codex write、handle-level TOCTOU、OS/container isolation与七路线acceptance也未完成 | NE-01 |
| TRACE-EXEC-04 | FR-NLE-002 | 多节点依赖、Attempt、Lease、Retry、受控 fan-out/fan-in、显式结果、整图原子成功与 verified durable cancellation | coordinator/heartbeat/task store；0090～0099；Budget/Operation focused Store；shared finalizer | 新生产 Graph 默认 atomic；0090/0091分别原子收口失败/成功，公开replay要求Graph COMPLETED+Budget CONSUMED。Budget unique winner、operation-free Attempt takeover，以及 scheduler-owned 多节点 canonical COMMITTED checkpoint continuation已有证据：`EXACT_ACCEPTED / EXACT_REJECTED / MISSING` 复用同 Attempt 的更高 fence且不重放 worker，rejected usage经现有 parallel settlement 收口；bounded/cancellation/reconciliation仍默认 OFF | `PARTIAL_ATOMIC_DEFAULT / NE01_IN_PROGRESS`。general/unknown-effect/multi-node batch recovery、历史半状态、现实resolver、部署key/credential、inventory remediation与wave canary仍缺 | NE-01 closeout/canary |
| TRACE-EXEC-04-FANOUT | FR-NLE-002 | bounded fan-out 只能由真实可验证的 canary authority 启用 | 0120 append-only receipt、TaskRuntimeFeatureCanaryReceiptStore、Host tool-runtime composition | receipt 由外部 Ed25519 签名；Host 重开后精确核对 build SHA、migration version、atomic policy、fan-out buffer digest、有效期与签名，伪签名不写入数据库 | `WIRED_DEFAULT_OFF / SIGNED_GATE_ONLY`；未有真实 Docker/Host/Obs/Monitor/Edge/Gateway canary receipt，不得启用或标 PASS | NE-07 真实服务栈验收 |
| TRACE-EXEC-04A | FR-NLE-002 | 0095 recovery evidence 不得伪造 revisioned ref 或拖慢普通调度 | `read_scheduler_snapshot(include_graph_outboxes)`；PlanningRecovery；Runtime Event replay | 默认 false；recovery 显式 true 且同事务 exact terminal Outbox；legacy payload 原样使用；仅旧版或替代 Store 未提供可选扩展时 fallback；created_ts=0 对 current/legacy 可重建 candidate 的受限容差，除 timestamp 外全字段 exact；GR-04/Runtime Event tests | `WIRED_0095_COMPAT / PROGRAM_STILL_OFF` | 持续故障矩阵 |
| TRACE-EXEC-04B | FR-ARCH-001、FR-NLE-002 | Task mutation 的中央 Outbox insert/replay 实现必须唯一，结构拆分不得产生第二事务 authority | Outbox/BudgetUsage/OperationCheckpoint/LeaseTakeover/Effect focused mixins；纯 Lease identity validator；由 `DurableTaskStore` 组合 | 调用方事务复用、AST MRO/事务顺序/只读SQL门禁、`task_repository.py` 4585 ≤ 4600、takeover mixin 230 < 250；Gate 1～14 全 PASS、未扩白名单 | `WIRED_FOCUSED_STORE / ONE_UOW` | 持续结构门禁，下一切片继续降低 repository 行数余量 |
| TRACE-EXEC-04C | FR-ARCH-003、FR-NLE-002 | Operator TaskGraph 取消必须先完成自己的 governed TaskGraph，随后使用 server-owned Secure Ingress authority 精确取消目标 revision，并在失败时给出可证明的副作用确定性 | Operator control compiler/runtime/Accepted handler、RuntimeGroup cancellation authority、Gateway/BFF/UI | Obs 直接 writer 已删除；外层 Secure Ingress→Governor→TaskGraph→Review/Preview/Verification/Acceptance 后才写目标 intent，只有目标取消 `FINALIZED` 才提交外层 Effect；exact replay 零目标重入；default OFF | `GOVERNED_OPERATOR_CONTROL_LOCAL_SQLITE_PASS / DEFAULT_OFF / PRODUCTION_CANARY_NOT_RUN` | 部署 reconciliation/key/credential 与 HTTP/network canary；完整进程重启、历史半状态/general recovery |
| TRACE-EXEC-04D | FR-NLE-002 | Cancellation Effect 只能凭可证明证据解除 fence；历史修复不得改写旧 authority；独立 Obs 只能只读 canonical DB | Effect reconciliation contract/service/focused Store + 0098；0099 exact auto；`effect_reconciliation_query_repository.py`；Effect delivery/cancellation；Operator command/read routes | 0099 `EXACT_REPAIRABLE` 在 Host 启动默认自动收敛；signed/quarantine 保持 fence。Cancellation 配置要求 Operation、Effect、Terminal 三类 reconciliation 全部启用。其余既有 proof、query-only 和 authority 测试继续有效 | `AUTO_EXACT_ON / OPERATOR_OFF / CANARY_NOT_RUN` | 在每个目标部署运行 legacy inventory gate/remediation；部署 verification keys、Operator credentials与完整 key lifecycle；运行真实 canary |
| TRACE-EXEC-04E | FR-NLE-002 | post-COMMIT 自动接管不得重跑 worker、补造冲突预算事实或把不确定性伪造成失败 | checkpoint Store/runtime、Budget recovery/validation、DAG scheduler、0130 recovery hold | exact checkpoint 的 accepted/rejected/missing 按 immutable graph order续接；共享 `EXCEEDED` missing 绑定唯一 winner。双 checkpoint、four-root、五节点菱形和八根节点均不重跑 worker；unknown/cancellation/conflict 写 exact hold + Outbox，conflict 零 takeover Lease | `GENERAL_SHAPE_COMMITTED_CHECKPOINT_RECOVERY_LOCAL_PROVEN / DURABLE_AMBIGUITY_HOLD_LOCAL_PROVEN` | 无限规模、其它 failure/cancellation、自动归因、Workspace 终态、部署 fan-out与 wave canary |
| TRACE-EXEC-04F | FR-NLE-002 | ACCEPTED 已落盘而 Effect 未落盘时不得无 Effect 完成 Graph | `AcceptedGraphFinalizationRuntime`、Graph+Effect Store UoW | process-loss restart、worker no-rerun、effect id/digest/idempotency replay、tampered projection fail-closed | `PROVEN_NARROW_RESTART_FINALIZATION` | production canary；跨系统 eventual dispatch 仍非单事务 |
| TRACE-EXEC-04G | FR-ARCH-001、FR-ARCH-003、FR-NLE-002 | 生产 Graph admission 与 Acceptance Effect staging 不得重新暴露 standalone Python 写 authority；两类新 INSERT 都必须在数据库边界失败关闭 | `HostCommandRepository._admit_normalized()`；`TaskDagStateStoreMixin._insert_graph_from_host_command_in_transaction()`；0124 `task_graph_admission_authority`；0125 `task_acceptance_effect_staging_authority`；三个 canonical Acceptance UoW | 三个 public 低层入口均不存在；Graph hook 仅 admission UoW 1 次，Effect hook 仅三个 canonical UoW 共 6 次，INSERT SQL 各只有一个私有 owner。0124 要求 exact graph/revision scope；0125 要求 exact effect/graph/revision/node/attempt/commit-scope/idempotency scope与规范初始状态，exact replay 不进入 scope。plain SQLite、wrong/unscoped Store、legacy source、late Node、raw HostCommand、非规范 Effect、trigger tamper 与迁移中途故障均失败关闭；既有行不重写。生产 AST 只允许 `_admit_normalized()` 持有 Graph scope、Effect 私有 hook 持有 Effect scope，禁止 alias/attribute 逃逸。最终受影响去重批次 `99 passed in 245.03s`；0124 的 `27 passed` 仅作被覆盖历史，不与 99 相加 | `APPLICATION_AND_SQL_ADMISSION_AND_EFFECT_STAGING_WRITE_AUTHORITIES_SEALED / EXISTING_ROWS_UNCHANGED / OTHER_RUNTIME_MUTATIONS_OUT_OF_SCOPE / NE01_IN_PROGRESS`。wave integration `NOT_RUN_PER_WAVE_POLICY`；`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA` | 继续消除其它 UPDATE/DELETE mutation authority；NE-01 波次集成与 production canary |
| TRACE-EXEC-04H | FR-ARCH-001、FR-NLE-005 | runtime/service-catalog 业务层不得持有私有 SQLite 连接、SQL 或事务；rollout receipt/head 必须原子 | `RuntimeEventStore` readiness port → `RuntimeEventRepository`；rollout Protocol → `SQLiteServiceCatalogRolloutRepository`；`host_startup.py`/ASGI concrete wiring | Context `7 passed`；Catalog unit/contract `15 passed`；Host socket `6 passed`；Repository/Global 独立复跑 `16 passed`（与 15 重叠不相加）；Global SQLite boundary `PASS`；`ACTIVATION_STARTED` receipt + head failure rollback；`_write_lock` RLock 误判更名 `_transition_lock` | `GATE_10_PASS / CURRENT_FULL_STATIC_PASS / TYPED_COMPOSITION_INCOMPLETE / NE01_IN_PROGRESS`。无 migration；该切片当时的基线失败不覆盖当前结果，当前 Gate 1/6/10/11 均已通过；wave integration `NOT_RUN_PER_WAVE_POLICY`；`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA` | 将 concrete wiring 收入 typed app-local composition |
| TRACE-EXEC-04I | FR-ARCH-001、FR-NLE-005 | Obs projection daemon 必须只有一个 app lifespan 启停 owner，mutable ports bundle 不得跨 app 共享；本地故障必须进入 secret-free health supervision | `ObsApplicationPorts.claim_lifecycle_owner()`；`ObsApplicationContainer.start_projection_jobs()/health_snapshot()/close()`；`ProjectionJobDaemon.health_snapshot()`；`obs_fastapi_app.py::create_obs_app().lifespan` 与 `/health` | owner/lifecycle 原有定向 19 项保持；新合同/单测首批 6 项。普通 job/lease acquire/renew 失败只输出错误类型和 opaque incident，线程继续，成功波清零连续失败；fatal process-control exit 进入 `FAILED`，显式 close 前拒绝重启；真实 `/health` 呈现 running/generation/wave/failure/recovery。首次受影响回归 67+1 fixture failure，补 lifespan 后单 case 1，最终受影响集合 68；批次重叠不相加 | `APP_LOCAL_SUPERVISION_WIRED / DURABLE_SLO_AND_CANARY_MISSING / NE01_IN_PROGRESS`。无 migration；wave integration `NOT_RUN_PER_WAVE_POLICY`；`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA` | durable SLO journal、Guardian acknowledgement、跨进程事件历史、production canary |
| TRACE-EXEC-04J | FR-ARCH-001、FR-NLE-005 | Service Catalog application 不得反向依赖 composition，persistence 不得依赖 application 合同 | `service_catalog/startup_coordinator.py`；`contracts/service_catalog.py`；composition/legacy contract 同对象重导出；contracts-only `SQLiteServiceCatalogRolloutRepository` | application→composition 2→0，Catalog persistence→application 1→0；SCC application/composition/persistence→application/persistence；contract/unit 61、真实 Catalog/Host socket 7、边界复跑 7（集合可能重叠）；Edge violation=0、reverse import=0；py_compile/diff-check PASS | `PARTIAL / GATE_1_FAIL / NE01_IN_PROGRESS`。该切片快照仍有 persistence→application 11 条（PlanningSearch 7、Evolution/Lab/Skill/World 4），后续 TRACE-EXEC-04K 清除其中 7 条；无 migration；wave integration `NOT_RUN_PER_WAVE_POLICY`；`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA` | 按 TRACE-EXEC-04K 继续清除反向依赖；完成 typed app-local Composition |
| TRACE-EXEC-04K | FR-ARCH-001、FR-PLANNING | PlanningSearch 持久化只能依赖纯合同包，旧 application 类型路径必须保持兼容重导出而不成为 repository authority | `contracts/planning_search/`；core/benchmark/calibration/code/research 八组合同；code benchmark/calibration/research shadow SQLite repositories；旧路径同对象重导出 | 边界 5；D13/D18/D19/D20 60 passed、1 skipped（Windows symlink 权限）；`__all__`、类型 identity、`isinstance`、序列化形状兼容；7 条 PlanningSearch persistence→application 清零；application→composition=0、Edge violation=0、reverse import=0；py_compile/diff-check PASS | `PARTIAL_CONTRACT_LAYERING_ONLY / GATE_1_FAIL / NE01_IN_PROGRESS`。application/persistence SCC 仍有 Evolution/Lab/Skill/World 4 条；Gate 6/10 PASS；无 migration；wave integration `NOT_RUN_PER_WAVE_POLICY`；`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`；不构成 PlanningSearch 功能或收益完成 | 清除剩余 4 条反向依赖并复跑 Gate 1；NE-02 独立完成生产候选与收益证明 |
| TRACE-EXEC-04L | FR-ARCH-001、FR-NLE-004、FR-NLE-005、FR-ARCH-010 | Evolution、Evolution Lab、Runtime Context、Skill Lifecycle 的持久化共享类型只能来自中立 contracts，repository 不得反向依赖 application | `contracts/evolution.py`、`contracts/evolution_lab.py`、`contracts/runtime_context.py`、`contracts/skill_lifecycle.py`；旧路径同对象重导出；Evolution Governance/Lab、Skill Lifecycle、World Observation repositories | 边界 4；Context/World、Evolution Lab、Skill、Evolution Governance 定向 32；py_compile/diff-check PASS；最后 4 条 persistence→application 清零；fresh Gate 1 issues=[]、SCC=[]、Edge finding/violation=0、reverse imports=[]，Gate 1/6/10 PASS；该切片完成时 full static `failed_gates=[11]`，后续 TRACE-EXEC-04M 关闭 Gate 11 | `GATE1_PASS / CURRENT_FULL_STATIC_PASS / NE01_IN_PROGRESS`。无 migration；wave integration `NOT_RUN_PER_WAVE_POLICY`；`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`；仅证明分层与类型 identity，不构成 NE-04～NE-06 功能、Universal Runtime、PlanningSearch 收益或 Full Architecture 完成 | 完成 typed app-local Composition、旧 authority 清零与 NE-01 波次生产验收；后续波次独立证明功能闭环 |
| TRACE-EXEC-04M | FR-ARCH-001、FR-NLE-005 | ToolBroker 生产构造必须 app-local 且唯一，process locator/container 与未消费 lifecycle provider 不得拥有第二构造 authority | `host_brain/tools/broker.py`；删除 `get_tool_broker`、`tool_broker` process slot 与 `HostLifecycleToolBrokerProvider`；Gate 11 production-service inventory | #89 commit-bound：唯一 constructor count=1；process accessor/slot/dead provider=0；Container 54 fields/4 locks/49 refs，named 45/123、all-form 51；结构/生命周期/bridge/route-token/seed 43 passed；ASGI+Tool/MCP+HTTP+broker+MCP audit+真实 SQLite 98 passed；补充 1 项与 43 重叠；py_compile、policy JSON、whitespace PASS | `GATE_11_PASS / FULL_STATIC_PASS / NE01_IN_PROGRESS`。`failed_gates=[]`；生产 locator 后由 08AA 物理退役，真实 workspace-bound governed executor、完整 typed Composition 与波次验收仍缺；`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA` | 保持 locator 退役 Gate，接入真实 executor/canary |
| TRACE-EXEC-04N | FR-NLE-002 | Operation 预留前的持久 Attempt 只能在无 live owner、无 Operation且预算lineage精确时接管；Operation建立后的异常不得伪造普通失败；Lease acquisition response loss只能exact replay同一请求 | `node_execution_preparation.py`、`preoperation_attempt_recovery.py`、`task_lease_takeover_authority.py`、`task_lease_replay_identity.py`、`dag_scheduling_runtime.py`、TaskStore fences | 真实SQLite覆盖same Attempt/higher fence、PRECHARGE、唯一Budget、Operation absence、deterministic Lease/Heartbeat、response loss、并发winner、live owner与post-reservation hold；本切片另证明 scheduler-owned 多节点 exact COMMITTED checkpoint continuation，但不把其推广为 pre-Operation 或 general batch recovery | `ATOMIC_DEFAULT / PRE_OPERATION_TAKEOVER_PROVEN / SCHEDULER_OWNED_EXACT_MULTI_NODE_CONTINUATION_PROVEN / GENERAL_RECOVERY_INCOMPLETE` | unknown-effect reconciliation、general multi-node/batch settlement、七路线wave验收 |
| TRACE-EXEC-04O | FR-ARCH-003、FR-NLE-002 | Host 启动后必须从 durable inventory 精确重入全部已有 Host admission，并在 live Lease 到期后在线重扫；单页容量不能导致整批恢复死锁，也不能等待客户端重复请求或重新构造 ingress/plan | `HostCommandRepository.list_nonterminal_admission_page()`、`persistence/host_command_recovery_inventory.py`、`app/host_startup_graph_continuation.py`、`app/periodic_durable_continuation.py`、`GovernedHostRuntime.recover_startup_graphs()`、`activate_internal_runtime()` | RuntimeGroup 和共享 Acceptance Effect owner 已激活后，repository 在每个只读事务中按 Host surface 联结 `CREATED/ACTIVE/BLOCKED` admission/Graph，以 immutable `(created_at_ms, command_id)` keyset 分页；cursor 绑定 surface digest，continuation 复验页序、页尾 cursor、command/Graph 唯一性、每页与整轮上限。foreign surface 不进入 Host 页，第 101 条继续下一页。mandatory boot pass 后 app-local supervisor 周期/wake 重用同一 continuation；每条只重开同一 `HostCommand + WorkOrderV3 + ExecutorAssignment + TaskGraph`。live Lease 或 `GOVERNED_EXECUTION_IN_PROGRESS` 为 `DEFERRED`，到期后可在线重评，page/snapshot/tuple/receipt 漂移失败关闭。独立 OS 进程写入 101 个真实 SQLite admission 后，新 Runtime 跨两页全部重开；定向集合 `10 passed` | `LOCAL_TARGETED_PASS / PAGINATED_LIFECYCLE_HOST_SURVIVOR / NE01_IN_PROGRESS`；不证明 unknown-effect、general multi-node/batch、wave integration 或 canary；`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA` | 完成 unknown-effect、一般多节点与其它恢复缺口，再执行 NE-01 wave integration |
| TRACE-EXEC-04OA | FR-ARCH-003、FR-NLE-002、FR-SURFACE | Internal、Scheduler、Operator 不能因不属于 Host inventory 而失去一般 nonterminal startup survivor；恢复发现必须按唯一 runtime 路由，且不能伪造 Operation 证据 | `composition/runtime_group.py`、`internal_runtime_composition.py`、`host_startup_graph_continuation.py`、`internal_startup_admission.py`、三类 runtime、`test_ne01_control_surface_startup_continuation.py` | RuntimeGroup 绑定独立 `INTERNAL/SCHEDULER/OPERATOR` surface digest，在 Host mandatory scan 后、Scheduler clock 前跨页扫描；immutable command surface/target 只映射到一个 runtime 的 `continue_startup_admission`。Operation recovery 在外层验证真实 reconciliation 后复用该核心，scanner 自身不合成 operation。App composition 注入 continuation/supervisor factory，Composition 不反向依赖 App；Internal startup 校验/执行/终态归并已从 1507 行 runtime 拆出，最终 1457 行且无新白名单。并发 mandatory scan 只执行一次；mandatory 或周期 supervisor shutdown 都先 join 活动 re-entry，不发 task cancellation。101 条控制记录跨两页精确分配为 Internal 34、Scheduler 34、Operator 33，真实 SQLite 三类 admission 各重开一次，新增定向 `5 passed in 1.92s`，职责拆分后集中定向 `56 passed in 58.09s`；首轮 static scan 的两条反向边和首轮 Gate 3 的 1507 行问题均如实失败，修正后 static scan、Gate 1～14 全部通过 | `LOCAL_TARGETED_PASS / PAGINATED_CONTROL_SURFACE_SURVIVOR / NE01_IN_PROGRESS`；Internal sealed-checkpoint authority 不变，真实 production worker 跨进程完成、一般 failure/cancellation/unknown-effect、wave integration 与 canary 仍未证明；`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA` | 完成控制面真实 worker/process restart 与一般 recovery 后执行 NE-01 波次集成 |
| TRACE-EXEC-04OB | FR-ARCH-003、FR-NLE-002 | Host restart 必须按最后 durable boundary 决定 worker 是否可重入；同 Attempt/Lease/Operation/Receipt 不得因换进程而失去 fencing 和 exact replay | `host_startup_graph_continuation.py`、`governed_task_adapter.py`、`preoperation_attempt_recovery.py`、`operation_checkpoint_continuation.py`、`test_ne01_host_worker_process_restart.py` | 三条 Windows spawn + file SQLite：A 在 PRECHARGE 后/Operation 前硬退出，B 复用同 Attempt、旧 Lease EXPIRED、新 Lease fence+1、一次 worker 后完成；A 在 Operation+Budget committed 后/Attempt success 前硬退出，B worker=0、原 receipt 完成 Verification PASS/Acceptance ACCEPT；`root→child` 中 root ACCEPTED artifact exact 不变，仅 child 恢复。每条 C 再启动 inventory/worker=0。新增集合 `3 passed in 22.41s`；连同既有 takeover 与 Host startup pagination 的相邻回归 `8 passed in 42.89s`，二者重叠不相加。两次首轮读取失败分别来自把嵌套 decision 当顶层和把 Lease 误认为含 node_id，按真实合同修正后通过 | `LOCAL_REAL_OS_PROCESS_AND_SQLITE_PASS / SAFE_SUBSETS_ONLY / NE01_IN_PROGRESS`；worker 是受控 production-protocol projection，不是现实模型/Skill/provider。并发 fan-out、RESERVED unknown-effect、签名 reacquisition、其它 failure/cancellation、控制面真实 worker、wave integration/canary 未完成；`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA` | 扩到 reserved unknown-effect 的 signed reconciliation、并发 fan-out 和其它 control surfaces |
| TRACE-EXEC-04P | FR-ARCH-003、FR-NLE-002 | 原始 Operation reconciliation 签名不得进入通用 TaskGraph；`REACQUIRE` 必须复用同一 Attempt、取得 higher fence，并在 Acceptance 后继续原 immutable graph | 0126 evidence ledger、`governed_operation_reconciliation_runtime.py`、`accepted_operation_reconciliation_handler.py`、`operation_reacquisition.py`、route continuations | admission 前验签并 append evidence；WorkOrder/Effect 仅含 ref/digest。Secure Ingress→Governor→risk-4 Review→Preview→Verification/Acceptance 后重开并再次验签；真实 SQLite 证明同 Attempt/higher fence/new owner/new Budget segment、原 worker续跑一次、Operation COMMITTED、exact replay零 target re-entry，evidence drift/UPDATE/DELETE失败关闭 | `GOVERNED_SIGNED_SINGLE_OPERATION_REACQUIRE_LOCAL_PROVEN / DEFAULT_OFF / NE01_IN_PROGRESS`；部署 key lifecycle、HTTP/network canary、一般 unknown-effect、general multi-node/batch 和 wave integration 未完成；`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA` | 部署 verification keys 与 Operator credential；完成一般 unknown-effect/multi-node恢复并执行波次 canary |
| TRACE-EXEC-04Q | FR-ARCH-003、FR-NLE-002 | 已持久化 WorkerProjection 的未知结果只能由 exact signed checkpoint `COMMIT` 确认；确认后必须继续原图且不得重跑 worker | `operation_checkpoint_reconciliation.py`、focused checkpoint Store、`accepted_operation_reconciliation_handler.py`、RuntimeGroup route continuation、0127、真实 SQLite integration | 无 cancellation intent 时只接受 `COMMIT + worker-projection://.../<sha256>`。读取层与 Operation CAS 的同一 SQLite UoW 分别重开 immutable Command/Graph/Node/Attempt、scope/fingerprint、projection digest、无 cancellation 与 expired former Lease；0127 阻断 projection UPDATE/DELETE。错误签名/live Lease/tamper拒绝，首次 COMMIT 响应丢失 exact reopen，原 Graph完成且 worker调用数保持1，唯一`OPERATION_RECOVERY_COMMIT` | `SIGNED_SINGLE_NODE_WORKER_CHECKPOINT_COMMIT_LOCAL_PROVEN / DEFAULT_OFF / GENERAL_RECOVERY_INCOMPLETE`；普通 RELEASE、任意external receipt、部署key/HTTP、general multi-node/batch、wave integration与canary未完成；`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA` | 扩展其它可证明副作用类型和多节点批次，部署 key/credential并执行 NE-01 波次 canary |
| TRACE-EXEC-04R | FR-ARCH-003、FR-NLE-002 | 没有 cancellation intent 的 raw worker failure 只有在签名证明无副作用且 canonical 负向事实完整时才能终结目标节点；多节点恢复不得夺取共享 Graph/Budget authority，响应丢失与 Internal 清理不得重跑 worker | `operation_failure_reconciliation.py`、`task_operation_failure_reconciliation_store.py`、`accepted_operation_reconciliation_handler.py`、`parallel_settlement_runtime.py`、`internal_raw_failure_claim_result.py`、`internal_artifact_terminal_reconciliation.py` | 真实 migrated SQLite 与临时 Ed25519 覆盖 signed `RELEASE/NO_SIDE_EFFECT/no receipt`、ACTIVE Graph、未终结目标 Attempt/Node、过期 Lease、唯一 graph Budget、零 WorkerProjection/Acceptance Effect/cancellation。第一 UoW 写 Operation RELEASED、Attempt/Node FAILED、Lease EXPIRED及四条 Outbox；单节点兼容路径同时终结 Graph/Budget，多节点路径保持二者 ACTIVE。重启后 0090 从 durable outcome 写唯一 failure decision、Graph FAILED、Budget RELEASED及Outbox，READY sibling worker 调用为0；并发双失败时非 selected 节点仍由同一 decision outcome 覆盖并可重开。v4 Acceptance receipt 在 `PENDING→COMMITTED` 间稳定，v3 单节点摘要不变；response-loss exact reopen，删除 0090 Budget Outbox 后 fail-closed。受影响运行集32项通过；Internal 单节点 claim-before-cleanup证据继续有效 | `SIGNED_MULTINODE_NODE_FAILURE_PLUS_0090_SETTLEMENT_LOCAL_PROVEN / DEFAULT_OFF / GENERAL_RECOVERY_INCOMPLETE`；自动 unknown-effect 归因、其它 failure/cancellation、external receipt、Workspace/claim closeout、部署key/HTTP、七路线 commit-bound acceptance与wave canary未完成；`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA` | 扩展其它 failure/cancellation 与 Workspace 终态；部署 key/credential并执行 NE-01 波次 canary |
| TRACE-EXEC-04RA | FR-ARCH-003、FR-NLE-002 | 签名 COMMIT/RELEASE 的目标 mutation 已提交但 Accepted handler 尚未返回时，进程崩溃不得产生第二次 worker、第二个 recovery case 或第二份终态 | `test_ne01_signed_terminal_process_restart.py`、`accepted_operation_reconciliation_handler.py`、checkpoint/failure reconciliation service、0131 current recovery case | 两条 Windows spawn + 文件 SQLite 链均由进程 A 产生真实 hold、独立进程 B 签名 exact case 并在目标 UoW mutation 后硬退出、进程 C exact replay、进程 D 终态启动零 replay。COMMIT 续接 Verification/Acceptance 且 worker=0；无 intent RELEASE 重开 failure terminal 且 worker=0。所有既有 checkpoint/failure evidence fixture 同步绑定 current case ref/subject digest。新增 `2 passed in 18.33s`，既有签名回归 `5 passed in 18.06s` | `LOCAL_REAL_OS_PROCESS_AND_SQLITE_PASS / SIGNED_COMMIT_AND_NO_INTENT_RELEASE_HANDLER_CRASH_SUBSET / NE01_IN_PROGRESS`；现有 Operation/Outbox UoW 已满足合同，无新增 production writer；cancellation-bound 组合、自动 unknown-effect 归因、部署 fan-out/key/HTTP、真实 provider 与 wave canary未完成；`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA` | 扩展 cancellation-bound 终态组合与并发部署 fan-out，再执行 NE-01 波次验收 |
| TRACE-EXEC-04RB | FR-ARCH-003、FR-NLE-002、FR-NLE-005 | Plan 节点的 signed no-effect failure 与 graph Budget overage 同时成立时，节点 proof 与图级 0090 authority 必须组合但不得互相冒充；Workspace 删除必须在最终 CAS 独立重开二者 | `task_operation_failure_reconciliation_store.py`、`task_budget_usage_store.py`、`accepted_operation_reconciliation_handler.py`、`workspace_terminal_failure_authority.py`、`skill_artifact_terminal_reconciliation.py`、`test_ne01_plan_node_review_runtime.py` | 真实 SQLite/Git/Review/fresh Runtime：writer seal 后、checkpoint 前中断，确定性 elapsed time 使 Budget EXCEEDED；Ed25519 RELEASE 关闭 Operation/Attempt/Node/Lease，fresh 0090 BUDGET 原子写 Graph BLOCKED/Budget RELEASED。组合 reader 绑定 failure error ref、唯一 winner usage、child reservation/usage 与 Outbox；node-failure kind 和 Accepted handler v4/v5 receipt ref 在 0090 前后保持稳定，response-loss exact replay，writer/result 零重跑。删除 current Budget Outbox 后 TaskStore 与 Workspace CAS 均失败关闭。六路径 `6 passed in 39.93s`，相邻集合 `46 passed in 137.82s`，独立报告 | `SIGNED_NODE_FAILURE_PLUS_BUDGET_TERMINAL_LOCAL_PROVEN / ONE_REVIEWED_PLAN_WRITER / NE01_IN_PROGRESS`；无 migration；完整 DEGRADE/REVIEW、其它 failure/cancellation/unknown-effect 组合、多 Workspace、wave integration 与 NE-07 未完成；`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA` | 扩展剩余 terminal cross-product、一般 Budget policy 与多 Workspace，再执行 NE-01 波次集成 |
| TRACE-EXEC-04S | FR-ARCH-003、FR-NLE-002 | cancellation-independent Acceptance Effect 只能由 exact signed `GENERAL_UNKNOWN_EFFECT` evidence 归因；原始签名不得进入通用 WorkOrder/TaskGraph，目标 Effect 与 proof/Outbox 必须原子收口 | 0128；`governed_effect_reconciliation_runtime.py`；`accepted_effect_reconciliation_handler.py`；general reconciliation contract/service/focused Store | Secure Ingress admission 前验证签名并 append evidence，图内只持 ref/digest；风险 4 Review 前 proof 为 0，Preview/Verification/外层 Acceptance 后重开并重新验签。真实 SQLite 覆盖 `NO_SIDE_EFFECT + quiescence → PENDING`、`SIDE_EFFECT_CONFIRMED + durable receipt → COMMITTED`、exact replay、subject/signature/evidence drift、UPDATE/DELETE 围栏和 outer handler receipt exact binding | `SIGNED_GENERAL_ACCEPTANCE_EFFECT_RECONCILIATION_LOCAL_PROVEN / DEFAULT_OFF / NE01_IN_PROGRESS`；cancellation-bound modes 分离，部署 key/HTTP/network、general multi-node/batch、七路线 receipt、wave integration与canary未完成；`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA` | 部署 key/credential，完成联合 readiness、网络 canary 与其余 general recovery |
| TRACE-EXEC-04T | FR-ARCH-003、FR-NLE-002 | Review approve/reject/resume 不能由 Obs 直接写；每条控制必须有独立 TaskGraph lineage，bearer 不得进入 WorkOrder/SQLite，resume 必须 task/subject/version/checkpoint-bound 且一次性 | `governed_operator_control_*`、`operator_control_acceptance_effect.py`、`AcceptedOperatorControlHandler`、`TaskReviewCommandService`、ReviewStore | 真实 SQLite 端到端覆盖外层 Review、approve→public token→resume、reject、历史 approve exact replay；Store 重开合同证明 public token 与普通 bearer API 隔离、一次性消费和 duplicate 拒绝；数据库字节与全部 control WorkOrder 不含 `review_resume::` | `GOVERNED_REVIEW_CONTROLS_LOCAL_SQLITE_PASS / PROGRAM_INCOMPLETE`；完整 Host 进程重启、Operator credential/UI、HTTP/network canary、wave integration未完成；`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA` | 执行完整进程恢复与部署 canary；继续迁移剩余 `27/37` Operator 命令 |
| TRACE-EXEC-04U | FR-ARCH-001、FR-ARCH-003、FR-NLE-002、FR-NLE-005 | Scheduler enable/disable 与 Host mode rules/event 不能由 Obs 直接写；每条状态控制必须独立进入 TaskGraph，且只在 Review/Preview/Verification/Acceptance 后修改 runtime-fenced canonical owner | 0129；`contracts/host_mode_policy.py`；`persistence/host_mode_policy_journal.py`；`HostModePolicyOwner`；`governed_operator_state_control_runtime.py`；`AcceptedOperatorStateControlHandler`；Scheduler accepted command service | 真实 SQLite 覆盖四命令端到端、Scheduler exact replay、1001-event policy replay、append/head 故障回滚、tamper、released fence、operation drift；受影响代码回归 175 项通过。休眠 Scene 的 Proactive block 保持，但显式 Operator 唤醒仍经 risk-4 Review | `GOVERNED_OPERATOR_STATE_CONTROLS_LOCAL_SQLITE_PASS / OPERATOR_14_OF_37 / PROGRAM_INCOMPLETE`；当时记录的独立 Scheduler CRUD HTTP 缺口已由 TRACE-EXEC-04V 关闭；完整 Host 进程重启、credential、真实 HTTP/network canary 与 wave integration未完成；`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA` | 迁移剩余 `23/37` Operator 命令；执行波次集成和部署 canary |
| TRACE-EXEC-04V | FR-ARCH-001、FR-ARCH-003、FR-NLE-002、FR-NLE-003 | Scheduler 管理 HTTP 不得直接写 definition；双前缀 create/update/delete/enable/disable 必须作为 `SCHEDULER` HostCommand 进入 TaskGraph，并在 Acceptance 后以不可变 operation receipt 封口 claim | `scheduler_routes.py`、`governed_operator_state_control_runtime.py`、`operator_state_control_support.py`、`AcceptedOperatorStateControlHandler`、`scheduler/command_service.py`、claim result validation、ASGI typed provider wiring | route cutover 单元覆盖两个前缀并令旧 writer 调用即失败；真实 SQLite 覆盖 publish/update/disable/delete、Review continuation、exact replay；Acceptance 后 operation 已提交而 claim completion 崩溃时，新 repository/service 只重开同一 receipt，definition head 不变。主批次 `34 passed in 139.12s`；Scheduler occurrence/dispatcher/Preview/UoW 扩展组 `57 passed in 54.81s`，两者不相加。Gate 3 首轮 1511 行失败后真实拆分至 1500 行，最终 Gate 1～14 PASS | `SCHEDULER_ADMIN_DIRECT_PRODUCTION_AUTHORITY_ZERO / LOCAL_REAL_SQLITE_PASS / ROUTE_AND_NE01_INCOMPLETE`；mutable batch、一般多节点/unknown-effect、commit-bound 七路线、完整进程重启、部署凭据、真实 HTTP/network canary 与 wave integration未完成；`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA` | 完成 Scheduler 余下恢复/批次缺口并执行波次集成与部署 canary |
| TRACE-EXEC-05 | FR-NLE-002 | Budget 声明必须被 runtime 消费且高竞争只产生一个超限 authority | budget runtime、`TaskBudgetUsageStoreMixin`、`test_gr03a_governed_durable_dag.py` | 8/16/32 线程、跨进程、restart/interleave/drift；concurrency=8 时 8 次 CAS conflict 后第 9 次成功；一般两节点 remaining-time timeout 与 concurrency=1 child reservation | `CONTENTION_AND_TWO_NODE_TIME_CONCURRENCY_PROVEN / PROGRAM_PARTIAL` | 完整 DEGRADE/REVIEW、一般 unknown-effect recovery、program-wide coverage |
| TRACE-EXEC-06 | FR-NLE-003 | Review token 后自动安全 continuation | ReviewStore、0096 coordination、Review worker、surface runtime、`review:decide` ingress、`dag_scheduling_review_denial.py` | task-bound PRE/POST、claim takeover、POST no-rerun；`REJECTED/EXPIRED/CANCELLED` 只携带 exact node identity、无 worker grant，且在 scheduler 重新确认等待 checkpoint、无 active sibling 与 `REVIEW_DENIED` 后才终结；node/grant/status/decision 漂移失败关闭 | `WIRED_0096_TASK_BOUND / CANARY_NOT_RUN`；拒绝终结是窄路径，不提升为一般 Review 或 multi-node recovery | 部署 credential/canary；不要与 single-node Operation takeover或 general recovery混写 |
| TRACE-EXEC-07 | FR-ARCH-003 | 不得有第二任务真相 | Durable Task/Conversation projections | registry absence contract + code audit | `WIRED` | NE-01 已完成切片 |
| TRACE-EXEC-07A | FR-ARCH-003、FR-NLE-003 | 未创建 TaskGraph 的 projection/read route 不得绕过 Acceptance 写入可见对话或派生副作用 | `cortex_runtime.py`、`read_routes.py` | T-NE01-READ-PROJECTION-01：以会抛错的 user/assistant writer 注入仍可返回 Task/conversation projection；无 Task 的 `task_progress/recent_turn` 不调用 conversation writer | `LOCAL_TARGETED_PASS / NE01_IN_PROGRESS`：read route 仍非 governed Task、没有 ingress receipt，也不完成七路线覆盖或 NE-01 | NE-01 正向图切流 |
| TRACE-EXEC-08 | FR-ARCH-001 | 显式 composition、生产无 locator | fresh typed ports + app-local Host lifecycle/durable HardState/Authority/Bond/Memory/Governance runtime + Host-owned Tribe shared runtime/Agent catalog + durable ResourceManager status | 生产 consumer/import/module/field/lock/ref 全为 0；Host builder 无全局 fallback，测试桥仅在 pytest；两个重叠 Tribe scope 的 Agent/registry identity 隔离，23 个 Agent 与三类 shared runtime 的无上下文 fallback 已归零；ResourceManager status 已由 0123 runtime-scope journal 持久化，旧共享文件生产路径为 0。外部 namespace、Token/MCP durable restart、其它外部资源恢复、legacy Executive generic delegate 和真实 executor 仍未闭环 | `PRODUCTION_LOCATOR_PHYSICALLY_RETIRED / HOST_APP_LOCAL_OWNER_35 / TRIBE_NO_CONTEXT_FALLBACK_RETIRED / RESOURCE_MANAGER_STATUS_DURABLE / AUTHORITY_EXACT_OWNER_BOUND / NE01_IN_PROGRESS` | 完成其它外部资源恢复、外部 namespace、事务化 Chat、多节点执行和 generic Delegate 真实切流 |
| TRACE-EXEC-08I | FR-ARCH-001、FR-NLE-003 | 受治理 Chat 的 Surface 主链、普通 Chat 与 Host Input 准入不得按请求读取全局服务，必须接收 Host lifespan 固化端口并保留 Acceptance 副作用边界 | `app/surface_runtime_composition.py`、`app/tribe_composition.py`、`app/chat_immune_gate.py`、`input_runtime/ports.py`、`input_runtime/contracts_and_normalization.py`、`internal_runtime_composition.py`、`tool_runtime_composition.py`、`route_execution_ports.py`、`surface_main_chain_adapter.py`、`chat_handler.py`、ASGI、`http_hooks.py` | Host 将同一 `AgentRuntimeApplicationPorts + Life authority` 组合为 immutable `SurfaceRuntimePorts`，经 Internal/Tool Runtime 注入 frozen Chat；ASGI 同时将 app-local `InputRuntimeApplicationPorts` 与 `TribeRuntimeAgentPorts` 交给普通 Chat、Gateway 与 ASR。`chat_immune_gate` 只经 `TribeRuntimeAgentPorts.require_immune_agent()`，输入归一化只经 `InputRuntimeApplicationPorts.require_immune_agent()` 取得 Immune；缺失分别稳定拒绝为 `HOST_TRIBE_IMMUNE_AGENT_NOT_INJECTED`、`HOST_INPUT_IMMUNE_AGENT_NOT_INJECTED`，无 singleton fallback。后续 TRACE-EXEC-08AC 令这些 ports 绑定 exact Host-owned catalog。普通检查器故障仍为 `IMMUNE_EVALUATE_DEGRADED`；模型调用保持 `defer_effects_to_task_runtime=true`。补充边界集合为 `141 passed in 9.66s`，与既有 `131 passed in 7.25s`、旧免疫 `43 passed in 7.75s`、#103+#104 的 `196 passed in 21.08s` 均重叠，不得相加 | `LOCAL_TARGETED_PASS / HOST_AGENT_IDENTITY_APP_LOCAL_BY_08AC / CHAT_ROUTE_PRODUCTION_RECEIPT_MISSING / NE01_IN_PROGRESS`；未证明并发 multi-Host、generic Delegate、七路线提交绑定验收、波次集成或 NE-07 服务栈 | locator 已由 08AA 物理退役；继续 TaskGraph 多节点、Chat 真实 acceptance receipt 与 generic Delegate 切流 |
| TRACE-EXEC-08A | FR-ARCH-001、FR-NLE-005 | HardState 必须 app-local、线性化且不以环境变量作为运行期状态总线 | `app/host_hard_state.py`、`HostLifecycleContainer`、Chat/Tribe/Lifecycle/Scheduler ports、runtime snapshot、Obs Life Body projection | `CoreSettings.sys_mode`仅bootstrap且非法值以maintenance失败关闭；immutable state原子替换且无递归锁获取，Endocrine resolver在锁外；运行期env读/写=0；process `get_hard_state`/`set_sys_mode`/`get_endocrine_state`、slot/lock、6个目标binding与Limbic/Prefrontal伪写均为0。集中定向210、D7b 7、Operator/focus/runtime projection 29独立通过且不得相加；宽Obs批次超时不计；fresh static=`PASS`、`failed_gates=[]` | `HISTORICAL_APP_LOCAL_SLICE / DURABLE_OWNER_AND_RESTART_RECOVERY_WAS_MISSING / NE01_IN_PROGRESS`；该提交的wave integration=`NOT_RUN_PER_WAVE_POLICY`；`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA` | durable mode缺口由 TRACE-EXEC-08B 后续关闭；当时的 process-global Endocrine 缺口已由 08AC/08AD/08AE 的 Host-owned catalog、execution context 与 fallback 退役关闭，事件缓冲持久化仍未完成 |
| TRACE-EXEC-08B | FR-ARCH-001、FR-NLE-005 | Host mode mutation 必须以 runtime-fenced append-only journal 和 canonical head 原子提交，并可在重启/换 fence 后恢复 | neutral `contracts/host_hard_state.py`；0115；`persistence/hard_state_journal.py`；`HostHardStateOwner`；ASGI `bind_host_hard_state_runtime()` | `T-NE01-HARD-STATE-JOURNAL-02`：`HOST_SYS_MODES`/`HOST_MODE_TRANSITIONS`由neutral contract冻结且persistence→application=0；production pre-bind mutation fail closed；event/head同一UoW；expected revision/mode CAS；scope/owner/lease/fence/expiry双层复验；完整revision/digest/from-to/head分页replay；内部exact replay/drift、双repository竞争、released fence、tamper、event/head fault rollback、1001 transitions与restart恢复；另含historical receipt、owner drift、no-fake NO_CHANGE、request-id与当时 blocked Operator adapter contract。成功transition仅以journal作为canonical audit，exact replay不追加进程内history/legacy `error_log`。权威consolidated为单一批次`84 passed in 66.37s`；旧83与82只作被取代历史，禁止叠加；full architecture static=`PASS`、`failed_gates=[]` | `DURABLE_MODE_AUTHORITY_WIRED / HISTORICAL_OPERATOR_ADAPTER_BLOCK_REMOVED_BY_TRACE_EXEC_08C / AUTO_RULES_LAST_INTERACTION_VOLATILE / CANARY_NOT_RUN / NE01_IN_PROGRESS`；HardState migration=0115，仓库当前 head=0119；wave integration=`NOT_RUN_PER_WAVE_POLICY`；`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA` | 持久化或显式降级auto-rules/last-interaction；运行production canary与NE-01 wave integration；继续清零locator |
| TRACE-EXEC-08C | FR-ARCH-003、FR-NLE-002、FR-NLE-003、FR-NLE-005 | `sys_mode.switch` 必须从 Secure Ingress 经 immutable TaskGraph、Review、Acceptance Effect 到 runtime-fenced HardState，且外层命令只在可归因终态后封口 | `app/governed_operator_runtime.py`、`app/observability_runtime/review_wait_recovery.py`、`composition/runtime_group.py`、`task_runtime/acceptance_effect_dispatcher.py`、`app/acceptance_effect_handlers.py`、Obs outcome WAL v2、ASGI lifecycle | typed action/HostCommand；task-bound一次性Review continuation；单一app-local Effect owner；`APPLIED/REPLAYED/NO_CHANGE`状态专用且runtime-scope-bound回执；前两者重开exact event、后者证明无event；永久event ID replay覆盖1001+后续事件；Review→outer WAL append故障在关闭admission后覆盖0条、多条及`PENDING/APPROVED/CONSUMED/REJECTED/EXPIRED/CANCELLED`；post-HardState响应丢失、`success=False`或归因失败先按event-id判定replay/unknown/权威absent；`RECOVERY_PENDING`不封outer claim；`CANCELLED`重开exact cancellation authority；启动残留PENDING/CLAIMED失败关闭；live关停失败保留依赖与refs。最终受影响集中回归`265 passed in 179.82s`；首轮`264 passed + 1 stale fixture failed`如实保留且不得与终轮或HardState历史批次相加 | `OPERATOR_2_OF_37 / TARGETED_SLICE_PASSED / COMMIT_BOUND_ACCEPTANCE_MISSING / CANARY_NOT_RUN / NE01_IN_PROGRESS`；其余35命令阻断；wave integration=`NOT_RUN_PER_WAVE_POLICY`；`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA` | 形成commit-bound本地证据；迁移其余Operator并运行wave integration/canary |
| TRACE-EXEC-08D | FR-ARCH-003、FR-NLE-002、FR-NLE-003、FR-NLE-005 | Scheduler worker 不得在 Graph Acceptance 前执行领域 handler；DUE→OCCURRENCE ownership 不得出现可被第二实例抢占的空窗 | `scheduler/operation_plan.py`、`scheduler/accepted_operation.py`、`scheduler/clock.py`、`persistence/scheduler_repository.py`、0117、`AcceptanceEffectDispatcher`、`SchedulerExecutionResultRepository` | immutable plan→Acceptance Effect→actual result authority；DUE owner/fence 在同一 UoW 转为 occurrence claim，dispatch 前 renew、执行中 heartbeat，expired/stale owner拒绝，actual failure time backoff。慢执行跨 1s lease 后第二实例 dispatch=0；Clock 定向 19 项、47.53 秒 | `SCHEDULER_DURABLE_CLOCK_OCCURRENCE_FENCE / ROUTE_INCOMPLETE / NE01_IN_PROGRESS`；当时的 definition/admin direct authority 缺口由 TRACE-EXEC-04V 后续关闭；mutable batch freezing、通用 unknown-effect 人工归因、wave integration与canary仍缺；`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA` | 完成 Scheduler 余下恢复/批次缺口；运行波次集成和生产 canary |
| TRACE-EXEC-08E | FR-ARCH-003、FR-NLE-003、FR-NLE-005 | Effect response-loss 按领域 authority 对账；恢复故障进入 Guardian且不泄露正文 | handlers、dispatcher、0118、Preference repository、Gateway reply query、observer、Guardian | visible/Preference/Life独立post-Acceptance effect；store gate缺失/非bool失败关闭零写，配置false/malformed env→bool false→`DISABLED`零event/profile；0118同UoW event/child profile/receipt，sealed-child+full-set tamper check；admitted-time+exact conversation/session canonical delivery digest；Gateway同SQLite payload校验effect_id，`NOT_FOUND`仍`UNKNOWN`。当前Conversation主定向15、受影响52、Gateway主批31、独立review3，各批可能重叠不得相加 | `PARTIAL / CONVERSATION_EFFECTS_EXACT / AUTHENTICATED_SURFACE_QUERY_WIRED / NE01_IN_PROGRESS`；一般unknown-effect人工归因、commit-bound evidence、wave integration/canary仍缺；`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA` | 运行NE-01波次集成、真实模型与production canary；不要把局部Effect authority外推为七路线完成 |
| TRACE-EXEC-08F | FR-ARCH-003、FR-NLE-003 | Internal raw artifact受保护；canonical result只能在Acceptance后写；任何 destructive cleanup 必须晚于精确 durable claim | `periodic_durable_continuation.py`、checkpoint UoW、`operation_checkpoint_budget_recovery.py`、`internal_precheckpoint_cancellation_claim_result.py`、`internal_raw_failure_claim_result.py`、`internal_artifact_terminal_reconciliation.py`、TaskStore、0116 | sealed checkpoint、A/B/C 和两类 signed cancellation authority 保持。新增 raw-failure 路径只重开 TRACE-EXEC-04R 的六聚合终态，生成独立 deterministic result；HostCommand replay从同一验签 Store 重算，terminal worker 先完成 claim 再 cleanup/discard，响应丢失不重跑 Agent，claim outage或tamper保持现场 | `PARTIAL / INTERNAL_WRITE_PROMOTION_AND_SIGNED_RAW_FAILURE_CLAIM_BEFORE_CLEANUP / NE01_IN_PROGRESS`；一般 unknown effect与其它 failure/cancel/budget claim closeout未完成；多节点/批次只有 bounded 证据；Domain/Skill/Codex write、handle-level TOCTOU、OS/container isolation与七路线receipt仍缺；`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA` | 完成剩余一般恢复缺口，再推进跨路线write与七路线验收 |
| TRACE-EXEC-08G | FR-ARCH-001、FR-ARCH-003 | 大型 owner 拆分不得产生第二 writer、lifespan/task owner 或 repository UoW；静态通过不得越级为架构完成 | Acceptance owner+common+Surface+sys-mode/general-Effect handler；general Effect proof/evidence Store mixin；Runtime service/dispatcher/port；ASGI+runtime-context wiring；Scheduler repository+retry store+primitives；Budget rejection writer+read resolver+route helper | Acceptance 当前966行、sys-mode handler368行；general Effect persistence 1426+716行、Runtime 793+88+34行；ASGI/Scheduler 1389/2942行。独立 Gate 1–14=`PASS`、failed=[]、各 Gate issues=[]；Gate 3 `new_mixed_over_800=[]`、allowlist 未扩。所有新 Store mixin 复用 canonical connection/UoW；967行Budget Store只拥有受约束的C-boundary mutation；444行只读resolver与93行纯route helper不拥有第二writer；上一结构提交及265项只作历史 | `STATIC_PASS / NE01_PARTIAL_IN_PROGRESS`；旧 authority、七路线、wave integration/canary仍缺；`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA` | 继续 authority 清零、波次集成与真实 canary |
| TRACE-EXEC-08H | FR-ARCH-003、FR-NLE-002、FR-NLE-003、FR-NLE-005 | 签名 `task_terminal.reconcile` 必须隔离敏感证明，并经 TaskGraph、Review、Preview 与 Acceptance 才可调用 0099 authority | 0119、`terminal_operator_command_store.py`、`governed_terminal_reconciliation_runtime.py`、`accepted_terminal_reconciliation_handler.py`、Acceptance compiler/dispatcher | Secure Ingress 验签后把 exact command append 到 0119；通用 Graph 仅携带 ref/digest；风险4触发 durable Review；Effect 执行点重开 Graph/Attempt/Grant/Scene/Guardian Preview，再重开 0119 命令。真实 SQLite 集成覆盖 Review 前零 proof/Budget mutation、Review 后 exact proof/Budget release、response loss/exact replay、一次性 renewed grant 与签名材料不进入 WorkOrder | `OPERATOR_3_OF_37 / LOCAL_GOVERNED_SQLITE_INTEGRATION_PASSED / DEFAULT_OFF / HTTP_NETWORK_CANARY_NOT_RUN / NE01_IN_PROGRESS`；其余34命令阻断；`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA` | 生成commit-bound证据；运行HTTP/network canary与NE-01波次集成 |
| TRACE-EXEC-09 | FR-ARCH-001、FR-ARCH-010 | Agent/Verification 路径不得逃逸 workspace | canonical resolver + scoped tool/verification wiring | resolver/tool/verification unit + contract | `D0_WIRED / OWNERSHIP_INCOMPLETE` | NE-01 |
| TRACE-EXEC-09A | FR-ARCH-003、FR-AGENT-RUNTIME | ReAct Skill/Delegate 不得在当前节点内绕过 TaskGraph 直执行 | ReAct registry/loop、Skill executor、Delegate dispatcher | `run_skill` / `dispatch_sub_agent` discovery 为 0；注入 legacy ToolSpec 在低层执行前固定拒绝；其他 Agent Tool authority 不在该结论内 | `RETIRED_FAIL_CLOSED / POSITIVE_PATH_MISSING` | NE-01 新 graph revision 节点 |
| TRACE-EXEC-09B | FR-ARCH-003、FR-AGENT-RUNTIME | Visual Agent HTTP 不得持有 route-local 模型/ClawdBot/Edge 执行 authority | `/v1/agent/run` compatibility route、vision chat format fence、legacy Action Planner seal | 固定 SSE 拒绝、raw loop/client/Ledger writer 退役、非 text 在模型/Memory/Ledger 前拒绝；不等于 Delegate `vision_agent` 或正向 Visual executor | `RETIRED_COMPATIBILITY_ONLY / PRODUCTION_OFF` | NE-01 governed Visual TaskGraph revision |

TRACE-EXEC-08J（Tribe runtime scope，历史）：该提交的单活 binding fence 继续成立；现行 locator 物理库存见 08AA，ResourceManager status 现行边界见 08AF。外部 Chroma/Neo4j namespace、其它外部资源恢复与 agent/bus/state registry 仍有 compatibility boundary，故 Host-root owner identity 隔离不代表完整 multi-app isolation、generic Delegate、七路线 receipt 或 NE-01 完成。

TRACE-EXEC-08K（Production authority ledger injection）：`build_durable_production_authority_verifier()` 必须只接受由 Tool runtime activation 或恢复启动路径传入的 ledger provider，并以该 provider 重开 durable command、Attempt、Node、Lease 和 Governor grant；模块内不得 import `process_services`、`get_process_service_container` 或在 worker 执行期解析 ledger。缺失、内存路径或读取异常必须返回稳定的 fail-closed reason。该切片不完成 Host/Worker composition、generic Delegate、七路线 acceptance 或 NE-01。

TRACE-EXEC-08L（Internal Security Immune route port）：`create_internal_security_immune_router(HostRoutePorts)` 必须由 Host ASGI router registry 构造，且只通过 `security_immune_agent_provider` 使用该 app 的 agent；route module 不得导入 `process_services` 或 `get_security_immune_agent()`。provider 缺失、异常、空值、方法缺失和非法结果必须返回稳定 `503`。Obs 的 `security.hunt.run_once` adapter 可以复用同一 route port，但在缺少 exact Preview、TaskGraph、Review、Acceptance、Effect 与 Receipt 前仍必须在阻断集合中失败关闭。Security route、Obs domain port、ASGI composition、route injection 与 locator inventory 的最终定向回归为 `101 passed in 8.67s`；这不是波次 integration、真实 canary 或 NE-07 Docker 证据。本切片只消除一个生产 HTTP 入口的隐式 authority，不启用 Operator 命令、不证明 Security Immune 执行成功，也不完成 NE-01。

TRACE-EXEC-08M（Host route locator cutover）：首批 Host route 切流的提交绑定 inventory 为 `named14/39、all-form17`，定向集合 `129 passed`；它现在只作为历史阶段证据。

TRACE-EXEC-08N（Context/Runtime locator cutover，提交绑定历史）：`ConversationContextReadPorts` 必须同 scope 绑定 history、turn、topic 与 tactic readers；Bond queue、StateArbiter、LifeMemory/Episodic、Multimodal Attempt 与 heartbeat Edge probe 必须由 composition 注入。缺端口稳定失败关闭/降级，不能回读 `process_services`。无调用者的 Surface capability/reflection fallback 必须删除。该切片冻结 inventory=`named3/15、all-form6、Container48/2/45`，当时剩余业务 consumer 精确等于 RouteCatalog、startup、validation 三项；最终文件不重叠定向集合为 `184 + 33 = 217 passed`，前组有 4 条既存 warning。扩大集合首跑的 5 个旧夹具失败已保留并修正。

TRACE-EXEC-08O（Business locator zero，提交绑定历史）：RouteCatalog 的 Host/Surface/Executive/Subagent resolver 必须接收显式 catalog；AgentRuntime 与 SurfaceRuntime 绑定同一 resolver，缺 Surface ports 时冻结 Chat 在执行前失败关闭。startup 只消费 `StartupApplicationPorts`，Edge endpoint 解析为纯配置；validation harness 只使用构造期 Life reader。该切片冻结 inventory=`named0/0、all-form3、Container48/2/45`，业务 consumer 精确为 0，后三项只在 composition；最终文件不重叠定向集合为 `191 + 76 = 267 passed`。ASGI/合同旧夹具首轮 `71 passed + 2 failed`、新增 startup 身份测试首轮 `73 passed + 1 test-fixture assertion failure` 均保留，修正后精确复跑及完整组通过；纯 Edge endpoint 配置与最后两项 composition/Container 库存都有直接测试。完整静态 Gate 仍为基线 `FAIL [1,3]`，当时 Container 45 个可变引用，compatibility facade、wave integration、canary 与 NE-07 Docker 尚未完成，故状态为 `BUSINESS_LOCATOR_IMPORTS_ZERO / NE01_IN_PROGRESS`。

TRACE-EXEC-08P（Dead process singleton retirement，提交绑定历史）：全仓生产调用清点必须证明 Bond style proxy、ReflectionChain、PredictiveStateEngine、PlanningAgent 与 RuntimeCapabilityRegistry 的 process getter 只有定义/再导出而没有消费者，才允许删除 getter、proxy 与 Container slot；领域类型本身不得随 locator 删除。合同同时冻结五个 slot/getter 不得重新出现，该提交 inventory=`named0/0、all-form3、Container43/2/40`。扩大 Composition/ASGI 定向集合最终为 `100 passed / 8 existing warning emissions`；首跑 `96 passed + 4 failed` 均是旧 MCP 夹具访问早已退役的 Obs slot，改用 `clear_service_references()` 后精确复跑 `5 passed` 并完整组通过。文档/authority/freshness/metadata/coverage 为 `39 passed`，doc gate、compile/JSON/diff 通过；统一 static scan 仍是两条既有 NLE boundary，完整 Gate 仍为基线 `FAIL [1,3]`，本切片新增失败为 0。该提交只把死的进程 authority 减少五项。

TRACE-EXEC-08Q（Internal sidecar ownership，提交绑定历史）：`GovernorPolicyCache` 只能随 composed `IntentDecisionEngine` 构造并由 engine 持有；Forge `TokenService` 只能随 `CapabilityForgeService` 构造并由 service 持有。`policy_cache`、`capability_forge_token_service` 两个 Container slot 及 `get_policy_cache`、`get_capability_forge_token_service` getter/宽 facade 必须保持为 0。该提交 inventory=`named0/0、all-form3、Container41/2/38`；最终受影响定向集合 `99 passed / 8 existing warning emissions`，文档 authority/freshness/metadata/coverage 合同 `45 passed`，doc gate、compile/JSON/diff 通过。扩大首跑的 `135 passed + 3 failed` 均为相邻既有边界：两项旧 SkillLoader 绕过期望被现行治理门禁拒绝，一项调用已不存在的 Obs POST route；它们未经过该切片组合路径且未被篡改。统一 static scan 的两条既有 NLE boundary 与完整 Gate 基线 `FAIL [1,3]` 均保留，该切片新增失败为 0。

TRACE-EXEC-08R（Host lightweight state ownership，提交绑定历史）：`EmotionalSilenceEngine`、`MilestoneTracker`、`MultimodalProcessor`、`TtsContextRouter`、`MetaLearningLoop` 与 `MemoryWorthEvaluator` 必须由每个生产 Host port root 独立持有；同一 root 的 provider 返回精确同一实例，两个 root 不得共享。对应六个 Container slot、getter 与宽 facade 再导出保持为 0；输入测试不得再 patch 已不存在的 process getter，而必须显式注入 Input ports。该提交 inventory=`named0/0、all-form3、Container35/2/32`，受影响定向集合 `133 passed / 8 existing warning emissions`，文档合同 `45 passed`，doc gate、compile/JSON/diff 通过；统一 static scan 的两条既有 NLE boundary 与完整 Gate 基线 `FAIL [1,3]` 原样保留，本切片新增静态失败为 0。

TRACE-EXEC-08S（Host security/evolution state ownership，提交绑定历史）：`SecurityImmuneAgent`、`CapabilityGapTelemetry` 与 `CapabilityForgeService` 必须由每个生产 Host port root 的线程安全惰性 provider 独立持有；同 root 多次解析必须复用 exact instance，两个 root 不得共享，且 Forge 的 `_telemetry` 必须是同 root Telemetry provider 的 exact instance。三个 Container slot、process getter 与宽 facade 再导出必须为 0；`security_immune.agent` 的模块级 getter/backing singleton 也必须不存在。该提交 inventory=`named0/0、all-form3、Container32/2/29`，受影响定向集合 `153 passed / 8 existing warning emissions`，文档合同 `45 passed`，doc gate、compile/JSON/diff 通过；统一 static scan 的两条既有 NLE boundary 与完整 Gate 基线 `FAIL [1,3]` 原样保留，本切片新增静态失败为 0。当时其余 29 个可变引用、compatibility facade、共享实例、波次 integration、canary 与 NE-07 Docker 均未完成，状态继续 `NE01_IN_PROGRESS`。

TRACE-EXEC-08T（Host conversation context wrapper ownership，提交绑定历史）：`ContextRepository`、`ConversationHistory`、`SessionTopicLedgerStore` 与 `SessionTacticLedgerStore` 必须由每个生产 Host port root 的线程安全惰性 provider 独立持有；同 root 多次解析复用 exact instance，两个 root 不得共享，且 History 的 repository 必须是同 root ContextRepository provider 的 exact instance。四个 Container slot、process getter 与宽 facade 再导出必须为 0。该提交 inventory=`named0/0、all-form3、Container28/2/25`，受影响定向集合 `91 passed / 8 existing warning emissions`。canonical ledger 仍由显式 transitional provider 共享；因此本切片不证明 ledger 隔离、事务化 Chat commit、当时其余 25 个可变引用、波次 integration、canary、NE-07 Docker 或 NE-01 完成。

TRACE-EXEC-08U（Host multimodal/shared-memory wrapper ownership，提交绑定历史）：`MultimodalObserver` 与 `SharedMemoryIndex` 必须由每个生产 Host port root 的线程安全惰性 provider 独立持有；同 root 多次解析复用 exact instance，两个 root 不得共享。两个 Container slot、process getter 与宽 facade 再导出必须为 0，输入路由测试只能通过 typed ports 注入 observer，不得 patch 已退役 getter。`SharedMemoryIndex` 必须显式消费 composition 提供的 EpisodicStore，`MultimodalObserver` 必须显式消费 composition 提供的 ModelAttempt service；这两个 backing authority 当前仍是共享 transitional dependency。该提交 inventory=`named0/0、all-form3、Container26/2/23`，受影响定向集合 `132 passed / 8 existing warning emissions`。因此该历史切片只证明包装器 identity 和依赖边界。

TRACE-EXEC-08V（Host core runtime/publication ownership，提交绑定历史）：`GraphRuntimeContract`、`RoutingMatrix` 与同步 `BondDynamicsWriter` wrapper 必须由每个生产 Host port root 的线程安全惰性 provider 独立持有；同 root 多次解析复用 exact instance，两个 root 不得共享。Memory service 必须经 app-local `HostMemoryServicePublication` exact identity publish/require/withdraw；重叠发布、stale withdraw 与未发布读取失败关闭。四个 Container slot、五个 getter、一个 setter 与宽 facade 再导出必须为 0。Graph runtime 绑定显式 ledger，Routing matrix 绑定 Host 审计 writer，Bond writer 在该提交仍绑定显式 transitional stage engine/Neo4j resolver。ASGI 的四个 Life/Ingress adapter 被拆入聚焦模块，该提交主文件 `1392 < 1400`，门槛未放宽。该提交 inventory=`named0/0、all-form3、Container22/2/19`；去重文件清单回归为 `218 passed / 8 existing warning emissions in 41.15s`。这只证明三个 wrapper 与一个 publication owner 的 identity/lifecycle，现行值和 Bond runtime 后续所有权见 08W。

TRACE-EXEC-08W（Host Bond runtime epoch ownership，提交绑定历史）：每个生产 Host port root 必须构造一个 `HostBondRuntimeOwner`；它在一次 active epoch 内冻结配置并惰性复用 exact `BondStageEngine`、`BondDynamicsWriter`、`BondUpdateQueue` 与 Bond client，两个 Host root 不得共享这些对象。shutdown 必须先 drain/stop queue、后关闭 client；queue 关闭后 enqueue 返回 `BOND_QUEUE_CLOSED`，任何关闭失败保留 exact owner 并以 `HOST_BOND_RUNTIME_CLOSE_RETRY_REQUIRED` 阻止新 epoch，成功关闭后才可开始下一 epoch。`bond_stage_engine`、`bond_update_queue` 两个 Container slot，`get_bond_stage_engine()`、`get_bond_update_queue()`、`get_bond_neo4j_client()` 三个 accessor 与宽 facade 再导出必须为 0。该提交 inventory=`named0/0、all-form3、Container20/2/17`，扩展定向回归为 `140 passed / 8 existing warning emissions in 40.02s`。完整 Host integration 没有形成有效 PASS；该切片不提供 queue crash recovery、外部 Neo4j namespace isolation、事务化 Chat commit、canary、NE-07 Docker 或 NE-01 完成。现行值见 08X。

TRACE-EXEC-08X（Host memory/state runtime ownership，提交绑定历史）：每个生产 Host port root 必须构造一个 `HostMemoryRuntimeOwner`，惰性持有 exact `MemoryGuard`、`StateArbiter`、`StateWriteArbiter`、`DistillationEngine`、`SemanticRecaller` 与 `EmotionTimeline`；同 root 重复解析必须复用 exact instance，两个 Host root 不得共享。六个 Container slot、六个 accessor 与宽 facade 再导出必须为 0。该提交 inventory=`named0/0、all-form2、Container14/2/11`；定向回归 `120 passed / 8 existing warning emissions in 32.78s`。canonical backing、外部 client recovery 与 NE-01 当时均未完成。

TRACE-EXEC-08Y（Host governance runtime ownership，提交绑定历史）：每个生产 Host port root 构造一个 `HostGovernanceRuntimeOwner`；`begin()` 冻结 Skill provider flags、MCP bridge 与 canonical DB path，同一 epoch 精确复用 Token、Intent、Skill、Scorecard、MCP 与 Tool discovery。Intent/Scorecard 捕获 exact 依赖，Capability maturity 默认开启；MCP close 失败围栏新 epoch。六个 Container slot/accessor、宽 facade 再导出和 `process_runtime_services.py` 已删除。该切片的 `202 passed / 12 existing warning emissions` 及 `all-form1` 只作提交绑定历史，现行值见 08AA。

TRACE-EXEC-08Z（Host authority runtime ownership，提交绑定历史）：ASGI 显式传入 `ProcessApplicationPorts`，每个 Host root 构造 `HostAuthorityRuntimeOwner`；migration-first、exact identity、启动与关闭次序继续成立。该提交冻结的 Container8/2/5 已被 08AA 取代，其回归数字不得搬作当前 locator 结论。

TRACE-EXEC-08AA（生产 Service Locator 物理退役）：`process_service_container.py`、`process_services.py`、`process_environment.py` 与旧 `process_service_wiring.py` 必须物理不存在；`ProcessApplicationPorts` 只保留 frozen contract。ASGI/CLI fresh 构造并显式传入 Host root，`build_production_host_application_ports()` 缺 ports 时失败且不得 require 全局状态。生产 consumer/import/module/field/lock/ref=`0/0/0/0/0/0`；`tests/support/process_service_compat.py` 仅由 pytest 安装，fresh 生产 ASGI 导入后 retired module 名不得出现在 `sys.modules`。结构/启动/路由 `27 passed`，Host lifecycle/Authority `44 passed`，Obs 两组各 `16 passed`，测试桥核心 `105 passed`，受影响集成 `1 + 5 passed`，批次不相加；最终精选结构/owner 复核 `59 passed`，文档/机器状态两组分别 `35 passed` 与 `40 passed`。一次 103 项合并批次虽到达 100%，但外层 timeout=124，明确不计 clean PASS。完整架构 Gate 保持基线 `FAIL [1,3]`，Gate 2/11 通过，本切片新增失败为 0；wave integration、canary、NE-07 Docker 与远程 CI 均未运行或为 `NOT_RUN_ACCOUNT_QUOTA`。该项只关闭生产 locator；ResourceManager status 的后续关闭证据见 08AF，外部 namespace、其它外部资源恢复、事务化 Chat、generic Delegate、七路线和 NE-01 仍未完成。

TRACE-EXEC-08AB（Tribe 共享运行期 Host 所有权，历史切片）：production `TribeRuntimeScope` 必须 fresh 构造并持有 `TribeStateRegistry`、`HostTribeRegistry` 与 `InMemoryTribeEventBus`，lifespan 通过 exact owner `TribeRuntimeContext` 向 legacy getter 发布借用视图；active Host 内三类 getter 必须解析到 scope exact objects，setter、第二 scope、错误 owner/context、provider failure 与 binding drift 必须失败关闭。Agent 的 application ports 和已存在 state registry 在 bind/unbind 间切换并精确恢复；启动、订阅、内部路由与 shutdown 必须使用 scope bus/registry，`http_hooks.py` 不得恢复全局 setter，shutdown adapter 不得回读 route module。该提交当时的 Agent instances 仍由 process-bound compatibility provider 提供；此结论已被 TRACE-EXEC-08AC 取代。其最终集中定向结果为 `101 passed in 14.75s`，文档/机器状态/仓库 Gate 为 `71 passed in 52.15s`，Ruff=`NOT_RUN_TOOL_UNAVAILABLE`。wave integration、canary、NE-07 Docker 与远程 CI 仍未运行或为 `NOT_RUN_ACCOUNT_QUOTA`，该 trace 不关闭 generic Delegate、七路线或 NE-01。

TRACE-EXEC-08AC（Tribe Agent catalog Host 所有权，历史切片）：production `build_production_tribe_agent_providers()` 必须登记固定 23 个 Agent constructor，禁止登记 legacy singleton getter；每个 `TribeRuntimeScope` 持有自己的 lazy `TribeAgentCatalog`。scope 绑定后，23 个 legacy getter、cross-Agent import 和显式 `TribeRuntimeAgentPorts` 必须解析 exact Host catalog，23 个 setter 必须拒绝 active Host 改写；scope 未绑定时显式 port 失败关闭，解绑后无 Host fallback identity 精确恢复。两个顺序 production scope 的全部 Agent identity 必须不同，且 stateful Agent 绑定各自 scope registry。集中受影响集合为 `103 passed in 16.19s`，宽 Agent/Tribe 集合为 `406 passed in 13.72s`，二者重叠、禁止相加；文档/机器状态集合为 `71 passed in 10.42s`，compileall 已通过。该提交当时的 process-level context 仍只允许一个 live Host，此并发边界已由 TRACE-EXEC-08AD 取代；当时仍存在的无 context fallback 已由 TRACE-EXEC-08AE 物理退役。wave integration、canary、NE-07 Docker 与远程 CI 仍未运行或为 `NOT_RUN_ACCOUNT_QUOTA`，该 trace 不关闭 generic Delegate、七路线或 NE-01。

TRACE-EXEC-08AD（Tribe execution-local context，历史并发切片）：`TribeRuntimeScope.bind_application_ports()` 只建立 Host scope 所有权，不得发布 module-global active owner；`runtime_lifespan()` 必须为启动期与派生 background execution 激活 exact context，ASGI HTTP middleware 必须为每个请求激活并在 `finally` 撤销。`runtime_context.py` 使用 `ContextVar` 保存 execution-local binding，旧 `_ACTIVE_OWNER/_ACTIVE_CONTEXT` 与全局 application-port binding fence 必须为 0。两个独立 scope 在重叠异步 execution 中必须分别解析自己的 Agent 与 registry；同 execution 切换 scope、owner/context drift、active execution 期间解绑和 scope 释放后的 inherited stale context 必须失败关闭。当前代码定向/组合集合为 `105 passed in 15.61s`，宽 Agent/Tribe 集合为 `408 passed in 12.89s`，补充 ASGI composition/scope 集合为 `80 passed in 8.24s`，三者存在重叠不得相加；运行期、ASGI 与文档末次合并批次为 `151 passed in 19.27s`。该证据关闭 compatibility context 的进程级单活约束；无上下文 fallback 的后续退役见 TRACE-EXEC-08AE。易失 EventBus、wave integration、production canary 与 NE-07 Docker 仍未完成，远程 CI=`NOT_RUN_ACCOUNT_QUOTA`。

TRACE-EXEC-08AE（Tribe 无上下文 fallback 物理退役，implementation commit `0fb96415f34f5fb2358b45cf3d458d1a9d039635`）：23 个 `*_agent.py` 必须仅保留 `require_active_tribe_agent()` getter，module backing singleton 与 `set_*_agent()` 为 0；`tribe_state.py`、`registry.py` 与 `event_bus.py` 的 `_GLOBAL_REGISTRY`、`_HOST_TRIBE_REGISTRY`、`_EVENT_BUS` 及 setter 为 0。没有 exact execution context 时，Agent/shared-runtime getter 必须统一返回 `TRIBE_RUNTIME_CONTEXT_NOT_BOUND`，不能懒建或回读测试全局。`DigitalLifeSupervisor` 必须显式构造、绑定、激活、撤销并释放自己的 Tribe scope；测试裸构造必须注入隔离 registry 或显式 fixture。`CortexIntentAnalyzer`、context collector、Prefrontal 与 ANS 已存在实例的方法必须使用注入的 `state_registry`，不能再次解析全局 helper；Obs Organ registry 只保存 23 组 Agent ID/族名。排除在 clean `5997daa2c` 基线复现的 7 个 Internal Runtime 既有失败后，Agent/Tribe 宽集合为 `659 passed, 4 skipped, 4 existing warnings in 53.83s`；包含 fallback、生命周期、Obs 投影与 harness 的集中集合为 `69 passed in 7.25s`，两者重叠不得相加。该切片不关闭易失 EventBus、完整 Host multi-app canary、波次 integration、NE-07 Docker、generic Delegate 或 NE-01；远程 CI=`NOT_RUN_ACCOUNT_QUOTA`。

TRACE-EXEC-08AF（ResourceManager durable status authority，implementation commit `e5648dc3ff6ee40946e5cd6f51d2899a45b0fde6`）：migration 0123 为每个 `runtime_scope_id` 建立唯一、连续 revision 的 `RESOURCE_MANAGER_STATUS` Runtime Event aggregate；payload 必须绑定 active Host owner/lease/fencing token、observation time、previous payload digest 与有界脱敏 status。ResourceManager 只能在 Task Runtime、Host lease/fence 与 RuntimeGuardian 后启动，关停必须先写 `stopped` revision，再关闭 Task Runtime 和释放 Host lease；append 或 loop stop 不确定时保留 exact owner/dependencies 供重试。journal 必须完整分页重放 1001+ 事件、支持 response-loss exact reopen、拒绝 released/stale fence、revision/payload/authority drift 与 append-only 篡改。Obs 只读 canonical Runtime Event head，System Skill 不再读取 `host_brain/data/runtime_resource_manager.json`；共享 JSON 的生产 reader/writer/fallback 为 0。受影响集合 `150 passed in 84.35s`，文档/机器状态合同 `62 passed in 10.35s`，compileall、JSON 与 diff-check 通过；完整静态 Gate 仍为既有 `FAIL [1,3]`，本切片新增失败为 0。该 trace 只关闭 ResourceManager status authority，不关闭其它外部资源恢复、external namespace、完整 Worker/Obs composition、波次 integration、production canary、NE-07 Docker 或 NE-01；`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。

TRACE-EXEC-08AG（Research shadow ledger responsibility split，implementation commit `0ba5e951a46deb4dc006648408e2ed3d7a37e3f2`）：`SQLiteResearchShadowLedger` 必须继续是唯一 SQLite connection、transaction 与 append-only UoW owner；canonical JSON codec 与 typed projection/error 必须是无连接、无写 authority 的 persistence helper。原 repository 公共导入、类型 identity、exact replay 与 `secrets.randbelow` 注入点保持兼容。单文件 1788 行拆为 repository 1271 行、codec 512 行、models 108 行，policy allowlist 未增加；Research ledger、paired execution 与 persistence contract（含 split authority guards）为 `18 passed in 22.38s`，文档/机器状态合同为 `62 passed`。fresh full architecture static 从 `FAIL [1,3]` 收敛为 `FAIL [1]`，Gate 3 通过，新增失败为 0。该 trace 只关闭未分类巨石 Gate，不关闭两个 SCC、Research 真实 provider/cohort/收益/promotion、NE-01 wave integration、production canary 或 NE-07；`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。

TRACE-EXEC-08AH（Runtime neutral contract layering，implementation commit `7e2473498623fc54d166a9e372b25a02cd157c4d`）：feature-canary receipt/验签/config digest 与 Host lifecycle ports/provider aliases/scope error 必须分别由 `contracts/task_runtime_feature_canary.py`、`contracts/host_lifecycle.py` 唯一拥有；旧 `task_runtime/feature_canary.py` 与 `app/host_lifecycle_composition.py` 只保留同一对象兼容导出。Persistence 与 Composition 必须直接依赖 canonical contract，禁止恢复 `persistence → task_runtime` 或 `composition → app lifecycle` 回边。identity/source-direction 与既有行为的受影响集合为 `74 passed in 21.79s`；fresh full architecture static Gate 1～14=`PASS`、`failed_gates=[]`，Gate 1 issues/SCC=`[]`，Gate 3 继续通过且未扩白名单。该 trace 只关闭静态依赖 SCC，不关闭 Universal Runtime 七路线、一般恢复、NE-01 wave integration、production canary、真实模型或 NE-07；`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。

TRACE-EXEC-08AI（Edge command process-global authority retirement，implementation commit `a836fb1fcfc3206a8c90e72b37e5c489e9c2f9e0`）：`edge_command_pusher.py` 只保留显式构造、强制 exact node-bound Preview proof 的低层 HTTP adapter；`_PUSHER/_PUSHER_LOCK/get/init/set_edge_command_pusher`、Host startup singleton 与所有生产 lookup 必须为 0。Motor 无 governed executor 固定拒绝，Limbic/Cortex 事件仅更新状态；内部状态返回 `GOVERNED_TASKGRAPH_ONLY`，旧内部 POST 与 Obs `edge.command.push` 在网络前返回 `ACTION_PREVIEW_TASK_AUTHORITY_REQUIRED`，Monitor UI 不开放直推。受影响 Python 集合为 `171 passed in 63.37s`，统一 static scan 与 fresh Gate 1～14=`PASS`、`failed_gates=[]`，Monitor UI production build 通过；npm 审计的 2 moderate/6 high 依赖漏洞保留为独立供应链缺口。该 trace 只证明旧 authority 退役与缺 proof 零 I/O；正向 Edge TaskGraph/Acceptance/Receipt/reconciliation、wave integration、production canary、真实 Edge/Gateway 与 NE-07 均未完成，NE-01 保持 `IN_PROGRESS`，`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。

TRACE-EXEC-08F 的历史分组只作为已被当前切片包含或重叠的证据，不得相加。当前检查点UoW Gate集中验证：DB路径一致性、唯一WorkerProjection SQL owner、private hook调用者、四个生产coordinator接线、A/B/C边界、A中间故障共同回滚、C内五故障点共同回滚、response-loss mutation调用数为1及exact read-only reopen。最终唯一去重的10文件批次为`130 passed in 296.69s`、0 fail/skip；首轮128+2与两项修正保留，旧87/35/3/62/46/4及上一结构切片265不得相加。NE-01波次集成、真实模型、NE-07 Docker/前后端服务栈与canary仍未运行，`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。

后续 post-COMMIT Budget settlement Gate 的最终唯一 12 文件批次为 `71 passed in 99.50s`、0 fail/skip；先前25/5/34/40/42/26/28项只作重叠历史，不得相加。该历史 Gate 覆盖 `EXACT_ACCEPTED / EXACT_REJECTED / MISSING / CONFLICT`、双重响应丢失、process-control传播、单节点higher-fence online takeover、generic/Internal原子拒绝收口、生产支持的HostCommand/TaskNode/WorkerProjection route authority、防Internal降级、response-loss只读重开、unknown/conflicting Outbox durable corruption、后续duplicate replay、terminal reconciliation、Runtime错误原因传播、多节点安全hold及选定DAG/parallel回归。首次hold不得附加Attempt/Node/Graph failure或Lease/Budget release，也不得覆盖可能已提交的usage/`EXCEEDED`。该批次当时只证明多节点安全hold；现行证据已由 TRACE-EXEC-08AK 扩展到双 checkpoint、shared-EXCEEDED missing、four-root、五节点菱形、八根节点与 0130 durable ambiguity hold，但一般恢复仍未完成。

TRACE-EXEC-04 的 lifecycle 验收还要求：activation rollback 或 `aclose` quiescence 未完成返回 `TASK_RUNTIME_LIFECYCLE_RETRY_REQUIRED`，bind/activate 必须锁死且只能 `aclose` 重试；Coordinator admission close、drain、`aclose` 任一失败均保持 Store 开放，Coordinator 与 worker 全静默后才 drain Outbox/关 infrastructure。

重放说明：#103 已合并后，#104 以最新 `dev` 重放，并把 Host composition root、Surface/Life authority 与普通 Chat 输入端口同时保留。随后 Host Input 通过 `TribeRuntimeAgentPorts` 将 Thalamus/ANS/Immune 交给普通 Chat、Gateway/ASR、startup 与 lifespan；聊天免疫门禁使用 `TribeRuntimeAgentPorts.require_immune_agent()`，输入归一化使用 `InputRuntimeApplicationPorts.require_immune_agent()`，二者均不直接读取对应 singleton getter。缺失 provider 分别稳定拒绝为 `HOST_TRIBE_IMMUNE_AGENT_NOT_INJECTED`、`HOST_INPUT_IMMUNE_AGENT_NOT_INJECTED`；普通检查器故障仍只标记 `IMMUNE_EVALUATE_DEGRADED`。补充边界集合 `141 passed in 9.66s` 与既有 `131 passed in 7.25s`、旧免疫 `43 passed in 7.75s`、#103+#104 的 `196 passed in 21.08s` 均重叠，禁止相加；production authority ledger injection 后 fresh AST 为 `named20/46、all-form23、Container48/2/45`；该结果只证明显式依赖和缺 provider fail-closed，不能把 `LOCAL_TARGETED_PASS` 写成 Chat/NE-01 production acceptance。

本轮在同一 trace 边界继续移除 Chat retrieval 的直接 locator：生产 `run_retrieval` 现在携带 Host-scoped conversation history、preference-pack builder 和绑定 ledger/Life memory/Bond Neo4j 的 recall factory；`chat_retrieval_helpers.py` 的 `process_services` import 为 0。端口与检索定向集合 `48 passed in 5.55s`，相邻 Surface/read-route 单元集合 `17 passed in 1.17s`；二者不得与上文或历史集合相加。该证据不涵盖 Chat Acceptance、外部 delivery、全局 locator 清零、波次 integration 或 NE-07。

| TRACE-EXEC-08AJ | FR-ARCH-001、FR-SURFACE、FR-NLE-003 | 情感连续状态与回复形态额度不得由进程 module-global singleton 拥有，回放不得推进 admission 状态 | `app/emotion_context.py`、`app/reply_form_decider.py`、`app/input_runtime_composition.py`、`app/tribe_composition.py`、`composition/host_application.py`、`contracts/host_lifecycle.py` | exact Host root 共享一个 `HostEmotionRuntime`，重叠 root identity/profile 隔离；Input composition 独占 `ResponseFormDecider`；纯 normalize 重放三次不改变 profile；50 个并发语音决定精确计数 50；旧 emotion/profile/decider globals 与 getters 静态库存为 0。原分支 affect/composition `81 passed`、ASGI/Input/NE-03 `119 passed`、文档/机器状态 `51 passed` 仅作历史；基于已合并 #120 的替代分支合并受影响集合 `190 passed`、文档/机器状态与交付元数据 `39 passed`，各批次不得相加；Gate 1～14 通过 | `APP_LOCAL_OWNER_WIRED / LOCAL_TARGETED_PASS / DURABILITY_CANARY_MISSING / NE01_IN_PROGRESS`；无 migration、波次 integration、真实 multi-Host server 或 NE-07 实测 | 将 Feedback/Persona 永久演化留给 NE-06；继续 NE-01 其它 authority 与恢复缺口 |
| TRACE-DL03-RELATIONSHIP-01 | FR-SURFACE、FR-CHAT、FR-PROACTIVE、FR-NLE-003 | 关系不能由 Bond 积分、模型措辞或单次道歉升级；accepted reply、aftereffect、修复与主动权限必须有同一 durable authority | `natsume_contracts/life/relationship.py`、`digital_life/relationship_lifecycle.py`、`digital_life/domain_reducer.py`、`persistence/life_command_repository.py`、`app/surface_runtime_composition.py`、`app/acceptance_effect_handlers.py`、0144 | accepted Surface/governed foreground reply 先获 Life receipt 后才开 provisional episode；下一次用户输入以 opaque digest 写 aftereffect。relation aggregate 为 workspace+subject，session 仅为事件上下文；0144 trigger 只从 Life journal 派生 head，direct mutation/identity drift fail closed。Surface 仅在可信 internal marker 下加入 boundary/repair non-manipulation constraints；Host Bond 默认禁用 score-driven auto stage。定向回归 `54 passed in 20.50s` | `DL03_CODE_COMPLETE_LOCAL / REMOTE_CI_NOT_RUN_ACCOUNT_QUOTA / LEGACY_BOND_COMPATIBILITY_REMAINS`；不证明主动生产调度、真人体验、真实模型、TTS/Avatar/Edge 或 DL-07 服务栈 | DL-04 流式文本，DL-05 ExpressionPlan，DL-06 主动陪伴与旧 writer 清零 |
| TRACE-DL04-AFFECTIVE-STREAM-01 | FR-CHAT、FR-SURFACE、FR-VOICE、FR-EMBODIMENT | 先冻结 self-affect/CompanionAct/ExpressionPlan，再受控地呈现文本；模型、外部 hint 和纯文本 fallback 不得改写七海自身情绪 | `life/affective_turn.py`、`digital_life/affective_turn_planner.py`、`surface_runtime/affective_streaming.py`、`app/surface_main_chain_adapter.py`、`surface_runtime/delivery_audit.py`、0145 | 可信 `AffectFusion` hand-off 生成 scope-bound `AffectiveTurnPlanV1`；低歧义且无 governed Budget guard 时调用真实 provider stream，稳定句先写 `surface_sentence_delivery_intent` 再 SSE；高歧义/修复回合必须输出恰好两候，并由本地 scorer 选择一条后再过非操控 Gate；0145 只派生 plan/sentence/cancel/foreground digest，不投影原文。完整受影响集合 `73 passed in 8.82s`，文档 authority gate `7 passed in 3.56s` | `DL04_LOCAL_TARGETED_PASS / REMOTE_CI_NOT_RUN_ACCOUNT_QUOTA / PROGRAM_INCOMPLETE`；受治理 Budget stream 尚无 provider-final canonical usage，连接恢复、TTS/Avatar delivery、真实 provider、远端 Edge 与完整服务栈未验证 | DL-04 closeout、DL-05、DL-07 |
| TRACE-DL05-EXPRESSION-PROTOCOL-01 | FR-SURFACE、FR-VOICE、FR-EMBODIMENT | 文本、Voice 与 Avatar 必须从同一冻结表达计划派生；Host 暂存与物理设备结果不可混淆 | `life/affective_delivery.py`、`digital_life/affective_expression_delivery.py`、`surface_runtime/delivery_audit.py`、`surface_main_chain_adapter.py`、`0146_affective_expression_delivery.sql` | 每个 native 稳定句先以相同 sync/self-affect revision 写 Voice `SPEAK` 与 Avatar `TRANSITION`，才发 SSE；结束/打断分别追加 `RELEASE`/`CANCEL`。0146 只从 immutable Life event 派生 scope-bound protocol head，不保存文本、音视频或模型推理；下一轮只从 exact scope 的 durable Host avatar head 读取 predecessor。Governed Chat 取消无条件 suppress，但在 Task Acceptance 前仍只返回 deferred effect；canonical plan 同时停用旧 life-director direct embodiment delivery。 | `DL05_LOCAL_HOST_PROTOCOL_STAGED / REMOTE_CI_NOT_RUN_ACCOUNT_QUOTA / PHYSICAL_EDGE_NOT_STARTED / PROGRAM_INCOMPLETE`；`HOST_*_STAGED` 不能当作 `EDGE_APPLIED`，真实 TTS/VTS、barge-in 物理停止、连接恢复、provider 与全服务栈仍未验证 | DL-06、DL-07 |
| TRACE-DL06-CANONICAL-PROACTIVE-01 | FR-PROACTIVE、FR-SURFACE、FR-NLE-003 | 主动联系必须由 canonical 关系许可与七海自身状态共同裁决，不得由 Bond、旧情绪投影、缓存目的地或固定模板授权；已接受 transport 必须与下一次用户后效应保持连续、可恢复且不得重发 | `contracts/life/relationship.py`、`digital_life/relationship_lifecycle.py`、`digital_life/outbox.py`、`digital_life/companion_proactive.py`、`digital_life/policy.py`、`persistence/life_command_repository.py`、`app/asgi.py`、`app/host_life_scheduler_wiring.py` | accepted foreground reply 将 `CompanionProactiveTargetV1` 与 provisional episode 写入同一 deterministic relationship command；只读 registry 只返回 explicit `ALLOWED`、无 repair/active episode、scope-valid 的 target，Scheduler 按 frozen Scene scope 和 occurrence digest 选择一个 aggregate。adapter 只接收 bounded canonical `relationship_v2`、`affective`、clock 与 admitted timezone；`UNKNOWN/DENIED`、repair、未结 episode、`GIVE_SPACE/QUIET_PRESENCE/DEFER` 与自我休息均 hold。旧 Bond/legacy affect/`last_target`/fixed template 无发送权；outbox trace 只存内容 digest，消息带无回应压力措辞；production fusion composition 不再解析 compatibility `HostEmotionRuntime`。完成的 `proactive.gateway` receipt 必须同 outbox id/key/payload/authorization digest 和 private scope-bound target 全部一致，才追加 `relationship_proactive_delivered` 与 `PROACTIVE_MESSAGE` episode；Host startup/tick 只读对账尚缺 relation event 的 completed receipt，关系回调崩溃后不重发 transport。下一真实用户输入才写 aftereffect。定向回归为 reconciliation/policy/registry/relationship/outbox `28 passed in 26.13s`、邻接 recovery/Surface/Acceptance/scheduler/fusion `91 passed in 49.91s`，集合重叠不相加 | `DL06_DELIVERY_RECEIPT_RELATIONSHIP_LOCAL_TARGETED_PASS / REMOTE_CI_NOT_RUN_ACCOUNT_QUOTA / PROGRAM_INCOMPLETE`；receipt 只表示 Host/Gateway accepted，不代表第三方平台送达或用户阅读；profile canary/rollback、旧 writer 清零、真实 Gateway/provider/Edge 均未完成 | DL-06 closeout、DL-07 |
| TRACE-DL06-EXPLICIT-PERMISSION-02 | FR-PROACTIVE、FR-NLE-003、FR-SECURITY | 主动联系许可只能来自明确、可认证且 scope-bound 的用户控制；模型、情绪、Bond、自然语言与跨用户 body 均不得替代 | `natsume_contracts/life/relationship.py`、`digital_life/companion_permission_ingress.py`、`secure_ingress/surfaces.py`、`secure_ingress/authority.py`、`app/digital_life_routes.py`、`app/asgi.py` | Host 的 `POST /v1/digital-life/relationship/proactive-permission` 仅接收 strict `CompanionProactivePermissionRequestV1`，只准 `ALLOWED/DENIED`。server-owned `host.companion-contact` policy 要求 `companion:contact:write`；验证后还必须满足 `principal_kind=user`、`principal_id=relationship_subject_id`、`tenant_id=workspace_id`。Secure Ingress audit ref 成为 `USER_EXPLICIT` evidence，claim completion 绑定 `life-command://...`；HTTP duplicate 和重启只重开同一 command，无法产生第二个 relation event。 | `LOCAL_TARGETED_PASS_47 / REMOTE_CI_NOT_RUN_ACCOUNT_QUOTA / PROGRAM_INCOMPLETE`；证据覆盖真实 SQLite migration、Host route、Secure Ingress、cross-scope/service/duplicate-JSON/UNKNOWN rejection 与 restart replay。不证明 Monitor 用户控件、第三方平台送达、真实 Gateway/provider/Edge、偏好 canary 或旧 writer 退役。 | DL-06 profile/cutover、DL-07 |
| TRACE-DL06-SIGNED-PROFILE-CONTROL-04 | FR-SURFACE、FR-VOICE、FR-EMBODIMENT、FR-PROACTIVE、FR-NLE-003、FR-SECURITY | 显式低风险表达偏好只能由关系对象经认证提出，并且只能启动可撤回 canary；请求不得自报 evidence 或修改 Soul/consent/self-affect | `contracts/life/companion_profile.py`、`digital_life/companion_profile_ingress.py`、`digital_life/companion_profile_lifecycle.py`、`secure_ingress/surfaces.py`、`app/asgi.py`、`app/digital_life_routes.py`、0148 | `POST /v1/digital-life/relationship/companion-profile` 只接收 strict `CompanionProfileControlRequestV1`。`host.companion-profile` 要求 `companion:profile:write`，并重验 `principal_kind=user`、`principal_id=relationship_subject_id`、`tenant_id=workspace_id`；Host 自行从 canonical strong aftereffect history 选证据，audit ref 进入 immutable profile version。duplicate/restart 或 claim-result 中断只重开同一 Life receipt，不能写第二个 candidate。 | `LOCAL_TARGETED_PASS_4_PLUS_ADJACENT_20_PLUS_SECURE_INGRESS_7 / REMOTE_CI_NOT_RUN_ACCOUNT_QUOTA / PROGRAM_INCOMPLETE`；覆盖真实 SQLite migration、route、scope/service/weak-history/duplicate-key 拒绝与 restart replay。只证明 CANARY start，未证明 Monitor/Review、真实 canary activation、legacy writer cutover、provider/Edge/full stack。 | DL-06 canary evidence/cutover、DL-07 |

## 4. Planning Search

| Trace | FR | 合同 | 生产落点 | 证据 | 当前状态 | 下一波 |
|---|---|---|---|---|---|---|
| TRACE-PLAN-01 | FR-PLANNING、FR-ARCH-003、FR-ARCH-010 | 公共无副作用搜索内核与 immutable compile boundary | `planning_search/`、`goal_planning.py`、`planning_graph_compiler.py`、`planning_execution.py` | GR-01、D14、D16 unit；state/context/grant lineage | `WIRED_FOUNDATION / DOMAIN_CUTOVER_INCOMPLETE` | NE-02 |
| TRACE-PLAN-02 | FR-PLANNING、FR-ARCH-003 | Host 从受约束目标生成竞争计划，并只允许显式 durable failure replan | `capability_plan_search.py`、`capability_domain.py`、`governed_task_adapter.py`、`planning_recovery.py` | T-NE02-HOST-CAPABILITY-SEARCH-01、T-NE02-OBJECTIVE-PLANNING-ADMISSION-01、T-NE02-CAPABILITY-FAILURE-REPLAN-01；真实收益仍由 T-PLAN-PROD-01 验收 | `PARTIAL_RULED_OBJECTIVE_CAPABILITY_SEARCH_V1 / EXPLICIT_DURABLE_REPLAN_ONLY`：仅 Research/Frontend rule-derived capability catalog；冻结目录中的两项不同能力在虚拟态中选成 `skill → skill → chat`，`FAILED` predecessor 才可 revision `+1`；普通目标、自动/failure-adaptive replan、跨域收益均未完成 | NE-02 |
| TRACE-PLAN-02A | FR-PLANNING、FR-ARCH-003 | 已绑定的 planning risk 只能收紧 capability candidate catalog | `capability_plan_search.py`、`governor_grant.py` | T-NE02-GOVERNOR-RISK-CEILING-01；requested=3 / Governor=1 时仅 risk=1 竞争候选进入 frozen state，缺失或畸形 binding risk 失败关闭 | `LOCAL_TARGETED_PASS / NE-02_PARTIAL`：只覆盖 Research/Frontend 窄路径；不构成通用 Plan、跨域收益或 production promotion | NE-02 |
| TRACE-PLAN-03 | FR-PENTEST | Pentest L1/L2 使用公共 service | Pentest adapter/AgentLoop | GR-01 pentest tests | `WIRED` | NE-02 |
| TRACE-PLAN-04 | FR-PENTEST、FR-PLANNING | L3 能按虚拟状态生成后继并 durable 校准 | L1 contract-deferred successor catalog、Pentest virtual world、0105 calibration | 38 项定向证明 root/successor 分离、capability 解锁和 catalog digest；30 次授权实验与 verified outcome 尚未运行 | `SHADOW_DYNAMIC_SUCCESSOR / ACCEPTANCE_PENDING` | NE-02 |
| TRACE-PLAN-05 | FR-PLANNING、FR-ARCH-010 | Research 真实收益 | synthetic fixture only | GR-02A report；T-PLAN-PROD-01 | `UNPROVEN` | NE-02 |
| TRACE-PLAN-06 | FR-PLANNING、FR-ARCH-010 | Code 动态 pure-shadow planning 与真实收益 | D16 catalog、D17～D19 benchmark/terminal、`contracts/planning_search/code_benefit_cohort*`、0106 ledger | 固定 20 项合同；同 source/model/provider/profile/budget；独立 fresh worktree/model/hidden/static execution；+10pp 或非劣 5pp 且成本 -15%；hidden/static/diff-risk 非退化；cleanup-gated PASS/FAIL terminal 均保留；定向 15 passed | `FIXED_PROTOCOL_LOCAL_PASS / REAL_COHORT_UNPROVEN`：尚无 cohort owner/migration、真实外部模型、20 项 evidence、独立部署 verifier 或 promotion Gate；同主机 D19 仍非 OS/container/network sandbox | NE-02 |

## 5. Digital Life 与长期演化

| Trace | FR | 目标合同 | 当前落点 | 当前状态 | 下一波 |
|---|---|---|---|---|---|
| TRACE-EVO-01 | FR-NLE-004 | World facts/provenance/TTL/conflict 进入 Context | 0111、`RuntimeWorldObservationApplication`、`WorldResolver` | `PARTIAL_PRODUCTION_OBSERVATION_WIRED_NOT_PREDICTIVE` | NE-03 本地 Gate |
| TRACE-EVO-02 | FR-NLE-004 | Scene aggregate/transition/策略 | 0112、`RuntimeContextApplicationService`、`SceneResolver` | `PARTIAL_THREE_CONSUMERS_PRODUCTION_WIRED` | NE-03 本地 Gate |
| TRACE-EVO-03 | FR-NLE-004 | user/task/life/relationship/authority 仲裁 | `ProductionIntentAuthorityPort`、`DurableIntentAdmissionVerifier` | `PARTIAL_PRODUCTION_ADMISSION_WIRED_RESTRICT_ONLY` | NE-03 本地 Gate；长周期 goal owner 后续 |
| TRACE-EVO-04 | FR-NLE-004 | 高风险 action 统一 preview/shadow | `ProductionActionPreviewAuthorityResolver`、Action Preview RuntimeEvent、terminal transport gates | `PARTIAL_PRODUCTION_AUTHORITY_WIRED_LOCAL_ACCEPTANCE_PASSED` | Operator Governed route 属 NE-01 |
| TRACE-EVO-05 | FR-NLE-005 | 统一 health/degradation/recovery/SLO 与 Catalog rollout | RuntimeGuardian、0112、0113、Host Service Catalog startup | `PARTIAL_HOST_LIFESPAN_AND_ENFORCE_TARGET_WIRED_LOCAL_ACCEPTANCE_PASSED` | Windows graceful stop 与部署 canary 后续 |
| TRACE-EVO-06 | FR-ARCH-010 | Scenario/Experiment Lab | 0108/0122 只读；0138 trusted ledger；0139 namespace repair；0140 sanitized real-trace dataset；专用 Docker、typed composition、query-only Obs/Monitor | `LOCAL_ACCEPTANCE_PASS / PROPOSAL_ONLY / PROGRAM_INCOMPLETE`：raw trace 不落库；唯一 fenced claim、真实容器、exact response-loss、unknown reconciliation、脱敏重启/篡改/回滚和只读 Operator 已验证；clean-commit suite 绑定 `b68a8b9dd` 与 receipt `020c8fc0…`，exact replay 不增长 Lab 表或容器 | NE-07 只验真实全栈 |
| TRACE-EVO-07 | FR-ARCH-010 | Counterfactual replay | 0138 trusted report/proposal/golden-suite + `GoldenExperimentService` + `CounterfactualPairV2` + Operator projection | `LOCAL_ACCEPTANCE_PASS / PROPOSAL_ONLY`：Research Planning、Proactive、Memory Write 六个真实隔离 arm 已形成 baseline→candidate 比较，Monitor 只读展示 diff/证据/成本/风险/Review 状态；没有 activation authority | NE-05/NE-06 消费 proposal；NE-07 实测 |
| TRACE-EVO-08 | FR-NLE-005、FR-ARCH-007 | Maturity 成为默认 restrict-only 门禁 | scorecard/provider + Governor adapter + 0141 maturity decision + lifecycle-bound Host Registry | `V2_AUTHORITY_WIRED_FAIL_CLOSED_LOCAL_WAVE_PASS`：缺失/伪造 scorecard 最高风险，新能力最高 L1/L2，active binding 超出静态上限令 Host 启动失败 | NE-07 外部 canary |
| TRACE-EVO-09 | FR-ARCH-007、FR-ARCH-010 | Skill Compiler 与 signed lifecycle | deterministic draft/bundle + inventory/CycloneDX SBOM/signature request + intake/quarantine + 0141 evidence/operation/active projection/receipt + Host resolver | `CODE_COMPLETE_LOCAL_WAVE_PASS / ACCEPTANCE_PENDING_NE07`：真实重开 0138 Lab、proposal、ReviewItem、Supply Chain/canary；同一 UoW 激活，exact replay 和 own-evidence rollback；旧 V1 mutation/hot loader/Forge runtime authority 已退役。外部 provider canary 未运行 | NE-07 实测 |
| TRACE-EVO-10 | FR-ARCH-010 | 经验只以 proposal 回流 | 0142；Task/Verification/rejection/tool、Input response feedback、Prefrontal/Scheduler adapters、`EvolutionVersionActivationService` | `CODE_COMPLETE_LOCAL_WAVE_PASS / ACCEPTANCE_PENDING_NE07`：所有入口 proposal-only，APPLIED 查询真实 Lab/Review 并切换 versioned policy；失败写 durable evidence | NE-07 真实服务栈 |
| TRACE-EVO-11 | FR-ARCH-005、FR-ARCH-010 | Knowledge provenance/version/conflict | `KnowledgeEntryV2`、`DecisionRecordV1`、0142 active projection、Host semantic recall SHADOW/CANARY/ENFORCE | `CODE_COMPLETE_LOCAL / DEFAULT_SHADOW / ACCEPTANCE_PENDING_NE07`：winner/cursor/supersedes/deprecation/dedup/conflict/TTL 完成 | NE-07 Chroma/Neo4j cutover |
| TRACE-EVO-12 | FR-NLE-005 | Identity snapshot 接 release | release gate 默认开启、0142 snapshot/active projection、`IdentityReleaseActivationService` | `CODE_COMPLETE_LOCAL / REAL_RELEASE_PAIR_PENDING_NE07` | NE-07 |
| TRACE-EVO-13 | FR-ARCH-004、FR-ARCH-010 | Identity migration/regression/rollback | `IdentityMigrationPlanV2`、真实 Lab/Review/regression resolver、0142 operation/receipt | `CODE_COMPLETE_LOCAL_WAVE_PASS / ACCEPTANCE_PENDING_NE07`：no-change、重大差异阻断、active owner switch 与 exact previous rollback 已证明 | NE-07 发布演练 |
| TRACE-EVO-14 | FR-NLE-005、FR-ARCH-010 | Persona trait 只能由 proposal→Lab→Review→版本激活写入 | legacy apply/rollback 中央拒绝；0142 migration authority 是唯一 writer；static scan 零违规 | `CODE_COMPLETE_LOCAL / DIRECT_RUNTIME_WRITER_ZERO` | NE-07 真实 persona regression |
| TRACE-EXEC-08AJ | FR-NLE-002、FR-ARCH-001 | Operation 预留后的已知中断必须立即留下 exact durable recovery fence，但 marker 不得由异常类型推断副作用 | `operation_interruption_runtime.py`、`task_worker_interruption_store.py`；真实 SQLite exact replay/漂移拒绝；后续 signed cancellation 与 raw-failure reconciliation | `KNOWN_INTERRUPTION_CLASSIFICATION_WIRED / EXACT_SIGNED_SINGLE_NODE_RAW_FAILURE_SEPARATELY_PROVEN / GENERAL_ATTRIBUTION_INCOMPLETE` | marker 只原子写 `RECOVERY_REQUIRED + Outbox`，caller cancel 继续传播；只有后续完整签名负向事实可走 TRACE-EXEC-04R。部署 key/HTTP canary、一般 unknown-effect 与多节点恢复仍待 NE-01 |
| TRACE-EXEC-08AK | FR-NLE-002、FR-ARCH-001、FR-ARCH-003 | 自动恢复只能消费 exact committed checkpoint；其它运行时不确定性必须持久登记且不得授权盲重放 | 0130 recovery hold；codec/current/support/store focused modules；existing-operation resolution；五节点菱形、八根节点、unknown/cancellation/conflict 真实 SQLite 集成 | `GENERAL_SHAPE_COMMITTED_CHECKPOINT_RECOVERY_LOCAL_PROVEN / DURABLE_UNKNOWN_CANCELLATION_CONFLICT_HOLD_LOCAL_PROVEN / GENERAL_RECOVERY_INCOMPLETE` | 37 项受影响集成通过；unknown/cancellation 仍需各自 signed reconciliation，conflict quarantine 且零 takeover Lease。继续覆盖其它 failure/cancellation、Workspace 终态、部署 fan-out、波次 integration 与真实 canary |
| TRACE-EXEC-08AL | FR-NLE-002、FR-ARCH-001、FR-ARCH-003 | 受治理写节点只能从 frozen source 在隔离 Workspace 形成密封 delta；Acceptance 必须重算确定性 accepted HEAD，且不得移动调用方 source branch | `workspace_write_promotion.py`、Workspace allocator/runtime、protected artifact store、0116 lifecycle、Internal executor/accepted-result handler | 真实 migrated SQLite + Git 验证 governed Internal write、bounded safe-path manifest、binary patch、deterministic commit、ACTIVE→ACCEPTED→PROMOTED、exact replay、源分支不变、seal 后 worktree 漂移拒绝，以及 commit 已创建但 lifecycle UoW 回滚后新 Runtime 接管并复用同一 HEAD。定向集成 `3 passed in 15.82s`；受影响集合 `78 passed, 3 skipped in 55.52s`，Windows symlink privilege 三项为既有 skip | `PARTIAL_W2_INTERNAL_WRITE_PROMOTION / CROSS_ROUTE_WRITE_INCOMPLETE`；Domain/Skill/Codex write、handle-level TOCTOU、OS/container tenant、跨路线 canary 和 NE-07 服务栈仍缺；`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA` |

## 6. 下一代执行波映射

| 执行波 | 主要 FR | 必须关闭的 Trace |
|---|---|---|
| NE-00 | 文档治理 | 当前权威链和机器状态 |
| NE-01 | FR-NLE-002～003、FR-NLE-005、FR-ARCH-001～003、FR-ARCH-010 | TRACE-EXEC-01～09B；TRACE-EVO-14 safety fence |
| NE-02 | FR-ARCH-003、FR-ARCH-010、FR-PENTEST | TRACE-PLAN-01～06 |
| NE-03 | FR-NLE-004～005、FR-ARCH-004 | TRACE-EVO-01～05 |
| NE-04 | FR-ARCH-010 | TRACE-EVO-06～07 |
| NE-05 | FR-NLE-005、FR-ARCH-007、FR-ARCH-010 | TRACE-EVO-08～09 |
| NE-06 | FR-ARCH-004、FR-ARCH-005、FR-ARCH-010、FR-NLE-005 | TRACE-EVO-10～14（含 direct writer replacement） |
| NE-07 | 全部 | T-NE-FINAL-01；所有 `BROKEN/MISSING/UNPROVEN/UNSAFE` 清零并重新验收 |

## 7. 证据边界

- `docs/45_testing/test_reports/gr06_universal_governed_runtime_live_acceptance_20260715.json`：旧提交、旧 migration head 的 Host 四路线固定验收；不得满足当前七路线 `commit_bound_acceptance`。
- `docs/45_testing/test_reports/gr02a_planning_search_shadow_benchmark_v1.json`：synthetic simulation，不是生产收益。
- `docs/50_release/core/**`：Core Infrastructure 固定提交发布证据。
- `docs/50_release/m48/**`：M48 历史快照，不能推导当前状态。
- `docs/30_devspec/evidence/m48b/**`：M48-B 固定范围证据。

新增证据必须绑定 source commit、环境、route、dataset、预算、失败尝试和原始 receipt；禁止只写总结性 PASS。

TRACE-NE01-DELEGATE-BUDGET-01（只读 Delegate 预算证明，当前切片）：生产唯一启用 target `pentest_agent` 必须登记为 `PORT_FREE_READ_ONLY`，不得取得模型、工具、嵌套派发或 Effect port。外层 Task Runtime 预扣一次 canonical Delegate dispatch；dispatcher 必须产生 digest-bound `ReadonlyDelegateUsageAttestationV1`，绑定 command、graph/revision、node、attempt、node grant、target、adapter 与 invocation，并明确证明内部 token/tool/retry/model-cost 为零。Frozen executor 只接受该证明，不再相信 legacy intent/repair Agent trace；缺失、非零、篡改或 lineage 漂移必须在 COMMIT 前失败关闭。真实 SQLite 必须同时重开 WorkerProjection 与 durable BudgetUsage 验证 attestation ref。当前定向证据为 `56 passed in 19.61s`；该 FR 的 enabled-target budget control 为 `PASS`，Delegate 路线、一般 Review/restart、commit-bound acceptance、program-wide Budget 与 NE-01 仍未完成。

TRACE-NE01-AGENT-BUDGET-01（Agent 多轮模型预算，当前切片）：Internal
`FrozenNodeInput` 与 governed Agent Skill sealed authority 必须把同一
ACTIVE Task Budget remaining 绑定到 graph/revision/node/attempt；
`AgentRuntimeExecutor` 只接受匹配其 WorkOrderV3 lineage 的 guard。
AgentExecutor 的每条 ModelAttempt trace 必须提供 fresh、完整、exact-once
Token/retry/cost measurement，下一步只可使用扣减后的 guard。read-only V2
Skill payload 由 server-side context 重建为 V3，caller `governed_lineage`
不拥有 authority。证据入口为
`tests/unit/test_ne01_governed_agent_model_budget.py`、
`tests/unit/test_ne01_governed_agent_work_order.py`、
`tests/unit/test_aw05_skill_executor_broker_cutover.py`、
`tests/integration/test_aw02_internal_agent_runtime_cutover.py` 与
`tests/integration/test_ne01_governed_agent_workspace_acceptance.py`。当前只
证明局部 budget bypass 关闭；program-wide Budget、真实 provider、波次
集成、七路线 commit-bound receipt 与 NE-07 均未完成。

TRACE-NE01-SKILL-MODEL-BUDGET-01（普通受治理 Skill 模型预算，当前切片）：
普通 Skill 只有在 Registry 明确声明 `model_budget_required` 后，才可由
governed Task executor 从当前 ACTIVE Budget 派生 typed model runtime；
未声明 Skill 不得取得裸 `ModelAttemptService`。每次模型请求必须携带 exact
graph/revision/node/attempt guard，下一次请求只能消费扣减后的额度。server
生成的 attestation 必须绑定 request、Budget、Graph revision、Node、Attempt
和非空 canonical model attempts；frozen worker 在构造 `WorkerBudgetUsage`
前重验全部 identity。缺 usage、cache、重复/incomplete measurement、越界或
lineage drift 均失败关闭。生产代码入口为
`host_brain/model_runtime/governed_skill_budget.py`、
`host_brain/app/governed_skill_execution.py`、
`host_brain/app/governed_frozen_skill_runtime.py` 与
`host_brain/skills/registry.py`；机械证据入口为
`tests/unit/test_ne01_governed_skill_model_budget.py` 及受影响
ModelAttempt/Emergent Growth/Skill Broker/frozen route 回归。当前仅
`emergent_growth_weekly` 声明该能力；真实 provider、其它模型 Skill
inventory、legacy 非 governed 调用、program-wide Budget、波次集成和 NE-07
仍未完成。

TRACE-NE01-UNKNOWN-EFFECT-PROCESS-REACQUIRE-01（当前切片）：
当进程在 Operation `RESERVED` 提交后、worker 调用前硬退出时，任何 fresh
Host 在证明前都不得执行 worker。原 Lease存活时报告 `DEFERRED`；到期后
必须持久提交 `RECOVERY_REQUIRED`、旧 Lease `EXPIRED`、Attempt
`RECOVERABLE` 和唯一 current recovery hold。只有 exact case/version-bound
Ed25519 `NO_SIDE_EFFECT` 可让同一 Attempt 取得严格更高 fence。startup 已
持久化为 `EXPIRED` 的旧 Lease仅在它仍是 Operation 精确绑定、唯一最高
fence 且没有其他 active owner 时可用。TaskStore 必须在同一 scheduler
read transaction 验签 Operation，并只输出不含签名、scope、fingerprint、
receipt 或 reconciler 的最小 execution-fence projection。签名 replacement
Lease只允许越过 Host discovery 预筛选，`existing_operation_resolution`
必须再复验当前 Runtime owner/Lease/fence。完整签名不得出现在
HostCommand、WorkOrder、Assignment、TaskGraph JSON 或通用 scheduler
snapshot。机械证据为
`tests/integration/test_ne01_host_signed_unknown_effect_process_restart.py`、
`tests/integration/test_ne01_signed_operation_reacquisition.py` 与
`tests/unit/test_ne01_host_startup_graph_continuation.py`。当前只证明
`REACQUIRE` 子集；其它 action、failure/cancellation、真实 provider 与
NE-07 未完成。

TRACE-NE01-REVIEW-POISON-01（Review continuation 队列活性，当前切片）：
迁移 0096 的有界候选扫描不得因前 500 条跨表损坏记录永久饿死健康记录。
每个损坏候选必须在自己的 writer transaction 内清除过期 claim、写入稳定
poison reason、推进 `status_version` 并设置有界退避；合法 live-Attempt
候选只跳过本轮。Store 只能输出计数、采样规模、截断、时间与 opaque digest，
Host composition 必须把该投影交给同一个 runtime-scoped Guardian，并保证
一段 active incident 只有一个 breach，replacement process 可从 durable
history 完成 resolution。证据入口为
`host_brain/persistence/review_continuation_poison.py`、
`host_brain/app/review_continuation_guardian_observer.py`、
`tests/unit/test_ne01_review_continuation_store.py`、
`tests/unit/test_ne01_review_continuation_worker.py`、
`tests/unit/test_ne01_review_runtime_group.py` 与
`tests/integration/test_ne01_review_continuation_guardian_observer.py`。
当前最终 `88 passed in 116.19s`，文档、交付元数据与 coverage 合同
`39 passed in 7.58s`，fresh 架构静态 Gate 1～14 全部通过；无 migration；独立 terminal DLQ、Operator
修复 UI、生产 canary、一般恢复与 NE-01 波次集成仍未完成。

TRACE-NE01-BUDGET-ACTION-01（Budget action fail-closed，当前切片）：
`BudgetPolicy` v1 的 `DEGRADE/REVIEW` 枚举只表示可保存的前向兼容提案，不得
被 canonical runtime 静默转换为 BLOCK。`DurableBudgetRuntime.ensure_active`
必须在任何 Store readiness、Reservation 或 Outbox 前检查显式 production
support matrix；低层 charge 必须重复门禁以防 adapter 漏检。两种未实现动作
固定返回 `GOVERNED_BUDGET_ACTION_UNSUPPORTED` 且 mutation 调用数为 0。
concurrency 以 READY claim 的 active fenced Lease + Attempt child reservation
执行和释放，不是累计 usage。证据入口为
`host_brain/task_runtime/budget_runtime.py`、
`host_brain/task_runtime/errors.py` 与
`tests/unit/test_ne01_budget_policy_runtime_boundary.py`；初始组合为
`30 passed in 0.94s`，文档/交付合同为 `79 passed in 36.98s`，changed-module
compile、JSON、diff、统一 static/doc 与架构 Gate 1～14 通过。更宽结构探针
为 `60 passed, 2 failed`，两项源文件与 `origin/dev` 完全相同，属于已合并
DAG lifecycle 调用矩阵和 terminal replay hash 的旧结构期望；本 Budget
切片不掩盖也不吸收该基线修复。预算例外、降级 replacement Graph、Review、
`graph_revision + 1`、restart 与 rollback 未完成，因此本 trace 只关闭静默
降级漏洞，NE-01 仍为 `IN_PROGRESS`。

TRACE-NE01-STRUCTURE-AUTHORITY-01（已合并 authority 结构门禁纠偏，当前切片）：
`origin/dev` 的 atomic Review denial、Plan writer acceptance/checkpoint、
共享 execution registry 与 DurableTaskStore 新 owner 不得继续被旧调用数、
AST hash、构造签名或 MRO 绝对下标误判。每项差异先追溯 #149/#167/#172 等
引入 commit 和真实行为合同；Review denial 只有在单一 settlement UoW 存在时
才删除 `transition_graph ×2 + transition_node + cancel_waiting_review_node`
期望，parallel checkpoint hash 只增加已验证 terminal kind。两处真实文件
越界通过拆出无连接 identity/facade/validator 关闭，Retry writer
827→751 行、Operation checkpoint owner 267→206 行，policy 阈值与 allowlist
均不变。最终 30 个结构文件 `120 passed in 11.09s`，相关功能/合同
`110 passed in 74.16s`，原子 Review/checkpoint 为
`39 passed in 6.56s`，文档/交付合同为 `79 passed in 42.49s`；架构 Gate
1～14 与统一 static/doc Gate 通过。证据入口为三个 runtime structure tests、
三个纯 persistence helper 及原 atomic behavior tests。该 trace 不新增生产
能力，NE-01 wave integration、canary 与 NE-07 仍未运行。
