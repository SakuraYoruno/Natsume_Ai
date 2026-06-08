受众：MASTER
标题：host_brain 核心应用层契约（当前代码实况：app / audit / skills / observability）

版本：v3.1
日期：2026-06-04
归属：docs/15_theory/

---

定位：本文只描述当前代码里 `host_brain` 应用层、审计层、技能层和观测层的真实入口与契约。
若与旧文档中的 `SceneRouter / main_chain / top_agent / dispatch_agent / chat_handler_complex` 口径冲突，以本文和代码为准。

## 0. 代码证据入口

- `host_brain/__main__.py`
- `host_brain/obs_main.py`
- `host_brain/app/asgi.py`
- `host_brain/app/app_factory.py`
- `host_brain/app/router_registry.py`
- `host_brain/app/obs_fastapi_app.py`
- `host_brain/app/chat_routes.py`
- `host_brain/app/chat_immune_gate.py`
- `host_brain/app/chat_handler.py`
- `host_brain/app/cortex_orchestrator.py`
- `host_brain/app/agent_runtime_shared.py`
- `host_brain/app/executive_router.py`
- `host_brain/app/executive_runtime.py`
- `host_brain/app/executive_runtime_core.py`
- `host_brain/app/input_routes.py`
- `host_brain/app/agent_routes.py`
- `host_brain/app/web_ui.py`
- `host_brain/app/obs_routes_life_body.py`
- `host_brain/audit/sqlite_ledger.py`
- `host_brain/audit/ledger_log_mixin.py`
- `host_brain/skills/registry.py`
- `host_brain/skills/loader.py`
- `host_brain/skills/executor_registry.py`

---

## 一、App 层（当前运行面）

### 1.1 当前进程与主入口

| 运行面 | 代码入口 | 说明 |
|---|---|---|
| Host Main | `host_brain/__main__.py` + `host_brain/app/asgi.py` (`app = create_app()`) | 主运行时，承载 `/v1/chat`、`/v1/host/input`、`/v1/agent/*` 及大部分业务路由。装配由 `app_factory.create_host_fastapi_app(...)` + `router_registry.register_host_routers(app)` 完成 |
| Observability Backend | `host_brain/obs_main.py` + `host_brain/app/obs_fastapi_app.py` | 独立观测后端，承载 `/obs/*`、`/api/ui/*` 与 Monitor UI 观测类接口 |
| Edge RPC | `edge_client/__main__.py` | 身体侧 RPC 能力与回调入口 |

### 1.2 Host Main 当前路由面

Host Main FastAPI 装配由三件套完成：`host_brain/app/asgi.py`（按 `app = create_app()` 暴露 ASGI app）→ `host_brain/app/app_factory.create_host_fastapi_app(...)`（装配 FastAPI 依赖与中间件）→ `host_brain/app/router_registry.register_host_routers(app)`（按顺序 include 全部业务路由）。Host Main 当前直接挂载或定义以下主路由面：

- `POST /v1/chat`
- `POST /v1/edge/events`
- `POST /v1/host/input`
- `POST /v1/agent/run`
- `GET /v1/agent/info`
- `/canon/*`
- `/scheduler/*`
- `/host-scheduler/*`
- `/tools/*`
- `/bond/*`
- `/prompt-config/*`
- `/api/ui/*`
- `/obs/*`
- `/health`
- `/ready`
- `/startup`
- `/metrics`
- `/panel`

其中 `/v1/host/input` 由 `host_brain/app/input_routes.py` 提供，`/v1/agent/*` 由 `host_brain/app/agent_routes.py` 提供，`/api/ui/*` 由 `host_brain/app/web_ui.py` 提供，`/obs/*` 在主应用中保留兼容挂载，同时也有独立 Obs Backend。

### 1.3 当前 `/v1/chat` 主链

当前聊天主链已经不再是旧的 `SceneRouter -> top_agent -> dispatch_agent`。当前代码里的常规主链是：

```text
POST /v1/chat
  -> host_brain.app.app_factory.chat_endpoint
  -> host_brain.app.chat_routes.handle_v1_chat_route
  -> host_brain.app.chat_immune_gate.evaluate_immune_gate_for_chat
  -> host_brain.app.chat_immune_gate.schedule_chat_immune_gate_event
  -> host_brain.app.chat_handler.handle_chat
  -> host_brain.app.chat_handler._unified_chat_sse_generator
  -> thalamus_agent.classify_and_compile_intent_draft
  -> Governor.evaluate_intent（实际为 `engine.evaluate_intent` 封装）
  -> cortex_orchestrator_sse_generator
  -> agent_runtime_shared._cortex_orchestrator_sse_generator_impl
  -> executive_router_sse_generator
  -> executive_runtime_sse_generator
  -> executive_runtime_core
       -> chat
       -> skill
       -> delegate
       -> plan
       -> recent_turn
       -> task_progress
```

