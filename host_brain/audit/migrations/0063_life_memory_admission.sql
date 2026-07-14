-- AW-03 Memory-1: one durable admission receipt and projection outbox per chat request.
-- Chat text remains in the authoritative life_episode/life_evidence tables.  The
-- outbox deliberately carries references and policy metadata only, so derived
-- projections never become a second durable source of truth.

-- Admission policy can call external/model-backed components and therefore
-- cannot run while holding a SQLite write transaction.  This lease is the
-- durable single-owner fence for that pre-commit work.  A crashed owner is
-- recoverable after claim_expires_ts_ms; owner_token fences stale commits.
CREATE TABLE IF NOT EXISTS life_memory_admission_claim (
    request_id TEXT PRIMARY KEY CHECK(length(trim(request_id)) > 0),
    command_hash TEXT NOT NULL CHECK(length(command_hash) = 64),
    owner_token TEXT NOT NULL CHECK(length(trim(owner_token)) > 0),
    claim_expires_ts_ms INTEGER NOT NULL CHECK(claim_expires_ts_ms >= 0),
    attempt_count INTEGER NOT NULL DEFAULT 1 CHECK(attempt_count >= 1),
    created_ts_ms INTEGER NOT NULL CHECK(created_ts_ms >= 0),
    updated_ts_ms INTEGER NOT NULL CHECK(updated_ts_ms >= 0)
);

CREATE TABLE IF NOT EXISTS life_memory_admission_receipt (
    request_id TEXT PRIMARY KEY CHECK(length(trim(request_id)) > 0),
    command_hash TEXT NOT NULL CHECK(length(command_hash) = 64),
    decision TEXT NOT NULL CHECK(decision IN ('SKIP', 'WRITE_LIGHT', 'WRITE')),
    status TEXT NOT NULL CHECK(status IN ('SKIPPED', 'COMMITTED')),
    reason_code TEXT NOT NULL CHECK(length(trim(reason_code)) > 0),
    worth_score REAL NOT NULL CHECK(worth_score >= 0.0 AND worth_score <= 1.0),
    tags_json TEXT NOT NULL DEFAULT '[]',
    episode_id TEXT,
    outbox_id TEXT,
    created_ts_ms INTEGER NOT NULL CHECK(created_ts_ms >= 0),
    CHECK (
        (decision = 'SKIP' AND status = 'SKIPPED' AND episode_id IS NULL AND outbox_id IS NULL)
        OR
        (decision = 'WRITE_LIGHT' AND status = 'COMMITTED' AND episode_id IS NOT NULL AND outbox_id IS NULL)
        OR
        (decision = 'WRITE' AND status = 'COMMITTED' AND episode_id IS NOT NULL AND outbox_id IS NOT NULL)
    ),
    FOREIGN KEY(episode_id) REFERENCES life_episode(episode_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS life_memory_projection_outbox (
    outbox_id TEXT PRIMARY KEY CHECK(length(trim(outbox_id)) > 0),
    episode_id TEXT NOT NULL,
    request_id TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL CHECK(event_type = 'LIFE_MEMORY_CHAT_ADMITTED'),
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK(status IN ('PENDING', 'PROCESSING', 'PUBLISHED', 'FAILED')),
    available_ts_ms INTEGER NOT NULL CHECK(available_ts_ms >= 0),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
    claim_owner TEXT,
    claim_expires_ts_ms INTEGER,
    last_error TEXT,
    created_ts_ms INTEGER NOT NULL CHECK(created_ts_ms >= 0),
    updated_ts_ms INTEGER NOT NULL CHECK(updated_ts_ms >= 0),
    published_ts_ms INTEGER,
    FOREIGN KEY(episode_id) REFERENCES life_episode(episode_id) ON DELETE RESTRICT,
    FOREIGN KEY(request_id) REFERENCES life_memory_admission_receipt(request_id) ON DELETE RESTRICT,
    CHECK (
        (status = 'PROCESSING' AND claim_owner IS NOT NULL AND claim_expires_ts_ms IS NOT NULL)
        OR
        (status != 'PROCESSING' AND claim_owner IS NULL AND claim_expires_ts_ms IS NULL)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_life_memory_admission_episode
    ON life_memory_admission_receipt(episode_id)
    WHERE episode_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_life_memory_projection_episode
    ON life_memory_projection_outbox(episode_id);

CREATE INDEX IF NOT EXISTS idx_life_memory_projection_pending
    ON life_memory_projection_outbox(status, available_ts_ms, created_ts_ms);

CREATE INDEX IF NOT EXISTS idx_life_memory_projection_claim_expiry
    ON life_memory_projection_outbox(status, claim_expires_ts_ms);

CREATE INDEX IF NOT EXISTS idx_life_memory_admission_claim_expiry
    ON life_memory_admission_claim(claim_expires_ts_ms);
