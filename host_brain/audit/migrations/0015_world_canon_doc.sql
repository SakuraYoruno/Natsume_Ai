-- Migration 0015: world_canon_doc 索引表（M9 World Canon 闭环）
-- 存储 World Canon 文件扫描后的索引快照，供 prompt_context 注入和 drift 检测使用。
-- 幂等：CREATE TABLE IF NOT EXISTS + ON CONFLICT UPSERT。
-- 关联：DevSpec §19.1.2，FR-WCANON-002，TASK-0112。
CREATE TABLE IF NOT EXISTS world_canon_doc (
    doc_id      TEXT    PRIMARY KEY,
    version     INTEGER NOT NULL,
    hash        TEXT    NOT NULL,
    status      TEXT    NOT NULL CHECK(status IN ('active', 'deprecated', 'draft')),
    source_path TEXT    NOT NULL,
    updated_ts  INTEGER NOT NULL
);

-- 按 status 过滤 active 条目（context_pack Top-K 注入）
CREATE INDEX IF NOT EXISTS idx_world_canon_doc_status
    ON world_canon_doc(status);

-- 按更新时间排序（drift 检测 / 最近变更查询）
CREATE INDEX IF NOT EXISTS idx_world_canon_doc_updated_ts
    ON world_canon_doc(updated_ts);
