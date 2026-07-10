受众：MASTER
标题：产品需求文档（PRD）

# Natsume Ai 产品需求文档（PRD）
版本：v2.5
日期：2026-07-10
定位：以当前代码事实为基线，记录已提供能力与已批准待实施需求的入口、边界和验收口径；规划项必须显式标注，不能写成已实现事实。

状态说明：`FR-NLE-001`～`FR-NLE-005` 是 M48-A `READY_FOR_EXECUTION` 规划项；`TASK-0461`～`TASK-0464` 与 `TASK-0466` 已于 2026-07-10 完成，其余 `TASK-0465`、`TASK-0467～0486` 为 `PLANNED`，其中 TASK-0465、0467、0468 已通过前置 Gate 解锁。`FR-ARCH-001`～`FR-ARCH-010` 是 M48～M57 `ADOPTED_TARGET_ARCHITECTURE` 目标需求，`TASK-0487～0622` 为尚未解锁实施的 `PROGRAMMED`。其余章节主要描述 2026-06-30 已核验的当前能力。规划项只有在对应 TASK、测试与 Seal 证据成立后才转为已实现事实。

---

## 1. 产品定义

Natsume Ai 是一个本地优先的 AI 陪伴系统，由 5 个协同运行面组成：

| 运行面 | 端口 | 入口 | 职责 |
|--------|------|------|------|
| Host Main | :8000 | `host_brain.app.asgi:app` | 主权裁决、人格渲染、记忆、审计、路由装配 |
| Obs Backend | :18082 | `host_brain/obs_main.py` | 可观测性后端 |
| Edge RPC | :17800 | `edge_client/__main__.py` | 本地感知与执行中枢 |
| Unified Message Gateway | :19000 | `edge_client/gateway/server.py` | 多平台消息收发 |
| Desktop Pet | :18788 | `desktop_pet/main.cjs` | Electron 桌面宠物 |

Host Main 由 `host_brain/app/asgi.py` 暴露 ASGI app，并通过 `app_factory` 与 `router_registry` 装配 HTTP 路由。核心原则：**模型/Agent 候选层负责提案，Host 负责事实、长期记忆、人格/回复政策、安全门控与最终表达收口；Edge、DesktopPet、MateEngine、VTS、VRM 只作为外显执行面。所有执行事实必须可审计回放。**

---

## 2. 核心功能需求

### FR-CHAT：Normal Chat 主链

- 入口：`POST /v1/chat`，SSE 流式响应。
- 三阶段协议：PRESENCE（在场流，非权威）→ COMMIT（提交流，Governor 后产生）→ POST（渲染流，读 Commit 快照）。
- 主链：`chat_routes/chat_handler → thalamus → Governor → Cortex/Executive → SurfaceMainChain → ReplyEnvelope`。
- 工程装配链：`host_brain.app.asgi:app → app_factory.create_host_fastapi_app() → router_registry → chat_routes.handle_v1_chat_route()`。
- `chat_immune_gate` 在 HTTP 层做 fastpath/BLOCK 拦截，再进入主编排。
- SurfaceMainChain（`host_brain/persona/surface_main_chain.py`）是 normal chat 文本的**唯一作者**；旧 prompt_builder 链路只作为辅助 facts/state 提供者。

验收口径：
- `/v1/chat` 的最终可发送文本必须经 `ReplyEnvelope` 收口。
- 未经 Governor 许可的执行意图不得进入外显执行面。
- PRESENCE 内容不得被下游当作权威事实。

### FR-INGRESS：Host 输入回流

- `POST /v1/host/input` 是 Gateway/ASR ingress，用于归一化外部消息与语音输入，不是简单转发 `/v1/chat`。
- 输入链路承担即时 ACK、质量分析、偏航检测、进度推送与重对齐通知，再进入 thalamus 与 Cortex/Executive 后续链路。
- `POST /v1/edge/events` 是 Edge runtime events，用于播放状态、资源门控、inline waiters 等事件上报，不走 `/v1/host/input`。

