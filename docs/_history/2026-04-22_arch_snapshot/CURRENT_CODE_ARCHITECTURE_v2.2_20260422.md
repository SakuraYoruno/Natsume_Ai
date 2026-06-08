受众：MASTER
标题：当前代码架构总览（2026-04-22）

版本：v2.2
日期：2026-04-22
定位：以当前仓库代码为准，说明运行面、入口、主链、目录和监控前端结构。若历史方案文档与本文冲突，以本文和代码为准。

# 当前代码架构总览（2026-04-22）

## 0. 快速导航

- 仓库入口：`README.md`
- 核心模块详解：`docs/50_project/arch/CORE_MODULES_DETAILED_REFERENCE.md`
- 数据流补充：`docs/50_project/arch/dataflow_report.md`
- 前端主体化方案：`docs/50_project/arch/DIGITAL_LIFE_BODY_FRONTEND_ARCHITECTURE_20260419.md`

## 0.1 2026-05-08 Normal Chat 主线记忆（高优先级）

这条记录是后续 normal chat 重构的主线约束。若历史方案、旧链文档或局部测试与本节冲突，以本节为准。

- 旧链是“完整能力库”，不是当前 normal chat 文本质量阶段的 live 主链。
- 当前新主链是“文本质量基线内核”，目标是先把中文文本质量拉到合格，再逐步迁回旧链能力。
- 当前阶段的 normal chat 文本主链固定为：`input -> ReplyControlPacket -> SurfaceMainChain -> TextQualityGate/Scorer -> ReplyEnvelope`。
- 文本质量未过关前，不继续把旧链功能模块塞回 normal chat live path。
- 后续每次只迁移一个旧链能力模块，并且必须通过 A/B 证明没有降低文本质量。
- 被迁移的旧链模块只能提供 `facts / state / memory / perception / performance / media action`，不能写最终人格回复。
- `reply_plan`、`validator`、fallback、fastpath、transport ack、thread resolver、cache、memory 都不得成为第二作者。
- normal chat 最终文本作者只能是 `SurfaceMainChain`；媒体场景的最终作者只能是结构化 reaction/media selector。
- 如果某个模块导致文本质量下降，它只保留在实验链或非聊天任务链，不进入 normal chat 主线。

## 1. 代码证据入口

以下文件构成当前代码架构的直接证据入口：

- `host_brain/__main__.py`
- `host_brain/obs_main.py`
- `edge_client/__main__.py`
- `host_brain/app/asgi.py`
- `host_brain/app/app_factory.py`
- `host_brain/app/router_registry.py`
- `host_brain/app/chat_routes.py`
- `host_brain/app/obs_fastapi_app.py`
- `edge_client/app/fastapi_app.py`
- `host_brain/app/chat_handler.py`
- `host_brain/app/cortex_orchestrator.py`
- `host_brain/app/input_routes.py`
- `host_brain/app/executive_runtime.py`
- `host_brain/app/executive_runtime_core.py`
- `host_brain/app/chat_agent.py`
- `host_brain/app/web_ui.py`
- `host_brain/tribe/registry.py`
- `edge_client/gateway/server.py`
- `edge_client/gateway/router.py`
- `host_brain/app/monitor_ui/src/App.jsx`
- `host_brain/app/monitor_ui/src/components/Layout.jsx`

## 2. 当前运行面

| 运行面 | 入口文件 | 默认端口 | 当前职责 |
|---|---|---:|---|
| Host Main | `host_brain/__main__.py` | 8000 | 主聊天入口、治理、兼容观测 API、静态面板入口 |
| Observability Backend | `host_brain/obs_main.py` | 18082 | 独立观测后端，承接重型监控查询和预热缓存 |
| Edge RPC | `edge_client/__main__.py` | 17800 | TTS、VTS、barge-in、截图、自动化、视觉定位、主动推送、fallback、微信查询等能力入口 |
| Unified Message Gateway | `edge_client/gateway/bootstrap.py` | 19000 | 外部平台消息接入与回推 |
| Monitor UI Source | `host_brain/app/monitor_ui/` | - | React 监控前端源码 |
| Monitor UI Static | `host_brain/app/static/monitor/` | - | 构建产物，由 Host Main 静态托管 |

