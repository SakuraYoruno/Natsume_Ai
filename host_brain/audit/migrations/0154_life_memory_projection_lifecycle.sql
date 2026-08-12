-- LifeMemory 投影生命周期扩展：SQLite 仍是唯一权威来源。
-- 旧 outbox 保持兼容；此表为更新和墓碑事件提供可重试、可诊断的追加式队列。
BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS life_memory_projection_event (
    projection_event_id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK(event_type IN ('UPSERT', 'TOMBSTONE')),
    reason_code TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING', 'PROCESSING', 'PUBLISHED', 'FAILED')),
    available_ts_ms INTEGER NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    claim_owner TEXT,
    claim_expires_ts_ms INTEGER,
    last_error TEXT,
    created_ts_ms INTEGER NOT NULL,
    updated_ts_ms INTEGER NOT NULL,
    published_ts_ms INTEGER,
    FOREIGN KEY(episode_id) REFERENCES life_episode(episode_id) ON DELETE RESTRICT,
    CHECK((status = 'PROCESSING' AND claim_owner IS NOT NULL AND claim_expires_ts_ms IS NOT NULL)
        OR (status != 'PROCESSING' AND claim_owner IS NULL AND claim_expires_ts_ms IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_life_memory_projection_event_idempotency
    ON life_memory_projection_event(episode_id, event_type, reason_code, payload_json);
CREATE INDEX IF NOT EXISTS idx_life_memory_projection_event_ready
    ON life_memory_projection_event(status, available_ts_ms, created_ts_ms);
CREATE INDEX IF NOT EXISTS idx_life_memory_projection_event_episode
    ON life_memory_projection_event(episode_id, created_ts_ms);

-- occurrence 描述内容是否确实出现过；claim_truth 描述能否当作事实使用。
ALTER TABLE life_episode ADD COLUMN occurrence_kind TEXT NOT NULL DEFAULT 'SYSTEM_RECORDED';
ALTER TABLE life_episode ADD COLUMN claim_truth TEXT NOT NULL DEFAULT 'CANDIDATE';
ALTER TABLE life_evidence ADD COLUMN occurrence_kind TEXT NOT NULL DEFAULT 'SYSTEM_RECORDED';
ALTER TABLE life_evidence ADD COLUMN claim_truth TEXT NOT NULL DEFAULT 'CANDIDATE';
CREATE INDEX IF NOT EXISTS idx_life_episode_truth_semantics
    ON life_episode(occurrence_kind, claim_truth, truth_state, status);
CREATE INDEX IF NOT EXISTS idx_life_evidence_truth_semantics
    ON life_evidence(occurrence_kind, claim_truth, episode_id);
COMMIT;
