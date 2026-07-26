受众：MASTER
标题：Natsume AI 功能模块与责任边界

# Natsume AI 功能模块与责任边界

版本：v2.29
核对日期：2026-07-27
原始审计基线：`2b84ae9ad`；当前模块事实以本文所在提交的工作树为准
文档类型：`CURRENT MODULE MAP / TARGET BOUNDARIES`

本文从生产入口和实际调用链反向说明“系统现在有哪些功能模块、各自应该管什么、当前边界哪里没有闭环”。它不以目录数量、文件数量或历史里程碑作为完成证据。

Skill Execution 模块的唯一生产 owner 是
[`app/governed_skill_execution.py`](../../../host_brain/app/governed_skill_execution.py)：
它消费 frozen node authority、调用 `SkillMeta.handler`、绑定 sealed
authority、进入 Capability Broker，并把结果交回 Task Runtime。
[`skills/loader.py`](../../../host_brain/skills/loader.py) 只是迁移兼容 facade，
所有 `execute()` 都以 `GOVERNED_SKILL_TASK_AUTHORITY_REQUIRED` 拒绝；
Executive executor 已删除，Scheduler 也不得直接调用 Skill metadata。
干净提交 `3d68bd1e` 的定向、真实 GR05、static/doc 与交付元数据证据使
该模块的 canonical execution authority 为 `PASS`；预算和事务副作用仍由
后续切片处理。

当前责任边界由 Host/Agent/Context/Startup typed ports 与 router/runtime factory 定义。生产 locator consumer/import/module/field/lock/ref 已全部清零；ASGI/CLI fresh 构造 `ProcessApplicationPorts`，Host builder 不再回读全局状态。`HostAuthorityRuntimeOwner` 管理 canonical ledger、Episodic/LifeMemory、Prompt/Route/Provider/ModelAttempt 与 Capability runtime；pytest-only bridge 不进入生产对象图。ResourceManager status 已接入 0123 Host-fenced journal。Chat 的本地 assistant/LifeMemory 投影屏障与七路线 commit-bound acceptance 已闭合，但外部 namespace、Token/MCP durable restart、其它外部资源恢复、完整 Worker/Obs Composition、路线其余控制与 NE-01 仍未闭环。

Route Acceptance 模块分为三个只读责任：`persistence/universal_route_acceptance_reader.py` 只负责从一个真实 SQLite snapshot 重开 bounded facts；`release/universal_route_acceptance.py` 只负责 Git/migration 绑定、七路判定、隐私字段白名单和 digest；`scripts/runtime/collect_universal_runtime_acceptance.py` 只负责参数解析与原子写出本地证据。它们都没有 Task、Budget、Review、Effect 或 Outbox writer。Coverage Gate 只接受可通过 v3 validator 的真实收据结构；当前正式收据绑定干净提交 `3d68bd1e`、migration 0135 和七路 PASS，使七个提交绑定控制及 Chat model budget 均为 `PASS`。Chat 因此完整，但不会覆盖其余路线控制。

Model Budget Evidence 模块不新增 writer。`ModelAttemptService` 和 ledger audit 仍拥有 provider 调用生命周期，Surface 只把成功调用的 identity 与 measurement 放入 WorkerProjection，Task Runtime 的 Budget owner 继续原子写 parent/child usage；`UniversalRouteAcceptanceReader` 只抽取隐私有界证据，release validator 只做跨表等值和生命周期校验。`governed_model_budget` 是执行期 guard，不属于 Life/Effect payload；executor 的 audit path 属于 executor，不得被 worker wrapper 覆盖。当前 GR05 只替换 terminal provider transport，证明模块接线和 replay，不证明外部模型质量。

Local Acceptance Driver 不属于 Host 生产 composition。`tools/acceptance/universal_remaining_routes.py` 只在显式验收命令中构造 app-local Scheduler/Internal/Operator Runtime，写入调用者指定的隔离 SQLite；`scripts/runtime/run_universal_remaining_route_acceptance.py` 负责 CLI 和 request map。Scheduler、Internal、Operator 仍使用各自正式 CommandService、Task coordinator、Preview、Effect 和 claim owner；确定性 worker 有显式 `bounded_local_acceptance` 标识，不能被 Model/PlanningSearch/NE-07 Gate 消费。

Node Budget 模块把总额度所有权与节点执行归因分开。`task_budget_usage_store.py` 继续拥有 Graph Budget 的 aggregate CAS、首个 EXCEEDED winner 与 terminal observation；`task_node_budget_store.py` 只在同一 TaskStore UoW 内创建和读取 Attempt-bound child reservation，`task_node_budget_usage_bridge.py` 负责把每次父级 usage 精确映射到 child append-only ledger，`task_budget_terminal_sibling.py` 隔离共享终态 observation。`BudgetRuntimeContext.bind_node_attempt()` 在 worker 取得执行权前要求 exact child，`node_execution_runtime.py` 在新 claim、Operation continuation与 retry 三条入口都完成绑定。child 终态由 Attempt/parent 状态派生，不拥有独立 Graph 终结权；0132 缺失、身份漂移或父子落账不原子时失败关闭。

Node Verification 模块由三个 focused owner 组成：`task_node_verification_common.py` 只负责合同、digest 和 subject 规范；`task_node_verification_store.py` 在一个 UoW 中写 Node transition、append-only decision 与 Node Outbox，并提供只读重开；`task_node_verification_terminal_store.py` 独占直接拒绝的 Node/Graph/Budget 原子终态。`operation_checkpoint_verification.py` 只读取 decision，不拥有 writer。`node_execution_runtime.py` 与 committed-checkpoint continuation 是当前生产调用方；通用低层 `transition_node()` 仅保留兼容边界，不能作为 verifier 的生产 writer。为避免 canonical repository 继续膨胀，基础 schema inventory 已迁到无 I/O 的 `task_repository_schema.py`，`task_repository.py` 回落到 4600 行既有上限内，未扩大 Gate 3 白名单。

Node Acceptance 模块同样不拥有第二套 Runtime。`task_node_acceptance_common.py` 负责 judgement/digest/subject 合同，`task_node_acceptance_store.py` 在 canonical TaskStore connection 上写 append-only decision，并原子协调 Node、Review、直接 Graph block 与 Outbox；`task_node_artifact_store.py` 把自动接纳的 decision、artifact、可选 Effect 和 Node 放在同一 UoW。`review_pending_uow.py` 是 ReviewStore 与 TaskStore 共用的唯一 pending Review INSERT owner。`review_runtime.py` 只准备未持久的合同对象和读取已提交 gate，Scheduler/Continuation 只重开 exact decision，不拥有 judge 或 writer。Review denial terminal bundle 仍由既有窄 continuation 处理，尚未合并进该原子边界。

Plan Review Policy 模块的职责分为三层：Capability adapter 只从 registry 冻结 `requires_review`，Planning domain 只携带候选级来源，Graph compiler 独占 candidate→TaskNode ID 重绑定。`review_policy.py` 负责合同解析和完整覆盖失败关闭，`TaskNodeReviewRuntime`/Scheduler 负责逐节点门禁，ReviewStore 与 0096 worker 仍分别拥有决定和续跑协调。`ParallelSuccessRuntime` 不修改审批，而只核对 WorkerProjection 中冻结的历史/续签 grant authority。Budget、搜索算法和 worker 都不能自行关闭审批。

Delegate Dispatch 模块当前只有一个正向生产 target：只读 `pentest_agent`。Registry 负责冻结 `PORT_FREE_READ_ONLY` execution mode，dispatcher 负责调用 effect-free Domain Pack/SecureRun adapter 并签发完整 lineage-bound usage attestation；Frozen Route Executor 只负责重验 attestation 并转换为 WorkerBudgetUsage。模型、工具、嵌套派发与 Effect port 不属于该 adapter，legacy Agent trace 也不属于预算 authority。未来任何有资源消耗的 Delegate 必须新增受治理 target 合同与实际 measurement，不能复用该零用量证明。

Worker Evidence 模块只负责对 durable WorkerProjection 做独立验证与验收，不执行 worker、不写 Task 状态，也不拥有领域语义。它复验 route-neutral immutable identity、grant、WorkOrder/context、receipt/COMMIT 与输出合同，并把未知语义标准交给 Review。Node Input Attestation 合同只规范前驱 TaskResult artifact 的 ref、receipt 与 payload digest；Plan Chat adapter 负责将这组已验收产物转换为唯一合成证据，不能混入普通 Memory retrieval。Task Runtime 仍是 Attempt/Lease/Budget/Review/Acceptance 的唯一状态 owner。

Conversation Acceptance 模块现在明确分为三个 owner：canonical TaskStore 负责有序 Effect staging，Digital Life command repository 负责 command/event/intent 原子入账，`LifeProjectionDispatcher + life_projection_barrier` 负责 exact intent 执行与回执集合校验。`surface.post_process` 继续调用真实 app-local AgentRuntime ports，只有 assistant turn 和 LifeMemory admission 均持久成功才可完成 projection。Gateway transport仍由后序 delivery Effect 拥有，任何本地投影 UNKNOWN 都会阻断它；这样没有新增第二个 Conversation writer，也没有让 Life command receipt越权成为投影成功证明。

情感上下文模块的当前 owner 是 Host-local `HostEmotionRuntime`：它管理连续情绪向量、前一标签、Timeline、用户画像和日记节流；Input、Tribe 与 Lifecycle 只通过注入端口访问同一实例。`normalize_chat_emotion_context()` 是 replay/render 只读入口，`resolve_chat_emotion_context()` 是要求 exact runtime 的 admission ingest 入口。回复形态模块的 `ResponseFormDecider` 由 Input composition 独占，不再提供 module-global getter；语音额度为实例内原子状态。两者均不拥有 Persona/Feedback 的永久写 authority。

Internal terminal recovery 模块除 #114 的精确后置审核取消外，当前还拥有一个独立的 signed raw-failure claim contract：它只从无取消意图、零投影/Effect、单节点和单 Budget 的原子失败终态生成 deterministic result，并要求先结算 Secure Ingress claim、再清理 Workspace/stage。该模块不拥有一般 failure/cancellation/multi-node/unknown-effect recovery authority。

Operation reconciliation 模块现由五个互不越权的 owner 组成：0126 evidence store 负责写入时验签和 append-only 保存，`OperationReacquisitionService` 负责同 Attempt/higher-fence/Budget segment 的重获规则，`OperationCheckpointReconciliationService` 只确认 canonical WorkerProjection checkpoint，`OperationFailureReconciliationService` 处理精确无副作用节点失败，route-specific continuation 负责继续原图或消费已终结结果。单节点 failure Store 可终结整图；多节点只在第一 UoW 关闭 Operation/Attempt/Node/Lease，Graph/Budget 继续由 0090 parallel-settlement owner 终结。Operator/TaskGraph 只传 evidence ref/digest，Accepted handler 在执行时重开完整证据；v4 receipt 只绑定不可变 node failure，故 0090 从 PENDING 到 COMMITTED 不改变摘要。0127 禁止 WorkerProjection UPDATE/DELETE。任何模块都不能靠 ref、Preview 或 Review 单独取得执行权。

Operation recovery hold 模块只负责“持久停下”，不负责“决定结果”。`task_operation_recovery_hold_codec.py` 定义规范 subject 与 digest，support 在同一 snapshot 重开 Graph/Node/Attempt/Operation/Budget/cancellation/active Lease；store mixin 的 transaction-local helper 现在由 `_mark_operation_recovery_required()` 直接调用，使 Operation marker、Operation Outbox、0130 append-only case 和 case Outbox同成同败。`operation_interruption_case.py` 只读重开 marker 同事务产生的唯一 case，`operation_interruption_runtime.py` 负责把 case ref/digest 投影到 recovery receipt，不另写 authority。current helper 对 unknown/cancellation case以 exact Operation version与取消身份为未决核心，因此 Budget settlement、Lease expiry和 lifecycle fence不会让 case消失；Graph cancellation 追加 intent-bound successor，旧 unknown case变为历史。checkpoint conflict仍复验完整冻结快照。0131 由 `task_operation_reconciliation_evidence_store.py` 把唯一 current case 精确绑定到 append-only signed evidence，`operation_reconciliation_acceptance_effect.py` 作为 authority-free compiler validator 将 case identity 冻结到 Acceptance Effect，Accepted handler 在原 Operation UoW 完成后验证 hold 已关闭。任何一条都不能创建 worker replay authority，精确 COMMITTED checkpoint continuation仍由唯一 DAG/operation checkpoint owner执行。

Edge command 模块不再拥有进程级可发现 owner。Motor/Limbic/Cortex 仍只负责计划/观察，内部调试入口无权直连网络；Obs `edge.command.push` 的首条窄路径已经由 governed Operator TaskNode 在 Acceptance 后生成 `edge.embodiment_enqueue`，把 `edge.somatic + emotion_state_command` 写入 app-local durable outbox。物理 dispatch 与 receipt reconciliation 仍由 `HostEmbodimentRuntime` 独立负责，真实 Edge/Gateway canary 尚未执行。

Operator Task control 模块的当前边界是“公开命令负责准入，原 Task/Review owner 负责最终写”。`governed_operator_control_support.py` 冻结 `task_graph.cancel` 与 `task_review.approve/reject/resume` 输入，`operator_control_acceptance_effect.py` 只编译 exact terminal Effect，`AcceptedOperatorControlHandler` 在 Acceptance 后重开 Secure Ingress authority 并调用 app-local Runtime Group 或共享 `TaskReviewCommandService`。Review repository 只持 public token ref/hash，不持 bearer；普通 API 和 Operator public-token API 是两个互斥入口。任何 Obs handler、compiler、Preview 或 Effect ref 都不取得第二个 Task/Review writer。

Scheduler 管理模块的公开 HTTP 现在只负责 Secure Ingress 与治理准入。`scheduler_routes.py` 在 `/scheduler`、`/host-scheduler` 两个前缀上把 create/update/delete/enable/disable 交给 app-local `GovernedOperatorStateControlRuntime`；该 runtime 冻结 method/path/body/head，生成 `SCHEDULER` HostCommand/TaskGraph。`AcceptedOperatorStateControlHandler` 只在 Acceptance 后调用 `SchedulerCommandService.apply_accepted_*`，并以不可变 admin operation receipt 封口 claim。路由不再拥有 definition writer；只读 list/get/health 与 governed trigger 分别保持原职责。

Direct Agent Skill Workspace 模块由五个窄 owner 组成：`governed_skill_work_order.py` 只从已验证节点上下文重建写 `WorkOrderV3`；`artifact_store.py` 只密封不可变 stage；231 行 `agent_workspace_acceptance_effect.py` 只做无 I/O 的 descriptor/lineage 校验；1499 行 canonical `acceptance_effects.py` 只把结果编排为 graph-scope promotion Effect；`accepted_workspace_artifact.py` 只在 Acceptance 后重开 Task、projection、终态 lineage、Preview、Effect 和 seal并调用共享 WorkspaceRuntime。Host/Tool composition 只注入 app-local ports，公共 Skill payload 没有 owner 选择权；结构拆分未扩大 allowlist。该模块当前只服务 `agent_worker/self_heal/self_evolve`，不拥有 Domain Pack、任意 Skill、一般 unknown-effect 或自动源分支合并 authority。

权威分工：

- 当前架构完成度看 [CURRENT_CODE_ARCHITECTURE.md](CURRENT_CODE_ARCHITECTURE.md)。
- 最终边界看 [TARGET_ARCHITECTURE.md](TARGET_ARCHITECTURE.md)。
- 当前缺口和实施波次看 [ARCHITECTURE_GAP_MATRIX.md](ARCHITECTURE_GAP_MATRIX.md)。
- 本文只维护“功能 → owner → 入口 → 依赖 → 缺口”的可导航地图。

## 1. 状态口径