补充说明：

- Host Main 仍然保留 `/obs/*` 兼容入口，主要用于本地一体化运行观察与排障。
- `host_brain/app/asgi.py` + `host_brain/app/app_factory.py` 负责统一组装应用与默认路由。
- Observability Backend 启动时会预热 `runtime snapshot`、`trace feed`、`insights summary`、`digital-life overview`、`life-body` 五类缓存。

## 3. 代码目录主干

```text
Natsume_Ai/
├─ host_brain/
│  ├─ app/                     # FastAPI、聊天运行时、obs 路由、监控前端源码
│  ├─ tribe/                   # Tribe family、事件总线、handoff、agent 注册
│  ├─ memory/ retrieval/ bond/ # 记忆、检索、关系系统
│  ├─ observability/           # 可观测性聚合与指标
│  ├─ scheduler/               # 调度器
│  ├─ skills/ tools/           # 技能系统与工具系统
│  ├─ channels/                # Host ↔ Edge 通道客户端
│  └─ persona/                 # 人格、内在世界、自我模型等
├─ edge_client/
│  ├─ app/                     # Edge FastAPI RPC 网关
│  ├─ runtime/                 # EdgeRuntime 装配
│  ├─ gateway/                 # Unified Message Gateway
│  ├─ actuators/               # TTS / VTS / playback
│  ├─ services/                # 微信数据库与 watcher 等本地服务
│  └─ senses/                  # ASR / fallback 感知
├─ configs/                    # model plane、deploy、release、secret 模板
├─ docs/                       # 文档体系
├─ scripts/runtime/            # 启停脚本
├─ tests/                      # unit / integration / fixtures
└─ tools/                      # 诊断、回放、静态扫描、文档门禁
```

## 4. Host Main 路由面

`host_brain/app/app_factory.py`（由 `host_brain/app/asgi.py` 注入路由函数）当前实际挂载的主要路由面如下：

| 前缀 / 入口 | 来源文件 | 作用 |
|---|---|---|
| `/v1/chat` | `asgi.py` -> `chat_routes.handle_v1_chat_route` -> `chat_handler.py` | HTTP 聊天入口；先做 immune gate / fastpath 判定，再进入统一 SSE 编排 |
| `/v1/edge/events` | `asgi.py` -> `edge_event_routes.py` -> `system_handler.py` | HTTP 路由接收 Edge 批量事件，再委托 `receive_edge_events_response()` 处理 |
| `/v1/agent/*` | `agent_routes.py` | 视觉/自动化 Agent Loop |
| `/v1/host/input` | `input_routes.py` | ASR / Gateway 归一化输入回流 |
| `/canon/*` | `canon_routes.py` | Canon 提案、文档和审核 |
| `/scheduler/*` | `scheduler_routes.py` | 调度器任务管理 |
| `/host-scheduler/*` | `app/automation/scheduler_routes.py` | HostTaskScheduler 运行时管理 |
| `/tools/*` + `/tool_kits` | `tool_routes.py` | 工具元数据、执行、技能安装/修复 |
| `/bond/*` | `bond_routes.py` | Bond 阶段与关系接口 |
| `/prompt-config/*` | `prompt_config_routes.py` | Prompt 模板查看、修改、重载 |
| `/internal/*` | `internal_tribe_routes.py` | Host/Edge 内部桥接 |
| `/internal/codex/*` | `internal_codex_routes.py` | Codex 内部管理接口 |
| `/health` `/ready` `/startup` | `health_routes.py` | 健康、就绪、启动报告 |
| `/obs/*` | `obs_routes*.py` | 兼容挂载的监控 API |
| `/api/ui/*` | `web_ui.py` | 监控 UI 所需聚合/搜索辅助 API |
| `/panel` `/monitor/*` `/static/monitor/*` | `obs_fastapi_app.py`（独立后端）或兼容入口（按部署形态） | 监控面板入口与静态资源 |

