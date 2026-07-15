受众：CODEX
标题：Natsume AI 稳定工程合同

# Natsume AI 稳定工程合同

版本：v4.2
日期：2026-07-16
文档类型：`STABLE_ENGINEERING_CONTRACT / NO_EXECUTION_STATUS`

本文把 PRD 转为长期稳定的工程约束，不记录实现完成度或阶段状态。

- 产品需求：[PRD.md](../20_prd/PRD.md)
- 当前事实：[CURRENT_CODE_ARCHITECTURE.md](../50_project/arch/CURRENT_CODE_ARCHITECTURE.md)
- 目标架构：[TARGET_ARCHITECTURE.md](../50_project/arch/TARGET_ARCHITECTURE.md)
- 当前差距：[ARCHITECTURE_GAP_MATRIX.md](../50_project/arch/ARCHITECTURE_GAP_MATRIX.md)
- 唯一活动执行：[NEXT_GENERATION_EVOLUTION_EXECUTION.md](NEXT_GENERATION_EVOLUTION_EXECUTION.md)

若本文与生产代码冲突，代码决定“现在是什么”，本文决定“实现必须收敛到什么合同”；不得用本文文字把未接线能力标成完成。

## 1. 进程与依赖合同

### 1.1 运行面

Host、Obs、Gateway、Edge、Worker 与 UI 必须可独立构造、启动、drain 和关闭。跨运行面只共享 `natsume_contracts` 等版本化合同，不共享具体 repository、service container 或设备实现。

### 1.2 分层

```text
Contracts / Kernel
        ↑
Domain
        ↑
Application
        ↑
Adapters / Interfaces
        ↑
Composition
```

- Domain 不导入 HTTP、composition、provider SDK 或其他领域私有 Store。
- Application 只依赖 typed ports。
- Adapter 实现网络、数据库、模型、设备和 UI 边界。
- Composition 是唯一允许绑定具体实现的位置。
- 禁止模块级可变 service registry、业务层 `get_*()` locator 和依赖 test reset 的单例。

## 2. Host、Chat 与 Ingress 合同

### 2.1 Secure Ingress

每个入口先归一化为版本化 ingress envelope，至少包含：

| 字段 | 语义 |
|---|---|
| `principal` / `surface` | 发起者与入口面 |
| `scope` | 允许的对象、动作、通道和时限 |
| `correlation_id` / `causation_id` | 跨进程因果链 |
| `idempotency_key` / `nonce` | 防重与重放保护 |
| `reply_context` | 回包平台、会话和目标 |
| `visibility` | 数据可见性和脱敏边界 |

Chat、Host Input、Gateway、Scheduler、Internal Agent 和 Operator Command 均不得绕过该入口直接调用 worker、repository 或 actuator。

### 2.2 Chat 流协议

Chat 使用三阶段语义：

```text
PRESENCE  非权威在场/进度信号，可取消，不可当作事实
COMMIT    经 Governor、执行与回复 authority 收口的可发送结果
POST      COMMIT 后的只读投影或 durable effect 进度
```

协议要求：

- `PRESENCE` 不写长期事实，不承诺工具已完成。
- `COMMIT` 必须绑定 command/graph/acceptance 或明确的只读 fastpath evidence。
- 最终文本只由 Surface/Reply authority 生成；worker、tool 和 sub-agent 只能返回结构化候选或事实。
- canonical `chat/skill/delegate/plan` 不得降级到 Executive 第二执行链。

### 2.3 输入与事件分流

- 用户消息进入 Host Input/Chat 语义链。
- Edge playback、resource、device 等事件进入独立 Event ingress。
- Scheduler/Internal Agent 使用服务身份和最小 scope。
- 查询类 `recent_turn/task_progress` 应走 projection/read service，不借执行 Runtime 维护第二任务状态。

## 3. Governed Execution 合同

### 3.1 唯一 lineage

所有生产执行遵循：

```text
Ingress
  → Governor Decision
  → HostCommand
  → WorkOrderV3 + BudgetPolicy
  → immutable TaskGraph revision
  → TaskNode / Attempt / Lease / Reservation
  → node-bound AuthorizationGrant
  → Worker
  → Verification
  → Acceptance / Review
  → Effect Intent / Receipt
```

