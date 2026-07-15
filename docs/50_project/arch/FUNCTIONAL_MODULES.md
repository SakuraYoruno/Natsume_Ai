受众：MASTER
标题：Natsume AI 功能模块与责任边界

# Natsume AI 功能模块与责任边界

版本：v1.3
核对日期：2026-07-16
代码基线：`2b84ae9ad`
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
| 任务、Skill 与 Delegate | Chat 意图、`/v1/agent/run`、Operator 命令 | `orchestration`、`task_runtime`、`agent_runtime`、`skills` | Model Runtime、Capability Broker、Domain Pack | Host canonical route `WIRED`；原生 DAG 重试/并发仍 `PARTIAL` |
| 自动化与定时任务 | Scheduler API、Monitor Automation | `scheduler`、`lifecycle_runtime` | Governed Command、Life、Memory、Security Immune | 定义、时钟、持久化与 governed execution 已接通；DAG 自动恢复仍受 Lane B 限制 |
| 记忆、检索与设定 | Memory/Canon API、Monitor Memory/Canon | `memory`、`retrieval`、`canon` | SQLite、Chroma、Neo4j、Surface Context | 多类记忆与 Canon proposal/apply 可用；统一 Knowledge OS 为 `MISSING` |
| 数字生命状态 | `/v1/digital-life/*`、Monitor Life Systems | `digital_life` | Projection、Persona、Lifecycle、Delivery Outbox | command/event/reducer 地基 `WIRED`；World/Scene/Intent 统一上下文仍 `PARTIAL/MISSING` |
| 人格、情绪和关系表达 | Surface Reply、Bond API、Monitor Persona/Bond | `persona`、`bond`、`surface_runtime` | Model Runtime、Memory、Life Projection、Embodiment | 表达链可用；Persona 旧直写已中央封口，受治理的演化提案链仍缺失 |
| 主动性与生命周期 | Scheduler tick、proactive API、Monitor Proactive | `lifecycle_runtime`、`digital_life`、`persona` | Governor、Gateway、Edge、Life Outbox | `PARTIAL`；Scene 生产默认关闭，意图统一仲裁未实现 |
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
| Governor | 路由决策、等级策略、能力许可、限流与 grant/token | [`governor/decision.py`](../../../host_brain/governor/decision.py)、[`governor/rules.py`](../../../host_brain/governor/rules.py)、[`governor/action_arbiter.py`](../../../host_brain/governor/action_arbiter.py) | 策略投影、Capability descriptor、审计 | worker执行、搜索 rollout、外部副作用 | `WIRED / PARTIAL`；还没有将 user/task/life/relationship/long-term goal 合并为目标 Intent Arbiter |

### 4.2 命令、图与持久执行

