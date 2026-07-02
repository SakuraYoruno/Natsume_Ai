受众：CODEX
标题：开发规格 DevSpec（当前代码工程契约参考）

# Natsume Ai 开发规格（DevSpec）
版本：v2.3
日期：2026-06-30
定位：当前代码的工程契约参考手册。每条契约应能追溯到具体文件、路由或运行时边界。

---

## 1. 架构总览

5 个协同运行面：

| 运行面 | 入口 | 端口 | 技术栈 |
|--------|------|------|--------|
| Host Main | `host_brain.app.asgi:app` | 8000 | FastAPI + LangGraph |
| Obs Backend | `host_brain/obs_main.py` | 18082 | FastAPI |
| Edge RPC | `edge_client/__main__.py` | 17800 (可配置) | Python RPC |
| Unified Message Gateway | `edge_client/gateway/server.py` | 19000 | FastAPI |
| Desktop Pet | `desktop_pet/main.cjs` | 18788 | Electron |

Host Main 入口以 `host_brain.app.asgi:app` 为准；`host_brain/__main__.py` 只是启动 uvicorn 的运行入口之一，不是 FastAPI app 的装配源。FastAPI app 由 `app_factory` 创建，由 `router_registry` 注册主路由。

外显边界：Edge、DesktopPet、MateEngine、VTS、VRM 负责执行与呈现；事实、长期记忆、人格/回复政策、安全门控与最终表达收口均在 Host。

---

## 2. Host Main 主链契约

### 2.1 FastAPI 装配

- `host_brain/app/asgi.py` — ASGI 应用入口，暴露 `app`。
- `host_brain/app/app_factory.py` — `create_host_fastapi_app()`。
- `host_brain/app/router_registry.py` — `register_host_routers()`，注册 canon、scheduler、tools、agent、input、digital life、memory、mateengine、pentest、health 等路由。

### 2.2 Chat 主链（`POST /v1/chat`，SSE）

```
host_brain.app.asgi:app
  → app_factory.create_host_fastapi_app()
  → router_registry.register_host_routers()
  → chat_routes.handle_v1_chat_route()
    → chat_immune_gate (HTTP 层免疫门控，支持 fastpath/BLOCK)
    → chat_handler.handle_chat()
      → thalamus_agent.classify_and_compile_intent_draft()
      → Governor.evaluate_intent()
      → cortex_orchestrator_sse_generator()
      → agent_runtime_shared
      → executive_router / executive_runtime / executive_runtime_core
        → surface_reply_runtime
          → surface_main_chain_adapter
            → SurfaceMainChain (normal chat 最终文本作者)
          → ReplyEnvelope (发送/持久化/缓存收口)
      → surface_reply_postprocessor
```

关键文件：
- `host_brain/app/chat_routes.py` — `/v1/chat` 路由层。
- `host_brain/app/chat_immune_gate.py` — HTTP 层免疫门控。
- `host_brain/app/chat_handler.py` — 主链薄编排入口。
- `host_brain/tribe/thalamus_agent.py` — 意图编译。
- `host_brain/governor/decision.py` — Governor 裁决入口。
- `host_brain/app/cortex_orchestrator.py` — SSE 与 Cortex 编排。
- `host_brain/app/agent_runtime_shared.py` — 共享运行时。
- `host_brain/app/executive_router.py` — 执行路由。
- `host_brain/app/executive_runtime.py` — 执行运行时。
- `host_brain/app/executive_runtime_core.py` — 执行核心。
- `host_brain/app/surface_reply_runtime.py` — 人格渲染路由者。
- `host_brain/persona/surface_main_chain.py` — SurfaceMainChain。
- `host_brain/persona/reply_envelope.py` — 最终输出收口。

### 2.3 Input 回流链（`POST /v1/host/input`）

`/v1/host/input` 是 Gateway/ASR ingress，不是 `/v1/chat` 的简单转发。它负责接收多平台消息与语音输入的归一化回流，并在进入后续主链前做 ACK、质量分析、偏航检测、进度推送与重对齐通知。

