# Skill-First 类人体架构执行补充方案（P5D-Overview Fusion）
日期：2026-04-12  
状态：`COMPLETED`

## 1. 本轮目标
- 将 Forge 质量闭环从“洞察页专属”下沉到“数字生命体总览页”。
- 在总览页直接高亮风险提案，减少跨页排障成本。

## 2. 关键实现
- `DigitalLifeOverview` 接入 capability-gap 主统计：
  - 新增 `fetchCapabilityGapStats` 拉取（含 `forge_runtime_quality`）。
  - 在页面状态中维护 `capabilityGapData` 并派生：
    - `avgForgeHealth`
    - `forgeCriticalCount`
    - `forgeUnhealthyCount`
    - `forgeRiskItems`（最低健康分优先）
- 总览页新增板块 `Capability Gap & Forge Loop`：
  - 指标卡：缺口检测、Forge 候选、平均健康分、风险提案数。
  - 风险告警：critical/unhealthy 分级横幅。
  - 详情列：
    - Gap Hotspots（reason/family/detected/failed）
    - Forge Risk Queue（health/failure/p95/tune）
- 生命脉冲条增加 Forge 节点，反映实时闭环健康度。

## 3. 结果
- Forge 质量链路已从“分析页”前移到“总览页”。
- 首页可直接看到“缺口压力 -> 锻造质量 -> 风险提案”闭环状态。