同一 action 不得存在第二个有效 grant、第二份 Task 状态或无法关联的副作用 receipt。

### 3.2 WorkOrderV3

生产 admission 只接受显式、版本化 WorkOrder。最低字段：

- identity：work/root/parent/correlation；
- objective、domain、run mode；
- workspace owner/root/isolation；
- allowed/forbidden actions；
- approval/review policy；
- `BudgetPolicy`；
- input/context/artifact refs；
- verifier 与 acceptance contract。

`AgentWorkOrder` 等旧结构只能作为边界输入，在 admission 前无损转换；缺失预算、identity 或权限时 fail closed。

Host exact replay 必须按 `command.route_type` 校验 authority：`chat/delegate/skill` 先统一验证 WorkOrder、HostCommand、planning lineage、grant、review waiver、target 与 capability binding；只有 `skill` 叠加 direct-skill 专属 metadata 校验。不得把 direct-skill validator 无条件套到 `chat/delegate`，也不得因 durable replay 改走较弱分支。该纯校验边界由 `app/governed_task_authority.py` 拥有，Host adapter 只负责生命周期、合同编译与 Runtime 调用。

### 3.3 TaskGraph 与 revision

- TaskGraph 必须持久化 node、dependency、input/output ref、retry policy、idempotency 和 verifier。
- `completion_policy` 是 TaskGraph v1 的持久可选扩展；缺省保持 legacy node-terminal 语义。选择 `ATOMIC_GRAPH_SUCCESS_V1` 时必须同时冻结 `result_node_id`，该节点必须存在于当前图且为 sink。legacy 多 sink 图不得声明单一 result；admission、durable replay、planner semantic comparison 与 worker projection 必须校验同一 identity，不允许用 completion order 推断结果 authority。
- 未满足依赖的节点保持 `BLOCKED`；循环和未知依赖显式阻断。
- Planner 只能提交冻结 revision；worker 不得改变节点集合或依赖。
- 失败重规划沿用 `graph_id` 并创建 `revision + 1`，保留旧 revision、失败 Attempt 和证据。

### 3.4 Attempt、Lease、Retry 与并发

