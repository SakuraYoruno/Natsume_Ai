受众：CODEX
标题：开发规格 DevSpec（当前代码工程契约参考）

# Natsume Ai 开发规格（DevSpec）
版本：v2.5
日期：2026-07-10
定位：当前代码的工程契约参考手册。每条契约应能追溯到具体文件、路由或运行时边界。

状态说明：§1～§14 主要记录当前代码事实；§15 是 M48 `READY_FOR_EXECUTION` 契约；§16 是 M48～M57 `ADOPTED_TARGET_ARCHITECTURE` 完整目标合同。§15、§16 均不得在对应 TASK、测试与 Seal 证据成立前被观测或文档引用为当前运行能力。

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

TASK-0488 后的 HTTP Webhook 主链：
```
平台原始 HTTP Webhook
  → gateway/server.py
  → webhook_security.py（保留 exact body/header/query，先验签/认证解密）
  → adapter.parse_inbound()
  → MessageNormalizer.normalize()
  → GatewayDeliveryRuntime.accept_inbound()
  → 0059 gateway_transport_inbox（commit 后才返回 accepted）
  → fenced worker / dispatch_started
  → EdgeInputReporter.deliver_gateway_payload()
  → POST /v1/host/input
  → TASK-0487 Host Secure Ingress + durable claim
  → input_routes 后续主链
  → edge_rpc_client.post_gateway_reply()
  → signed POST /v1/gateway/reply
  → 0059 gateway_transport_outbox
  → OutboundDispatcher.prepare_payload()（零副作用冻结/选 adapter）
  → fenced worker / dispatch_started
  → OutboundDispatcher.dispatch_prepared()（至多调用一次）
  → platform adapter.send_reply()
  → adapter-confirmation ACK；不确定副作用入 DLQ 且禁止自动重发
```

`EDGE_GATEWAY_TRANSPORT_MODE` 默认 `LEGACY_DRAIN`：拒绝新 HTTP intake，
只排空旧兼容重试队列；`SHADOW` 只执行 raw-body 认证验证，不持久化、
不返回 durable ACK，也不启动副作用 worker；只有 `DURABLE` 执行持久化和
Host/平台投递。非法 mode 回退 `LEGACY_DRAIN`。
`EDGE_GATEWAY_ACCEPT_NEW_INTAKE=false` 可同时停止 HTTP、DB watcher 与 Telegram
新输入，DURABLE worker 继续排空；仅在 `pending=0`、无 claimed 且
`reconciliation_required=false` 后切换 `LEGACY_DRAIN`。凭据、verifier 与 0059
schema 在任何 producer/worker 前完成 preflight；HTTP bind/readiness 或 Telegram
连接失败会逆序停止 server、producer、平台连接与 worker，禁止 orphan runtime。
Inbox/outbox 以业务幂等键、tenant/principal、payload digest、ordering key、
lease/fencing 和 attempt ledger 约束重复、乱序与重启恢复；DLQ replay 必须携带
operator ref，原记录原位恢复，不能创建第二身份。

平台原始 body 仅在请求内存中用于认证；0059 只保存 SHA-256 与字节数，不保存
明文原始 body。LINE 使用 exact raw-body HMAC；飞书和企微在运行装配中默认完成认证解密；
QQ、WeChat HTTP bridge、Telegram 以及 Host reply 使用 Natsume internal raw-body
HMAC。Host reply 使用独立密钥，禁止回退通用 Edge RPC key。DURABLE 下 DB watcher /
MTProto 等非 HTTP 入口与 HTTP 共用 0059 inbox，不再写 legacy reporter cache；
`LEGACY_DRAIN` 仅排空已存在的兼容队列。共享 channel SDK 与 composition port
由 `TASK-0592` 收口；Edge 对 Host Store/签名实现的临时导入由 `TASK-0493`、
`TASK-0584` 移除，禁止复制 Store 形成第二真相源。

