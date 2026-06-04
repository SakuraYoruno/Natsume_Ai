受众：MASTER
标题：当前数据流报告（2026-06-04）

版本：v3.1
日期：2026-06-04
定位：基于当前仓库代码，描述 `/v1/chat` / `/v1/host/input` / `/v1/edge/events` / Unified Message Gateway / Observability / Desktop Pet / MateEngine Bridge 等主流量路径的真实走向。若历史方案文档与本文冲突，以本文与代码为准。

# 当前数据流报告（2026-06-04）

## 0. 代码证据入口

本文所有数据流以下列文件为证据：

进程入口：
- `host_brain/__main__.py`
- `host_brain/obs_main.py`
- `edge_client/__main__.py`
- `edge_client/gateway/bootstrap.py`
- `desktop_pet/main.cjs`

Host Main FastAPI 装配：
- `host_brain/app/asgi.py`
- `host_brain/app/app_factory.py`
- `host_brain/app/router_registry.py`

Chat 主链：
- `host_brain/app/chat_routes.py`
- `host_brain/app/chat_immune_gate.py`
- `host_brain/app/chat_handler.py`
- `host_brain/app/cortex_orchestrator.py`
- `host_brain/app/agent_runtime_shared.py`
- `host_brain/app/executive_router.py` / `executive_runtime.py` / `executive_runtime_core.py`
- `host_brain/app/chat_agent.py` / `chat_agent_preprocessor.py` / `chat_agent_postprocessor.py` / `chat_payload_builder.py`
- `host_brain/app/surface_main_chain_adapter.py`
- `host_brain/persona/surface_main_chain.py` / `surface_packet_compiler.py` / `surface_runtime.py`

Input 与 Edge：
- `host_brain/app/input_routes.py`
- `host_brain/app/edge_event_routes.py`
- `host_brain/channels/edge_rpc_client.py`
- `edge_client/gateway/server.py` / `router.py` / `normalizer.py`
- `edge_client/gateway/adapters/`
- `edge_client/app/fastapi_app.py`
- `edge_client/runtime/voice_pipeline.py`
- `edge_client/runtime/avatar_behavior.py`

Desktop Pet / MateEngine：
- `desktop_pet/main.cjs`
- `host_brain/app/mateengine_routes.py`
- `host_brain/channels/mateengine.py`

观测：
- `host_brain/app/obs_fastapi_app.py`
- `host_brain/app/obs_routes_*.py`
- `host_brain/app/obs_routes_stats_system.py`
- `host_brain/app/monitor_ui/src/components/AvatarStateCard.jsx`

## 1. 当前运行面与流量入口

| 流量类型 | 入口 | 处理面 | 出口 |
|---|---|---|---|
| 用户聊天 | `POST /v1/chat`（Host Main 8000） | `chat_routes → chat_immune_gate → chat_handler → cortex → executive → chat_agent → surface chain` | SSE token 流 |
| 平台消息入站 | Gateway adapter（19000）→ `POST /v1/host/input`（Host Main） | `input_routes → cortex_orchestrator` | SSE 或 ack |
| Edge 事件回流 | `POST /v1/edge/events`（Host Main） | `edge_event_routes` | ack |
| Edge 能力调用 | Host → Edge RPC（17800） | Host 主动出站 | 同步响应 |
| Desktop Pet 控制 | Host → Desktop Pet 直连（18788）或 Host → Edge → Desktop Pet | `edge_rpc_client.post_desktop_pet` / `POST /v1/edge/desktop_pet` | 本地桌宠窗口动作 |
| MateEngine 表现层 | Host `/v1/mateengine/*` 或 chat lifecycle cue | `mateengine_routes` / `host_brain.channels.mateengine` | UDP signal 32145 或 bridge HTTP 18770 |
| 观测数据查询 | Monitor UI → Obs Backend（18082） | `obs_routes_*` | JSON |

## 2. `/v1/chat` 主链

### 2.1 入口层（`asgi.py` + `app_factory.py`）