| 状态 | 本文含义 |
|---|---|
| `WIRED` | 已被生产 composition 构造，且真实入口能达到该模块 |
| `PARTIAL` | 有生产实现或局部接线，但范围、策略或恢复语义未闭环 |
| `BROKEN` | 入口已接线，但当前合同不兼容，真实调用会失败 |
| `DEFAULT_OFF` | 有消费链或实现，但生产默认配置关闭 |
| `SHADOW` | 可计算或记录，不影响生产决策 |
| `LEGACY` | 仍被兼容路径使用，不是目标 authority |
| `MISSING` | 尚无满足目标语义的生产实现 |

“源码存在”和“模块已成为生产权威”不是同一件事。例如 PlanningSearch 内核存在，不代表 Host 已能生成竞争计划；World Model 查询资产存在，不代表它已是规划世界模型。兼容 World projection 只保留 `(key, source_ref)` 来源事实：不同值会形成只读冲突组，同值多来源仅是佐证，不能作为新的 Scene 或执行 authority。

## 2. 生产运行面

| 运行面 | 实际入口 | 主要责任 | 边界和当前状态 |
|---|---|---|---|
| Host Main | [`host_brain/__main__.py`](../../../host_brain/__main__.py) → [`host_brain/app/asgi.py`](../../../host_brain/app/asgi.py) | Chat、Host Input、治理执行、Task/Agent Runtime、Life、Memory、Pentest 和业务 API | `WIRED / COMPOSITION PARTIAL`；ASGI 仍拥有 lifespan/后台任务，Internal/Workspace/Operator/runtime-group compose/activate 与 Runtime Context/Action Preview/Guardian/Catalog 接线已拆入 focused wiring。`ServiceHealthHeartbeat` 由每个 lifespan 单独拥有 task/observer/probe cache/HTTP transport；无 observer 的 compatibility probe 不可写全局 degradation reducer，独立 Obs 只读 durable health projection，兼容顶层 helper 不是 Host authority，其他兼容责任仍待清零 |
| Observability | [`host_brain/obs_main.py`](../../../host_brain/obs_main.py) → [`host_brain/app/obs_fastapi_app.py`](../../../host_brain/app/obs_fastapi_app.py) | 读模型、健康、Trace、回放、Operator Query/BFF | `WIRED / PARTIAL ISOLATION`；有独立进程，仍复用部分全局对象图 |
| Monitor UI | [`host_brain/app/monitor_ui/src/App.jsx`](../../../host_brain/app/monitor_ui/src/App.jsx) | 数字生命驾驶舱、观测、运维、执行审查、安全与设备控制 | `WIRED`；路由以 [`routeManifest.jsx`](../../../host_brain/app/monitor_ui/src/app/routeManifest.jsx) 为准 |
| Edge RPC | [`edge_client/__main__.py`](../../../edge_client/__main__.py) → [`edge_client/app/fastapi_app.py`](../../../edge_client/app/fastapi_app.py) | ASR、TTS、Avatar、Desktop、截图、自动化和本地设备 actuator | `WIRED / ENVIRONMENT_DEPENDENT` |
| Unified Gateway | [`edge_client/gateway/bootstrap.py`](../../../edge_client/gateway/bootstrap.py) | QQ、微信、飞书、企微、Telegram、LINE 等平台的验签、归一化、Host 回流和耐久投递 | `WIRED / PLATFORM_DEPENDENT` |
| Desktop Pet | [`desktop_pet/main.cjs`](../../../desktop_pet/main.cjs) → [`desktop_pet/renderer/pet.js`](../../../desktop_pet/renderer/pet.js) | Electron 窗口、Live2D 动作/表情、安全本地 bridge | `WIRED / DEVICE_DEPENDENT` |
| External Services | [`scripts/runtime/docker-compose.external.yml`](../../../scripts/runtime/docker-compose.external.yml) | Neo4j、Chroma、Redis 外部依赖 | `OPTIONAL / DEGRADABLE`；SQLite 仍承担多数 canonical journal |

启停编排入口是 [`scripts/runtime/start_distributed_services.ps1`](../../../scripts/runtime/start_distributed_services.ps1)，Host 和 Edge 也有独立启停脚本。脚本是部署 adapter，不是业务状态 authority。

## 3. 面向用户的功能与后端 owner

| 功能域 | 外部入口/呈现 | 当前主 owner | 主要下游 | 当前裁决 |
|---|---|---|---|---|
| 对话与多模态输入 | `/v1/chat`、`/v1/host/input`、`/v1/edge/events` | `input_runtime`、`agent_runtime/cortex_runtime.py`、`surface_runtime`、`persona` | Governor、Governed Runtime、Memory、Gateway/Edge | Host `chat/skill/delegate/plan` 已进入主链；Chat 的投影屏障、模型预算和提交绑定控制均为 PASS，Chat 路线为 COMPLETE；其余六路仍未完成 |
| 任务、Skill 与 Delegate | Chat 意图、Operator 命令 | `orchestration`、`task_runtime`、`agent_runtime`、`skills` | Model Runtime、Capability Broker、Domain Pack | Host canonical route `WIRED`；0102+GR06 canonical Node Acceptance 已统一 exact Effect/artifact/`ACCEPTED`/Attempt Outbox。public TaskStore graph/effect staging 入口已删除，应用 Python 写 authority 已封口；raw/orphan SQL 仍可绕过，通用恢复与生产并发仍 `PARTIAL` |
| 视觉桌面自动化兼容入口 | `/v1/agent/run`、`/v1/agent/info`、vision chat `response_format` fence | `app/agent_routes.py`、`app/chat_handler_vision.py` | 无执行下游 | `RETIRED_COMPATIBILITY_ONLY / PRODUCTION_OFF`；raw loop/client 与 Ledger writer 已删除，`run` 只返回固定拒绝，`info` 明示不可用，vision chat 非 `text` 在 handle/model/Memory/Ledger 前拒绝；`ActionPlan` schema/parser 仅 proposal-only。它不是 Delegate `vision_agent`，也不是正向 Visual Agent Runtime |
| 自动化与定时任务 | Scheduler API、Monitor Automation | `scheduler`、`lifecycle_runtime` | Governed Command、Life、Memory、Security Immune | governed occurrence 的 worker 已改为 immutable operation plan，真实 handler 由共享 Acceptance Effect dispatcher 在验收后执行；0117 已持久化 DUE→OCCURRENCE fence 与 execution heartbeat。definition/admin、mutable batch、一般未知效果归因和 canary 仍为 `PARTIAL` |
| 记忆、检索与设定 | Memory/Canon API、Monitor Memory/Canon | `memory`、`retrieval`、`canon` | SQLite、Chroma、Neo4j、Surface Context | 多类记忆与 Canon proposal/apply 可用；统一 Knowledge OS 为 `MISSING` |
| 数字生命状态 | `/v1/digital-life/*`、Monitor Life Systems | `digital_life` | Projection、Persona、Lifecycle、Delivery Outbox | command/event/reducer 地基 `WIRED`；World/Scene/Intent 统一上下文仍 `PARTIAL/MISSING` |
| 人格、情绪和关系表达 | Surface Reply、Bond API、Monitor Persona/Bond | `persona`、`bond`、`surface_runtime` | Model Runtime、Memory、Life Projection、Embodiment | 表达链可用；Persona 旧直写已中央封口，受治理的演化提案链仍缺失 |
| 主动性与生命周期 | Scheduler `life_tick`、proactive API、Monitor Proactive | `lifecycle_runtime`、`digital_life`、`persona` | Governor、Gateway、Edge、Life Outbox | `PARTIAL`；生产 proactive dispatch 已校验与 WorkOrder 冻结的 canonical Scene identity，需要 Review/BLOCK 或上下文漂移时禁止 delivery；完整主动性验收仍待后续波次 |
| 语音、形象和桌面行为 | Edge RPC、MateEngine、Desktop Pet | Host `channels`、Edge `runtime/actuators/senses`、`desktop_pet` | TTS/ASR provider、VTS/VRM、OS UI | 软件链 `WIRED`；完整验收取决于本地模型、应用和硬件 |
| 外部消息平台 | Gateway webhook/reply | Edge `gateway`、Host `input_runtime/gateway_delivery.py` | Secure Ingress、Host Runtime、平台 adapter | 耐久 transport 主干存在；单平台可用性依赖凭据与本地端 |
| 安全免疫 | Internal Security API、Monitor Security | `security_immune`、`secure_ingress`、`airlock` | Scheduler、Audit、Governor | 输入门禁与传感器资产存在；定时 governed 路径受 Scheduler 故障影响 |
| 授权渗透测试 | `/v1/pentest/*`、secure-run | `pentest` | Governor、Capability/Tool adapter、PlanningSearch、Pentest Store | 大型独立领域实现可用；L3 MCTS 仍为未校准 shadow |
| 运维、观测与审批 | Obs API、Monitor UI、Scheduler Admin API | `observability`、`app/obs_*`、Operator Gateway、Scheduler routes | 读模型、Review Store、受治理命令 | 主要读链 `WIRED`；Operator 写面已有十四条 governed command，`23/37` 条仍阻断；Scheduler 双前缀 CRUD HTTP 已作为独立 surface 进入相同 TaskGraph/Acceptance Effect 边界，Obs 与 Host composition 仍未完全隔离 |

## 4. 后端模块边界

### 4.1 入口、身份和执行许可

| 模块 | 当前责任 | 主入口/代码证据 | 允许依赖 | 不应拥有 | 状态/缺口 |
|---|---|---|---|---|---|
| Capability Planning Risk Authority | 将已绑定计划的风险请求收紧为冻结候选上限，并把授权证据纳入 virtual-state lineage | [`capability_plan_search.py`](../../../host_brain/orchestration/capability_plan_search.py)、[`governor_grant.py`](../../../host_brain/orchestration/governor_grant.py) | immutable intent、Governor approved binding、只读 Registry metadata | 放宽 binding、从 handler 推导权限、把 request 字段当作 grant | `LOCAL_TARGETED_PASS / NE-02_PARTIAL`；effective ceiling=`min(requested, governor)`，binding 缺失或畸形时 Search 前失败关闭。仅覆盖 Research/Frontend 窄 capability path，不表示通用 Plan、跨域收益或 promotion。 |
| HTTP/Application Adapters | FastAPI route、schema、lifecycle 和 provider 绑定 | [`app/asgi.py`](../../../host_brain/app/asgi.py)、[`app/app_factory.py`](../../../host_brain/app/app_factory.py)、[`app/router_registry.py`](../../../host_brain/app/router_registry.py) | application port、composition provider | 业务规则、持久化真相、长期状态 | `PARTIAL`；`app/` 仍有较多真实编排和 service factory |
| Input Runtime | ASR/Gateway 结构验证、归一化、幂等 claim、Cortex dispatch 和 reply envelope | [`input_runtime/ingress.py`](../../../host_brain/input_runtime/ingress.py)、[`input_runtime/cortex_dispatch.py`](../../../host_brain/input_runtime/cortex_dispatch.py)、[`input_runtime/worker_dispatch.py`](../../../host_brain/input_runtime/worker_dispatch.py) | Secure Ingress port、Governor port、Governed Command port、delivery port | 自建 Task 状态机、直接设备副作用 | `WIRED`；仍有跨包直接 import 与较多输入特例 |
| Secure Ingress | 凭据验证、principal/scope、replay claim、rate decision 和审计 | [`secure_ingress/service.py`](../../../host_brain/secure_ingress/service.py)、[`secure_ingress/authority.py`](../../../host_brain/secure_ingress/authority.py)、[`secure_ingress/surfaces.py`](../../../host_brain/secure_ingress/surfaces.py) | 凭据/rate/audit ports | 业务路由选择、任务执行 | `WIRED`；为 Gateway、Scheduler、Internal、Operator 等高风险入口的身份边界 |
| Governor | 路由决策、等级策略、能力许可、限流与 grant/token | [`governor/decision.py`](../../../host_brain/governor/decision.py)、[`governor/rules.py`](../../../host_brain/governor/rules.py)、[`governor/action_arbiter.py`](../../../host_brain/governor/action_arbiter.py) | 策略投影、Capability descriptor、审计 | worker执行、搜索 rollout、外部副作用 | `WIRED / PARTIAL`；Task admission 已接 authority-attested Intent；Action Preview/Catalog authority 的 #84 本地 Gate 已通过，Preview 仍不取代 Governor grant |

### 4.2 命令、图与持久执行

