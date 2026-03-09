# 需求文档：清理 fastapi_app.py 重复代码，确保单一职责

## 引言

`host_brain/app/fastapi_app.py`（150KB，3700行）是项目的原始 FastAPI 入口文件。在拆分工作完成后，各子模块已承担了具体职责，但 `fastapi_app.py` 中仍保留了大量与子模块**完全重复**的代码，违反了单一职责原则（SRP）。

### 当前重复代码全景

经过逐文件对比，发现以下 5 类重复：

#### 1. Schema 类重复（与 `schemas.py` 完全重复）
`fastapi_app.py` 第 98–186 行自行定义了以下 9 个 Pydantic 模型，与 `schemas.py` 中的定义**逐字相同**：
- `HealthResponse`、`ReadinessResponse`、`ObservabilityRequest`、`ObservabilityResponse`
- `ChatRequest`、`ChatResponse`、`MetricsResponse`
- `EdgeEventsRequest`、`EdgeEventsResponse`、`ErrorResponse`

#### 2. 依赖注入函数重复（与 `dependencies.py` 完全重复）
`fastapi_app.py` 第 1252–1530 行自行定义了以下 11 个依赖注入函数，与 `dependencies.py` 中的实现**逻辑完全相同**：
- `get_ledger()`、`get_episodic_store()`、`get_retrieval_pipeline()`、`get_graph_runtime()`
- `get_obs_metrics_collector()`、`get_tool_catalog()`、`get_tool_broker()`
- `get_bond_stage_engine()`、`get_skill_registry()`、`get_policy_cache()`、`get_intent_engine()`
- 同时还重复定义了 11 个全局单例变量（`_ledger`、`_intent_engine` 等）

#### 3. 健康检查路由重复（与 `health_routes.py` 完全重复）
`fastapi_app.py` 第 1636–1864 行自行定义了以下 7 个路由处理函数，与 `health_routes.py` 中的实现**逻辑完全相同**：
- `health_check()`（`GET /health`）、`environment_readiness()`（`GET /ready`）
- `obs_metrics()`（`GET /obs/metrics`）、`mcp_health_check()`（`GET /health/mcp`）
- `canon_sync_health_check()`（`GET /health/canon_sync`）、`request_trace()`（`POST /observability/trace`）
- `startup_report()`（`GET /startup`）
- 同时还重复定义了 `_resolve_node_inventory_payload()` 辅助函数

#### 4. 提示词工具函数重复（与 `prompt_builder.py` 完全重复）
`fastapi_app.py` 第 509–870 行自行定义了以下函数，与 `prompt_builder.py` 中的实现**逻辑完全相同**：
- `_truncate_for_prompt()`、`_contains_kana()`、`_contains_cjk_ideograph()`、`_contains_cjk_or_kana()`
- `_infer_render_target_lang()`、`_repair_utf8_latin1_mojibake()`
- `_render_host_generation_prompt()`、`_extract_node_runtime_payload()`、`_extract_node_narrative_text()`
- `_render_node_generation_prompt()`（第 614 行和第 2210 行**两处定义**，互相重复）
- `_extract_json_object_from_text()`、`_coerce_gateway_payload_json_text()`、`_extract_langchain_content_text()`

#### 5. 场景常量和分类器重复（与 `graph_config_builder.py` 完全重复）
`fastapi_app.py` 第 351–450 行自行定义了以下常量和函数，与 `graph_config_builder.py` 中的定义**完全相同**：
- `CHAT_DIRECT_NODE_ID`、`CHAT_GRAPH_NODE_ORDER`、`CHAT_RENDER_NODE_ID`、`CHAT_INTENT_SOURCE_NODE_ID`
- `SCENE_CHAT`、`SCENE_EXEC_INTENT`、`SCENE_WORK_TRANSLATE`、`SCENE_WORK_CODE`、`SCENE_VISION_UI`
- `_PIC_VISION_UI_KEYWORDS`、`_PIC_WORK_CODE_KEYWORDS`、`_PIC_WORK_TRANSLATE_KEYWORDS`、`_PIC_EXEC_INTENT_KEYWORDS`
- `pre_intent_classifier()`、`_resolve_scene_node_sequence()`

### fastapi_app.py 应保留的独有内容

