-- TASK-0472 / LTE-WP-32: durable ReviewStore, bound resume tokens, checkpoints.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS agent_runtime_review (
    review_id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL CHECK(schema_version = 'Natsume.agent.review_item.v1'),
    review_type TEXT NOT NULL CHECK(review_type IN (
        'PRE_EXECUTION_APPROVAL',
        'POST_EXECUTION_ACCEPTANCE'
    )),
    subject_ref TEXT NOT NULL CHECK(length(trim(subject_ref)) > 0),
    subject_version INTEGER NOT NULL CHECK(subject_version >= 0),
    requester TEXT NOT NULL CHECK(length(trim(requester)) > 0),
    reason TEXT NOT NULL CHECK(length(trim(reason)) > 0),
    risk_level INTEGER NOT NULL CHECK(risk_level BETWEEN 1 AND 5),
    side_effects_json TEXT NOT NULL DEFAULT '[]',
    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL CHECK(status IN (
        'PENDING',
        'APPROVED',
        'REJECTED',
        'EXPIRED',
        'CONSUMED',
        'CANCELLED'
    )),
    status_version INTEGER NOT NULL DEFAULT 0 CHECK(status_version >= 0),
    expires_at INTEGER NOT NULL CHECK(expires_at >= 0),
    resume_phase TEXT NOT NULL CHECK(resume_phase IN (
        'PRE_EXECUTION',
        'POST_EXECUTION_ACCEPTANCE'
    )),
    diff_ref TEXT,
    checkpoint_id TEXT NOT NULL CHECK(length(trim(checkpoint_id)) > 0),
    decision_actor TEXT,
    token_ref TEXT,
    resume_count INTEGER NOT NULL DEFAULT 0 CHECK(resume_count >= 0),
    resume_result_ref TEXT,
    work_order_id TEXT,
    task_graph_id TEXT,
    task_node_id TEXT,
    review_json TEXT NOT NULL,
    created_ts_ms INTEGER NOT NULL CHECK(created_ts_ms >= 0),
    updated_ts_ms INTEGER NOT NULL CHECK(updated_ts_ms >= created_ts_ms),
    CHECK(
        (review_type = 'PRE_EXECUTION_APPROVAL' AND resume_phase = 'PRE_EXECUTION')
        OR (
            review_type = 'POST_EXECUTION_ACCEPTANCE'
            AND resume_phase = 'POST_EXECUTION_ACCEPTANCE'
        )
    ),
    CHECK(
        status NOT IN ('APPROVED', 'CONSUMED')
        OR (
            decision_actor IS NOT NULL
            AND length(trim(decision_actor)) > 0
            AND token_ref IS NOT NULL
            AND length(trim(token_ref)) > 0
        )
    ),
    CHECK(status != 'CONSUMED' OR resume_count >= 1)
);

CREATE TABLE IF NOT EXISTS agent_runtime_review_token (
    token_digest TEXT PRIMARY KEY,
    token_ref_public TEXT NOT NULL UNIQUE,
    review_id TEXT NOT NULL,
    token_type TEXT NOT NULL CHECK(token_type = 'review_resume'),
    subject_ref TEXT NOT NULL CHECK(length(trim(subject_ref)) > 0),
    subject_version INTEGER NOT NULL CHECK(subject_version >= 0),
    resume_phase TEXT NOT NULL CHECK(resume_phase IN (
        'PRE_EXECUTION',
        'POST_EXECUTION_ACCEPTANCE'
    )),
    checkpoint_id TEXT NOT NULL CHECK(length(trim(checkpoint_id)) > 0),
    issued_ts_ms INTEGER NOT NULL CHECK(issued_ts_ms >= 0),
    expires_ts_ms INTEGER NOT NULL CHECK(expires_ts_ms > issued_ts_ms),
    state TEXT NOT NULL CHECK(state IN (
        'AVAILABLE',
        'CONSUMED',
        'EXPIRED',
        'REVOKED'
    )),
    consumed_ts_ms INTEGER,
    consumed_result_ref TEXT,
    created_ts_ms INTEGER NOT NULL CHECK(created_ts_ms >= 0),
    updated_ts_ms INTEGER NOT NULL CHECK(updated_ts_ms >= created_ts_ms),
    FOREIGN KEY (review_id) REFERENCES agent_runtime_review(review_id) ON DELETE RESTRICT,
    CHECK(state != 'CONSUMED' OR consumed_ts_ms IS NOT NULL)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_runtime_review_available_token
    ON agent_runtime_review_token(review_id)
    WHERE state = 'AVAILABLE';

CREATE INDEX IF NOT EXISTS idx_agent_runtime_review_status
    ON agent_runtime_review(status, expires_at, updated_ts_ms);

CREATE INDEX IF NOT EXISTS idx_agent_runtime_review_subject
    ON agent_runtime_review(subject_ref, subject_version, resume_phase);

CREATE INDEX IF NOT EXISTS idx_agent_runtime_review_token_review
    ON agent_runtime_review_token(review_id, state, expires_ts_ms);

COMMIT;