验收口径：
- Gateway/ASR 输入必须携带来源与回包上下文，不能被写成“直接调用 chat”。
- Edge 运行时事件与用户输入必须分流，避免事件被误判成用户话语。

### FR-SURFACE：人格渲染

- 75 文件位于 `host_brain/persona/`（120+ 类），按 Phase I-XVI 组织，包括：
  - `persona_engine.py`（Persona 引擎，7 维特质向量）+ `personality_traits.py`
  - `inner_world.py`（2,565 行，离线状态机）+ `inner_activity_agent.py` + `inner_monologue.py`
  - `surface_main_chain.py` → `surface_runtime.py`（2,668 行）→ `surface_memory_recall.py`（4,539 行）→ `surface_packet_compiler.py`
  - `surface/candidate_scoring.py`（1,912 行）→ `surface/scoring/`（6 文件评分管线：parse → stage_positions → control_affect → texture）
  - `proactive_voice.py`（1,477 行，6 种场景主动表达）+ `emotional_proactive.py`
  - `text_quality_gate.py`、`galgame_text_contract.py`（1,631 行）
- 人格设定文件：`Soul.md`（项目根目录）。
- Surface 层渲染已裁决事实、记忆候选、风格约束与情绪标签。Inner-state leakage 通过 `ReplyControlPacket`（7 字段）影响 LLM tone，不暴露原始私密思维。

### FR-GATEWAY：多平台消息网关

- 独立运行面，6 平台适配器：wechat、qq、feishu、wecom、telegram、line。
- 链路：平台 Webhook → `gateway/server.py` → `adapter.parse_inbound()` → `MessageNormalizer.normalize()` → `GatewayRouter.route()` → `EdgeInputReporter._send()` → `POST /v1/host/input` → Host 输入回流链 → `edge_rpc_client.py` → `POST /v1/gateway/reply` → `OutboundDispatcher.dispatch()` → 平台 adapter `send_reply()`。
- 出入站均走 AIRLOCK 净化。

### FR-EMOTION：情感系统

- `host_brain/core/metabolism.py`：EmotionVector（VAD 三维连续情绪）、HardState。
- `host_brain/core/emotion_timeline.py`：情感时间线、compute_emotion_echo。
- `host_brain/core/endocrine.py`：离散硬状态 → 连续内分泌基调（dopamine/serotonin/cortisol）。
- emotion_tag 确定性映射到 VTS 表情 ID 和 TTS 语气。

### FR-MEMORY：生活记忆

- LifeMemoryStore（`host_brain/memory/life_memory.py`，8260+ 行，SQLite 持久化）是记忆 SSOT。
- 多路召回融合：`RecallFusionServiceContract` → vector + FTS5 混合 → 候选过滤 → `surface_memory_recall` 决定注入。
- 记忆蒸馏：Episodic → 语义记忆 → Canon pending 提案。
- Markdown 知识库召回（`markdown_kb_store.py`）作为补充通道。

### FR-CANON：World Canon 与人格锚

- World Canon：`host_brain/canon/world/` 与 `WorldCanonStore`，维护最小权威内容集合，用于一致性引用（非执行事实）。
- 当前不设独立风格 Canon 功能项；表达风格由 `Soul.md`、Soul/Persona 编译锚、SurfaceMainChain 控制包与硬门禁共同约束。
- Canon 更新走 Host 审批、版本化写入与审计；不得用 Canon 声明“已执行/已保存/用户已确认”等事实。

### FR-TRIBE：器官族系统

- 23 个 family spec，均 A 级升级完毕，无灰度开关。
- `host_brain/tribe/registry.py`：family 注册。
- 关键 agent：thalamus_agent（意图编译）、immune_agent（风险门控）、limbic_agent（动机态）、proprioception_agent（认知负荷）、prefrontal_agent（漂移告警）。

### FR-AGENT-RUNTIME：Agent Runtime