清理后，`fastapi_app.py` 应只保留以下**真正独有**的内容：
- `app` FastAPI 实例 + `lifespan` 生命周期 + 中间件注册 + 路由注册（`include_router`）
- `_RuntimeMetrics` 类 + `_metrics` 全局实例（运行时指标，仅用于 `/metrics` 端点）
- `global_exception_handler` + `add_request_id_and_audit` 中间件函数
- `POST /v1/chat` 路由（调用 `chat_handler.handle_chat`）
- `GET /v1/chat/stream` 路由（调用 `chat_stream_handler.handle_chat_stream`）
- `GET /metrics` 路由（使用 `_RuntimeMetrics`）
- `GET /panel` 路由（面板重定向）
- `POST /v1/edge/events` 路由（`receive_edge_events`）
- `GET /` 路由（根路由重定向）

---

## 需求

### 需求 1：删除 fastapi_app.py 中重复的 Schema 类，改为从 schemas.py 导入

**用户故事：** 作为一名开发者，我希望 `fastapi_app.py` 中的 HTTP 契约模型统一由 `schemas.py` 提供，以便消除重复定义，确保 Schema 的单一来源。

#### 验收标准

1. WHEN 清理完成后 THEN `fastapi_app.py` SHALL 不再包含 `HealthResponse`、`ReadinessResponse`、`ObservabilityRequest`、`ObservabilityResponse`、`ChatRequest`、`ChatResponse`、`MetricsResponse`、`EdgeEventsRequest`、`EdgeEventsResponse`、`ErrorResponse` 的类定义。
2. WHEN 清理完成后 THEN `fastapi_app.py` SHALL 从 `host_brain.app.schemas` 导入上述所有 Schema 类。
3. IF 测试文件或其他模块从 `fastapi_app` 导入这些 Schema THEN 这些导入 SHALL 继续正常工作（`fastapi_app` 重新导出这些符号）。

---

### 需求 2：删除 fastapi_app.py 中重复的依赖注入函数，改为从 dependencies.py 导入

**用户故事：** 作为一名开发者，我希望所有全局单例的依赖注入函数统一由 `dependencies.py` 管理，以便消除重复的全局状态和工厂函数。

#### 验收标准

1. WHEN 清理完成后 THEN `fastapi_app.py` SHALL 不再包含 `get_ledger`、`get_episodic_store`、`get_retrieval_pipeline`、`get_graph_runtime`、`get_obs_metrics_collector`、`get_tool_catalog`、`get_tool_broker`、`get_bond_stage_engine`、`get_skill_registry`、`get_policy_cache`、`get_intent_engine` 的函数定义。
2. WHEN 清理完成后 THEN `fastapi_app.py` SHALL 不再包含 `_ledger`、`_intent_engine`、`_skill_registry`、`_policy_cache`、`_tool_catalog`、`_tool_broker`、`_bond_stage_engine`、`_episodic_store`、`_retrieval_pipeline`、`_obs_metrics_collector`、`_graph_runtime` 等全局单例变量定义。
3. WHEN 清理完成后 THEN `fastapi_app.py` SHALL 从 `host_brain.app.dependencies` 导入上述所有依赖注入函数。
4. IF 测试文件或其他模块从 `fastapi_app` 导入这些函数 THEN 这些导入 SHALL 继续正常工作（`fastapi_app` 重新导出这些符号）。
5. WHEN `lifespan` 函数中调用 `get_ledger()` 等函数时 THEN SHALL 使用从 `dependencies` 导入的版本。

---

### 需求 3：删除 fastapi_app.py 中重复的健康检查路由处理函数，改为注册 health_routes.py 的 router

**用户故事：** 作为一名开发者，我希望健康检查路由统一由 `health_routes.py` 的 `router` 提供，以便消除重复的路由处理逻辑。

#### 验收标准

1. WHEN 清理完成后 THEN `fastapi_app.py` SHALL 不再包含 `health_check`、`environment_readiness`、`obs_metrics`、`mcp_health_check`、`canon_sync_health_check`、`request_trace`、`startup_report` 的路由处理函数定义。
2. WHEN 清理完成后 THEN `fastapi_app.py` SHALL 不再包含 `_resolve_node_inventory_payload`、`_load_json_file` 的辅助函数定义（已在 `health_routes.py` 中）。
3. WHEN 清理完成后 THEN `fastapi_app.py` SHALL 通过 `app.include_router(health_router)` 注册 `health_routes.py` 的 `router`。
4. WHEN 清理完成后 THEN `GET /health`、`GET /ready`、`GET /obs/metrics`、`GET /health/mcp`、`GET /health/canon_sync`、`POST /observability/trace`、`GET /startup` 端点 SHALL 仍然可以正常访问。