| 模块 | 当前责任 | 主入口/代码证据 | 允许依赖 | 不应拥有 | 状态/缺口 |
|---|---|---|---|---|---|
| Runtime Contracts | WorkOrder、TaskGraph、Budget、Review、Verification、Acceptance 数据合同 | [`contracts/agent_runtime/`](../../../host_brain/contracts/agent_runtime)、[`natsume_contracts/task/v1.py`](../../../natsume_contracts/task/v1.py) | 标准库与纯类型 | app、persistence、provider 实现 | `WIRED`；TaskGraph v1 可选扩展包含 result/completion policy，legacy 多 sink 无 result，atomic policy 必须显式结果；Host/Scheduler/Internal canonical admission 用 V3 |
| Orchestration | HostCommand admission、Governor grant 解析、计划图编译、revision 和 node grant | [`orchestration/governed_command.py`](../../../host_brain/orchestration/governed_command.py)、[`orchestration/planning_graph_compiler.py`](../../../host_brain/orchestration/planning_graph_compiler.py)、[`orchestration/node_grants.py`](../../../host_brain/orchestration/node_grants.py) | contracts、repository ports、PlanningSearch result | 领域 worker 实现、外部副作用 | Host 主链 `WIRED`；失败重规划和所有入口统一性仍 `PARTIAL` |
| Governed Host Adapter | 把 `chat/skill/delegate/plan` 编译为 HostCommand + `WorkOrderV3` + immutable graph | [`app/governed_task_adapter.py`](../../../host_brain/app/governed_task_adapter.py) | Orchestration、Task Runtime、route worker | 执行期二次规划 | `WIRED`；是当前 Host canonical vertical slice |
| Task Runtime | 依赖推进、Attempt、Lease、Idempotency、Budget、Review、Verification、Acceptance、effect intents | [`task_runtime/coordinator.py`](../../../host_brain/task_runtime/coordinator.py)、[`task_runtime/heartbeat.py`](../../../host_brain/task_runtime/heartbeat.py)、[`task_runtime/dag_scheduler.py`](../../../host_brain/task_runtime/dag_scheduler.py)、[`task_runtime/budget_runtime.py`](../../../host_brain/task_runtime/budget_runtime.py)、[`task_runtime/review_runtime.py`](../../../host_brain/task_runtime/review_runtime.py) | Task repository ports、worker/verifier/acceptance ports | 路由意图识别、计划搜索 | `PARTIAL_B5_TERMINAL_SUCCESS_SHADOW / PRODUCTION_OFF`；独立 atomic/bounded 开关均默认关闭，三处生产 composition OFF。0091 单 UoW 和 canonical result projection 已覆盖单节点/串行/多 terminal；0090/B4 保留。B5C durable cancel、revisioned 0090、跨进程 Outbox、Budget 高竞争、takeover/continuation/canary 未闭环 |
| Scheduler | 定义、occurrence、时钟、lease、模式/资源策略和定时 worker registry | [`scheduler/clock.py`](../../../host_brain/scheduler/clock.py)、[`scheduler/scheduling_center.py`](../../../host_brain/scheduler/scheduling_center.py)、[`scheduler/governed_runtime.py`](../../../host_brain/scheduler/governed_runtime.py) | Secure Ingress、Governor、Task Runtime、Scheduler Repository | 自建第二套 Task 状态机 | `WIRED`；Clock/Manual occurrence 已贯通 V3/Budget/child grant/Review/Verification/Acceptance/Receipt |
| Internal Agent Runtime | 认证后的内部 Agent WorkOrder、结果持久化和 Review API | [`app/internal_task_runtime.py`](../../../host_brain/app/internal_task_runtime.py)、[`app/internal_agent_ingress.py`](../../../host_brain/app/internal_agent_ingress.py) | Secure Ingress、Agent Executor、Task Runtime | 绕过 HostCommand 权限 lineage | `WIRED`；边界 v2 编译 V3，完整 child grant、Budget、PRE/POST Review、Verification、Acceptance、Receipt 与 replay 已验证 |
| Read Projection / Executive Guard | `task_progress`、`recent_turn` 的只读投影与 retired fail-close guard | [`agent_runtime/read_routes.py`](../../../host_brain/agent_runtime/read_routes.py)、[`executive_runtime/runtime.py`](../../../host_brain/executive_runtime/runtime.py) | Durable Task/Conversation projection | Task/Attempt、worker、状态写入 | `WIRED / RETIRED`；Executive 无生产入口，legacy SkillDag 执行和内存 registry 已删除 |

### 4.3 规划、Agent 和领域能力

