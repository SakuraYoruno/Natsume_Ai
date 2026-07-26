-- NE-01: bind governed Operation reconciliation to one exact recovery hold.
--
-- Existing reconciliation evidence remains byte-compatible when no 0130 hold
-- is involved.  A hold-bound request must carry both the immutable case ref
-- and subject digest.  The evidence row is still staged before Task admission;
-- the accepted handler later reopens this row and the existing Operation
-- reconciliation UoW remains the only mutation authority.

BEGIN IMMEDIATE;

ALTER TABLE task_runtime_operation_reconciliation_evidence
    ADD COLUMN recovery_case_ref TEXT
        REFERENCES task_runtime_operation_recovery_hold(case_ref)
        ON DELETE RESTRICT
        CHECK(
            recovery_case_ref IS NULL
            OR (
                length(recovery_case_ref) = 97
                AND substr(recovery_case_ref, 1, 33)
                    = 'operation-recovery-hold://sha256/'
                AND substr(recovery_case_ref, 34)
                    NOT GLOB '*[^0-9a-f]*'
            )
        );

ALTER TABLE task_runtime_operation_reconciliation_evidence
    ADD COLUMN recovery_case_subject_sha256 TEXT
        CHECK(
            recovery_case_subject_sha256 IS NULL
            OR (
                length(recovery_case_subject_sha256) = 71
                AND substr(recovery_case_subject_sha256, 1, 7) = 'sha256:'
                AND substr(recovery_case_subject_sha256, 8)
                    NOT GLOB '*[^0-9a-f]*'
            )
        );

CREATE UNIQUE INDEX IF NOT EXISTS
idx_task_operation_reconciliation_evidence_recovery_case
    ON task_runtime_operation_reconciliation_evidence(recovery_case_ref)
    WHERE recovery_case_ref IS NOT NULL;

CREATE TRIGGER IF NOT EXISTS
trg_task_operation_reconciliation_evidence_recovery_case
BEFORE INSERT ON task_runtime_operation_reconciliation_evidence
WHEN
    (
        NEW.recovery_case_ref IS NULL
        AND NEW.recovery_case_subject_sha256 IS NOT NULL
    )
    OR (
        NEW.recovery_case_ref IS NOT NULL
        AND NEW.recovery_case_subject_sha256 IS NULL
    )
    OR (
        NEW.recovery_case_ref IS NOT NULL
        AND NOT EXISTS (
            SELECT 1
            FROM task_runtime_operation_recovery_hold AS hold
            JOIN task_runtime_idempotency_reservation AS operation
              ON operation.reservation_id = hold.reservation_id
            WHERE hold.case_ref = NEW.recovery_case_ref
              AND hold.subject_sha256
                    = NEW.recovery_case_subject_sha256
              AND hold.reservation_id = NEW.reservation_id
              AND hold.operation_status = 'RECOVERY_REQUIRED'
              AND hold.operation_status_version = NEW.expected_version
              AND operation.status = hold.operation_status
              AND operation.status_version = hold.operation_status_version
              AND hold.classification IN (
                    'SIGNED_OPERATION_RECONCILIATION_REQUIRED',
                    'SIGNED_CANCELLATION_RECONCILIATION_REQUIRED'
              )
              AND (
                    (
                        hold.classification
                            = 'SIGNED_OPERATION_RECONCILIATION_REQUIRED'
                        AND NEW.intent_ref IS NULL
                        AND NEW.action IN ('COMMIT', 'RELEASE', 'REACQUIRE')
                    )
                    OR
                    (
                        hold.classification
                            = 'SIGNED_CANCELLATION_RECONCILIATION_REQUIRED'
                        AND NEW.intent_ref = hold.cancellation_intent_ref
                        AND NEW.action IN ('COMMIT', 'RELEASE')
                    )
              )
        )
    )
BEGIN
    SELECT RAISE(
        ABORT,
        'TASK_OPERATION_RECONCILIATION_RECOVERY_CASE_INVALID'
    );
END;

COMMIT;