当前 adapter 的布尔成功只能证明本地 `ADAPTER_CONFIRMED`，不能冒充远端平台
message ID/receipt；provider receipt 对账由 `TASK-0592` 移除。Gateway proof 的
独立 durable replay/rate claim 同样由 `TASK-0592` 补齐；Host response receipt 的独立
签名与 `/v1/host/input` raw middleware 前置审计分别由 `TASK-0592`、`TASK-0512`
收口，以上限制不得在 TASK-0488 证据中写成已完成能力。

关键文件：
- `edge_client/gateway/server.py` — Gateway 服务入口。
- `edge_client/gateway/webhook_security.py` — 平台 raw-body 验签与认证解密。
- `edge_client/gateway/delivery_runtime.py` — durable worker 和切流 mode。
- `host_brain/eventing/gateway_transport.py` — 0059 inbox/outbox/attempt 唯一 Store。
- `edge_client/gateway/adapters/` — 各平台适配器。
- `edge_client/gateway/router.py` — 非 HTTP 兼容消息路由。
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

---

## 15. M48 长期演化安全收敛契约

正式任务入口：`docs/30_devspec/NATSUME_AI_V1_LONG_TERM_EVOLUTION_EXECUTION_20260710.md`。本节冻结工程契约；具体顺序、文件边界、停止条件和回滚由该执行文档定义。

### 15.1 领域写边界

- Domain 只能写领域自有 Store；Persona、Bond、LifeMemory 和 WorldCanon 的 direct write 必须由静态扫描阻断。
- WorldCanon 唯一写入口是受治理 Apply Service；候选 ID 不提供授权语义。
- Pentest 历史迁移采用 backfill/shadow/final-delta/domain-only，不允许持续 Canon 双写。
- M48 新增 SQLite schema 使用现有 central audit migration catalog；独立组件 catalog 必须先具备 scoped migration metadata，不能复用无 scope 的 migration ID。

### 15.2 Canonical Task Runtime

- WorkOrder v3 在 v2 上增加 root/parent/task lineage、graph revision、attempt、业务幂等 scope、status version 和 review refs。
- Planner 必须只产生并消费同一 `graph_id/revision`；循环依赖进入 `PLAN_CYCLE`，不得忽略依赖执行。
- Child WorkOrder 是 SubAgentRequest 的唯一任务来源 adapter，必须保留 root/parent/graph/node lineage、授权和 Context refs。
- TaskStore 必须持久化 Graph、Node、Attempt、Lease、Heartbeat、IdempotencyReservation、BudgetReservation 和 transactional outbox。
- BudgetReservation 覆盖 token/tool/time/concurrency/retry/model cost，预留与消费可对账；超限只能阻断、降级或进入 Review。
- 状态变更必须使用 expected-version CAS；进程重启后通过 lease/attempt ownership 恢复。
- scheduler shadow 阶段只比较调度决策，禁止同时执行真实副作用。

### 15.3 执行前审批、执行后验收

执行前状态：

```text
READY → WAITING_APPROVAL → READY | CANCELLED
```

执行后状态：

```text
EXECUTED → VERIFYING → VERIFIED | VERIFICATION_FAILED
VERIFIED → ACCEPTED | WAITING_ACCEPTANCE_REVIEW
WAITING_ACCEPTANCE_REVIEW → ACCEPTED | FAILED | RETRY_PENDING | CANCELLED
```

- `VerificationResult.status` 只允许 `PASS|FAIL|ERROR|NOT_RUN`；required verification 仅 `PASS` 可继续。
- Acceptance 必须逐项关联 criterion、evidence 和 verdict；确定性失败不能被 LLM 覆盖。
- Review token 绑定 subject、version、actor、expiry 和一次性消费；POST review resume 不得重放已执行副作用。

### 15.4 Runtime Event 与 Context

