受众：PROJECT
标题：Skill-First Humanoid Execution Plan Stage 7F (2026-04-13)

# Skill-First Humanoid Execution Plan Stage 7F (2026-04-13)

## 目标

- 将 Stage7A~7E 的“人体化执行闭环”能力显式接入数字生命体总览页，避免闭环能力只存在于后端观测端点。
- 让前端从“纯监控面板”进一步靠近“生命体体征面板”，突出 ACK、进度触达、偏航修复、闭环完成四段流转。

## 范围

- API 层新增：
  - `fetchInputHumanoidLoopStats(params)` -> `GET /obs/input/humanoid-loop-stats`
- 总览页接入：
  - `DigitalLifeOverview` 在现有 `Promise.all` 里并行拉取人体闭环统计
  - 增加“人体闭环健康（Gateway）”区块
  - 展示核心比率、关键计数、阶段分布与失败告警

## 数据契约

- 来源端点：`/obs/input/humanoid-loop-stats`
- 关键字段：
  - 比率：`closed_loop_rate`、`ack_touch_rate`、`progress_touch_rate`、`deviation_recovery_rate`
  - 计数：`total_gateway_requests`、`closed_loop_requests`、`failed_requests`、`ack_confirmed_requests`、`progress_touched_requests`
  - 分布：`stage_counts[]`

## 交互与视觉策略

- 保持与总览页现有 `LifeSection + MetricCard + NoticeBanner + KeyValueRow` 体系一致，不引入风格割裂组件。
- 无数据场景展示空态卡，避免“0 值误判为健康”。
- 失败请求存在时给出提示条，辅助快速定位闭环质量下降。

## 验收标准

1. 页面能在窗口切换和自动轮询时同步刷新人体闭环数据。
2. 当端点无数据或失败时不影响总览页其他模块渲染。
3. 前端生产构建通过，无新增编译错误。

## 风险与缓解

- 风险：短窗口样本少导致比率波动大。
- 缓解：同时展示“比率 + 分子/分母计数”，降低误读。

- 风险：观测端点暂不可用造成页面空白。
- 缓解：按模块容错（`catch(() => null)`）+ 空态卡兜底。