## 5. Observability 与 Monitor UI

### 5.1 独立观测后端

`host_brain/app/obs_fastapi_app.py` 当前承载：

- `/api/ui/*`
- `/health`
- `/obs/metrics`
- `/obs/services/health`
- `/obs/llm/nodes`
- `/obs/organ-tribe/status`
- `/obs/sessions`
- `/obs/trace/{request_id}`
- `/obs/trace-feed/snapshot`
- `/obs/trace-feed/live`
- `/obs/tribe/events`
- `/obs/graph/schema`
- `/obs/insights/summary-snapshot`
- `/obs/digital-life/overview-snapshot`
- `/obs/life-body`
- `/obs/life-timeline`
- `/obs/pathologies`

这说明当前观测后端已经从“单一统计页 API”演进到“生命体总览 + life body / timeline / pathologies + trace feed + 洞察摘要 + UI 聚合搜索”的多视图后端。

### 5.2 Monitor UI 当前导航

`host_brain/app/monitor_ui/src/App.jsx` 和 `Layout.jsx` 显示，前端当前分为 4 组导航：

| 导航组 | 页面 |
|---|---|
| 数字生命体 | 总览、生命时间轴、病灶中心、情绪、关系、记忆、存在态、内在世界、回复策略、自我模型 |
| 运营 | 提示词、人格文案、系统底座、设定管理 |
| 器官级观测 | 生命体征、神经会话、神经链路、代谢指标、皮层洞察、神经调试、免疫告警、生理日志 |
| 外周执行 | 感官网关、行为技能、执行工具、节律调度、反射回路 |

技术栈来自 `host_brain/app/monitor_ui/package.json`：

- React 18
- React Router
- Tailwind CSS
- ECharts
- XYFlow
- Zustand

### 5.3 当前 LLM 恢复落点

截至 2026-04-22，当前代码已经把“全认知能力 LLM 恢复”中的共享底座落到运行面：

- 共享池入口：`host_brain/integrations/llm_helpers.py` + `host_brain/integrations/llm_gateway.py::complete_with_pool()`
- 当前 tier：A/B/C/D 四档，Gemini 与 MiniMax 组成主备候选池
- 审计面：`llm_pool_call_log` + `LLM_POOL_EXHAUSTED` 已落库到 ledger
- 主链接池：`chat_agent.py`、`thalamus_agent.py`、`multimodal_observer_backends.py`、`emotion_llm_inference.py`
- 认知接池：`reflection_chain.py`、`self_reflection.py`、`evolution_desire_engine.py`、`bond_narrative.py`、`style_distiller.py`
- 记忆层补点：`hippocampus_agent.py` recall 后联想摘要、`memory/distillation.py`、`long_term_distiller.py`
- 调度治理：`scheduler.py` 已对后台 LLM 任务增加 `focus / gaming / sleep` 模式 allowlist 和 `LLM_POOL_EXHAUSTED -> SKIPPED` 收口

## 6. 聊天主链

当前 `/v1/chat` 的常规主链可以概括为：

```text
POST /v1/chat
  -> app_factory.create_host_fastapi_app()
  -> asgi.chat_endpoint()
  -> chat_routes.handle_v1_chat_route()
  -> asgi.evaluate_immune_gate_for_chat() / asgi.schedule_immune_gate_audit()
  -> chat_handler.handle_chat()
  -> _unified_chat_sse_generator()
  -> thalamus_agent.classify_and_compile_intent_draft()
  -> Governor evaluate_intent()
  -> cortex_orchestrator_sse_generator()
  -> executive_router / executive_runtime / executive_runtime_core
  -> chat_agent_sse_generator()（需要人格渲染时）
  -> post process（记忆、Bond、VTS、TTS、观测）
```

补充说明：

- 若 immune gate 决策为 `BLOCK`，会在 `chat_routes.handle_v1_chat_route` / `asgi.chat_endpoint` 直接返回单个 `COMMIT(REJECT)` SSE
- 若命中 `phatic_fastpath`，也会在 HTTP 路由层直接返回 SSE，不再进入 `chat_handler.handle_chat()`