- 关键状态变化与 event outbox 必须同事务提交；SSE 使用 cursor、至少一次投递和客户端去重。
- durable payload 只允许 decision summary、input/output refs、evidence refs 和脱敏元数据；禁止 raw thought、secret、cookie 和未授权私密正文。
- Context 沿用 `AgentContextEnvelope`，增加 tokenizer 预算、source refs、授权 profile、实际 token、截断原因和污染检测，不新建平行 Context Pack。

### 15.5 Digital Life 演化层

- `digital_life_state` 使用 expected-version OCC；schema 迁出运行时代码并进入受控 migration catalog。
- WorldProjection 是只读投影；事实携带 source/confidence/observed/expires/conflict/epistemic type。
- AppraisalRecord 连接 event、affect/drive、episode/expression；不替代现有 VAD/inner weather。
- BondDynamics 保持单写者；score/stage 只作为兼容 projection，rift/repair/boundary 语义不得丢失。
- SceneDecision 只影响表达和主动性；ActionArbiter 只能收紧 Governor，不能覆盖用户显式目标或提权。

### 15.6 Capability、守护与身份

- Capability Scorecard 联邦聚合现有 registries；自动仅可降级，升级必须有评测证据和人工 Review。
- DegradationState 复用 startup/health/heartbeat/resource manager，记录进入、恢复、证据和受影响能力。
- Identity Snapshot 复用 SelfModel、漂移检测和 release manifest；V1 只允许 snapshot/diff/review，不自动迁移人格。

### 15.7 M48 测试契约

| 测试段 | 覆盖范围 |
|---|---|
| T-NLE-01～03 | 基线/边界、Pentest 迁移、Canon Apply 单入口 |
| T-NLE-04～07 | v2/v3 契约、单 revision 规划、TaskStore、shadow scheduler |
| T-NLE-08～11 | Verification、Acceptance、Review/Resume、主链切流/drain |
| T-NLE-12～13 | durable event/redaction/SSE replay、Context 授权与预算 |
| T-NLE-14～17 | Life OCC、WorldProjection、Appraisal/Bond、Scene/Arbiter |
| T-NLE-18～20 | Capability、Degradation、Identity Snapshot |
| T-NLE-21～24 | Monitor UI 三组 read model 与最终封板/回滚演练 |

---

## 16. M48～M57 完整目标架构合同

目标蓝图：`docs/50_project/arch/NATSUME_AI_V1_FULL_ARCHITECTURE_TARGET_20260710.md`。

Master 执行文档：`docs/30_devspec/NATSUME_AI_V1_FULL_ARCHITECTURE_REFACTOR_MASTER_EXECUTION_20260710.md`。

M48 地基执行：`docs/30_devspec/NATSUME_AI_V1_LONG_TERM_EVOLUTION_EXECUTION_20260710.md`。

本节冻结目标工程边界，对应 `FR-ARCH-001`～`FR-ARCH-010`。目标蓝图解释最终归宿和设计理由；Master 执行文档定义 `TASK-0461～TASK-0622` 的任务顺序、依赖 Gate、切流、停止条件与回滚。三者发生冲突时，不得自行扩大实现范围：代码事实优先触发停工和文档修订，实施顺序与切流以 Master 执行文档为准，产品验收边界以 PRD 为准。

### 16.1 目标层次与依赖方向

目标包层次：

```text
natsume_contracts
  ↑
kernel / domain kernels
  ↑
application / orchestration / runtime services
  ↑
api / adapters / infrastructure / UI clients

composition → concrete implementations（唯一装配例外）
```

- `natsume_contracts` 只依赖 stdlib 和批准的最小 schema 库，不得依赖 Host、Edge、FastAPI、SQLite 或 provider。
- Domain/Kernel 只依赖 contracts、kernel primitives 和 domain ports，不得依赖 `app`、HTTP、数据库连接、全局 container 或具体 LLM provider。
- Application/Orchestration 通过 ports 调用 Domain、Task、Model、Capability、Persistence 和 Device 能力，不接触 FastAPI request 或 adapter 私有对象。
- API 只做认证、DTO mapping、idempotency envelope 和 application service 调用；Repository/Adapter 实现 ports，但不得反向成为领域真相源。
- Composition 可导入所有具体实现；任何业务层不得反向导入 Composition。
- `edge_client` 只能依赖 `natsume_contracts` 与 Edge 自有层；Domain Pack 只能依赖 Pack SDK 和公开 ports。
- 目标层之间双向 package dependency 必须为 0。临时例外必须具备 ADR、owner、usage telemetry 和删除版本。

