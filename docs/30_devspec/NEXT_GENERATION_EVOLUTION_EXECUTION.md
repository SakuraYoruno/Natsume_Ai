受众：CODEX
标题：Natsume AI 下一代演进正式执行合同

# Natsume AI 下一代演进正式执行合同

版本：v2.9
日期：2026-07-18
状态：`NE-02_IN_PROGRESS / PROGRAM_INCOMPLETE`
原始审计基线：`origin/dev@a6d7ae383`，叠加仓库清理 `f0cd1dfd`；执行事实以本文所在提交的工作树为准
目标架构：[TARGET_ARCHITECTURE.md](../50_project/arch/TARGET_ARCHITECTURE.md)
当前事实：[CURRENT_CODE_ARCHITECTURE.md](../50_project/arch/CURRENT_CODE_ARCHITECTURE.md)
差距矩阵：[ARCHITECTURE_GAP_MATRIX.md](../50_project/arch/ARCHITECTURE_GAP_MATRIX.md)

本文是唯一活动执行合同。旧 M48、M49～M57、AW、GR 和 TASK-xxxx 文件均已归档，只能作为需求和证据来源，不能决定当前顺序或完成状态。

## 1. 唯一目标

在不重建第二套 Runtime、Life Core、Memory 或 Capability 平台的前提下，完成以下闭环：

1. 修复当前 governed execution 的真实断点，并删除 legacy authority。
2. 删除全局 service locator，形成可测试、可隔离的显式进程 composition。
3. 把公共 PlanningSearch 从“算法和拓扑排序”升级为能生成真实候选、可证明收益的通用规划能力。
4. 建立 World/Scene/Intent/Guardian 的统一局面与动作治理。
5. 建立无副作用 Evolution Lab、Scenario 与 Counterfactual。
6. 建立 Skill Compiler、成熟度、签名供应链、staged activation 与 rollback。
7. 建立 Experience Feedback、Knowledge OS 与 Identity Migration 的受审演化闭环。
8. 用一次最终全量与真实服务验收重新证明 Full Architecture。

## 2. 当前起点不得再误写

| 项目 | 执行起点 |
|---|---|
| Host `chat/skill/delegate/plan` | 外层 canonical runtime 已接；D9 已将受认证 Host Input/Gateway 的 principal、claim/audit、body digest 与 surface 贯通到 Governor 和 command/plan admission。D10 仅将 `pentest_agent` 切到完整 node grant/Attempt/Lease/Fence 的 effect-free inner dispatcher；其余 delegate target 在 admission 前拒绝，Host 也不生成候选计划 |
| Scheduler | `WIRED`：真实 occurrence 已贯通 `WorkOrderV3 + Budget + child grant + Review + Verification + Acceptance + Receipt` |
| Internal Agent | `WIRED`：边界 v2 已立即编译为 V3，完整 Budget、child grant、Review、Verification、Acceptance、Receipt 与 replay 已通过真实集成 |
| Durable DAG | `PARTIAL_B5C_DURABLE_CANCELLATION_SHADOW / PRODUCTION_OFF`：0102 canonical Node Acceptance 已在应用 Store 内统一 exact NODE Effect 集、immutable artifact、`ACCEPTED` 与 Attempt-bound Outbox；normal/checkpoint/Review 共用同一路径，Effect delivery 重验 exact artifact Attempt。新 admission 拒绝空 `output_contract`，startup 隔离活动 legacy 空合同/缺 artifact和缺 exact artifact 的可投递 terminal legacy Effect。应用 `transition_node(ACCEPTED)` 旁路已封口，但 pre-stage、低层 `create_graph` 与 direct SQL 兼容/物理旁路仍在。Cancellation、Operation、Effect 与 0099 均默认关闭；general/batch/pre-budget recovery、目标部署 inventory/remediation、现实 resolver、部署 key lifecycle 与真实 canary仍未完成，atomic/bounded/cancellation 均 OFF |
| Budget | Host/Scheduler/Internal 已接；8/16/32 线程与跨进程竞争只允许一个 `EXCEEDED` winner/Outbox，其余 sibling 以 `ALREADY_EXCEEDED` 引用 winner。每次 charge 的 CAS 尝试下限为 policy `concurrency + 1`，concurrency=8 时可跨过连续 8 次冲突并在第 9 次成功。concurrency 计量、DEGRADE/REVIEW 与 program-wide 策略仍未完成 |
| Review | `WIRED_TASK_BOUND / CANARY_NOT_RUN`：0096 coordination、surface continuation、PRE optional Attempt、POST no-rerun、exact Task-authority hand-off 与独立 `review:decide` 已接通；Operator 部署/真实 canary 未完成，也不代表 cancellation reconciliation 完成 |
| Persona 演化 authority | `CENTRAL_FENCE_WIRED`：legacy runtime writer 已被中央拒绝；当前只保留无副作用候选，proposal/Review/版本激活将在 NE-06 建立 |
| PlanningSearch | 公共内核存在；Host 不生成竞争方案 |
| Pentest L3 | 强制 shadow-only；0105 通用 durable calibration 可显式注入并跨重启重放，但默认兼容 ledger 仍在内存，outcome 是 `UNVERIFIED_RUNTIME_RECORD`，不能影响真实动作或生成 capability-unlocking 后继 |
| Code/Research | Code 已有动态纯 shadow catalog、`PRIVATE_BARE_WORKTREE_TRUSTED_PROCESS_ONLY` 双 worktree、0106 cleanup-gated ledger，以及 D19 预注册固定 pytest argv 的受信子进程验证；Research 的 D20a 现有公开 metadata 动态 catalog 与可信实验根/manifest 的 readonly-file loader。两者都仍没有 Research paired cohort、真实模型、独立 verifier、成本/延迟收益对照或生产 influence；D19 也没有 durable historical cohort、受控模型/工具、独立信任域 verifier、hermetic Python 或 OS/container sandbox |
| Composition | `PARTIAL_D8_DURABLE_EMBODIMENT_AUTHORITY`：D1～D7c 的 app-local routes、21个producer、Host五合同lineage、Memory/VTS冷读、Life Body冷聚合、Direct Skill依赖失败关闭及Obs app assembly继续成立。D8 为每个 Host ASGI lifespan 构造显式 `HostEmbodimentRuntime`，并保证 Task Runtime migration gate 先于 Life authority 与 Query Projection 构造；新 outbox 行绑定 canonical DB 派生的不可变 `runtime_scope_id`；同 scope 只允许一个持 durable runtime lease/fence 的进程，第二实例失败关闭，历史无 scope 行不被接管。enqueue、claim、过期、收口与对账均在自身 SQLite 写事务内复验 exact Host authority。Host 在该 authority 下持久保存精确 Edge `session/owner/lease/fence`；替代 runtime 在开放路由前必须 exact reconnect、原子轮换 Edge fence 并验证新 lease/旧 fence 加一，否则失败关闭；不同 store 不争用默认 lease。Life、reply-performance、Gateway voice 与其他已迁移路径的新命令只返回 `PENDING / DURABLY_QUEUED`；精确重复回放既有 durable 状态，只有既有 `APPLIED` receipt 是物理收口证据。Edge receipt query v2 还校验 owner/current session/lease/fence 与精确 command/key，legacy ownerless command 返回 `NOT_FOUND`；Host 对 `IN_DOUBT` 只读查询，只有 identity+receipt-digest CAS 命中的 FOUND receipt 才能收口，重复同 digest 不重复落审计，`NOT_FOUND`/查询故障保持不确定且绝不重放。非 loopback HTTP 失败关闭，远端必须 HTTPS。真实 child Edge 的 loopback TCP/HMAC/SQLite、receipt query 与重启 exact replay 已验证；该 smoke 不含真实设备，物理效果仍 `NOT_TESTED`。剩余 legacy direct adapter、全路径 runtime 注入、Host source底层authority、Chat/Delegate/Acceptance Effect共享边界、约53个非锁可变 service 引用、完整Host/Worker对象图和全局 locator 删除均未完成；health仍因8个partial和4个认证job保持DEGRADED，NE-01不得封板 |
| Evolution abilities | 多为 partial/placeholder/missing |

Memory/Distillation 的 D6 口径进一步冻结为：`COMPRESS_BEFORE_SAVE` 只证明 pre-save hook 被观测，不能记作 compression outcome；`DistillationEngine` 在 batch、single 及 item-level 写出的 terminal audit event 也不能记作完整 `LongTermMemoryDistiller` cycle。对应不可测 total/success/failed/cycle 字段必须为 `null`，只允许另列 observed event counts。