```
uvicorn → host_brain.app.asgi:app
  → app_factory.create_host_fastapi_app(...)
  → 全局中间件链：
      add_request_id_and_audit（attach request_id, count metrics, audit）
      configure_http_middleware（CORS / GZip 等）
  → router_registry.register_host_routers（注入业务路由）
  → 直接注册 /v1/chat、/v1/edge/events、/metrics、/
  → /v1/chat 委托 chat_endpoint(request_body, request)
        → handle_v1_chat_route(...)（chat_routes.py）
```

`chat_endpoint` 接收 `ChatRequest`：

- `message` / `session_id` / `person_id` / `reply_channel` / `reply_target` / `input_source`
- `attachments` / `multimodal_observations` / `attachment_summary`
- `emotion_tag` / `emotion_intensity` / `emotion_context` / `intent_draft`
- `render_target_lang` / `suppress_edge_tts` / 其它运行时偏好

### 2.2 `chat_routes.handle_v1_chat_route`

职责：

1. `notify_user_interaction()`：触发用户活动信号（被 ANS / inner_world / proactive 使用）
2. `build_chat_bio_ops_preflight_trace(request_body)`：构建 BioOps trace（即使失败也降级），写入 `request.state.bio_ops_runtime_trace`
3. `evaluate_immune_gate_fn(request_body)` → 得到 immune 决策（ALLOW / REPORT / BLOCK）
4. 异常时降级为 `IMMUNE_EVALUATE_DEGRADED + ALLOW`
5. `schedule_immune_gate_fn(...)`：异步把 immune 决策写到 ledger（不阻塞主链）
6. **BLOCK** 时直接返回 `sse_single_commit({phase: COMMIT, decision: REJECT, reason_code: IMMUNE_BLOCKED_HIGH_RISK, ...})`
7. **REPORT** 时只写 info 日志、继续放行
8. 否则委托 `handle_chat_fn(request_body, request, metrics)`

### 2.3 `chat_handler.handle_chat`

职责：

1. `metrics.inc_chat()`
2. 解析 `request_id` / `session_id` / `person_id` / `render_target_lang`
   - GATEWAY 输入强制 `render_target_lang = "zh"`
3. **sys_mode 门控**：
   - `HOST_SYS_MODE=locked` → 拒绝（`SYS_MODE_LOCKED` SSE）
   - `HOST_SYS_MODE=maintenance` → 维护提示（`SYS_MODE_MAINTENANCE` SSE）
4. 否则返回 `StreamingResponse(_unified_chat_sse_generator(...))`

### 2.4 `_unified_chat_sse_generator` 阶段

```
PRESENCE(THINKING)
  ↓
thalamus_agent.classify_and_compile_intent_draft(...)
  → 产出 canonical intent_draft（route=chat/delegate/skill/plan）
  → 推断 scene_type（默认 SCENE_CHAT）
  ↓
_evaluate_governor_with_timing(...)（在 _GOVERNOR_EXECUTOR 线程池）
  → governor_result（approved/declined/...）
  ↓
PRESENCE(GOV_APPROVED)
  ↓
cortex_orchestrator_sse_generator(...)
  → record_user_turn（conversation/writer）
  → agent_runtime_shared._cortex_orchestrator_sse_generator_impl(...)
```

## 3. `cortex_orchestrator` 数据流

实际实现都在 `agent_runtime_shared._cortex_orchestrator_sse_generator_impl`。

### 3.1 编排步骤

```
1. airlock_sanitize_input(message) → sanitized_message
2. 触发 retrieval prefetch（surface chain 之前的 fact 预取）
   - prefetch_retrieval_persona_inputs：拿 bond / canon / style / character
   - timeout 由 _resolve_retrieval_timeout_seconds 控制
3. 直接路由 fastpath 检查（多个 try）：
   - _maybe_build_task_progress_fastpath
   - _maybe_build_recent_turn_answer
   - _maybe_build_contextual_memory_answer
   - _maybe_build_contextual_style_answer
   - _maybe_build_contextual_session_answer
   - _maybe_build_previous_meaning_answer
   命中任一则直接产出 commit（不进 LLM）
4. 否则进入 executive 层：
   executive_router_sse_generator(...)
     → executive_runtime_sse_generator(...)
       → _executive_runtime_core_sse_generator_impl(...)
         → 决策 route 类型 → chat_agent_sse_generator(...)
5. commit 之后：
   _async_post_process(...) 调度副作用（不阻塞 SSE 流）：
     - TTS 落地（trigger_tts_after_commit + broca_to_tts_params）
     - life_memory 写回（_write_surface_main_chain_memory_sync）
     - bond stage 更新
     - observability trace 落盘
     - PII egress scrub
6. MateEngine lifecycle cue（不写事实源）：
   - `_send_delayed_mateengine_thinking_cue` 在长思考时发送 thinking cue
   - `_send_mateengine_state_cue` 按 intent / event 推断 listening、thinking、speaking、home 等状态
   - COMMIT 后 `_send_mateengine_chat_bubble` 提取回复文本与 `reply_envelope.performance` / `audit.natsume_emotion_tag`，发送本地桌宠说话状态与气泡
   - 该链路只驱动本地表现层，不改变 memory / policy / audit 的事实判定
```