- Agent Runtime 的核心输入契约是 `AgentWorkOrder`。
- 必填/关键字段：`domain`、`run_mode`、`objective`、`workspace`、`allowed_actions`、`forbidden_actions`、`approval_policy`。
- 写模式必须同时受 `WorkspacePolicy` 与 `approval_policy` 限制：需要显式 `allowed_actions` 包含 `write_files`，需要禁止 `modify_secret_files`，并在 `approval_policy.require_confirm=true` 时要求 `approval_policy.confirmed=true`。
- `self_evolve` 不得直接在 main/master 分支写入。

验收口径：
- Agent 写入类任务不能只凭自然语言授权执行。
- 运行结果必须带工作目录、artifact、审计事件或可追踪失败原因。

### FR-OBS：可观测性

- Obs Backend（`host_brain/obs_main.py`）独立进程。
- Monitor UI（`host_brain/app/monitor_ui/`）是多页面 SPA。
- `host_brain/app/monitor_ui/src/app/routeManifest.jsx` 是 Monitor 路由与导航 SSOT。
- 当前导航固定为 7 组：Cockpit、Life Systems、Observability、Operations、Execution、Security Lab、Integrations。
- 预热缓存：runtime snapshot、trace feed、insights summary、digital life overview、life body。

### FR-AVATAR：外显执行面

- Edge、DesktopPet、MateEngine、VTS、VRM 是外显执行面；事实、长期记忆、人格/回复政策、安全门控均在 Host。
- Desktop Pet：Electron 应用（`desktop_pet/main.cjs`），HTTP 控制端口 18788。
  - Actions：show、hide、preset、touch、emote、speak、click_through、status。
- MateEngine：UDP 桥接（`host_brain/channels/mateengine.py`），端口 32145。
  - 13 种 v0 事件、11 种 pose preset、状态→姿势→表情映射。
  - 支持 action_sequence / choreography 编排。
- VTS：emotion_tag → expression_id 确定性映射（16 种表情）。
- `/v1/edge/visual_locate` 当前为 stub/removed，返回 `VLM_REMOVED`，不得作为可用视觉定位自动化能力验收。

### FR-VOICE：语音链

- ASR：faster-whisper large-v3，流式/分段。
- TTS：GPT-SoVITS，全 GPU 常驻。
- Barge-in：用户开口可打断当前播报。
- Edge 资源闸门：ASR > 播放 > TTS 合成 > 兜底 LLM。

### FR-MODE：模式切换

- 6 种系统模式：normal、focus、gaming、sleep、maintenance、locked。
- `host_brain/core/mode_transition.py`：ModeTransitionService。
- 6 种触发来源：MANUAL、AUTO_GAMING、AUTO_IDLE、AUTO_WAKEUP、AUTO_SCHEDULE、SKILL。
- 模式影响：能力矩阵、资源预算、调度器行为。

### FR-CODEX：Codex Runtime

- Codex 作为正式 LLM provider，配置：`HOST_CODEX_BASE_URL`、`HOST_CODEX_MODEL_ID`。
- `host_brain/integrations/codex_cli_provider.py`：CLI 适配。
- App-server 线程预热保留在当前运行时能力中。

### FR-SECURITY：安全与免疫

- `host_brain/app/chat_immune_gate.py`：免疫门控（HTTP 层 fastpath/BLOCK）。
- `host_brain/airlock/`：入站隔离（quarantine、sandbox、sanitizer、pii_filter）+ 出站净化。
- Governor rate limiting + Token 体系（approval/confirm/undo）。
- 审计三联画：intent_log / governor_log / skill_exec_log。

### FR-SECURITY-LAB：Pentest 安全实验室