Ledger-stats 的 D6 容量合同冻结为“最新有界窗口”：每表按公开时间列和 durable 行身份倒序读取 5,001 行，只物化最新 5,000 行；`truncated` 只声明更旧尾部未物化，不再使整表永久 503。截断后只回答 `start_ms` 严格晚于最老保留时间的窗口；同毫秒边界、直接全表读取和未命中的 request ID 均以 `PROJECTION_SOURCE_TRUNCATED` 失败关闭。没有新事实的轮询复用 source cursor/digest、只刷新 freshness，immutable journal 行数保持不变；来源不可用与超保留期查询也继续失败关闭。因此这不是完整历史聚合，也不得据此宣称全量覆盖。

迁移 0103 的冲突口径同样冻结：legacy 行映射出的确定性 attempt ID 若已被不同 canonical 证据占用，整笔 `BEGIN IMMEDIATE` 必须失败并保留 `llm_node_call_log`；不得用 `INSERT OR IGNORE` 跳过冲突后删除旧表。已提交迁移的重放由旧表已删除的空兼容壳保证幂等。

ModelAttempt 非流式生产路径只投影 provider 明确回报的 token 维度，缺失维度保持不可测，只有 prompt 与 completion 都明确测得时才推导 total。总览、模型分组与时序桶都必须公开 measured-call coverage 与 lower-bound；部分覆盖的累计值不是精确总数，完全缺测为 `null`，真实零仍有测量覆盖证据。流式路径当前没有生产调用者，adapter 也未保留最终 usage，所以状态冻结为 `UNMEASURED / NOT_ENABLED`；在引入 typed final response/usage 与终态回归测试之前不得启用或计入“预算闭环完成”。

任何 PR 或报告若把上述 `PARTIAL/BROKEN/SHADOW` 写成 `DONE/PASS`，该交付自动失败。

任何生产代码或工程工具变更必须在同一变更集中更新 README、对应权威文档或机器状态；CI 的 change-contract 门禁直接检查 Git 变更集，不能再用“文档最近更新过”代替本次同步。架构语义变化还必须按 freshness policy 的 source→doc 映射更新对应事实、数据流、图和 Trace。

## 3. 执行 DAG

```text
NE-00 文档与机器事实重置
  ↓
NE-01 执行主链与显式 Composition 闭环
  ├──────────────┐
  ↓              ↓
NE-02 真实规划   NE-03 World/Scene/Intent/Guardian
  └──────┬───────┘
         ↓
NE-04 Evolution Lab / Scenario / Counterfactual
  ├──────────────┐
  ↓              ↓
NE-05 Skill      NE-06 Feedback / Knowledge / Identity
  └──────┬───────┘
         ↓
NE-07 Full Program Acceptance
```

NE-02 与 NE-03 可在 NE-01 合同冻结后并行；NE-05 与 NE-06 可在 NE-04 的实验/提案合同冻结后并行。不得为了“并行”同时修改同一 authority、migration 或 composition 文件。

## 4. Architecture Waves

### NE-00：事实重置与文档权威链

状态：`COMPLETED`

目标：删除“目标、事实、状态、历史混在一起”的文档结构。

交付：

- 当前代码事实、稳定目标、差距矩阵、唯一执行合同四层分离；
- `current_architecture_status.json` 成为机器可读当前状态；
- 旧 M48/AW/GR 文档进入 `docs/_history/`，历史 receipt 原样保留；
- indexes、PRD、DevSpec、Trace 只指向新权威链；
- 文档门禁阻止历史计划再次成为 current authority。

完成证据：权威文档链接、JSON、受众 lint、authority gate、全量 freshness、相关 contract 与全仓静态扫描均通过；旧计划和失真专项文档已归档或删除。

### NE-01：执行主链与显式 Composition 闭环

状态：`IN_PROGRESS`

当前已完成切片：

