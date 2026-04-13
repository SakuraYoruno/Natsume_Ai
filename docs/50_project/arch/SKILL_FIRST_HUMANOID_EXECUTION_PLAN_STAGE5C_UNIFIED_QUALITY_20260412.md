# Skill-First 类人体架构执行补充方案（P5C-Unified Quality Surface）
日期：2026-04-12  
状态：`COMPLETED`

## 1. 本轮目标
- 将 Forge 运行质量聚合并入 `/obs/capability-gaps/stats` 主视图，避免前端多接口拼接。
- 在 Insights 全局分组新增“能力缺口与锻造质量”板块，形成可见的闭环健康面。

## 2. 后端实现
- `CapabilityGapStatsResponse` 扩展字段：
  - `forge_runtime_quality`（summary + proposal 级 items）
- `/obs/capability-gaps/stats` 增强：
  - 从 `CapabilityForgeService.runtime_quality_stats()` 汇总质量快照。
  - 当 forge 服务不可用时返回安全默认值（0/空数组）。
- 兼容性：
  - 保留原有字段不变，仅追加结构化质量字段。

## 3. 前端实现
- `monitor_ui/src/api/obs.js` 新增：
  - `fetchCapabilityGapStats()`
  - `fetchCapabilityForgeRuntimeQuality()`
- `monitor_ui/src/pages/Insights.jsx`（global 分组）新增：
  - `CapabilityGapStatsSection`
  - 展示缺口规模、Forge 候选、提案健康摘要、最差健康提案列表（health/failure/p95/tune）

## 4. 结果
- Stage5 的“策略自优化”不再是黑箱：同一页面可看到缺口来源与 Forge 发布质量。
- capability-gap 主接口具备直接支撑前端运营面板的结构化输出。
