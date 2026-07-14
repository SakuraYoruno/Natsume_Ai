-- AW-02/AW-03: immutable public projection for governed internal Agent work.
--
-- The Task Runtime remains authoritative for attempts, leases, operation
-- reservations and terminal state.  This journal owns only the durable worker
-- result that must exist before a worker is allowed to emit COMMIT.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS agent_execution_result (
    receipt_ref TEXT PRIMARY KEY
        CHECK(length(trim(receipt_ref)) BETWEEN 1 AND 512),
    command_id TEXT NOT NULL UNIQUE
        CHECK(length(trim(command_id)) BETWEEN 1 AND 512),
    work_order_id TEXT NOT NULL UNIQUE
        CHECK(length(trim(work_order_id)) BETWEEN 1 AND 240),
    result_status TEXT NOT NULL
        CHECK(result_status IN ('EXECUTED', 'FAILED', 'BLOCKED', 'DEGRADED')),
    result_json TEXT NOT NULL
        CHECK(length(CAST(result_json AS BLOB)) BETWEEN 2 AND 16777216),
    result_digest TEXT NOT NULL
        CHECK(
            length(result_digest) = 71
            AND substr(result_digest, 1, 7) = 'sha256:'
            AND substr(result_digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
    FOREIGN KEY(command_id)
        REFERENCES host_command_journal(command_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_agent_execution_result_work_status
    ON agent_execution_result(work_order_id, result_status, created_at_ms);

CREATE TRIGGER IF NOT EXISTS trg_agent_execution_result_no_update
BEFORE UPDATE ON agent_execution_result
BEGIN
    SELECT RAISE(ABORT, 'AGENT_EXECUTION_RESULT_IMMUTABLE');
END;

CREATE TRIGGER IF NOT EXISTS trg_agent_execution_result_no_delete
BEFORE DELETE ON agent_execution_result
BEGIN
    SELECT RAISE(ABORT, 'AGENT_EXECUTION_RESULT_IMMUTABLE');
END;

COMMIT;