```
input_routes.py
  → ACK / quality / drift check / progress notification
  → thalamus_agent
  → cortex_orchestrator / executive 后续链路
  → edge_rpc_client 回复 Gateway 或外显执行面
```

关键文件：
- `host_brain/app/input_routes.py` — Host 输入回流入口。
- `edge_client/gateway/server.py` — Gateway 入站。
- `host_brain/channels/edge_rpc_client.py` — Host → Edge/Gateway 推送。

### 2.4 Edge 事件回流（`POST /v1/edge/events`）

Edge 运行时事件（播放状态、资源门控、inline waiters 等）走 `/v1/edge/events`，不走 `/v1/host/input`。事件回流不得被当成用户输入，也不得触发普通 chat 语义链。

---

## 3. Agent Runtime 契约

### 3.1 AgentWorkOrder

Agent Runtime 的核心契约是 `host_brain/agent_runtime/work_order.py::AgentWorkOrder`。

关键字段：
- `domain` — Agent 领域，例如 code、finance、translation、creative、skill、immune、evolution、organ_ops、embodiment、memory、bio_ops、research。
- `run_mode` — 运行模式，例如 inspect、worker、self_heal、self_evolve、research、translate、audit。
- `objective` — 本次任务目标。
- `workspace` — 工作目录解析策略，支持 runtime repo、显式 path、git_url、temp_workspace 等。
- `allowed_actions` — 显式允许动作。
- `forbidden_actions` — 显式禁止动作。
- `approval_policy` — 确认/授权策略。

### 3.2 写模式边界

写模式由 `AgentWorkOrder.requires_write` 与 `WorkspacePolicy.enforce_write_policy()` 共同约束：

- `run_mode in {"worker", "self_heal", "self_evolve"}` 或 `allowed_actions` 包含 `write_files` 时视为写模式。
- 写模式必须显式包含 `allowed_actions=["write_files", ...]`。
- 写模式必须在 `forbidden_actions` 中包含 `modify_secret_files`。
- 当 `approval_policy.require_confirm=true` 时，必须同时满足 `approval_policy.confirmed=true`。
- 环境变量 `HOST_CODEX_FORBID_MAIN_BRANCH_DIRECT_WRITE` 默认开启；`self_evolve` 不得直接在 main/master 分支执行写入。

关键文件：
- `host_brain/agent_runtime/work_order.py`
- `host_brain/agent_runtime/workspace_policy.py`
- `host_brain/agent_runtime/executor.py`
- `host_brain/app/internal_agent_routes.py`

---

## 4. Surface 主链契约

`host_brain/persona/` 目录（55+ 模块），核心模块：

| 文件 | 职责 |
|------|------|
| `surface_main_chain.py` | SurfaceMainChain，normal chat 文本唯一作者 |
| `surface_main_chain_adapter.py` | 主链适配器，连接 surface_reply_runtime 与 SurfaceMainChain |
| `surface_memory_recall.py` | 记忆召回注入（RECALL_POLICY_MUST/MAY/NO） |
| `surface_context_packet.py` | 上下文包组装 |
| `surface_reply_preprocessor.py` | 输入预处理与 payload 构建 |
| `reply_envelope.py` | 最终输出收口（sendable/persistable/cacheable） |
| `reply_modality_router.py` | 媒体输出路由 |
| `inner_monologue.py` | 内心独白 |
| `motive_engine.py` | 动机引擎 |
| `text_quality_gate.py` | 文本质量门控 |
| `galgame_taste_guard.py` | Galgame 风格守护 |
| `isolated_surface_harness.py` | 隔离测试 harness |

Surface 禁令：
- 不得新增事实、不得升级承诺强度、不得升级证据强度、不得引入新 intent。
- Canon、风格材料、情绪标签、外显动作提示不得当成已发生事实。

---

## 5. 记忆与召回契约

### 5.1 LifeMemoryStore（SSOT）

- `host_brain/memory/life_memory.py` — 生活记忆 SSOT，SQLite 持久化。
- 负责 episodic 记忆的写入、衰减、合并、删除。