| 模块 | 当前责任 | 主入口/代码证据 | 允许依赖 | 不应拥有 | 状态/缺口 |
|---|---|---|---|---|---|
| Runtime Contracts | WorkOrder、TaskGraph、Budget、Review、Verification、Acceptance 数据合同 | [`contracts/agent_runtime/`](../../../host_brain/contracts/agent_runtime)、[`natsume_contracts/task/v1.py`](../../../natsume_contracts/task/v1.py) | 标准库与纯类型 | app、persistence、provider 实现 | `WIRED`；TaskGraph v1 可选扩展包含 result/completion policy；新生产图默认 atomic 且必须显式结果，历史 legacy 多 sink 保持兼容；Host/Scheduler/Internal canonical admission 用 V3 |
| Orchestration | HostCommand admission、Governor grant 解析、计划图编译、revision、0100 durable supersession 和 node grant | [`orchestration/governed_command.py`](../../../host_brain/orchestration/governed_command.py)、[`orchestration/planning_graph_compiler.py`](../../../host_brain/orchestration/planning_graph_compiler.py)、[`orchestration/planning_admission.py`](../../../host_brain/orchestration/planning_admission.py)、[`persistence/host_command_repository.py`](../../../host_brain/persistence/host_command_repository.py)、[`orchestration/node_grants.py`](../../../host_brain/orchestration/node_grants.py) | contracts、repository ports、PlanningSearch result | 领域 worker 实现、外部副作用 | Host 主链 `WIRED`；依赖 cycle 以 `PLAN_CYCLE` 拒绝，不能降级为串行执行。显式 FAILED-only replan 已有单事务 supersession 与旧 revision fence，但自动触发、更宽 predecessor classifier 和生产 canary仍 `PARTIAL` |
| Governed Host Adapter | 把 `chat/skill/delegate/plan` 编译为 HostCommand + `WorkOrderV3` + immutable graph；以 route-aware authority 校验 exact replay，并在完整 runtime group 激活后启动 Host-owned durable survivor | [`app/governed_task_adapter.py`](../../../host_brain/app/governed_task_adapter.py)、[`app/host_startup_graph_continuation.py`](../../../host_brain/app/host_startup_graph_continuation.py)、[`app/periodic_durable_continuation.py`](../../../host_brain/app/periodic_durable_continuation.py)、[`app/governed_task_authority.py`](../../../host_brain/app/governed_task_authority.py) | Orchestration、Task Runtime、route worker、canonical command repository | 执行期二次规划、原始 ingress 重建、夺取 live Lease、扫描外部 runtime 的业务 tuple | `PARTIAL_HOST_LIFECYCLE_SURVIVOR_EXACT_CONTINUATION`；command repository 在单个读事务内按 Host surface 联结 nonterminal admission/Graph，`limit + 1` 区分 exact bound 与真实 overflow，foreign surface 不会饥饿 Host。mandatory boot pass 后，同一 continuation 由 app-local supervisor 周期/按 wake 串行重扫；live Lease/竞争保持 `DEFERRED`，到期后在线重评。unknown-effect、general multi-node/batch recovery 仍缺，chat/delegate 不再误套 skill 专属 metadata |
| Task Runtime | 依赖推进、Attempt、Lease、Idempotency、Budget、Review、Verification、Acceptance、effect intents、已知中断分类、两阶段 Graph cancellation、签名 Operation continuation/失败终结、general Effect 归因与 0099 历史终态 reconciliation | [`task_runtime/coordinator.py`](../../../host_brain/task_runtime/coordinator.py)、[`task_runtime/node_execution_runtime.py`](../../../host_brain/task_runtime/node_execution_runtime.py)、[`task_runtime/operation_interruption_runtime.py`](../../../host_brain/task_runtime/operation_interruption_runtime.py)、[`task_runtime/operation_failure_reconciliation.py`](../../../host_brain/task_runtime/operation_failure_reconciliation.py)、[`task_runtime/parallel_settlement_runtime.py`](../../../host_brain/task_runtime/parallel_settlement_runtime.py)、[`task_runtime/acceptance_effect_reconciliation.py`](../../../host_brain/task_runtime/acceptance_effect_reconciliation.py)、[`persistence/task_acceptance_effect_reconciliation_store.py`](../../../host_brain/persistence/task_acceptance_effect_reconciliation_store.py)、[`task_runtime/dag_scheduling_runtime.py`](../../../host_brain/task_runtime/dag_scheduling_runtime.py) | Task repository ports、worker/verifier/acceptance/cancellation-authority ports、canonical HostCommand repository | 路由意图识别、计划搜索、未经验证的业务取消、第二套状态、通用副作用猜测 | `PARTIAL_ATOMIC_DEFAULT / SIGNED_MULTINODE_NODE_FAILURE_0090_LOCAL_PROVEN / SIGNED_GENERAL_ACCEPTANCE_EFFECT_RECONCILIATION_LOCAL_PROVEN / NE01_IN_PROGRESS`；已知中断与 Operation 子集保持。signed `NO_SIDE_EFFECT` 可 node-local 关闭多节点失败，再由0090唯一收口Graph/Budget；双根重启证明READY sibling零执行、receipt稳定和Outbox篡改失败关闭，并发双失败证明非 selected 节点仍可由同一 decision outcome 精确重开。0128 另为 cancellation-independent `GENERAL_UNKNOWN_EFFECT` 隔离 raw signature；它是签名人工归因，不是自动猜测。bounded fan-out/cancellation、其它 multi-node/batch recovery、跨节点时间预算、部署 key/credential 和 canary 未闭环 |
| Acceptance Effect Runtime | Acceptance 后 claim/fence/dispatch/对账/恢复观测 | `acceptance_effect_handlers.py`、`accepted_sys_mode_transition_handler.py`、`accepted_effect_reconciliation_handler.py`、`acceptance_effect_handler_common.py`、`acceptance_surface_effect_handler.py`、dispatcher、Guardian observer | Effect Store、Preview、领域 authority、Guardian | 猜测外部结果、盲重放、异常正文 | `PARTIAL`；原 owner 1798→966 行，sys-mode 执行/重开进入独立368行 handler，general Effect accepted handler 独立359行。common 只拥有纯 JSON/effect identity/opaque failure/receipt helper，Surface 模块只拥有 Gateway reply handler 与只读 reconciler，均未取得第二 writer。Preference/Life/Conversation 的既有 exact authority 保持不变 |
| Scheduler | 定义、occurrence、时钟、lease、模式/资源策略、operation plan 与验收后 handler dispatch | [`scheduler/clock.py`](../../../host_brain/scheduler/clock.py)、[`persistence/scheduler_repository.py`](../../../host_brain/persistence/scheduler_repository.py)、[`persistence/scheduler_clock_retry_store.py`](../../../host_brain/persistence/scheduler_clock_retry_store.py)、[`persistence/scheduler_repository_primitives.py`](../../../host_brain/persistence/scheduler_repository_primitives.py)、[`scheduler/governed_runtime.py`](../../../host_brain/scheduler/governed_runtime.py)、[`scheduler/operation_plan.py`](../../../host_brain/scheduler/operation_plan.py) | Secure Ingress、Governor、Task Runtime、shared Acceptance Effect dispatcher、Scheduler Repository | worker 内领域副作用、自建第二套 Task/result 状态机、未知效果盲重放 | `PARTIAL / DURABLE_CLOCK_OCCURRENCE_FENCE`；原 repository 3537→2942 行，retry mixin 复用同 connection/UoW 完成 Clock lease/fence 事务，primitives 仅做纯校验/稳定错误。definition/admin、mutable batch freezing、人工 reconciliation 与 canary仍缺 |
| Internal Agent Runtime | 认证后的内部 Agent WorkOrder、protected artifact、Governed write promotion、projection recovery、签名 terminal reconciliation、验收后 canonical result | [`app/internal_task_runtime.py`](../../../host_brain/app/internal_task_runtime.py)、[`agent_runtime/executor.py`](../../../host_brain/agent_runtime/executor.py)、[`agent_runtime/workspace_write_promotion.py`](../../../host_brain/agent_runtime/workspace_write_promotion.py)、[`agent_runtime/accepted_result.py`](../../../host_brain/agent_runtime/accepted_result.py)、[`agent_runtime/internal_artifact_terminal_reconciliation.py`](../../../host_brain/agent_runtime/internal_artifact_terminal_reconciliation.py) | Secure Ingress、Task Runtime、Workspace Runtime、Acceptance Effect | 丢失 attempt/workspace fence、公开 raw artifact、Acceptance 前移动源分支、seal 后漂移、把 Internal 专属恢复外推为一般恢复 authority | `PARTIAL / INTERNAL_GOVERNED_WRITE_PROMOTION`；缺 authority 的 write 仍 proposal-only。Governed Internal Worker 密封 bounded manifest/binary patch，Acceptance 重验并生成 deterministic accepted HEAD，通过 0116 晋升且源分支不动；exact replay、tamper 与 Runtime reconstruction 已由真实 SQLite+Git 验证。sealed recovery 与 signed cancellation/raw-failure claim-before-cleanup 保持。一般 unknown effect、其它 failure/cancel、handle-level TOCTOU、OS/container isolation 与七路线 receipt 仍未完成 |
| Direct Agent Skill Workspace Runtime | 已登记 risk-4 Direct Skill 的 server-bound V3、consumed Review、sealed artifact proposal 与 post-Acceptance promotion | [`agent_runtime/governed_skill_work_order.py`](../../../host_brain/agent_runtime/governed_skill_work_order.py)、[`agent_runtime/accepted_workspace_artifact.py`](../../../host_brain/agent_runtime/accepted_workspace_artifact.py)、[`app/governed_frozen_skill_runtime.py`](../../../host_brain/app/governed_frozen_skill_runtime.py)、[`task_runtime/agent_workspace_acceptance_effect.py`](../../../host_brain/task_runtime/agent_workspace_acceptance_effect.py)、[`task_runtime/acceptance_effects.py`](../../../host_brain/task_runtime/acceptance_effects.py) | SkillRegistry、Task Runtime、ReviewStore、CapabilityBroker、ArtifactStore、WorkspaceRuntime、Acceptance Effect | caller V3/Workspace/Review/fence 注入、未审核写入、公开 protected stage、Acceptance 前提升、可见 Effect 越过失败 promotion | `PARTIAL / REVIEWED_SKILL_CODEX_WORKSPACE_PROMOTION_LOCAL_PROVEN`；仅 `agent_worker/self_heal/self_evolve`。WorkerProjection 只含 ref/digest，pure helper 校验后由 canonical compiler 编排；Accepted handler 重开 exact Task/Attempt/Lease/Preview/Effect/seal 后推进 0116。Domain Pack、其它 Skill、一般恢复、OS/container 与真实 canary仍缺 |
| Read Projection / Executive Guard | `task_progress`、`recent_turn` 的只读投影与 retired fail-close guard | [`agent_runtime/read_routes.py`](../../../host_brain/agent_runtime/read_routes.py)、[`executive_runtime/runtime.py`](../../../host_brain/executive_runtime/runtime.py)、[`app/executive_runtime.py`](../../../host_brain/app/executive_runtime.py) | Durable Task/Conversation projection | Task/Attempt、worker、状态写入、技能 registry/handler、Executive application ports | `PARTIAL_RETIRED_FAIL_CLOSED / PRODUCTION_OFF`；Executive 无生产入口，`route_planning`、`conversation_execution`、application `composition`、application port wiring、legacy diagnostic direct-skill matrix、legacy SkillDag 执行、`app/intelligent_runtime.py` 和内存 registry 已删除；Cortex direct reply/post-process compatibility branch 也已删除，unsupported canonical route 在写入前拒绝，stale app wrapper 只能拒绝，纯 SkillDag adapter 零生产调用，cycle 只能保留空 batch 并由适配器以 `PLAN_CYCLE` 拒绝，Executive 内的 Forge override call 已移除；但独立 Forge `runtime-match` 仍待 NE-05 退役。这不等于 NE-01 完成 |

Task Runtime lifecycle 的实际边界是：activation rollback 或 `aclose` quiescence 未完成统一使用 `TASK_RUNTIME_LIFECYCLE_RETRY_REQUIRED`，后续 bind/activate 锁死，只允许 `aclose` 重试。Coordinator admission close、drain、`aclose` 任一失败均保持 Store 开放；Coordinator 和 0094 worker 都静默后才 drain Outbox/关 infrastructure。

到期 retry 的边界也必须区分：不需要新 Review 的 `RETRY_PENDING` 在同一 SQLite writer UoW 内提升为 READY 并领取 Attempt/Lease/Heartbeat/claim Outbox，Attempt Outbox 漂移会整笔回滚；需要新 Review 的 retry 先生成 durable Review，再由下一轮领取，不能绕过审批。这是受限的 retry 原子窗口，不代表 general recovery、跨节点恢复或 NE-01 已完成。

普通或重试耗尽的 direct、non-deferred pre-reservation failure 也有一个更窄的五聚合事务：`Attempt FAILED`、`Node FAILED`、`Graph FAILED/BLOCKED`、`Lease RELEASED` 与精确 graph-scope `Budget RELEASED` 连同五条 Outbox 一起提交；Budget release 必须是无 attempt lineage 的精确 id/version 事件，response-loss replay 与 injected Outbox fault 均逐项校验。并行 deferred child 不获得共享 Budget authority，仍交 outer settlement；其它终态路径仍不得据此声称 Budget 已全局收口。

NE-01 两批职责拆分保持这些 authority 不变：首批分离 Host adapter/worker/contracts、HostCommand UoW/codec、Review Store/binding/codec 与 Scheduler occurrence/command；第二批分离 ASGI lifecycle wiring、Obs response models、Degradation policy、Node Grant/Planning codec、Intent Review continuation 和 Guardian contracts/operations。第三个切片移除 Obs 五个无消费者的 locator refresher。此前 Tool/MCP 切片删除 process-owned `get_mcp_tool_broker` accessor/`mcp_tool_broker` slot并退役 Tool seed/route locator。Operator 已迁移既有十条 route，并新增 `scheduler.task.enable/disable` 与 `sys_mode.auto_rules.update/event.submit`；Task/Review control 只有 terminal `task.operator_control` Effect 可调用原 owner，状态控制只有 `operator.state_control` Effect 可调用 Scheduler/Host policy owner，另 `23/37` 个入口 fail closed。HTTP Tool 接入 canonical runtime，旧 ToolBroker/MCP direct/callback API 固定拒绝且不保留 provider handler，但生产 seed governed executor 仍为 0。

视觉切片进一步退役 route-local 执行 authority：`/v1/agent/run` 只发出 `AGENT_INIT` 后以 `LOOP_DONE / REJECTED` 结束，并固定 `side_effects_executed=false`；它不解析或调用模型、Ledger、Edge session、HTTP client 或 automation executor。`agent_loop.py` 与 `edge_rpc_client.py` 已删除，`generate_action_plan()` 固定抛出 `VISUAL_AGENT_GOVERNED_ADMISSION_REQUIRED`，保留的 `ActionPlan` schema/parser 仅可生成 proposal。vision chat 的非 `text` 请求在 handle/model/Memory/Ledger 前拒绝，历史 vision text helper 没有生产 caller。该入口不是 Delegate `vision_agent`，也没有恢复 ToolBroker、MCP、Skill、Delegate、registry 或 closure 的直执行 authority；正向视觉自动化必须经 Secure Ingress/Governor 生成新的 immutable TaskGraph revision。

Host lifecycle 模块以 app-local `HostLifecycleContainer` 拥有 ActivityMonitor、ResourceManager 与 ModeTransition；factory/start 全部成功后才发布，Activity factory 失败不发布 Mode，closing fence 阻断 `begin()`，ResourceManager startup sample 进入唯一 owned loop。ResourceManager 在 canonical migration、Task Runtime、Host lease/fence 与 RuntimeGuardian 后启动；`ResourceManagerStatusOwner` 负责 bounded redaction、pending exact replay 与恢复，`RuntimeEventResourceManagerStatusJournal` 负责 0123 aggregate、revision/digest chain 和 active Host fencing。停机必须先停 Scheduler，再持久停止 ResourceManager，成功后才能关闭 Task Runtime 和释放 Host lease。Guardian health heartbeat 使用每个 ASGI lifespan 自有的 `ServiceHealthHeartbeat` 保存 task、keepalive、observer、probe cache 与 HTTP transport，关闭只作用于本 scope；兼容模块 helper 仍只服务嵌入调用和测试。Chat/Input、Tool、Scheduler、Guardian snapshot 读取 app-local 实例，Obs 从 canonical Runtime Event head 物化 read projection，禁止共享文件回退或查询期懒启。`TribeRuntimeScope` fresh 持有 state registry、Host registry、event bus 与 23-Agent catalog；bind 不发布进程级 active owner，lifespan/background execution 与 HTTP middleware 经 `ContextVar` 激活 exact-owner context。legacy shared-state/cross-Agent getter 与显式 Chat/Input ports 解析同一 scope catalog；两个重叠 scope 的 Agent/registry identity 已隔离，同 execution 跨 scope 与 stale inherited context 失败关闭。23 个 Agent backing singleton/setter 与三类 shared runtime fallback 已退役，验证 harness 显式拥有 scope；易失 bus 与完整 Host multi-app canary 尚未收口，因此不宣称全 Host 隔离或 durable authority。`http_hooks.py` locator 与 Tribe setter 导入为 0，全局 monitor/resource setter 及 2 slot + 2 lock 已删除。

Host lifecycle、Tool/MCP、#89、ReAct、视觉、HardState、Context、内部旁车和此前各 wrapper/state 库存只绑定各自提交。当前 Host bundle 显式解析依赖，生产 locator module/field/lock/ref=`0/0/0/0`，ResourceManager status 由 0123 journal 持久化。它仍不能作为外部 Chroma/Neo4j namespace、Token/MCP durable restart、其它外部资源恢复或 legacy Executive generic delegate 已迁移的证据。

Chat 检索模块现在不再承担 Service Locator adapter：Host scope 通过 AgentRuntime port 提供 conversation history、preference-pack builder 与 recall factory，后者唯一负责绑定 ledger、Life memory 和 Bond Neo4j。该模块的职责仍只是 recall packet 压缩与读路径降级，不拥有 Chat admission、对话写入、Acceptance 或任何 effect authority。

