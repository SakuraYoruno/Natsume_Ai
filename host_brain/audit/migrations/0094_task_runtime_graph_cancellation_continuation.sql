-- NE-01 B5C: cross-process continuation lease for pending Graph cancellation.
--
-- This table is coordination metadata only.  The 0092 intent and decision
-- remain the sole business status authority; no completion status is copied
-- here.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS task_runtime_graph_cancellation_continuation (
    intent_ref TEXT NOT NULL PRIMARY KEY CHECK(length(trim(intent_ref)) > 0),
    graph_id TEXT NOT NULL CHECK(length(trim(graph_id)) > 0),
    graph_revision INTEGER NOT NULL CHECK(
        typeof(graph_revision) = 'integer' AND graph_revision >= 1
    ),
    claim_owner TEXT,
    claim_token TEXT,
    claim_expires_ts_ms INTEGER,
    claim_fencing_token INTEGER NOT NULL DEFAULT 0 CHECK(
        typeof(claim_fencing_token) = 'integer'
        AND claim_fencing_token >= 0
    ),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(
        typeof(attempt_count) = 'integer'
        AND attempt_count >= 0
        AND attempt_count = claim_fencing_token
    ),
    available_ts_ms INTEGER NOT NULL CHECK(
        typeof(available_ts_ms) = 'integer' AND available_ts_ms >= 0
    ),
    last_error TEXT CHECK(
        last_error IS NULL
        OR (
            typeof(last_error) = 'text'
            AND length(trim(last_error)) > 0
            AND length(last_error) <= 2000
        )
    ),
    status_version INTEGER NOT NULL DEFAULT 0 CHECK(
        typeof(status_version) = 'integer' AND status_version >= 0
    ),
    created_ts_ms INTEGER NOT NULL CHECK(
        typeof(created_ts_ms) = 'integer' AND created_ts_ms >= 0
    ),
    updated_ts_ms INTEGER NOT NULL CHECK(
        typeof(updated_ts_ms) = 'integer'
        AND updated_ts_ms >= created_ts_ms
    ),
    FOREIGN KEY (intent_ref, graph_id, graph_revision)
        REFERENCES task_runtime_graph_cancellation_intent(
            intent_ref, graph_id, graph_revision
        ) ON DELETE RESTRICT,
    CHECK (
        (
            claim_owner IS NULL
            AND claim_token IS NULL
            AND claim_expires_ts_ms IS NULL
        )
        OR
        (
            typeof(claim_owner) = 'text'
            AND length(trim(claim_owner)) > 0
            AND length(claim_owner) <= 255
            AND typeof(claim_token) = 'text'
            AND length(trim(claim_token)) > 0
            AND length(claim_token) <= 512
            AND typeof(claim_expires_ts_ms) = 'integer'
            AND claim_expires_ts_ms >= 0
            AND claim_fencing_token > 0
        )
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_task_runtime_graph_cancellation_continuation_graph
    ON task_runtime_graph_cancellation_continuation(graph_id, graph_revision);

CREATE INDEX IF NOT EXISTS idx_task_runtime_graph_cancellation_continuation_ready
    ON task_runtime_graph_cancellation_continuation(
        available_ts_ms, claim_expires_ts_ms, graph_id, graph_revision, intent_ref
    );

COMMIT;