### 16.2 跨运行面合同

以下合同必须版本化并置于实现无关的 contract package：

| 合同 | 必备字段/语义 |
|---|---|
| `IdentityEnvelope` | principal、tenant/user/session/device、actor、delegation、scope |
| `CommandEnvelope` | command/version、principal、authorization、idempotency、budget、correlation/causation |
| `QueryEnvelope` | query/version、principal、scope、cursor、freshness requirement |
| `RuntimeEvent` | event/schema version、identity、correlation/causation、observed/ingested time、visibility、evidence refs |
| `AuthorizationGrant` | subject、action、scope、version、actor、expiry、once/consumption state |
| `SideEffectReceipt` | action/attempt/idempotency、target、status、result/evidence refs、completed time |
| `DeviceEnvelope` | device identity、protocol/capability version、command sequence、lease、receipt |

- 合同升级采用 expand → compatibility window → consumer migration → remove；不得原地改变既有字段语义。
- Host/Edge/Gateway/UI 通过 schema/protocol negotiation 明确兼容性；不兼容必须 fail closed 或进入明确 `DegradationState`。
- OpenAPI、SSE event schema 和 UI generated client 必须来自同一版本化合同；重复 method/path handler 为 0。

### 16.3 Command、Query 与 Event Plane

- 所有外部写请求经 Secure Ingress 进入 Governed Command Plane；route 不得直接操作 Repository、Ledger `_conn`、Neo4j、Chroma 或设备 adapter。
- Query/Observability Plane 只读取版本化 projection；read model 必须包含 `schema_version`、`source_cursor`、`freshness` 和 `degraded_state`。
- 关键状态变化与 journal/outbox 在同一 UoW 中提交；consumer 使用 durable offset、幂等消费和 dead letter。
- SSE、进程内 EventBus、Neo4j、Chroma、Tribe Registry 和 UI cache 均为 projection/transport/cache，不得作为状态真相源。
- durable payload 只保存 decision summary、输入输出引用、证据引用与脱敏元数据；禁止 raw thought、secret、完整 cookie 和未授权私密正文。

### 16.4 Bounded Context 与唯一真相源

| Context | 唯一写模型 | 兼容投影/适配器 |
|---|---|---|
| Authorization | `AuthorizationGrant` + Token Repository | route/UI risk display |
| Task | `TaskGraph` + `TaskAttempt` + `BudgetReservation` | execution/trace UI |
| Runtime Event | `EventJournal` + Transactional Outbox | SSE/EventBus/metrics |
| Life | `LifeEventJournal` + versioned reducers | `digital_life_state` compatibility projection |
| Affect | `NatsumeAffectProjection` | `UserAffectObservation`、VAD compatibility |
| Homeostasis | `HomeostasisProjection` | Tribe organ contribution |
| Relationship | `RelationshipProjection` | Neo4j、Bond summary、SocialBrain cache |
| Memory | `LifeMemoryService` | Chroma/FTS/semantic projection |
| Canon | Canon proposal/review/apply aggregate | prompt/retrieval citation projection |
| Knowledge | Domain/Personal Knowledge Document Store | vector/full-text index |
| Proactive | `IntentDecision` + durable outbox | recent episode/read model |
| Embodiment | `EmbodimentCommand` + receipt | VTS/VRM/MateEngine/Pet adapter |
| Identity | immutable baseline + adaptive overlay version | release snapshot/diff |
| Capability | `CapabilityCatalog` + signed `SkillBundle` | registry/UI compatibility view |