### 5.2 召回融合

- `host_brain/retrieval/recall_fusion.py` — `RecallFusionServiceContract`。
- `host_brain/retrieval/recall_fusion_stores.py` — 多路 store 搜索。
- `host_brain/retrieval/hybrid_fusion.py` — vector + FTS5 混合融合。
- `host_brain/retrieval/full_text_searcher.py` — FTS5 全文检索。
- `host_brain/retrieval/recall_schemas.py` — `RecallFusionResult`。
- `host_brain/retrieval/markdown_kb_store.py` — Markdown 知识库召回。

### 5.3 记忆蒸馏

- `host_brain/memory/long_term_distiller.py` — Episodic → 语义记忆蒸馏。
- `host_brain/memory/semantic_memory.py` — 语义记忆存储。

---

## 6. Tribe 系统契约

23 个 family spec，均 A 级升级完毕。

- `host_brain/tribe/registry.py` — family 注册。
- `host_brain/tribe/thalamus_agent.py` — 意图编译入口。
- `host_brain/tribe/immune_agent.py` — 风险门控。
- `host_brain/tribe/limbic_agent.py` — 动机态与情绪路由。
- `host_brain/tribe/proprioception_agent.py` — 认知负荷。
- `host_brain/tribe/prefrontal_agent.py` — 漂移告警。

---

## 7. Gateway 契约

6 平台适配器：wechat、qq、feishu、wecom、telegram、line。

全链路：
```
平台 Webhook
  → gateway/server.py
  → adapter.parse_inbound()
  → MessageNormalizer.normalize()
  → GatewayRouter.route()
  → EdgeInputReporter._send()
  → POST /v1/host/input
  → input_routes 后续主链
  → edge_rpc_client.post_gateway_reply()
  → POST /v1/gateway/reply
  → OutboundDispatcher.dispatch()
  → platform adapter.send_reply()
```

关键文件：
- `edge_client/gateway/server.py` — Gateway 服务入口。
- `edge_client/gateway/adapter/` — 各平台适配器。
- `edge_client/gateway/router.py` — 消息路由。
- `host_brain/channels/edge_rpc_client.py` — Host → Edge/Gateway 推送。

---

## 8. 外显执行面契约

外显执行面只执行 Host 下发的已裁决动作，不拥有事实、长期记忆、人格/回复政策或安全门控。

### 8.1 Edge RPC

- `edge_client/__main__.py` — Edge 运行入口。
- `edge_client/app/fastapi_app.py` — Edge RPC FastAPI app。
- `/v1/edge/events` — Edge runtime events。
- `/v1/edge/visual_locate` 当前为 stub/removed；`_handle_visual_locate()` 返回 `reason_code="VLM_REMOVED"`，不得依赖它做可用视觉定位自动化。

### 8.2 Desktop Pet

- `desktop_pet/main.cjs` — Electron 运行时。
- HTTP 控制端口：127.0.0.1:18788。
- Actions：show、hide、preset、touch、emote、speak、click_through、status。
- Host 侧调用：`host_brain/channels/edge_rpc_client.py` → `post_desktop_pet()`。

### 8.3 MateEngine

- `host_brain/channels/mateengine.py` — UDP 桥接，端口 32145。
- 13 种 v0 事件、11 种 pose preset。
- 状态→姿势映射、状态→情绪映射、touch→情绪映射。
- action_sequence / choreography 编排支持。

### 8.4 VTS / VRM

- `host_brain/channels/vts.py` — emotion_tag → expression_id 确定性映射（16 种表情）。
- `host_brain/channels/vts_mapping_store.py` — 映射持久化与版本化。
- `host_brain/channels/vts_rate_limiter.py` — 令牌桶频率限制。
- VRM 作为外显呈现/执行面时遵循同一边界：由 Host 下发已裁决状态，不反写事实与长期记忆。

---

## 9. 可观测性与 Monitor UI 契约

