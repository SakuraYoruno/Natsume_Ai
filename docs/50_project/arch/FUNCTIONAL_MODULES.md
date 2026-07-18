受众：MASTER
标题：Natsume AI 功能模块与责任边界

# Natsume AI 功能模块与责任边界

版本：v2.3
核对日期：2026-07-18
原始审计基线：`2b84ae9ad`；当前模块事实以本文所在提交的工作树为准
文档类型：`CURRENT MODULE MAP / TARGET BOUNDARIES`

本文从生产入口和实际调用链反向说明“系统现在有哪些功能模块、各自应该管什么、当前边界哪里没有闭环”。它不以目录数量、文件数量或历史里程碑作为完成证据。

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

“源码存在”和“模块已成为生产权威”不是同一件事。例如 PlanningSearch 内核存在，不代表 Host 已能生成竞争计划；World Model 查询资产存在，不代表它已是规划世界模型。

## 2. 生产运行面

| 运行面 | 实际入口 | 主要责任 | 边界和当前状态 |
|---|---|---|---|
| Host Main | [`host_brain/__main__.py`](../../../host_brain/__main__.py) → [`host_brain/app/asgi.py`](../../../host_brain/app/asgi.py) | Chat、Host Input、治理执行、Task/Agent Runtime、Life、Memory、Pentest 和业务 API | `WIRED`；同时仍承担过多 composition 与兼容责任 |
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
| 对话与多模态输入 | `/v1/chat`、`/v1/host/input`、`/v1/edge/events` | `input_runtime`、`agent_runtime/cortex_runtime.py`、`surface_runtime`、`persona` | Governor、Governed Runtime、Memory、Gateway/Edge | Host `chat/skill/delegate/plan` 为 `WIRED`；进度/最近轮次走只读投影，Executive 已退出生产路由 |
| 任务、Skill 与 Delegate | Chat 意图、`/v1/agent/run`、Operator 命令 | `orchestration`、`task_runtime`、`agent_runtime`、`skills` | Model Runtime、Capability Broker、Domain Pack | Host canonical route `WIRED`；0102+GR06 canonical Node Acceptance 已统一 exact Effect/artifact/`ACCEPTED`/Attempt Outbox，successor exact input 与 delivery guard 已接线。pre-stage、legacy/internal graph create、direct SQL、通用恢复与生产并发仍 `PARTIAL` |
| 自动化与定时任务 | Scheduler API、Monitor Automation | `scheduler`、`lifecycle_runtime` | Governed Command、Life、Memory、Security Immune | 定义、时钟、持久化与 governed execution 已接通；DAG 自动恢复仍受 Lane B 限制 |
| 记忆、检索与设定 | Memory/Canon API、Monitor Memory/Canon | `memory`、`retrieval`、`canon` | SQLite、Chroma、Neo4j、Surface Context | 多类记忆与 Canon proposal/apply 可用；统一 Knowledge OS 为 `MISSING` |
| 数字生命状态 | `/v1/digital-life/*`、Monitor Life Systems | `digital_life` | Projection、Persona、Lifecycle、Delivery Outbox | command/event/reducer 地基 `WIRED`；World/Scene/Intent 统一上下文仍 `PARTIAL/MISSING` |
| 人格、情绪和关系表达 | Surface Reply、Bond API、Monitor Persona/Bond | `persona`、`bond`、`surface_runtime` | Model Runtime、Memory、Life Projection、Embodiment | 表达链可用；Persona 旧直写已中央封口，受治理的演化提案链仍缺失 |
| 主动性与生命周期 | Scheduler `life_tick`、proactive API、Monitor Proactive | `lifecycle_runtime`、`digital_life`、`persona` | Governor、Gateway、Edge、Life Outbox | `PARTIAL`；生产 proactive dispatch 已校验与 WorkOrder 冻结的 canonical Scene identity，需要 Review/BLOCK 或上下文漂移时禁止 delivery；完整主动性验收仍待后续波次 |
| 语音、形象和桌面行为 | Edge RPC、MateEngine、Desktop Pet | Host `channels`、Edge `runtime/actuators/senses`、`desktop_pet` | TTS/ASR provider、VTS/VRM、OS UI | 软件链 `WIRED`；完整验收取决于本地模型、应用和硬件 |
| 外部消息平台 | Gateway webhook/reply | Edge `gateway`、Host `input_runtime/gateway_delivery.py` | Secure Ingress、Host Runtime、平台 adapter | 耐久 transport 主干存在；单平台可用性依赖凭据与本地端 |
| 安全免疫 | Internal Security API、Monitor Security | `security_immune`、`secure_ingress`、`airlock` | Scheduler、Audit、Governor | 输入门禁与传感器资产存在；定时 governed 路径受 Scheduler 故障影响 |
| 授权渗透测试 | `/v1/pentest/*`、secure-run | `pentest` | Governor、Capability/Tool adapter、PlanningSearch、Pentest Store | 大型独立领域实现可用；L3 MCTS 仍为未校准 shadow |
| 运维、观测与审批 | Obs API、Monitor UI | `observability`、`app/obs_*`、Operator Gateway | 读模型、Review Store、受治理命令 | 主要读链 `WIRED`；Obs 与 Host composition 还未完全隔离 |

## 4. 后端模块边界

### 4.1 入口、身份和执行许可

| 模块 | 当前责任 | 主入口/代码证据 | 允许依赖 | 不应拥有 | 状态/缺口 |
|---|---|---|---|---|---|
| HTTP/Application Adapters | FastAPI route、schema、lifecycle 和 provider 绑定 | [`app/asgi.py`](../../../host_brain/app/asgi.py)、[`app/app_factory.py`](../../../host_brain/app/app_factory.py)、[`app/router_registry.py`](../../../host_brain/app/router_registry.py) | application port、composition provider | 业务规则、持久化真相、长期状态 | `PARTIAL`；`app/` 仍有较多真实编排和 service factory |
| Input Runtime | ASR/Gateway 结构验证、归一化、幂等 claim、Cortex dispatch 和 reply envelope | [`input_runtime/ingress.py`](../../../host_brain/input_runtime/ingress.py)、[`input_runtime/cortex_dispatch.py`](../../../host_brain/input_runtime/cortex_dispatch.py)、[`input_runtime/worker_dispatch.py`](../../../host_brain/input_runtime/worker_dispatch.py) | Secure Ingress port、Governor port、Governed Command port、delivery port | 自建 Task 状态机、直接设备副作用 | `WIRED`；仍有跨包直接 import 与较多输入特例 |
| Secure Ingress | 凭据验证、principal/scope、replay claim、rate decision 和审计 | [`secure_ingress/service.py`](../../../host_brain/secure_ingress/service.py)、[`secure_ingress/authority.py`](../../../host_brain/secure_ingress/authority.py)、[`secure_ingress/surfaces.py`](../../../host_brain/secure_ingress/surfaces.py) | 凭据/rate/audit ports | 业务路由选择、任务执行 | `WIRED`；为 Gateway、Scheduler、Internal、Operator 等高风险入口的身份边界 |
| Governor | 路由决策、等级策略、能力许可、限流与 grant/token | [`governor/decision.py`](../../../host_brain/governor/decision.py)、[`governor/rules.py`](../../../host_brain/governor/rules.py)、[`governor/action_arbiter.py`](../../../host_brain/governor/action_arbiter.py) | 策略投影、Capability descriptor、审计 | worker执行、搜索 rollout、外部副作用 | `WIRED / PARTIAL`；Task admission 已接 authority-attested Intent；Action Preview/Catalog authority 的 #84 本地 Gate 已通过，Preview 仍不取代 Governor grant |