历史结构切片保持 Acceptance/ASGI/Scheduler owner 为1262/1389/2942行，focused helper/wiring/store不转移原writer、lifespan/task owner或repository UoW。967行 Budget rejection Store只处理受约束的 C mutation；444行只读 terminal resolver与93行纯 route helper不拥有第二 writer。这些提交绑定的静态 PASS 和36文件265项只作历史。当前分支已把 feature-canary 与 Host lifecycle 共享类型迁入中立 contracts，Persistence/Composition 不再反向依赖 Task Runtime/App，旧路径保持同对象兼容；独立 Gate 1～14 为 `PASS`、`failed_gates=[]`，Gate 1 issues/SCC 为 0。它不代表 atomic/fan-out/cancellation、真实 workspace executor 和其他 Host/Worker scope 完成；NE-01 维持 `PARTIAL / IN_PROGRESS`，`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。

Effect reconciliation persistence 以 `task_effect_reconciliation_store.py` 保留稳定兼容 composite，实际责任拆到 common helpers、read/validation 和 mutation focused modules；cancellation trusted overlay 独立位于 `task_graph_cancellation_effect_overlay_store.py`。所有 mixin 复用 `DurableTaskStore` 的同一 connection/transaction/UoW，没有第二 Store authority，也未通过 architecture policy 白名单掩盖大型文件。

TaskStore 写口封口后，`DurableTaskStore.create_graph`、`insert_graph_in_transaction`、`stage_acceptance_effect_intent` 不再存在。Graph 私有 hook 只由 `HostCommandRepository._admit_normalized()` 在同一 admission UoW 调用 1 次；Effect 私有 hook 只在 `transition_graph_and_stage_acceptance_effects`、`commit_node_acceptance`、`complete_parallel_graph_success` 三个 canonical UoW 中共调用 6 次。Graph/Node INSERT SQL 仅位于 Graph hook，Effect INSERT SQL 仅位于 Effect hook。`tests/task_runtime_authority_fixtures.py` 只创建无 `host_command_journal` 的 test-only raw/orphan 状态；SQLite 仍没有阻止此类 direct SQL 的新 migration/trigger，所以 NE-01 仍为 `IN_PROGRESS`。定向证据保留分组结果与首跑 fixture 失败/修正记录；最终 key regression 批次为 `26 passed in 35.33s`，波次 integration 仍为 `NOT_RUN_PER_WAVE_POLICY`。TaskStore 切片当时 full static 为 `FAIL [1,6,10]`；Gate 10、Gate 6、最终 persistence 合同切片的 Gate 1 与后续 Gate 11 曾在后续提交绑定快照通过。当前分支 fresh 全量结果已重新验证为 Gate 1～14 `PASS`、`failed_gates=[]`。

Terminal reconciliation persistence 以 0099 schema-only migration 建立 append-only case/proof/supersession；migration 不改 Graph/Budget。Readiness 严格比较 3 table/4 index/10 trigger canonical SQL 并以 Python 复核 case/subject/proof。Composition 在 `recover_startup` 后单次最多 inventory 10,000 个 terminal Graph。`TerminalStateReconciliationService` 只允许三分法：`FAILED + exact atomic failure bundle + 唯一 graph-scope ACTIVE/EXCEEDED Budget` 由默认开启的 system auto 精确释放 Budget；`BLOCKED failure` 与 legacy `CANCELLED` 要求 Ed25519；child/missing/multi Budget、PENDING/CONSUMED，或 Graph/Node/Review/Attempt/Lease/并行终态任一不一致只 quarantine。Classifier 要求唯一 0090 decision、完整 0091/0092 validator 与 exact failure Outbox payload refs。Runtime 重入不再静默修复 terminal Graph 的未结 Budget；未决 current case graph-wide 围栏普通 Graph/cancellation request 与 Budget transition/consume/usage/retry，只有 0099 proof UoW bypass。System proof 用 immutable case 时间跨进程收敛；healthy closure 只在历史未决 case 且 current healthy 时生成 signed `NONE` 且零业务写。该模块为 `AUTO_EXACT_ON / OPERATOR_OFF / CANARY_NOT_RUN`，不是 general recovery。

Budget contention persistence 由 `TaskBudgetUsageStoreMixin` 唯一拥有：首个 rejected usage 是唯一 `EXCEEDED` winner并拥有 Budget transition/Outbox；随后 sibling 只落 `ALREADY_EXCEEDED` observation并引用 winner。共享 terminal-authority reader 被 0090 decision 写入、读取和 replay复用，并以单一 SQLite snapshot 复核 Reservation 精确版本、sole Outbox、Attempt/Graph lineage与全部 sibling。`DurableBudgetRuntime` 以 `max(configured retries, concurrency + 1)`提供 CAS attempt，concurrency=8/连续 8 次冲突/第 9 次成功已有单测；这不是 concurrency charge。正数 `time_ms` 不能退回 policy 值：runtime 必须从 canonical remaining 读取，零剩余用一个持久 `TIME_GUARD` 使用拒绝启动 Operation/worker；非零值以本地 bounded stream 强制当前单节点 worker 截止，timeout 后强制把用量结算为超过余量、保留 Operation 并交 `RECOVERY_REQUIRED` 对账。`time_ms=0` 仅是历史兼容的无截止，不是零预算。外层 `DurableDagSchedulingRuntime` 在 owner lease 已过期且无 live owner时，可显式授权完整 `COMMITTED` 证据的同一 Attempt 以更高 fence 续接。BudgetUsage 的 `EXACT_ACCEPTED / EXACT_REJECTED / MISSING` 都不重跑 worker；共享 `EXCEEDED` 下 accepted/missing checkpoint 必须证明唯一 sibling winner，missing 只能形成不推进 Reservation version且共享 winner Outbox/dimensions 的 `ALREADY_EXCEEDED`。双 checkpoint、four-root、五节点菱形与八根节点已本地证明。`CONFLICT`、unknown `RESERVED/RECOVERY_REQUIRED` 与取消交叉现由 0130 持久 hold；它们仍不自动恢复，跨节点时间预算、无限规模和其它 failure/cancellation 继续是缺口。

普通受治理 Skill 的模型端口由 `host_brain/model_runtime/governed_skill_budget.py` 负责。`SkillMeta.model_budget_required` 是显式能力声明；执行层只能从 exact ACTIVE Task Budget 生成 typed runtime，普通 Skill 不再收到裸 `ModelAttemptService`。typed runtime 对每次 canonical `ModelAttemptResult` 扣减 Token、retry、成本与时间，并把 server-generated attestation 交给 frozen worker 重验后构造 `WorkerBudgetUsage`。当前正向 Skill 是 `emergent_growth_weekly`；缺端口、缺 measurement、重复/cache、超限或 lineage drift 均失败关闭。该模块没有 durable writer，也不证明 Persona proposal/activation、真实 provider 或 program-wide Budget 完成。

### 4.3 规划、Agent 和领域能力

| 模块 | 当前责任 | 主入口/代码证据 | 允许依赖 | 不应拥有 | 状态/缺口 |
|---|---|---|---|---|---|
| PlanningSearch Kernel | Candidate expansion、cognitive ranking、conditional MCTS、virtual transition、reward 和 trace | [`planning_search/service.py`](../../../host_brain/planning_search/service.py)、[`planning_search/contracts.py`](../../../host_brain/planning_search/contracts.py)、[`planning_search/ports.py`](../../../host_brain/planning_search/ports.py) | 纯合同、领域注入的纯函数 port | 工具执行、网络、持久写入、真实副作用 | 内核 `WIRED`；通用智能规划收益未证明 |
| Host Plan Adapter | 遗留模式把 Governor 已批准的 steps 合法化为顺序候选；明确 Research/调研和 Frontend/可用性计划则由 Thalamus server-owned rule 填入 mode/tag/risk，经过 Governor 后从 app-composed `SkillRegistry` 冻结竞争能力，在虚拟态选择两项不同 Skill 的顺序路径，再在选中后重建 node 输入并编译 `skill → skill → chat` 图；显式 `failure_replan` 只经 canonical durable recovery 创建同 root revision `+1` | [`tribe/thalamus_agent.py`](../../../host_brain/tribe/thalamus_agent.py)、[`governor/schemas.py`](../../../host_brain/governor/schemas.py)、[`orchestration/plan_intent_search.py`](../../../host_brain/orchestration/plan_intent_search.py)、[`orchestration/capability_plan_search.py`](../../../host_brain/orchestration/capability_plan_search.py)、[`planning_search/capability_domain.py`](../../../host_brain/planning_search/capability_domain.py)、[`orchestration/planning_execution.py`](../../../host_brain/orchestration/planning_execution.py)、[`orchestration/planning_recovery.py`](../../../host_brain/orchestration/planning_recovery.py) | PlanningSearch、graph compiler、只读 Registry metadata、Governor-bound route seed、canonical TaskStore failure snapshot | 凭空新增工具、调用 handler、越过 Governor 或把未选候选带入执行输入 | `PARTIAL_RULED_OBJECTIVE_CAPABILITY_SEARCH_V1`；仅明确的 Research/调研、Frontend/可用性计划可自动形成 `CAPABILITY_SEARCH_V1`，且仍需至少两项 host/免确认/低风险/标签匹配能力。显式失败恢复不是自动或 failure-adaptive replan；仅建议、禁止工具、通用目标、Code/Pentest 和真实收益/promotion 仍未完成 |
| Code Planning Shadow Adapter | 用冻结 task/workspace/verification refs 与 digest 动态构造 inspect→registered patch→unit/rollback 虚拟候选，并把重验后的唯一 node args 绑定到 immutable compilation context；root 只允许 `inspect + generate_report + temp_workspace` 且必须禁 `write_files/network` | [`planning_search/code_domain_models.py`](../../../host_brain/planning_search/code_domain_models.py)、[`planning_search/code_domain.py`](../../../host_brain/planning_search/code_domain.py)、[`orchestration/planning_execution.py`](../../../host_brain/orchestration/planning_execution.py)、[`orchestration/planning_recovery.py`](../../../host_brain/orchestration/planning_recovery.py) | PlanningSearch、GoalPlanning、compiler context、纯 JSON 证据 | worktree/source、模型/工具/测试/provider、Task admission、Workspace ACL、legacy `intent_draft` 覆盖 | `PARTIAL_D16_DYNAMIC_CATALOG_SHADOW_ONLY`；`virtual_model_ready=false` 时不能启用 L3；未有真实 A/B receipt、写入切流或收益结论 |
| Research Planning Shadow Adapter | 用 `ResearchTaskSnapshot` 的公开 source metadata，按虚拟状态动态构造 `query`、`quick-answer`、`primary-source`、`independent-source`、`cross-check`、`resolve-conflict`、`stop` 虚拟候选；L2 只比较 authority/relevance/read-cost，L3 只在冻结 `virtual_transition_ready=true` 时存在。D20c 只按由账本 owner 以 CSPRNG 持久化的双臂顺序，把 canonical audit 前态与终态均严格绑定的模型执行结果封为外部模型证据 | [`planning_search/research_domain_models.py`](../../../host_brain/planning_search/research_domain_models.py)、[`planning_search/research_domain.py`](../../../host_brain/planning_search/research_domain.py)、[`planning_search/research_benchmark_corpus.py`](../../../host_brain/planning_search/research_benchmark_corpus.py)、[`planning_search/research_benchmark_verifier.py`](../../../host_brain/planning_search/research_benchmark_verifier.py)、[`planning_search/research_citation_output_parser.py`](../../../host_brain/planning_search/research_citation_output_parser.py)、[`planning_search/attested_external_research_adapter.py`](../../../host_brain/planning_search/attested_external_research_adapter.py)、[`planning_search/research_paired_execution.py`](../../../host_brain/planning_search/research_paired_execution.py)、[`model_runtime/composition.py`](../../../host_brain/model_runtime/composition.py)、[`persistence/research_shadow_ledger_repository.py`](../../../host_brain/persistence/research_shadow_ledger_repository.py) | PlanningSearch、冻结公开 metadata、受信实验 corpus manifest、profile-bound citation envelope parser、机械 citation verifier、composition 注入的 `ModelAttemptService` 与 `AttemptAuditLookup`、0107/0121 evidence owner | `PARTIAL_D20C_ACCEPTANCE_PENDING_EXTERNAL`；planning payload/公开 receipt 仅有 source id/ref/digest 等 metadata。`FrozenResearchCorpusLoader` 只从可信实验根目录下 manifest 列出的 repository-relative 普通文件读取，在稳定输入中前后检查大小并在读完后核对 digest，且拒绝绝对/遍历/ADS colon/case alias/link/reparse/未列来源；`ResearchCitationVerifier` 每次新建 loader，精确校验 snapshot/manifest/source binding、UTF-8 byte boundary 和 span digest；`ResearchCitationOutputParserV1` 的固定 JSON schema digest 必须与 profile 相等，它拒绝额外 envelope/item 字段并丢弃 answer 正文。新 RunSpec 在 SQLite 写事务中只持久化 cohort key digest；owner 先重开精确 replay，再用 CSPRNG 决定实际 arm order，调用方不能传递 order 或自报随机化；旧规格/receipt 只读可重开、不能生成新执行或 terminal receipt。`ResearchPairedExecutionRunner` 只重开 owner-randomized RunSpec 的实际 arm order、使用 deterministic execution ID，raw prompt/output 不入 0107，fresh verifier、profile prompt digest、工具调用数与单臂/成对预算均必须匹配。D20c adapter 在外呼前必须确认 execution ID 无 canonical audit row，已有行或 lookup 失败均要求 reconciliation；随后才接受一个非缓存成功 attempt、exact route/output identity、prompt digest、工具/token/成本与 provider/总延迟，并重开 canonical audit started/terminal pair 后将完整 attestation 嵌入 receipt；cache、多 attempt、audit 或缺测量失败关闭。已有 evidence 只重开，audit-without-evidence 不得重发外呼。0107 owner 在 terminal/replay 时重开 registration、RunSpec 和两条 arm，逐项复核 plan/model/verifier/evidence digest。普通请求不能改写 route-owned retry ceiling，且没有 real credential/provider execution 或 production prompt/workload；本地 model double 只测恢复机械链。它不是 hostile TOCTOU、事实正确性或来源独立性证明，不是 OS/tenant/sandbox，未有 paired cohort 或收益结论，Research 为 `ACCEPTANCE_PENDING_EXTERNAL`、benefit `UNPROVEN` |
| Code Historical Benchmark（trusted-process） | 用受保护 clean source 建立一次性 private bare clone，再给 baseline/search arm 建同 commit detached worktree；仅给仓库自有受信 adapter 一个 lower-case repository-relative allow/deny 文件 API，输出 redacted tracked/untracked diff、changed-path/verifier evidence。D19 可将冻结 `python -m pytest -q <repo-relative-test>` 预注册为 verifier plan，在 worktree 根目录 shell-free 子进程执行，记录无路径命令/退出/输出 digest/耗时回执；只有私有物料全部删除且 source fingerprint 一致时产生 `VERIFIED_CLEAN`，专用 service 才登记 terminal receipt | [`planning_search/code_benchmark_models.py`](../../../host_brain/planning_search/code_benchmark_models.py)、[`planning_search/code_benchmark_sandbox.py`](../../../host_brain/planning_search/code_benchmark_sandbox.py)、[`planning_search/code_benchmark_ledger.py`](../../../host_brain/planning_search/code_benchmark_ledger.py)、[`planning_search/code_benchmark_verification_contracts.py`](../../../host_brain/planning_search/code_benchmark_verification_contracts.py)、[`planning_search/code_benchmark_verifier.py`](../../../host_brain/planning_search/code_benchmark_verifier.py)、[`planning_search/code_benchmark_experiment.py`](../../../host_brain/planning_search/code_benchmark_experiment.py)、[`persistence/code_benchmark_ledger_repository.py`](../../../host_brain/persistence/code_benchmark_ledger_repository.py) | `DomainBenchmarkWorkload/Arm/Outcome`、Git、受限 pytest subprocess、0106 SQLite ledger | Host/TaskGraph/AgentExecutor、真实模型、ModelBudgetGuard、W2 WorkspaceGrant、container/tenant sandbox、winner/promotion authority；不可信 model/skill/plugin/hidden verifier | `PARTIAL_D19_ALLOWLISTED_SUBPROCESS_VERIFICATION_EXPERIMENT`；只证明 cleanup-gated durable experiment evidence 与两项受控测试预算，必须标记 `PRIVATE_BARE_WORKTREE_TRUSTED_PROCESS_ONLY` / `network_isolation=NOT_ENFORCED`；没有模型/工具/token/cost budget、cohort/statistical uplift、crash cleanup worker 或收益结论 |
| Agent Runtime | Agent contract、executor、workspace/protected artifact、write materializer、projection/terminal recovery、verification 与 studios | [`agent_runtime/application_ports.py`](../../../host_brain/agent_runtime/application_ports.py)、[`app/agent_runtime_composition.py`](../../../host_brain/app/agent_runtime_composition.py)、[`agent_runtime/executor.py`](../../../host_brain/agent_runtime/executor.py)、[`agent_runtime/governed_skill_work_order.py`](../../../host_brain/agent_runtime/governed_skill_work_order.py)、[`agent_runtime/accepted_workspace_artifact.py`](../../../host_brain/agent_runtime/accepted_workspace_artifact.py)、[`agent_runtime/workspace_write_promotion.py`](../../../host_brain/agent_runtime/workspace_write_promotion.py)、[`agent_runtime/workspace_runtime.py`](../../../host_brain/agent_runtime/workspace_runtime.py) | Host ports、Model/Capability、TaskStore、ReviewStore、Workspace lifecycle、Acceptance Effect、checkpoint UoW | 模块全局 bundle、入口身份、Task 权威、公开 raw artifact、自动修改源分支、payload 注入 Workspace authority | `PARTIAL_W2_INTERNAL_AND_REVIEWED_AGENT_SKILL_PROMOTION / APP_LOCAL_PORT_CUTOVER_PARTIAL`；focused write materializer 负责 path/size/patch/commit，不膨胀 allocator；Internal 与三条 risk-4 Agent Skill 共用一个 app-local Workspace owner。WorkerProjection 单一 INSERT owner；Skill promotion 在 visible Effects 前重开 durable acceptance lineage。legacy Executive generic delegate、一般恢复、Domain Pack/其它 Skill、逐操作 fence/强杀、OS/container/tenant/handle-level TOCTOU 与 canary 仍缺 |
| Domain Packs | Research、Media Translation、Light Novel、Pentest 等声明式领域包与生命周期 job | [`domain_packs/registry.py`](../../../host_brain/domain_packs/registry.py)、[`domain_packs/production_runtime.py`](../../../host_brain/domain_packs/production_runtime.py)、[`domain_packs/contracts.py`](../../../host_brain/domain_packs/contracts.py) | 平台 ports、Agent Runtime、Capability Broker | 复制平台调度、自建授权 | `PARTIAL`；存在生产 job 资产，尚未全部收敛到统一 `WorkOrderV3` 主链 |
| Pentest Domain | 授权范围、阶段执行、工具 adapter、finding/report、攻击图和自主策略 | [`pentest/production_runtime.py`](../../../host_brain/pentest/production_runtime.py)、[`pentest/secure_run.py`](../../../host_brain/pentest/secure_run.py)、[`pentest/agent/agent_loop.py`](../../../host_brain/pentest/agent/agent_loop.py) | Secure authority、受限工具 port、PlanningSearch adapter、领域 repositories | 绕过 scope/approval 的真实工具调用 | 领域功能 `WIRED`；L3 仍 `SHADOW_UNCALIBRATED`，且领域包内部责任过密 |
| Tribe / Organ / BioOps | 意图分流、内部事件、生理隐喻 agent、organ 健康与 BioOps 专项工作流 | [`tribe/thalamus_agent.py`](../../../host_brain/tribe/thalamus_agent.py)、[`tribe/event_bus.py`](../../../host_brain/tribe/event_bus.py)、[`organ_ops/organ_registry.py`](../../../host_brain/organ_ops/organ_registry.py)、[`bio_ops/workflow.py`](../../../host_brain/bio_ops/workflow.py) | Model invocation port、typed event、受治理 capability | 任务真相、长期状态的平行 writer | `PARTIAL`；应保持为策略/领域 proposal 层，不能成为第二执行内核 |

### 4.4 模型、能力、工具与 Skill

| 模块 | 当前责任 | 主入口/代码证据 | 允许依赖 | 不应拥有 | 状态/缺口 |
|---|---|---|---|---|---|
| Model Runtime | 路由目录、provider adapter、prompt registry、ModelAttempt、重试/输出合同和审计 | [`model_runtime/model_attempt_service.py`](../../../host_brain/model_runtime/model_attempt_service.py)、[`model_runtime/production_composition.py`](../../../host_brain/model_runtime/production_composition.py)、[`model_runtime/prompt_registry.py`](../../../host_brain/model_runtime/prompt_registry.py) | provider ports、route policy、audit journal | 业务路由 authority、未受管的外部工具 | `WIRED`；为所有模型调用的目标主干 |
| Capability Plane | Capability request/grant/attempt/receipt、policy 和执行 journal | [`capability_plane/broker.py`](../../../host_brain/capability_plane/broker.py)、[`capability_plane/contracts.py`](../../../host_brain/capability_plane/contracts.py)、[`capability_plane/production_composition.py`](../../../host_brain/capability_plane/production_composition.py) | Governor grant、descriptor、adapter、journal | 不受授权的自行执行 | `WIRED / MATURITY_FAIL_CLOSED_IN_PRODUCTION`；生产 IntentDecisionEngine 显式启用 restrict-only scorecard 门禁，缺失/伪造 scorecard 最高风险 |
| Tool Runtime | 工具目录、Broker、权限守卫、实际 adapter | [`composition/tool_runtime.py`](../../../host_brain/composition/tool_runtime.py)、[`tools/`](../../../host_brain/tools)、[`app/tool_routes.py`](../../../host_brain/app/tool_routes.py)、[`app/governed_tool_runtime.py`](../../../host_brain/app/governed_tool_runtime.py) | Capability Plane、Audit、外部 integration | 自己颁发 grant、自建 Task | `PARTIAL_GOVERNED_HTTP / PRODUCTION_FAIL_CLOSED`；`/tools/execute` 已进入 Secure Ingress 与 canonical TaskGraph，0114 扩展 Worker Projection；原始 Broker direct/bound API 均拒绝。Frozen Tool Worker 的 app-local governed execution port 先由 `TaskStore` 在单个 SQLite 读事务内原子复验 Graph/Node/Attempt/Lease/Reservation 与 owner/fence/expiry，再分阶段 fail-closed 复验 node grant 和 exact binding；全部通过后 `before_handler` 才在 handler 前写入 `PENDING` marker，三段不属于同一原子区间。真实 SQLite 测试 adapter 证明 Review/replay/Receipt。process-owned MCP accessor/slot 已删除，旧 direct/nested callback handler=0，registry 只读发现保留；普通 ReAct `GovernedToolNode → CapabilityBroker` 仍待独立审计。Tool seed readiness 早于 runtime activation 的 P2 尚未修复；executor=0 当前阻止误报 `READY`，首个真实 executor 启用前必须统一状态机。生产 seed 仍无 workspace-bound 真实 governed Tool/MCP executor，HTTP health 与 Host startup 均为 DEGRADED |
| ReAct Skill/Delegate 嵌套入口 | 向模型公开 Skill/Delegate 候选并在统一 TaskGraph 外直接执行的 legacy 分支 | [`agent_runtime/agent_tools.py`](../../../host_brain/agent_runtime/agent_tools.py)、[`agent_runtime/executor.py`](../../../host_brain/agent_runtime/executor.py)、[`app/agents/react_loop.py`](../../../host_brain/app/agents/react_loop.py) | 只读 ToolSpec metadata、governed admission reason | 在当前 Agent 节点内执行 `run_skill` / `dispatch_sub_agent` | `RETIRED_FAIL_CLOSED`；两类动作已从发现面清除，注入旧 ToolSpec 也在 low-level executor 前拒绝。恢复能力必须编译为新的 immutable TaskGraph revision；普通 ReAct Tool 路线和完整 executor 不在本切片完成范围 |
| 旧 SubAgent Compatibility Gate | 历史 request/response codec 与只读 registry view；direct dispatch 的确定性拒绝 | [`app/sub_agent_protocol.py`](../../../host_brain/app/sub_agent_protocol.py)、[`app/handlers/intent_agent_handler.py`](../../../host_brain/app/handlers/intent_agent_handler.py) | 版本化兼容合同、冻结 lineage 诊断 | handler registry、模型 gateway、独立 execution log、工具/Agent 执行 | `RETIRED_FAIL_CLOSED`；无 module-global singleton，Executive compatibility port/wiring/`_dispatch_to_sub_agent` 也不再携带 accessor，direct dispatch 返回 `LEGACY_SUB_AGENT_DISPATCH_RETIRED`，Intent 只能提出 governed handoff。正向 Delegate 仅 `pentest_agent` read-only adapter；一般 Delegate、模型预算与执行闭环仍未完成 |
| Built-in Skills | 内置 skill metadata、permission、scheduler profile 和 executor | [`skills/registry.py`](../../../host_brain/skills/registry.py)、[`skills/impl/`](../../../host_brain/skills/impl) | Tool/Capability port、冻结输入 | 目录扫描自动提权、生产状态 authority | `WIRED`；这里是代码包，不是已删除的顶层杂物 `skills/` |
| Skill Supply Chain | 签名、inventory/SBOM 检查、quarantine/stage、证据绑定和生命周期回执 | [`skills/supply_chain.py`](../../../host_brain/skills/supply_chain.py)、[`skills/signed_intake_http.py`](../../../host_brain/skills/signed_intake_http.py)、[`skills/skill_lifecycle.py`](../../../host_brain/skills/skill_lifecycle.py)、[`persistence/skill_lifecycle_repository.py`](../../../host_brain/persistence/skill_lifecycle_repository.py) | 信任键、Quarantine、Supply Chain Repository、Lab/Review evidence | 未经验证的 import/activation、目录扫描 | `PARTIAL_NE05_EVIDENCE_GATED_LIFECYCLE`；0109 将 staged→activate/degrade/rollback 绑定 evidence，rollback 只重开目标版本自己既有 `ACTIVATED` receipt 的 Lab/Review/SBOM/signature/sandbox 证据，未独立激活或跨 skill target 会拒绝；V1 URI 尚未解析真实 authority，Supply Chain/evidence/receipt 仍非同一 UoW，Host resolver 也未切流，外部 provider canary 与真实 Review/activation composition 仍待完成 |
| External Integrations | LLM/Codex CLI、LangGraph、MCP、LSP、browser/vision 等 adapter | [`integrations/`](../../../host_brain/integrations)、[`mcp/`](../../../host_brain/mcp)、[`lsp/`](../../../host_brain/lsp)、[`vision/`](../../../host_brain/vision) | 外部 SDK/进程、内部 typed port | 业务策略、长期状态、授权决策 | `PARTIAL`；多个 adapter 仍与上层实现形成循环依赖 |

### 4.5 记忆、知识和 Canon

| 模块 | 当前责任 | 主入口/代码证据 | 允许依赖 | 不应拥有 | 状态/缺口 |
|---|---|---|---|---|---|
| Life Memory | 记忆写入价值评估、admission、episode/entity/collection、consolidation/sleep 和 projection | [`memory/life_memory/service.py`](../../../host_brain/memory/life_memory/service.py)、[`memory/life_memory/consolidator.py`](../../../host_brain/memory/life_memory/consolidator.py)、[`persistence/life_memory_repository.py`](../../../host_brain/persistence/life_memory_repository.py) | Memory Guard、repository port、projection port | 最终表达决策、Canon 直写 | `WIRED / PARTIAL`；与旧 episodic/semantic/canon memory 资产并存 |
| Retrieval | 多路 recall、全文/向量检索、融合评分和降级 | [`retrieval/recall_fusion.py`](../../../host_brain/retrieval/recall_fusion.py)、[`retrieval/full_text_searcher.py`](../../../host_brain/retrieval/full_text_searcher.py)、[`retrieval/markdown_kb_store.py`](../../../host_brain/retrieval/markdown_kb_store.py) | 只读 store ports、Chroma、SQLite/Neo4j adapter | 修改记忆/人格、伪造来源 | `WIRED / COMPLEX`；多存储语义尚未收敛为统一 Knowledge OS |
| Canon | proposal、approve/reject、apply journal、文档投影和 rollback | [`canon/application.py`](../../../host_brain/canon/application.py)、[`memory/canon_apply_service.py`](../../../host_brain/memory/canon_apply_service.py)、[`persistence/canon_repository.py`](../../../host_brain/persistence/canon_repository.py) | approval repository、受限文件副作用 port | 无审批的世界设定写入 | `WIRED`；是 Canon 的局部闭环，不等于 Knowledge OS |
| Knowledge OS | provenance、版本、过期、冲突、Decision Record 和 proposal promotion | 尚无单一生产 owner | Memory/Retrieval/Canon 只能作为底层资产 | 任意 trace miner 直接改规则 | `MISSING` |

### 4.6 数字生命、人格与关系

| 模块 | 当前责任 | 主入口/代码证据 | 允许依赖 | 不应拥有 | 状态/缺口 |
|---|---|---|---|---|---|
| Digital Life Core | LifeCommand、event、OCC/version、deterministic reducer、projection/outbox | [`digital_life/service.py`](../../../host_brain/digital_life/service.py)、[`digital_life/reducer.py`](../../../host_brain/digital_life/reducer.py)、[`persistence/life_command_repository.py`](../../../host_brain/persistence/life_command_repository.py) | Life contracts、repository port、projection/delivery port | Surface、Scheduler 或 Tribe 各自维护平行 Life 真相 | `WIRED`；是 Life 状态的 canonical 基础 |
| World/Environment Projection | 当前环境与世界事实只读投影、workspace-scoped Context 解析 | 0111、[`persistence/world_observation_journal.py`](../../../host_brain/persistence/world_observation_journal.py)、[`runtime/world_observation_application.py`](../../../host_brain/runtime/world_observation_application.py)、[`runtime/world_resolver.py`](../../../host_brain/runtime/world_resolver.py) | typed ObservationPort、canonical source verifier、Host lease、server-owned signals | caller 分配 revision、request body 伪造 Scene/World、截断分页、读取时续期 | `PARTIAL_NE03_PRODUCTION_OBSERVATION_WIRED`；Host lifespan 中的 application 写入并重开 World，TTL 显式过期、competing refs 与 captured-head replay 已实现；上游 Bond 原始证据与预测世界模型仍缺 |
| Persona Surface | 人格表达、reply plan、情感/关系质感、memory grounding、文本质量门禁 | [`persona/surface_main_chain.py`](../../../host_brain/persona/surface_main_chain.py)、[`persona/surface_runtime.py`](../../../host_brain/persona/surface_runtime.py)、[`persona/reply_plan.py`](../../../host_brain/persona/reply_plan.py) | 只读 Persona/Life/Memory context、Model Runtime | 任务调度、能力授权、人格永久直写 | `WIRED / LARGE`；表达链能力丰富，边界与文件仍过密 |
| Scene Policy | 统一生成表达、主动性和 Task admission 约束的只读场景决策 | 0112、[`runtime/scene_resolver.py`](../../../host_brain/runtime/scene_resolver.py)、[`runtime/context_application.py`](../../../host_brain/runtime/context_application.py)、[`runtime/scene_consumer_projection.py`](../../../host_brain/runtime/scene_consumer_projection.py) | WorldSnapshot、typed context、app-local Guardian、Host lease | caller 指定最终 Scene/Guardian 状态、三消费者各自重算、写入 World/Life/Persona | `PARTIAL_NE03_THREE_CONSUMERS_WIRED_LOCAL_ACCEPTANCE_PASSED`；Surface、Proactive、Task admission 冻结同一 revision/digest，旧 builder 无这三处生产调用；八类场景 commit-bound 验收已通过 |
| Intent Arbiter | 将 Scene、Task、关系、长期目标、风险和 Grant 约束合并且只收紧 | [`runtime/production_intent_authority.py`](../../../host_brain/runtime/production_intent_authority.py)、[`runtime/intent_admission.py`](../../../host_brain/runtime/intent_admission.py)、[`runtime/intent_arbiter.py`](../../../host_brain/runtime/intent_arbiter.py) | canonical HostCommand/WorkOrder/TaskGraph/Scene/Grant + durable ReviewStore | 裸信 caller digest、遗漏节点、过期/撤销 Grant、扩大权限或执行动作 | `PARTIAL_NE03_PRODUCTION_ADMISSION_WIRED_LOCAL_ACCEPTANCE_PASSED`；准备/完成 receipt set 与 Review continuation 已接 Task Runtime；长周期目标 owner 仍缺 |
| Action Preview Authority | 在高风险执行点重开已有 authority 并冻结 exact action/context/scene；自身不授予执行权 | [`runtime/action_preview_authority.py`](../../../host_brain/runtime/action_preview_authority.py)、[`runtime/action_preview_resolver.py`](../../../host_brain/runtime/action_preview_resolver.py)、[`governor/action_preview.py`](../../../host_brain/governor/action_preview.py) | HostCommand/TaskGraph/Attempt/Lease/Grant/Guardian、Runtime Event、typed EffectDescriptor | caller 自报 node grant/risk/capability、扩展 scope/args、用 Preview 替代 Grant | `PARTIAL_NE03_PRODUCTION_AUTHORITY_WIRED_LOCAL_ACCEPTANCE_PASSED`；Skill/Delegate/Scheduler/Acceptance/Life 已接线，终端正向还覆盖 sys-mode、terminal、Edge、Operation/Effect reconciliation、`task.operator_control` 与 `operator.state_control`；旧任务/effect、剩余 `23/37` 个 Operator 直写、reply-performance/Gateway voice/Avatar 直通隔离，后三者和 Operator 其余功能迁移仍属 NE-01 |
| Runtime Guardian / Service Catalog | app-local health/degradation/recovery/SLO，以及 Host 五阶段启动 authority | 0112、0113、[`runtime/runtime_guardian.py`](../../../host_brain/runtime/runtime_guardian.py)、[`app/health_routes.py`](../../../host_brain/app/health_routes.py)、[`service_catalog/host_startup.py`](../../../host_brain/service_catalog/host_startup.py)、[`service_catalog/rollout_authority.py`](../../../host_brain/service_catalog/rollout_authority.py)、[`app/http_hooks.py`](../../../host_brain/app/http_hooks.py) | Host runtime lease、Runtime Event、append-only rollout operation/receipt/head、typed health ledger/probe ports | module-global singleton、裸 owner/fence、伪造 reducer/terminal、路由自行解析 process locator、跳阶段或收养未知 listener | `PARTIAL_NE03_APP_LOCAL_HEALTH_PROBE_STATE_WIRED`；startup probe 只通过 app-local Guardian sink 发布，缺失 sink 时显式 `UNBOUND` 而不回退 global reducer；ASGI 自有 Guardian task、cache 与 transport 是正式 Host 的唯一 health-loop owner，scope close 只清理本 scope；生产 `/health`、`/ready` 和内部免疫实时报告显式消费 composition 提供的端口，缺失即 503；fresh DB 逐级到 ENFORCE、crash takeover、重启/append 不确定性与状态机已有；legacy provider、Windows graceful stop 和部署多实例 canary 待收口 |
| Bond | 用户身份解析、关系阶段、里程碑、叙事和关系投影 | [`bond/bond_stage_engine.py`](../../../host_brain/bond/bond_stage_engine.py)、[`bond/milestone_tracker.py`](../../../host_brain/bond/milestone_tracker.py)、[`bond/bond_projection.py`](../../../host_brain/bond/bond_projection.py) | 事件、只读 Life/Memory、repository | 绕过人格版本激活直改 trait | `PARTIAL / CANDIDATE ONLY`；stage/milestone 可触发 guard 后候选，但中央围栏禁止 trait 直写 |
| Persona Growth | trait drift/growth 候选、interaction/emergent 候选和部分反馈 | [`persona/persona_growth.py`](../../../host_brain/persona/persona_growth.py)、[`persona/evolution_orchestrator.py`](../../../host_brain/persona/evolution_orchestrator.py)、[`persona/emergent_growth_engine.py`](../../../host_brain/persona/emergent_growth_engine.py) | 只读 Persona 与 guard；未来只产生 Feedback/Identity proposal | 非迁移 trait 写入、伪造已应用历史或消费一次性状态 | `CENTRAL_FENCE_WIRED`；NE-01 已封口并静态禁止新 writer，NE-06 再建立 proposal 闭环 |
| Lifecycle Runtime | Scheduler handler、Life jobs、背景投影和 proactive dispatch | [`lifecycle_runtime/composition.py`](../../../host_brain/lifecycle_runtime/composition.py)、[`lifecycle_runtime/life_jobs.py`](../../../host_brain/lifecycle_runtime/life_jobs.py)、[`lifecycle_runtime/proactive_dispatch.py`](../../../host_brain/lifecycle_runtime/proactive_dispatch.py) | Scheduler、Life Command、Governor、delivery ports | 新的时钟 authority、人格直写 | `PARTIAL`；定时执行闭环受 Scheduler runtime 故障限制 |
| Evolution / Identity | 实验隔离策略、ExperimentSpec/Run/Report/Proposal durable ledger、Feedback/Knowledge/Identity proposal ledger 与 rollback 事件 | [`evolution_lab/contracts.py`](../../../host_brain/evolution_lab/contracts.py)、[`evolution_lab/policy.py`](../../../host_brain/evolution_lab/policy.py)、[`evolution_lab/runner.py`](../../../host_brain/evolution_lab/runner.py)、[`persistence/evolution_lab_ledger_repository.py`](../../../host_brain/persistence/evolution_lab_ledger_repository.py)、[`evolution/contracts.py`](../../../host_brain/evolution/contracts.py)、[`persistence/evolution_governance_ledger.py`](../../../host_brain/persistence/evolution_governance_ledger.py)、[`release/identity_snapshot.py`](../../../host_brain/release/identity_snapshot.py) | 隔离实验、Review、版本存储 | 直接生产激活、Lab 副作用、proposal 外写入 | Evolution Lab `PARTIAL_NE04_DURABLE_PROPOSAL_LEDGER`；NE-06 的 0110 已提供 Feedback/Knowledge/Identity append-only proposal→Review→activation/rollback 事件与 namespace projection；旧增长入口适配、真实 Identity owner 和生产 cutover 仍待完成 |

### 4.7 渠道、Gateway 与 Edge

| 模块 | 当前责任 | 主入口/代码证据 | 允许依赖 | 不应拥有 | 状态/缺口 |
|---|---|---|---|---|---|
| Host Channels | Edge transport、TTS/VTS/MateEngine mapping、embodiment outbox 和 Life delivery transport | [`channels/edge_transport.py`](../../../host_brain/channels/edge_transport.py)、[`channels/embodiment.py`](../../../host_brain/channels/embodiment.py)、[`channels/embodiment_outbox.py`](../../../host_brain/channels/embodiment_outbox.py)、[`channels/mateengine.py`](../../../host_brain/channels/mateengine.py) | 跨进程 contracts、Acceptance effect/outbox、terminal Action Preview | 未验证直接物理副作用、无 Preview 的高风险网络/设备调用 | `PARTIAL_FIRST_GOVERNED_EXPRESSION_SLICE / DEVICE_DEPENDENT`；raw HTTP transport 与低层 command port 不再公开；Operator 表情命令经 `edge.embodiment_enqueue`、terminal Preview 与 durable outbox 进入 `set_expression`，exact replay/receipt reconciliation 已接。旧 voice/performance/avatar 直通和更多 command type 仍失败关闭，真实设备留 NE-07 |
| Gateway Transport | 平台 webhook 验签、归一化、durable intake/delivery、receipt 和重试 | [`edge_client/gateway/server.py`](../../../edge_client/gateway/server.py)、[`edge_client/gateway/delivery_runtime.py`](../../../edge_client/gateway/delivery_runtime.py)、[`persistence/gateway_transport_repository.py`](../../../host_brain/persistence/gateway_transport_repository.py) | `natsume_contracts.edge`、Host HMAC 输入端、平台 adapter | Host 业务实现、对话状态真相 | `WIRED`；Gateway store factory 通过合同注入，但当前组装较大 |
| Edge Runtime | 本地 sensor/actuator composition、语音 pipeline、Avatar 状态和健康 | [`edge_client/runtime/bootstrap.py`](../../../edge_client/runtime/bootstrap.py)、[`edge_client/runtime/voice_pipeline.py`](../../../edge_client/runtime/voice_pipeline.py)、[`edge_client/actuators/`](../../../edge_client/actuators)、[`edge_client/senses/`](../../../edge_client/senses) | Edge contracts、本地 provider/OS adapter | Host app/audit 实现、全局任务 authority | `WIRED / ENVIRONMENT_DEPENDENT`；边界扫描禁止 Edge 导入 Host 实现 |
| Gateway Platform Adapters | QQ、微信、飞书、企微、Telegram、LINE 协议适配 | [`edge_client/gateway/adapters/`](../../../edge_client/gateway/adapters) | 平台 SDK/API、Gateway model | 跨平台业务策略、Host 任务状态 | `PLATFORM_DEPENDENT`；未配凭据或本地 bridge 时应显式 degraded |
| Desktop Pet | HMAC/replay/rate-limit 本地 bridge、Live2D 渲染和交互 | [`desktop_pet/bridge_security.cjs`](../../../desktop_pet/bridge_security.cjs)、[`desktop_pet/main.cjs`](../../../desktop_pet/main.cjs)、[`desktop_pet/renderer/pet.js`](../../../desktop_pet/renderer/pet.js) | Edge/Host 签名命令 | 人格、Task 或 Life 状态 writer | `WIRED / LOCAL ONLY` |

### 4.8 观测、持久化和 composition

Tribe 的偏好与对话经历 state helper 只接受 Host lifespan ledger；其 `3/15/6、Container48/2/45` 库存只作历史，现行生产 locator 已全部清零，ResourceManager status 也已迁至 0123 journal。Host-root owner 与底层 Authority identity 隔离仍不表示外部 namespace、其它外部资源恢复、正式 activation、Tribe、Chat Acceptance 或完整 Composition 已完成。

受治理 Chat 的 Surface 主链由 `SurfaceRuntimePorts` 承接：Host lifespan 一次绑定 AgentRuntime/Life authority，Internal/Tool Runtime 只传递 frozen bundle；普通 Chat、Gateway 与 ASR 则由 ASGI 接收同一 `InputRuntimeApplicationPorts`，再经 `TribeRuntimeAgentPorts` 取得 Thalamus/ANS/Immune。聊天免疫门禁只接收 `TribeRuntimeAgentPorts.require_immune_agent()`，输入归一化只接收 `InputRuntimeApplicationPorts.require_immune_agent()`；缺失 provider 分别拒绝为 `HOST_TRIBE_IMMUNE_AGENT_NOT_INJECTED`、`HOST_INPUT_IMMUNE_AGENT_NOT_INJECTED`，不重新读取 singleton。普通检查器故障仍记录 `IMMUNE_EVALUATE_DEGRADED`。补充边界集合 `141 passed in 9.66s` 与既有 `131 passed in 7.25s`、旧免疫 `43 passed in 7.75s`、#103+#104 的 `196 passed in 21.08s` 均重叠，均仅证明依赖收口，不能升级为 Chat acceptance、Tribe app-local 化、generic Delegate、七路线或 NE-01 完成。

| 模块 | 当前责任 | 主入口/代码证据 | 允许依赖 | 不应拥有 | 状态/缺口 |
|---|---|---|---|---|---|
| Observability Query | projection query、trace、metrics、health、degradation、reconciliation read 与 auth | [`observability/operator_query_gateway.py`](../../../host_brain/observability/operator_query_gateway.py)、[`app/obs_route_ports.py`](../../../host_brain/app/obs_route_ports.py)、[`app/observability_runtime/life_body_source.py`](../../../host_brain/app/observability_runtime/life_body_source.py)、[`app/obs_projection_jobs.py`](../../../host_brain/app/obs_projection_jobs.py) | 只读 projection/store/source reader、版本化Host合同 | domain writer、原始 secret/signature | `D7C_LIFE_BODY_DURABLE_RISK_AND_JOURNAL_PARTIAL`；21 producer声明为`6 durable / 3 static / 8 partial / 4 authenticated / 0 unavailable`。Life Body从同app四个公开durable roots与canonical ledger有界lineage冷聚合；缺根/无效/过期/查询失败写typed unavailable，保留安全与Gateway风险，未变化根不追加journal，输出去除content/private字段。真实Edge未证明，底层Host source authority仍未收口 |
| Operator / Scheduler Admin Command | 受认证的运维命令入口、受治理路由和 outcome journal | [`observability/secure_command_ingress.py`](../../../host_brain/observability/secure_command_ingress.py)、[`app/governed_operator_runtime.py`](../../../host_brain/app/governed_operator_runtime.py)、[`app/governed_operator_state_control_runtime.py`](../../../host_brain/app/governed_operator_state_control_runtime.py)、[`scheduler/scheduler_routes.py`](../../../host_brain/scheduler/scheduler_routes.py)、accepted handlers | Secure Ingress、typed command ports、Task Runtime、Acceptance Effect runtime、Task/Review/Scheduler/Host policy canonical owners、0119/0126/0128/0129 stores | Query handler 直写 domain store、caller 自报 request/source、bearer 落入 WorkOrder/SQLite、独立 Effect recovery loop | `WIRED / PARTIAL`；十四条 Operator 命令进入 governed runtime，其余 `23/37` 阻断。Scheduler Admin create/update/delete/enable/disable 以独立 `SCHEDULER` surface 进入同一 TaskGraph/accepted effect，不计入 Operator 库存；完整 commit-bound acceptance、真实 HTTP/Edge canary仍缺 |
| Persistence | 按聚合提供 SQLite repository、OCC、journal/outbox 和 read projection | [`persistence/`](../../../host_brain/persistence)、[`persistence/hard_state_journal.py`](../../../host_brain/persistence/hard_state_journal.py)、[`persistence/host_mode_policy_journal.py`](../../../host_brain/persistence/host_mode_policy_journal.py)、[`persistence/task_node_budget_store.py`](../../../host_brain/persistence/task_node_budget_store.py)、[`persistence/task_node_verification_store.py`](../../../host_brain/persistence/task_node_verification_store.py)、[`persistence/task_node_acceptance_store.py`](../../../host_brain/persistence/task_node_acceptance_store.py)、[`persistence/task_review_denial_store.py`](../../../host_brain/persistence/task_review_denial_store.py)、[`persistence/review_pending_uow.py`](../../../host_brain/persistence/review_pending_uow.py)、[`persistence/task_operation_recovery_hold_store.py`](../../../host_brain/persistence/task_operation_recovery_hold_store.py)、[`persistence/task_operation_reconciliation_evidence_store.py`](../../../host_brain/persistence/task_operation_reconciliation_evidence_store.py)、[`persistence/task_acceptance_effect_reconciliation_store.py`](../../../host_brain/persistence/task_acceptance_effect_reconciliation_store.py)、[`audit/migration_runner.py`](../../../host_brain/audit/migration_runner.py) | SQL/driver、稳定 contracts | 业务决策、route 编排、人格策略 | `WIRED / LARGE / STATIC_LAYERING_PASS`；当前 migration head=`0135_task_review_denial_settlement.sql`。0126～0132 保持既有 Operation/Effect/recovery/Budget 边界；0133 增加 durable Verification，0134 增加 durable Acceptance/Review 创建，0135 增加 Review denial 的 Node/Graph/Budget/Outbox 原子 settlement。Task Runtime composition 独立验证 0133～0135 readiness。Gate 1/3 通过不代表一般恢复或各应用域生产完成 |
| Runtime Eventing | runtime event 投影、投递恢复与 0112 schema readiness repository port | [`eventing/runtime_event_projection.py`](../../../host_brain/eventing/runtime_event_projection.py)、[`persistence/runtime_event_repository.py`](../../../host_brain/persistence/runtime_event_repository.py)、[`integrations/runtime_events.py`](../../../host_brain/integrations/runtime_events.py) | event contract、repository | 替代 domain aggregate、runtime 私有 SQLite 连接 | `WIRED / SUPPORTING`；runtime 仅经 `RuntimeEventStore` readiness port，schema 检查归 repository |
| Host Mode Runtime | app-local mode 与 policy owner、durable bind、CAS transition、restart history | [`app/host_hard_state.py`](../../../host_brain/app/host_hard_state.py)、[`app/host_mode_policy.py`](../../../host_brain/app/host_mode_policy.py)、[`persistence/hard_state_journal.py`](../../../host_brain/persistence/hard_state_journal.py)、[`persistence/host_mode_policy_journal.py`](../../../host_brain/persistence/host_mode_policy_journal.py)、[`app/governed_operator_state_control_runtime.py`](../../../host_brain/app/governed_operator_state_control_runtime.py)、0115/0129 | Host lifecycle、canonical DB、Host runtime authority provider、governed Operator Effect | 环境变量运行期总线、第二 writer、未持久化 production mutation | `WIRED_DURABLE_MODE_AND_POLICY_AUTHORITY / OPERATOR_SLICE_PARTIAL`；两个聚合各自 event/head 同事务、active lease/fence、exact replay、tamper/fault rollback、1001+ restart replay已有定向证据。`sys_mode.switch`、rules/event 均从 TaskGraph/Review/Effect 接入；自动规则、last-interaction 和事件 outcome 已持久。完整 Host 进程重启、production canary和NE-01 wave integration未完成 |
| Composition | 构造 Task、Life、Memory、Canon、Scheduler、Tool、Obs 与 Host-lifespan AgentRuntime 对象图 | [`app/application_port_wiring.py`](../../../host_brain/app/application_port_wiring.py)、[`composition/host_application.py`](../../../host_brain/composition/host_application.py)、[`composition/host_authority_runtime.py`](../../../host_brain/composition/host_authority_runtime.py)、[`app/host_lifecycle_composition.py`](../../../host_brain/app/host_lifecycle_composition.py)、[`app/agent_runtime_composition.py`](../../../host_brain/app/agent_runtime_composition.py)、[`app/input_runtime_composition.py`](../../../host_brain/app/input_runtime_composition.py)、[`app/tool_runtime_composition.py`](../../../host_brain/app/tool_runtime_composition.py)、[`app/obs_application.py`](../../../host_brain/app/obs_application.py) | adapter和构造器 | 业务方法、模块全局真相 | `INCOMPLETE / PRODUCTION_LOCATOR_PHYSICALLY_RETIRED / OBS_APP_LOCAL_SUPERVISION_WIRED`；生产 locator module/field/lock/ref=`0/0/0/0`，pytest-only bridge 不发布；ResourceManager status 已由 0123 journal 持久化。Obs daemon 的 secret-free wave/failure/recovery snapshot 已进入真实 `/health`，但 durable SLO/Guardian acknowledgement/canary 未完成；外部 namespace、Token/MCP durable restart、其它外部资源恢复、legacy Executive generic delegate、真实 Edge/executor 与完整 Host/Worker/Obs typed scopes仍缺，NE-01 保持 `IN_PROGRESS` |
| Shared Contracts | Host/Edge 间 Task、Life、Identity、Operator、Edge/Embodiment 版本化数据协议 | [`natsume_contracts/`](../../../natsume_contracts) | 标准库、纯数据类型 | Host/Edge 具体实现、连接、全局容器 | `WIRED`；该包是真实跨进程边界，不是可删兼容目录 |

Gate 10 切片的证据分组为 Runtime Context `7 passed`、Catalog unit/contract `15 passed`、Host socket integration `6 passed`、Repository/Global 独立复跑 `16 passed`；最后一组与 15 项有重叠，不得相加。Global SQLite boundary=`PASS`。Gate 6 的完整 Obs app 首跑为 `13 passed + 1 test fixture failed`，旧 monkeypatch 修正后该 case `1 passed`；最终生命周期关键批次 `12 passed`，daemon cadence/shutdown/serialization/start-single-flight `4 passed`，最终定向集合 `19 passed`，各组有重叠不得相加。其关闭顺序是 bundle claim→compose→start→yield，finally 执行 stop/join/in-flight fence→精确 lease release→owned SQLite close→state clear；timeout/rollback close failure 保留原句柄，`dataclasses.replace()` 不能绕过同一 claim。后续监督切片又把 app-local daemon 的 running/generation/wave/failure/recovery 状态接入真实 `/health`，普通失败继续运行、fatal exit 进入 `FAILED` 且要求 close 后重启；首批 `6 passed in 13.13s`，最终受影响集合 `68 passed in 211.30s`。本切片仍无 durable SLO journal、Guardian acknowledgement、production canary 或 wave integration；NE-01 和完整架构项目未完成，`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。