- Obs Backend：`host_brain/obs_main.py`，独立进程，端口 18082。
- FastAPI app：`host_brain/app/obs_fastapi_app.py`。
- Monitor UI：`host_brain/app/monitor_ui/` — 多页面 SPA。
- Monitor 路由与导航 SSOT：`host_brain/app/monitor_ui/src/app/routeManifest.jsx`。
- 当前导航组固定为 7 组：Cockpit、Life Systems、Observability、Operations、Execution、Security Lab、Integrations。
- `NAV_SECTIONS` 从 `ROUTE_NAV_GROUPS` 与 `routeManifest` 派生；新增页面必须先进入 routeManifest，不得另造导航清单。
- 预热缓存：runtime snapshot、trace feed、insights summary、digital life overview、life body。

主要 API：
- `GET /obs/services/health` — 全服务健康聚合。
- `GET /obs/sessions` — 会话列表（分页+过滤）。
- `GET /obs/trace/{request_id}` — 链路节点详情。
- `GET /obs/metrics/timeseries` — 指标时序。

---

## 10. 安全、免疫与 Security Lab 契约

### 10.1 Immune Gate

- `host_brain/app/chat_immune_gate.py` — HTTP 层免疫门控。

### 10.2 Governor

- `host_brain/governor/decision.py` — `IntentDecisionEngine`。
- `host_brain/governor/rules.py` — `evaluate_governor_gates` (R1-R8)。
- `host_brain/governor/tokens.py` — `TokenService`。

### 10.3 Airlock

- `host_brain/airlock/quarantine.py` — 入站隔离分类。
- `host_brain/airlock/sanitizer.py` — 出站净化。
- `host_brain/airlock/sandbox.py` — 沙箱执行。
- `host_brain/airlock/pii_filter.py` — PII 过滤。

### 10.4 Pentest Security Lab

- `host_brain/app/pentest_routes.py` — `APIRouter(prefix="/v1/pentest")`。
- `GET /v1/pentest/capabilities` — 工具、阶段、readonly/side-effect 元信息。
- `GET /v1/pentest/environment` — lab mode、allow_exploit、OOB、Kali/tool availability、side-effect policy。
- `POST /v1/pentest/runs` — 创建 run。
- `GET /v1/pentest/runs` — 列出 run。
- `GET /v1/pentest/runs/{run_id}` — run detail。
- `GET /v1/pentest/runs/{run_id}/events` — SSE events。
- `POST /v1/pentest/runs/{run_id}/cancel` — 取消 run。
- `POST /v1/pentest/runs/{run_id}/approve-side-effect` — 记录 Phase4/5 approval token。
- `POST /v1/pentest/runs/{run_id}/continue-side-effect` — 在 approval 后继续受控 continuation。
- `GET /v1/pentest/runs/{run_id}/report` — markdown/json 报告。

Pentest side-effect 规则：
- Phase1-3 与 report 默认按 readonly/fixture 工作流处理。
- Phase4/5 只有在 Lab Mode、`allow_exploit`、scope guard、rate guard、engagement manifest guard 与 Governor/approval token 同时满足时才可继续。
- `approve-side-effect` 只记录 approval token；`continue-side-effect` 才写 continuation artifact。
- 当前 Web continuation 默认记录受控计划与审计 artifact，`simulated=true`、`executed=false`，不得写成默认发起真实 exploit 命令。

### 10.5 审计

- `host_brain/audit/sqlite_ledger.py` — SQLite Ledger SSOT。
- 三联画：intent_log / governor_log / skill_exec_log。
- Pentest Lab 将 `manifest_ref`、`approval_token_ref`、artifact refs 写入 skill_exec 审计。

### 10.6 Pentest Agent 内部契约

**CognitiveEngine**（`pentest/agent/cognitive_engine.py`，834 行）：
- 输入：态势分析结果 + 信念模型快照 + 目标模型当前状态
- 输出：加权假设列表（Hypothesis），每条含 action + confidence + rationale
- 接口：`generate_hypotheses(situation, beliefs, goals) -> list[Hypothesis]`
- 约束：不可绕过 scope/rate/approval guard；不可在 readonly 模式下产生 side-effect 假设

