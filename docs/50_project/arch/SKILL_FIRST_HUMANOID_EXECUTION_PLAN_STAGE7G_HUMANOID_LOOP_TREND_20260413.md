受众：PROJECT
标题：Skill-First Humanoid Execution Plan Stage 7G (2026-04-13)

# Skill-First Humanoid Execution Plan Stage 7G (2026-04-13)

## 目标

- 在 Stage7F 静态闭环指标基础上补齐“时序生命流”视角。
- 让前端能看到闭环触达能力的波动与退化，而不是只看单点快照。

## 范围

- 后端新增趋势端点：
  - `GET /obs/input/humanoid-loop-trend`
- 前端总览页接入趋势曲线：
  - 闭环率（Closed Loop）
  - ACK 触达率（ACK Touch）
  - 进度触达率（Progress Touch）

## 后端设计

- 输入：`window_minutes`, `interval_minutes`
- 数据源：`voice_event_log` 中 InputDispatch 生命周期事件（仅 `source=GATEWAY`）。
- 聚合策略：
  - 先按 `request_id` 归并事件，得到请求级 flags（ACK/进度/偏航/完成/失败）
  - 再按请求锚点时间（优先 `INPUT_ACCEPTED` 时间）映射到时间桶
  - 输出每桶计数与比率序列（completion/closed_loop/ack_touch/progress_touch/deviation_recovery）

## 前端设计

- `DigitalLifeOverview` 并行拉取 trend 端点，窗口粒度与总览时间窗一致：
  - `<=1h -> 5m`
  - `<=6h -> 30m`
  - `<=24h -> 60m`
  - `>24h -> 360m`
- 在“人体闭环健康（Gateway）”卡区下新增趋势图，强化“生命流”连续性。

## 验收标准

1. 趋势端点能稳定返回统一长度的时间序列。
2. 非 Gateway 事件不会污染趋势统计。
3. 前端构建通过，趋势图在有数据时可渲染、无数据时不阻塞页面。