| 模块 | 当前责任 | 主入口/代码证据 | 允许依赖 | 不应拥有 | 状态/缺口 |
|---|---|---|---|---|---|
| PlanningSearch Kernel | Candidate expansion、cognitive ranking、conditional MCTS、virtual transition、reward 和 trace | [`planning_search/service.py`](../../../host_brain/planning_search/service.py)、[`planning_search/contracts.py`](../../../host_brain/planning_search/contracts.py)、[`planning_search/ports.py`](../../../host_brain/planning_search/ports.py) | 纯合同、领域注入的纯函数 port | 工具执行、网络、持久写入、真实副作用 | 内核 `WIRED`；通用智能规划收益未证明 |
| Host Plan Adapter | 把 Governor 已批准的 steps 合法化为顺序候选并编译图 | [`orchestration/plan_intent_search.py`](../../../host_brain/orchestration/plan_intent_search.py)、[`orchestration/planning_execution.py`](../../../host_brain/orchestration/planning_execution.py) | PlanningSearch、graph compiler | 凭空新增工具或越过批准边界 | `PARTIAL`；只比较已批准步骤的顺序，不生成竞争方案 |
| Agent Runtime | Agent contract、planner adapter、executor、workspace/artifact、verification 和 studios | [`agent_runtime/executor.py`](../../../host_brain/agent_runtime/executor.py)、[`agent_runtime/planner_adapter.py`](../../../host_brain/agent_runtime/planner_adapter.py)、[`agent_runtime/workspace_paths.py`](../../../host_brain/agent_runtime/workspace_paths.py)、[`agent_runtime/workspace_policy.py`](../../../host_brain/agent_runtime/workspace_policy.py)、[`agent_runtime/studios/`](../../../host_brain/agent_runtime/studios) | Model/Capability ports、Artifact store、Task context | 入口身份、Task 权威、全局 composition | `PARTIAL`；D0 路径围栏已接 scoped I/O/Verification，但 durable workspace owner、隔离 worktree、handle-level TOCTOU 防护未完成，且尚有 `AgentWorkOrder` 兼容面 |
| Domain Packs | Research、Media Translation、Light Novel、Pentest 等声明式领域包与生命周期 job | [`domain_packs/registry.py`](../../../host_brain/domain_packs/registry.py)、[`domain_packs/production_runtime.py`](../../../host_brain/domain_packs/production_runtime.py)、[`domain_packs/contracts.py`](../../../host_brain/domain_packs/contracts.py) | 平台 ports、Agent Runtime、Capability Broker | 复制平台调度、自建授权 | `PARTIAL`；存在生产 job 资产，尚未全部收敛到统一 `WorkOrderV3` 主链 |
| Pentest Domain | 授权范围、阶段执行、工具 adapter、finding/report、攻击图和自主策略 | [`pentest/production_runtime.py`](../../../host_brain/pentest/production_runtime.py)、[`pentest/secure_run.py`](../../../host_brain/pentest/secure_run.py)、[`pentest/agent/agent_loop.py`](../../../host_brain/pentest/agent/agent_loop.py) | Secure authority、受限工具 port、PlanningSearch adapter、领域 repositories | 绕过 scope/approval 的真实工具调用 | 领域功能 `WIRED`；L3 仍 `SHADOW_UNCALIBRATED`，且领域包内部责任过密 |
| Tribe / Organ / BioOps | 意图分流、内部事件、生理隐喻 agent、organ 健康与 BioOps 专项工作流 | [`tribe/thalamus_agent.py`](../../../host_brain/tribe/thalamus_agent.py)、[`tribe/event_bus.py`](../../../host_brain/tribe/event_bus.py)、[`organ_ops/organ_registry.py`](../../../host_brain/organ_ops/organ_registry.py)、[`bio_ops/workflow.py`](../../../host_brain/bio_ops/workflow.py) | Model invocation port、typed event、受治理 capability | 任务真相、长期状态的平行 writer | `PARTIAL`；应保持为策略/领域 proposal 层，不能成为第二执行内核 |

### 4.4 模型、能力、工具与 Skill

