-- Canon schema extension for style/world versioning and index sync.

CREATE TABLE IF NOT EXISTS canon_proposal_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id TEXT,
    canon_kind TEXT,
    target_id TEXT,
    base_version INTEGER,
    proposed_version INTEGER,
    proposed_hash TEXT,
    proposer_node_id TEXT,
    summary TEXT,
    status TEXT,
    ts_ms INTEGER
);

CREATE TABLE IF NOT EXISTS canon_change_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    change_id TEXT,
    proposal_id TEXT,
    canon_kind TEXT,
    target_id TEXT,
    before_version INTEGER,
    after_version INTEGER,
    before_hash TEXT,
    after_hash TEXT,
    source_path TEXT,
    ts_ms INTEGER
);

CREATE TABLE IF NOT EXISTS world_canon_doc (
    doc_id TEXT PRIMARY KEY,
    version INTEGER NOT NULL,
    hash TEXT NOT NULL,
    status TEXT NOT NULL,
    source_path TEXT NOT NULL,
    updated_ts INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_canon_proposal_target ON canon_proposal_log(canon_kind, target_id);
CREATE INDEX IF NOT EXISTS idx_canon_change_target ON canon_change_log(canon_kind, target_id);