在进入 `chat_handler.handle_chat` 之前，`chat_routes.handle_v1_chat_route` 会处理 `request_id`、immune gate 审查与异步审计记录；若 immune gate 返回 BLOCK，会直接在 HTTP 路由层返回 SSE，不再进入后续编排链。

链路中各层职责如下：

| 层 | 当前模块 | 职责 |
|---|---|---|
| Intent 收口 | `host_brain/tribe/thalamus_agent.py` | 生成 canonical intent draft |
| Governor | `host_brain/governor/decision.py` | 对 intent_draft 做审批与票据约束 |
| Cortex 编排入口 | `host_brain/app/cortex_orchestrator.py` | 统一转交到共享运行时实现 |
| 共享编排实现 | `host_brain/app/agent_runtime_shared.py` | 召回、bond、上下文、trace、后处理、SSE 贯通 |
| Executive 路由 | `host_brain/app/executive_router.py` | 路由层薄门面 |
| Executive 执行 | `host_brain/app/executive_runtime.py` + `host_brain/app/executive_runtime_core.py` | skill-first 路由、计划执行、恢复策略、chat 回落 |

### 1.4 当前外部输入与独立 Agent Loop 入口

除了 `/v1/chat`，当前 app 层还有两条高频入口：

| 入口 | 当前模块 | 说明 |
|---|---|---|
| `POST /v1/host/input` | `host_brain/app/input_routes.py` | 接收 ASR / Gateway 回流；做输入质检、免疫门控、ACK、异步回流到 Host 主链 |
| `POST /v1/agent/run` | `host_brain/app/agent_routes.py` | 独立 Agent Loop 兼容入口，可选择 Mock / Edge RPC / legacy external client，不属于 `/v1/chat` 主链，也不代表当前聊天主链具备屏幕自动执行主权 |

补充：`POST /v1/host/input` 当前只接受 `ASR` / `GATEWAY` 两类归一化输入；其他 Edge 运行时事件走 `POST /v1/edge/events`。

### 1.5 App 层当前不应再使用的口径

以下历史写法不再代表当前 app 层主入口，仅作历史留痕：

- `SceneRouter.route_sse()` 是当前场景分流中心
- `host_brain/app/graph/main_chain.py` 是聊天主编排入口
- 旧 SSE 输出中心口径
- `dispatch_agent.py` 是主调度层
- `chat_handler_complex.py` 承担复杂场景主执行

---

## 二、Audit 层（SQLiteLedger 为审计枢纽）

### 2.1 当前模块结构

| 模块 | 说明 |
|---|---|
| `host_brain/audit/sqlite_ledger.py` | Ledger 主体，对外暴露统一写入/查询方法 |
| `host_brain/audit/ledger_log_mixin.py` | LLM 节点调用、图运行追踪、LLM 路由等补充写接口 |
| `host_brain/audit/ledger_fetch_mixin.py` | 拉取明细 |
| `host_brain/audit/ledger_query_mixin.py` | 聚合查询 |
| `host_brain/audit/ledger_scheduler_mixin.py` | 调度相关查询与写入 |
| `host_brain/audit/ledger_schema.py` | DDL 与 schema 常量 |
| `host_brain/audit/migration_guard.py` + `migration_runner.py` | 迁移执行与守卫 |
| `host_brain/audit/migrations/*.sql` | 当前账本演进历史 |

### 2.2 当前账本写接口分组

当前代码中，`SQLiteLedger` 与 mixin 暴露的写接口至少覆盖以下几组事件：

| 类别 | 当前方法示例 |
|---|---|
| 主三联画 | `log_intent()` / `log_governor()` / `log_skill_exec()` |
| 模型与编排 | `log_model_control()` / `log_llm_node_call()` / `log_graph_run_trace()` / `log_llm_route()` / `log_llm_route_config_change()` |
| Canon / Memory / Vault | `log_canon_proposal()` / `log_canon_change()` / `log_memory_op()` / `log_vault_manifest()` / `log_vault_gc()` |
| Runtime I/O | `log_airlock()` / `log_channel_io()` / `log_voice_event()` / `log_retrieval_event()` / `log_vision_event()` |
| Agent / System | `log_agent_loop()` / `log_service_health()` / `log_config_snapshot()` / `log_migration()` / `log_scheduler_event()` |
| 关系与执行 | `log_bond_update()` / `log_sandbox_exec()` / `log_capability_forge_proposal()` |

### 2.3 当前契约口径

- Ledger 仍是跨模块的审计枢纽，`request_id / intent_id / event_id / evidence_ref` 仍是主要串联键。
- 表结构不是停留在旧文档里的固定“26 张表”口径，而是通过 `migrations/*.sql` 持续演化。
- 当前代码事实应优先看 `sqlite_ledger.py`、`ledger_log_mixin.py` 和迁移目录，而不是旧阶段表数统计。