### 3.2 直接路由 fastpath（不经 LLM）

| Fastpath | 用户意图举例 | 出处 |
|---|---|---|
| `task_progress` | "进度怎么样" / "做完了吗" | `_maybe_build_task_progress_fastpath` |
| `recent_turn` | "刚刚我说什么" / "你刚说什么" | `_maybe_build_recent_turn_answer` |
| `contextual_memory` | "你记得 X 吗" | `_maybe_build_contextual_memory_answer` |
| `contextual_style` | "你刚才那种语气" | `_maybe_build_contextual_style_answer` |
| `contextual_session` | "刚刚我们聊到哪" | `_maybe_build_contextual_session_answer` |
| `previous_meaning` | "刚才那句啥意思" | `_maybe_build_previous_meaning_answer` |

`_legacy_direct_text_routes_enabled()`（默认 false）控制旧的直接文本路由是否启用，仅作 A/B 用途。

### 3.3 Retrieval 与 SurfacePacket

```
RecallFusionServiceContract（host_brain/retrieval/recall_fusion.py）
  → 多路召回：vector（Chroma）+ FTS5（FullTextSearcher）+ canon stores + life_memory + world store + artifact vault
  → hybrid fusion（vector_weight + fts_weight）
  → 候选过滤：anaphoric selection、deep review、specific item reference、broad collection
  → 输出 RecallFusionResult
↓
host_brain/persona/surface_memory_recall.py
  → 决定 RECALL_POLICY_MUST/MAY/NO
  → 应用 surface_memory_recall_decision_matrix
  → 产出 MemoryRecallPacket（必要事实 + 时间锚 + 引语，已压缩）
↓
host_brain/persona/surface_packet_compiler.py
  → 联合 SurfaceAffectControlPacket / SurfaceInfluencePacket
  → 编译为 CompiledSurfaceInput（SurfaceTaskControl + facts + style anchor + must_not_do）
↓
host_brain/persona/surface_runtime.py
  → 多候选生成 + 重排 + observation_codes + natsume_emotion_tag
  → 输出 SurfaceRuntimeResult
```

## 4. `executive_runtime` 路由流

### 4.1 当前路由类型

| Route | 含义 | 终点 |
|---|---|---|
| `chat` | 普通聊天 | `chat_agent_sse_generator` → SurfaceMainChain |
| `delegate` | 委派给指定 agent | `chat_agent_sse_generator`（带 sub_agent_context） |
| `skill` | 调用某个 skill | tool/skill broker |
| `plan` | 多步任务规划 | `_maybe_build_multi_step_plan` → 多回合执行 |

### 4.2 路由判断顺序（`executive_runtime_core`）

```
_hard_rule_route(intent_draft, user_message)
  优先 hard rules：
    - explicit skill target → skill route
    - explicit delegate target → delegate route
    - 多步请求 → plan route
    - simple technical advice → chat route
    - simple task split → chat route
    - 默认 → chat route

_should_force_complex_plan(...)
  覆盖：当 prefrontal / cortex_route_decider 强制要求复杂规划时

最终输出 (route, target, plan_meta)
```

### 4.3 路由决策的辅助器

- `host_brain/tribe/cortex_route_decider.py`：双层意图分类 + G1 TTS 兜底
- `host_brain/tribe/cortex_intent_analyzer.py`：意图解析
- `host_brain/tribe/prefrontal_agent.py:plan_response`：前额叶决策
- `host_brain/tribe/thalamus_agent.py`：canonical intent 收口