| 模块 | 当前责任 | 主入口/代码证据 | 允许依赖 | 不应拥有 | 状态/缺口 |
|---|---|---|---|---|---|
| Model Runtime | 路由目录、provider adapter、prompt registry、ModelAttempt、重试/输出合同和审计 | [`model_runtime/model_attempt_service.py`](../../../host_brain/model_runtime/model_attempt_service.py)、[`model_runtime/production_composition.py`](../../../host_brain/model_runtime/production_composition.py)、[`model_runtime/prompt_registry.py`](../../../host_brain/model_runtime/prompt_registry.py) | provider ports、route policy、audit journal | 业务路由 authority、未受管的外部工具 | `WIRED`；为所有模型调用的目标主干 |
| Capability Plane | Capability request/grant/attempt/receipt、policy 和执行 journal | [`capability_plane/broker.py`](../../../host_brain/capability_plane/broker.py)、[`capability_plane/contracts.py`](../../../host_brain/capability_plane/contracts.py)、[`capability_plane/production_composition.py`](../../../host_brain/capability_plane/production_composition.py) | Governor grant、descriptor、adapter、journal | 不受授权的自行执行 | `WIRED`；Capability Maturity 尚未默认成为生产门禁 |
| Tool Runtime | 工具目录、Broker、权限守卫、实际 adapter | [`composition/tool_runtime.py`](../../../host_brain/composition/tool_runtime.py)、[`tools/`](../../../host_brain/tools)、[`app/tool_routes.py`](../../../host_brain/app/tool_routes.py) | Capability Plane、Audit、外部 integration | 自己颁发 grant、自建 Task | `WIRED / MIXED OWNERSHIP`；工具注册和实现仍分散 |
| Built-in Skills | 内置 skill metadata、permission、scheduler profile 和 executor | [`skills/registry.py`](../../../host_brain/skills/registry.py)、[`skills/impl/`](../../../host_brain/skills/impl) | Tool/Capability port、冻结输入 | 目录扫描自动提权、生产状态 authority | `WIRED`；这里是代码包，不是已删除的顶层杂物 `skills/` |
| Skill Supply Chain | 签名、inventory/SBOM 检查、quarantine/stage 与审计 | [`skills/supply_chain.py`](../../../host_brain/skills/supply_chain.py)、[`skills/signed_intake_http.py`](../../../host_brain/skills/signed_intake_http.py) | 信任键、Quarantine、Supply Chain Repository | 未经验证的 import/activation | `PARTIAL`；安全接收存在，staged activation 和 rollback 尚未形成完整可用闭环 |
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
| World/Environment Projection | 当前环境与世界事实只读投影 | [`runtime/world_model.py`](../../../host_brain/runtime/world_model.py)、[`runtime/environment_state.py`](../../../host_brain/runtime/environment_state.py)、[`persistence/world_model_query_repository.py`](../../../host_brain/persistence/world_model_query_repository.py) | Life/Context 投影 | 自建事实 writer、执行副作用 | `PARTIAL / DEFAULT_OFF`；不是预测、模拟或规划 world model |
| Persona Surface | 人格表达、reply plan、情感/关系质感、memory grounding、文本质量门禁 | [`persona/surface_main_chain.py`](../../../host_brain/persona/surface_main_chain.py)、[`persona/surface_runtime.py`](../../../host_brain/persona/surface_runtime.py)、[`persona/reply_plan.py`](../../../host_brain/persona/reply_plan.py) | 只读 Persona/Life/Memory context、Model Runtime | 任务调度、能力授权、人格永久直写 | `WIRED / LARGE`；表达链能力丰富，边界与文件仍过密 |
| Scene Policy | 根据场景生成表达/主动性约束 | [`persona/scene_policy.py`](../../../host_brain/persona/scene_policy.py)、[`surface_runtime/life_context.py`](../../../host_brain/surface_runtime/life_context.py) | World/Life 只读上下文 | 写入 World/Life/Persona | `PARTIAL_DEFAULT_OFF`；缺统一 scene aggregate、transition journal、TTL 与 re-evaluation owner |
| Bond | 用户身份解析、关系阶段、里程碑、叙事和关系投影 | [`bond/bond_stage_engine.py`](../../../host_brain/bond/bond_stage_engine.py)、[`bond/milestone_tracker.py`](../../../host_brain/bond/milestone_tracker.py)、[`bond/bond_projection.py`](../../../host_brain/bond/bond_projection.py) | 事件、只读 Life/Memory、repository | 绕过人格版本激活直改 trait | `PARTIAL / CANDIDATE ONLY`；stage/milestone 可触发 guard 后候选，但中央围栏禁止 trait 直写 |
| Persona Growth | trait drift/growth 候选、interaction/emergent 候选和部分反馈 | [`persona/persona_growth.py`](../../../host_brain/persona/persona_growth.py)、[`persona/evolution_orchestrator.py`](../../../host_brain/persona/evolution_orchestrator.py)、[`persona/emergent_growth_engine.py`](../../../host_brain/persona/emergent_growth_engine.py) | 只读 Persona 与 guard；未来只产生 Feedback/Identity proposal | 非迁移 trait 写入、伪造已应用历史或消费一次性状态 | `CENTRAL_FENCE_WIRED`；NE-01 已封口并静态禁止新 writer，NE-06 再建立 proposal 闭环 |
| Lifecycle Runtime | Scheduler handler、Life jobs、背景投影和 proactive dispatch | [`lifecycle_runtime/composition.py`](../../../host_brain/lifecycle_runtime/composition.py)、[`lifecycle_runtime/life_jobs.py`](../../../host_brain/lifecycle_runtime/life_jobs.py)、[`lifecycle_runtime/proactive_dispatch.py`](../../../host_brain/lifecycle_runtime/proactive_dispatch.py) | Scheduler、Life Command、Governor、delivery ports | 新的时钟 authority、人格直写 | `PARTIAL`；定时执行闭环受 Scheduler runtime 故障限制 |
| Evolution / Identity | 实验隔离策略、identity snapshot/diff/review/rollback 合同 | [`evolution_lab/policy.py`](../../../host_brain/evolution_lab/policy.py)、[`release/identity_snapshot.py`](../../../host_brain/release/identity_snapshot.py) | 隔离实验、Review、版本存储 | 直接生产激活、Lab 副作用 | Evolution Lab `PLACEHOLDER`；Identity Snapshot `PARTIAL`；Migration `MISSING` |