- `/v1/pentest` 是 Security Lab 入口，124 个 Python 文件分为 10 个子系统。
- API 入口：capabilities、environment、runs、run detail、events、cancel、approve-side-effect、continue-side-effect、report、knowledge ingest/stats。
- ToolSpec：88 个，Executor：92 个，Side-effect：37 个，0 缺 executor。
- 规划器：3 条路径（MCTS + ReAct + CognitiveEngine），由 `PENTEST_COGNITIVE_KERNEL` 环境变量控制。
- Agent 子包（`pentest/agent/`，31 文件）：cognitive_engine、belief_model、goal_model、evolution_core、meta_cognition 等。
- Adversarial 子包（9 文件）：红蓝对抗 game loop + 遗传算法策略协同进化（pop=20）。
- Swarm 子包（8 文件）：多 agent 协调器 + 消息总线 + 图合并。
- Online 子包（5 文件）：三层安全门控（ONLINE_SCAN_MODE=offline|manual + AuthorizationManager + RateGovernor 硬编码限速）。
- Phase1-3 与 report 默认可作为 readonly/fixture 工作流；Phase4/5 side-effect 必须经过安全实验室护栏。
- Phase4/5 side-effect 需要同时满足 `allow_exploit`、Governor/approval token、scope guard、rate guard、engagement manifest guard。

### FR-PROACTIVE：主动自主性

- Proactive 回路：`DigitalLifeSupervisor.tick_loop()` → `autonomous_self.apply_autonomous_self_cycle()`（8 维奖励权重 + 7 维自适应特质）→ `PersonaProactiveVoice.evaluate()`（6 种场景，24h per-type 冷却）→ Edge RPC proactive_push。
- API 端点：
  - `POST /v1/digital-life/proactive/attempt` — 手动触发（dry_run/forced）
  - `GET /v1/digital-life/proactive/recent` — 近期 outbox
  - `GET /v1/digital-life/proactive/episodes/recent` — 近期 episodes
- 观测端点：`GET /obs/proactive-autonomy` — proactive autonomy 快照（inner_weather/reach_out_calibration/active_projects/proactive_readiness/top_intention/dialogue_closure）
- Monitor UI：`/proactive-autonomy` 页面（ProactiveAutonomyDashboard）
- 持久化：`life_continuity.py`（1,920 行）SQLite `proactive_life_outbox` 表

### FR-SCHEDULER：调度系统

- 声明式 Intent 调度（`host_brain/scheduler/`）：NatsumeScheduler + SchedulerHealthReporter。
- Host 常驻任务调度（`host_brain/app/automation/task_scheduler.py`）：HostTaskScheduler。
- 两者分开管理：声明式调度面向业务 intent，常驻调度面向系统维护任务。

### FR-NLE：V1 长期演化安全收敛

#### FR-NLE-001：领域与核心状态边界

- Domain Pack 只能写本领域状态；不得直接写 Persona、Bond、LifeMemory 或 WorldCanon。
- WorldCanon 变更必须经过唯一受治理 Apply 入口；候选编号本身不构成审批。
- 现有领域数据迁移必须可对账、可回滚，并保留来源与证据引用。

验收口径：

- Pentest 在 domain-only 模式下对 WorldCanon 的直接读写为 0。
- 无有效授权、授权过期、主体/版本不匹配或重复消费时，Canon Apply 必须拒绝并留下审计证据。

#### FR-NLE-002：统一、持久的任务控制面

- 一个复杂任务只生成并消费同一 TaskGraph revision；Planner 不得在执行阶段静默生成第二份计划。
- Child WorkOrder 与 SubAgent request 必须保留 root/parent/graph/node lineage、权限和 Context 引用，不能由调用者拼装无来源字典。
- TaskGraph 的依赖、attempt、lease、heartbeat、retry、cancel、recovery 和业务幂等状态必须持久化。
- token、tool、time、concurrency、retry 和 model cost 预算必须可预留、计量、阻断和回放；模型不得自行扩大预算。
- 新旧执行器切换只影响新任务；存量任务不得跨执行器恢复或重复执行副作用。

验收口径：

- 未满足依赖的节点启动数为 0；循环依赖显式阻断。
- 进程重启后任务可恢复；同一业务副作用不因重规划、重试或重复 resume 再次执行。

#### FR-NLE-003：验证、审查与事件回放

