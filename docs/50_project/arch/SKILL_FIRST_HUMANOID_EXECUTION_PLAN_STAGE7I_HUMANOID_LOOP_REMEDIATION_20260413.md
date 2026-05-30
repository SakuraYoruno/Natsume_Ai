受众：PROJECT
标题：Skill-First Humanoid Execution Plan Stage 7I (2026-04-13)

# Skill-First Humanoid Execution Plan Stage 7I (2026-04-13)

## 目标

- 将 Stage7H 的“异常检测”继续推进为“异常后的下一步行动”。
- 让人体闭环模块不仅能发现问题，还能直接给出结构化排查顺序与跳转入口。

## 范围

- 后端新增：
  - `GET /obs/input/humanoid-loop-remediation`
- 前端新增：
  - 人体闭环健康区下方“闭环自愈建议”面板
  - 每条建议包含优先级、说明、证据码、跳转目标、operator hint

## 后端设计

- 输入：`window_minutes`, `interval_minutes`, `min_bucket_requests`
- 依赖：复用 `GET /obs/input/humanoid-loop-alerts`
- 输出：
  - `status`, `severity`, `summary`
  - `primary_focus`
  - `next_review_hint`
  - `steps[]`

## 规则映射

- 全局步骤：
  - 先看 Gateway 最近窗口健康
  - 再钻取全链路 Trace
  - 回告警中心做原因收敛
- 告警专项步骤：
  - `ACK_TOUCH_LOW` -> 检查 `HOST_GATEWAY_IMMEDIATE_ACK_*`
  - `PROGRESS_TOUCH_LOW` -> 检查 `HOST_GATEWAY_PROGRESS_FASTPATH_*`
  - `DEVIATION_RECOVERY_LOW` -> 检查 `HOST_GATEWAY_DEVIATION_*`
- 无样本：
  - 先补齐真实 Gateway 请求样本

## 验收标准

1. remediation 端点在异常场景下返回非空结构化步骤。
2. 每个步骤都具备明确 target_path 或 operator hint。
3. 前端能直接显示这些步骤，并通过跳转按钮进入对应排查页面。
