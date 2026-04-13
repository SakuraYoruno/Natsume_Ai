# Skill-First 人体架构执行补充方案（P4-Obs）

日期：2026-04-12  
状态：`COMPLETED`

## 1. 目标
- 让 Forge canary/full 的运行时命中可观测、可量化。

## 2. 实现
- `CapabilityForgeService` 新增运行时命中统计：
  - `total / applied / full_hit / canary_hit / canary_miss / no_active_proposal / invalid_input`
  - `apply_rate`
- 新增观测接口：
  - `GET /obs/capability-gaps/forge-runtime/stats`
- 现有接口补充：
  - `POST /obs/capability-gaps/forge-proposals/runtime-match`（单次命中决策验证）

## 3. 价值
- 可直接判断 canary 是否生效、命中率是否符合预期。
- 为后续自动回滚阈值（failure_rate + hit_count）提供统计基础。