### 4.4 不同 route 的后续流向

```
route=chat
  → executive_runtime_core 直接 stream_chat_route → chat_agent_sse_generator
  → ChatAgent 检查 surface chain，启用时走 surface_main_chain_sse_generator

route=delegate
  → 把任务交给 sub-agent（如 prefrontal / specific tribe agent）
  → 返回时 chat_agent 决定是否保留 sub-agent 结果给用户

route=skill
  → 调 tool_broker.execute_skill（host_brain/tools / skills/）
  → commit 时附带 skill 结果

route=plan
  → 生成多步计划 → 分别执行 → 汇总 commit
```

## 5. `chat_agent` 输出流

```
chat_agent_sse_generator(agent_input: ChatAgentInput)
  ↓
检查 surface_main_chain_required(agent_input, sub_agent_ctx)
检查 surface_main_chain_enabled(agent_input, sub_agent_ctx)
  ↓
启用 → 委托 surface_main_chain_sse_generator(agent_input, ...)
        ↓
        build_surface_main_chain_input_from_chat_input
        run_surface_main_chain_for_chat_input
          → SurfaceRuntime.run(...)
          → SurfaceRuntimeResult
        ↓
        _apply_surface_memory_output_guard（按 readiness、deep review、misc guard 调整）
        _sanitize_main_chain_meta_reply（去掉元语言）
        ↓
        通过 reply_envelope 包装成 COMMIT SSE
        ↓
        chat_agent_postprocessor.schedule_async_post_process
          - log_input_completed（input ack 写入）
          - scrub_pii_egress（出站 PII 净化）
          - trigger_tts_after_commit（按 broca 参数）
          - _write_surface_main_chain_memory_sync（life memory 回写）

未启用（fallback / sub-agent bypass）
  → 走 chat_agent.py 原有兼容路径（旧链 prompt_builder + chat_payload_builder）
  → 同样走 chat_agent_postprocessor 收尾
```

SSE 三阶段：

| 阶段 | 出现时机 | 是否阻塞流 |
|---|---|---|
| `PRESENCE` | THINKING / GOV_APPROVED / SCENE / RUNTIME_HINT | 否（轻量 ping） |
| `COMMIT` | 文本完成、reply 决策 | 是（commit 必须完成） |
| `POST` | commit 之后异步副作用 | 否（不影响用户接收） |

## 6. `/v1/host/input` 回流链

### 6.1 输入源

- 网关：`edge_client/gateway/router.py` 把平台消息归一化后 POST 到 `/v1/host/input`
- ASR：Edge 的 `runtime/voice_pipeline.py` 把识别结果作为 input 上报
- 自动化任务：scheduler 触发的"主动发声"也走 input 路径（`emotional_proactive` 等）

### 6.2 当前回流流程

```
POST /v1/host/input
  ↓
input_routes.handle_input_request
  ↓
input_service_quality（去重、长度门槛）
input_service_log（写 ledger）
input_service_ack（生成 ack）
input_service_gateway（gateway 特定逻辑：thread resolve、media merge）
  ↓
分支：
  - chat-bound → 调用 cortex_orchestrator_sse_generator（同 /v1/chat 主链）
  - perception-only → 写 perception episode 入 life_memory，不触发 chat
  - 系统事件 → 派发 tribe_event
  ↓
ack response（含 input_id / decision / queue_state）
```

## 7. Host ↔ Edge 调用链

### 7.1 Host → Edge（host_brain/channels/edge_rpc_client.py）

主要能力：

- `/v1/edge/health`
- `/v1/edge/avatar_state`
- `/v1/edge/speak`（TTS）
- `/v1/edge/barge_in`（打断）
- `/v1/edge/asr`（ASR 控制）
- `/v1/edge/vts`（虚拟形象 / avatar behavior）
- `/v1/edge/desktop_pet`（Electron 桌宠转发）
- `/v1/edge/fallback/*`
- `/v1/edge/screenshot`
- `/v1/edge/automate`（桌面自动化）
- `/v1/edge/visual_locate`
- `/v1/edge/proactive_push` / `proactive_messages`
- `/v1/edge/wx/*`（微信查询能力）

