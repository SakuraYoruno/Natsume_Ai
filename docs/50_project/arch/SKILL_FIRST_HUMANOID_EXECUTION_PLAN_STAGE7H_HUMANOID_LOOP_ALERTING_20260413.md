受众：PROJECT
标题：Skill-First Humanoid Execution Plan Stage 7H (2026-04-13)

# Skill-First Humanoid Execution Plan Stage 7H (2026-04-13)

## 目标

- 在 Stage7G 趋势可视化基础上补齐“自动判异”能力。
- 让人体闭环趋势不仅能看曲线，还能自动标注健康/退化/严重异常状态。

## 范围

- 后端新增告警端点：
  - `GET /obs/input/humanoid-loop-alerts`
- 前端总览接入：
  - 趋势线阈值高亮（Closed Loop 线色随健康状态变化）
  - 闭环趋势告警条（NoticeBanner）
  - 告警标签（code + value）

## 后端告警规则

- 基于最新满足最小样本量的时间桶评估：
  - `completion_rate` 低于阈值
  - `closed_loop_rate` 低于阈值
  - `ack_touch_rate` 低于阈值
  - `progress_touch_rate` 低于阈值
  - `failed_rate` 高于阈值
  - `deviation_recovery_rate` 低于阈值
- 附加突降检测：
  - 与历史基线桶对比，若闭环率跌幅超过阈值则触发 `CLOSED_LOOP_DROP`

## 输出契约

- `status`: `healthy | degraded | critical | no_data`
- `alerts[]`: 结构化告警（code/severity/title/detail/metric/value/threshold/direction）
- 同时返回最新桶关键比率，方便前端直接渲染。

## 验收标准

1. 趋势异常时能输出结构化告警，并给出明确 summary。
2. 非 Gateway 数据不参与告警评估。
3. 前端在异常状态下出现高亮与告警提示，健康状态下保持简洁。