Service Catalog 模块的分层子切片现由 `service_catalog/startup_coordinator.py` 承担 application 启动协调，`contracts/service_catalog.py` 提供稳定 DTO/校验/port，composition 与旧类型路径只同对象重导出；`rollout_authority.py` 只保留 Guardian observer，repository 只依赖 contracts。application→composition 2 条和 Catalog persistence→application 1 条已归零，Gate 1 SCC 缩为 application/persistence；该切片完成时其余 persistence→application 仍有 11 条（PlanningSearch 7、Evolution/Lab/Skill/World 4）。证据为 61 项 contract/unit、7 项真实 Catalog/Host socket、7 项边界复跑，集合可能重叠。

PlanningSearch 模块的第二个分层子切片把 core、benchmark、calibration、code、research 八组稳定合同/模型迁入纯 `contracts/planning_search/`，旧模块只做保持 `__all__`、类型 identity、`isinstance` 与序列化形状的同对象重导出。code benchmark、calibration、research shadow 三个 SQLite repository 已清除 7 条 persistence→application；边界 5 项和 D13/D18/D19/D20 60 项通过，另有 1 项因 Windows 无 symlink 权限跳过。Gate 1 仍有 application/persistence SCC 和 Evolution/Lab/Skill/World 4 条反向依赖；application→composition=0、Edge violation=0、reverse import=0，Gate 6/10 保持通过。这只是合同分层，不增加 PlanningSearch 功能或收益证明；无 migration，Composition 与 NE-01 仍 `IN_PROGRESS`，wave integration/remote CI 分别为 `NOT_RUN_PER_WAVE_POLICY`/`NOT_RUN_ACCOUNT_QUOTA`。