### 4.7 渠道、Gateway 与 Edge

| 模块 | 当前责任 | 主入口/代码证据 | 允许依赖 | 不应拥有 | 状态/缺口 |
|---|---|---|---|---|---|
| Host Channels | Edge transport、TTS/VTS/MateEngine mapping、embodiment outbox 和 Life delivery transport | [`channels/edge_transport.py`](../../../host_brain/channels/edge_transport.py)、[`channels/embodiment_outbox.py`](../../../host_brain/channels/embodiment_outbox.py)、[`channels/mateengine.py`](../../../host_brain/channels/mateengine.py) | 跨进程 contracts、Acceptance effect/outbox | 未验证直接物理副作用 | `WIRED / DEVICE_DEPENDENT` |
| Gateway Transport | 平台 webhook 验签、归一化、durable intake/delivery、receipt 和重试 | [`edge_client/gateway/server.py`](../../../edge_client/gateway/server.py)、[`edge_client/gateway/delivery_runtime.py`](../../../edge_client/gateway/delivery_runtime.py)、[`persistence/gateway_transport_repository.py`](../../../host_brain/persistence/gateway_transport_repository.py) | `natsume_contracts.edge`、Host HMAC 输入端、平台 adapter | Host 业务实现、对话状态真相 | `WIRED`；Gateway store factory 通过合同注入，但当前组装较大 |
| Edge Runtime | 本地 sensor/actuator composition、语音 pipeline、Avatar 状态和健康 | [`edge_client/runtime/bootstrap.py`](../../../edge_client/runtime/bootstrap.py)、[`edge_client/runtime/voice_pipeline.py`](../../../edge_client/runtime/voice_pipeline.py)、[`edge_client/actuators/`](../../../edge_client/actuators)、[`edge_client/senses/`](../../../edge_client/senses) | Edge contracts、本地 provider/OS adapter | Host app/audit 实现、全局任务 authority | `WIRED / ENVIRONMENT_DEPENDENT`；边界扫描禁止 Edge 导入 Host 实现 |
| Gateway Platform Adapters | QQ、微信、飞书、企微、Telegram、LINE 协议适配 | [`edge_client/gateway/adapters/`](../../../edge_client/gateway/adapters) | 平台 SDK/API、Gateway model | 跨平台业务策略、Host 任务状态 | `PLATFORM_DEPENDENT`；未配凭据或本地 bridge 时应显式 degraded |
| Desktop Pet | HMAC/replay/rate-limit 本地 bridge、Live2D 渲染和交互 | [`desktop_pet/bridge_security.cjs`](../../../desktop_pet/bridge_security.cjs)、[`desktop_pet/main.cjs`](../../../desktop_pet/main.cjs)、[`desktop_pet/renderer/pet.js`](../../../desktop_pet/renderer/pet.js) | Edge/Host 签名命令 | 人格、Task 或 Life 状态 writer | `WIRED / LOCAL ONLY` |