### 4.2 命令、图与持久执行

| 模块 | 当前责任 | 主入口/代码证据 | 允许依赖 | 不应拥有 | 状态/缺口 |
|---|---|---|---|---|---|
| Runtime Contracts | WorkOrder、TaskGraph、Budget、Review、Verification、Acceptance 数据合同 | [`contracts/agent_runtime/`](../../../host_brain/contracts/agent_runtime)、[`natsume_contracts/task/v1.py`](../../../natsume_contracts/task/v1.py) | 标准库与纯类型 | app、persistence、provider 实现 | `WIRED`；TaskGraph v1 可选扩展包含 result/completion policy，legacy 多 sink 无 result，atomic policy 必须显式结果；Host/Scheduler/Internal canonical admission 用 V3 |
| Orchestration | HostCommand admission、Governor grant 解析、计划图编译、revision、0100 durable supersession 和 node grant | [`orchestration/governed_command.py`](../../../host_brain/orchestration/governed_command.py)、[`orchestration/planning_graph_compiler.py`](../../../host_brain/orchestration/planning_graph_compiler.py)、[`orchestration/planning_admission.py`](../../../host_brain/orchestration/planning_admission.py)、[`persistence/host_command_repository.py`](../../../host_brain/persistence/host_command_repository.py)、[`orchestration/node_grants.py`](../../../host_brain/orchestration/node_grants.py) | contracts、repository ports、PlanningSearch result | 领域 worker 实现、外部副作用 | Host 主链 `WIRED`；显式 FAILED-only replan 已有单事务 supersession 与旧 revision fence，但自动触发、更宽 predecessor classifier 和生产 canary仍 `PARTIAL` |
| Governed Host Adapter | 把 `chat/skill/delegate/plan` 编译为 HostCommand + `WorkOrderV3` + immutable graph；以 route-aware authority 校验 exact replay | [`app/governed_task_adapter.py`](../../../host_brain/app/governed_task_adapter.py)、[`app/governed_task_authority.py`](../../../host_brain/app/governed_task_authority.py) | Orchestration、Task Runtime、route worker | 执行期二次规划 | `WIRED`；adapter 负责生命周期/编译，authority 模块负责纯 replay、lineage 与 fail-closed 校验；chat/delegate 不再误套 skill 专属 metadata |
| Task Runtime | 依赖推进、Attempt、Lease、Idempotency、Budget、Review、Verification、Acceptance、effect intents、两阶段 Graph cancellation、窄 continuation 与 0099 历史终态 reconciliation | [`task_runtime/coordinator.py`](../../../host_brain/task_runtime/coordinator.py)、[`task_runtime/accepted_graph_finalization.py`](../../../host_brain/task_runtime/accepted_graph_finalization.py)、[`task_runtime/operation_recovery_runtime.py`](../../../host_brain/task_runtime/operation_recovery_runtime.py)、[`task_runtime/terminal_state_reconciliation.py`](../../../host_brain/task_runtime/terminal_state_reconciliation.py)、[`persistence/task_terminal_reconciliation_store.py`](../../../host_brain/persistence/task_terminal_reconciliation_store.py) | Task repository ports、worker/verifier/acceptance/cancellation-authority ports、canonical HostCommand repository | 路由意图识别、计划搜索、未经验证的业务取消、第二套状态、通用副作用猜测 | `PARTIAL_B5C_DURABLE_CANCELLATION_SHADOW / PRODUCTION_OFF`；Budget unique winner、single-node post-budget takeover、ACCEPTED→Effect restart 与 0099 窄分类/修复已有。general/multi-node/pre-budget recovery、外部 inventory/remediation、现实 resolver、部署 key/credential 与 canary未闭环 |
| Scheduler | 定义、occurrence、时钟、lease、模式/资源策略和定时 worker registry | [`scheduler/clock.py`](../../../host_brain/scheduler/clock.py)、[`scheduler/scheduling_center.py`](../../../host_brain/scheduler/scheduling_center.py)、[`scheduler/governed_runtime.py`](../../../host_brain/scheduler/governed_runtime.py) | Secure Ingress、Governor、Task Runtime、Scheduler Repository | 自建第二套 Task 状态机 | `WIRED`；Clock/Manual occurrence 已贯通 V3/Budget/child grant/Review/Verification/Acceptance/Receipt |
| Internal Agent Runtime | 认证后的内部 Agent WorkOrder、结果持久化和 Review API | [`app/internal_task_runtime.py`](../../../host_brain/app/internal_task_runtime.py)、[`app/internal_agent_ingress.py`](../../../host_brain/app/internal_agent_ingress.py) | Secure Ingress、Agent Executor、Task Runtime | 绕过 HostCommand 权限 lineage | `WIRED`；边界 v2 编译 V3，完整 child grant、Budget、PRE/POST Review、Verification、Acceptance、Receipt 与 replay 已验证 |
| Read Projection / Executive Guard | `task_progress`、`recent_turn` 的只读投影与 retired fail-close guard | [`agent_runtime/read_routes.py`](../../../host_brain/agent_runtime/read_routes.py)、[`executive_runtime/runtime.py`](../../../host_brain/executive_runtime/runtime.py) | Durable Task/Conversation projection | Task/Attempt、worker、状态写入 | `WIRED / RETIRED`；Executive 无生产入口，legacy SkillDag 执行和内存 registry 已删除 |

