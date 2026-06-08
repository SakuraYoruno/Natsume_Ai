受众：MASTER
标题：产品需求文档（PRD）

# Natsume Ai 产品需求文档（PRD）
版本：v2.0
日期：2026-06-05
定位：基于当前代码事实的系统能力描述。不再写"目标蓝图"，而是记录"系统实际做什么"。

---

## 1. 产品定义

Natsume Ai 是一个本地优先的 AI 陪伴系统，由 5 个协同运行面组成：

| 运行面 | 端口 | 入口 | 职责 |
|--------|------|------|------|
| Host Main | :8000 | `host_brain/__main__.py` | 主权裁决、人格渲染、记忆、审计 |
| Obs Backend | :18082 | `host_brain/obs_main.py` | 可观测性后端 |
| Edge RPC | :17800 | `edge_client/__main__.py` | 本地感知与执行中枢 |
| Unified Message Gateway | :19000 | `edge_client/gateway/server.py` | 多平台消息收发 |
| Desktop Pet | :18788 | `desktop_pet/main.cjs` | Electron 桌面宠物 |

核心原则：**模型/Agent 候选层负责提案，Host 负责裁决与最终表达收口，所有执行事实必须可审计回放。**

---

## 2. 核心功能需求

### FR-CHAT：Normal Chat 主链

- 入口：`POST /v1/chat`，SSE 流式响应
- 三阶段协议：PRESENCE（在场流，非权威）→ COMMIT（提交流，Governor 后产生）→ POST（渲染流，读 Commit 快照）
- 主链：`asgi → app_factory → router_registry → chat_routes → chat_immune_gate → chat_handler → thalamus → governor → cortex_orchestrator → agent_runtime_shared → executive_router/runtime/core → chat_agent → surface_main_chain_adapter → persona/surface_* → reply_envelope → chat_agent_postprocessor`
- SurfaceMainChain（`host_brain/persona/surface_main_chain.py`）是 normal chat 文本的**唯一作者**，旧 prompt_builder 链路已降级为辅助 facts/state 提供者
- Immune gate 在 HTTP 层做 fastpath/BLOCK 拦截，再进入主编排

### FR-SURFACE：人格渲染

- 55+ 模块位于 `host_brain/persona/`，包括：
  - `surface_main_chain.py`（主链入口，SurfaceMainChain）
  - `surface_memory_recall.py`（记忆召回注入）
  - `inner_monologue.py`、`motive_engine.py`
  - `text_quality_gate.py`、`galgame_taste_guard.py`
  - `isolated_surface_harness.py`（隔离测试 harness）
- 人格设定文件：`Soul.md`（项目根目录）

### FR-GATEWAY：多平台消息网关

- 独立运行面，6 平台适配器：wechat、qq、feishu、wecom、telegram、line
- 链路：平台 Webhook → `gateway/server.py` → `adapter.parse_inbound()` → `MessageNormalizer.normalize()` → `GatewayRouter.route()` → `EdgeInputReporter._send()` → `POST /v1/host/input` → Host 主链 → `edge_rpc_client.py` → `POST /v1/gateway/reply` → `OutboundDispatcher.dispatch()` → 平台 adapter `send_reply()`
- 出入站均走 AIRLOCK 净化

### FR-EMOTION：情感系统

- `host_brain/core/metabolism.py`：EmotionVector（VAD 三维连续情绪）、HardState
- `host_brain/core/emotion_timeline.py`：情感时间线、compute_emotion_echo
- `host_brain/core/endocrine.py`：离散硬状态→连续内分泌基调（dopamine/serotonin/cortisol）
- emotion_tag 确定性映射到 VTS 表情 ID 和 TTS 语气

### FR-MEMORY：生活记忆

- LifeMemoryStore（`host_brain/memory/life_memory.py`，8260+ 行，SQLite 持久化）是记忆 SSOT
- 多路召回融合：`RecallFusionServiceContract` → vector + FTS5 混合 → 候选过滤 → `surface_memory_recall` 决定注入
- 记忆蒸馏：Episodic → 语义记忆 → Canon pending 提案
- Markdown 知识库召回（`markdown_kb_store.py`）作为补充通道

### FR-CANON：人物/世界/风格 Canon

- World Canon：`host_brain/canon/` — 最小权威内容集合，用于一致性引用（非执行事实）
- Style Canon：风格范式约束（tone_only），不承载事实
- 更新走 Evolution 提案流程 + 审计

### FR-TRIBE：器官族系统

- 23 个 family spec，均 A 级升级完毕，无灰度开关
- `host_brain/tribe/registry.py`：family 注册
- 关键 agent：thalamus_agent（意图编译）、immune_agent（风险门控）、limbic_agent（动机态）、proprioception_agent（认知负荷）、prefrontal_agent（漂移告警）

