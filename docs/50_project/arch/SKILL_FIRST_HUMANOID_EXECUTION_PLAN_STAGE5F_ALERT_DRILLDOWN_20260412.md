# Skill-First 类人体架构执行补充方案（P5F-Alert Drilldown）日期：2026-04-12
状态：`COMPLETED`

## 1. 本轮目标
- 将 Forge runtime 风险从“被动展示”升级为“全局可钻取”能力。
- 建立统一链路：全局风险胶囊 -> 告警中心过滤 -> 全局洞察定位。

## 2. 关键实现
- `runtimeHealth` 中 Forge 告警跳转改为可过滤路由：
  - `FORGE_RUNTIME_CRITICAL_PROPOSALS` -> `/alerts?reason_code=FORGE_RUNTIME_CRITICAL_PROPOSALS`
  - `FORGE_RUNTIME_UNHEALTHY_PROPOSALS` -> `/alerts?reason_code=FORGE_RUNTIME_UNHEALTHY_PROPOSALS`
- `Layout` 新增 `ForgeRiskCapsule`：
  - 显示 `critical/unhealthy/avg_health`。
  - 提供 `Alert Drill-down` 与 `Forge Insights` 快捷入口。
- `Alerts` 页新增 query 驱动过滤：
  - 读取 `reason_code` 自动过滤。
  - 过滤变化同步回 URL，形成可分享深链。
  - Forge 过滤态显示引导横幅，可一键跳转 `/insights/global?focus=forge-risk`。
- `Insights` 页新增焦点模式：
  - 读取 `focus=forge-risk`。
  - 自动滚动并高亮 `Capability Gap & Forge Quality` 区块。

## 3. 涉及文件
- `host_brain/app/monitor_ui/src/utils/runtimeHealth.js`
- `host_brain/app/monitor_ui/src/components/Layout.jsx`
- `host_brain/app/monitor_ui/src/pages/Alerts.jsx`
- `host_brain/app/monitor_ui/src/pages/Insights.jsx`

## 4. 结果
- Forge 风险具备“发现-定位-分析”闭环路径，跨页面连续性显著提升。
- 导航层、告警层、洞察层的风险语义保持一致，减少人工检索成本。