---

### 需求 4：删除 fastapi_app.py 中重复的提示词工具函数，改为从 prompt_builder.py 导入

**用户故事：** 作为一名开发者，我希望所有提示词构建和文本处理工具函数统一由 `prompt_builder.py` 提供，以便消除重复实现。

#### 验收标准

1. WHEN 清理完成后 THEN `fastapi_app.py` SHALL 不再包含 `_truncate_for_prompt`、`_contains_kana`、`_contains_cjk_ideograph`、`_contains_cjk_or_kana`、`_infer_render_target_lang`、`_repair_utf8_latin1_mojibake`、`_render_host_generation_prompt`、`_extract_node_runtime_payload`、`_extract_node_narrative_text`、`_render_node_generation_prompt`（两处）、`_extract_json_object_from_text`、`_coerce_gateway_payload_json_text`、`_extract_langchain_content_text` 的函数定义。
2. WHEN 清理完成后 THEN `fastapi_app.py` 中调用这些函数的代码 SHALL 改为使用从 `host_brain.app.prompt_builder` 导入的版本。
3. IF `fastapi_app.py` 中存在两处 `_render_node_generation_prompt` 定义 THEN 清理后 SHALL 只保留 `prompt_builder.py` 中的一处权威定义。

---

### 需求 5：删除 fastapi_app.py 中重复的场景常量和分类器，改为从 graph_config_builder.py 导入

**用户故事：** 作为一名开发者，我希望场景常量和预分类器统一由 `graph_config_builder.py` 提供，以便消除重复定义。

#### 验收标准

1. WHEN 清理完成后 THEN `fastapi_app.py` SHALL 不再包含 `CHAT_DIRECT_NODE_ID`、`CHAT_GRAPH_NODE_ORDER`、`CHAT_RENDER_NODE_ID`、`CHAT_INTENT_SOURCE_NODE_ID`、`SCENE_CHAT`、`SCENE_EXEC_INTENT`、`SCENE_WORK_TRANSLATE`、`SCENE_WORK_CODE`、`SCENE_VISION_UI`、`_PIC_*_KEYWORDS`、`pre_intent_classifier`、`_resolve_scene_node_sequence` 的定义。
2. WHEN 清理完成后 THEN `fastapi_app.py` SHALL 从 `host_brain.app.graph_config_builder` 导入上述常量和函数。
3. IF 测试文件从 `fastapi_app` 导入 `pre_intent_classifier`、`SCENE_CHAT`、`SCENE_WORK_CODE` 等符号 THEN 这些导入 SHALL 继续正常工作（`fastapi_app` 重新导出这些符号）。

---

### 需求 6：确保清理后 fastapi_app.py 的向后兼容性

**用户故事：** 作为一名开发者，我希望清理后所有依赖 `fastapi_app.py` 的外部文件（测试、路由模块等）无需修改即可继续运行，以便降低重构风险。

#### 验收标准

1. WHEN 清理完成后 THEN `fastapi_app.py` SHALL 通过重新导出（`from ... import ... as ...` 或直接 `from ... import ...`）保持所有被外部引用的公共符号可访问。
2. WHEN 清理完成后 THEN `host_brain/__main__.py` 中的 `from host_brain.app.fastapi_app import app` SHALL 继续正常工作。
3. WHEN 清理完成后 THEN `host_brain/app/obs_routes.py` 和 `host_brain/app/web_ui.py` 中的 `from host_brain.app.fastapi_app import get_ledger` SHALL 继续正常工作。
4. WHEN 清理完成后 THEN 所有测试文件中从 `fastapi_app` 导入的符号（`app`、`ChatResponse`、`ChatRequest`、`get_intent_engine`、`pre_intent_classifier`、`_render_node_generation_prompt`、`SCENE_CHAT`、`SCENE_WORK_CODE` 等）SHALL 继续正常工作。
5. WHEN 清理完成后 THEN `fastapi_app.py` 的文件大小 SHALL 显著减小（预计从 150KB 降至 30KB 以内）。