每个长期字段只能有一个 writer；所有 projection 必须能从 journal 或权威 store 重建、对账和迁移。不得以“缓存更快”“旧代码仍调用”或“不同设备”作为第二真相源的理由。

### 16.5 Host Runtime 合同

- `composition` 统一装配 Settings、Feature Flags、Service Catalog、Clock、UoW、Task/Agent Runtime、Model/Capability Plane、Governor 和生命周期；业务代码不得读取第二套全局配置真源。
- Secure Ingress → Application/Orchestration → Task Runtime/Agent Runtime → governed side effect 是唯一生产执行路径。
- `Plan once, execute same revision`：规划、审批、执行、验证、验收和 replay 必须引用同一 `graph_id/revision`。
- TaskStore 持久化 Node、Attempt、Lease、Heartbeat、Budget、IdempotencyReservation 和 Outbox；恢复依据 attempt ownership，不依据进程内对象。
- 同一 action 只能有一个有效 AuthorizationGrant、一个 Task/Attempt lineage 和一个 SideEffectReceipt。
- Obs、legacy routes、scheduler 和 Domain Pack 不得形成平行 Planner、Governor、Review、Model Gateway、EventBus 或副作用执行器。

### 16.6 Digital Life Kernel 合同

目标数据流：

```text
Input/Time/Work/Device Receipt
  → LifeEventJournal
  → World/Appraisal/Affect/Homeostasis/Relationship projections
  → SceneResolver
  → IntentArbiter
  → Surface Expression | Proactive Outbox | Embodiment Outbox
  → Feedback/Receipt
  → LifeEventJournal
```

- LifeContinuity、LifeOrgans、Tribe、InnerWorld、DigitalLifeSupervisor 与 Scheduler 必须收敛到同一 coordinator、clock 和 event/reducer 流；不得长期保留双核。
- Surface Context build 是纯读操作；预览、重试、取消和 replay 不得推进生命状态或产生新 LifeEvent。
- 用户情绪观察与夏目自身 Affect 是不同 contract、字段和 writer；Relationship 必须完整持久化 trust/safety/dependency/rift/repair/boundary，而非只恢复 stage/score。
- Tribe Agent 只能输出 `StateContribution`；私密 Inner Narrative 使用独立 store、retention 和 redaction，不进入通用 trace/memory/prompt。
- SceneDecision 可影响表达和主动性；IntentArbiter 只能在 Governor 授权范围内选择或收紧，不能覆盖用户显式目标或提权。
- 主动行为与具身动作均通过 durable outbox、lease、idempotency 和 receipt；人格成长走 proposal → shadow evaluation → activate version → rollback。

### 16.7 Memory、Knowledge 与 Persistence 合同

- 现有 `memory_event` 应演进为统一 Life/Runtime Event Journal 的兼容后端，不得再创建平行、无迁移计划的事件真源。
- `LifeMemoryService` 是生活记忆唯一写服务；Episodic、Semantic、Distillation、Correction、Forgetting 与 Recall 通过明确 service/port 协作。
- WorldCanon 只有 proposal/review/apply 单写入口；Apply 校验 subject、version、expiry、once 和批准内容 hash。
- Core Canon、Persona、Pentest、BioOps、Personal Knowledge 等 namespace 必须隔离；跨域 promotion 保留来源、证据、审批和撤销记录。
- SQLite schema 只由受控 migration catalog 管理；production runtime inline DDL 和业务模块直接访问 Ledger 私有 `_conn` 必须归零。
- Repository/UoW 统一 transaction、WAL、timeout、backup/restore；Neo4j、Chroma 和全文索引均通过 outbox 投影并可重建。

### 16.8 Domain Pack 合同

每个 Pack 只能提供以下公开 contribution：

```text
manifest
commands / capabilities / tools
state repository port
knowledge namespace
verifier / acceptance profile
risk profile
benchmark suite
read-model / UI contribution
```

