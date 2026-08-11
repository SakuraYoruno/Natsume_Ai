-- NE-01: isolate signed Operation reconciliation evidence from TaskGraph input.
--
-- The Secure Ingress body is not copied into HostCommand/WorkOrder contracts.
-- Only an immutable evidence reference and digest cross the governed admission
-- boundary; the post-Acceptance handler reopens this append-only record.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS task_runtime_operation_reconciliation_evidence (
    evidence_ref TEXT PRIMARY KEY,
    evidence_digest TEXT NOT NULL UNIQUE,
    request_id TEXT NOT NULL UNIQUE,
    ingress_audit_ref TEXT NOT NULL,
    body_sha256 TEXT NOT NULL,
    reservation_id TEXT NOT NULL,
    expected_version INTEGER NOT NULL CHECK(expected_version >= 0),
    action TEXT NOT NULL CHECK(action IN ('COMMIT', 'RELEASE', 'REACQUIRE')),
    intent_ref TEXT,
    downstream_receipt_ref TEXT,
    reconciler_actor_id TEXT NOT NULL,
    attestation_ref TEXT NOT NULL,
    attestation_key_id TEXT NOT NULL,
    attestation_signature TEXT NOT NULL,
    attestation_binding_digest TEXT NOT NULL,
    created_ts_ms INTEGER NOT NULL CHECK(created_ts_ms > 0),
    FOREIGN KEY(reservation_id)
        REFERENCES task_runtime_idempotency_reservation(reservation_id)
);

CREATE INDEX IF NOT EXISTS idx_task_operation_reconciliation_evidence_subject
    ON task_runtime_operation_reconciliation_evidence(
        reservation_id,
        expected_version,
        action
    );

CREATE TRIGGER IF NOT EXISTS trg_task_operation_reconciliation_evidence_no_update
BEFORE UPDATE ON task_runtime_operation_reconciliation_evidence
BEGIN
    SELECT RAISE(ABORT, 'TASK_OPERATION_RECONCILIATION_EVIDENCE_IMMUTABLE');
END;

CREATE TRIGGER IF NOT EXISTS trg_task_operation_reconciliation_evidence_no_delete
BEFORE DELETE ON task_runtime_operation_reconciliation_evidence
BEGIN
    SELECT RAISE(ABORT, 'TASK_OPERATION_RECONCILIATION_EVIDENCE_IMMUTABLE');
END;

COMMIT;
