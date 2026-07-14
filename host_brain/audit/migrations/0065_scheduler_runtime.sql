-- AW-02 Scheduler: durable definition catalog, occurrence journal, and result projection.
--
-- Definition revisions and admitted occurrences are append-only.  The head is
-- the only mutable scheduler row; its version and due pointer form the CAS
-- fence used by the clock producer.  Task Runtime remains authoritative for
-- execution attempts and terminal command state.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS scheduler_definition_revision (
    task_id TEXT NOT NULL
        CHECK(length(trim(task_id)) BETWEEN 1 AND 255),
    definition_revision INTEGER NOT NULL CHECK(definition_revision >= 1),
    definition_json TEXT NOT NULL
        CHECK(length(CAST(definition_json AS BLOB)) BETWEEN 2 AND 1048576),
    definition_digest TEXT NOT NULL
        CHECK(
            length(definition_digest) = 71
            AND substr(definition_digest, 1, 7) = 'sha256:'
            AND substr(definition_digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
    trigger_mode TEXT NOT NULL CHECK(trigger_mode IN ('cron', 'interval', 'once')),
    enabled_at_publish INTEGER NOT NULL CHECK(enabled_at_publish IN (0, 1)),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
    PRIMARY KEY(task_id, definition_revision)
);

CREATE TABLE IF NOT EXISTS scheduler_definition_head (
    task_id TEXT PRIMARY KEY
        CHECK(length(trim(task_id)) BETWEEN 1 AND 255),
    definition_revision INTEGER NOT NULL CHECK(definition_revision >= 1),
    definition_digest TEXT NOT NULL
        CHECK(
            length(definition_digest) = 71
            AND substr(definition_digest, 1, 7) = 'sha256:'
            AND substr(definition_digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
    head_version INTEGER NOT NULL CHECK(head_version >= 1),
    enabled INTEGER NOT NULL CHECK(enabled IN (0, 1)),
    retired INTEGER NOT NULL DEFAULT 0 CHECK(retired IN (0, 1)),
    next_scheduled_at_ms INTEGER CHECK(next_scheduled_at_ms IS NULL OR next_scheduled_at_ms >= 0),
    updated_at_ms INTEGER NOT NULL CHECK(updated_at_ms >= 0),
    FOREIGN KEY(task_id, definition_revision)
        REFERENCES scheduler_definition_revision(task_id, definition_revision)
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS scheduler_occurrence (
    occurrence_id TEXT PRIMARY KEY
        CHECK(length(trim(occurrence_id)) BETWEEN 1 AND 512),
    task_id TEXT NOT NULL
        CHECK(length(trim(task_id)) BETWEEN 1 AND 255),
    definition_revision INTEGER NOT NULL CHECK(definition_revision >= 1),
    source TEXT NOT NULL CHECK(source IN ('CLOCK', 'MANUAL')),
    request_id TEXT NOT NULL
        CHECK(length(trim(request_id)) BETWEEN 1 AND 512),
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
    scheduled_at_ms INTEGER NOT NULL CHECK(scheduled_at_ms >= 0),
    next_scheduled_at_ms INTEGER
        CHECK(next_scheduled_at_ms IS NULL OR next_scheduled_at_ms >= 0),
    payload_json TEXT NOT NULL
        CHECK(length(CAST(payload_json AS BLOB)) BETWEEN 2 AND 4194304),
    payload_digest TEXT NOT NULL
        CHECK(
            length(payload_digest) = 71
            AND substr(payload_digest, 1, 7) = 'sha256:'
            AND substr(payload_digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
    UNIQUE(source, request_id),
    FOREIGN KEY(task_id, definition_revision)
        REFERENCES scheduler_definition_revision(task_id, definition_revision)
        ON DELETE RESTRICT,
    CHECK(
        (source = 'CLOCK')
        OR (source = 'MANUAL' AND next_scheduled_at_ms IS NULL)
    )
);

CREATE TABLE IF NOT EXISTS scheduler_execution_result (
    receipt_ref TEXT PRIMARY KEY
        CHECK(length(trim(receipt_ref)) BETWEEN 1 AND 512),
    occurrence_id TEXT NOT NULL UNIQUE
        CHECK(length(trim(occurrence_id)) BETWEEN 1 AND 512),
    command_id TEXT NOT NULL UNIQUE
        CHECK(length(trim(command_id)) BETWEEN 1 AND 512),
    work_order_id TEXT NOT NULL UNIQUE
        CHECK(length(trim(work_order_id)) BETWEEN 1 AND 240),
    result_status TEXT NOT NULL
        CHECK(result_status IN ('EXECUTED', 'SUCCEEDED', 'FAILED', 'BLOCKED', 'DEGRADED', 'SKIPPED')),
    result_json TEXT NOT NULL
        CHECK(length(CAST(result_json AS BLOB)) BETWEEN 2 AND 16777216),
    result_digest TEXT NOT NULL
        CHECK(
            length(result_digest) = 71
            AND substr(result_digest, 1, 7) = 'sha256:'
            AND substr(result_digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
    FOREIGN KEY(occurrence_id)
        REFERENCES scheduler_occurrence(occurrence_id) ON DELETE RESTRICT,
    FOREIGN KEY(command_id)
        REFERENCES host_command_journal(command_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_scheduler_definition_head_due
    ON scheduler_definition_head(retired, enabled, next_scheduled_at_ms, task_id);

CREATE INDEX IF NOT EXISTS idx_scheduler_occurrence_task_schedule
    ON scheduler_occurrence(task_id, definition_revision, scheduled_at_ms, source);

CREATE UNIQUE INDEX IF NOT EXISTS uq_scheduler_clock_occurrence_fence
    ON scheduler_occurrence(task_id, definition_revision, scheduled_at_ms)
    WHERE source = 'CLOCK';

CREATE INDEX IF NOT EXISTS idx_scheduler_execution_result_status
    ON scheduler_execution_result(result_status, created_at_ms, occurrence_id);

CREATE TRIGGER IF NOT EXISTS trg_scheduler_definition_revision_no_update
BEFORE UPDATE ON scheduler_definition_revision
BEGIN
    SELECT RAISE(ABORT, 'SCHEDULER_DEFINITION_REVISION_IMMUTABLE');
END;

CREATE TRIGGER IF NOT EXISTS trg_scheduler_definition_revision_no_delete
BEFORE DELETE ON scheduler_definition_revision
BEGIN
    SELECT RAISE(ABORT, 'SCHEDULER_DEFINITION_REVISION_IMMUTABLE');
END;

CREATE TRIGGER IF NOT EXISTS trg_scheduler_occurrence_no_update
BEFORE UPDATE ON scheduler_occurrence
BEGIN
    SELECT RAISE(ABORT, 'SCHEDULER_OCCURRENCE_IMMUTABLE');
END;

CREATE TRIGGER IF NOT EXISTS trg_scheduler_occurrence_no_delete
BEFORE DELETE ON scheduler_occurrence
BEGIN
    SELECT RAISE(ABORT, 'SCHEDULER_OCCURRENCE_IMMUTABLE');
END;

CREATE TRIGGER IF NOT EXISTS trg_scheduler_execution_result_no_update
BEFORE UPDATE ON scheduler_execution_result
BEGIN
    SELECT RAISE(ABORT, 'SCHEDULER_EXECUTION_RESULT_IMMUTABLE');
END;

CREATE TRIGGER IF NOT EXISTS trg_scheduler_execution_result_no_delete
BEFORE DELETE ON scheduler_execution_result
BEGIN
    SELECT RAISE(ABORT, 'SCHEDULER_EXECUTION_RESULT_IMMUTABLE');
END;

COMMIT;