- required verification 只有 `PASS` 可进入已验证状态；`FAIL`、`ERROR` 和 `NOT_RUN` 均 fail closed。
- 执行前审批与执行后验收审查必须使用不同状态和恢复阶段。
- 关键 Task、Review、Verification 和裁决事件必须先持久化再广播，并支持 cursor 补发和客户端去重。
- durable trace 不得包含原始模型内部推理、密钥、完整 cookie 或未授权私密记忆正文。

验收口径：

- 验证失败仍被接受、无效 token 被消费、重复 resume 再次产生副作用的数量均为 0。
- 关键事件可完整回放；SSE 重连无静默缺失；禁止字段泄漏数量为 0。

#### FR-NLE-004：Digital Life 长期演化最小闭环

- Digital Life 状态写入必须使用版本条件更新，禁止 silent overwrite。
- World Model 是现有事实源的只读投影；推断内容必须携带来源、置信度、观察时间、过期时间和冲突状态。
- Appraisal、Bond、Scene 和 Action Arbitration 必须复用现有情绪、关系、主动行为与 Governor 能力，不建立平行状态真源。
- 情绪或关系推断可以收紧表达和主动性，但不得覆盖用户显式任务或放宽安全门禁。

验收口径：

- 并发状态写入无静默覆盖；WorldProjection 不提供反写接口。
- 代表性互动可从事件回放至 Appraisal、affect/drive、表达策略；Bond 的边界、裂痕和修复语义保持一致。

#### FR-NLE-005：安全升级与 Operator 观测

- Capability maturity 只作为权限附加收紧条件；自动降级允许，自动升级禁止。
- 核心依赖故障必须进入可解释的 DegradationState，并在恢复时留下健康证据。
- 发布前必须生成可比较的 Identity Snapshot；重大人格差异需人工审查。
- Monitor UI 应在现有信息架构中展示 TaskGraph、Review、Trace、World/Scene、Capability、Health 和 Identity read model。

验收口径：

- 成熟度不会把原本需要审批的能力提升为无审批执行。
- 新观测页面不提供绕过 Review 的写入口，也不显示 raw thought 或未授权私密正文。

### FR-ARCH：V1 完整架构重构（M48～M57）

目标蓝图：`docs/50_project/arch/NATSUME_AI_V1_FULL_ARCHITECTURE_TARGET_20260710.md`。

正式执行入口：`docs/30_devspec/NATSUME_AI_V1_FULL_ARCHITECTURE_REFACTOR_MASTER_EXECUTION_20260710.md`。

本组需求定义 M48 安全地基之后的完整目标产品边界，不表示当前代码已经具备这些能力。目标是收敛现有巨石、循环依赖、重复真相源和并行运行时；禁止以长期双核、长期双写或新增另一套平台能力完成表面迁移。

#### FR-ARCH-001：模块分层与依赖治理（M49 建界，M57 终验）

- 产品应采用模块化单体优先的目标形态，并形成 Contracts/Kernel、Domain、Application、Interface/Adapter、Composition 的明确边界。
- 业务依赖只能由外向内；Composition 是装配具体实现的唯一例外，业务包不得反向导入 Composition、HTTP route、全局依赖容器或 provider 实现。
- `host_brain/app` 必须从隐式系统内核退化为明确的 interface/application/legacy facade；巨型混合职责文件按行为切片拆分，不得通过复制再造新巨石。

验收口径：

- 目标包之间双向依赖为 0；所有临时例外均有 ADR、owner 和删除版本。
- 新增超过 800 行的混合职责文件为 0；现有超过 1500 行的生产巨石已拆分或取得纯数据/生成代码例外批准。

#### FR-ARCH-002：跨运行面合同与 Command/Query/Event 分离（M48-B/M49 建界，M56 终验）

- Host、Edge、Gateway 与 Operator UI 必须共享独立、版本化的 identity、ID、error、command、query、event、risk 和 embodiment 合同，不共享 Host 实现包。
- 所有写请求进入 Governed Command Plane；Query Plane 只读版本化投影；关键状态变化与 Event/Outbox 同事务提交。
- 合同必须携带 schema/protocol version、principal、correlation/causation、idempotency、visibility 和 evidence refs，并提供兼容窗口与协商规则。