- Persona 中央写入围栏、迁移专用审计入口、无副作用候选化及 `T-PERSONA-FENCE-01`；
- Scheduler 真实 `WorkOrderV3` admission、Budget、child grant、Review、Verification、Acceptance、Receipt 与 replay；
- Internal Agent 真实 `WorkOrderV3` admission、Secure Ingress principal、Budget、`delegate:agent_runtime` child grant、PRE/POST Review、Verification、Acceptance、Receipt 与 replay；
- Durable DAG B1 safe-retry 子集：Coordinator 消费 B0，原子提交 claim 并执行到期 promotion；worker resolver 的显式 `RetryableWorkerPreparationError` 只会在 operation reservation 前进入 `schedule_retry`。retry_count BudgetUsage、Budget、Attempt、Node、Lease 与 Outbox 在同一 `BEGIN IMMEDIATE` 内提交，超限则原子阻断 graph；真实 Coordinator 已证明 attempt 1 → RETRY_PENDING → 到期 attempt 2，且 reservation 后 worker 异常绝不 retry。提交后响应丢失只在 Attempt/Node/Lease/Graph/Budget/BudgetUsage/关联 Outbox 全部精确一致时 replay，不重复扣费；`CancelledError` 原样传播。当前生产 resolver 尚无现实异常源抛该 marker，因此这是已接 contract path，不是现实故障自动分类已完成；
- pre-reservation 普通/耗尽 failure 已通过 `fail_running_attempt_bundle` 在单事务内关闭 Attempt、Node、Graph、Lease 并生成四条 attempt/evidence-bound Outbox；已有 operation reservation 时拒绝，精确重放会拒绝 fence、version、evidence 或 Outbox 漂移。Budget finalize 仍在 bundle 外；
- Task mutation 共用的 `_insert_outbox` 与 exact replay 归属 `TaskOutboxDeliveryStoreMixin`；BudgetUsage contention 与 Operation checkpoint read 分别归属 732/214 行 focused mixin。`DurableTaskStore` 仍是唯一连接/事务/UoW authority，`task_repository.py` 当前 4,358 行，低于 4,600 行 policy；
- operation reservation 后已启动 `DurableExecutionHeartbeat`：先续 Lease、再续 Reservation，流事件和最终结果均 checkpoint，stop/join 后才允许使用最新 reservation version COMMIT；续租同参响应丢失支持精确重放，失败返回 `RECOVERY_REQUIRED` 且绝不 COMMIT；
- Budget 高竞争已由 focused `TaskBudgetUsageStoreMixin` 收口：8/16/32 线程及跨进程竞争中只有首个超限 usage 能把 Reservation 推到 `EXCEEDED` 并拥有唯一 Outbox；刷新后到达的不同 sibling 只追加 rejected `ALREADY_EXCEEDED` usage，版本 before/after 不前进且引用 winner Outbox，不能成为 0090 terminal authority。0090 Budget decision 的写入、读取与 replay 均复用完整 terminal authority 校验；public reader 以显式只读事务冻结一个 SQLite snapshot，篡改 Reservation version、第二同版本 Outbox、sibling Graph/Attempt lineage或 winner 引用会 fail closed。`DurableBudgetRuntime._charge` 的尝试数为 `max(configured retries, policy concurrency + 1)`；定向测试以 concurrency=8/连续 8 次 CAS conflict证明第 9 次成功；
- 自动 Operation continuation 只开放 `SINGLE_NODE_CANONICAL_COMMITTED_CHECKPOINT_PROVEN` 窄窗：Store 必须同时证明 `COMMITTED` Reservation、唯一 `OPERATION_COMMITTED` Outbox、原 Attempt、历史 Lease/fence、hash receipt、canonical WorkerProjection 与唯一 ACTIVE graph Budget。既有 exact accepted worker usage 只读核对；stable usage 与所有 worker-kind row 完全缺失时，旧 Lease 到期后先以新 owner/fence 接管，再按 Attempt start→Reservation commit 的正持久时间上界幂等结算。并发接管、settlement 响应丢失、超限和再次重入均不重跑 worker；冲突/拒绝/漂移 usage、非法时间、pre-operation、multi-node/batch 或不确定副作用一律 fail closed，状态仍为 `GENERAL_RECOVERY_INCOMPLETE`；
- `AcceptedGraphFinalizationRuntime` 统一正常执行、在线 continuation 与 outer DAG 的 Graph completion。Node `ACCEPTED` 已落盘、Graph 仍 `ACTIVE`、Effect 尚未落盘时，重启只从 exact Operation/Attempt/Lease/Outbox 与 canonical WorkerProjection 重建 Effect，并通过原 UoW 原子提交；篡改投影不得完成 Graph，重复重放不新增 Effect，worker 不重跑；
- 0102 immutable TaskNode result artifact 与 GR06 NODE Effect 已合入 canonical `commit_node_acceptance()`：production normal、COMMITTED checkpoint continuation 与 Review approve 在一个 Store UoW 中冻结 exact Effect set、artifact、`Node ACCEPTED` 和 exact Attempt Outbox；响应丢失逐项重验且禁止 Acceptance 后补 Effect。Effect delivery 必须命中并验证同 graph/revision/node/Attempt artifact，旧成功 Attempt 不得借权。compiler 独占 dependency result ref，READY/worker 前解析同 revision 前驱并冻结输入；缺失、跨 lineage 或篡改 fail closed。新 admission 拒绝空 `output_contract`，Task Runtime startup 扫描活动 legacy 空合同和缺 artifact。应用低层 ACCEPTED writer 已封口；standalone pre-stage、legacy/internal `create_graph` 与 direct SQL 仍保留，所以数据库物理全封闭未完成；
- `GovernedTaskCoordinator` 的 `bounded_fanout_enabled` 仍默认 `false`，生产 composition 未开启。`0090` schema 在 Budget/worker 前 fail-fast；进入 B5 成功路径还要求 `0091` schema ready。Store-only `ParallelSettlementProjection` 已接管 settlement 所需的 active re-entry、rejected Budget outcome 与 outcome snapshot read/validation，`ParallelSettlementRuntime` 共享原 Store/BudgetRuntime/RuntimeOutbox 并接管 failure/review/Budget outcome resolution 与 settlement orchestration；Coordinator 直接调用该服务。live 与 crash/re-entry 的 0090 outcomes 继续从整个 admitted Graph 的 durable Node、Attempt、Reservation、BudgetUsage 重建同一 payload/ref并 exact replay，DurableTaskStore 仍是唯一 UoW mutation authority，既有 ownership/CAS、selected Budget usage、digest/Outbox/Attempt/usage fail-closed 语义不变；
- B5A 已把 `result_node_id` 与可选 `completion_policy` 加入 canonical TaskGraph/AdmittedExecution、durable identity 和 planner semantic/shadow comparison。atomic policy 必须显式绑定 sink result；legacy 多 sink 保持无 result。`PlanningGraphCompiler` 默认 legacy，只有调用方显式请求或失败重规划继承前一 revision 才生成 atomic policy；
- B5B 使用独立 `atomic_graph_success_enabled`，Host、Scheduler、Internal 三处生产 composition 均未启用。Application 在 repository admission 前校验 runtime capability、node-grant service 与 parent grant；Coordinator 直入只接受覆盖全部 TaskNode 的 exact grant map。atomic policy 即使单节点或串行执行也由 outer 成功封口；terminal-leaf 实际并行还需另开 bounded fan-out；
- 每个节点必须已有 `ACCEPTED` Node、latest `SUCCEEDED` Attempt、已提交 Reservation receipt 和 canonical worker projection。projection 会重算 digest并绑定 command/request、route/target、graph/node/attempt、receipt 与 node grant；只有显式结果节点编译整图 effect intents 和用户可见结果，Skill 也使用冻结 canonical projection；
- 全节点成功由 `complete_parallel_graph_success()` 在单一 Store UoW 中提交 `Graph COMPLETED`、共享 `Budget CONSUMED`、两条不同的 Graph/Budget Outbox、整图 durable effect intents 与迁移 0091 的 `Natsume.task_runtime.parallel_success.v1` canonical decision。任一 identity、projection、effect、Outbox 或版本漂移 fail closed；晚故障整体回滚。completed replay 只读 0091、不重编 effect；连续两次 Store 响应丢失会重读 terminal authority；
- 定向证据覆盖单节点、串行、多 terminal roots 完成顺序对抗、flag-off admission 前拒绝、缺 grant service 拒绝、projection 篡改、restart、一次/两次响应丢失与 completed replay；
- B5C 以迁移 0092 持久化 canonical cancellation intent、final decision 与 effect fence。`task_graph.cancel` 已接入 Operator Command Gateway、同源 BFF 与 Task Runtime UI，payload 精确绑定 `graph_id + graph_revision + reason_code`，权限固定为 `obs:write + task-runtime:cancel`。Gateway 生成的 server-owned authority 携带 durable audit/claim、request/principal/tenant、认证 body digest、action/scopes；同一个 `SecureIngressGraphCancellationAuthorityVerifier` 注入 Host/Internal/Scheduler，重读 committed 且尚无 terminal `claim_result_ref` 的 Secure Ingress authority 并核对 canonical body后才签发以 `audit_ref` 为 request identity 的取消 authority。Settled authority 不得作为进程内 bearer 重用，公开 duplicate 只重放原 outcome；Application 不接受调用方自报 request identity/source；
- Cancellation、Operation、Effect 与 terminal reconciliation 分别由默认 `false` 的四个 Operator 开关控制；开启 cancellation 时 Operation 与 Effect 对账仍必须同时开启。`HOST_TASK_TERMINAL_RECONCILIATION_AUTO_ENABLED` 也默认 `false`，只允许处理 0099 `EXACT_REPAIRABLE` case。任一 Operator 入口启用都要求 Secure Ingress=`ENFORCE`、可执行 Operator Gateway 与 verification-only Ed25519 公钥环，否则 ASGI 在路由开放前 fail closed。下游异常后 Runtime Group 重读 exact revision 的 durable intent：匹配本次 request/audit/source/reason/graph/revision 才标记可能已有副作用，无 intent 则明确未执行，读取失败或畸形事实保守标记不确定；
- 0099 是 schema-only、append-only terminal reconciliation sidecar：migration 只安装 immutable case/proof/supersession，不扫描或改写业务行。Readiness 从受信 migration 资产重建 canonical schema，对 live `sqlite_master` 严格比较 3 table/4 index/10 trigger 的全部 SQL，并以 Python codec 复核 case/subject/proof。`recover_startup` 后才在单次启动最多 10,000 个 terminal Graph 的上限内 inventory。`FAILED + exact atomic failure bundle + 唯一 graph-scope ACTIVE/EXCEEDED Budget` 可由显式 system auto 修复，`BLOCKED failure` 与 legacy `CANCELLED + unsettled Budget` 只接受签名 Operator；child/missing/multi Budget、PENDING/CONSUMED、`COMPLETED`不一致或 Graph/Node/Review/Attempt/Lease/parallel after-state 漂移永久 quarantine。Parallel authority 对 0090 要求恰好一条 canonical decision，0091/0092 分别复用完整 success 与 trusted cancellation validator；exact Attempt failure bundle 还逐条校验 Attempt/Node/Graph/Lease Outbox `payload_ref`。Runtime 与 Store 写事务在新建/激活 Budget 前双重拒绝 terminal Graph；同 Graph/revision 任一 inventoried 未决 current case 都围栏普通 TaskGraph transition/cancellation request 和 Budget transition/consume/usage/retry，0099 proof UoW 是唯一显式 bypass。Proof 只在原 Store UoW 中 CAS exact Graph/Budget并写确定性 Outbox；旧 case 仅能链接到新的 current case，每个历史 case 的 supersession chain 最终仍须收敛到可信 proof，不创建 Attempt/Lease或重跑 worker。`TERMINAL_HEALTHY_CLOSURE` 只在存在 unresolved unsuperseded 历史 case 且 current classifier healthy 时生成，固定 `SIGNED_REPAIR_REQUIRED + NONE`、仅 `SIGNED_OPERATOR`、Graph/Budget/Outbox 零写；snapshot 漂移继续追加 replacement/supersession。默认关闭的 exact auto 在查询层只取 exact case并循环到空/显式 `TRUNCATED`；system proof 以 immutable case 创建时间生成稳定 identity，多进程收敛到同一 proof/replay，批处理前缀已提交后的失败必须明示 `side_effects_may_have_run=true`。主 Host 与独立 Obs 的三条查询入口均只构造同库 `mode=ro + query_only=ON` reader，禁止回退完整 Store。该能力仍为 `WIRED_DEFAULT_OFF / CANARY_NOT_RUN`；外部 deployment inventory、完整 key lifecycle 与 canary 仍未知，general/multi-node/pre-budget recovery仍未完成；
- phase 1 UoW 先将 Graph 推入 `BLOCKED`，把非终态 Node、active Attempt/Lease、Review/token、operation 和未授权 effect 写入取消或 reconciliation 状态，提交后才 cancel/join 本进程 child 与 heartbeat。执行重入会发现 intent 并继续 advance/finalize，但本地 task handle 不是 authority；
- phase 2 只有在 active ownership、operation/effect/review 不确定性全部收敛后才原子提交 Graph `CANCELLED`、Budget 释放/取消、Outbox 与 canonical decision。accepted NODE + succeeded Attempt 的 effect 保留既有 delivery authority；其余 NODE/未完成 GRAPH effect 被 fence。0091 success 与 0092 cancellation first-writer-wins，请求/封口响应丢失 exact replay；
- Effect fence 不把“投递失败”解释成“没有副作用”：取消前已经 `FAILED`，或取消时仍 `CLAIMED` 且随后 handler/transport 失败，均继续保持 `RECONCILIATION_REQUIRED`。只有 `COMMITTED` 且存在 durable receipt 才可自动成为 `COMMITTED_EVIDENCE`。当前表没有可信的 handler-never-invoked / `NO_SIDE_EFFECT` provenance，因此不能用 reason code 猜测；旧 `FAILED_EVIDENCE` 只作兼容读取，必须纳入历史修复或隔离；`PENDING` 由完整 codec tuple 再细分，只有 `attempt_count=0 + fencing_token=0 + status_version=0 + reason_code/last_error=NULL` 的 virgin epoch 可逻辑取消，retry epoch保守待对账，混合 tuple 按 durable corruption fail closed。当前 canonical authority 不会让 handler-run retry epoch进入 cancellation fence；该分类是 legacy/corruption hardening。历史 retry-PENDING + CANCELLED fence 同样需要显式 audit/repair/quarantine，不能在 FINALIZED replay 中改写；
- Operation 与 Effect 对账均已完成受限签名合同/服务、ProcessTaskRuntimeContainer 构造、ASGI Operator Runtime 注册、scoped OBS command、公钥解析与 verification-only TaskStore 注入，状态为 `WIRED_DEFAULT_OFF / CANARY_NOT_RUN`。`task_effect.reconcile` 只接受 exact case/subject snapshot；`NO_SIDE_EFFECT` 解析为 `CANCELLED`，`SIDE_EFFECT_CONFIRMED` 必须带 durable receipt 并解析为 `COMMITTED_EVIDENCE`。response-loss 只允许 exact proof replay，未知写结果保持 uncertain；
- 0098 只追加 immutable case/proof sidecar 和 revisioned reconciliation Outbox，不更新或删除 0087/0092 Effect、fence、decision 行。PENDING proof 参与之后的新 decision evidence；FINALIZED correction 必须绑定当前 immutable legacy decision ref/payload digest，且只作为 getter/request/advance/finalize replay 的 trusted overlay，proof ID 不得进入或替换旧 decision evidence。迟到的 Effect commit/fail/reschedule 必须在原事务内看见 PENDING proof 并 fail closed；
- Operator 只读接口提供脱敏 candidates、cases 与 exact signing challenge。0098 production readiness 只阻断数据库中真实存在且未满足 proof/correction 的 repairable/finalized legacy inventory，structural anomaly 永远 fail closed；正常运行时不存在的虚拟 case 不得阻断启动。仓库扫描未发现 SQLite 资产，因此尚无生产存量 remediation 或 canary 证据；
- Outbox publish 保留 `RuntimeOutbox` asyncio lock 与 `RuntimeOutboxPublisher` 实例级 `threading.Lock` 作为进程内降噪；调用矩阵仍为 27 build/31 publish/58 calls/5 best-effort。0093 已提供跨进程 durable claim/CAS。0095 要求 Graph Outbox 绑定现存 revision，并冻结 `graph_revision` 为 INTEGER/nullable/no-default/non-PK。回填来源包括 decision/intent/attempt/唯一 revision、HostCommand evidence 对 CREATED 的绑定，以及 Migration Runner deterministic `natsume_sha256_hex` 以 revision-bound command ID 重算升级前 governed ID/key，对 ACTIVE 等非 attempt transition 的绑定；冲突或不可解析自定义 producer 整笔迁移失败。Outbox ID/key 保持升级前稳定，revision 是新增列维度。新 Graph payload ref 必须是 `taskstore://event/TASK_GRAPH/{graph_id}@{revision}/{version}`，旧 ref 仅限 durable codec/replay compatibility，新写 trigger 拒绝。predecessor/head 按 type+id+revision 隔离；所有 Graph producer 显式写 revision。新 Runtime Event 使用 `graph_id@revision`，旧 journal 经 source Outbox revision 兼容读取；`TASK_STORE_OUTBOX source_ref` unique partial index 保证一对一，append 已成功但 complete 未确认的 legacy 重投只有全字段精确重建时返回原事件。Operator/ReviewStore 按 canonical revision subject 过滤。publisher 继续逐条 claim→幂等 append→fenced complete，失败 release/backoff；后台 worker 自动续投/takeover，close 与前台 publish 线性化，`DRAINED` 仅在 total_pending=0。0093/0095 readiness 与 receipt gate 同时冻结 payload trigger、source index、delivery index 与 persisted bindings；
- Runtime Event `created_ts=0` 对可重建的 current revisioned 与 legacy unrevisioned candidate 都允许 durable 非负 timestamp 兼容，因为两条历史 append 路径都可能用时钟替代零值；除 timestamp 外的字段继续 exact。PlanningRecovery 显式调用 `read_scheduler_snapshot(..., include_graph_outboxes=true)` 并在同一读事务使用唯一 terminal Outbox 的真实 payload ref；默认 false，普通 scheduler/Operator 不扫描 Outbox。只有旧版或替代 Store 未提供可选扩展字段时可合成 revisioned ref；生产 DurableTaskStore 提供 inventory 后，缺行、畸形或歧义都 fail closed。0100 append-only supersession、`admit_replan()` predecessor validation + revision+1 admission 单事务、exact replay/rollback 和旧 revision 共享 mutation/Review fence 已完成；当前 safe subset 仅接受静止 `FAILED`，拒绝 `BLOCKED`、所有历史 Review continuation 与 Acceptance Effect，自动 failure-triggered replan/生产 canary仍未完成，状态为 `PARTIAL_DURABLE_SUPERSESSION_SAFE_SUBSET`；
- Task Runtime 启动已按 migration 与 0093/0094/0095/0098/0099、Review 0096、grant uniqueness 0097 readiness → `task_store.recover_startup` → 0099 terminal inventory → 可选且默认关闭的 exact system auto → publisher → recovery outbox publish → outbox background worker → Host/Internal/Scheduler runtime activation → cancellation 与 Review continuation workers 排序；ASGI 将配置逻辑 owner 派生为每次 container construction 唯一的 execution owner。恢复异常在 route activation 前 fail closed。Container 拥有且向 Host/Internal/Scheduler/worker 注入同一 process-shared `LocalGraphExecutionRegistry`，仅用于本进程 quiescence，不是 durable authority。Coordinator 激活后任一 worker start 失败会尝试反向 stop/join worker 与 close Coordinator；全部回滚成功才 unbind，失败则保留 handle 供 `aclose` 重试，Runtime Group 也保留关闭失败的 child。shutdown 经有界 grace+cancel，若任一 worker 仍不静默则 fail closed 并保持 Store 开放供重试。0093 Outbox 已具在线过期 takeover；0094 cancellation worker 以独立 recovery-scoped owner 扫描、claim、heartbeat、defer 与 expired takeover pending intent，claim/heartbeat/defer/complete 在 `BEGIN IMMEDIATE` 后才采时；Runtime 未显式获得 `local_pending` 时必做 `cancel_and_join`，本地非静默或 operation/effect/review 未收敛时都只 durable defer；
- lifecycle 未完成统一返回 `TASK_RUNTIME_LIFECYCLE_RETRY_REQUIRED`；activation rollback 或 `aclose` quiescence 失败后 bind/activate 锁死，只允许 `aclose` 重试。Coordinator admission close、drain、`aclose` 任一失败均保持 Store 开放；只有 Coordinator 与 worker 均静默后才 drain Outbox/关 infrastructure；
- 0096 新增纯协调表与 composition-owned Review continuation worker。`CONSUMED/REJECTED/EXPIRED` 的 task-bound Review 与原 Review mutation 同事务入队；worker 以 owner/token/expiry/单调 fence 跨进程 claim/takeover，按 immutable HostCommand surface 回到原 Host/Internal/Scheduler runtime，并只在 reviewed Node 离开对应 waiting checkpoint 后完成协调。PRE `WAITING_APPROVAL` 进入统一 0090 outcome，公开 receipt/decision 使用合法的 `attempt_id=null`；POST 仍绑定原成功 Attempt 且不重跑 worker。续跑 credential 的期限覆盖 `budget time + child TTL` 且硬上限 24 小时；READY claim 与 worker 首次执行校验必须复核 live claim、exact Attempt/Lease/owner/fence。首次校验后由 Task Attempt/Lease 管理已开始的执行，Review heartbeat 丢失不伪称能撤销 blocking/to_thread 外部副作用，旧 owner也不得 complete/defer；
- 0097 为 `S_TASK_GRAPH_NODE_GRANT` 增加 request+intent partial unique index 与身份 trigger；SQLite ledger 在 `BEGIN IMMEDIATE` 后重查 deterministic batch identity，跨进程 loser 只读取 winner 的 exact batch。批量第二条写入失败整事务回滚，历史重复让迁移 fail closed；
- 0089 若在物理事务提交后丢失 migration receipt，只能在 checksum、列约束与索引形状全部吻合时修复 receipt；schema readiness 仍必须先于 Budget 写入；
- Executive 生产入口、legacy SkillDag 执行和进程内任务 registry 已删除，`task_progress/recent_turn` 改为 durable projection/read route。
- D9 统一将受认证 Host Input/Gateway 的 Secure Ingress tuple（principal、成对 claim/audit ref、body SHA-256、派生 surface）冻结到 Governor、direct HostCommand 与 approved-plan admission；Plan 首次 admission 和 direct admission 均先以 base request/session/principal 只读预检 parent Governor binding，child grant 派生再复验。真实 SQLite admission/replay 与 principal-drift 零写入已覆盖该链。该证据不覆盖 Delegate 内部 dispatcher authority，也不等于通用候选搜索或 MCTS 已进入 Host。
- D10 用 composition-owned `GovernedDelegateDispatcher` 替换 Host Delegate 对 legacy global dispatcher 的回落：只注册 `pentest_agent` 这个 effect-free Domain Pack/SecureRun handoff，冻结 command/request/session/principal/root WorkOrder/graph revision/node/attempt/lease/owner/fence/parent+node grant；调用前重验 node grant，Attempt/Lease/Fence 仍由外层 durable worker 保持。它不创建 runtime gateway、不写 legacy `skill_exec_log`。`intent_agent`、`translate_agent`、`vision_agent`、`repair_agent` 与未注册 target 在 direct/plan admission 前失败关闭。真实 SQLite direct + plan delegate/replay 已证明；这不是通用 delegate 平台，也不证明预算硬限、完整 Review canary 或候选搜索。
- D12 先修复 PlanningSearch 的错误影响边界：`PentestPlanningSearchAdapter` 已移除“校准阈值通过即可选择 L3”分支，始终执行 L1/L2 baseline，并仅把 L3 作为 shadow evidence 记录。内存 `PentestMCTSCalibrationLedger` 不能跨 run、不能提供反事实 outcome，也不再拥有任何 production influence；dynamic successor、durable calibration、promotion proposal/gate 仍是 NE-02 未完成工作。
- D13 建立领域无关的 0105 `CalibrationPrediction/Outcome/Repository`：prediction 和 outcome 分表 append-only，canonical JSON/digest、prediction identity、candidate bind、outcome identity、重启 replay、physical-schema semantic verification 与 SQL immutable trigger 均 fail closed。Pentest adapter/loop 可从显式 kernel context 注入 repository，把 virtual state、baseline/shadow path、trace、预算和纯虚拟 counters 写入；候选 target 会在 fingerprint 前先收口到授权 scope。注入库的 busy/schema/drift 失败只产生 `mcts_durable_calibration_unavailable` 可观察事件并跳过可选 shadow evidence，不阻断既有 L1/L2 baseline。现有 Pentest execution record 只投影为 `UNVERIFIED_RUNTIME_RECORD`，不是 Task/Run receipt，默认无注入路径仍是内存 compatibility ledger；Research/Code、dynamic successor、receipt-bound counterfactual 和 promotion gate 仍未完成。
- Workspace 已推进到 `PARTIAL_W2_INTERNAL_GIT_WORKTREE_WIRED`：D0/W0/W1 继续有效；认证 Internal Agent 写 admission 会覆写 caller workspace 为 graph epoch 派生 ID，并在显式配置稳定 HMAC secret 时注入 W2 runtime。首次 source HEAD 被冻结为 durable commit，独立 namespace worktree、实例/PID 租约、独占 scope、heartbeat、顺序 handoff、过期 takeover、可信 cleanup receipt 与 pending cleanup worker 已接线；启动同步最多续清 1 条，剩余 durable backlog 交 worker。首次 HEAD 并发 winner 可严格重放，其他 immutable drift 失败关闭。Provisioning/allocator/cleanup adapter 的 Filesystem/Git callback 已移出 SQLite writer transaction，workspace/source 跨进程锁、共享 deadline 与前后 exact authority 短事务允许 heartbeat 并拒绝后验 fence/expiry 漂移；Agent tool 只在动作间协作取消，Verification 只在整体启动前验活，二者没有逐操作 fence或强杀。默认缺 secret 时 Host 降级在线而写入关闭。它仍缺进程级强杀、promotion/abandon、accepted checkpoint HEAD、Domain Pack/Codex runtime 接线以及 OS/container/tenant/handle 隔离，真实 canary 未运行；
- Composition D1～D7a 的 app-local route、Obs lifecycle、Host五合同lineage、Memory/VTS冷读、route locator 清零与0103边界继续有效。D7b 让 Life Body 只聚合同一 app 的 runtime/overview/insights/trace durable roots 与 canonical ledger 有界只读 lineage；Host 停止后可冷读，缺根、无效、过期和 ledger 失败均写 typed `SOURCE_UNAVAILABLE`，公开 payload 不保留 content/private body，安全/Gateway风险语义保留，源未变化不追加journal。21个 producer 的声明 source-mode 更新为`6/3/8/4-auth/0-unavailable`，但声明为 partial 的成功路径不等于运行可用。Direct Skill worker 的 resource manager、episodic store 与 semantic recaller 改为 composition-time 实例快照，执行期不再回读进程 locator；authority-first依赖预检阻止缺后端被接受。D7c同时清零composition→app反向依赖且未增加NLE白名单。D8 的 Host 0104 migration 为 canonical DB 派生的 `runtime_scope_id` 增加单一 runtime lease/fence、scope-filtered outbox 与 Edge credential 持久记录；每个 lifespan 仍显式持有 runtime，但同 scope 只有持租约者可工作，第二实例和旧 fence 失败关闭。Edge session/owner 稳定派生，并以 Host 持久保存的 exact lease/fence 支持同身份重启重连。Edge 0002 冻结 command owner；receipt query v2 要求 HMAC+nonce、owner/current session/lease/fence 与精确 command/key，legacy ownerless 返回 `NOT_FOUND`。`IN_DOUBT` 只由 found receipt 的 identity+digest CAS 收口，重复 digest 不重复落审计，`NOT_FOUND`/查询失败不重试或重放。非 loopback HTTP 被拒绝，远端仅允许 HTTPS。child Edge 的 loopback TCP/HMAC/SQLite、receipt query 与重启 exact replay 有证据；Life、reply-performance、Gateway voice 等选定路径的新命令只 durable queue 并返回 `PENDING / DURABLY_QUEUED`，精确重复只回放既有 durable 状态，只有 `APPLIED` receipt 才能表明 Edge 已收口，仍没有真实设备效果。底层 Host source、Chat/Delegate、Acceptance Effect authority 仍共享，剩余 direct embodiment adapter、约53个非锁可变服务引用、完整Host/Worker对象图和全局 locator 删除均未完成；完整Query Plane、NE-01和全局Composition不得封板。

