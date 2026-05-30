受众：PROJECT
标题：Skill-First 人体架构执行补充方案（P3-Stage3）

# Skill-First 人体架构执行补充方案（P3-Stage3）

日期：2026-04-12  
状态：`COMPLETED`

## 1. 目标
- 在能力缺口闭环中新增“热点识别 + 转正候选筛选”。
- 将 P4 `Capability Forge` 入口前置为可观测、可排序、可调阈值的数据面。

## 2. 实现项
- 扩展 `CapabilityGapTelemetry`：
  - 新增候选聚合维度：`reason_code / suggested_family / missing_tags`
  - 新增热点输出：`hotspots`
  - 新增转正候选输出：`forge_candidates`
  - 新增候选阈值回显：`candidate_thresholds`
- 扩展观测接口：
  - `GET /obs/capability-gaps/stats`
    - 支持查询参数：`limit_hotspots / limit_candidates / min_candidate_*`
    - 返回字段新增：`hotspots / forge_candidates / candidate_thresholds`
  - `GET /obs/capability-gaps/forge-candidates`
    - 仅返回候选清单，用于前端和运维面板直接消费。

## 3. 候选筛选策略（当前版）
- 阈值门控：
  - `min_detected_count`
  - `min_unique_sessions`
  - `min_failure_rate`
  - `min_failed_count`
- 优先级评分（`priority_score`）：
  - 频次、失败压力、影响会话范围、执行量加权合成。
- 推荐动作（`recommended_action`）：
  - `forge_with_reliability_tests`
  - `forge_with_canary`
  - `forge_new_skill_family_entry`

## 4. P4 衔接
- `forge_candidates` 可直接作为 P4 的 proposal 输入。
- 下一步接入审批与发布流水线：
  - proposal schema
  - shadow/canary/full rollout
  - rollback guard

