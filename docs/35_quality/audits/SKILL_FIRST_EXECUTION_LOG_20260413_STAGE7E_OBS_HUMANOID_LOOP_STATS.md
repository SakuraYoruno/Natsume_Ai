受众：CODEX
标题：Skill-First Execution Log 2026-04-13 Stage 7E

# Skill-First Execution Log 2026-04-13 Stage 7E

## Scope

- Objective: 新增 Stage7A~7D 的可观测聚合端点，量化人体化交互闭环效果。
- Endpoint: `GET /obs/input/humanoid-loop-stats`

## Implemented Changes

- `host_brain/app/obs_routes_stats.py`
  - 新增响应模型：
    - `InputHumanoidLoopStageItem`
    - `InputHumanoidLoopStatsResponse`
  - 新增端点：
    - `get_input_humanoid_loop_stats(window_minutes=...)`
  - 聚合逻辑：
    - 从 `voice_event_log` 提取 Gateway `INPUT_*` 事件
    - 按 request_id 汇总 ACK / 进度触达 / 偏航检测与恢复
    - 计算闭环率与各阶段计数
- `tests/integration/test_obs_input_humanoid_loop_stats.py`
  - 新增端到端统计验证（含 Gateway/ASR 混合数据过滤）。

## Test Evidence

- Command:
  - `pytest -q tests/integration/test_obs_input_humanoid_loop_stats.py tests/integration/test_obs_input_dispatch_stats.py`
- Result:
  - `3 passed in 0.76s`
- Command:
  - `pytest -q tests/unit/test_input_routes_dispatch.py tests/unit/test_input_routes_worker.py tests/unit/test_chat_agent_quick_ack.py tests/integration/test_obs_input_humanoid_loop_stats.py tests/integration/test_obs_input_dispatch_stats.py`
- Result:
  - `32 passed in 1.13s`

## Behavior Verification

- 新端点可正确输出：
  - Gateway 请求总数
  - ACK/进度触达率
  - 偏航恢复率
  - 闭环完成率
- 非 Gateway 输入（如 ASR）不会污染闭环统计。

## Follow-up

- 建议前端新增“人体闭环健康卡”：
  - 闭环率、偏航恢复率、ACK 触达率、进度触达率。
- 建议补一条 24h 窗口的趋势图端点（按小时聚合 closed_loop_rate）。