Lane A 已完成；Lane B 当前到 `PARTIAL_B5C_DURABLE_CANCELLATION_SHADOW`。B4/0090 与 B5A/B5B/0091 语义继续有效；B5C 已有 0092～0099 分范围 durable authority。Budget 高竞争唯一 winner、单节点 post-budget 检查点接管与 0099 窄历史终态 reconciliation 已有证据，但不构成通用恢复。0094 本身不复制 0092 业务状态；Operation、Effect 与 terminal reconciliation 均为 `WIRED_DEFAULT_OFF / CANARY_NOT_RUN`。外部生产库存尚未盘点，general/batch/pre-budget recovery、部署密钥/凭据、完整 key lifecycle 与真实 canary尚未完成，atomic、bounded、cancellation均为 `PRODUCTION_OFF`。现实 resolver 分类仍未闭环。Lane D 的 Workspace 只到 `PARTIAL_W2_INTERNAL_GIT_WORKTREE_WIRED / DEFAULT_FAIL_CLOSED`，Internal 单路线接线不等于强隔离或全 Agent 接线；Composition 也未完成。因此 Lane B、NE-01 和完整架构都不得封板。

目标：先修发动机底座，保证所有生产入口真的可执行，再删除第二 authority。

#### Lane A：Scheduler / Internal contract repair

