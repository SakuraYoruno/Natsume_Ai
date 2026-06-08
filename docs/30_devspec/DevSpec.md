受众：CODEX
标题：开发规格 DevSpec（当前代码工程契约参考）

# Natsume Ai 开发规格（DevSpec）
版本：v2.1
日期：2026-06-08
定位：当前代码的工程契约参考手册。每条契约可追溯到具体 .py 文件。不再承担"从零可重建蓝图"职责。

---

## 1. 架构总览

5 个协同运行面：

| 运行面 | 入口 | 端口 | 技术栈 |
|--------|------|------|--------|
| Host Main | `host_brain/__main__.py` | 8000 | FastAPI + LangGraph |
| Obs Backend | `host_brain/obs_main.py` | 18082 | FastAPI |
| Edge RPC | `edge_client/__main__.py` | 17800 (可配置) | Python RPC |
| Unified Message Gateway | `edge_client/gateway/server.py` | 19000 | FastAPI |
| Desktop Pet | `desktop_pet/main.cjs` | 18788 | Electron |

---

## 2. Host Main 主链契约

### 2.1 FastAPI 装配

- `host_brain/app/asgi.py` — ASGI 应用入口
- `host_brain/app/app_factory.py` — FastAPI app 工厂
- `host_brain/app/router_registry.py` — 路由注册

### 2.2 Chat 主链（`POST /v1/chat`，SSE）

```
asgi.py
  → app_factory.create_host_fastapi_app()
  → router_registry
  → chat_routes.handle_v1_chat_route()
    → chat_immune_gate (HTTP 层免疫门控，支持 fastpath/BLOCK)
    → chat_handler.handle_chat()
      → thalamus_agent.classify_and_compile_intent_draft()
      → Governor.evaluate_intent()
      → cortex_orchestrator_sse_generator()
      → agent_runtime_shared
      → executive_router / executive_runtime / executive_runtime_core
        → surface_reply_runtime (人格渲染路由)
          → surface_main_chain_adapter
            → SurfaceMainChain (最终文本作者)
          → reply_envelope (发送/持久化/缓存收口)
      → surface_reply_postprocessor (异步后处理)
```

关键文件：
- `host_brain/app/chat_routes.py` — 路由层
- `host_brain/app/chat_immune_gate.py` — 免疫门控
- `host_brain/app/chat_handler.py` — 薄编排入口
- `host_brain/tribe/thalamus_agent.py` — 意图编译
- `host_brain/governor/decision.py` — 裁决入口
- `host_brain/app/cortex_orchestrator.py` — SSE 编排
- `host_brain/app/agent_runtime_shared.py` (5111 行) — 共享运行时
- `host_brain/app/executive_router.py` — 执行路由
- `host_brain/app/executive_runtime.py` — 执行运行时
- `host_brain/app/executive_runtime_core.py` — 执行核心
- `host_brain/app/surface_reply_runtime.py` — 人格渲染路由者
- `host_brain/persona/surface_main_chain.py` — SurfaceMainChain (normal chat 最终文本作者)
- `host_brain/persona/reply_envelope.py` — 最终输出收口

### 2.3 Input 回流链（`POST /v1/host/input`）

Gateway/ASR 归一化输入回流：
```
input_routes.py
  → 即时 ACK / 质量分析 / 偏航检测
  → thalamus_agent
  → cortex_orchestrator
  → (同 chat 主链后续)
  → 通过 edge_rpc_client 回复 Gateway
```

- `host_brain/app/input_routes.py` — 承担 ACK、进度推送、重对齐通知

### 2.4 Edge 事件回流（`POST /v1/edge/events`）

Edge 运行时事件（播放状态、资源门控、inline waiters）走此端点，不走 `/v1/host/input`。

---

## 3. Surface 主链契约

`host_brain/persona/` 目录（55+ 模块），核心模块：

