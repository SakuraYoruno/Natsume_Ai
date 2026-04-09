# 数字生命 P1 实施记录（Tribe Bootstrap）
日期：2026-04-08

## 本轮目标
- 落地 Host/Edge Tribe 最小可运行骨架。
- 打通 Edge -> Host Tribe 事件上报链路。
- 建立 Obs + WebUI 可观测闭环。

## 已交付内容
- Host Tribe 基础模块：
  - `host_brain/tribe/base_agent.py`
  - `host_brain/tribe/event_bus.py`
  - `host_brain/tribe/tribe_state.py`
  - `host_brain/tribe/__init__.py`
- Edge Tribe 基础模块：
  - `edge_client/tribe/base_agent.py`
  - `edge_client/tribe/edge_tribe_bridge.py`
  - `edge_client/tribe/registry.py`
  - `edge_client/tribe/__init__.py`
- Host 内部入口：
  - `POST /internal/tribe_event`
  - `GET /internal/tribe_event/recent`
  - 文件：`host_brain/app/internal_tribe_routes.py`
- Obs 读取接口：
  - `GET /obs/tribe/events`
  - 文件：`host_brain/app/obs_routes_core.py`
- Edge 链路事件发布：
  - `edge_client/runtime/voice_pipeline.py`
    - `body_sense_updated`
    - `emotion_inference_updated`
    - `voice_pipeline_started`
    - `voice_pipeline_ended`
  - `edge_client/gateway/router.py`
    - `channel_message_received`
    - `gateway_degraded`
- 启动注入：
  - `edge_client/runtime/bootstrap.py`
  - `edge_client/gateway/bootstrap.py`
- 环境变量模板补充（`.env.example`）：
  - `HOST_INTERNAL_TRIBE_TOKEN`
  - `EDGE_TRIBE_BRIDGE_ENABLED`
  - `EDGE_TRIBE_HOST_BASE_URL`
  - `EDGE_TRIBE_BRIDGE_TIMEOUT_MS`

## 前端接入（Insights）
- API 层：
  - `fetchTribeEvents(params)` in `host_brain/app/monitor_ui/src/api/obs.js`
- 页面：
  - `host_brain/app/monitor_ui/src/pages/Insights.jsx`
  - 分组：`/insights/safety`
  - 展示：最近事件数、事件类型 Top、事件表（时间/类型/source/target/payload 摘要）
  - 本轮新增：`event_type/source_agent/target_agent` 过滤输入与手动刷新

## 前端接入（Trace 联动）
- 页面：
  - `host_brain/app/monitor_ui/src/pages/Trace.jsx`
  - 位置：选中请求卡片下方
- 联动能力：
  - 选中请求后自动拉取关联 Tribe 事件
  - 支持手动刷新
  - 支持跳转 `Insights` 查看全局窗口
- 关联维度：
  - `request_id`
  - `session_id`
  - `conversation_id`

## 本轮增量（继续推进）
- Obs 后端过滤参数补齐：
  - `GET /obs/tribe/events` 新增：
    - `source_agent`
    - `target_agent`
    - `request_id`
    - `session_id`
    - `conversation_id`
  - 文件：`host_brain/app/obs_routes_core.py`
- EventBus 多条件过滤增强：
  - `host_brain/tribe/event_bus.py`
  - `recent(...)` 支持上述过滤项
- 内部 recent 接口对齐过滤参数：
  - `host_brain/app/internal_tribe_routes.py`
- Trace 侧改为“服务端优先过滤”：
  - 过滤优先级：`request_id` > `session_id` > `conversation_id`
  - 文件：`host_brain/app/monitor_ui/src/pages/Trace.jsx`
- TraceRequestDetail 新增请求级 Tribe 时序卡片：
  - 文件：`host_brain/app/monitor_ui/src/pages/TraceRequestDetail.jsx`
  - 能力：
    - 进入请求详情自动拉取 `request_id` 关联 Tribe 事件
    - 10 秒自动刷新（非阻塞）
    - 手动刷新按钮一并刷新 Trace + Stream + Tribe
    - 展示事件类型、source/target、时间与 payload 摘要
    - 新增“同轴时间线（节点 + Tribe）”视图，按统一时间轴联看跨层事件
    - 新增同轴时间线快速筛选：类型（NODE/TRIBE）、阶段、关键词搜索

## 本轮增量（按重构文档对齐）
- 新增 Host 族注册与族路由解析：
  - `host_brain/tribe/registry.py`
  - 能力：`HostTribeRegistry`、`TribeFamilySpec`、`resolve_target_family(...)`
- 新增 Dispatch handoff 协调器：
  - `host_brain/tribe/handoff.py`
  - 能力：记录 outbound/inbound 状态、发布 `dispatch_handoff` 事件
- 新增族壳层 Agent（便于后续 phase 逐族替换）：
  - `host_brain/tribe/ans_agent.py`
  - `host_brain/tribe/hippocampus_agent.py`
  - `host_brain/tribe/limbic_agent.py`
  - `host_brain/tribe/prefrontal_agent.py`
  - `host_brain/tribe/social_brain_agent.py`
  - `host_brain/tribe/cortex_agent.py`
- `dispatch_agent.py` 已接入 handoff 记录（Phase 5 关键要求）：
  - 覆盖路由：`recent_turn`、`plan`、`task_progress`、`chat`、`delegate`、`skill`、`fallback_chat`
  - 原则：非阻塞，失败仅 debug 日志，不影响主链路
- 新增测试：
  - `tests/unit/test_tribe_registry.py`
  - `tests/unit/test_tribe_handoff.py`
  - `tests/unit/test_dispatch_agent_tribe_handoff.py`

## 验证结果
- 后端测试：
  - `pytest tests/unit/test_tribe_event_bus.py tests/integration/test_internal_tribe_routes.py tests/integration/test_obs_tribe_events.py -q`
  - 结果：`8 passed`
- 后端增量测试（本轮）：
  - `pytest tests/unit/test_tribe_registry.py tests/unit/test_tribe_handoff.py tests/unit/test_dispatch_agent_tribe_handoff.py tests/unit/test_tribe_event_bus.py tests/unit/test_tribe_state_registry.py tests/integration/test_internal_tribe_routes.py tests/integration/test_obs_tribe_events.py -q`
  - 结果：`16 passed`
- 前端构建：
  - `npm --prefix host_brain/app/monitor_ui run build`
  - 结果：`vite build` 成功
- P0 验收脚本：
  - `scripts/runtime/verify_multi_agent_p0.ps1` 已通过（含 runtime 字段检查）

## 当前结论
- P1 的 Tribe 能力已形成 Host/Edge/Obs/UI 的稳定闭环。
- 事件观测从“全局窗口”扩展到“按请求下钻”，排障效率明显提升。

## 下一步建议
- 在 Trace 详情页增加 Tribe 事件时序泳道（与节点执行时间对齐）。
- 增加 `event_type/source_agent` 时序趋势图与异常突刺告警。
- 评估 Event Bus 持久化（Redis/消息中间件）降低重启丢失影响。