最后一个分层子切片新增纯 `contracts/evolution.py`、`evolution_lab.py`、`runtime_context.py`、`skill_lifecycle.py`，旧 Evolution/Lab/Runtime Context 路径同对象重导出，Skill Lifecycle 模块只保留应用服务并重导出合同。Evolution Governance、Evolution Lab、Skill Lifecycle、World Observation 仓储的 4 条 persistence→application 已清零；fresh Gate 1=`PASS`，issues/SCC/reverse imports 均为空，Edge finding/violation=0，Gate 6/10 继续通过。在该切片 commit-bound 快照中，full static 仍因 #87 组合后的 Gate 11 ToolBroker inventory 双命中为 `FAIL [11]`。边界 4 项与相关定向 32 项通过，py_compile/diff-check 通过；无 migration。该结果不提升 Evolution Lab、Skill 生命周期、Feedback/Knowledge/Identity 或 Universal Runtime 的生产状态。

Gate 11 后续切片明确区分既有 MCP 清理与 ToolBroker 清理；唯一 `ToolBroker(...)` constructor 仍由 app-local `GovernedHostToolRuntimeComposition → ToolRuntimeBoundary` 创建。前一 HardState 切片没有恢复这些 authority，并进一步删除 process `get_hard_state`、`set_sys_mode`、`get_endocrine_state` 与 `hard_state` slot/lock。视觉库存 named41/117、all-form47、Container54/4/49 与 HardState 的40/116/all-form46、Container52/3/48 都是历史；删除两个无消费者的 Obs cache 后，当前 Container 为50/3/46。fresh full static=`PASS`、`failed_gates=[]`；前一HardState集中定向210、D7b7、Operator/focus/runtime projection29分别保留。`HOST_SYS_MODES`/`HOST_MODE_TRANSITIONS`现由neutral contract冻结，HardState repository不再反向导入core application。0129 再把 mode rules、last-interaction 与 event outcome 交给 app-local Host policy owner。当前十四条 governed Operator command 与 Scheduler Admin 双前缀已接入 shared Effect owner，但真实workspace-bound governed executor、其他 `23/37` 个 Operator 入口、commit-bound acceptance与余下对象图仍缺，production canary/wave integration=`NOT_RUN_PER_WAVE_POLICY`，NE-01仍`IN_PROGRESS`，`REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA`。