- 每次执行创建唯一 Attempt；retry 创建新 Attempt，不覆盖旧结果。
- Lease、heartbeat 和 fencing token 防止双 worker；takeover 必须留下恢复证据。
- retry/backoff 必须实际消费 `retry_policy` 和 retry budget。
- 所有依赖已满足节点可有界 fan-out；fan-in 在依赖全部 Accepted 后推进。
- atomic success 与 bounded fan-out 使用独立、默认关闭的进程能力开关。atomic policy 必须在 repository admission 前验证 runtime capability、acceptance-effects writer、durable projection reader、node-grant service、parent grant 与显式 `result_node_id`；不满足即 fail closed。terminal-leaf 实际并发还必须另行启用 bounded fan-out，不能从 policy 或图形状推断开关。
- cancel、timeout、crash、lease expiry 和 process restart 不得重复已确认副作用。
- TaskStore 的 `task_store_types.py` 只拥有稳定值对象，`task_record_codec.py` 只拥有确定性 JSON/SQLite Row 校验与转换；二者不得打开连接、执行 SQL、读取时钟或开始事务。`task_decision_integrity.py` 只拥有 decision/outbox lineage 的只读校验，必须借用调用方现有事务连接且只能执行 `SELECT`；不得另开连接、另建快照、提交事务或取得 mutation authority。
- Acceptance Effect 持久化按 atomic staging 与 fenced delivery 拆为无状态 mixin；mixin 不得定义 `__init__`、连接、时钟或事务 owner，只能复用 `DurableTaskStore` 的 `_connect()` / `_transaction()`。`_stage_acceptance_effect_intent_in_transaction(conn, ...)` 必须继续接受调用方当前连接，Parallel Success、Node/Graph acceptance 不得因模块拆分跨事务。
- 0091 Parallel Success 持久化 mixin 只借用主 Store authority；`complete_parallel_graph_success()` 必须只有一个 owner transaction，并以同一 `conn` 贯通 cancellation fence、Node/Attempt/projection lineage、Graph/Budget/Outbox CAS、Acceptance Effect staging、0091 insert/replay 与 decision binding。Cancellation 侧在自身事务内对 0091 的直接查询不得改成另开连接的 public getter。
- 0090 Parallel Settlement 持久化 mixin 同样只能借用一个 owner transaction；cancellation fence、完整 Node snapshot、Attempt/BudgetUsage lineage、Graph/Budget CAS、两类 Outbox 与 canonical decision insert/replay 必须使用同一 `conn`。latest reader 的 `taskbatch` marker 扫描、exact Outbox binding 和 lineage 校验必须复用同一个读连接。0090/0091/0092 共享的 Node snapshot helper 必须保持无 SQL、无 Store owner 的纯函数。
- 0092 cancellation mutation 按 request 与 advance/finalize 拆为无状态 mixin；三个写方法各自必须恰好一个 owner transaction，所有直接 SQL 和 Graph/Budget/Outbox/CAS/evidence helper 都复用该 `conn`。request/finalize 对 0091 的 first-writer 查询必须位于同一事务快照；公共 evidence builder 继续调用主 Store `_operation_record()` 完整验证 reconciliation proof，不得在 mixin 内复制或降级。
- Retry mutation 与 response-loss replay 必须分离职责但共享调用方当前 `conn`；任何 operation reservation 都在 Node/版本 mutation 前关闭自动 retry，existing BudgetUsage 是唯一 replay 入口。BudgetUsage、Budget/Attempt/Node/Lease/Outbox、retry deadline 与 legacy/node-only Graph disposition 必须在同一事务内精确校验。Atomic failure 只原子关闭 Attempt/Node/Lease、可选 Graph 和 3/4 条 Outbox，明确不得取得 Budget mutation authority；Budget finalize 仍在 bundle 外。
- DAG state 与 READY claim 拆分模块只能借用主 Store authority。`insert_graph_in_transaction()` 和 `insert_outbox_in_transaction()` 必须要求调用方提供已开启事务的 `conn`，不得自行 begin/commit；scheduler snapshot 必须在同一个只读事务内取得 Graph、Node、Attempt、Lease、Budget 与 usage。READY claim 必须在一个 writer transaction 中复核 cancellation、Graph/Budget/dependency/capacity，提交 Attempt、Node、Lease、初始 Heartbeat 与三条 Outbox；response-loss replay 必须复用该 `conn`，不得再占并发槽、取得新时钟或写入第二份事实。Retry promotion 与 claim 当前仍是两个各自原子的事务，不得把结构拆分描述成已合并。
- Coordinator 的 record validation、不可变 execution receipt/terminal rendering 与 cancellation payload/state 纯投影必须位于纯叶子模块；这些模块不得持有 TaskStore、runtime port、锁、task、Coordinator 实例或绑定回调，也不得反向导入 `coordinator.py`。`GovernedExecutionReceipt` 的旧 Coordinator/package 导入必须与新定义保持 object identity。`GraphCancellationProjection` 是独立的只读 stateful leaf，只能持有 TaskStore 并调用冻结的只读方法白名单；`GraphCancellationRuntime` 只能持有同一个 TaskStore、共享 `RuntimeOutbox`、共享 `LocalGraphExecutionRegistry`、lease TTL 与该 Projection，不得反向持有 Coordinator、绑定方法、callback 或新增锁。Coordinator 的公开取消方法只作薄委托，执行重入直接委托该 Runtime continuation。
- `RuntimeOutbox` 是每个 Coordinator 唯一的进程内 Outbox 构造/发布锁域，并与 `GraphCancellationRuntime` 共享同一实例：message identity 必须继续绑定 command/type/id/version/event 五段材料，seed/final evidence 不得改变 outbox id；同步/异步 publisher 共用同一锁，best-effort 只能降级普通异常，不能吞 `CancelledError`。`LocalGraphExecutionRegistry` 只持 graph+revision→Task 集合与一把锁；`execute_stream` 的完整迭代必须位于 `track()` 作用域，cancel/wait/gather 必须在锁外，正常、异常、外部取消和 async-generator `aclose` 都必须清空注册。Registry 不是取消 authority；只有 durable intent 已提交或 exact replay 且 binding 校验通过后，才允许先尝试 publish、再在 `finally` 本地 cancel/join、最后 continue。

