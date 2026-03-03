-- Migration 0017: canon_proposal_log.proposal_id UNIQUE 约束（M10-A P1 风险修复）
-- 问题：0016 建表时 proposal_id 无 UNIQUE 约束，高并发双写可能产生重复提案。
-- 修复：新增 UNIQUE INDEX（幂等，IF NOT EXISTS）。
-- 关联：risk_registry.md R118-3；DevSpec §20；TASK-0117。

-- proposal_id 全局唯一（幂等检查依赖此约束）
CREATE UNIQUE INDEX IF NOT EXISTS idx_canon_proposal_id_unique
    ON canon_proposal_log(proposal_id);

-- change_id 全局唯一（审计回放依赖此约束）
CREATE UNIQUE INDEX IF NOT EXISTS idx_canon_change_id_unique
    ON canon_change_log(change_id);