验收口径：

- Edge 对 `host_brain.*` 实现包导入为 0；重复 method/path handler 为 0。
- Runtime inline DDL 为 0；空库、旧库、备份恢复和回滚演练使用同一受控 migration catalog 通过。

#### FR-ARCH-003：统一 Host Runtime 与任务执行主链（M50）

- Secure Ingress、Conversation/Work Orchestration、Task Runtime、Agent Runtime、Governor、Lifecycle 和 Service Catalog 必须形成单一生产主链。
- 一个任务只能消费同一 TaskGraph revision；授权、预算、attempt、lease、verification、acceptance 和副作用 receipt 必须沿同一 lineage 可恢复、可回放。
- 工具、消息、主动行为及其他外部副作用必须经过 durable outbox；Obs 或 legacy route 不得形成第二条写主链。

验收口径：

- 同一 action 只有一个有效 AuthorizationGrant、Task/Attempt lineage 和副作用 receipt。
- 重启、重试、重复 resume 和新旧执行器切换不会重复执行副作用；旧任务由旧执行器 drain，不跨执行器恢复。

#### FR-ARCH-004：单核 Digital Life 与长期人格连续性（M51）

- LifeEventJournal 与版本化 reducers/projections 是生命状态唯一推进模型；LifeContinuity、LifeOrgans、Tribe、InnerWorld、Surface 和 Scheduler 不得各自推进平行生命时钟。
- 用户情绪观察与夏目自身 Affect 必须使用不同类型和 writer；Relationship、Homeostasis、World、Scene、Intent 和 Identity 均有唯一写模型。
- Surface Context 构造必须纯读；Tribe Agent 只能提交 `StateContribution`；主动行为和具身动作必须通过 durable outbox 并产生 receipt。
- 人格成长必须经过 proposal、shadow evaluation、版本激活和 rollback，不得直接改写不可变人格基线。

验收口径：

- 预览、重试和回放不会推进生命状态；并发写入无 silent overwrite。
- 同一事件可确定性重放出 Affect、Drive、Relationship、Scene 和 Expression 投影；重启前后裂痕、修复、边界与身份版本一致。

#### FR-ARCH-005：统一 Memory、Knowledge 与 Data Plane（M52）

- LifeMemoryService 是生活记忆唯一写服务；Episodic、Semantic、Distillation、Canon 与领域知识必须有明确 namespace、promotion 和 retention 边界。
- EventJournal、Transactional Outbox、Repository/UoW 和 versioned projection 构成统一 Data Plane；业务模块不得直接持有 Ledger 私有连接或运行时建表。
- Neo4j、Chroma、Tribe Registry、进程缓存和 Monitor cache 只能作为可重建 projection/cache，不能成为长期状态真相源。
- WorldCanon 只能通过 proposal/review/apply 聚合写入，并校验批准内容 hash；Pentest 等领域知识不得进入普通人格对话权威 Canon。

验收口径：

- 每个长期字段只有一个 writer，所有 projection 均能从 journal/权威 store 重建并对账。
- 普通聊天检索 Pentest 私有知识为 0；备份、恢复、压缩、遗忘和索引重建均有可重复证据。

#### FR-ARCH-006：Domain Pack 平台（M53）

- Code、Research、Media、Personal Knowledge、Desktop、Hardware、Pentest、BioOps 和 MCP 等领域必须通过统一 Pack SDK/Registry 接入。
- Pack 只提供 manifest、capability/tool、领域 repository port、knowledge namespace、verifier/acceptance、risk、benchmark 和 UI contribution。
- Pack 不得拥有第二套 TaskGraph、Scheduler、Governor、Review、Model Gateway、EventBus、Persona、Bond 或身份系统，也不得直接导入全局容器和其他领域私有 Store。

验收口径：