Task Runtime lifecycle 的实际边界是：activation rollback 或 `aclose` quiescence 未完成统一使用 `TASK_RUNTIME_LIFECYCLE_RETRY_REQUIRED`，后续 bind/activate 锁死，只允许 `aclose` 重试。Coordinator admission close、drain、`aclose` 任一失败均保持 Store 开放；Coordinator 和 0094 worker 都静默后才 drain Outbox/关 infrastructure。

NE-01 两批职责拆分保持这些 authority 不变：首批分离 Host adapter/worker/contracts、HostCommand UoW/codec、Review Store/binding/codec 与 Scheduler occurrence/command；第二批分离 ASGI lifecycle wiring、Obs response models、Degradation policy、Node Grant/Planning codec、Intent Review continuation 和 Guardian contracts/operations。第三个切片移除 Obs 五个无消费者的 locator refresher，将 locator 消费库存降为 47 模块/152 个导入名；ReAct 的 Skill/Delegate 嵌套直执行工具从发现面删除；Operator 只迁移 `scheduler.task.trigger`，其余 36 个入口保持 fail-closed；HTTP Tool 首个切片接入 canonical runtime，但生产 seed 的 governed executor 仍为 0，Broker/MCP direct API 尚未退役。兼容 import 继续指向同一类/常量，focused 模块不持有第二连接、journal store 或事务 owner。`AdmittedTaskNode` 冻结并验证 TaskNode 的 1..5 `risk_level`；Chat terminal 的 model budget guard 在 route executor 精确验证后不进入公开 WorkerProjection，只保留 typed budget usage，不能通过放宽敏感字段 Gate 绕过。fresh mixed-file Gate 已为 0，但 atomic/fan-out/cancellation 默认仍关闭，约 53 个 locator 服务槽位和其他旧执行旁路仍未清零。

Effect reconciliation persistence 以 `task_effect_reconciliation_store.py` 保留稳定兼容 composite，实际责任拆到 common helpers、read/validation 和 mutation focused modules；cancellation trusted overlay 独立位于 `task_graph_cancellation_effect_overlay_store.py`。所有 mixin 复用 `DurableTaskStore` 的同一 connection/transaction/UoW，没有第二 Store authority，也未通过 architecture policy 白名单掩盖大型文件。

Terminal reconciliation persistence 以 0099 schema-only migration 建立 append-only case/proof/supersession；migration 不改 Graph/Budget。Readiness 严格比较 3 table/4 index/10 trigger canonical SQL 并以 Python 复核 case/subject/proof。Composition 在 `recover_startup` 后单次最多 inventory 10,000 个 terminal Graph。`TerminalStateReconciliationService` 只允许三分法：`FAILED + exact atomic failure bundle + 唯一 graph-scope ACTIVE/EXCEEDED Budget` 可在显式 default-off system auto 下释放 Budget；`BLOCKED failure` 与 legacy `CANCELLED` 要求 Ed25519；child/missing/multi Budget、PENDING/CONSUMED，或 Graph/Node/Review/Attempt/Lease/并行终态任一不一致只 quarantine。Classifier 要求唯一 0090 decision、完整 0091/0092 validator 与 exact failure Outbox payload refs。Runtime 重入不再静默修复 terminal Graph 的未结 Budget；未决 current case graph-wide 围栏普通 Graph/cancellation request 与 Budget transition/consume/usage/retry，只有 0099 proof UoW bypass。System proof 用 immutable case 时间跨进程收敛；healthy closure 只在历史未决 case 且 current healthy 时生成 signed `NONE` 且零业务写。该模块为 `WIRED_DEFAULT_OFF / CANARY_NOT_RUN`，不是 general recovery。

Budget contention persistence 由 `TaskBudgetUsageStoreMixin` 唯一拥有：首个 rejected usage 是唯一 `EXCEEDED` winner并拥有 Budget transition/Outbox；随后 sibling 只落 `ALREADY_EXCEEDED` observation并引用 winner。共享 terminal-authority reader 被 0090 decision 写入、读取和 replay复用，并以单一 SQLite snapshot 复核 Reservation 精确版本、sole Outbox、Attempt/Graph lineage与全部 sibling。`DurableBudgetRuntime` 以 `max(configured retries, concurrency + 1)`提供 CAS attempt，concurrency=8/连续 8 次冲突/第 9 次成功已有单测；这不是 concurrency charge。Operation checkpoint 仍限 single-node canonical COMMITTED facts；exact usage 只读，完全缺失且无冲突时才以持久 elapsed 上界在更高 fence 后幂等结算。

### 4.3 规划、Agent 和领域能力

