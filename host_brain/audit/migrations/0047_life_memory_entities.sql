CREATE TABLE IF NOT EXISTS life_memory_entity (
    entity_id TEXT PRIMARY KEY,
    entity_kind TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    normalized_key TEXT NOT NULL,
    domain TEXT,
    first_seen_ts_ms INTEGER NOT NULL,
    last_seen_ts_ms INTEGER NOT NULL,
    mention_count INTEGER NOT NULL DEFAULT 0,
    salience REAL NOT NULL DEFAULT 0.0,
    memory_strength REAL NOT NULL DEFAULT 0.0,
    retention_state TEXT NOT NULL DEFAULT 'WARM',
    metadata TEXT
);

CREATE TABLE IF NOT EXISTS life_memory_entity_mention (
    mention_id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL,
    episode_id TEXT,
    collection_id TEXT,
    collection_item_id TEXT,
    source_ref TEXT,
    evidence_ref TEXT,
    position INTEGER NOT NULL DEFAULT 0,
    created_ts_ms INTEGER NOT NULL,
    metadata TEXT,
    FOREIGN KEY(entity_id) REFERENCES life_memory_entity(entity_id) ON DELETE CASCADE,
    FOREIGN KEY(episode_id) REFERENCES life_episode(episode_id) ON DELETE SET NULL,
    FOREIGN KEY(collection_id) REFERENCES life_memory_collection(collection_id) ON DELETE SET NULL,
    FOREIGN KEY(collection_item_id) REFERENCES life_memory_collection_item(collection_item_id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_life_memory_entity_key ON life_memory_entity(entity_kind, domain, normalized_key);
CREATE INDEX IF NOT EXISTS idx_life_memory_entity_domain ON life_memory_entity(domain, memory_strength, last_seen_ts_ms);
CREATE INDEX IF NOT EXISTS idx_life_memory_entity_retention ON life_memory_entity(retention_state, memory_strength, last_seen_ts_ms);
CREATE UNIQUE INDEX IF NOT EXISTS idx_life_memory_entity_mention_key ON life_memory_entity_mention(entity_id, episode_id, collection_id, collection_item_id);
CREATE INDEX IF NOT EXISTS idx_life_memory_entity_mention_episode ON life_memory_entity_mention(episode_id, created_ts_ms);
CREATE INDEX IF NOT EXISTS idx_life_memory_entity_mention_collection ON life_memory_entity_mention(collection_id, created_ts_ms);