- 每个 Pack 可独立启停、版本化、授权、观测和回滚；停用单个 Pack 不破坏 Host 核心主链。
- 静态依赖扫描和运行时权限测试均证明 Pack 不能跨领域直接写核心状态。

#### FR-ARCH-007：统一 Model、Prompt、Capability 与 Governance Plane（M54）

- 模型调用必须收敛为 `ModelRequest → RouteDecision → ProviderAttempt → StructuredResult`，统一 timeout、retry、fallback、熔断、预算、usage 和 route trace。
- Prompt 模板必须版本化并声明 owner、input contract、slots 与 output schema；业务模块不得绕过 Model Plane 调用私有 provider helper。
- CapabilityCatalog、唯一 Tool/Capability Broker、Governor、Review Token 与签名 SkillBundle 必须共享统一风险及授权语义；maturity 只能收紧权限。

验收口径：

- 一次模型请求的所有 provider attempt、预算消耗、降级原因和结构化验证可串联回放。
- 未签名、无 SBOM、未隔离或未审批的 Skill/Provider bundle 不能在 Host 进程激活；自动提权数量为 0。

#### FR-ARCH-008：独立 Edge Device Runtime（M55）

- Edge 必须可独立安装和升级，只依赖版本化共享合同与 Edge 自有层；Host 不得反向依赖 Edge 私有实现。
- Gateway、Voice、Avatar、Desktop Pet、MateEngine、VTS/VRM 和硬件适配器必须通过统一设备身份、协议版本、capability handshake、inbox/outbox 和 receipt 接入。
- 物理输出只能由 Edge Device Runtime 持有最终 authority；所有本地 Bridge 默认 loopback 且要求 token/HMAC，远程暴露必须显式配置和审计。

验收口径：

- Edge 独立安装测试通过，Host 实现导入为 0；协议不兼容时 fail closed 并给出可解释降级状态。
- 同一具身命令不会被多个出口重复执行，断线重连后 receipt 可对账且不会重放已完成副作用。

#### FR-ARCH-009：只读 Query Plane 与 Operator Console（M56）

- Obs 必须成为只读 Query/Observability Plane；read model 必须携带 schema version、freshness、source cursor 和 degraded state。
- Operator Console 应提供 Operate、Understand、Replay/Lab 三个受治理产品面；任何写动作必须重新进入 Secure Ingress，UI 风险标签不能代替后端授权。
- UI client 应由版本化 OpenAPI/合同生成，并纳入 type、unit/component、a11y、E2E 与权限可见性验证。

验收口径：

- Query/UI 不直接查询业务私有表、进程全局缓存或未授权私密正文；Obs 绕过 Command Plane 的写入口为 0。
- raw thought、secret、cookie 和未授权私密记忆泄漏为 0；重放视图可按 cursor 解释完整因果链。

#### FR-ARCH-010：Evolution Lab、兼容退役与 Release 治理（M57）

- Evolution Lab 负责 benchmark、scenario、counterfactual、Skill Compiler、feedback proposal 和 Identity Migration；评测结果只能生成受审提案，不能直接改写生产策略、人格或能力权限。
- 完整迁移采用 Strangler + Contract-First + Shadow Compare；shadow 只比较结果，禁止同时执行真实副作用，兼容 facade 必须声明 owner、usage telemetry 和删除版本。
- ReleaseManifest 必须覆盖 commit、package、schema、protocol、model/prompt/avatar assets、SBOM、质量 Gate 和 rollback compatibility。

验收口径：

- compatibility facade 在 usage=0 且经过两个稳定发布窗口后按计划删除；无永久双核或永久双写。
- Golden Paths、replay、chaos、performance、security、backup/restore、identity regression 与 rollback drill 全部具有 Seal 证据后，M57 才可标记完成。

---

## 3. 非功能需求

- **NFR-PERF**：3-5 秒开口（在场回应 + 云超时退化链：Gemini → Codex relay → DeepSeek → host_14b）。
- **NFR-RELIABILITY**：依赖故障不崩溃，输出结构稳定，关键链路可观测。
- **NFR-AUDIT**：≥99% 请求可串联追溯（request_id → intent → governor → skill_exec → result）。
- **NFR-SAFETY**：写入、外显执行、Pentest side-effect 必须有明确 scope、approval、审计与可解释拒绝原因。