| 模块 | 当前责任 | 主入口/代码证据 | 允许依赖 | 不应拥有 | 状态/缺口 |
|---|---|---|---|---|---|
| PlanningSearch Kernel | Candidate expansion、cognitive ranking、conditional MCTS、virtual transition、reward 和 trace | [`planning_search/service.py`](../../../host_brain/planning_search/service.py)、[`planning_search/contracts.py`](../../../host_brain/planning_search/contracts.py)、[`planning_search/ports.py`](../../../host_brain/planning_search/ports.py) | 纯合同、领域注入的纯函数 port | 工具执行、网络、持久写入、真实副作用 | 内核 `WIRED`；通用智能规划收益未证明 |
| Host Plan Adapter | 把 Governor 已批准的 steps 合法化为顺序候选并编译图 | [`orchestration/plan_intent_search.py`](../../../host_brain/orchestration/plan_intent_search.py)、[`orchestration/planning_execution.py`](../../../host_brain/orchestration/planning_execution.py) | PlanningSearch、graph compiler | 凭空新增工具或越过批准边界 | `PARTIAL`；只比较已批准步骤的顺序，不生成竞争方案 |
| Code Planning Shadow Adapter | 用冻结 task/workspace/verification refs 与 digest 动态构造 inspect→registered patch→unit/rollback 虚拟候选，并把重验后的唯一 node args 绑定到 immutable compilation context；root 只允许 `inspect + generate_report + temp_workspace` 且必须禁 `write_files/network` | [`planning_search/code_domain_models.py`](../../../host_brain/planning_search/code_domain_models.py)、[`planning_search/code_domain.py`](../../../host_brain/planning_search/code_domain.py)、[`orchestration/planning_execution.py`](../../../host_brain/orchestration/planning_execution.py)、[`orchestration/planning_recovery.py`](../../../host_brain/orchestration/planning_recovery.py) | PlanningSearch、GoalPlanning、compiler context、纯 JSON 证据 | worktree/source、模型/工具/测试/provider、Task admission、Workspace ACL、legacy `intent_draft` 覆盖 | `PARTIAL_D16_DYNAMIC_CATALOG_SHADOW_ONLY`；`virtual_model_ready=false` 时不能启用 L3；未有真实 A/B receipt、写入切流或收益结论 |
| Research Planning Shadow Adapter | 用 `ResearchTaskSnapshot` 的公开 source metadata，按虚拟状态动态构造 `query`、`quick-answer`、`primary-source`、`independent-source`、`cross-check`、`resolve-conflict`、`stop` 虚拟候选；L2 只比较 authority/relevance/read-cost，L3 只在冻结 `virtual_transition_ready=true` 时存在 | [`planning_search/research_domain_models.py`](../../../host_brain/planning_search/research_domain_models.py)、[`planning_search/research_domain.py`](../../../host_brain/planning_search/research_domain.py)、[`planning_search/research_benchmark_corpus.py`](../../../host_brain/planning_search/research_benchmark_corpus.py) | PlanningSearch、冻结公开 metadata、受信实验 corpus manifest | dedicated 本机 locator/source-body/answer-key/verifier-decision 字段、Host/TaskGraph/BrowserWorker/网页抓取、真实模型、独立 verifier、winner/promotion authority；任务文字不是内容级 DLP | `PARTIAL_D20_FROZEN_CORPUS_SHADOW_ONLY`；planning payload/公开 receipt 仅有 source id/ref/digest 等 metadata。`FrozenResearchCorpusLoader` 只从可信实验根目录下 manifest 列出的 repository-relative 普通文件读取，在稳定输入中前后检查大小并在读完后核对 digest，且拒绝绝对/遍历/ADS colon/case alias/link/reparse/未列来源；它不是 hostile TOCTOU 防线，未把 snapshot 与 manifest/document 精确绑定，故不证明来源独立性。不是 OS/tenant/sandbox，未有 paired cohort 或收益结论，Research benefit `UNPROVEN` |
| Code Historical Benchmark（trusted-process） | 用受保护 clean source 建立一次性 private bare clone，再给 baseline/search arm 建同 commit detached worktree；仅给仓库自有受信 adapter 一个 lower-case repository-relative allow/deny 文件 API，输出 redacted tracked/untracked diff、changed-path/verifier evidence。D19 可将冻结 `python -m pytest -q <repo-relative-test>` 预注册为 verifier plan，在 worktree 根目录 shell-free 子进程执行，记录无路径命令/退出/输出 digest/耗时回执；只有私有物料全部删除且 source fingerprint 一致时产生 `VERIFIED_CLEAN`，专用 service 才登记 terminal receipt | [`planning_search/code_benchmark_models.py`](../../../host_brain/planning_search/code_benchmark_models.py)、[`planning_search/code_benchmark_sandbox.py`](../../../host_brain/planning_search/code_benchmark_sandbox.py)、[`planning_search/code_benchmark_ledger.py`](../../../host_brain/planning_search/code_benchmark_ledger.py)、[`planning_search/code_benchmark_verification_contracts.py`](../../../host_brain/planning_search/code_benchmark_verification_contracts.py)、[`planning_search/code_benchmark_verifier.py`](../../../host_brain/planning_search/code_benchmark_verifier.py)、[`planning_search/code_benchmark_experiment.py`](../../../host_brain/planning_search/code_benchmark_experiment.py)、[`persistence/code_benchmark_ledger_repository.py`](../../../host_brain/persistence/code_benchmark_ledger_repository.py) | `DomainBenchmarkWorkload/Arm/Outcome`、Git、受限 pytest subprocess、0106 SQLite ledger | Host/TaskGraph/AgentExecutor、真实模型、ModelBudgetGuard、W2 WorkspaceGrant、container/tenant sandbox、winner/promotion authority；不可信 model/skill/plugin/hidden verifier | `PARTIAL_D19_ALLOWLISTED_SUBPROCESS_VERIFICATION_EXPERIMENT`；只证明 cleanup-gated durable experiment evidence 与两项受控测试预算，必须标记 `PRIVATE_BARE_WORKTREE_TRUSTED_PROCESS_ONLY` / `network_isolation=NOT_ENFORCED`；没有模型/工具/token/cost budget、cohort/statistical uplift、crash cleanup worker 或收益结论 |
| Agent Runtime | Agent contract、planner adapter、executor、workspace/artifact、verification 和 studios | [`agent_runtime/executor.py`](../../../host_brain/agent_runtime/executor.py)、[`agent_runtime/planner_adapter.py`](../../../host_brain/agent_runtime/planner_adapter.py)、[`agent_runtime/workspace_runtime.py`](../../../host_brain/agent_runtime/workspace_runtime.py)、[`agent_runtime/workspace_allocator.py`](../../../host_brain/agent_runtime/workspace_allocator.py)、[`composition/agent_workspace.py`](../../../host_brain/composition/agent_workspace.py)、[`persistence/workspace_repository.py`](../../../host_brain/persistence/workspace_repository.py)、[`persistence/workspace_operation_fencing.py`](../../../host_brain/persistence/workspace_operation_fencing.py)、[`agent_runtime/studios/`](../../../host_brain/agent_runtime/studios) | Model/Capability ports、Artifact store、Task context | 入口身份、Task 权威、全局 composition | `PARTIAL_W2_INTERNAL_GIT_WORKTREE_WIRED / DEFAULT_FAIL_CLOSED`；Internal 写路线已有 graph-bound frozen-source worktree、实例/PID lease、独占 scope、heartbeat/handoff/takeover、provisioning/allocator/cleanup adapter 的 focused 跨进程 workspace/source fencing、“启动 1 条+后台 backlog”cleanup continuation 与生命周期失败关闭。Agent tool 仅动作间取消，Verification 仅启动前验活；二者无逐操作 fence或强杀。缺 secret 不启用，canary 未跑；promotion/abandon、accepted checkpoint HEAD、Domain/Skill 接线与 OS/container/tenant/handle 隔离未完成，且尚有 `AgentWorkOrder` 兼容面 |
| Domain Packs | Research、Media Translation、Light Novel、Pentest 等声明式领域包与生命周期 job | [`domain_packs/registry.py`](../../../host_brain/domain_packs/registry.py)、[`domain_packs/production_runtime.py`](../../../host_brain/domain_packs/production_runtime.py)、[`domain_packs/contracts.py`](../../../host_brain/domain_packs/contracts.py) | 平台 ports、Agent Runtime、Capability Broker | 复制平台调度、自建授权 | `PARTIAL`；存在生产 job 资产，尚未全部收敛到统一 `WorkOrderV3` 主链 |
| Pentest Domain | 授权范围、阶段执行、工具 adapter、finding/report、攻击图和自主策略 | [`pentest/production_runtime.py`](../../../host_brain/pentest/production_runtime.py)、[`pentest/secure_run.py`](../../../host_brain/pentest/secure_run.py)、[`pentest/agent/agent_loop.py`](../../../host_brain/pentest/agent/agent_loop.py) | Secure authority、受限工具 port、PlanningSearch adapter、领域 repositories | 绕过 scope/approval 的真实工具调用 | 领域功能 `WIRED`；L3 仍 `SHADOW_UNCALIBRATED`，且领域包内部责任过密 |
| Tribe / Organ / BioOps | 意图分流、内部事件、生理隐喻 agent、organ 健康与 BioOps 专项工作流 | [`tribe/thalamus_agent.py`](../../../host_brain/tribe/thalamus_agent.py)、[`tribe/event_bus.py`](../../../host_brain/tribe/event_bus.py)、[`organ_ops/organ_registry.py`](../../../host_brain/organ_ops/organ_registry.py)、[`bio_ops/workflow.py`](../../../host_brain/bio_ops/workflow.py) | Model invocation port、typed event、受治理 capability | 任务真相、长期状态的平行 writer | `PARTIAL`；应保持为策略/领域 proposal 层，不能成为第二执行内核 |

