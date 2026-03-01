-- Episodic lifecycle audit table extension.

CREATE TABLE IF NOT EXISTS memory_ops_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT,
    intent_id TEXT,
    op_kind TEXT,
    memory_id TEXT,
    target_memory_id TEXT,
    source_memory_ids TEXT,
    evidence_refs TEXT,
    status TEXT,
    reason_code TEXT,
    detail TEXT,
    decay_profile_id TEXT,
    visibility TEXT,
    ts_ms INTEGER
);

CREATE INDEX IF NOT EXISTS idx_memory_ops_memory_id ON memory_ops_log(memory_id);
CREATE INDEX IF NOT EXISTS idx_memory_ops_request ON memory_ops_log(request_id);