- Pack 不得拥有平台级 TaskGraph、Scheduler、Governor、Review、Model Gateway、EventBus、Identity 或核心状态 writer。
- Pack 不能直接导入 Host global container、Persona/Bond/LifeMemory/Canon private store 或其他 Pack 私有实现。
- Pack 生命周期必须支持发现、签名校验、staged activation、disable、upgrade、rollback 和使用量审计。
- 首批 Pack 为 Code、Research/Browser、Media、Personal Knowledge、Desktop Automation、Hardware/Embodiment、Pentest Lab、BioOps、MCP/External Providers。

### 16.9 Model、Prompt、Capability 与 Governance 合同

- 唯一模型尝试合同为 `ModelRequest → RouteDecision → ProviderAttempt → StructuredResult`；统一 provider registry、role/profile、timeout/retry/fallback/circuit breaker、token/cost/latency budget、usage 和 route trace。
- Prompt 模板声明 version、owner、input contract、slots 和 output schema；Persona/Task/Domain 仅通过已注册 slots 组合。
- `CapabilityCatalog` 联邦呈现 Skill、Tool、MCP、Agent 和 Pack；所有执行必须经过唯一 Tool/Capability Broker。
- Governor 是授权单写者；风险转换必须保留来源尺度与证据，Capability maturity 只可附加收紧，自动升级和自动提权禁止。
- `SkillBundle`/Provider bundle 必须具备 digest、signature、SBOM、quarantine、sandbox、staged activation 和 rollback；未隔离 Python 源码不得热导入 Host。

### 16.10 Edge Device Runtime 合同

- Edge 必须可作为独立发布单元安装、测试和回滚，只依赖共享 contracts 与 Edge 自有 composition/application/ports/adapters。
- Host、Edge、Gateway、Desktop Pet、MateEngine、VTS/VRM 通过 Service Catalog 协商 endpoint、principal、protocol version、capability 和 health。
- Gateway webhook 保留 raw body/header 用于平台验签，并使用 durable inbox/outbox、ack、idempotency、replay protection 和 DLQ。
- 物理输出的最终 authority 只属于 Edge Device Runtime；Host 仅发送 `EmbodimentCommand`，adapter 必须回传 receipt。
- Bridge 默认 loopback + token/HMAC；远程暴露必须显式启用、限定 scope/rate 并审计。

### 16.11 Query/Observability Plane 与 Operator Console 合同

- Obs 只承载 Query/Projection/Trace/Metrics/Replay；任何变更 Policy、Review、Domain、Device 或 Release 的动作必须回到 Secure Ingress。
- Operator Console 分为 Operate、Understand、Replay/Lab；route visibility 由后端 role/capability read model 决定，前端 risk 标签不构成授权。
- UI 不直接查询业务表、domain private store 或进程全局缓存；不得显示 raw thought、secret、cookie 或未授权 identity/memory 正文。
- 前端 client 从版本化 OpenAPI 生成，并通过 typecheck、unit/component、a11y、E2E、权限矩阵和 degraded-state 测试。

### 16.12 Evolution、Identity 与 Release 合同

- Evolution Lab 提供 benchmark、scenario、counterfactual、Skill Compiler、feedback proposal 和 identity migration analysis；输出只能进入受治理 proposal/review，不得直接改写生产状态。
- Identity 使用 immutable baseline + versioned adaptive overlay；激活前必须产生 replay/shadow evaluation、IdentitySnapshot/diff、审批和 rollback target。
- ReleaseManifest 包含 commit、package、schema、protocol、model/prompt/avatar assets、SBOM、Gate 结果和 rollback compatibility。
- 发布 Gate 覆盖 Golden Paths、replay、chaos、performance、security、migration、backup/restore、Edge 独立安装、UI 合同、identity regression 和 rollback drill。

### 16.13 兼容、迁移与退役规则