### 4.4 模型、能力、工具与 Skill

| 模块 | 当前责任 | 主入口/代码证据 | 允许依赖 | 不应拥有 | 状态/缺口 |
|---|---|---|---|---|---|
| Model Runtime | 路由目录、provider adapter、prompt registry、ModelAttempt、重试/输出合同和审计 | [`model_runtime/model_attempt_service.py`](../../../host_brain/model_runtime/model_attempt_service.py)、[`model_runtime/production_composition.py`](../../../host_brain/model_runtime/production_composition.py)、[`model_runtime/prompt_registry.py`](../../../host_brain/model_runtime/prompt_registry.py) | provider ports、route policy、audit journal | 业务路由 authority、未受管的外部工具 | `WIRED`；为所有模型调用的目标主干 |
| Capability Plane | Capability request/grant/attempt/receipt、policy 和执行 journal | [`capability_plane/broker.py`](../../../host_brain/capability_plane/broker.py)、[`capability_plane/contracts.py`](../../../host_brain/capability_plane/contracts.py)、[`capability_plane/production_composition.py`](../../../host_brain/capability_plane/production_composition.py) | Governor grant、descriptor、adapter、journal | 不受授权的自行执行 | `WIRED / MATURITY_FAIL_CLOSED_IN_PRODUCTION`；生产 IntentDecisionEngine 显式启用 restrict-only scorecard 门禁，缺失/伪造 scorecard 最高风险 |
| Tool Runtime | 工具目录、Broker、权限守卫、实际 adapter | [`composition/tool_runtime.py`](../../../host_brain/composition/tool_runtime.py)、[`tools/`](../../../host_brain/tools)、[`app/tool_routes.py`](../../../host_brain/app/tool_routes.py)、[`app/governed_tool_runtime.py`](../../../host_brain/app/governed_tool_runtime.py) | Capability Plane、Audit、外部 integration | 自己颁发 grant、自建 Task | `PARTIAL_GOVERNED_HTTP / PRODUCTION_FAIL_CLOSED`；`/tools/execute` 已进入 Secure Ingress 与 canonical TaskGraph，0114 扩展 Worker Projection，真实 SQLite 测试 adapter 证明 Review/replay/Receipt；生产 seed 无真实 governed executor，HTTP health 与 Host startup 均为 DEGRADED，旧 Broker/MCP direct API 仍待退役 |
| Built-in Skills | 内置 skill metadata、permission、scheduler profile 和 executor | [`skills/registry.py`](../../../host_brain/skills/registry.py)、[`skills/impl/`](../../../host_brain/skills/impl) | Tool/Capability port、冻结输入 | 目录扫描自动提权、生产状态 authority | `WIRED`；这里是代码包，不是已删除的顶层杂物 `skills/` |
| Skill Supply Chain | 签名、inventory/SBOM 检查、quarantine/stage、证据绑定和生命周期回执 | [`skills/supply_chain.py`](../../../host_brain/skills/supply_chain.py)、[`skills/signed_intake_http.py`](../../../host_brain/skills/signed_intake_http.py)、[`skills/skill_lifecycle.py`](../../../host_brain/skills/skill_lifecycle.py)、[`persistence/skill_lifecycle_repository.py`](../../../host_brain/persistence/skill_lifecycle_repository.py) | 信任键、Quarantine、Supply Chain Repository、Lab/Review evidence | 未经验证的 import/activation、目录扫描 | `PARTIAL_NE05_EVIDENCE_GATED_LIFECYCLE`；0109 将 staged→activate/degrade/rollback 绑定 Lab、Review、SBOM、signature、sandbox 和 rollback evidence，active-only content-addressed lookup 已接线；外部 provider canary 与真实 Review/activation composition 仍待完成 |
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
| Action Preview Authority | 在高风险执行点重开已有 authority 并冻结 exact action/context/scene；自身不授予执行权 | [`runtime/action_preview_authority.py`](../../../host_brain/runtime/action_preview_authority.py)、[`runtime/action_preview_resolver.py`](../../../host_brain/runtime/action_preview_resolver.py)、[`governor/action_preview.py`](../../../host_brain/governor/action_preview.py) | HostCommand/TaskGraph/Attempt/Lease/Grant/Guardian、Runtime Event、typed EffectDescriptor | caller 自报 node grant/risk/capability、扩展 scope/args、用 Preview 替代 Grant | `PARTIAL_NE03_PRODUCTION_AUTHORITY_WIRED_LOCAL_ACCEPTANCE_PASSED`；Skill/Delegate/Scheduler/Acceptance/Life 已接线，终端正向覆盖 Acceptance surface Gateway 与 Scheduler-derived Life Edge/Gateway/VTS/MateEngine；旧任务/effect、37 个 Operator 直写、reply-performance/Gateway voice/Avatar 直通隔离，后三者和 Operator 功能迁移仍属 NE-01 |
| Runtime Guardian / Service Catalog | app-local health/degradation/recovery/SLO，以及 Host 五阶段启动 authority | 0112、0113、[`runtime/runtime_guardian.py`](../../../host_brain/runtime/runtime_guardian.py)、[`service_catalog/host_startup.py`](../../../host_brain/service_catalog/host_startup.py)、[`service_catalog/rollout_authority.py`](../../../host_brain/service_catalog/rollout_authority.py) | Host runtime lease、Runtime Event、append-only rollout operation/receipt/head、typed probes | module-global singleton、裸 owner/fence、伪造 reducer/terminal、跳阶段或收养未知 listener | `PARTIAL_NE03_ENFORCE_TARGET_WIRED_LOCAL_ACCEPTANCE_PASSED`；fresh DB 逐级到 ENFORCE、crash takeover、重启/append 不确定性与状态机已有；Windows graceful stop 和部署多实例 canary 待收口 |
| Bond | 用户身份解析、关系阶段、里程碑、叙事和关系投影 | [`bond/bond_stage_engine.py`](../../../host_brain/bond/bond_stage_engine.py)、[`bond/milestone_tracker.py`](../../../host_brain/bond/milestone_tracker.py)、[`bond/bond_projection.py`](../../../host_brain/bond/bond_projection.py) | 事件、只读 Life/Memory、repository | 绕过人格版本激活直改 trait | `PARTIAL / CANDIDATE ONLY`；stage/milestone 可触发 guard 后候选，但中央围栏禁止 trait 直写 |
| Persona Growth | trait drift/growth 候选、interaction/emergent 候选和部分反馈 | [`persona/persona_growth.py`](../../../host_brain/persona/persona_growth.py)、[`persona/evolution_orchestrator.py`](../../../host_brain/persona/evolution_orchestrator.py)、[`persona/emergent_growth_engine.py`](../../../host_brain/persona/emergent_growth_engine.py) | 只读 Persona 与 guard；未来只产生 Feedback/Identity proposal | 非迁移 trait 写入、伪造已应用历史或消费一次性状态 | `CENTRAL_FENCE_WIRED`；NE-01 已封口并静态禁止新 writer，NE-06 再建立 proposal 闭环 |
| Lifecycle Runtime | Scheduler handler、Life jobs、背景投影和 proactive dispatch | [`lifecycle_runtime/composition.py`](../../../host_brain/lifecycle_runtime/composition.py)、[`lifecycle_runtime/life_jobs.py`](../../../host_brain/lifecycle_runtime/life_jobs.py)、[`lifecycle_runtime/proactive_dispatch.py`](../../../host_brain/lifecycle_runtime/proactive_dispatch.py) | Scheduler、Life Command、Governor、delivery ports | 新的时钟 authority、人格直写 | `PARTIAL`；定时执行闭环受 Scheduler runtime 故障限制 |
| Evolution / Identity | 实验隔离策略、ExperimentSpec/Run/Report/Proposal durable ledger、Feedback/Knowledge/Identity proposal ledger 与 rollback 事件 | [`evolution_lab/contracts.py`](../../../host_brain/evolution_lab/contracts.py)、[`evolution_lab/policy.py`](../../../host_brain/evolution_lab/policy.py)、[`evolution_lab/runner.py`](../../../host_brain/evolution_lab/runner.py)、[`persistence/evolution_lab_ledger_repository.py`](../../../host_brain/persistence/evolution_lab_ledger_repository.py)、[`evolution/contracts.py`](../../../host_brain/evolution/contracts.py)、[`persistence/evolution_governance_ledger.py`](../../../host_brain/persistence/evolution_governance_ledger.py)、[`release/identity_snapshot.py`](../../../host_brain/release/identity_snapshot.py) | 隔离实验、Review、版本存储 | 直接生产激活、Lab 副作用、proposal 外写入 | Evolution Lab `PARTIAL_NE04_DURABLE_PROPOSAL_LEDGER`；NE-06 的 0110 已提供 Feedback/Knowledge/Identity append-only proposal→Review→activation/rollback 事件与 namespace projection；旧增长入口适配、真实 Identity owner 和生产 cutover 仍待完成 |