**ToolRegistry**（`pentest/agent/tool_registry.py`，796 行）：
- 意图→工具映射：30+ Capability × EnvTag → ToolCapability → ToolSpec
- 接口：`resolve(intent: Capability, env: EnvTag) -> list[ToolCapability]`
- 约束：side-effect 工具只在 `allow_exploit=true` 时返回

**EvolutionCore**（`pentest/agent/evolution_core.py`，693 行）：
- 循环：observe(结果) → reflect(反思) → adapt(适应) → evolve(演化)
- 组合：KnowledgeBase + ExperienceRetriever + CampaignMemory + FailureClassifier
- 约束：演化结果持久化到 WorldCanon；不可覆盖 immutable baseline

**规划器选择**（`pentest/agent/compat.py`，77 行）：
- `PENTEST_COGNITIVE_KERNEL=1` → CognitiveEngine
- `PENTEST_COGNITIVE_KERNEL=0` → MCTSPlanner 或 LLMReActPlanner
- LegacyKernelAdapter 负责透明切换

### 10.7 Persona Surface Scoring 契约

**评分管线**（`persona/surface/`，17 文件）：
```text
CandidateScoring.score_candidates()
  → parse: 候选解析 + 问题预算计数（scoring/parse.py, 262 行）
  → stage_positions: 场景位置评分 5 种位置（scoring/stage_positions.py, 406 行）
  → control_affect: 情感控制评分 双轴（scoring/control_affect.py, 398 行）
  → texture: 纹理评分（scoring/texture.py, 273 行）
```

**Inner-state leakage**：
- `ReplyControlPacket`（`surface/data_models.py`）：7 个泄漏字段 — inner_weather/body_state_hint/care_signal/continuity_signal/tension_hint/liveliness/active_loops
- 约束：泄漏不可暴露原始私密思维；仅影响 LLM prompt 的 tone 和风格

### 10.8 Digital Life Proactive 回路契约

**自主循环**（`runtime/autonomous_self.py`，1,009 行）：
- 8 维 SELF_REWARD_WEIGHTS：continuity/coherence/competence/curiosity/restraint/presence/aesthetic/attachment
- 7 维 ADAPTIVE_TRAITS：curiosity/assertiveness/playfulness/sensitivity/resilience/restraint/warmth
- 接口：`apply_autonomous_self_cycle(life_state) -> AdaptiveSelfPack`

**Proactive Voice**（`persona/proactive_voice.py`，1,477 行）：
- 6 种场景：LATE_NIGHT/LONG_ABSENT/POST_INTENSE/TOPIC_FOLLOWUP/MORNING_GREET/HOLIDAY_GREET
- 24h per-type 冷却门控
- 接口：`evaluate(inner_world_snapshot) -> ProactiveExpression | None`

**端点**：
- `POST /v1/digital-life/proactive/attempt` — 手动触发（dry_run/forced）
- `GET /v1/digital-life/proactive/recent` — 近期 outbox
- `GET /v1/digital-life/proactive/episodes/recent` — 近期 episodes
- `GET /obs/proactive-autonomy` — proactive autonomy 快照

---

## 11. 路由表

