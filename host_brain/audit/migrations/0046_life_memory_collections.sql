CREATE TABLE IF NOT EXISTS life_memory_collection (
    collection_id TEXT PRIMARY KEY,
    collection_kind TEXT NOT NULL,
    domain TEXT,
    scope_key TEXT,
    person_id TEXT,
    session_id TEXT,
    title TEXT,
    summary TEXT,
    item_count INTEGER NOT NULL DEFAULT 0,
    salience REAL NOT NULL DEFAULT 0.0,
    memory_strength REAL NOT NULL DEFAULT 0.0,
    retention_state TEXT NOT NULL DEFAULT 'WARM',
    created_ts_ms INTEGER NOT NULL,
    updated_ts_ms INTEGER NOT NULL,
    metadata TEXT
);

CREATE TABLE IF NOT EXISTS life_memory_collection_item (
    collection_item_id TEXT PRIMARY KEY,
    collection_id TEXT NOT NULL,
    episode_id TEXT NOT NULL,
    item_key TEXT NOT NULL,
    item_title TEXT,
    item_summary TEXT,
    source_ref TEXT,
    evidence_ref TEXT,
    position INTEGER NOT NULL DEFAULT 0,
    created_ts_ms INTEGER NOT NULL,
    metadata TEXT,
    FOREIGN KEY(collection_id) REFERENCES life_memory_collection(collection_id) ON DELETE CASCADE,
    FOREIGN KEY(episode_id) REFERENCES life_episode(episode_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_life_memory_collection_scope ON life_memory_collection(collection_kind, domain, scope_key, updated_ts_ms);
CREATE INDEX IF NOT EXISTS idx_life_memory_collection_person ON life_memory_collection(person_id, session_id, updated_ts_ms);
CREATE INDEX IF NOT EXISTS idx_life_memory_collection_retention ON life_memory_collection(retention_state, memory_strength, updated_ts_ms);
CREATE UNIQUE INDEX IF NOT EXISTS idx_life_memory_collection_item_key ON life_memory_collection_item(collection_id, item_key);
CREATE INDEX IF NOT EXISTS idx_life_memory_collection_item_episode ON life_memory_collection_item(episode_id, created_ts_ms);
