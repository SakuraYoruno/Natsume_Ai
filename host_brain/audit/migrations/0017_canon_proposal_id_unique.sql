-- Migration 0017: canon_proposal_log.proposal_id UNIQUE 约束（F-30 修复）
-- 问题：0016 中 proposal_id 无 UNIQUE 约束，高并发时幂等检查可能双写（R118-3）。
-- 修复：为 canon_proposal_log 添加 UNIQUE INDEX，保证数据库层幂等性。
-- 幂等：CREATE UNIQUE INDEX IF NOT EXISTS。
-- 关联：DevSpec §19.1.4；FR-WCANON-002；F-30（R118-3）。

-- 为 proposal_id 添加 UNIQUE 约束（通过 UNIQUE INDEX 实现，兼容 SQLite）
CREATE UNIQUE INDEX IF NOT EXISTS idx_canon_proposal_id_unique
    ON canon_proposal_log(proposal_id);