| 方法 | 路径 | 文件 | 说明 |
|------|------|------|------|
| POST | `/v1/chat` | `chat_routes.py` | 主对话入口（SSE） |
| POST | `/v1/host/input` | `input_routes.py` | Gateway/ASR ingress，不是 chat 转发 |
| POST | `/v1/edge/events` | `edge_routes.py` | Edge 事件上报 |
| POST | `/v1/edge/visual_locate` | `edge_client/app/fastapi_app.py` | 当前返回 `VLM_REMOVED` |
| POST | `/v1/agent/run` | `agent_routes.py` | Agent 执行入口 |
| POST | `/v1/mateengine/signal` | `mateengine_routes.py` | MateEngine 信号 |
| POST | `/v1/mateengine/say` | `mateengine_routes.py` | MateEngine 语音 |
| GET | `/v1/pentest/capabilities` | `pentest_routes.py` | Pentest 能力元信息 |
| GET | `/v1/pentest/environment` | `pentest_routes.py` | Pentest Lab 环境状态 |
| GET/POST | `/v1/pentest/runs` | `pentest_routes.py` | 列出/创建 Pentest run |
| GET | `/v1/pentest/runs/{run_id}` | `pentest_routes.py` | Pentest run detail |
| GET | `/v1/pentest/runs/{run_id}/events` | `pentest_routes.py` | Pentest run SSE events |
| POST | `/v1/pentest/runs/{run_id}/cancel` | `pentest_routes.py` | 取消 Pentest run |
| POST | `/v1/pentest/runs/{run_id}/approve-side-effect` | `pentest_routes.py` | 记录 side-effect approval |
| POST | `/v1/pentest/runs/{run_id}/continue-side-effect` | `pentest_routes.py` | 继续受控 side-effect continuation |
| GET | `/v1/pentest/runs/{run_id}/report` | `pentest_routes.py` | Pentest report |
| GET | `/health` | `health_router` | 健康检查 |
| GET | `/obs/services/health` | `obs_fastapi_app.py` | 全服务健康 |
| GET | `/obs/sessions` | `obs_fastapi_app.py` | 会话列表 |
| GET | `/obs/trace/{request_id}` | `obs_fastapi_app.py` | 链路详情 |
| GET | `/obs/metrics/timeseries` | `obs_fastapi_app.py` | 指标时序 |

---

## 12. 数据格式契约

### 12.1 SSE 三阶段协议

- PRESENCE（在场流）：非权威，只给前端，不作为下游节点输入。
- COMMIT（提交流）：权威，仅 `GOV_OK_RENDER` 时产生。
- POST（渲染流）：读 Commit 快照，PhaseLockGuard 验证。

实现：`host_brain/integrations/graph_streaming.py`。

### 12.2 GraphState 关键字段分组

- 基础执行：`request_id / graph_id / timeout_ms / payload / trace`。
- 路由：`pending_node_id / edge_map / committed_route_token`。
- 模型：`model_nodes / model_profile_id / model_calls`。
- 情感：`emotion_context / emotion_tag / mood_state`。
- Clock：`policy_start_ms / policy_timeout_ms / clock_events`。
- 并行分支：`branch_specs / branch_manifest`。
- Streaming：`presence_events / commit_snapshot / render_snapshot_ref`。
- Checkpoint：`gov_checkpoint_id / interrupt_reason / resume_allowed`。
- Multi-Agent：`active_agent_id / handoff_history / agent_budget / agent_artifacts`。

---

## 13. 验收检查清单

- Host Main 文档入口必须写 `host_brain.app.asgi:app`，装配必须写 `app_factory` + `router_registry`。
- `/v1/chat` 主链必须收束为 `chat_routes/chat_handler → thalamus → Governor → Cortex/Executive → SurfaceMainChain → ReplyEnvelope`。
- `/v1/host/input` 必须写成 Gateway/ASR ingress；`/v1/edge/events` 必须写成 Edge events。
- Agent 写模式必须写明 `AgentWorkOrder`、`WorkspacePolicy`、`approval_policy` 三层限制。
- Monitor UI 导航必须以 `routeManifest.jsx` 为 SSOT，并保持 7 组导航。
- Pentest Phase4/5 必须写明 allow_exploit、Governor/approval token、scope/rate/manifest guard，不能写成无条件执行。
- `/v1/edge/visual_locate` 必须写明当前返回 `VLM_REMOVED`。

---

## 14. 当前代码事实入口

- `docs/50_project/arch/CURRENT_CODE_ARCHITECTURE.md`（v4.2）— 模块全景。
- `docs/50_project/arch/dataflow_report.md`（v4.0）— 数据流报告。
- `docs/50_project/arch/CORE_MODULES_DETAILED_REFERENCE.md`（v4.2）— 模块详细参考。
- `docs/50_project/arch/PENTEST_MODULES_ARCHITECTURE.md`（v1.1）— Pentest 专项架构。
- `host_brain/pentest/CAPABILITY.md`（v2.3）— Pentest 能力边界。
