-- AW-03 Scheduler durable UoW closure.
--
-- An admitted occurrence can terminate before a HostCommand exists when the
-- Governor rejects it.  That decision is an immutable terminal fact, not
-- retryable pending work.  Scheduler administration likewise journals the
-- exact result in the same transaction as the definition-head mutation so a
-- crash before Secure Ingress claim completion is recoverable.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS scheduler_occurrence_terminal_outcome (
    receipt_ref TEXT PRIMARY KEY
        CHECK(length(trim(receipt_ref)) BETWEEN 1 AND 512),
    occurrence_id TEXT NOT NULL UNIQUE
        CHECK(length(trim(occurrence_id)) BETWEEN 1 AND 512),
    outcome_kind TEXT NOT NULL
        CHECK(outcome_kind IN ('GOVERNOR_REJECTED')),
    reason_code TEXT NOT NULL
        CHECK(length(trim(reason_code)) BETWEEN 1 AND 160),
    outcome_json TEXT NOT NULL
        CHECK(length(CAST(outcome_json AS BLOB)) BETWEEN 2 AND 65536),
    outcome_digest TEXT NOT NULL
        CHECK(
            length(outcome_digest) = 71
            AND substr(outcome_digest, 1, 7) = 'sha256:'
            AND substr(outcome_digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
    FOREIGN KEY(occurrence_id)
        REFERENCES scheduler_occurrence(occurrence_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS scheduler_admin_operation_result (
    receipt_ref TEXT PRIMARY KEY
        CHECK(length(trim(receipt_ref)) BETWEEN 1 AND 512),
    request_id TEXT NOT NULL UNIQUE
        CHECK(length(trim(request_id)) BETWEEN 1 AND 512),
    operation_kind TEXT NOT NULL
        CHECK(operation_kind IN ('PUBLISH', 'SET_ENABLED', 'DELETE')),
    task_id TEXT NOT NULL
        CHECK(length(trim(task_id)) BETWEEN 1 AND 255),
    principal_id TEXT NOT NULL
        CHECK(length(trim(principal_id)) BETWEEN 1 AND 240),
    ingress_claim_ref TEXT NOT NULL UNIQUE
        CHECK(length(trim(ingress_claim_ref)) BETWEEN 1 AND 512),
    ingress_audit_ref TEXT NOT NULL UNIQUE
        CHECK(length(trim(ingress_audit_ref)) BETWEEN 1 AND 512),
    body_sha256 TEXT NOT NULL
        CHECK(
            length(body_sha256) = 64
            AND body_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
    request_json TEXT NOT NULL
        CHECK(length(CAST(request_json AS BLOB)) BETWEEN 2 AND 1048576),
    request_digest TEXT NOT NULL
        CHECK(
            length(request_digest) = 71
            AND substr(request_digest, 1, 7) = 'sha256:'
            AND substr(request_digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
    result_json TEXT NOT NULL
        CHECK(length(CAST(result_json AS BLOB)) BETWEEN 2 AND 2097152),
    result_digest TEXT NOT NULL
        CHECK(
            length(result_digest) = 71
            AND substr(result_digest, 1, 7) = 'sha256:'
            AND substr(result_digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0)
);

CREATE INDEX IF NOT EXISTS idx_scheduler_terminal_outcome_created
    ON scheduler_occurrence_terminal_outcome(created_at_ms, occurrence_id);

CREATE INDEX IF NOT EXISTS idx_scheduler_admin_operation_task
    ON scheduler_admin_operation_result(task_id, created_at_ms, request_id);

CREATE TRIGGER IF NOT EXISTS trg_scheduler_terminal_outcome_no_update
BEFORE UPDATE ON scheduler_occurrence_terminal_outcome
BEGIN
    SELECT RAISE(ABORT, 'SCHEDULER_TERMINAL_OUTCOME_IMMUTABLE');
END;

CREATE TRIGGER IF NOT EXISTS trg_scheduler_terminal_outcome_no_delete
BEFORE DELETE ON scheduler_occurrence_terminal_outcome
BEGIN
    SELECT RAISE(ABORT, 'SCHEDULER_TERMINAL_OUTCOME_IMMUTABLE');
END;

CREATE TRIGGER IF NOT EXISTS trg_scheduler_admin_operation_no_update
BEFORE UPDATE ON scheduler_admin_operation_result
BEGIN
    SELECT RAISE(ABORT, 'SCHEDULER_ADMIN_OPERATION_IMMUTABLE');
END;

CREATE TRIGGER IF NOT EXISTS trg_scheduler_admin_operation_no_delete
BEFORE DELETE ON scheduler_admin_operation_result
BEGIN
    SELECT RAISE(ABORT, 'SCHEDULER_ADMIN_OPERATION_IMMUTABLE');
END;

CREATE TRIGGER IF NOT EXISTS trg_scheduler_terminal_outcome_excludes_result
BEFORE INSERT ON scheduler_occurrence_terminal_outcome
WHEN EXISTS (
    SELECT 1 FROM scheduler_execution_result
    WHERE occurrence_id = NEW.occurrence_id
)
BEGIN
    SELECT RAISE(ABORT, 'SCHEDULER_TERMINAL_OUTCOME_CONFLICTS_WITH_RESULT');
END;

CREATE TRIGGER IF NOT EXISTS trg_scheduler_result_excludes_terminal_outcome
BEFORE INSERT ON scheduler_execution_result
WHEN EXISTS (
    SELECT 1 FROM scheduler_occurrence_terminal_outcome
    WHERE occurrence_id = NEW.occurrence_id
)
BEGIN
    SELECT RAISE(ABORT, 'SCHEDULER_RESULT_CONFLICTS_WITH_TERMINAL_OUTCOME');
END;

COMMIT;
