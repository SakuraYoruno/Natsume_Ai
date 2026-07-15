BEGIN IMMEDIATE;

-- GR-06: durable Acceptance Outbox foundation.  Effects are staged before
-- dispatch, but may only be claimed after their bound durable acceptance scope
-- has committed.  The monotonically increasing fencing token prevents an
-- expired dispatcher from committing over a newer claimant.
CREATE UNIQUE INDEX IF NOT EXISTS uq_task_runtime_attempt_effect_binding
    ON task_runtime_attempt(attempt_id, graph_id, graph_revision, node_id);

CREATE TABLE IF NOT EXISTS task_runtime_acceptance_effect_intent (
    effect_id TEXT PRIMARY KEY
        CHECK(length(trim(effect_id)) BETWEEN 1 AND 512),
    graph_id TEXT NOT NULL,
    graph_revision INTEGER NOT NULL CHECK(graph_revision >= 1),
    node_id TEXT NOT NULL CHECK(length(trim(node_id)) > 0),
    attempt_id TEXT NOT NULL CHECK(length(trim(attempt_id)) > 0),
    commit_scope TEXT NOT NULL CHECK(commit_scope IN ('NODE', 'GRAPH')),
    effect_type TEXT NOT NULL
        CHECK(
            length(effect_type) BETWEEN 3 AND 128
            AND effect_type NOT GLOB '*[^a-z0-9_.]*'
            AND substr(effect_type, 1, 1) GLOB '[a-z]'
            AND instr(effect_type, '.') > 1
            AND substr(effect_type, -1, 1) <> '.'
            AND effect_type NOT LIKE '%..%'
        ),
    payload_ref TEXT NOT NULL
        CHECK(length(trim(payload_ref)) BETWEEN 1 AND 512),
    payload_json TEXT NOT NULL
        CHECK(length(payload_json) BETWEEN 2 AND 262144),
    payload_sha256 TEXT NOT NULL
        CHECK(
            length(payload_sha256) = 71
            AND substr(payload_sha256, 1, 7) = 'sha256:'
            AND substr(payload_sha256, 8) NOT GLOB '*[^0-9a-f]*'
        ),
    idempotency_key TEXT NOT NULL UNIQUE
        CHECK(length(trim(idempotency_key)) BETWEEN 1 AND 512),
    status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK(status IN ('PENDING', 'CLAIMED', 'COMMITTED', 'FAILED')),
    available_ts_ms INTEGER NOT NULL CHECK(available_ts_ms >= 0),
    lease_owner TEXT,
    lease_expires_ts_ms INTEGER,
    fencing_token INTEGER NOT NULL DEFAULT 0 CHECK(fencing_token >= 0),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
    status_version INTEGER NOT NULL DEFAULT 0 CHECK(status_version >= 0),
    receipt_ref TEXT,
    reason_code TEXT,
    last_error TEXT,
    created_ts_ms INTEGER NOT NULL CHECK(created_ts_ms >= 0),
    updated_ts_ms INTEGER NOT NULL CHECK(updated_ts_ms >= 0),
    committed_ts_ms INTEGER,
    failed_ts_ms INTEGER,
    FOREIGN KEY (graph_id, graph_revision)
        REFERENCES task_runtime_graph(graph_id, revision) ON DELETE RESTRICT,
    FOREIGN KEY (graph_id, graph_revision, node_id)
        REFERENCES task_runtime_node(graph_id, graph_revision, node_id) ON DELETE RESTRICT,
    FOREIGN KEY (attempt_id, graph_id, graph_revision, node_id)
        REFERENCES task_runtime_attempt(
            attempt_id, graph_id, graph_revision, node_id
        ) ON DELETE RESTRICT,
    CHECK (
        (
            status = 'PENDING'
            AND lease_owner IS NULL
            AND lease_expires_ts_ms IS NULL
            AND receipt_ref IS NULL
            AND committed_ts_ms IS NULL
            AND failed_ts_ms IS NULL
            AND (
                (
                    fencing_token = 0
                    AND attempt_count = 0
                    AND status_version = 0
                    AND reason_code IS NULL
                    AND last_error IS NULL
                )
                OR
                (
                    fencing_token >= 1
                    AND attempt_count >= 1
                    AND status_version >= 2
                    AND reason_code IS NOT NULL
                    AND last_error IS NOT NULL
                )
            )
        )
        OR
        (
            status = 'CLAIMED'
            AND lease_owner IS NOT NULL
            AND lease_expires_ts_ms IS NOT NULL
            AND fencing_token >= 1
            AND attempt_count >= 1
            AND status_version >= 1
            AND receipt_ref IS NULL
            AND reason_code IS NULL
            AND last_error IS NULL
            AND committed_ts_ms IS NULL
            AND failed_ts_ms IS NULL
        )
        OR
        (
            status = 'COMMITTED'
            AND lease_owner IS NOT NULL
            AND lease_expires_ts_ms IS NULL
            AND fencing_token >= 1
            AND attempt_count >= 1
            AND status_version >= 2
            AND receipt_ref IS NOT NULL
            AND reason_code IS NULL
            AND last_error IS NULL
            AND committed_ts_ms IS NOT NULL
            AND failed_ts_ms IS NULL
        )
        OR
        (
            status = 'FAILED'
            AND lease_owner IS NOT NULL
            AND lease_expires_ts_ms IS NULL
            AND fencing_token >= 1
            AND attempt_count >= 1
            AND status_version >= 2
            AND receipt_ref IS NULL
            AND committed_ts_ms IS NULL
            AND failed_ts_ms IS NOT NULL
            AND reason_code IS NOT NULL
            AND last_error IS NOT NULL
        )
    )
);

CREATE INDEX IF NOT EXISTS idx_task_runtime_acceptance_effect_ready
    ON task_runtime_acceptance_effect_intent(
        status, available_ts_ms, created_ts_ms, effect_id
    );

CREATE INDEX IF NOT EXISTS idx_task_runtime_acceptance_effect_lease
    ON task_runtime_acceptance_effect_intent(
        status, lease_expires_ts_ms, created_ts_ms, effect_id
    );

CREATE INDEX IF NOT EXISTS idx_task_runtime_acceptance_effect_binding
    ON task_runtime_acceptance_effect_intent(
        graph_id, graph_revision, node_id, attempt_id, commit_scope, status
    );

COMMIT;
