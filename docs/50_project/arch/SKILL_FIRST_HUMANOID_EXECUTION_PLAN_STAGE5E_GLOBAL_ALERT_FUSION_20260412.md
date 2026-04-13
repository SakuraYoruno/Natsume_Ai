# Skill-First 类人体架构执行补充方案（P5E-Global Alert Fusion）日期：2026-04-12
状态：`COMPLETED`

## 1. 本轮目标
- 将 Forge runtime 风险并入全局告警流，避免只在局部页面可见。
- 让导航 badge、告警中心、运行时卡片对 Forge 风险呈现保持一致。

## 2. 关键实现
- `buildDigitalLifeAlerts` 扩展 `options.capabilityGapData`：
  - 新增 `FORGE_RUNTIME_CRITICAL_PROPOSALS`（`critical`）。
  - 新增 `FORGE_RUNTIME_UNHEALTHY_PROPOSALS`（`warning`）。
  - detail 统一包含 `critical/unhealthy/tracked/avg_health` 关键字段。
- `Layout` 接线 capability gap 数据并参与全局告警构建：
  - 轮询拉取 `/obs/capability-gaps/stats`。
  - `runtimeAlerts` 改为 `buildDigitalLifeAlerts(runtimeSnapshot, { promptStats, capabilityGapData })`。
  - 导航 `alerts` badge 自动吃到 Forge 风险。
- `Alerts` 页面接线 capability gap 数据：
  - `load()` 并行拉取 error/health/capability gap。
  - `runtimeAlerts` 增加 `capabilityGapData` 参与构建与排序。
- `DigitalLifeRuntimeCard` 增加 `capabilityGapData` 入参，避免页面内告警割裂。

## 3. 涉及文件
- `host_brain/app/monitor_ui/src/utils/runtimeHealth.js`
- `host_brain/app/monitor_ui/src/components/Layout.jsx`
- `host_brain/app/monitor_ui/src/pages/Alerts.jsx`
- `host_brain/app/monitor_ui/src/components/DigitalLifeRuntimeCard.jsx`
- `host_brain/app/monitor_ui/src/pages/DigitalLifeOverview.jsx`

## 4. 结果
- Forge 风险从“洞察局部信息”升级为“全局告警一等公民”。
- Stage5 的 capability gap -> forge runtime -> risk alert 闭环完成全局融合。