---

## 4. 外部接口

| 接口 | 端点 | 说明 |
|------|------|------|
| Chat | `POST /v1/chat` | 主对话入口（SSE） |
| Host Input | `POST /v1/host/input` | Gateway/ASR ingress，不是 `/v1/chat` 简单转发 |
| Edge Events | `POST /v1/edge/events` | Edge 运行时事件上报 |
| Edge Visual Locate | `POST /v1/edge/visual_locate` | 当前返回 `VLM_REMOVED`，不可作为可用自动化能力 |
| Gateway Webhook | `POST /v1/gateway/{platform}/webhook` | 平台消息入站 |
| Gateway Reply | `POST /v1/gateway/reply` | Host → Gateway 出站 |
| Desktop Pet | `GET/POST :18788` | 桌面宠物控制 |
| MateEngine | UDP :32145 | 姿态/表情/动作控制 |
| Agent Run | `POST /v1/agent/run` | Agent 执行入口，底层受 AgentWorkOrder 约束 |
| Pentest Capabilities | `GET /v1/pentest/capabilities` | Pentest 工具与阶段元信息 |
| Pentest Environment | `GET /v1/pentest/environment` | Lab mode、allow_exploit、Kali/OOB 环境状态 |
| Pentest Runs | `GET/POST /v1/pentest/runs` | 创建与列出安全实验室 run |
| Pentest Run Detail | `GET /v1/pentest/runs/{run_id}` | 单次 run 详情 |
| Pentest Events | `GET /v1/pentest/runs/{run_id}/events` | run 事件流 |
| Pentest Cancel | `POST /v1/pentest/runs/{run_id}/cancel` | 取消 run |
| Pentest Approve Side Effect | `POST /v1/pentest/runs/{run_id}/approve-side-effect` | 记录 Phase4/5 approval token |
| Pentest Continue Side Effect | `POST /v1/pentest/runs/{run_id}/continue-side-effect` | 在 approval 后继续受控 side-effect continuation |
| Pentest Report | `GET /v1/pentest/runs/{run_id}/report` | 读取 markdown/json 报告 |
| Obs Health | `GET /obs/services/health` | 全服务健康聚合 |
| Obs Sessions | `GET /obs/sessions` | 会话列表 |
| Obs Trace | `GET /obs/trace/{request_id}` | 链路节点详情 |
| Obs Metrics | `GET /obs/metrics/timeseries` | 指标时序 |

---

## 5. 验收边界

- Host Main 入口以 `host_brain.app.asgi:app` 为准，`app_factory` 与 `router_registry` 是装配事实。
- `/v1/chat`、`/v1/host/input`、`/v1/edge/events` 必须保持职责分离。
- Agent 写模式必须走 `AgentWorkOrder` + `WorkspacePolicy` + `approval_policy`。
- Monitor UI 导航以 `routeManifest.jsx` 为唯一来源，7 组导航名不得在其他文档中另造口径。
- Pentest Phase4/5 不得被描述为无条件自动执行；必须写明 allow_exploit、approval token、scope/rate/manifest guard。
- `/v1/edge/visual_locate` 当前不可用，只能按 `VLM_REMOVED` 降级处理。

---

## 6. 当前架构事实入口

以下文档为当前代码的权威参考（均标注 `[LIVING]`）：

- `docs/50_project/arch/CURRENT_CODE_ARCHITECTURE.md` — 模块全景
- `docs/50_project/arch/dataflow_report.md` — 数据流报告
- `docs/50_project/arch/CORE_MODULES_DETAILED_REFERENCE.md` — 核心模块详细参考
- `docs/15_theory/APP_AUDIT_SKILLS_CONTRACT.md` — 应用/审计/技能层契约
- `docs/30_devspec/DevSpec.md` — 工程契约参考