状态：`COMPLETED`（Scheduler 与 Internal 均有首次真实 admission/replay 证据）。

- 将 `AgentWorkOrder` 转为显式 `WorkOrderV3`；
- 每路必须有 `BudgetPolicy`、node-bound grant、Review runtime、Verification、Acceptance 与 Receipt；
- 增加首个真实 admission/integration，不允许只测 startup；
- Host Input/Gateway/Scheduler/Internal/Operator 的 route matrix 全部 fail closed。

#### Lane B：Durable DAG completion

状态：`PARTIAL_B5C_DURABLE_CANCELLATION_SHADOW / LANE_IN_PROGRESS / PRODUCTION_OFF`。0102/GR06 canonical Node Acceptance 的应用 Store UoW 已统一，但 pre-stage、low-level graph create 与 direct SQL 仍是兼容/物理旁路；Cancellation、Operation、Effect 与 0099 terminal reconciliation 均为 `WIRED_DEFAULT_OFF / CANARY_NOT_RUN`。Budget 唯一 winner、单节点 post-budget 安全接管、ACCEPTED 后 Effect 原子补写，以及 0099 窄历史终态 authority 已证明；尚缺 general/batch/pre-budget recovery、现实 resolver、目标部署 inventory/remediation、部署 key lifecycle 与真实 canary，不能宣称 Lane B 或通用恢复完成。

