# QGBT240413R 进度询问强制纠偏（task_progress）

日期：2026-04-13

## 问题

短批次中出现了 `“现在进度怎么样了” -> chat` 的误判（由 LLM 首判导致），与预期的 `task_progress` 不一致。

## 修复

文件：`host_brain/app/input_routes.py`

- 在 `_apply_gateway_force_override()` 新增优先规则：
  - 命中进度询问模式时，强制输出：
    - `route=task_progress`
    - `route_source=override`
    - `reason_code=FORCE_OVERRIDE_TASK_PROGRESS`

## 测试

- 单测新增：
  - `test_build_gateway_intent_draft_force_overrides_task_progress_query`
- 单测结果：
  - `pytest tests/unit/test_input_routes_worker.py -q`
  - `25 passed`

## 运行冒烟

- request_id：`req_92a5d30ec890491f83883fbea1aacf5f`
- query：`现在进度怎么样了`
- `INPUT_ROUTE_DECIDED`：
  - `route=task_progress`
  - `route_source=rule`
  - `llm_route_used=false`

说明：本次实跑直接命中了规则路径（无需触发 override），与目标行为一致；当未来 LLM 误判时，将由新增强制纠偏兜底。
