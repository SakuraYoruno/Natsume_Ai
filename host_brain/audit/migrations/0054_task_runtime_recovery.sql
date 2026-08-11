-- TASK-0467 P1 forward upgrade: lease-fenced idempotency recovery.
-- 0052 is immutable; 0053 is reserved by TASK-0465.

BEGIN IMMEDIATE;

CREATE TABLE task_runtime_idempotency_reservation_recovery_v2 (
    reservation_id TEXT PRIMARY KEY,
    business_scope TEXT NOT NULL CHECK(length(trim(business_scope)) > 0),
    operation_fingerprint TEXT NOT NULL CHECK(length(trim(operation_fingerprint)) > 0),
    attempt_id TEXT,
    lease_id TEXT,
    owner_id TEXT,
    fencing_token INTEGER,
    expires_ts_ms INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN (
        'RESERVED', 'RECOVERY_REQUIRED', 'COMMITTED', 'RELEASED', 'CANCELLED'
    )),
    status_version INTEGER NOT NULL DEFAULT 0 CHECK(status_version >= 0),
    receipt_ref TEXT,
    legacy_receipt_raw TEXT,
    reconciliation_proof_ref TEXT,
    reconciliation_proof_digest TEXT,
    reconciliation_proof_key_id TEXT,
    reconciliation_proof_signature TEXT,
    reconciliation_action TEXT CHECK(
        reconciliation_action IS NULL OR reconciliation_action IN (
            'COMMIT', 'RELEASE', 'REACQUIRE'
        )
    ),
    reconciliation_outcome TEXT CHECK(
        reconciliation_outcome IS NULL OR reconciliation_outcome IN (
            'NO_SIDE_EFFECT', 'SIDE_EFFECT_CONFIRMED'
        )
    ),
    reconciler_actor_id TEXT,
    reconciliation_recovery_version INTEGER,
    recovery_reason_code TEXT,
    recovery_ts_ms INTEGER,
    created_ts_ms INTEGER NOT NULL,
    updated_ts_ms INTEGER NOT NULL,
    UNIQUE (business_scope, operation_fingerprint),
    FOREIGN KEY (attempt_id) REFERENCES task_runtime_attempt(attempt_id) ON DELETE RESTRICT,
    FOREIGN KEY (lease_id) REFERENCES task_runtime_lease(lease_id) ON DELETE RESTRICT,
    CHECK(expires_ts_ms >= created_ts_ms),
    CHECK(
        status != 'RESERVED'
        OR (
            attempt_id IS NOT NULL
            AND lease_id IS NOT NULL
            AND owner_id IS NOT NULL
            AND length(trim(owner_id)) > 0
            AND fencing_token IS NOT NULL
            AND fencing_token >= 1
        )
    ),
    CHECK(
        status != 'COMMITTED'
        OR (receipt_ref IS NOT NULL AND length(trim(receipt_ref)) > 0)
    ),
    CHECK(
        legacy_receipt_raw IS NULL
        OR recovery_reason_code GLOB 'LEGACY_0052_*'
    ),
    CHECK(
        status != 'RECOVERY_REQUIRED'
        OR (
            recovery_reason_code IS NOT NULL
            AND length(trim(recovery_reason_code)) > 0
            AND recovery_ts_ms IS NOT NULL
        )
    ),
    CHECK(
        reconciliation_proof_ref IS NULL
        OR (
            reconciliation_proof_digest IS NOT NULL
            AND reconciliation_proof_digest GLOB 'sha256:*'
            AND reconciliation_proof_key_id IS NOT NULL
            AND length(trim(reconciliation_proof_key_id)) > 0
            AND reconciliation_proof_signature IS NOT NULL
            AND reconciliation_proof_signature GLOB 'ed25519:*'
            AND reconciliation_action IS NOT NULL
            AND reconciliation_outcome IS NOT NULL
            AND reconciler_actor_id IS NOT NULL
            AND length(trim(reconciler_actor_id)) > 0
            AND reconciliation_recovery_version IS NOT NULL
            AND reconciliation_recovery_version >= 0
        )
    ),
    CHECK(
        status != 'RELEASED'
        OR reconciliation_proof_ref IS NOT NULL
        OR recovery_reason_code = 'LEGACY_0052_RELEASED'
    )
);

INSERT INTO task_runtime_idempotency_reservation_recovery_v2 (
    reservation_id, business_scope, operation_fingerprint, attempt_id,
    lease_id, owner_id, fencing_token, expires_ts_ms, status, status_version,
    receipt_ref, legacy_receipt_raw, reconciliation_proof_ref,
    reconciliation_proof_digest,
    reconciliation_proof_key_id, reconciliation_proof_signature,
    reconciliation_action, reconciliation_outcome, reconciler_actor_id,
    reconciliation_recovery_version,
    recovery_reason_code, recovery_ts_ms, created_ts_ms, updated_ts_ms
)
SELECT
    reservation_id,
    business_scope,
    operation_fingerprint,
    attempt_id,
    NULL,
    NULL,
    NULL,
    CASE WHEN updated_ts_ms >= created_ts_ms THEN updated_ts_ms ELSE created_ts_ms END,
    CASE WHEN status = 'RESERVED' THEN 'RECOVERY_REQUIRED' ELSE status END,
    status_version + CASE WHEN status = 'RESERVED' THEN 1 ELSE 0 END,
    CASE
        WHEN status = 'COMMITTED'
        THEN printf('legacy-receipt://0052/rowid-%lld', rowid)
        ELSE NULL
    END,
    receipt_ref,
    NULL,
    NULL,
    NULL,
    NULL,
    NULL,
    NULL,
    NULL,
    NULL,
    CASE
        WHEN status = 'RESERVED' THEN 'LEGACY_0052_UNBOUND_RESERVATION'
        ELSE 'LEGACY_0052_' || status
    END,
    updated_ts_ms,
    created_ts_ms,
    updated_ts_ms
FROM task_runtime_idempotency_reservation;

INSERT INTO task_runtime_outbox (
    outbox_id, aggregate_type, aggregate_id, aggregate_version,
    event_type, payload_ref, evidence_refs_json, attempt_id,
    idempotency_key, status, available_ts_ms, created_ts_ms,
    published_ts_ms, status_version
)
SELECT
    'outbox::migration-0054::' || reservation_id,
    'OPERATION_RESERVATION',
    reservation_id,
    status_version + 1,
    'OPERATION_RECOVERY_REQUIRED',
    'taskstore://event/OPERATION_RESERVATION/' || reservation_id || '/' || (status_version + 1),
    '[]',
    attempt_id,
    'migration-0054-recovery-required:' || reservation_id || ':' || (status_version + 1),
    'PENDING',
    updated_ts_ms,
    updated_ts_ms,
    NULL,
    0
FROM task_runtime_idempotency_reservation
WHERE status = 'RESERVED';

DROP TABLE task_runtime_idempotency_reservation;
ALTER TABLE task_runtime_idempotency_reservation_recovery_v2
    RENAME TO task_runtime_idempotency_reservation;

CREATE INDEX idx_task_runtime_idempotency_expiry
    ON task_runtime_idempotency_reservation(status, expires_ts_ms);

COMMIT;
