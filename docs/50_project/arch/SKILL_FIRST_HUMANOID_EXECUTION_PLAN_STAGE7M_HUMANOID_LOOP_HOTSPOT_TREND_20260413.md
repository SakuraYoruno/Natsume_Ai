# Skill-First Humanoid Execution Plan Stage 7M (2026-04-13)

## 目标

- 在 Stage7L 的“系统级病灶热区”基础上补齐时间轴。
- 让总览不仅知道“哪里是主病灶”，还知道“主病灶是最近突然升温，还是持续累积出来的”。

## 范围

- 后端新增：
  - `GET /obs/input/humanoid-loop-hotspot-trend`
- 前端增强：
  - `DigitalLifeOverview` 新增“病灶演化趋势”面板

## 后端设计

- 输入来源：
  - `voice_event_log`
  - `governor_log`
  - `error_log`
- 计算路径：
  - 先按 request 聚合 Gateway 事件
  - 再按 bucket 归并异常 request
  - 将 bucket 内异常 request 映射到器官系统与根因
  - 输出 top organ systems 的时间序列

## 输出结构

- `timeseries_labels`
- `timeseries_total_requests`
- `timeseries_abnormal_requests`
- `series[]`
  - `organ_system`
  - `organ_label`
  - `request_counts[]`
- `dominant_buckets[]`
  - 当前 bucket 主病灶器官
  - 当前 bucket 主根因
  - 当前 bucket 异常数与占比

## 验收标准

1. 热区趋势端点能稳定返回器官级异常时间序列。
2. 前端总览能看到主病灶升温曲线和最近几个 bucket 的主病灶摘要。
3. Stage7L 当前热区与 Stage7M 最新 bucket 的主病灶口径一致。
