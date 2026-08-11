-- NE-01: cross-process continuation lease for terminal TaskNode reviews.
--
-- The ReviewItem and TaskNode remain the only business status authorities.
-- This table contains delivery coordination only and is deleted after the
-- reviewed node has durably crossed its bound waiting checkpoint.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS task_runtime_review_continuation (
    review_id TEXT NOT NULL PRIMARY KEY CHECK(length(trim(review_id)) > 0),
    graph_id TEXT NOT NULL CHECK(length(trim(graph_id)) > 0),
    graph_revision INTEGER NOT NULL CHECK(
        typeof(graph_revision) = 'integer' AND graph_revision >= 1
    ),
    node_id TEXT NOT NULL CHECK(length(trim(node_id)) > 0),
    subject_version INTEGER NOT NULL CHECK(
        typeof(subject_version) = 'integer' AND subject_version >= 0
    ),
    resume_phase TEXT NOT NULL CHECK(resume_phase IN (
        'PRE_EXECUTION',
        'POST_EXECUTION_ACCEPTANCE'
    )),
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
    FOREIGN KEY (review_id)
        REFERENCES agent_runtime_review(review_id) ON DELETE RESTRICT,
    FOREIGN KEY (graph_id, graph_revision, node_id)
        REFERENCES task_runtime_node(
            graph_id, graph_revision, node_id
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

CREATE UNIQUE INDEX IF NOT EXISTS uq_task_runtime_review_continuation_checkpoint
    ON task_runtime_review_continuation(
        graph_id, graph_revision, node_id, subject_version, resume_phase
    );

CREATE INDEX IF NOT EXISTS idx_task_runtime_review_continuation_ready
    ON task_runtime_review_continuation(
        available_ts_ms, claim_expires_ts_ms,
        graph_id, graph_revision, node_id, review_id
    );

COMMIT;
