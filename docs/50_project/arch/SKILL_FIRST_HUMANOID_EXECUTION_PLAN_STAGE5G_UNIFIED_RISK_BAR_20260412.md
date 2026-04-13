# Skill-First 类人体架构执行补充方案（P5G-Unified Risk Bar）日期：2026-04-12
状态：`COMPLETED`

## 1. 本轮目标
- 将 Forge 风险胶囊升级为跨页统一风险条。
- 支持在统一风险条上直接执行“一键审批 / 一键回滚”。

## 2. 关键实现
- 新增前端 API 封装：
  - `fetchCapabilityForgeProposals`
  - `fetchCapabilityForgeProposal`
  - `approveCapabilityForgeProposal`
  - `rollbackCapabilityForgeProposal`
- 新增组件 `ForgeRiskActionBar`（挂载在 Layout 头部，全页面可见）：
  - 展示 `critical/unhealthy/avg_health/tracked`。
  - 关联当前最风险 proposal（proposal_id/status/stage/health）。
  - 提供一键 `Approve->Canary`、`Rollback` 操作。
  - 保留 Drill-down 链接到 Alerts 与 Insights。
- Layout 改造：
  - 移除旧 `ForgeRiskCapsule`。
  - 统一接入 `ForgeRiskActionBar`，沿用全局轮询配置。

## 3. 涉及文件
- `host_brain/app/monitor_ui/src/api/obs.js`
- `host_brain/app/monitor_ui/src/components/ForgeRiskActionBar.jsx`
- `host_brain/app/monitor_ui/src/components/Layout.jsx`

## 4. 结果
- Forge 风险已形成“全局可见 + 直接可控 + 可追踪”的统一操作面。
- 不再需要先跳转到细分页面才能执行审批/回滚动作。