---

## 三、Skills 层（动态注册 + 审计执行）

### 3.1 当前技能元数据契约

`host_brain/skills/registry.py` 中 `SkillMeta` 仍是当前技能元数据 SSOT，核心字段包括：

- `skill_name`
- `clearance`
- `risk_level`
- `scope`
- `reversible`
- `handler`
- `require_confirm`
- `family`
- `capability_tags`
- `parallelizable`
- `fallback_policy`
- `scheduler_allowed`
- `access_level`

当前 `SkillRegistry` 负责：

- `register()` / `require()` / `get()` / `all()`
- `list_skill_names()`
- `build_skill_manifest()`

因此，当前技能集合不是旧文档里那种固定数量的静态清单，而是可由注册表、MCP bridge、自定义 provider 与 Hub 安装共同扩展。

### 3.2 当前执行路径

当前技能执行至少存在两条真实路径：

| 路径 | 当前模块 | 说明 |
|---|---|---|
| 严格票据执行 | `host_brain/skills/loader.py` | `SkillLoader.execute()`：Governor 票据校验、幂等保护、审计写入、executor 分发 |
| Skill-first 运行时 | `host_brain/app/executive_runtime_core.py` | `/v1/chat` 主链的 skill route，按 skill family / executor 执行并记录 `skill_exec` |

### 3.3 当前执行器分发

`host_brain/skills/executor_registry.py` 提供当前执行器分发面，内置覆盖：

- `file`
- `notification`
- `shell`
- `browser`
- `cursor` / `pycharm` / `codex`
- `desktop`
- `memory`
- `emotion`
- `scheduler`
- `system`
- `web`
- `hub_skill`

此外，`host_brain/app/dependencies.py` 当前通过以下步骤构造技能能力面：

- `build_builtin_skill_registry()`
- `register_mcp_tools_into_skill_registry(...)`
- `register_configured_skill_providers(...)`

因此，当前代码的正确口径是“动态技能注册与执行”，而不是“固定 17 个技能实现”。

---

## 四、Observability 层（主应用兼容挂载 + 独立 Obs Backend）

### 4.1 当前运行方式

当前观测层不是单一模块，而是两层并存：

| 运行方式 | 当前模块 | 说明 |
|---|---|---|
| 主应用兼容挂载 | `host_brain/app/router_registry.py` + `host_brain/app/obs_routes_*.py` | 让主应用也能提供部分观测接口 |
| 独立 Obs Backend | `host_brain/app/obs_fastapi_app.py` | 面向 Monitor UI 的独立观测进程，支持 warm cache |

### 4.2 当前路由分组

当前观测相关接口主要分布在：

- `host_brain/app/obs_routes.py`
- `host_brain/app/obs_routes_trace.py`
- `host_brain/app/obs_routes_summary.py`
- `host_brain/app/obs_routes_life_body.py`
- `host_brain/app/obs_routes_health.py`
- `host_brain/app/obs_routes_metrics.py`
- `host_brain/app/obs_routes_sessions.py`
- `host_brain/app/obs_routes_stats*.py`
- `host_brain/app/obs_routes_sysmode.py`
- `host_brain/app/web_ui.py`

独立 Obs Backend 还会在启动时拉起：

- runtime snapshot warm cache
- trace feed warm cache
- insights summary warm cache
- digital life overview warm cache
- life body warm cache

### 4.3 与 Monitor UI 的关系

当前 Monitor UI 前端位于：

- `host_brain/app/monitor_ui/src/*`
- `host_brain/app/static/monitor/*`

因此，当前 observability 层的正确理解是：

- 后端接口分布在 `obs_routes*`
- `/api/ui/*` 聚合 / 搜索接口由 `web_ui.py` 提供
- 独立进程入口在 `obs_fastapi_app.py`
- 前端消费层在 `monitor_ui/`

---

## 五、结论

当前 `host_brain` 应用层的主事实是：

1. `/v1/chat` 主链已经收敛到 `chat_routes.handle_v1_chat_route -> chat_handler.handle_chat -> thalamus_agent -> Governor -> cortex_orchestrator -> agent_runtime_shared -> executive_router -> executive_runtime -> executive_runtime_core`；最终文本作者为 `host_brain/persona/surface_main_chain.py`。
2. Audit 以 `SQLiteLedger` 为枢纽，但表结构与写接口已持续演进，不应再复用旧表数口径。
3. Skills 以 `SkillRegistry + SkillLoader + executor_registry` 为当前执行契约，且来源是动态扩展的。
4. Observability 采用“主应用兼容挂载 + 独立 Obs Backend + Monitor UI”三层结构。