| 文件 | 职责 |
|------|------|
| `surface_main_chain.py` | SurfaceMainChain — normal chat 文本唯一作者 |
| `surface_main_chain_adapter.py` | 主链适配器，连接 surface_reply_runtime 与 SurfaceMainChain |
| `surface_memory_recall.py` | 记忆召回注入（决策 RECALL_POLICY_MUST/MAY/NO） |
| `surface_context_packet.py` | 上下文包组装 |
| `surface_reply_preprocessor.py` | 输入预处理与 payload 构建 |
| `reply_envelope.py` | 最终输出收口（sendable/persitable/cacheable） |
| `reply_modality_router.py` | 媒体输出路由 |
| `inner_monologue.py` | 内心独白 |
| `motive_engine.py` | 动机引擎 |
| `text_quality_gate.py` | 文本质量门控 |
| `galgame_taste_guard.py` | Galgame 风格守护 |
| `isolated_surface_harness.py` | 隔离测试 harness |

Surface 禁令：
- 不得新增事实、不得升级承诺强度、不得升级证据强度、不得引入新 intent
- Canon/风格材料/情绪标签/外显动作提示不得当成已发生事实

---

## 4. 记忆与召回契约

### 4.1 LifeMemoryStore（SSOT）

- `host_brain/memory/life_memory.py` (8260+ 行) — 生活记忆 SSOT，SQLite 持久化
- 负责 episodic 记忆的写入、衰减、合并、删除

### 4.2 召回融合

- `host_brain/retrieval/recall_fusion.py` — `RecallFusionServiceContract`
- `host_brain/retrieval/recall_fusion_stores.py` — 多路 store 搜索
- `host_brain/retrieval/hybrid_fusion.py` — vector + FTS5 混合融合
- `host_brain/retrieval/full_text_searcher.py` — FTS5 全文检索
- `host_brain/retrieval/recall_schemas.py` — `RecallFusionResult`
- `host_brain/retrieval/markdown_kb_store.py` — Markdown 知识库召回

### 4.3 记忆蒸馏

- `host_brain/memory/long_term_distiller.py` — Episodic → 语义记忆蒸馏
- `host_brain/memory/semantic_memory.py` — 语义记忆存储

---

## 5. Tribe 系统契约

23 个 family spec，均 A 级升级完毕。

- `host_brain/tribe/registry.py` — family 注册
- `host_brain/tribe/thalamus_agent.py` — 意图编译入口
- `host_brain/tribe/immune_agent.py` — 风险门控
- `host_brain/tribe/limbic_agent.py` — 动机态与情绪路由
- `host_brain/tribe/proprioception_agent.py` — 认知负荷
- `host_brain/tribe/prefrontal_agent.py` — 漂移告警

---

## 6. Gateway 契约

6 平台适配器：wechat、qq、feishu、wecom、telegram、line

全链路：
```
平台 Webhook
  → gateway/server.py
  → adapter.parse_inbound()
  → MessageNormalizer.normalize()
  → GatewayRouter.route()
  → EdgeInputReporter._send()
  → POST /v1/host/input (Host 主链)
  → input_routes._deliver_gateway_reply()
  → edge_rpc_client.post_gateway_reply()
  → POST /v1/gateway/reply
  → OutboundDispatcher.dispatch()
  → platform adapter.send_reply()
```

关键文件：
- `edge_client/gateway/server.py` — Gateway 服务入口
- `edge_client/gateway/adapter/` — 各平台适配器
- `edge_client/gateway/router.py` — 消息路由
- `host_brain/channels/edge_rpc_client.py` — Host → Edge/Gateway 推送

---

## 7. 外显执行面契约

### 7.1 Desktop Pet

- `desktop_pet/main.cjs` — Electron 运行时
- HTTP 控制端口：127.0.0.1:18788
- Actions：show、hide、preset、touch、emote、speak、click_through、status
- Host 侧调用：`host_brain/channels/edge_rpc_client.py` → `post_desktop_pet()`

### 7.2 MateEngine

- `host_brain/channels/mateengine.py` (1579 行) — UDP 桥接，端口 32145
- 13 种 v0 事件、11 种 pose preset
- 状态→姿势映射、状态→情绪映射、touch→情绪映射
- action_sequence / choreography 编排支持

### 7.3 VTS

- `host_brain/channels/vts.py` — emotion_tag→expression_id 确定性映射（16 种表情）
- `host_brain/channels/vts_mapping_store.py` — 映射持久化与版本化
- `host_brain/channels/vts_rate_limiter.py` — 令牌桶频率限制

---

## 8. 可观测性契约

