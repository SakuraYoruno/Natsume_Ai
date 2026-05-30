受众：PROJECT
标题：Skill-First 类人体架构执行补充方案（P4-Guardrail）

# Skill-First 类人体架构执行补充方案（P4-Guardrail）
日期：2026-04-12  
状态：`COMPLETED`

## 1. 本轮目标
- 让 Forge 回滚策略真正使用 `observe_window_minutes`，避免“全历史累计失败率”带来的判定滞后。
- 修正手动回滚时 `auto_rollback_triggered` 被错误置位的问题，消除状态语义污染。

## 2. 核心实现
- `CapabilityForgeService.record_runtime_result()`：
  - 新增窗口统计：`window_total / window_failed / window_failure_rate`。
  - 新增 `recent_results`（滚动窗口结果序列，带时间戳）并按窗口时长裁剪。
  - canary 自动回滚的失败率判定改为基于窗口统计，而非全量历史累计。
- `CapabilityForgeService._rollback_locked()`：
  - 仅当 `reason_code == CAPABILITY_FORGE_AUTO_ROLLBACK` 时设置 `auto_rollback_triggered=True`。
  - 手动回滚保留 `auto_rollback_triggered=False`（除非历史已为真）。
- proposal/runtime_stats 默认结构补齐窗口字段，保证重放与增量更新一致。

## 3. 验收点
- 规则层：
  - 支持“连续失败触发”和“窗口失败率触发”双策略并存。
- 语义层：
  - 手动回滚与自动回滚在审计字段上可区分。
- 稳定性：
  - Forge/dispatch/obs 关键回归用例通过。