### 4.7 渠道、Gateway 与 Edge

| 模块 | 当前责任 | 主入口/代码证据 | 允许依赖 | 不应拥有 | 状态/缺口 |
|---|---|---|---|---|---|
| Host Channels | Edge transport、TTS/VTS/MateEngine mapping、embodiment outbox 和 Life delivery transport | [`channels/edge_transport.py`](../../../host_brain/channels/edge_transport.py)、[`channels/embodiment.py`](../../../host_brain/channels/embodiment.py)、[`channels/embodiment_outbox.py`](../../../host_brain/channels/embodiment_outbox.py)、[`channels/mateengine.py`](../../../host_brain/channels/mateengine.py) | 跨进程 contracts、Acceptance effect/outbox、terminal Action Preview | 未验证直接物理副作用、无 Preview 的高风险网络/设备调用 | `PARTIAL_NE03_TERMINAL_GATE / DEVICE_DEPENDENT`；raw HTTP transport 与低层 command port 不再公开；Acceptance 只放行 exact minimal text，公开具身端口要求 exact proof，durable claim 后由私有 submitter 执行；旧 voice/performance/avatar 直通失败关闭且待 NE-01，真实设备留 NE-07 |
| Gateway Transport | 平台 webhook 验签、归一化、durable intake/delivery、receipt 和重试 | [`edge_client/gateway/server.py`](../../../edge_client/gateway/server.py)、[`edge_client/gateway/delivery_runtime.py`](../../../edge_client/gateway/delivery_runtime.py)、[`persistence/gateway_transport_repository.py`](../../../host_brain/persistence/gateway_transport_repository.py) | `natsume_contracts.edge`、Host HMAC 输入端、平台 adapter | Host 业务实现、对话状态真相 | `WIRED`；Gateway store factory 通过合同注入，但当前组装较大 |
| Edge Runtime | 本地 sensor/actuator composition、语音 pipeline、Avatar 状态和健康 | [`edge_client/runtime/bootstrap.py`](../../../edge_client/runtime/bootstrap.py)、[`edge_client/runtime/voice_pipeline.py`](../../../edge_client/runtime/voice_pipeline.py)、[`edge_client/actuators/`](../../../edge_client/actuators)、[`edge_client/senses/`](../../../edge_client/senses) | Edge contracts、本地 provider/OS adapter | Host app/audit 实现、全局任务 authority | `WIRED / ENVIRONMENT_DEPENDENT`；边界扫描禁止 Edge 导入 Host 实现 |
| Gateway Platform Adapters | QQ、微信、飞书、企微、Telegram、LINE 协议适配 | [`edge_client/gateway/adapters/`](../../../edge_client/gateway/adapters) | 平台 SDK/API、Gateway model | 跨平台业务策略、Host 任务状态 | `PLATFORM_DEPENDENT`；未配凭据或本地 bridge 时应显式 degraded |
| Desktop Pet | HMAC/replay/rate-limit 本地 bridge、Live2D 渲染和交互 | [`desktop_pet/bridge_security.cjs`](../../../desktop_pet/bridge_security.cjs)、[`desktop_pet/main.cjs`](../../../desktop_pet/main.cjs)、[`desktop_pet/renderer/pet.js`](../../../desktop_pet/renderer/pet.js) | Edge/Host 签名命令 | 人格、Task 或 Life 状态 writer | `WIRED / LOCAL ONLY` |