### 7.2 Edge → Host

- 文本 / 语音 / 多媒体 → Gateway 归一化 → `POST /v1/host/input`
- 状态 / 事件 → `POST /v1/edge/events`

### 7.3 `POST /v1/edge/events`

入口：`host_brain/app/edge_event_routes.handle_edge_events_route`

- 接收 EdgeEventsRequest
- 写 ledger（audit）
- 必要时 emit tribe_event（唤起 ANS / inner_world / language_system）
- 返回 EdgeEventsResponse（ack 状态 + 处理摘要）

### 7.4 Desktop Pet / MateEngine 外显流

Desktop Pet 直连路径：

```
Host 表现层副作用
  ↓
host_brain/channels/edge_rpc_client.post_desktop_pet(...)
  ↓ HOST_DESKTOP_PET_DIRECT_ENABLED=true（默认）
desktop_pet/main.cjs
  - GET /health
  - POST /action
  ↓
Electron 透明窗口 / Live2D renderer
```

Desktop Pet Edge 转发路径：

```
Host
  ↓ HOST_DESKTOP_PET_DIRECT_ENABLED=false
Edge RPC POST /v1/edge/desktop_pet
  ↓ EDGE_DESKTOP_PET_BASE_URL（默认 http://127.0.0.1:18788）
desktop_pet/main.cjs /action
```

MateEngine 路径：

```
/v1/chat lifecycle cue 或 /v1/mateengine/* Host API
  ↓
host_brain/channels/mateengine.py
  ├─ UDP signal: 127.0.0.1:32145
  └─ HTTP bridge: http://127.0.0.1:18770
  ↓
MateEngine 本地桌面表现层
```

这两条链路都属于外显执行面；它们可以展示状态、口型、气泡、动作和姿态，但不成为 memory / policy / audit 的事实来源。

## 8. Unified Message Gateway 闭环

```
External Platform
  ↓ webhook / poll / 长连接
edge_client/gateway/adapters/<platform>.py
  - wechat_adapter / qq_adapter / feishu_adapter / wecom_adapter / telegram_adapter / line_adapter
  ↓ raw_event
edge_client/gateway/router.py
  - 平台路由 + thread resolve
  ↓ 平台格式
edge_client/gateway/normalizer.py
  - 归一化为统一 input contract（含 attachment、reply_target、input_source=GATEWAY）
  ↓
edge_client/channels/input_reporter.py / channels/base.py
  - POST 到 host_brain /v1/host/input
  ↓
host_brain.input_routes → cortex_orchestrator → ... → COMMIT
  ↓ reply
edge_client/gateway/humanize_sender.py
  - 拆分长文本为人类节奏的多条
  ↓
adapter.send 出站 → 平台
```

新增：

- `gateway/media/video_handler.py`：视频附件处理
- `gateway/models.py`：扩展的 gateway 数据模型
- gateway 多模态附件 pipeline（`feat: add gateway multimodal attachment pipeline`）
- 队列重试持久化（`Persist gateway input retry queue`）

## 9. 观测与前端数据流

### 9.1 独立观测后端

```
host_brain/obs_main.py
  ↓ 加载 host_brain.app.obs_fastapi_app:app
host_brain/app/obs_fastapi_app.py
  - lifespan：预热 digital_life_overview / insights_summary / life_body 缓存
  - include_router：
      web_ui_router            (/api/ui/*)
      obs_router               (/obs/*)
      obs_health_router        (/obs/* health 子集)
      obs_trace_router         (/obs/trace/*)
      obs_summary_router       (/obs/* summary)
      obs_life_body_router     (/obs/life-body/*)
      obs_digital_life_soak_router (/obs/digital-life-soak/*)
  - 中间件：CORS + GZip
```

数据来源：

- `audit/sqlite_ledger`（trace、metrics、incidents）
- `host_brain/observability/queries`（聚合查询）
- 实时进程内 metrics（按需拉取 Host Main 进程 / 自身缓存）

### 9.2 Monitor UI 页面组（30+ 页）