## 5. Monitor UI 功能分区

Monitor UI 采用 React HashRouter，每个页面的导航分组、依赖服务、风险等级与 smoke 标记由 [`routeManifest.jsx`](../../../host_brain/app/monitor_ui/src/app/routeManifest.jsx) 统一声明。功能模块不应再在 README 中复制一份手写路由表。

| UI 分区 | 主要功能 | 主要后端 | 命令边界 |
|---|---|---|---|
| Cockpit | 数字生命总览与驾驶舱 | Obs + Host | 主要只读，操作必须转 Operator Command |
| Life Systems | Timeline、Pathology、Emotion、Bond、Memory、Runtime、Inner World、Self Model、Proactive | Obs 投影 + 少量 Host API | 不得直接修改 Life/Persona 底层 store |
| Observability | Overview、Sessions、Trace、BioOps、Metrics、Insights、Alerts、Logs | Obs | 只读 query gateway，私密字段必须脱敏 |
| Operations | Prompt、Persona、System、Canon | Host + Obs | 修改必须经过专用 application service 或 proposal/review |
| Execution | Gateway、Skills、Tools、Automation、Task Runtime、Tribe、Edge Command | Host + Obs | 高风险操作进 Secure Ingress/Governor，不在 React 端自造权限 |
| Security Lab | Security Immune | Host + Obs | hunt/probe/action 使用受治理命令 |
| Integrations | Body Integration | Host/Edge | 物理命令经 outbox/receipt，不直连设备 |

UI 访问边界分布在 [`src/shared/api/`](../../../host_brain/app/monitor_ui/src/shared/api) 和各 `features/*/api.js`。Operator Command 客户端有生成产物，不应在页面组件内临时拼接高风险请求。

## 6. 关键模块协作链

### 6.1 Chat / Gateway 输入

```text
HTTP Chat / ASR / Gateway
  → app adapter
  → Input Runtime normalization
  → Secure Ingress claim + principal
  → Tribe/intent draft + Governor decision
  → Cortex route
  → GovernedHostRuntime（chat/skill/delegate/plan）
  → HostCommand + WorkOrderV3 + TaskGraph
  → GovernedTaskCoordinator
  → worker + ModelAttempt/CapabilityBroker
  → Verification + Acceptance
  → effect intents
  → Conversation/Life/Memory/Gateway/Edge projection or delivery
```

关键边界：Input Runtime 不执行工具，Cortex 不写 Task 第二真相，worker 不修改冻结图，外部副作用必须发生在 Acceptance 之后。`task_progress` 与 `recent_turn` 虽可读取 durable projection，但在未创建 TaskGraph 的暂态 read route 内不写 visible conversation、Memory、Life、Preference、Outbox 或 Delivery；需要持久会话语义时必须由后续 governed Task 承担。

本批对“发生在 Acceptance 之后”增加了可核验边界：visible turn、Preference evaluation 和 Life projection 由三个独立 Effect 提交并重开 exact authority。Preference store gate不可用/非bool时失败关闭零写；配置false或malformed env保守解析为disabled，只写`DISABLED` receipt且零event/profile。启用时event、append-only child profile、evaluation receipt同一SQLite UoW，sealed-child trigger与full-set reconcile共同防篡改，`NO_SIGNAL`也有真实receipt。推荐反馈只能绑定admitted-time以前的exact conversation/session delivery与canonical digest。Surface response loss必须通过authenticated read-only query；Gateway从同一SQLite outbox payload复验`effect_id`，`NOT_FOUND`保持`UNKNOWN`。这不改变各Effect最终一致语义，也不构成NE-01波次验收。

Task composition 在开放运行时前按 migration/readiness → `recover_startup` → 0099 单次最多 10,000 个 terminal Graph inventory → 可选 default-off exact auto → publisher/surface/continuation 排序。Container 构造 Operation/Effect/terminal reconciliation service，并向 TaskStore 注入 verification-only Ed25519 公钥；`task_terminal.reconcile` 与 system auto 分别由独立默认关闭开关控制。Operator repair 要求 Secure Ingress=`ENFORCE`、可执行 Gateway 与公钥环，私钥不进入 Host。Cancellation production gate 在启用 cancellation 时拒绝 current terminal anomaly、缺失 trusted proof/supersession closure 的历史 case 和 quarantine，但不拒绝 terminal remediation Operator 自身。

Review 命令与内部 Agent 执行使用不同 Secure Ingress authority：`review.operator / review:decide` 才能 approve/reject/resume，actor 与 requester 绑定认证 principal，Task-bound Review create 只允许 Task Runtime 内部调用。0096 authority 是 secret-free 固定字段投影；READY claim 与首次 worker entry 精确绑定 Attempt/Lease/owner/fence。0097 以 writer CAS + partial unique index 保证多进程 node-grant batch 唯一。Review heartbeat 丢失不被描述为可撤销已经开始的 blocking/to_thread 外部副作用；此类执行由 Task lease 管理，旧 Review owner不得提交协调结果。

0093 将 Task Runtime Outbox delivery 独立收口为 durable authority：Store 持久化 claim owner/token/expiry/单调 fence，允许 expired takeover。0095 进一步要求 `TASK_GRAPH` Outbox 精确绑定现存 revision、非 Graph revision 为 `NULL`，冻结 `graph_revision` 的 INTEGER/nullable/no-default/non-PK 物理形状，并按 `type + id + revision` 隔离 predecessor/head；同 revision 保序，不同 revision 独立推进。历史数据只从 0090/0091/0092 decision、0092 intent、Attempt、唯一 revision，或 Outbox evidence 中 immutable HostCommand `command_id` 回填，后者覆盖正常 multi-revision CREATED，歧义会回滚整笔迁移；所有生产 Graph producer 都写 revision。新 Runtime Event key 使用 `graph_id@revision`，旧 journal 不改写并通过 source Outbox revision 兼容读取；Operator 按 exact revision 查询，ReviewStore 再按 canonical task-node subject ref revision 过滤。Publisher 逐条 `claim → idempotent append → fenced complete`，append 失败则 release 并设置 retry backoff/last error。Composition 将当次 boot/construction 唯一 `recovery_id` 派生为 Outbox owner，启动可关闭后台 worker，并按 aggregate-head 的最早 available/claim-expiry 与有界轮询自动续投。Drain 只在 canonical `total_pending=0` 时返回 `DRAINED`；非空但暂无 ready head 的 inventory 返回 `DEFERRED + next_wakeup_ts_ms`。`list_pending_outbox` 是完整 inventory，claim/backlog 才是 delivery-ready 视图；0093/0095 readiness 缓存在单个 Store 实例，热路径不重扫。0094 已实现独立的 cancellation scanner/claim/heartbeat/defer/expired takeover/continuation coordination，但它不复制 0092 业务状态，也不自动制造 operation/effect/review reconciliation 证据。

0095 还用 deterministic SQL digest 重算 revision-bound HostCommand 的 legacy governed Outbox ID/key，使 ACTIVE 等非 attempt transition exact 回填，同时保持升级前 ID/key 不变。新 Graph payload ref 带 `@revision`；旧 ref 只允许 durable replay compatibility，新写 trigger 拒绝。Runtime Event 的 `TASK_STORE_OUTBOX source_ref` unique partial index 将一条 Outbox 围栏为一条 journal，append 已成功但 complete 未确认的 legacy 重投只能 exact 返回既有事件。payload trigger 与 source index 同属 readiness/receipt-loss gate。

PlanningRecovery 显式 opt-in `include_graph_outboxes=true`，在同一 snapshot 事务读取真实 terminal payload ref；默认 false，普通 Scheduler/Operator 不增加 Outbox 扫描。只有旧版或替代 Store 未提供可选扩展字段时才合成 revisioned fallback；生产 DurableTaskStore 提供 inventory 后 fail closed。`created_ts=0` 的 timestamp 容差可用于 current revisioned 与 legacy unrevisioned 两种可重建 Event candidate，因为两条历史 append 路径都可能用时钟替代零值；除 timestamp 外的字段继续 exact。