### 3.4.1 Durable cancellation

- Application 取消入口必须先由 `GraphCancellationAuthorityVerifierPort` 验证 request、principal/source/reason 与 exact `graph_id + revision`；没有 verifier、绑定漂移或无法解析既有 durable admission 时 fail closed。该内部入口在生产接线完成前不得暴露为 HTTP/UI API。
- 取消是两阶段 Graph authority。`request_graph_cancellation` 在一个 Store UoW 中写入 canonical intent，将 Graph 推入 `BLOCKED`，并为非终态 Node、active Attempt/Lease、Review/resume token、operation 与未授权 effect 建立取消或 reconciliation 状态；只有 durable intent 返回后，`GraphCancellationRuntime` 才能通过共享 registry cancel/join 本进程 child 与 heartbeat，Outbox publisher 异常不得阻止该本地停工。已有 intent 仅允许 `request_id/request_identity/source/reason_code` 四字段完全相同的 replay。完整 reconciliation evidence 由 canonical decision 摘要绑定，operation proof 在封口与 getter 中完整验签；终态 Outbox 只保留 decision、intent、request identity 三个稳定 manifest authority ref，不展开可增长的证据清单。
- 本地 task registry 只用于加速 join，不是 authority。重启或另一进程只能依据 0092 intent/decision、Graph/Node/Attempt/Lease/operation/effect/review durable facts推进；不能因本进程没有 task handle 就假定取消已经完成。
- `advance_graph_cancellation` 只能在 ownership 已稳定后推进 `CANCEL_REQUESTED`；`RESERVED/RECOVERY_REQUIRED` operation、待对账 effect、active Attempt/Lease 或 live Review/token 均阻断 finalization。`finalize_graph_cancellation` 必须在单一 UoW 中提交 Graph `CANCELLED`、Budget `RELEASED/CANCELLED`、Outbox、evidence 和 canonical decision。
- `ReviewStore` 的 create/approve/resume 必须在自身 `BEGIN IMMEDIATE` 内检查 exact `task-node://graph/revision/node` 是否已有 0092 intent；Graph/Node URI 段统一使用 canonical percent-encoding，TaskStore 撤销、live 查询和 evidence 查询必须复用同一编码口径。同 revision 存在 intent 时 fail closed，Review 先提交则由随后取消事务撤销。不得用 Graph ID 前缀误伤相邻 revision。
- NODE effect 若绑定 `ACCEPTED` Node 与 `SUCCEEDED` Attempt，视为已提交的 acceptance authority，取消后继续派发/重试；其他 NODE effect 与未完成 Graph 的 GRAPH effect 必须被 fence。final Graph 取消不等待已验收 effect 的外部 delivery 完成。
- 0091 success 和 0092 cancellation 采用 first-writer-wins：已存在 terminal success 时拒绝取消，已存在 cancellation intent 时禁止新成功/claim/charge/未授权 COMMIT。request 与 finalize 的响应丢失只能 exact replay；advance 必须从当前 durable facts 幂等收敛，任意 identity、version、snapshot、Outbox 或 evidence 漂移均 fail closed。
- 调用方断开、进程 shutdown 等基础设施 `CancelledError` 原样传播；worker 自发取消转为 `RECOVERY_REQUIRED`，bounded fan-out supervisor 必须立即停批并 cancel/join 未完成 siblings，Graph 保持 `ACTIVE` 等待 durable recovery。两者都不能绕过 verified authority 建立业务取消。

### 3.5 Budget

Budget ledger 支持 token、tool call、time、concurrency、retry 和 model cost 的 reserve/charge/release/reconcile。

- provider 未返回 usage 时记录 `UNMEASURED`，不得伪造精确值。
- 超限策略 `BLOCK/DEGRADE/REVIEW` 必须有确定性分支和 receipt。
- model retry、tool retry 与 Task retry 都进入同一 root budget。
- Planner、worker 和 provider 不能自行扩大预算。

### 3.6 Review 与 continuation

- PRE_EXECUTION 与 POST_EXECUTION/ACCEPTANCE Review 使用不同状态。
- Resume token 一次性，绑定 subject、version、checkpoint、principal、expiry 和 allowed action。
- 合法 resume 重新调度原图的可继续节点，不重放已确认 effect。
- 拒绝、过期、主体不匹配、版本漂移和重复消费均 fail closed。