- 维持 0094 scanner/worker、Operation/Effect 签名对账与 0098 append-only/late-writer/replay 故障矩阵；对每个目标部署运行真实 legacy inventory gate、补齐 deployment key/credential lifecycle 并完成 canary，此前保持 OFF；
- 持续守护 0095 revisioned Graph Outbox：歧义历史回填必须整笔失败，Graph/non-Graph trigger/readiness、multi-revision marker 完整扫描、revision 内顺序/不同 revision 独立推进、Runtime Event 与 Operator exact revision 必须保持通过；
- 守护 0099 历史终态合同：单次启动盘点最多 10,000 个 terminal Graph；只允许唯一 graph-scope Budget 进入精确修复，完整校验 Graph/Node/Review/Attempt/Lease、0090 唯一 decision、0091/0092 canonical authority 及 Attempt failure Outbox payload refs；child/missing/multi/不一致事实 quarantine；未决 case 围栏 graph-wide 普通 Graph/Budget mutation，仅 0099 UoW 可 bypass；旧 case 只可形成最终收敛到可信 proof 的 supersession chain；保持 canonical 3 table/4 index/10 trigger SQL readiness、Python codec、多进程 system proof 稳定性与批处理 partial-side-effect 语义；
- 守护 0096 Review continuation 故障矩阵：Review mutation 与 enqueue 原子性、过期扫描、claim heartbeat/takeover/stale fence、surface 精确路由、PRE null Attempt、POST no-rerun、长等待 child-grant renewal 与 shutdown quiescence；不得把它混同于 0092 cancellation reconciliation；
- 守护统一的 0102/GR06 Node Acceptance UoW、Attempt-bound Outbox、effect delivery exact-artifact guard、schema/asset readiness、startup legacy inventory 与 successor exact-input 矩阵；下一步只在能迁移 standalone pre-stage、low-level graph create 并增加物理 SQL cross-table fence 时再提升定级；
- 保持 0093/0095 故障矩阵：并发 owner、takeover/stale fence、per-revision 顺序；deterministic CREATED/ACTIVE 回填、自定义 producer 整事务失败、升级 ID/key 稳定；revisioned payload/legacy replay-only ref；payload/source-index readiness；append-success/complete-loss exact replay 与零重复投影；created_ts=0 对 current/legacy candidate 的受限兼容；PlanningRecovery opt-in 同事务实际 terminal ref、旧版或替代 Store 缺失可选扩展时的兼容 fallback，以及默认热路径零 Outbox 扫描；multi-revision marker/Event/Review/Operator exact read；PENDING/DRAINED、inventory/ready 与热路径不重扫；
- 把现实 resolver 的可证明瞬态准备失败映射到显式 marker；禁止扩大到 reservation 后异常；
- 在现有 max-attempt/backoff/retry-budget 原子路径上补齐真实故障分类与运行观测；
- 持续守护已完成的 Budget 高竞争唯一 winner/`ALREADY_EXCEEDED` 合同；不得把它扩写成 concurrency 维度计量或 `DEGRADE/REVIEW` 已完成；
- 持续守护 0096 task-bound Review continuation、单节点 post-budget Operation continuation 与 ACCEPTED→Effect restart finalization，保证 worker/Effect 不重放；另行实现 general/batch/pre-budget recovery 和一般 recoverable Attempt 的统一在线 continuation；
- 并发、retry、cancel、lease takeover、crash recovery 均产生同一 lineage。
- 完成跨进程、接管、续跑和故障矩阵后再做 production canary；在 canary 证据前 `atomic_graph_success_enabled` 与 `bounded_fanout_enabled` 必须保持关闭。

#### Lane C：Legacy authority removal

- 在 `PersonaEngine.apply_growth_delta()` authority 边界中央 fail closed 所有非迁移调用，并对 stage、milestone、interaction、emergent weekly 及后续新增调用点建立静态/运行时门禁；NE-01 只阻断旧写入，不在此波偷偷建立第二套演化系统；
- 删除 Executive `_execute_explicit_skill_dag` 生产路径；
- 删除 `LegacyRuntimeTaskRegistry` 写入和全局 accessor；
- `task_progress/recent_turn` 改为 projection/read route，不再借 Executive 执行；
- 删除 cycle→忽略依赖串行的兼容行为。

#### Lane D：Composition 与 workspace

- D0/W0/W1 已完成既定围栏；W2 在 Internal Agent 单路线接入 graph-bound Git worktree、heartbeat/handoff/takeover 和“启动 1 条 + 后台 backlog”的 cleanup continuation，并保持缺 secret 默认 fail closed。仍需受控 container/OS tenant、进程级取消、promotion/abandon、accepted checkpoint HEAD、Domain/Skill 全路线与真实 canary，不能把 `DIRECTORY_FENCE + worktree` 写成完整隔离；
- mixed-file gate 未抬高 ceiling，但全仓仍因 `governed_task_adapter.py`、`host_command_repository.py`、`review_continuation_store.py`、`scheduler/governed_runtime.py` 四个既存 >1,500 行未分类文件为 RED；本次 `asgi.py` 已保持在 1,500 行以内。`DurableDagSchedulingRuntime` 当前 1,338 行，`coordinator.py` 268 行；新增 238 行 `AcceptedGraphFinalizationRuntime` 是 normal/restart/continuation 共用的 Graph+Effect completion boundary。Dag 调用矩阵现为 Store 9、Budget 9、Lifecycle 17、AcceptedFinalization 1、NodeProjection 13、NodeExecution 7、Settlement 4、Success 1、Review 4、Cancellation 1、Outbox 1 build/6 publish；不反向持有 Coordinator callback、不建新锁、不取得第二 persistence authority。它仍是临时 outer orchestrator，不能据此宣称 mixed-responsibility、历史 reconciliation 或一般恢复闭环完成；
- 用 `HostApplicationContainer`、`ObsApplicationContainer`、`WorkerApplicationContainer` 等 typed scopes 替代模块全局 container；
- route/handler 只依赖显式 ports；
- D1 已完成 Canon/Tool/Bond 及其 OBS adapter 的 app-local ports；不得把该局部结果写成整个 OBS 或 ProcessServiceContainer 已去 locator；
- D4～D7c 的projection迁移、五合同source、Memory/VTS冷读、Obs-route locator 清零、Host六个GET切流、Life Body冷源、安全与journal语义、Direct Skill authority-first依赖门禁及Obs app assembly反向依赖清零继续守护。D8 已补 Host 0104 scope/runtime lease、持久 Edge reconnect credential、Edge 0002 owner authority、receipt query v2、最小真实 TCP/HMAC/SQLite/restart smoke 与 `IN_DOUBT` 只读 CAS 对账；选定 Life/reply-performance/Gateway voice 只 durable queue。它不证明物理设备效果，也不覆盖剩余 legacy direct adapter 或全路径注入。下一刀继续收口仍在`ProcessServiceContainer`中的约53个非锁可变引用、Chat/Delegate/Acceptance Effect共享authority和其余完整Host/Worker scope。迁移测试不等于部署库已执行0103/0104或Edge 0002，局部D8切片不得写成NE-01完成；
- 消除业务层 `get_*()` service locator 和 test reset 单例；
- 每个 Task/Experiment 绑定 workspace owner、root、isolation mode 与 cleanup receipt；
- Code shadow 使用隔离 worktree，禁止污染主工作区。

完成条件：Scheduler/Internal 首次真实任务成功；全 route 使用 `WorkOrderV3`；legacy registry 写入为 0；T-PERSONA-FENCE-01 通过；global service locator/accessor 为 0；DAG retry/parallel/resume 有 crash recovery 证据。

### NE-02：真实通用规划与收益证明

目标：让 PlanningSearch 生成并选择竞争方案，而不是包装上游既定步骤。

#### 公共合同