```
host_brain/app/monitor_ui/src/App.jsx
  ↓ React Router + lazy loading
routeLoaders.js（30+ lazy import）
  ↓
Layout.jsx（4 大导航 + 32 个菜单项）
  ↓
pages/<Page>.jsx + components/<topic>/*.jsx
  ↓
queries/<topic>Queries.js（统一封装 API 调用 + 缓存）
  ↓
Obs Backend /api/ui/* + /obs/*
```

页面分组（与 Layout 一致）：

- 数字生命体：`DigitalLifeOverview / LifeTimelinePage / PathologyCenterPage / EmotionDashboard / Bond / MemoryBrowser / RuntimeMode / InnerWorldDashboard / RenderPolicyDashboard / SelfModelDashboard`
- 运营：`PromptConfig / PersonaPromptOps / SystemConfig / Canon`
- 器官级观测：`Overview / Sessions / Trace / CodexBioOpsObservability / Metrics / Insights / ChatDebug / Alerts / LogStream`
- 外周执行：`Gateway / SkillsHub / Tools / Automation / AgentLoop`

Avatar 状态观测：

```
DigitalLifeOverview
  ↓ useAvatarStateQuery / AvatarStateCard
obs.js fetchAvatarState()
  ↓
GET /obs/avatar/state
  ↓ host_brain.channels.edge_rpc_client.post_vts(action="status")
POST /v1/edge/vts
  ↓
edge_client.runtime.avatar_behavior.build_avatar_behavior_state
```

Monitor UI 的 avatar 行为按钮走 `POST /obs/avatar/behavior` → `edge_rpc_client.post_avatar_behavior()` → `POST /v1/edge/vts`，属于安全的高层行为控制，不直接写 VTS 热键或 MateEngine 事实状态。

## 10. 当前已废弃的旧口径（不要再按这些读）

| 旧描述 | 当前事实 |
|---|---|
| Host Main 入口是 `host_brain/app/fastapi_app.py` | 入口是 `host_brain.app.asgi:app`，由 `app_factory + router_registry` 装配 |
| Host Main 同时承担观测后端 | Host Main 与 Observability 是两个独立进程；Host Main 仅保留兼容 `/obs/*` |
| `prompt_builder` 是 normal chat 主作者 | normal chat 主作者是 `SurfaceMainChain`；prompt_builder 仅供旧链路或非 chat 任务 |
| Agentic RAG pipeline 提供 chat 召回 | Agentic RAG stack 已从 `retrieval/__init__.py` 移除；当前走 `RecallFusionServiceContract` + `surface_memory_recall` |
| Tribe family 通过灰度开关启用 | 灰度开关已全部删除；23 个 family 全部直接生效 |
| `chat_context_builder.py` 提供 chat 上下文 | 该文件已删除；上下文构建拆到 `chat_payload_builder.py` + `chat_agent_preprocessor.py` + `host_brain/app/context/` |
| `code_agent_handler.py` 是 chat 路径之一 | 已删除；不再被 chat 路径依赖 |
| `executive_runtime` 是单文件 1 万行巨石 | 已拆为 `executive_router.py`（薄壳） + `executive_runtime.py`（路由薄层） + `executive_runtime_core.py`（核心决策） |
| Surface 主链是实验链 | Surface 主链已是 normal chat 默认作者（`Make surface main chain default for text chat`） |
| 旧 `surface_renderer` 路径仍然存在 | 已退役（`remove retired surface renderer path`） |
| `style_canon` / `persona_utterance` 召回路由 | 已删除 |

## 11. 关键不变量

1. normal chat 文本最终作者只能是 `SurfaceMainChain`。
2. 旧链能力只能注入 facts/state/memory/perception，不能写最终回复。
3. 媒体场景的最终作者是 `reply_modality_router`。
4. SSE 三阶段 `PRESENCE → COMMIT → POST` 不可颠倒。
5. immune gate（`chat_immune_gate`）必须在 chat handler 之前执行。
6. life memory 是当前记忆 SSOT；vector / semantic 是衍生层。
7. Host Main 与 Observability 是两个独立 ASGI app。
8. Desktop Pet / MateEngine / VTS / VRM 只承载外显执行与状态观测，不提供记忆、人格策略或审计事实。
8. 23 个 tribe family 没有灰度开关，全部直接生效。