### 4.8 观测、持久化和 composition

| 模块 | 当前责任 | 主入口/代码证据 | 允许依赖 | 不应拥有 | 状态/缺口 |
|---|---|---|---|---|---|
| Observability Query | projection query、trace、metrics、health、degradation、reconciliation read 与 auth | [`observability/operator_query_gateway.py`](../../../host_brain/observability/operator_query_gateway.py)、[`app/obs_route_ports.py`](../../../host_brain/app/obs_route_ports.py)、[`app/observability_runtime/life_body_source.py`](../../../host_brain/app/observability_runtime/life_body_source.py)、[`app/obs_projection_jobs.py`](../../../host_brain/app/obs_projection_jobs.py) | 只读 projection/store/source reader、版本化Host合同 | domain writer、原始 secret/signature | `D7C_LIFE_BODY_DURABLE_RISK_AND_JOURNAL_PARTIAL`；21 producer声明为`6 durable / 3 static / 8 partial / 4 authenticated / 0 unavailable`。Life Body从同app四个公开durable roots与canonical ledger有界lineage冷聚合；缺根/无效/过期/查询失败写typed unavailable，保留安全与Gateway风险，未变化根不追加journal，输出去除content/private字段。真实Edge未证明，底层Host source authority仍未收口 |
| Operator Command | 受认证的运维命令入口、受治理路由和 outcome journal | [`observability/secure_command_ingress.py`](../../../host_brain/observability/secure_command_ingress.py)、[`observability/command_router.py`](../../../host_brain/observability/command_router.py)、[`observability/command_outcome_journal.py`](../../../host_brain/observability/command_outcome_journal.py) | Secure Ingress、typed command ports | Query handler 直写 domain store | `WIRED / PARTIAL`；应全部进入通用 governed runtime |
| Persistence | 按聚合提供 SQLite repository、OCC、journal/outbox 和 read projection | [`persistence/`](../../../host_brain/persistence)、[`audit/migration_runner.py`](../../../host_brain/audit/migration_runner.py)、[`audit/sqlite_ledger.py`](../../../host_brain/audit/sqlite_ledger.py) | SQL/driver、稳定 contracts | 业务决策、route 编排、人格策略 | `WIRED / LARGE`；持久化主干稳定，但若干 repository 过大且与 application 双向耦合 |
| Runtime Eventing | runtime event 投影和投递恢复 | [`eventing/runtime_event_projection.py`](../../../host_brain/eventing/runtime_event_projection.py)、[`persistence/runtime_event_repository.py`](../../../host_brain/persistence/runtime_event_repository.py) | event contract、repository | 替代 domain aggregate | `WIRED / SUPPORTING` |
| Composition | 构造 Task、Life、Memory、Canon、Scheduler、Tool、Obs 对象图 | [`app/obs_application.py`](../../../host_brain/app/obs_application.py)、[`app/governed_skill_execution_ports.py`](../../../host_brain/app/governed_skill_execution_ports.py)、[`app/host_obs_source_composition.py`](../../../host_brain/app/host_obs_source_composition.py)、[`composition/process_service_container.py`](../../../host_brain/composition/process_service_container.py) | adapter和构造器 | 业务方法、模块全局真相 | `INCOMPLETE / D7C_OBS_APP_ASSEMBLY_AND_DIRECT_SKILL_FAIL_CLOSED_PARTIAL`；Obs assembly移入app层，composition→app NLE违规归零且无白名单；Direct Skill三项资源/记忆实例已由lifespan冻结，授权后缺依赖失败关闭，执行期不回读locator。`ProcessServiceContainer`仍有60字段/约53个非锁可变引用及大量accessor，Host source、Chat/Delegate、Acceptance Effect、真实Edge与其余Host/Worker typed scopes未完成；NE-01仍IN_PROGRESS |
| Shared Contracts | Host/Edge 间 Task、Life、Identity、Operator、Edge/Embodiment 版本化数据协议 | [`natsume_contracts/`](../../../natsume_contracts) | 标准库、纯数据类型 | Host/Edge 具体实现、连接、全局容器 | `WIRED`；该包是真实跨进程边界，不是可删兼容目录 |

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

关键边界：Input Runtime 不执行工具，Cortex 不写 Task 第二真相，worker 不修改冻结图，外部副作用必须发生在 Acceptance 之后。

Task composition 在开放运行时前按 migration/readiness → `recover_startup` → 0099 单次最多 10,000 个 terminal Graph inventory → 可选 default-off exact auto → publisher/surface/continuation 排序。Container 构造 Operation/Effect/terminal reconciliation service，并向 TaskStore 注入 verification-only Ed25519 公钥；`task_terminal.reconcile` 与 system auto 分别由独立默认关闭开关控制。Operator repair 要求 Secure Ingress=`ENFORCE`、可执行 Gateway 与公钥环，私钥不进入 Host。Cancellation production gate 在启用 cancellation 时拒绝 current terminal anomaly、缺失 trusted proof/supersession closure 的历史 case 和 quarantine，但不拒绝 terminal remediation Operator 自身。

Review 命令与内部 Agent 执行使用不同 Secure Ingress authority：`review.operator / review:decide` 才能 approve/reject/resume，actor 与 requester 绑定认证 principal，Task-bound Review create 只允许 Task Runtime 内部调用。0096 authority 是 secret-free 固定字段投影；READY claim 与首次 worker entry 精确绑定 Attempt/Lease/owner/fence。0097 以 writer CAS + partial unique index 保证多进程 node-grant batch 唯一。Review heartbeat 丢失不被描述为可撤销已经开始的 blocking/to_thread 外部副作用；此类执行由 Task lease 管理，旧 Review owner不得提交协调结果。

