BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS host_embodiment_outbox (
    outbox_id TEXT PRIMARY KEY,
    command_id TEXT NOT NULL UNIQUE,
    idempotency_key TEXT NOT NULL UNIQUE,
    command_digest TEXT NOT NULL,
    command_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('PENDING', 'DISPATCHING', 'APPLIED', 'REJECTED', 'IN_DOUBT')
    ),
    claim_token TEXT,
    claim_expires_at_ms INTEGER,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    receipt_json TEXT,
    receipt_digest TEXT,
    reason_code TEXT,
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    completed_at_ms INTEGER
);

CREATE INDEX IF NOT EXISTS idx_host_embodiment_outbox_ready
    ON host_embodiment_outbox(status, created_at_ms);

CREATE TRIGGER IF NOT EXISTS host_embodiment_outbox_identity_no_update
BEFORE UPDATE OF command_id, idempotency_key, command_digest, command_json
ON host_embodiment_outbox
BEGIN
    SELECT RAISE(ABORT, 'host embodiment outbox identity is immutable');
END;

CREATE TRIGGER IF NOT EXISTS host_embodiment_outbox_no_delete
BEFORE DELETE ON host_embodiment_outbox
BEGIN
    SELECT RAISE(ABORT, 'host embodiment outbox is append-only');
END;

COMMIT;
