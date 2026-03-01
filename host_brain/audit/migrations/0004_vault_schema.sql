-- Vault schema extension for manifest index and GC audit logs.

CREATE TABLE IF NOT EXISTS vault_manifest_index (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT,
    manifest_id TEXT,
    intent_id TEXT,
    integrity_digest TEXT,
    manifest_path TEXT,
    items_count INTEGER,
    evidence_refs TEXT,
    policy_profile_id TEXT,
    undo_ref TEXT,
    reversible INTEGER,
    skill_name TEXT,
    session_id TEXT,
    seq INTEGER,
    status TEXT,
    ts_ms INTEGER
);

CREATE TABLE IF NOT EXISTS vault_gc_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT,
    policy_profile_id TEXT,
    deleted_manifest_ids TEXT,
    deleted_artifact_ids TEXT,
    blocked_manifest_ids TEXT,
    evidence_refs TEXT,
    status TEXT,
    ts_ms INTEGER
);

CREATE INDEX IF NOT EXISTS idx_vault_manifest_id ON vault_manifest_index(manifest_id);
CREATE INDEX IF NOT EXISTS idx_vault_manifest_intent ON vault_manifest_index(intent_id);
CREATE INDEX IF NOT EXISTS idx_vault_gc_ts ON vault_gc_log(ts_ms);