### 4.8 观测、持久化和 composition

| 模块 | 当前责任 | 主入口/代码证据 | 允许依赖 | 不应拥有 | 状态/缺口 |
|---|---|---|---|---|---|
| Observability Query | projection query、trace、metrics、health、degradation 和 operator auth | [`observability/operator_query_gateway.py`](../../../host_brain/observability/operator_query_gateway.py)、[`observability/projection_store.py`](../../../host_brain/observability/projection_store.py)、[`observability/query_boundary.py`](../../../host_brain/observability/query_boundary.py) | 只读 projection/store | domain writer、原始 secret 返回 | `WIRED`；应继续收敛为纯读边界 |
| Operator Command | 受认证的运维命令入口、受治理路由和 outcome journal | [`observability/secure_command_ingress.py`](../../../host_brain/observability/secure_command_ingress.py)、[`observability/command_router.py`](../../../host_brain/observability/command_router.py)、[`observability/command_outcome_journal.py`](../../../host_brain/observability/command_outcome_journal.py) | Secure Ingress、typed command ports | Query handler 直写 domain store | `WIRED / PARTIAL`；应全部进入通用 governed runtime |
| Persistence | 按聚合提供 SQLite repository、OCC、journal/outbox 和 read projection | [`persistence/`](../../../host_brain/persistence)、[`audit/migration_runner.py`](../../../host_brain/audit/migration_runner.py)、[`audit/sqlite_ledger.py`](../../../host_brain/audit/sqlite_ledger.py) | SQL/driver、稳定 contracts | 业务决策、route 编排、人格策略 | `WIRED / LARGE`；持久化主干稳定，但若干 repository 过大且与 application 双向耦合 |
| Runtime Eventing | runtime event 投影和投递恢复 | [`eventing/runtime_event_projection.py`](../../../host_brain/eventing/runtime_event_projection.py)、[`persistence/runtime_event_repository.py`](../../../host_brain/persistence/runtime_event_repository.py) | event contract、repository | 替代 domain aggregate | `WIRED / SUPPORTING` |
| Composition | 构造 Task、Life、Memory、Canon、Scheduler、Tool 与进程服务对象图 | [`composition/task_runtime.py`](../../../host_brain/composition/task_runtime.py)、[`composition/life.py`](../../../host_brain/composition/life.py)、[`composition/memory.py`](../../../host_brain/composition/memory.py)、[`composition/process_service_container.py`](../../../host_brain/composition/process_service_container.py) | 所有具体 adapter 和应用构造器 | 业务方法、模块级可变服务真相 | `INCOMPLETE`；两批 6 槽位/4 死 accessor 已退役，第二批另收掉 7 个宽泛重导出；排除 6 个 lock 与 `application_ports` 后仍有 53 个可变服务引用，typed container 与大型全局 locator 并存 |
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

Task composition 在开放运行时前依次完成 migration、`task_store.recover_startup`、publisher 构造与 recovery outbox 发布；任一恢复异常 fail closed。ASGI 把逻辑 owner 派生为当前 `ProcessTaskRuntimeContainer` 构造唯一的 execution owner。执行中 heartbeat 已续租 Lease/Reservation 并阻断失去所有权后的 COMMIT，但启动门禁和心跳都不会自动续跑 recoverable attempt，也不等价于在线 takeover 或即时 reconciliation 已完成。

