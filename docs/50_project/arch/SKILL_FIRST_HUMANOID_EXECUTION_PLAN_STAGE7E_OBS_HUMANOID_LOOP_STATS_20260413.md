# Skill-First Humanoid Execution Plan Stage 7E (2026-04-13)

## 目标

- 将 Stage7A~7D 的交互闭环能力量化为可观测指标，支持“是否真的像人一样在闭环工作”。
- 新增统一统计端点，直接输出 ACK/进度/偏航恢复/闭环率。

## 端点设计

- 新增：`GET /obs/input/humanoid-loop-stats`
- 数据来源：`voice_event_log` 中的 `INPUT_*` 生命周期事件（仅 `source=GATEWAY`）。

## 指标定义

- `total_gateway_requests`
- `completed_requests` / `failed_requests`
- `ack_touched_requests` / `ack_confirmed_requests`
- `progress_touched_requests`
- `deviation_detected_requests` / `deviation_recovered_requests`
- `closed_loop_requests`

对应比率：

- `completion_rate`
- `ack_touch_rate`
- `progress_touch_rate`
- `deviation_recovery_rate`
- `closed_loop_rate`

## 闭环判定规则

单请求满足以下条件记为闭环：

1. 已完成（`INPUT_COMPLETED`）
2. 有“人化交互触达”：
   - ACK 触达（`INPUT_ACK_PENDING`/`INPUT_ACKED`）或
   - 进度触达（`INPUT_PROGRESS_FASTPATH` 成功 / `INPUT_PROGRESS_PUSHED`）或
   - 偏航检测触发（`INPUT_DEVIATION_DETECTED`）
3. 若触发偏航检测，则要求已恢复（`INPUT_DEVIATION_RECOVERED`）

## 实施说明

- 复用已有 `obs_routes_core` 的输入事件识别与 source 归一化逻辑。
- 与既有 `/obs/input/dispatch-stats` 并存，不替代原有吞吐与延迟统计。
- 输出 `stage_counts` 用于前端分阶段可视化。

## 风险与缓解

- 风险：历史日志缺字段导致统计偏低。
- 缓解：按 event_type 与 payload source 双重兜底归一化。
- 风险：闭环规则过严导致低估。
- 缓解：闭环定义显式可读，后续可按业务反馈调参。