0093 将 Task Runtime Outbox delivery 独立收口为 durable authority：Store 持久化 claim owner/token/expiry/单调 fence，允许 expired takeover。0095 进一步要求 `TASK_GRAPH` Outbox 精确绑定现存 revision、非 Graph revision 为 `NULL`，冻结 `graph_revision` 的 INTEGER/nullable/no-default/non-PK 物理形状，并按 `type + id + revision` 隔离 predecessor/head；同 revision 保序，不同 revision 独立推进。历史数据只从 0090/0091/0092 decision、0092 intent、Attempt、唯一 revision，或 Outbox evidence 中 immutable HostCommand `command_id` 回填，后者覆盖正常 multi-revision CREATED，歧义会回滚整笔迁移；所有生产 Graph producer 都写 revision。新 Runtime Event key 使用 `graph_id@revision`，旧 journal 不改写并通过 source Outbox revision 兼容读取；Operator 按 exact revision 查询，ReviewStore 再按 canonical task-node subject ref revision 过滤。Publisher 逐条 `claim → idempotent append → fenced complete`，append 失败则 release 并设置 retry backoff/last error。Composition 将当次 boot/construction 唯一 `recovery_id` 派生为 Outbox owner，启动可关闭后台 worker，并按 aggregate-head 的最早 available/claim-expiry 与有界轮询自动续投。Drain 只在 canonical `total_pending=0` 时返回 `DRAINED`；非空但暂无 ready head 的 inventory 返回 `DEFERRED + next_wakeup_ts_ms`。`list_pending_outbox` 是完整 inventory，claim/backlog 才是 delivery-ready 视图；0093/0095 readiness 缓存在单个 Store 实例，热路径不重扫。0094 已实现独立的 cancellation scanner/claim/heartbeat/defer/expired takeover/continuation coordination，但它不复制 0092 业务状态，也不自动制造 operation/effect/review reconciliation 证据。

0095 还用 deterministic SQL digest 重算 revision-bound HostCommand 的 legacy governed Outbox ID/key，使 ACTIVE 等非 attempt transition exact 回填，同时保持升级前 ID/key 不变。新 Graph payload ref 带 `@revision`；旧 ref 只允许 durable replay compatibility，新写 trigger 拒绝。Runtime Event 的 `TASK_STORE_OUTBOX source_ref` unique partial index 将一条 Outbox 围栏为一条 journal，append 已成功但 complete 未确认的 legacy 重投只能 exact 返回既有事件。payload trigger 与 source index 同属 readiness/receipt-loss gate。

PlanningRecovery 显式 opt-in `include_graph_outboxes=true`，在同一 snapshot 事务读取真实 terminal payload ref；默认 false，普通 Scheduler/Operator 不增加 Outbox 扫描。只有旧版或替代 Store 未提供可选扩展字段时才合成 revisioned fallback；生产 DurableTaskStore 提供 inventory 后 fail closed。`created_ts=0` 的 timestamp 容差可用于 current revisioned 与 legacy unrevisioned 两种可重建 Event candidate，因为两条历史 append 路径都可能用时钟替代零值；除 timestamp 外的字段继续 exact。

节点局部执行由 [`DurableNodeExecutionRuntime`](../../../host_brain/task_runtime/node_execution_runtime.py) 编排，并共享 `AcceptedGraphFinalizationRuntime`。`DurableOperationRecoveryRuntime` 只接管 exact single-node canonical COMMITTED checkpoint，能够只读核对已有 worker usage，或在 usage 完全缺失且无冲突时以持久 elapsed 上界补结算；`CommittedCheckpointContinuation` 不执行 worker，只继续 Verification/Acceptance。general/batch/pre-operation recovery仍 fail closed。只读 Projection 与纯 records 不拥有 mutation authority。

Coordinator 的 `atomic_graph_success_enabled` 与 `bounded_fanout_enabled` 均默认 `false`。Legacy all-ACCEPTED 重入不再直接把 Graph 置为 COMPLETED：共享 finalizer 必须先验证 exact Operation checkpoint/canonical WorkerProjection，再原子提交 Graph+Effect；篡改 fail closed且 worker 不重跑。0091 atomic 与0090 settlement authority保持原边界。

B5C 的 0092 intent/decision 仍是唯一取消业务真相；0099 case/proof/supersession 只处理历史 terminal Graph/Budget 半状态，不取代它。模块继续 `PRODUCTION_OFF`，因为 general/multi-node/pre-budget recovery、外部 deployment inventory/remediation、部署密钥/凭据、完整 key lifecycle 与 canary仍缺。

### 6.2 定时任务和内部 Agent

```text
SchedulerClock / authenticated Internal Agent
  → Secure Ingress authority
  → Governor grant
  → GovernedSchedulerRuntime / GovernedInternalRuntime
  → HostCommand admission
  → Task Runtime
```

Scheduler 与 Internal Agent 均已按该调用链接通。Internal 只在 ingress 接受 `AgentWorkOrder`，剥离调用方 authority 后立即编译为 `WorkOrderV3 + BudgetPolicy`；既有 executor 仅在 child grant 验证后接收兼容投影。Lane A 已完成；0096 已把两者的长时间 Review grant renewal 与 surface continuation 接入同一合同。

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
| P1 | B5 terminal success 尚未 production canary | shadow 已覆盖单节点、串行、多 terminal、篡改与响应丢失；atomic/bounded 和三处生产 composition 必须保持关闭 | Governed Execution |
| P0 | B5C 生产启用条件未完成 | Budget contention 与 0099 窄终态分类已有；cancellation gate会阻断 current anomaly、缺失 proof/supersession closure 与 quarantine。0099 仍为 `WIRED_DEFAULT_OFF / CANARY_NOT_RUN`；仍缺目标部署 inventory/remediation、general/multi-node/pre-budget recovery、现实 resolver、部署密钥/凭据、完整 key lifecycle 与真实 canary | Governed Execution |
| P1 | 0093/0095 Outbox 故障矩阵仍需持续守护 | delivery/identity 已接通；需持续验证 deterministic legacy identity、升级 ID/key 稳定、revisioned/legacy payload 边界、payload/source-index readiness、append-success/complete-loss legacy exact replay、零重复 Event、stale fence、per-revision 顺序与 `DRAINED/DEFERRED` | Durable Outbox Authority |
| P1 | Budget 完整策略仍未收口 | unique `EXCEEDED` winner/`ALREADY_EXCEEDED` 已通过 8/16/32 线程和跨进程验证；concurrency 维度计量与 `DEGRADE/REVIEW` 仍缺 | Governed Execution |
| P1 | 全局 ProcessServiceContainer 和 accessor 仍存在 | 资源归属、测试隔离和进程边界不清 | Composition Kernel |
| P1 | Host Plan 不生成竞争路径 | 通用智能规划目标尚未实现 | Planning and Decision |
| P1 | 核心包依赖图存在大型循环 | 模块无法独立演进、构造和验证 | 各 bounded context + Composition Kernel |
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