Coordinator 的 `atomic_graph_success_enabled` 与 `bounded_fanout_enabled` 均默认 `false`，Host/Scheduler/Internal 生产 composition 未启用。atomic policy 在 repository admission 前检查 runtime capability、node-grant service 与 parent grant；Coordinator 只接收 exact node-grant map。全节点 Accepted 后，0091 UoW 使用重算 digest并绑定 command/request/route/target/grant 的 canonical worker projection，只有显式 result 生成 effects 与公开结果；completed replay 不重编 effects，双响应丢失重读 terminal authority。0090/B4 的 live/re-entry、Budget、failure/review 语义保持。B5C durable cancel、revisioned 0090、reconciliation/continuation 和生产 canary 未完成。

### 6.2 定时任务和内部 Agent

```text
SchedulerClock / authenticated Internal Agent
  → Secure Ingress authority
  → Governor grant
  → GovernedSchedulerRuntime / GovernedInternalRuntime
  → HostCommand admission
  → Task Runtime
```

Scheduler 与 Internal Agent 均已按该调用链接通。Internal 只在 ingress 接受 `AgentWorkOrder`，剥离调用方 authority 后立即编译为 `WorkOrderV3 + BudgetPolicy`；既有 executor 仅在 child grant 验证后接收兼容投影。Lane A 已完成，长时间 Review 的 grant renewal 属于后续 continuation 合同。

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
| P1 | 历史半提交终态尚无自动 reconciliation；`WAITING_APPROVAL` 仍非统一 optional-attempt observation | `BLOCKED + failure Node` / `FAILED + ACTIVE Budget` 不由 B4 自动修复；PRE Review 保留独立专线 | Governed Execution |
| P1 | legacy TaskGraph Outbox 没有 revisioned aggregate identity | multi-revision graph ID 无法完整扫描历史 taskbatch marker 缺行/错绑，只能校验返回 decision row 自身 | Durable Outbox Authority |
| P1 | 长时间 Review 的 child-grant renewal/derivation 尚未统一 | 等待超过 grant TTL 后的自动 continuation 仍缺明确授权时机 | Governed Execution |
| P1 | Persona candidate 尚未进入统一 proposal/Review/版本激活 | 旧直写虽已封口，但人格长期演化仍不可用 | Persona Evolution Authority |
| P1 | B5 terminal success 尚未 production canary | shadow 已覆盖单节点、串行、多 terminal、篡改与响应丢失；atomic/bounded 和三处生产 composition 必须保持关闭 | Governed Execution |
| P0 | B5C durable cancel 与 takeover/continuation/reconciliation 未完成 | 进程内取消能 join child/heartbeat，但 crash 后只有 TTL/startup recovery，不能自动接管或续跑 | Governed Execution |
| P1 | Outbox publisher 只有同进程实例锁 | Host/Internal/Scheduler 共享实例的 drain 已串行，但多进程仍没有 durable Outbox claim/CAS 或单 writer | Durable Outbox Authority |
| P1 | Budget CAS 高竞争语义未收敛 | 固定重试上限与 `ALREADY_EXCEEDED` 在 concurrency > 4 时仍不能作为完整生产合同 | Governed Execution |
| P1 | 全局 ProcessServiceContainer 和 accessor 仍存在 | 资源归属、测试隔离和进程边界不清 | Composition Kernel |
| P1 | Host Plan 不生成竞争路径 | 通用智能规划目标尚未实现 | Planning and Decision |
| P1 | 核心包依赖图存在大型循环 | 模块无法独立演进、构造和验证 | 各 bounded context + Composition Kernel |
| P1 | World/Scene/Intent 没有统一 context authority | 对话、主动性、规划和人格可使用不同局面解释 | World, Scene and Intent |
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