- `SuccessorCatalogPort`：根据虚拟状态动态生成后继；
- `VirtualStateSnapshot`：schema、digest、capabilities、facts、constraints；
- `CalibrationRepository`：durable prediction、baseline、selected path、actual outcome、cost；
- `DomainBenchmarkPort`：真实 workload、baseline、verifier、budget、isolation；
- Search result 标准编译为 node-bound immutable TaskGraph payload。

当前已完成的基础切片仅限以下范围：D14 已把 `PlanningGoal`、`VirtualStateSnapshot`、动态 successor 合同、shadow workload 合同与 context-owned immutable compilation 接入公共内核。D16 已在 Code 域实现从冻结 opaque ref/digest 状态动态生成 `inspect → registered patch → unit/rollback` 的纯 catalog；selected inputs 必须重新对照 catalog，`virtual_model_ready=false` 时不暴露 L3 transition/reward，即 `MCTSMode.ALWAYS` 也不能绕过。context 在 initial/replan/recovery 中透传，且 `approval_ref` 必须绑定 parent governor grant。它没有打开 worktree、源代码、模型、工具、测试、provider、Task admission 或生产 influence；中间虚拟 state 的 Code replan lineage 也尚未设计。D17 在独立实验 adapter 中增加 private bare clone（`--no-local`、移除 remote、private hooks path）和同 commit 的 baseline/selected detached worktree；它要求 source 自有非链接/非共享 `.git`，其 local Git config 与 checkout content 是受信输入。Git 子进程清除继承 Git 配置、system/global config 和交互提示，关闭 `core.fsmonitor`，仅允许 `file` transport，并跳过标准 LFS smudge。arm 只能经 lower-case portable repository-relative 文件 API 读写，padding/case alias/`.git`/遍历/symlink 逃逸都会拒绝，opaque ref 也拒绝 host-path-shaped 值；ignored artifact 失败。D18 只在 D17 成功清理后，才生成路径无关的 `VERIFIED_CLEAN` 回执：private bare、双 worktree 与运行根必须已删除，受保护 source fingerprint 必须前后一致。0106 随后以独立、append-only SQLite ledger 持久化 workload registration、冻结 paired RunSpec、两臂 evidence/outcome、cleanup receipt 与 terminal experiment receipt；每段均 canonical JSON/digest 绑定、精确 replay、schema semantic verification，且 terminal receipt 必须同时拥有预注册、两臂和 clean receipt。D19 可把一个冻结 `python -m pytest -q <repo-relative-test>` 计划预注册进同一 workload：它在两条 detached worktree 中以 shell-free、workspace-root cwd、无 `PYTHONPATH`、关闭 pytest 自动插件加载的子进程执行，并在 arm evidence 中持久化无路径的命令 identity、exit/status、受限输出 digest/长度和耗时；只有该 adapter 真正拒绝的 paired `max_test_commands` / `max_wall_time_ms` 才是硬门禁。D20a 新增 ResearchTaskSnapshot 和动态 `query/quick-answer/primary/independent/cross-check/conflict/stop` catalog：它只看冻结的公开 source id/ref/content digest 与有限 authority/relevance/read-cost hints，且没有专用本机 locator、source-body、answer-key 或 verifier-decision 字段；constraints 拒绝这些命名 key，但任务文字不是内容级 DLP。只有 `virtual_transition_ready=true` 才提供 L3 virtual ports。与 planner 分离的 FrozenResearchCorpusLoader 只从可信实验根与 manifest 共同指定的 repository-relative regular files 读取，并在稳定受信输入中前后检查 ADS colon/case alias/链接/reparse、文件状态、大小并在读完后核对 digest，公开 receipt 不含路径或正文。D20a 没有真实 Research WorkOrder/BrowserWorker 或网页抓取、模型调用、独立 verifier、paired receipt/cohort、成本/延迟对照或生产 influence，根与 manifest 也是受信实验输入而非 OS/tenant sandbox。D19 仍是同主机受信 workspace，不是 independent verifier、container/OS/tenant/filesystem/network sandbox；本地 source、测试和用户 site 依赖仍是受信输入，model/provider/tool/token/cost 未受管。它不接入 Host/TaskGraph/AgentExecutor，也没有真实模型、隔离 hidden oracle、历史 cohort 或 uplift。D15 已把 sealed governed `codex_runtime` 的 `ModelAttemptService` / 同源 `PromptRegistry` 端口接入 `AgentRuntimeExecutor`，但它只证明 read-only agent runtime 的受信模型端口可达。`agent_worker` 写入仍缺 task-bound WorkspaceGrant/WorkspaceRuntime，ReAct 模型调用也尚未消费 `ModelBudgetGuard`；因此 D15～D20a 都不是 Code 执行、Research 生产研究或 Code 规划切流。

#### Research 第一真实领域

- 使用真实只读网页/文件来源与 Research WorkOrder；
- 候选覆盖 query、primary-source、cross-check、conflict resolution、stop；
- verifier 计算 claim coverage、引用有效率、主来源比例、冲突消解、成本和真实延迟；
- 同预算比较当前 baseline 与 PlanningSearch，先 shadow，不影响用户答案。

当前进度：D20a 仅完成后续实验的纯规划和受信 readonly-input 机械边界；loader 对稳定输入做普通文件/摘要漂移检测，不是 hostile concurrent replacement 的原子 TOCTOU 防线，且尚未把 `ResearchTaskSnapshot` 与 manifest 的 corpus/source id/ref/digest/authority tier 精确握手，因此不证明独立来源。它不满足本节任一“真实”验收项，`research_production_planning_benefit=UNPROVEN`。D20b 必须先预注册同一 workload/profile/budget 的 baseline/search 双臂，再将 planning snapshot、manifest、每份 opened document 做 exact corpus/source id/ref/digest/tier handshake，独立运行、独立验证并保存不可篡改的 source/model/output/verifier/cost/latency 证据；无真实非缓存 provider receipt 时不得把 scripted test 计入收益。

#### Code 第二真实领域

- D16 的纯 catalog 只能作为前置 planner：其 `allowed_path_refs` / `forbidden_path_refs` 不是 live Workspace ACL，不能拿模拟 descriptor 代替 executor；
- D17 只证明 trusted-process private-bare 双 detached worktree 与 server-side lower-case repository-relative API 的实验机械链；它必须保持 `PRIVATE_BARE_WORKTREE_TRUSTED_PROCESS_ONLY` / `network_isolation=NOT_ENFORCED`，不允许把本地 Git hardening 或窄 API 写成 container/tenant/OS/network 隔离、模型预算治理、hidden-oracle 安全或真实 uplift；
- D18 将该机械链的成功证据写入独立 0106 durable ledger：必须先以独立 append-only transaction preregister workload，再 preregister exact baseline/selected RunSpec；cleanup 后的 terminal-evidence transaction 才写两臂 verifier/diff evidence、`VERIFIED_CLEAN` cleanup receipt 和 terminal receipt。它只让实验事实跨重启可审计，不产生 winner、promotion、生产切流或 hard model/tool/test budget authority；
- D19 只允许 registration 已绑定的固定 `python -m pytest -q <repo-relative-test>` argv 在两条 detached worktree 内运行；它实际拒绝 paired test-command/wall budget 超限，要求 `PASS ↔ exit_code=0`，并持久化 exact run/arm/plan/budget-bound 的 path-free command receipt，terminal replay 再核验两 arm aggregate bound。该 child 不是独立安全域或 hermetic Python：没有 network/container/OS/tenant/filesystem/process-tree/CPU/memory 隔离，APPDATA/user-site 仍是受信输入，也没有模型、provider、工具、token 或成本预算；专用 experiment service 仅在两 arm PASS 与 `VERIFIED_CLEAN` 时写 terminal receipt，crash cleanup recovery 仍未实现；
- 使用临时 worktree 和固定真实任务集；
- 候选覆盖 inspect、patch、unit、integration、rollback；
- verifier 使用真实测试、静态检查和 diff risk；
- 只在隔离 workspace 执行，不改主工作区。

#### Pentest 校准修正

- expander 根据虚拟 capability/fact 动态产生后继；
- 真实 PreActionGate 只在执行当前节点前复验；
- 校准跨 run 持久化；
- 不得用 baseline 根动作 outcome 冒充 L3 未执行路径 outcome；
- 达到预注册样本量、误差、安全和 uplift 门槛后，才允许 influence proposal。

完成条件：Research 真实 shadow 证明预设净收益；Code 至少完成 production shadow；Pentest L3 可在本波保持 shadow，或在 durable Gate 通过后受控切流。若保持 shadow，必须把 Target Gate #6 记录为 NE-07 未决条件；Synthetic benchmark 只能作为算法回归。

### NE-03：World、Scene、Intent 与 Runtime Guardian

