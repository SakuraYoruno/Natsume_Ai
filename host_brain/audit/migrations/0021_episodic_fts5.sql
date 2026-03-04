-- Episodic 记忆全文检索虚拟表（episodic_fts）
-- 为 Episodic 记忆的 content/text 字段创建 FTS5 虚拟表，支持混合检索（TASK-0202）
-- tokenizer: unicode61（支持中文字符边界分割）

-- FTS5 虚拟表：存储 memory_id 与 content 的全文索引
CREATE VIRTUAL TABLE IF NOT EXISTS episodic_fts USING fts5(
    memory_id UNINDEXED,
    content,
    tokenize = "unicode61"
);

-- 触发器：当 chroma_memory（或 episodic 内存表）有新记录时自动同步到 FTS5
-- 注意：本项目 Episodic 记忆主要存储在 InMemoryChromaClient 中，
-- FTS5 表通过 FullTextSearcher.index_memory() 手动写入，无需触发器。

-- 索引：按 memory_id 快速查找（用于去重融合）
CREATE INDEX IF NOT EXISTS idx_episodic_fts_memory_id ON episodic_fts(memory_id);