- Obs Backend：`host_brain/obs_main.py`，独立进程，端口 18082
- FastAPI app：`host_brain/app/obs_fastapi_app.py`
- Monitor UI：`host_brain/app/monitor_ui/` — 多页面 SPA
- 预热缓存：runtime snapshot、trace feed、insights summary、digital life overview、life body

主要 API：
- `GET /obs/services/health` — 全服务健康聚合
- `GET /obs/sessions` — 会话列表（分页+过滤）
- `GET /obs/trace/{request_id}` — 链路节点详情
- `GET /obs/metrics/timeseries` — 指标时序

---

## 9. 安全与免疫契约

### 9.1 Immune Gate

- `host_brain/app/chat_immune_gate.py` — HTTP 层免疫门控

### 9.2 Governor

- `host_brain/governor/decision.py` — `IntentDecisionEngine`
- `host_brain/governor/rules.py` — `evaluate_governor_gates` (R1-R8)
- `host_brain/governor/tokens.py` — `TokenService`

### 9.3 Airlock

- `host_brain/airlock/quarantine.py` — 入站隔离分类
- `host_brain/airlock/sanitizer.py` — 出站净化
- `host_brain/airlock/sandbox.py` — 沙箱执行
- `host_brain/airlock/pii_filter.py` — PII 过滤

### 9.4 审计

- `host_brain/audit/sqlite_ledger.py` — SQLite Ledger SSOT
- 三联画：intent_log / governor_log / skill_exec_log

---

## 10. 路由表

| 方法 | 路径 | 文件 | 说明 |
|------|------|------|------|
| POST | `/v1/chat` | `chat_routes.py` | 主对话入口（SSE） |
| POST | `/v1/host/input` | `input_routes.py` | Gateway/ASR 回流 |
| POST | `/v1/edge/events` | `edge_routes.py` | Edge 事件上报 |
| POST | `/v1/agent/run` | `agent_routes.py` | Agent 执行 |
| POST | `/v1/mateengine/signal` | `mateengine_routes.py` | MateEngine 信号 |
| POST | `/v1/mateengine/say` | `mateengine_routes.py` | MateEngine 语音 |
| GET | `/health` | `main.py` | 健康检查 |
| GET | `/obs/services/health` | `obs_fastapi_app.py` | 全服务健康 |
| GET | `/obs/sessions` | `obs_fastapi_app.py` | 会话列表 |
| GET | `/obs/trace/{request_id}` | `obs_fastapi_app.py` | 链路详情 |
| GET | `/obs/metrics/timeseries` | `obs_fastapi_app.py` | 指标时序 |

---

## 11. 数据格式契约

### 11.1 SSE 三阶段协议

- PRESENCE（在场流）：非权威，只给前端，不作为下游节点输入
- COMMIT（提交流）：权威，仅 `GOV_OK_RENDER` 时产生
- POST（渲染流）：读 Commit 快照，PhaseLockGuard 验证

实现：`host_brain/integrations/graph_streaming.py`

### 11.2 GraphState 关键字段分组

- 基础执行：`request_id / graph_id / timeout_ms / payload / trace`
- 路由：`pending_node_id / edge_map / committed_route_token`
- 模型：`model_nodes / model_profile_id / model_calls`
- 情感：`emotion_context / emotion_tag / mood_state`
- Clock：`policy_start_ms / policy_timeout_ms / clock_events`
- 并行分支：`branch_specs / branch_manifest`
- Streaming：`presence_events / commit_snapshot / render_snapshot_ref`
- Checkpoint：`gov_checkpoint_id / interrupt_reason / resume_allowed`
- Multi-Agent：`active_agent_id / handoff_history / agent_budget / agent_artifacts`

---

## 12. 当前代码事实入口

- `docs/50_project/arch/CURRENT_CODE_ARCHITECTURE.md` — 模块全景
- `docs/50_project/arch/dataflow_report.md` — 数据流报告
- `docs/50_project/arch/CORE_MODULES_DETAILED_REFERENCE.md` — 模块详细参考
- `docs/15_theory/APP_AUDIT_SKILLS_CONTRACT.md` — 应用/审计/技能层契约
- `docs/30_devspec/ModelPlane.md` — 模型平面治理
