-- AW05: process-independent at-most-once capability admission and receipts.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS capability_execution_claim (
    idempotency_key        TEXT PRIMARY KEY,
    request_hash           TEXT NOT NULL,
    request_id             TEXT NOT NULL,
    attempt_id             TEXT NOT NULL,
    capability_id          TEXT NOT NULL,
    kind                   TEXT NOT NULL CHECK(kind IN ('tool', 'skill', 'mcp', 'domain')),
    owner_token            TEXT NOT NULL,
    state                  TEXT NOT NULL CHECK(state IN ('CLAIMED', 'IN_DOUBT', 'COMPLETED')),
    lease_expires_at_ms    INTEGER NOT NULL CHECK(lease_expires_at_ms >= 0),
    created_at_ms          INTEGER NOT NULL CHECK(created_at_ms >= 0),
    updated_at_ms          INTEGER NOT NULL CHECK(updated_at_ms >= created_at_ms)
);

CREATE INDEX IF NOT EXISTS idx_capability_claim_request
    ON capability_execution_claim(request_id, attempt_id);
CREATE INDEX IF NOT EXISTS idx_capability_claim_state
    ON capability_execution_claim(state, lease_expires_at_ms);

CREATE TABLE IF NOT EXISTS capability_execution_receipt (
    idempotency_key        TEXT PRIMARY KEY,
    request_hash           TEXT NOT NULL,
    request_id             TEXT NOT NULL,
    attempt_id             TEXT NOT NULL,
    capability_id          TEXT NOT NULL,
    kind                   TEXT NOT NULL CHECK(kind IN ('tool', 'skill', 'mcp', 'domain')),
    status                 TEXT NOT NULL CHECK(status IN (
                               'SUCCEEDED', 'REJECTED', 'FAILED', 'TIMED_OUT', 'IN_DOUBT'
                           )),
    attempt_json           TEXT NOT NULL CHECK(json_valid(attempt_json)),
    evidence_json          TEXT NOT NULL CHECK(json_valid(evidence_json)),
    receipt_json           TEXT NOT NULL CHECK(json_valid(receipt_json)),
    created_at_ms          INTEGER NOT NULL CHECK(created_at_ms >= 0),
    FOREIGN KEY(idempotency_key) REFERENCES capability_execution_claim(idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_capability_receipt_request
    ON capability_execution_receipt(request_id, attempt_id);
CREATE INDEX IF NOT EXISTS idx_capability_receipt_capability
    ON capability_execution_receipt(capability_id, created_at_ms);

CREATE TABLE IF NOT EXISTS capability_execution_late_outcome (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key       TEXT NOT NULL,
    request_hash          TEXT NOT NULL,
    owner_token           TEXT NOT NULL,
    status                TEXT NOT NULL CHECK(status IN ('LATE_SUCCEEDED', 'LATE_FAILED')),
    output_digest         TEXT,
    error_type            TEXT,
    observed_at_ms        INTEGER NOT NULL CHECK(observed_at_ms >= 0),
    FOREIGN KEY(idempotency_key) REFERENCES capability_execution_claim(idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_capability_late_outcome_key
    ON capability_execution_late_outcome(idempotency_key, observed_at_ms);

CREATE TRIGGER IF NOT EXISTS capability_execution_claim_identity_no_update
BEFORE UPDATE OF idempotency_key, request_hash, request_id, attempt_id,
    capability_id, kind, owner_token, lease_expires_at_ms, created_at_ms
ON capability_execution_claim
BEGIN
    SELECT RAISE(ABORT, 'capability claim identity is immutable');
END;

CREATE TRIGGER IF NOT EXISTS capability_execution_claim_no_delete
BEFORE DELETE ON capability_execution_claim
BEGIN
    SELECT RAISE(ABORT, 'capability claim is durable');
END;

CREATE TRIGGER IF NOT EXISTS capability_execution_receipt_no_update
BEFORE UPDATE ON capability_execution_receipt
BEGIN
    SELECT RAISE(ABORT, 'capability receipt is immutable');
END;

CREATE TRIGGER IF NOT EXISTS capability_execution_receipt_no_delete
BEFORE DELETE ON capability_execution_receipt
BEGIN
    SELECT RAISE(ABORT, 'capability receipt is immutable');
END;

CREATE TRIGGER IF NOT EXISTS capability_execution_late_outcome_no_update
BEFORE UPDATE ON capability_execution_late_outcome
BEGIN
    SELECT RAISE(ABORT, 'capability late outcome is immutable');
END;

CREATE TRIGGER IF NOT EXISTS capability_execution_late_outcome_no_delete
BEFORE DELETE ON capability_execution_late_outcome
BEGIN
    SELECT RAISE(ABORT, 'capability late outcome is immutable');
END;

COMMIT;
