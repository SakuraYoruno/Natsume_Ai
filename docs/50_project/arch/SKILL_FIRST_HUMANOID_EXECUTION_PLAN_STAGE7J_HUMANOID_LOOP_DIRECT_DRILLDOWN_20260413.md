受众：PROJECT
标题：Skill-First Humanoid Execution Plan Stage 7J (2026-04-13)

# Skill-First Humanoid Execution Plan Stage 7J (2026-04-13)

## 目标

- 将 Stage7I 的“排查步骤”继续推进为“异常请求直达”。
- 让闭环健康面板在出现异常时，直接给出最近代表性 request 的 Trace 跳转入口。

## 范围

- 后端增强：
  - 在 `GET /obs/input/humanoid-loop-remediation` 中新增 `representative_requests[]`
- 前端增强：
  - 在“闭环自愈建议”面板展示“快速钻取请求”快捷入口

## 后端设计

- 以告警所评估的最新有效 bucket 为范围，重新归并该桶内 Gateway 请求。
- 筛选优先级：
  - failed request
  - completed but not closed-loop
  - low-touch request
- 输出字段：
  - `request_id`
  - `category`
  - `reason`
  - `target_path=/trace/{request_id}`

## 验收标准

1. remediation 响应中可返回代表性异常 request 列表。
2. 前端能够直接跳转到单请求 Trace 页面。
3. 代表请求优先落在最近异常桶内，而不是窗口内任意请求。