### 3.7 Verification 与 Acceptance

- required verification 只有 `PASS` 可进入 Acceptance。
- `FAIL/ERROR/NOT_RUN` 必须阻断或进入显式 Review。
- Acceptance 绑定 verifier result、artifact digest、graph/node/attempt、grant 和预算结算。
- handler 返回成功不能代替 Acceptance。

### 3.8 副作用与一致性

- 外部副作用只能由 Acceptance 同事务写入 durable effect intents 后执行。
- 多节点全成功的外层 UoW 必须一次提交 `Graph COMPLETED`、共享 `Budget CONSUMED`、Graph/Budget 两条不同 Outbox、整图 effect intents 与 canonical success decision；任何晚故障必须整体回滚，响应丢失只允许 identity 完全一致的 exact replay。
- 整图 effect intents 和最终公开 projection 必须从显式结果节点已经冻结并通过 canonical digest、command/request、route/target、attempt/receipt 与 node-grant lineage 校验的 durable worker projection 编译；其他 Accepted 节点只提供 outcome/evidence，不得争夺结果 authority。Skill 节点同样不得绕过该冻结投影读取瞬时 executor 返回值。Graph 已完成后的 replay 只读 canonical success decision，不重新编译 effect intents。
- dispatcher 对每个 effect 独立 claim、retry、dedupe 和出 receipt。
- 对话、Memory、Life、Gateway 和 Edge 可以是 Acceptance 后最终一致，但必须公开该语义；不得宣称跨系统单事务。
- fire-and-forget task 不得承担不可恢复的 canonical 写入。

## 4. Surface 与 Digital Life 合同

### 4.1 Surface

Surface 输入只包含已裁决事实、授权记忆引用、Scene/Emotion/Relationship 控制和 worker 结构化结果。Surface 可以调整表达，不得：

- 新增事实或升级承诺强度；
- 把 Canon/风格/情绪/候选当作已执行事实；
- 泄漏 raw thought、secret 或未授权私密正文；
- 反写任务、权限、World 或 Memory authority。

### 4.2 Life 状态

Life 由 Command/Event/Reducer/Projection 推进；Surface、Tribe、Scheduler、Inner World 和后台循环只提交贡献或命令，不各自推进平行生命时钟。

用户观察、Natsume Affect、Relationship、Drive、Scene 与 Identity 使用不同 typed state 和明确 writer。重放同一事件流必须得到兼容版本下确定性投影。

### 4.3 Persona 演化

交互统计、里程碑、反馈和模型总结只能生成 proposal：

```text
evidence
  → FeedbackProposal
  → Lab / shadow / regression
  → Review
  → versioned policy or identity activation
  → rollback point
```

禁止后台任务或模型输出直接调用 trait writer。

## 5. Memory、World 与 Knowledge 合同

### 5.1 Memory

- Life Memory 是生活记忆唯一写服务。
- Episodic/Semantic/Vector/Graph/Cache 有明确 source/projection 关系。
- Recall 返回 evidence refs、visibility、confidence、freshness 和 conflict；只读 Context 构造不能写 Memory。
- distillation、forgetting、merge 和 promotion 使用 version/CAS 和审计。

### 5.2 World、Scene 与 Intent

`WorldFact` 至少包含 subject、predicate、value ref、source refs、confidence、observed/valid/expires 和 conflict state。`WorldSnapshot` 绑定 person/workspace/project、fact versions 和 digest。

- World authority 接受受治理 Command；消费者只读版本化 projection。
- Scene transition 写 journal，并输出 expression/proactive/risk constraints 与 TTL。
- Intent Arbiter 合并显式用户目标、任务、关系、场景、风险和权限。
- Scene/Affect 只能收紧，不能取消用户已授权目标或放宽 Governor。

### 5.3 Knowledge OS

Life Memory 与 Project Knowledge 分 namespace。`KnowledgeItem` 具有 provenance、version、validity、confidence、conflict、decision refs 和 promotion state；支持 dedupe、staleness、deprecation 与 rollback。

重大架构和产品选择生成 Decision Record；Canon 不作为普通项目事实数据库。

## 6. Planning and Decision 合同

