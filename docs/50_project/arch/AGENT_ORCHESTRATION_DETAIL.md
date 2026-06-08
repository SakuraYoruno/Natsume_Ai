受众：MASTER
标题：Agent 编排详情（当前运行时实况）

版本：v2.3
日期：2026-05-30

---

定位：本文只整理当前代码中真正参与 Host 运行时的 Agent 编排路径。
若与旧阶段的 `SceneRouter / main_chain / agent_dispatch_node / judge_agent_node` 说法冲突，以本文和代码为准。

## 0. 代码证据入口

- `host_brain/app/asgi.py`
- `host_brain/app/app_factory.py`
- `host_brain/app/router_registry.py`
- `host_brain/app/chat_routes.py`
- `host_brain/app/chat_handler.py`
- `host_brain/app/cortex_orchestrator.py`
- `host_brain/app/agent_runtime_shared.py`
- `host_brain/app/executive_router.py`
- `host_brain/app/executive_runtime.py`
- `host_brain/app/executive_runtime_core.py`
- `host_brain/app/agent_routes.py`
- `host_brain/app/input_routes.py`
- `host_brain/tribe/registry.py`

---

## 一、当前编排入口

| 入口 | 当前模块 | 说明 |
|---|---|---|
| `POST /v1/chat` | `host_brain/app/asgi.py` -> `host_brain/app/chat_routes.py` -> `host_brain/app/chat_handler.py` | 主聊天入口先经过 HTTP 路由与免疫门控，再进入聊天编排并返回 SSE |
| `POST /v1/edge/events` | `host_brain/app/asgi.py` -> `host_brain/app/edge_event_routes.py` | Edge 运行时事件回流入口，委托 `receive_edge_events_response()` |
| `POST /v1/host/input` | `host_brain/app/input_routes.py` | ASR / Gateway 回流入口，会把输入重新送入 Host 主链 |
| `POST /v1/agent/run` | `host_brain/app/agent_routes.py` | 独立 Agent Loop 自动化入口，不等于聊天主链 |
| `/v1/edge/*` | `edge_client/app/fastapi_app.py` | 身体侧 RPC 能力面，供 Host 调用 |

---

## 二、当前 `/v1/chat` 主编排链

当前代码里，`/v1/chat` 的常规主链是：

```text
POST /v1/chat
  -> app_factory.create_host_fastapi_app()
  -> asgi.chat_endpoint
  -> chat_routes.handle_v1_chat_route
  -> asgi.evaluate_immune_gate_for_chat（注入）
  -> asgi.schedule_immune_gate_audit（注入）
  -> chat_handler.handle_chat
  -> chat_handler._unified_chat_sse_generator
  -> thalamus_agent.classify_and_compile_intent_draft
  -> IntentDecisionEngine.evaluate_intent
  -> cortex_orchestrator_sse_generator
  -> agent_runtime_shared._cortex_orchestrator_sse_generator_impl
  -> executive_router_sse_generator
  -> executive_runtime_sse_generator
  -> executive_runtime_core
```

在进入 `chat_handler.handle_chat` 之前，`chat_routes.handle_v1_chat_route` 还承担三件事：

- 记录并透传 `request_id`
- 执行 immune gate 检查，并异步写入 `IMMUNE_CHAT_GATE` 审计事件
- 若命中 `phatic_fastpath`，会直接在 HTTP 路由层返回 SSE，不再进入后续编排主链

这条链路中的职责分层如下：

| 分层 | 当前模块 | 职责 |
|---|---|---|
| Intent 收口 | `thalamus_agent` | 把用户输入收敛为 canonical intent draft |
| 治理审批 | Governor | 做票据、风险、批准决策 |
| Cortex 编排 | `cortex_orchestrator.py` + `agent_runtime_shared.py` | 组装上下文、召回、bond、trace 和后处理 |
| Executive 路由 | `executive_router.py` | 路由层薄门面 |
| Executive 核心 | `executive_runtime.py` + `executive_runtime_core.py` | 决定 chat / skill / delegate / plan 等执行路径 |

---

## 三、当前 Executive 路由类型

`host_brain/app/executive_runtime_core.py` 当前主路由类型如下：

| route_type | 说明 | 典型目标 |
|---|---|---|
| `chat` | 直接进入聊天生成链 | `chat_agent` |
| `skill` | 命中 SkillRegistry 中的具体 skill | 各类 `skill_id` |
| `delegate` | 委派给某个 agent / handler / runtime capability | `intent_agent` 等 |
| `plan` | 构建 `SkillDagPlan` 并执行多步计划 | `skill_dag` |
| `recent_turn` | 对“刚才说了什么”类问题走直接快路 | 无固定 target |
| `task_progress` | 对“现在做到哪了”类问题走任务摘要快路 | 无固定 target |

当前代码不是“默认 Supervisor -> agent_dispatch_node -> judge_agent_node”这套旧图式，而是 skill-first runtime：

- 先做 route 选择
- 再按 route 走 skill / delegate / plan / chat
- 失败时由 `executive_runtime_core` 生成恢复策略，再决定回退到 delegate 或 chat

---

## 四、当前 Tribe 侧编排关系

`host_brain/tribe/registry.py` 当前定义了 `23` 个 tribe family spec，并默认注册了当前关键 agent：

- `cortex_orchestrator`
- `executive_router`
- `chat_agent`
- `thalamus_agent`
- `ans_agent`
- `hippocampus_agent`
- `limbic_agent`
- `prefrontal_agent`
- `social_brain_agent`
- `immune_agent`

当前代码中的关键关系是：

| 角色 | 所属 family | 说明 |
|---|---|---|
| `cortex_orchestrator` | `cortex` | Host 主聊天编排入口 |
| `executive_router` | `cortex` | 承接 route / delegate / skill |
| `chat_agent` | `cortex` | 负责 chat 路径的生成与渲染 |
| `thalamus_agent` | `thalamus` | 统一 intent compile |
| `immune_agent` | `immune` | 输入与聊天入口风险扫描 |

`executive_runtime.record_executive_handoff()` 还会把 handoff 写入：

- tribe handoff coordinator
- `INTERACTION_RECORDED` tribe event

因此，当前“编排”不仅是函数调用链，也是 tribe handoff 与 trace 事件链。

---

## 五、`/v1/host/input` 与编排闭环

`host_brain/app/input_routes.py` 当前承担的是“外部输入回流”而不是旧的 main_chain 直驱：

```text
ASR / Gateway input
  -> /v1/host/input
  -> 输入质检 / 免疫门控 / ACK
  -> 需要时异步回流到 Host 主链
  -> 结果再经 Gateway / Edge 回送外部平台
```

这意味着当前 Agent 编排不能只看 `/v1/chat`，还要把 `/v1/host/input` 视为外部世界进入 Host 主权链的入口。

---

## 六、当前不再适用的旧口径

以下说法不再代表当前主编排路径：

- `SceneRouter.route_sse()` 是当前总分流器
- `host_brain/app/graph/main_chain.py` 是聊天主链入口
- `agent_dispatch_node` 是默认多 Agent 主路径
- `judge_agent_node` 是所有子 Agent 的固定收口
- `top_agent.py` / `dispatch_agent.py` 是当前主链中心

这些内容如果还出现在旧文档中，应视为历史阶段留痕，而不是当前运行时事实。
