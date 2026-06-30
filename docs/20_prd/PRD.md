受众：MASTER
标题：产品需求文档（PRD）

# Natsume Ai 产品需求文档（PRD）
版本：v2.3
日期：2026-06-30
定位：基于当前代码事实的系统能力描述，记录系统实际提供的入口、边界、执行契约与验收口径。

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
