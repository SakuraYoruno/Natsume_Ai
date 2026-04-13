# Skill-First 人体架构执行补充方案（P4-Runtime）

日期：2026-04-12  
状态：`COMPLETED`

## 1. 本轮目标
- 让 Capability Forge proposal 具备重启后可恢复能力（持久化回放）。
- 将 canary/full 阶段真正作用到运行时路由（不是只改控制面状态）。

## 2. 核心实现
- `CapabilityForgeService` 增强：
  - 持久化事件写入 `error_log`（`stage=CAPABILITY_FORGE_STATE`，`detail` 为完整 proposal snapshot）。
  - 启动后回放 `CAPABILITY_FORGE_STATE` 事件恢复 proposal 状态。
  - 新增 `get_runtime_route_override()`：
    - 匹配 family/reason/tags。
    - `FULL` 全量命中。
    - `CANARY` 按稳定哈希桶命中（`request_id + proposal_id`）。
- `dispatch_agent` 接线：
  - 在能力缺口检测后，先查询 Forge 运行时覆盖。
  - 命中后优先走 `ForgePromotion` 路由，不再生成临时 runtime capability。
  - trace/handoff 增加 `capability_forge_proposal_id / capability_forge_stage`。
- 观测接口增强：
  - 新增 `POST /obs/capability-gaps/forge-proposals/runtime-match`，可直接验证请求是否命中 canary/full。

## 3. 结果
- Proposal 不再仅内存态，重启后可恢复。
- canary/full 阶段已接入主执行链路，具备真实流量分配语义。
- P4 已从“准备态”进入“可运行态”。

## 4. 下一步建议
- 将 Forge proposal 迁移到独立审计表，减少对 `error_log` 的事件承载压力。
- 把 canary 命中统计上报到 `/obs/capability-gaps/stats`（命中率、失败率、回滚触发次数）。
- 将 rollback 自动触发规则接入实时监控阈值而不是手动 API。