### 6.1 公共边界

公共 PlanningSearch 只依赖以下 ports：

- `SuccessorCatalogPort`：按虚拟状态动态生成后继；
- `CognitiveRankingPort`：领域认知评分；
- `VirtualTransitionPort`：纯虚拟状态变换；
- `RewardPort`：领域 outcome/reward；
- `MCTSConditionPort`：决定是否进入 L3；
- `CalibrationRepository`：持久 baseline/candidate/prediction/actual outcome/cost。

公共层没有工具执行、网络、TokenService、Memory writer 或 actuator。

### 6.2 三层搜索

- L1：从当前虚拟状态生成合法候选，不只排列根节点候选。
- L2：结合领域模型、经验和约束排序；领域提示词留在 adapter。
- L3：在阈值满足时执行有预算、确定性的 selection/rollout/backpropagation。
- SearchResult 必须编译为 node-bound immutable TaskGraph payload。

### 6.3 Shadow 与 promotion

- synthetic fixture 只用于算法回归。
- production shadow 使用真实 workload、model、source/repo、verifier、成本和延迟。
- 未执行的替代路径不能用 baseline outcome 冒充；需要隔离实验或可验证模拟。
- 达到预注册样本、误差、安全与净收益阈值后，只生成 influence proposal；实际切流仍经 Review。

## 7. Capability、Domain 与 Pentest 合同

### 7.1 Capability lifecycle

```text
source material / accepted trace
  → SkillDraft
  → contracts + verifier + test vectors
  → SBOM + signature
  → quarantine sandbox
  → Review
  → staged activation
  → evidence-based promotion / rollback
```

未经签名、SBOM、隔离验证或审批的 bundle 不可 import/activate。成熟度策略只收紧；自动升级为 0。

### 7.2 Domain Pack

Pack 可提供 manifest、candidate/tool、repository port、knowledge namespace、verifier、risk、benchmark 和 UI contribution。Pack 不拥有 TaskGraph、Scheduler、Governor、Review、Model Gateway、EventBus、Persona 或跨域 Store。

### 7.3 Pentest

- 所有 Run 绑定真实 principal、engagement manifest、target/scope、时间窗和速率。
- side-effect 阶段必须有有效 approval 和执行前 PreActionGate。
- AttackGraph、CVE、工具知识和认知提示留在 Pentest adapter。
- 虚拟 rollout 禁止真实工具；L3 在 durable calibration Gate 前保持 shadow。
- Pentest 知识进入领域 Store；跨域 promotion 走 Canon/Knowledge proposal。

## 8. Gateway、Edge 与设备合同

### 8.1 Gateway

- raw-body 签名/认证在正文解析前完成。
- inbox/outbox/attempt/dedupe/DLQ 使用 durable store；adapter 不持第二 retry 真相。
- 本地 adapter 成功与远端 provider receipt 分开记录。
- reply 使用独立身份和密钥，不回退通用 Edge 凭据。

### 8.2 Edge

- Edge 只依赖版本化共享合同，可独立安装和升级。
- capability handshake 声明协议、设备身份、支持动作和降级状态。
- 物理效果只有一个 actuator owner；重连不会重放已完成命令。
- loopback bridge 默认认证；远程暴露必须显式配置、最小 scope 和审计。

## 9. Query、Observability 与 Operator 合同

- Query Plane 只读 projection，不直接查询业务私有表或全局内存对象。
- Read model 包含 schema version、source cursor、freshness、visibility 和 degraded state。
- UI 写操作构造 Operator Command，重新进入 Secure Ingress。
- Trace 能串联 command、graph/revision、node、attempt、model/tool attempt、verification、acceptance、effect 和 receipt。
- raw thought、secret、cookie 与未授权私密记忆不进入 UI 或 durable trace。

## 10. Model 与 Prompt 合同

模型调用统一为：

```text
ModelRequest
  → RouteDecision
  → ProviderAttempt(s)
  → StructuredResult / Failure
```

- route、timeout、retry、fallback、circuit breaker、usage、cost 与 reason code 可回放。
- 每个 provider attempt 绑定 root Budget 与 trace。
- Prompt 模板版本化，声明 owner、input slots、output schema、visibility 和 rollback。
- 业务模块不得绕过 Model Plane 调私有 provider helper。

