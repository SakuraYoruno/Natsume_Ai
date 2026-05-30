受众：CODEX
标题：数字生命 Phase 2 留痕（HippocampusAgent 接口化）

# 数字生命 Phase 2 留痕（HippocampusAgent 接口化）
日期：2026-04-08

## 对齐文档项
- 对齐 `TRIBE_AGENT_REFACTOR_PLAN_20260408.md` Phase 2：
  - 将检索入口统一到 `HippocampusAgent.recall()`
  - `chat_context_builder.py` 通过 Hippocampus 接口构建 retrieval rows

## 本轮改动
- `host_brain/tribe/hippocampus_agent.py`
  - 从壳层扩展为可执行服务：
    - `recall(...)`：统一承接 retrieval orchestrator 调用
    - `build_prompt_retrieval_rows(...)`：统一承接 prompt retrieval rows 投影
  - 新增单例接口：`get_hippocampus_agent()` / `set_hippocampus_agent()`
- `host_brain/app/chat_retrieval_helpers.py`
  - `_run_retrieval(...)` 改为调用 `get_hippocampus_agent().recall(...)`
- `host_brain/app/chat_context_builder.py`
  - retrieval rows 构建改为通过 `_build_retrieval_rows(...)`
  - `_build_retrieval_rows(...)` 优先走 Hippocampus 接口，失败时走本地 fallback，保证主链路稳定
- `host_brain/tribe/__init__.py`
  - 导出 `get_hippocampus_agent` / `set_hippocampus_agent`

## 新增测试
- `tests/unit/test_hippocampus_agent.py`
- `tests/unit/test_chat_retrieval_hippocampus_integration.py`
- `tests/unit/test_chat_context_builder_hippocampus_integration.py`

## 验证结果
- `pytest tests/unit/test_hippocampus_agent.py tests/unit/test_chat_retrieval_hippocampus_integration.py tests/unit/test_chat_context_builder_hippocampus_integration.py tests/unit/test_chat_retrieval_helpers.py tests/unit/test_chat_context_builder_helpers_extra.py -q`
  - 结果：`39 passed`
- `pytest tests/unit/test_tribe_registry.py tests/unit/test_tribe_handoff.py tests/unit/test_dispatch_agent_tribe_handoff.py tests/unit/test_tribe_event_bus.py tests/unit/test_tribe_state_registry.py tests/integration/test_internal_tribe_routes.py tests/integration/test_obs_tribe_events.py -q`
  - 结果：`16 passed`

## 说明
- 本轮为“接口统一”与“主链路接线”改造，优先确保行为一致与可回退。
- 下一步可继续推进 Phase 3：LimbicAgent 事件化（先解耦 `inner_world.py -> evolution_orchestrator` 直连）。
