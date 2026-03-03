-- Migration 0016: canon_proposal_log + canon_change_log（M9 World Canon 闭环）
-- canon_proposal_log：记录 World Canon Evolution 提案（pending→approved/rejected）。
-- canon_change_log：记录每次 Canon 变更的前后 hash，支持审计回放。
-- 幂等：CREATE TABLE IF NOT EXISTS。
-- 关联：DevSpec §19.1.4；FR-WCANON-002；TASK-0112；TASK-0114。

CREATE TABLE IF NOT EXISTS canon_proposal_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id      TEXT    NOT NULL,
    canon_kind       TEXT    NOT NULL CHECK(canon_kind IN ('world', 'style')),
    target_id        TEXT    NOT NULL,
    base_version     INTEGER,
    proposed_version INTEGER,
    proposed_hash    TEXT,
    proposer_node_id TEXT,
    summary          TEXT,
    status           TEXT    NOT NULL,
    ts_ms            INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS canon_change_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    change_id      TEXT    NOT NULL,
    proposal_id    TEXT,
    canon_kind     TEXT    NOT NULL CHECK(canon_kind IN ('world', 'style')),
    target_id      TEXT    NOT NULL,
    before_version INTEGER,
    after_version  INTEGER NOT NULL,
    before_hash    TEXT,
    after_hash     TEXT    NOT NULL,
    source_path    TEXT,
    ts_ms          INTEGER NOT NULL
);

-- 按 (canon_kind, target_id) 查询提案历史
CREATE INDEX IF NOT EXISTS idx_canon_proposal_target
    ON canon_proposal_log(canon_kind, target_id);

-- 按 status 过滤 pending 提案（审核队列）
CREATE INDEX IF NOT EXISTS idx_canon_proposal_status
    ON canon_proposal_log(status);

-- 按 (canon_kind, target_id) 查询变更历史（审计回放）
CREATE INDEX IF NOT EXISTS idx_canon_change_target
    ON canon_change_log(canon_kind, target_id);

-- 按时间排序（最近变更查询）
CREATE INDEX IF NOT EXISTS idx_canon_change_ts
    ON canon_change_log(ts_ms);