## 11. Evolution Lab、Feedback 与 Identity 合同

### 11.1 Evolution Lab

`ExperimentSpec` 绑定 dataset/version、seed、policy variants、budget、isolation、metrics 和 evidence。Lab 只能读取脱敏快照/trace，不能持有生产 writer、Capability side-effect port 或 Edge actuator。

Scenario、benchmark、shadow 和 counterfactual 只输出 `ExperimentReport`/proposal，不直接切生产。

### 11.2 Feedback

成功、失败、用户拒绝、工具可靠性和表达偏好统一转为 `FeedbackProposal`。Proposal 绑定 trace/outcome/confidence/suggested change/rollback，经过 Lab、Review 后才更新版本化 policy、maturity、knowledge 或 identity。

异常不得被吞掉；失败 feedback path 必须进入可观测 evidence。

### 11.3 Identity

每次 release 生成 `IdentitySnapshot`，只包含公开身份、人格核心、关系/表达/主动性/主权 policy 的版本与 digest，不包含记忆正文和私密 thought。

Identity Migration 在隔离环境执行，产出 diff、persona/relationship regression、Review candidate 和 rollback plan。重大差异默认阻断并要求用户确认。

## 12. Runtime Guardian 与 Workspace 合同

### 12.1 Runtime Guardian

统一 dependency health、heartbeat、degradation、recovery、maintenance/sleep 和 SLO evidence。进入/退出降级必须产生 versioned incident；局部 fallback 不能静默覆盖全局状态。

### 12.2 Workspace

- 每个 Task/Experiment 绑定 owner、root、allowed paths、isolation mode 和 cleanup receipt。
- 使用解析后的路径边界，禁止字符串前缀判断逃逸。
- Code/Skill 实验使用隔离 worktree/container；主工作区和 secret 默认只读。
- subprocess 不是安全 sandbox；需要执行不可信材料时必须使用可验证隔离。

## 13. Schema、Migration 与恢复合同

- durable schema 只有中央、forward-only migration catalog 可修改；service startup 不 inline DDL。
- migration 绑定 id/name/checksum，支持 empty/old/current DB、backup/restore 和 crash recovery。
- 破坏性变更采用 expand → backfill → dual-read/controlled-write → cutover → verify → retire。
- 每个写模型使用 version/CAS、idempotency 和 transactional outbox；projection 可从 authority 重建。

## 14. 验证与质量 Gate

每个 vertical slice 至少验证：

1. 正常路径和 fail-closed 负路径；
2. identity/scope/budget/review/version 绑定；
3. restart、retry、duplicate、cancel 和 rollback；
4. 无第二 writer、第二执行 authority 或不可恢复 fire-and-forget；
5. receipt、trace、privacy 与 operator projection。

算法或模拟测试只能证明其覆盖的合同。生产切流还需要真实 composition、真实 admission 和至少一条真实 golden path。

## 15. 兼容与退役合同

- 兼容 facade 必须声明 owner、usage telemetry、remove-by condition 和 fail-closed 边界。
- 新任务切到新 runtime 后，旧任务由旧 executor drain；不得跨 executor 恢复。
- usage 为 0 且稳定窗口通过后删除旧 writer、registry、route、migration adapter 和测试。
- 不允许长期 dual-write、长期 shadow authority 或“改名后的全局 locator”。

## 16. 状态与执行分离

| 问题 | 唯一来源 |
|---|---|
| 产品必须提供什么 | `../20_prd/PRD.md` |
| 工程必须遵守什么 | 本文 |
| 代码现在是什么 | `../50_project/arch/CURRENT_CODE_ARCHITECTURE.md` |
| 最终架构是什么 | `../50_project/arch/TARGET_ARCHITECTURE.md` |
| 还差什么 | `../50_project/arch/ARCHITECTURE_GAP_MATRIX.md` |
| 当前按什么顺序实施 | `NEXT_GENERATION_EVOLUTION_EXECUTION.md` |
| 历史为何这样做 | `../_history/` 与固定 receipt |

DevSpec 不再保存 `DONE/CODE_COMPLETE`、旧阶段编号或任务进度。任何完成结论必须来自当前代码审计和绑定 commit/environment 的机器证据。