1. 迁移顺序固定为行为特征化 → contract/port 提取 → 新实现经旧 facade 接入 → shadow compare → 单 vertical slice 切新请求 → legacy drain → 删除旧 writer/facade。
2. Shadow 只能比较决策和输出，禁止同时执行真实副作用。
3. 双写不是默认策略；确需同步时只能由 transactional outbox 驱动，并具备 reconciliation、停止和 rollback 证据。
4. 兼容 facade 必须声明 owner、usage telemetry、introduced version、remove-by version 和 fallback 行为；不得新增业务逻辑。
5. 旧 projection 只读保留至对账完成；旧任务由旧执行器 drain，禁止跨执行器恢复。
6. facade usage=0、两个稳定发布窗口、rollback drill 通过后必须删除旧入口、旧 writer、runtime DDL 和 feature flag。
7. 不允许以“暂时兼容”为名永久保留新旧目录两套 Runtime、Life Core、Memory writer、Model Gateway、Governor 或 Device authority。

### 16.14 M48～M57 里程碑合同

下表的需求映射是主交付映射，不是排他映射。`FR-ARCH-001` 在 M49 建立边界并由 M57 终验全量依赖/巨石退役；`FR-ARCH-002` 在 M48-B/M49 建立安全与合同边界，并随 Host、Edge、Query/UI 迁移至 M56 完成跨运行面终验。

| 里程碑 | 目标主题 | TASK 范围 | 需求映射 | 退出 Gate |
|---|---|---|---|---|
| M48-A | 长期演化安全地基 | TASK-0461～0486 | FR-NLE-001～005；FR-ARCH 全局前置 | 边界、TaskStore、Review、Event、Life OCC 及回滚 Seal |
| M48-B | 平台 P0 安全封口 | TASK-0487～0496 | FR-ARCH-002、007～009 | Ingress/Gateway/Obs/Skill/Device 高风险入口 fail closed |
| M49 | Contracts、Kernel、Composition、依赖治理 | TASK-0497～0510 | FR-ARCH-001～002 | 共享合同、UoW/Outbox 地基、依赖规则和 composition 成立 |
| M50 | Host Application 与 Task/Agent Runtime 分层 | TASK-0511～0524 | FR-ARCH-003 | 单一任务执行主链、same revision、restart/retry 不重复副作用 |
| M51 | Digital Life Kernel、Surface 与 Identity | TASK-0525～0538 | FR-ARCH-004 | 单时钟/单 writer、Surface 纯读、关系/身份可重放回滚 |
| M52 | Memory、Knowledge、Event 与 Persistence | TASK-0539～0552 | FR-ARCH-005 | 唯一 Memory/Canon 写入口、projection 可重建、runtime DDL 为 0 |
| M53 | Domain Pack 与 Capability Platform | TASK-0553～0566 | FR-ARCH-006 | Pack 可独立启停/授权/回滚且无平台级第二内核 |
| M54 | Model、Prompt、Governor 与 Security Plane | TASK-0567～0580 | FR-ARCH-007 | 单一 ModelAttempt/ToolBroker/Authorization，供应链 Gate 通过 |
| M55 | Edge、Gateway、Voice、Avatar 与 Device Runtime | TASK-0581～0594 | FR-ARCH-008 | Edge 独立安装，Host 实现导入为 0，物理输出 authority 唯一 |
| M56 | Query/Observability Plane 与 Operator Console | TASK-0595～0608 | FR-ARCH-009 | Obs 只读、typed UI client、无 Query 绕写或私密泄漏 |
| M57 | Evolution Lab、Decompat、Release 与最终封板 | TASK-0609～0622 | FR-ARCH-010 | 旧 facade/writer 按 Gate 删除，完整质量矩阵和 rollback Seal |

M48-A 与 M48-B 只有在文件、migration 和切流范围不重叠时才可并行；M49 以后按前置 Gate 和 Master 执行文档推进，不得只依据任务编号并行。任何里程碑未通过退出 Gate 时，后续里程碑不得把目标能力描述为已实现事实。
