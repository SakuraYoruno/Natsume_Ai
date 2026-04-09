# DIGITAL_LIFE_P7_ORGAN_LOOP_CLOSURE_BATCH_A_20260409

## 目标
- 将“器官闭环”从概念推进到可运行链路：补齐 `EndocrineAgent` 与 `ImmuneAgent`，并接入主入口与运行时观测。

## 本批完成项
- 新增 `host_brain/tribe/endocrine_agent.py`
  - 提供 `resolve_endocrine_state(...)`，统一承接内分泌状态派生。
  - 提供 `resolve_vital_signs(...)`、`export_runtime_state()`，支持观测与门控基线。
- 新增 `host_brain/tribe/immune_agent.py`
  - 提供 `inspect_text(...)` 风险扫描与门禁决策（`ALLOW/REPORT/BLOCK`）。
  - 默认“报告模式”运行（`HOST_IMMUNE_GATE_BLOCKING=false`），避免误拦截生产流量。
- Tribe 注册扩展
  - `host_brain/tribe/registry.py` 新增家族：`endocrine`、`immune`。
  - 注册默认 agent：`endocrine_agent`、`immune_agent`。
- 入口接入
  - `host_brain/app/fastapi_app.py`：`/v1/chat` 增加 immune 前置检查；命中 `BLOCK` 时返回单帧 `COMMIT REJECT`（`IMMUNE_BLOCKED_HIGH_RISK`）。
  - `host_brain/app/input_routes.py`：`/v1/host/input` 增加 immune 前置检查；命中 `BLOCK` 直接拒绝入队。
- 观测接入
  - `host_brain/app/http_hooks_runtime_helpers.py`：`build_digital_life_runtime_snapshot` 新增 `immune_state` 与 `slot_status.immune_state`。
- 依赖收敛
  - `host_brain/app/dependencies.py:get_endocrine_state()` 改为通过 `EndocrineAgent` 统一分发。

## 新增/更新测试
- 新增
  - `tests/unit/test_endocrine_agent.py`
  - `tests/unit/test_immune_agent.py`
  - `tests/unit/test_fastapi_app_immune_gate.py`
- 更新
  - `tests/unit/test_tribe_registry.py`
  - `tests/unit/test_input_routes_dispatch.py`
  - `tests/unit/test_http_hooks_runtime_helpers.py`

## 验证记录
- `pytest -q tests/unit/test_endocrine_agent.py tests/unit/test_immune_agent.py tests/unit/test_tribe_registry.py tests/unit/test_input_routes_dispatch.py tests/unit/test_fastapi_app_immune_gate.py tests/unit/test_http_hooks_runtime_helpers.py -k "immune or endocrine or tribe_registry or receives_input_rejects_when_immune_gate_blocks or build_digital_life_runtime_snapshot_collects_core_slots"`  
  - 结果：`13 passed, 8 deselected`
- `pytest -q tests/contract/test_http_chat_contract.py`
  - 结果：`12 passed`
- `pytest -q tests/unit/test_input_routes_smoke.py`
  - 结果：`3 passed`
- `pytest -q tests/unit/test_http_hooks_runtime_helpers.py`
  - 结果：`6 passed`

## 下一批建议（Batch B）
- 将 `ImmuneAgent` 规则从静态代码迁移到可运营配置（DB + WebUI）。
- 把 immune 决策写入 trace timeline（与 query-planner / dispatch / sub-agent 链路对齐）。
- 在 `scheduler` 引入 immune-aware 主动任务抑制策略（只影响主动触发，不影响被动会话）。