目标：形成“当前局面 → 场景约束 → 意图仲裁 → 动作预演 → 执行”的统一治理链。

交付：

- WorldFact/WorldSnapshot 有唯一 resolver、provenance、confidence、TTL、conflict 和 workspace scope；
- Project/User/Environment 状态进入 Context Pack，但不直接改长期记忆；
- Scene transition 写入 journal，并统一影响 Surface、Proactive 和 Task context；
- Intent Arbiter 合并显式用户目标、任务、关系、场景、风险、权限与长期目标；
- 高风险 action 必须经过统一 preview/shadow；
- Runtime Guardian 统一 dependency health、degradation、recovery、maintenance/sleep 与 SLO evidence。

完成条件：至少覆盖架构重构、调试、研究、闲聊、深夜、用户烦躁、游戏低打扰和发布维护八类场景；所有约束保持“只收紧、不覆盖用户目标”；故障进入/恢复可回放。

### NE-04：Evolution Lab、Scenario 与 Counterfactual

目标：让所有演化先在隔离实验室获得证据。

交付：

- versioned `ExperimentSpec`、dataset、seed、budget、policy variants；
- scenario runner、synthetic user/project、real-trace redacted replay；
- shadow executor 无生产 writer、TokenService、CapabilityBroker side-effect port 或 Edge actuator；
- counterfactual 比较原路径、替代路径、证据、成本、风险和可验证 outcome；
- ExperimentReport 只能生成 proposal，不能直接切生产；
- Operator Lab 提供只读结果、diff、证据和 Review 链接。

完成条件：Research planning、主动行为、记忆写入三类实验可重复；同 seed/dataset digest 结果稳定；Lab 外部副作用为 0。

### NE-05：Skill Compiler、成熟度与签名供应链

目标：把材料和成功轨迹转为可验证能力，但默认不能自动上线。

交付：

- Markdown、API contract、script、accepted trace → typed SkillDraft；
- 自动生成 input/output contract、风险、工具依赖、verifier、test vectors、rollback；
- bundle 生成 SBOM 和签名，进入 quarantine sandbox；
- staged activation/rollback 成为真实生命周期，不靠目录扫描/import；
- maturity policy 默认开启且 fail closed；新能力最高 L1/L2；
- promotion/degradation 由 durable evidence 与 Review 决定；
- 退役旧 Capability Forge 平行 routing authority，只保留 proposal source。

完成条件：至少一份 Markdown 和一条 accepted trace 各生成一个 draft；隔离验证、拒绝、staged activation、降级和 rollback 都有 receipt；未经审批自动升级为 0。

### NE-06：Experience Feedback、Knowledge OS 与 Identity Migration

目标：完成“经验可回流、知识可治理、人格可迁移但不自动漂移”的长期闭环。

#### Feedback

- 统一成功/失败/用户拒绝/工具可靠性/表达偏好来源；
- 只生成 `FeedbackProposal`，绑定 trace、outcome、置信度与 rollback；
- 修复 Prefrontal → `on_interaction_cycle()` 参数签名断链，并让异常进入可观测 evidence；
- 用 proposal-only adapter 替换 NE-01 已封口的 stage、milestone、interaction、emergent weekly 及其他 `apply_growth_delta()` 直接 writers；
- 删除其余吞异常的局部反馈链；
- proposal 进入 Lab、Review，再更新版本化 policy/maturity/knowledge。

#### Knowledge OS

- 明确 Life Memory 与 Project Knowledge namespace；
- 知识条目记录 provenance、version、validity、confidence、conflict 和 decision refs；
- 支持去重、过期、冲突、promotion、deprecation 与项目摘要；
- 重大架构选择生成 ADR/Decision Record，不把 Canon 当普通事实库。

#### Identity

- release 主链真实生成和持久化 IdentitySnapshot；
- 建立 migration plan/executor、persona/relationship regression、用户 Review；
- 重大变化默认阻断，激活后保留前一 release rollback ref；
- 记忆正文和私密 thought 不进入 snapshot。

完成条件：T-PERSONA-PROPOSAL-01 通过；至少一条真实任务经验走完整 proposal→Lab→Review→versioned apply；一个项目知识冲突被识别并处置；一次 identity no-change 与一次重大差异阻断/回滚演练通过。

### NE-07：Full Program Acceptance

目标：只运行一次完整最终验收，并明确外部环境限制。

进入条件：NE-01～NE-06 均 `CODE_COMPLETE`，没有已知 P0/P1，旧 authority usage 为 0；所有 Target Architecture Gate 仍是强制条件，早期 wave 允许 shadow 不等于最终豁免。

验收范围：

1. 全量 unit/contract/integration/e2e；
2. migration fresh/upgrade/backup/restore；
3. Docker 依赖、Host、Obs、Monitor UI、Gateway、Edge；
4. chat/skill/delegate/plan/scheduler/internal/operator 全路线 ledger 对账；
5. Research/Code/Pentest planning benchmark 与 calibration；
6. Evolution Lab、Skill compile、feedback、knowledge、identity golden paths；
7. security、privacy、performance、chaos、crash recovery、rollback；
8. ASR/TTS/VRM/VTS/硬件矩阵：可用则实测，不可用则精确记为 `DEGRADED/NOT_TESTED`。

其中 Pentest L3 必须满足 Target Gate #6：使用 durable calibration、能按虚拟状态生成后继，并由 promotion Gate 证明安全与收益；否则 NE-07 只能 `FAIL` 或 `ACCEPTANCE_PENDING_EXTERNAL`，不得 `PASS`。

完成状态只能是：

```text
PASS
ACCEPTANCE_PENDING_EXTERNAL
FAIL
```

不得用“测试很多”“diff 很大”“历史 22/22”替代最终 Gate。

## 5. 测试与效率合同

遵守以下节奏，避免再次把数小时花在每个小工作片上：

| 层级 | 何时运行 | 范围 |
|---|---|---|
| 小工作片 | 每次实现 | 相关 unit/contract/static；目标分钟级 |
| 波内汇合 | 一个 lane 完成 | 只跑该 lane 受影响的 integration |
| Wave Integration | 整个 NE 波代码接线完成 | 每波一次，包含一条真实 golden/smoke |
| Program Acceptance | NE-07 | 一次最终全量、Docker、前后端、Edge、真实 provider/hardware |

失败后只重跑失败节点和受影响 wave；禁止因一个测试修复重复跑全仓。全量测试在 NE-07 前仅用于仓库级高风险破坏，并必须记录原因。

## 6. 工作与提交合同

1. 每个 NE 波至少交付一条 production cutover、两个可量化架构改善和旧路径删除。
2. 只增加 adapter、shadow fixture、空目录、报告或测试时，状态保持 `IN_PROGRESS`。
3. 不按微型 TASK 编号逐个提交；按可审 vertical slice 中文提交。
4. 提交信息、PR 标题和正文统一使用中文。
5. 每个 PR 写清：真实生产路径、删除的旧 authority、定向测试、一次 wave integration、风险与回滚。
6. 不保护无价值 WIP；保护范围只包括用户明确资产、secret、模型、有效运行数据和当前任务外的真实业务改动。
7. 运行证据写入被忽略的 `artifacts/`；只有最终、脱敏、可复核的 machine receipt 才进入文档证据目录。

## 7. 停止与回滚

仅在以下情况停止当前 wave：

- 发现会破坏用户数据、身份、密钥或不可逆外部副作用；
- 需要用户选择会显著改变产品语义；
- 同一外部依赖阻塞连续出现且无安全替代；
- 发现目标合同本身矛盾，继续实现会产生第二 authority。

代码难、测试慢、旧文档冲突或实现量大都不是停止理由。每个迁移必须 expand-first、读写可切、数据可备份；禁止 destructive down migration。Core Release manifest 的 `migration_head` 始终以仓库中实际连续的最高 migration 为准；当前基线已推进到 `0106_code_historical_benchmark_ledger.sql`，后续 NE 波次继续使用 next-free 编号，不得把旧的 0104 固定值写回合同或测试。

## 8. 后续 Goal 定义

文档验收后创建的 Goal 应使用以下目标，不附带微任务数量：

```text
按 NEXT_GENERATION_EVOLUTION_EXECUTION.md 完成 NE-01～NE-07：先修复 Scheduler/Internal 与 Durable DAG/Review/Budget/legacy authority，删除全局 service locator；再完成真实跨领域 PlanningSearch、World/Scene/Intent、Evolution Lab、Skill Compiler、Experience/Knowledge/Identity 受治理闭环；最后统一启动 Docker、Host、Obs、前端、Gateway 与 Edge 进行一次完整验收。全过程按小工作片定向单测、每波一次集成、最终一次全量实测执行。
```