### FR-OBS：可观测性

- Obs Backend（`host_brain/obs_main.py`）独立进程
- Monitor UI（`host_brain/app/monitor_ui/`）：30+ 页面的多页面 SPA
- 4 组信息架构：数字生命体 / 运营 / 器官级观测 / 外周执行
- 预热缓存：runtime snapshot、trace feed、insights summary、digital life overview、life body

### FR-AVATAR：外显执行面

- Desktop Pet：Electron 应用（`desktop_pet/main.cjs`），HTTP 控制端口 18788
  - Actions：show、hide、preset、touch、emote、speak、click_through、status
- MateEngine：UDP 桥接（`host_brain/channels/mateengine.py`，1579 行），端口 32145
  - 13 种 v0 事件、11 种 pose preset、完整状态→姿势→表情映射
  - 支持 action_sequence / choreography 编排
- VTS：emotion_tag→expression_id 确定性映射（16 种表情）

### FR-VOICE：语音链

- ASR：faster-whisper large-v3，流式/分段
- TTS：GPT-SoVITS，全 GPU 常驻
- Barge-in：用户开口可打断当前播报
- Edge 资源闸门：ASR > 播放 > TTS 合成 > 兜底 LLM

### FR-MODE：模式切换

- 6 种系统模式：normal、focus、gaming、sleep、maintenance、locked
- `host_brain/core/mode_transition.py`：ModeTransitionService
- 6 种触发来源：MANUAL、AUTO_GAMING、AUTO_IDLE、AUTO_WAKEUP、AUTO_SCHEDULE、SKILL
- 模式影响：能力矩阵、资源预算、调度器行为

### FR-CODEX：Codex Runtime

- Codex 作为正式 LLM provider，配置：`HOST_CODEX_BASE_URL`、`HOST_CODEX_MODEL_ID`
- `host_brain/integrations/codex_cli_provider.py`：CLI 适配
- App-server 线程预热（`ea9ae24f`）

### FR-SECURITY：安全与免疫

- `host_brain/app/chat_immune_gate.py`：免疫门控（HTTP 层 fastpath/BLOCK）
- `host_brain/airlock/`：入站隔离（quarantine、sandbox、sanitizer、pii_filter） + 出站净化
- Governor rate limiting + Token 体系（approval/confirm/undo）
- 审计三联画：intent_log / governor_log / skill_exec_log

### FR-SCHEDULER：调度系统

- 声明式 Intent 调度（`host_brain/scheduler/`）：NatsumeScheduler + SchedulerHealthReporter
- Host 常驻任务调度（`host_brain/app/automation/task_scheduler.py`）：HostTaskScheduler
- 两者分开管理：声明式调度面向业务 intent，常驻调度面向系统维护任务

---

## 3. 非功能需求

- **NFR-PERF**：3-5 秒开口（在场回应 + 云超时退化链：Gemini → Codex relay → DeepSeek → host_14b）
- **NFR-RELIABILITY**：依赖故障不崩溃，输出结构稳定，关键链路可观测
- **NFR-AUDIT**：≥99% 请求可串联追溯（request_id → intent → governor → skill_exec → result）

---

## 4. 外部接口

| 接口 | 端点 | 说明 |
|------|------|------|
| Chat | `POST /v1/chat` | 主对话入口（SSE） |
| Host Input | `POST /v1/host/input` | Gateway/ASR 回流入口 |
| Edge Events | `POST /v1/edge/events` | Edge 运行时事件上报 |
| Gateway Webhook | `POST /v1/gateway/{platform}/webhook` | 平台消息入站 |
| Gateway Reply | `POST /v1/gateway/reply` | Host → Gateway 出站 |
| Desktop Pet | `GET/POST :18788` | 桌面宠物控制 |
| MateEngine | UDP :32145 | 姿态/表情/动作控制 |
| Agent Run | `POST /v1/agent/run` | Agent 执行入口 |
| Obs Health | `GET /obs/services/health` | 全服务健康聚合 |
| Obs Sessions | `GET /obs/sessions` | 会话列表 |
| Obs Trace | `GET /obs/trace/{request_id}` | 链路节点详情 |
| Obs Metrics | `GET /obs/metrics/timeseries` | 指标时序 |

---

## 5. 当前架构事实入口

以下文档为当前代码的权威参考（均标注 `[LIVING]`）：

- `docs/50_project/arch/CURRENT_CODE_ARCHITECTURE.md` — 模块全景
- `docs/50_project/arch/dataflow_report.md` — 数据流报告
- `docs/50_project/arch/CORE_MODULES_DETAILED_REFERENCE.md` — 核心模块详细参考
- `docs/15_theory/APP_AUDIT_SKILLS_CONTRACT.md` — 应用/审计/技能层契约
- `docs/30_devspec/DevSpec.md` — 工程契约参考
