-- Migration 0017: canon_proposal_log 和 canon_change_log UNIQUE 约束（F-30 修复）
-- 问题：0016 中 proposal_id 无 UNIQUE 约束，高并发时幂等检查可能双写（R118-3）。
-- 修复：为 canon_proposal_log 添加 UNIQUE INDEX，保证数据库层幂等性。
--       同时为 canon_change_log.change_id 添加 UNIQUE INDEX（审计回放依赖此约束）。
-- 幂等：CREATE UNIQUE INDEX IF NOT EXISTS。
-- 关联：DevSpec §19.1.4；FR-WCANON-002；F-30（R118-3）；TASK-0117。

-- proposal_id 全局唯一（幂等检查依赖此约束）
CREATE UNIQUE INDEX IF NOT EXISTS idx_canon_proposal_id_unique
    ON canon_proposal_log(proposal_id);

-- change_id 全局唯一（审计回放依赖此约束）
CREATE UNIQUE INDEX IF NOT EXISTS idx_canon_change_id_unique
    ON canon_change_log(change_id);
