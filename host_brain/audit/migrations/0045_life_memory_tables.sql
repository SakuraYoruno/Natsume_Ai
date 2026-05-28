CREATE TABLE IF NOT EXISTS life_episode (
    episode_id TEXT PRIMARY KEY,
    episode_kind TEXT NOT NULL,
    thread_id TEXT,
    person_id TEXT,
    session_id TEXT,
    primary_ref TEXT,
    summary TEXT,
    salience REAL NOT NULL DEFAULT 0.0,
    truth_state TEXT NOT NULL DEFAULT 'CANDIDATE',
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    created_ts_ms INTEGER NOT NULL,
    last_touched_ts_ms INTEGER NOT NULL,
    metadata TEXT
);

CREATE TABLE IF NOT EXISTS life_evidence (
    evidence_id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL,
    evidence_kind TEXT NOT NULL,
    source_ref TEXT,
    request_id TEXT,
    content TEXT,
    payload TEXT,
    trust_level TEXT NOT NULL DEFAULT 'CANDIDATE',
    created_ts_ms INTEGER NOT NULL,
    FOREIGN KEY(episode_id) REFERENCES life_episode(episode_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS life_episode_link (
    link_id TEXT PRIMARY KEY,
    source_episode_id TEXT NOT NULL,
    target_episode_id TEXT NOT NULL,
    link_kind TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0,
    created_ts_ms INTEGER NOT NULL,
    metadata TEXT,
    FOREIGN KEY(source_episode_id) REFERENCES life_episode(episode_id) ON DELETE CASCADE,
    FOREIGN KEY(target_episode_id) REFERENCES life_episode(episode_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS life_memory_correction (
    correction_id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL,
    correction_episode_id TEXT,
    correction_kind TEXT NOT NULL,
    reason_code TEXT,
    detail TEXT,
    created_ts_ms INTEGER NOT NULL,
    metadata TEXT,
    FOREIGN KEY(episode_id) REFERENCES life_episode(episode_id) ON DELETE CASCADE,
    FOREIGN KEY(correction_episode_id) REFERENCES life_episode(episode_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_life_episode_thread ON life_episode(thread_id, last_touched_ts_ms);
CREATE INDEX IF NOT EXISTS idx_life_episode_person_session ON life_episode(person_id, session_id, last_touched_ts_ms);
CREATE INDEX IF NOT EXISTS idx_life_episode_truth ON life_episode(truth_state, status, last_touched_ts_ms);
CREATE INDEX IF NOT EXISTS idx_life_episode_primary_ref ON life_episode(primary_ref);
CREATE INDEX IF NOT EXISTS idx_life_evidence_episode ON life_evidence(episode_id, created_ts_ms);
CREATE INDEX IF NOT EXISTS idx_life_evidence_source_ref ON life_evidence(source_ref);
CREATE INDEX IF NOT EXISTS idx_life_evidence_request ON life_evidence(request_id);
CREATE INDEX IF NOT EXISTS idx_life_episode_link_source ON life_episode_link(source_episode_id, link_kind);
CREATE INDEX IF NOT EXISTS idx_life_episode_link_target ON life_episode_link(target_episode_id, link_kind);
CREATE INDEX IF NOT EXISTS idx_life_memory_correction_episode ON life_memory_correction(episode_id, created_ts_ms);
