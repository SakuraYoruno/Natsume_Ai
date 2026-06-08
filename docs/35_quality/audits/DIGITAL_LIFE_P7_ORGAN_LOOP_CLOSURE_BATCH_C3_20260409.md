受众：CODEX
标题：DIGITAL_LIFE_P7_ORGAN_LOOP_CLOSURE_BATCH_C3_20260409

# DIGITAL_LIFE_P7_ORGAN_LOOP_CLOSURE_BATCH_C3_20260409

## 目标
- 把 immune 门禁状态与 scheduler 健康快照并排展示，形成“调度状态 + 免疫状态”一体观测。

## 本批完成项
- `scheduler_routes` 增加 `immune_gate_state` 聚合：
  - `get_scheduler_health_snapshot_safe()` 返回中新增 `immune_gate_state`。
  - `/scheduler/health` 响应中新增 `immune_gate_state`。
  - 字段包含：`enabled`、`blocking_enabled`、阈值、模板规则数量、命中计数、最近一次判定信息。
- 文件：
  - `host_brain/scheduler/scheduler_routes.py`

## 测试
- 新增：
  - `tests/unit/test_scheduler_routes_immune_gate_state.py`
- 覆盖：
  - 无 provider 降级场景下的 safe snapshot。
  - HTTP `/scheduler/health` 响应带 immune 状态。
- 相关 scheduler 与 obs 回归测试通过。