节点局部执行由 [`DurableNodeExecutionRuntime`](../../../host_brain/task_runtime/node_execution_runtime.py) 编排，并共享 `AcceptedGraphFinalizationRuntime`。`DurableOperationRecoveryRuntime` 接管 exact canonical COMMITTED checkpoint，把 worker usage 分类为 `EXACT_ACCEPTED / EXACT_REJECTED / MISSING / CONFLICT`；`CommittedCheckpointContinuation` 不执行 worker。另一个独立窄窗由 `OperationCheckpointReconciliationService` 处理 canonical WorkerProjection 的 signed `COMMIT`；`OperationFailureReconciliationService` 则只处理没有 cancellation intent、没有 projection/effect、旧 Lease 到期且唯一 graph Budget 可证的 signed `RELEASE/NO_SIDE_EFFECT`。单节点在一个 SQLite UoW 写六聚合失败终态；多节点第一阶段只关闭 exact Operation/Attempt/Node/Lease，保留 Graph/Budget，由 [`DurableDagSchedulingRuntime`](../../../host_brain/task_runtime/dag_scheduling_runtime.py) 在重启重入时调用唯一 0090 writer 原子关闭共享终态，且不得先运行 READY sibling。两类 receipt/reopen exact replay 都不重跑 worker，也不能互相替代。共享 `EXCEEDED` missing、four-root bounded settlement，以及双根 signed node failure→0090 settlement 已有真实 SQLite 证据。任一 sibling live owner、scope/fingerprint/投影 digest 漂移、Graph cancellation、任意 transport receipt、`CONFLICT`、`RESERVED` 或超出已证明边界的其它 general multi-node/batch settlement均在 worker/continuation 前阻断。只读 Projection 与纯 records 不拥有 mutation authority。

Coordinator 的 `atomic_graph_success_enabled` 对新生产图默认 `true`，`bounded_fanout_enabled` 仍默认 `false`。所有新生产 builder 都冻结 atomic policy/result node；0091 在单一 UoW 完成 Graph、Budget、Outbox、Effect 与 decision。Node `ACCEPTED + Graph ACTIVE` 不可作为公开成功 replay，仅可供 0090/0091 内部恢复重建；公开 replay 还必须看到唯一 graph Budget `CONSUMED`。历史 Legacy all-ACCEPTED 重入继续由共享 finalizer验证 exact Operation checkpoint/canonical WorkerProjection，异常保持 recovery hold，禁止形成 `FAILED + CONSUMED`。

B5C 的 0092 intent/decision 仍是唯一取消业务真相；0099 case/proof/supersession 只处理历史 terminal Graph/Budget 半状态，不取代它。模块继续 `PRODUCTION_OFF`，因为 general/multi-node/pre-budget recovery、外部 deployment inventory/remediation、部署密钥/凭据、完整 key lifecycle 与 canary仍缺。

### 6.2 定时任务和内部 Agent

```text
SchedulerClock / manual occurrence
  → Secure Ingress authority → Governor grant
  → GovernedSchedulerRuntime → HostCommand admission → Task Runtime
  → immutable scheduler operation plan
  → Verification / Acceptance + scheduler.operation.execute staging（同一 TaskStore UoW）
  → shared app-local AcceptanceEffectDispatcher
  → real Scheduler handler
  → immutable SchedulerExecutionResultRepository

authenticated Internal Agent
  → Secure Ingress authority → Governor grant
  → GovernedInternalRuntime → HostCommand admission → Task Runtime
  → native WorkOrderV3 Agent executor（外部只接收未准入 v2；V3/顶层图身份拒绝）
```

Scheduler 的 worker 只生成 plan，不调用真实 handler；Review pending 时 handler=0，Effect commit 后只调用一次，duplicate 重放既有结果。handler 调用后的响应丢失或过期 claim 先只读查询 operation/result authority，无法归因时保持恢复状态而不盲调。Internal 只在 ingress 接受 `AgentWorkOrder`，剥离调用方 authority 后立即编译为 `WorkOrderV3 + BudgetPolicy`；child grant 验证后既有 executor 原生接收该 V3，不能再接收兼容投影。0096 已把两者的长时间 Review grant renewal 与 surface continuation 接入同一合同，但 Scheduler 其余 authority、一般恢复和七路线验收仍使 NE-01 保持 `IN_PROGRESS`。

### 6.3 数字生命与主动输出

```text
Scheduler occurrence / accepted experience / explicit command
  → LifeCommandService
  → deterministic reducer
  → Life event + snapshot + projection/delivery intents
  → projection dispatcher / Life outbox
  → Scene/Persona read context
  → Governor preflight
  → Gateway/Edge delivery
```

关键边界：Persona 只应消费 Life/Scene 约束生成表达，不应通过阶段、里程碑或后台周期直接改动生产 trait。

### 6.4 记忆与设定

```text
Accepted conversation/outcome
  → LifeMemory admission + guard
  → durable episode/entity/collection
  → optional vector/graph projection
  → Retrieval read fusion
  → Surface grounding

Knowledge proposal
  → CanonApplicationService
  → approval/reject
  → CanonApplyService
  → versioned document + journal + rollback
```

Memory 与 Canon 已有若干受治理局部闭环，但 provenance、冲突、过期和 Decision Record 还没有统一 owner，因此 Knowledge OS 仍是未实现目标。

## 7. 依赖方向

### 7.1 目标方向

```text
Process entry / UI / HTTP / Gateway / CLI adapters
                    ↓
              Application services
                    ↓
       Domain contracts, policies and aggregates
                    ↓
                  Ports
                    ↑
 Persistence / model / tool / network / device adapters

Composition Root 只负责把两侧构造并连接
```

必须维持的方向：

1. `natsume_contracts` 不导入 Host 或 Edge 具体实现。
2. Edge 只通过版本化合同与 Host 协作，不导入 `host_brain.app` 或 Host audit 实现。
3. domain/application 不导入 `host_brain.app`；app 是外层 adapter。
4. domain 不直接构造 SQLite、HTTP client、模型 provider 或设备 driver，而是消费 port。
5. persistence 只实现事务和映射，不决定路由、人格、安全等级或执行策略。
6. PlanningSearch 只消费虚拟状态和纯计算 port，不能获得真实工具或副作用 port。
7. Persona/Surface 只消费已批准上下文；人格演化必须转 proposal、Review、version activation 和 rollback。
8. Monitor UI 的查询走 Operator Query Gateway，写操作走 Operator Command/Governed Runtime。

### 7.2 当前实情

[`tools/static_scan/dependency_direction_audit.py`](../../../tools/static_scan/dependency_direction_audit.py) 已能拒绝非豁免包对 `host_brain.app` 的反向依赖，[`tools/static_scan/nle_boundary_policy.json`](../../../tools/static_scan/nle_boundary_policy.json) 也守护 Edge/Host、Canon writer 等局部边界。

但包依赖图仍存在一个覆盖大量核心包的大型强连通分量，包括 Agent、Memory、Persona、Pentest、Persistence、Scheduler、Skills、Task Runtime 等。这说明“没有反向导入 app”已成立，但“核心模块已单向分层”仍不成立。

当前主要循环来源：

- 领域包直接引用 concrete repository/integration；
- composition 以外仍有 service locator accessor；
- Persona、Memory、Tribe、Scheduler 与 app 兼容代码交叉编排；
- 历史执行合同与新 Runtime 合同并存；
- 读模型、业务规则和 transport adapter 在若干大文件中仍过度聚合。

## 8. 当前模块边界的硬缺口

| 优先级 | 模块缺口 | 影响 | 目标 owner |
|---|---|---|---|
| P1 | 0099 历史终态只完成窄 reconciliation | Runtime 已停止静默修复；唯一 graph-scope exact FAILED bundle 可显式 auto，BLOCKED/legacy CANCELLED需签名；child/missing/multi Budget 与完整终态漂移只 quarantine。未决 case graph-wide 围栏普通 Graph/cancellation request 与 Budget transition/consume/usage/retry，仅 0099 UoW bypass；每条历史 supersession 链必须最终有可信 proof。外部 deployment inventory/remediation 未知，general/multi-node/pre-budget recovery仍缺 | Governed Execution |
| P1 | 0096 Review continuation 仍需真实 production canary | 原子 enqueue、expiry/poison isolation、takeover/stale fence、surface 路由、POST no-rerun、grant uniqueness、denial replay 与 shutdown fault matrix 已有；Operator credential 部署和真实 canary 仍未跑 | Governed Execution |
| P2 | Review poison 运维队列仍不完整 | claim 已按候选隔离且单批扫描最多 500 行；尚无 durable DLQ/告警，极端前 500 行全 poison 时会延迟后续健康行。跨进程低延迟仍依靠轮询与 lease takeover，wake 只是进程内提示 | Governed Execution |
| P1 | Persona candidate 尚未进入统一 proposal/Review/版本激活 | 旧直写虽已封口，但人格长期演化仍不可用 | Persona Evolution Authority |
| P1 | 原子终态已切流但尚未 wave canary | 新生产图已默认使用 0090/0091，定向覆盖单节点、串行、篡改、取消传播、重启与响应丢失；bounded fan-out 仍关闭，跨路线 wave integration、general recovery 和真实 canary未完成 | Governed Execution |
| P0 | B5C 生产启用条件未完成 | Budget contention 与 0099 窄终态分类已有；cancellation gate会阻断 current anomaly、缺失 proof/supersession closure 与 quarantine。0099 仍为 `WIRED_DEFAULT_OFF / CANARY_NOT_RUN`；仍缺目标部署 inventory/remediation、general/multi-node/pre-budget recovery、现实 resolver、部署密钥/凭据、完整 key lifecycle 与真实 canary | Governed Execution |
| P1 | 0093/0095 Outbox 故障矩阵仍需持续守护 | delivery/identity 已接通；需持续验证 deterministic legacy identity、升级 ID/key 稳定、revisioned/legacy payload 边界、payload/source-index readiness、append-success/complete-loss legacy exact replay、零重复 Event、stale fence、per-revision 顺序与 `DRAINED/DEFERRED` | Durable Outbox Authority |
| P1 | Budget 完整策略仍未收口 | unique `EXCEEDED` winner/`ALREADY_EXCEEDED` 已通过 8/16/32 线程和跨进程验证；Chat、Internal 与受治理 Agent Skill 已共享 node/attempt-bound 模型 guard，多轮调用扣减 measured Token/retry/成本/时间。concurrency 维度计量、`DEGRADE/REVIEW`、一般多节点恢复和真实 provider canary 仍缺 | Governed Execution |
| 已关闭 | 生产 ProcessServiceContainer、facade、environment 与全局 ports accessor 已物理删除 | 旧测试暂由 pytest-only bridge 兼容；不得重新进入生产 | Composition Kernel |
| P1 | Host Plan 不生成竞争路径 | 通用智能规划目标尚未实现 | Planning and Decision |
| P1 | 核心包依赖图存在大型循环 | 模块无法独立演进、构造和验证 | 各 bounded context + Composition Kernel |
| P1 | Projection daemon durable continuous-SLO 缺口 | Gate 6 生命周期 owner 与 app-local `/health` supervision 已接通；仍缺 durable SLO journal、Guardian acknowledgement、跨进程历史、部署 canary 和告警闭环，不能宣称 projection 全闭环 | Obs Application Composition / Health |
| P1 | 统一 Runtime Context 仍缺长周期目标与 Bond 原始证据 owner | Surface、Proactive、Task admission 已共用 Scene identity，但缺失的长期目标不能被臆造，Bond 仍依赖既有上游投影 | World, Scene and Intent |
| P2 | Knowledge OS、Evolution Lab、Skill Compiler、Identity Migration 未闭环 | 长期学习和演化还不是可治理生产能力 | Evolution bounded contexts |

## 9. 模块维护规则

1. 新功能先在本文找到唯一 owner；找不到时先定义 bounded context，不先新建“utils/service/manager”杂包。
2. 每个模块至少明确一个入口 contract、一个状态 writer 和一个 composition owner；不需要 writer 的纯策略模块应显式声明无副作用。
3. 新建顶层包必须同时提交真实 vertical slice；禁止为未实现目标预建空目录。
4. 端口、route 和文件数量不在本文手工冻结；以 OpenAPI、route manifest、registry 和代码为准。
5. 模块状态只在生产 composition 和真实入口改变时更新；单测中构造或文件存在不足以升级状态。
6. 新增跨进程调用先扩展 `natsume_contracts`，再在 Host/Edge 分别实现 adapter；不在两侧复制一份形似 schema。
7. 修改核心边界后至少运行依赖方向扫描、文档门禁和对应模块测试；涉及跨进程合同时运行 Host/Edge contract guard。

## 10. 代码包归属索引

下表用于处理“这个包应归谁管”，不将包名当作功能完成证据。

| 主 owner | 当前代码包 |
|---|---|
| Application/HTTP | `app` |
| Composition Kernel | `composition` |
| Governed Execution | `secure_ingress`、`governor`、`orchestration`、`task_runtime`、`scheduler` |
| Agent and Domain Execution | `agent_runtime`、`domain_packs`、`skills`、`capability_plane`、`tools` |
| Legacy Execution Retirement | `executive_runtime` |
| Planning and Decision | `planning_search`、`reasoning`、`agents` |
| Conversation and Surface | `input_runtime`、`conversation`、`surface_runtime`、`persona` |
| Digital Life | `digital_life`、`lifecycle_runtime`、`runtime`、`bond` |
| Memory and Knowledge | `memory`、`retrieval`、`canon` |
| Model and External Adapters | `model_runtime`、`integrations`、`mcp`、`lsp`、`vision` |
| Embodiment and Internal Agents | `channels`、`tribe`、`organ_ops`、`bio_ops` |
| Security | `security_immune`、`immune_memory`、`airlock`、`pentest` |
| Persistence and Events | `persistence`、`audit`、`eventing` |
| Observability and Release | `observability`、`service_catalog`、`release`、`validation` |
| Shared Foundation | `core`、`contracts`、`natsume_contracts` |

目标不是保留所有当前包，而是在不破坏生产 vertical slice 的前提下，把平行 authority、兼容执行内核、全局 service locator 和双向包依赖逐步删掉。

`scripts/runtime/run_task_terminal_reconciliation_canary.py` 是 NE-01 的隔离验收入口，不是第二套 Runtime。它调用真实 migration、`HostCommandRepository`、`DurableTaskStore` 和 `ProcessTaskRuntimeContainer`，验证 restart inventory、exact-auto 修复、signed-required 围栏及启动依赖束；测试 fixture 不再用 raw Graph 冒充 canonical admission。

## 11. Agent 模型预算模块边界

| 模块 | 唯一职责 | 禁止承担 |
|---|---|---|
| `model_runtime/governed_budget.py` | 从 durable remaining 构造 guard；累计一次 Agent loop 的 measured remainder | 持久化 Budget、选择 provider、估算缺失 usage |
| `agent_runtime/agent_executor.py` | 每个 ReAct step 请求更小 guard，并消费 canonical trace | 重置额度、修改 TaskGraph、执行工具 |
| `agent_runtime/internal_result_proposal.py` | 将 FrozenNodeInput 的 exact Budget 交给 Internal Agent | 自建环境预算或吞掉预算错误 |
| `skills/executor_registry.py` | 将 Budget JSON 纳入 sealed governed Skill authority 并原样恢复 | 接受 payload 自证 budget/graph authority |
| `agent_runtime/governed_skill_work_order.py` | 将只读 V2 语义重建为 server-bound WorkOrderV3 | 信任嵌套 `governed_lineage` 或 caller V3 |

该模块边界只关闭 Internal/Agent Skill 多轮模型超支旁路；Budget 的
concurrency、DEGRADE/REVIEW、一般多节点恢复和 program-wide coverage
仍属于 Governed Execution 的 P1 缺口。