关键事实：

1. `chat_handler.py` 已经不是旧式“单文件大链路”，而是主入口薄层。
2. 意图收口由 `thalamus_agent` 负责。
3. 主编排收口在 `cortex_orchestrator`。
4. 真正的执行分派和 direct route/skill/delegate/chat 处理落在 `executive_runtime.py` 与 `executive_runtime_core.py`。
5. `chat_agent.py` 当前是“人格渲染与流式说话模块”，不再承担总路由职责。

## 7. 外部输入与 Edge 回流

### 7.1 外部输入回流

当前外部输入统一收敛到 `POST /v1/host/input`：

- ASR 文本
- Gateway 平台消息

其他 Edge 运行时事件（例如播放状态、资源门控、inline waiters）走 `POST /v1/edge/events`，不通过 `POST /v1/host/input`。

`host_brain/app/input_routes.py` 会先做输入解析和日志，再接入：

- `thalamus_agent` 意图编译
- gateway 即时 ACK / 进度推送
- 执行质量分析与偏航检测
- `cortex_orchestrator` 主链

### 7.2 Edge RPC

`edge_client/app/fastapi_app.py` 当前已经不仅是 TTS 网关，还包括：

- `/v1/edge/health`
- `/v1/edge/speak`
- `/v1/edge/barge_in`
- `/v1/edge/vts`
- `/v1/edge/fallback/*`
- `/v1/edge/screenshot`
- `/v1/edge/automate`
- `/v1/edge/visual_locate`
- `/v1/edge/proactive_push`
- `/v1/edge/proactive_messages`
- `/v1/edge/wx/*`
- `/internal/tribe_command`

补充：`/v1/edge/wechat/callback` 与 `/v1/edge/wechat/command` 在当前代码中保留兼容壳，但实现会显式返回 `legacy disabled`；平台侧入口应以 Gateway `/v1/gateway/*` 为准。

这说明 Edge 侧已经同时承担：

- 语音执行
- 桌面自动化
- 视觉定位
- 主动消息推送
- 微信数据查询与桥接

### 7.3 Unified Message Gateway

`edge_client/gateway/adapters/` 当前存在以下适配器：

```text
wechat / qq / feishu / wecom / telegram / line
```

链路为：

```text
外部平台
  -> edge_client/gateway/server.py
  -> GatewayRouter
  -> Host /v1/host/input
  -> thalamus / cortex / executive runtime
  -> Host 回复
  -> OutboundDispatcher
  -> 平台适配器
```

## 8. Tribe 系统当前口径

`host_brain/tribe/registry.py` 当前注册的是 `23` 个 family spec，而不是旧 README 中的 `22` 个。

当前 family_id 列表如下：

```text
cortex
thalamus
ans
hippocampus
limbic
prefrontal
social_brain
endocrine
immune
pain_reward
language
somatic
peripheral
circadian
proprioception
initiative_governor
default_mode
mirror_neuron
interoception
motor_cortex
cerebellum
glymphatic
vestibular
```

因此，所有仍写“22 个器官族 Agent”的旧文档，都应视为历史表述。

## 9. 当前阅读建议

如果目标是快速理解代码，不建议从阶段性升级方案文档入手。推荐顺序是：

1. `README.md`
2. 本文
3. `CORE_MODULES_DETAILED_REFERENCE.md`
4. `dataflow_report.md`
5. `DIGITAL_LIFE_BODY_FRONTEND_ARCHITECTURE_20260419.md`

如果目标是追某条主链，再往下看对应代码：

- 聊天主链：`asgi.py` + `app_factory.py`（/v1/chat 绑定）→ `chat_routes.py` → `chat_handler.py` → `cortex_orchestrator.py` → `executive_runtime_core.py` → `chat_agent.py`
- 观测后端：`obs_fastapi_app.py` → `obs_routes_*.py`
- Edge/网关：`edge_client/app/fastapi_app.py` → `edge_client/runtime/bootstrap.py` → `edge_client/gateway/*`
