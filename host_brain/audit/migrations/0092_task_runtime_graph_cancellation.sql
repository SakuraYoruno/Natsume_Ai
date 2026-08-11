-- NE-01 B5C: durable graph-scoped cancellation authority.

BEGIN IMMEDIATE;

CREATE UNIQUE INDEX IF NOT EXISTS uq_task_runtime_budget_graph_binding
    ON task_runtime_budget_reservation(budget_id, graph_id, graph_revision);

CREATE TABLE IF NOT EXISTS task_runtime_graph_cancellation_intent (
    intent_ref TEXT PRIMARY KEY CHECK(length(trim(intent_ref)) > 0),
    schema_version TEXT NOT NULL CHECK(length(trim(schema_version)) > 0),
    request_id TEXT NOT NULL UNIQUE CHECK(length(trim(request_id)) > 0),
    request_identity TEXT NOT NULL CHECK(length(trim(request_identity)) > 0),
    source TEXT NOT NULL CHECK(length(trim(source)) > 0),
    reason_code TEXT NOT NULL CHECK(length(trim(reason_code)) > 0),
    graph_id TEXT NOT NULL,
    graph_revision INTEGER NOT NULL CHECK(graph_revision >= 1),
    graph_version_before INTEGER NOT NULL CHECK(graph_version_before >= 0),
    graph_version_after INTEGER NOT NULL CHECK(
        graph_version_after = graph_version_before + 1
    ),
    budget_id TEXT,
    budget_version_at_request INTEGER CHECK(budget_version_at_request >= 0),
    payload_json TEXT NOT NULL CHECK(length(payload_json) > 0),
    payload_sha256 TEXT NOT NULL CHECK(
        length(payload_sha256) = 71
        AND substr(payload_sha256, 1, 7) = 'sha256:'
        AND substr(payload_sha256, 8) NOT GLOB '*[^0-9a-f]*'
    ),
    graph_outbox_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'REQUESTED'
        CHECK(status IN ('REQUESTED', 'FINALIZED')),
    status_version INTEGER NOT NULL DEFAULT 0 CHECK(status_version >= 0),
    requested_ts_ms INTEGER NOT NULL CHECK(requested_ts_ms >= 0),
    finalized_ts_ms INTEGER,
    UNIQUE(graph_id, graph_revision),
    UNIQUE(intent_ref, graph_id, graph_revision),
    FOREIGN KEY (graph_id, graph_revision)
        REFERENCES task_runtime_graph(graph_id, revision) ON DELETE RESTRICT,
    FOREIGN KEY (budget_id, graph_id, graph_revision)
        REFERENCES task_runtime_budget_reservation(
            budget_id, graph_id, graph_revision
        ) ON DELETE RESTRICT,
    FOREIGN KEY (graph_outbox_id)
        REFERENCES task_runtime_outbox(outbox_id) ON DELETE RESTRICT,
    CHECK (
        (budget_id IS NULL AND budget_version_at_request IS NULL)
        OR (budget_id IS NOT NULL AND budget_version_at_request IS NOT NULL)
    ),
    CHECK (
        (status = 'REQUESTED' AND status_version = 0 AND finalized_ts_ms IS NULL)
        OR (status = 'FINALIZED' AND status_version = 1
            AND finalized_ts_ms IS NOT NULL
            AND finalized_ts_ms >= requested_ts_ms)
    )
);

CREATE TABLE IF NOT EXISTS task_runtime_graph_cancellation_decision (
    decision_ref TEXT PRIMARY KEY CHECK(length(trim(decision_ref)) > 0),
    schema_version TEXT NOT NULL CHECK(length(trim(schema_version)) > 0),
    intent_ref TEXT NOT NULL UNIQUE,
    graph_id TEXT NOT NULL,
    graph_revision INTEGER NOT NULL CHECK(graph_revision >= 1),
    graph_version_before INTEGER NOT NULL CHECK(graph_version_before >= 0),
    graph_version_after INTEGER NOT NULL CHECK(
        graph_version_after = graph_version_before + 1
    ),
    budget_id TEXT,
    budget_version_before INTEGER CHECK(budget_version_before >= 0),
    budget_version_after INTEGER CHECK(
        budget_version_after >= budget_version_before
        AND budget_version_after <= budget_version_before + 1
    ),
    payload_json TEXT NOT NULL CHECK(length(payload_json) > 0),
    payload_sha256 TEXT NOT NULL CHECK(
        length(payload_sha256) = 71
        AND substr(payload_sha256, 1, 7) = 'sha256:'
        AND substr(payload_sha256, 8) NOT GLOB '*[^0-9a-f]*'
    ),
    graph_outbox_id TEXT NOT NULL,
    budget_outbox_id TEXT,
    created_ts_ms INTEGER NOT NULL CHECK(created_ts_ms >= 0),
    UNIQUE(graph_id, graph_revision),
    CHECK(budget_outbox_id IS NULL OR graph_outbox_id <> budget_outbox_id),
    FOREIGN KEY (intent_ref, graph_id, graph_revision)
        REFERENCES task_runtime_graph_cancellation_intent(
            intent_ref, graph_id, graph_revision
        ) ON DELETE RESTRICT,
    FOREIGN KEY (graph_id, graph_revision)
        REFERENCES task_runtime_graph(graph_id, revision) ON DELETE RESTRICT,
    FOREIGN KEY (budget_id, graph_id, graph_revision)
        REFERENCES task_runtime_budget_reservation(
            budget_id, graph_id, graph_revision
        ) ON DELETE RESTRICT,
    FOREIGN KEY (graph_outbox_id)
        REFERENCES task_runtime_outbox(outbox_id) ON DELETE RESTRICT,
    FOREIGN KEY (budget_outbox_id)
        REFERENCES task_runtime_outbox(outbox_id) ON DELETE RESTRICT
    ,CHECK (
        (budget_id IS NULL AND budget_version_before IS NULL
            AND budget_version_after IS NULL AND budget_outbox_id IS NULL)
        OR
        (budget_id IS NOT NULL AND budget_version_before IS NOT NULL
            AND budget_version_after IS NOT NULL)
    )
    ,CHECK (
        (budget_id IS NULL)
        OR (budget_version_after = budget_version_before
            AND budget_outbox_id IS NULL)
        OR (budget_version_after = budget_version_before + 1
            AND budget_outbox_id IS NOT NULL)
    )
);

-- Existing 0087 effect rows cannot gain a new status without rebuilding the
-- live queue.  This append-only authority fences only effects lacking a
-- committed NODE acceptance boundary: their PENDING rows become logically
-- CANCELLED, CLAIMED rows require reconciliation, and terminal rows remain
-- irreversible evidence.  Accepted NODE effects do not enter this table.
CREATE TABLE IF NOT EXISTS task_runtime_graph_cancellation_effect_fence (
    effect_id TEXT PRIMARY KEY,
    intent_ref TEXT NOT NULL,
    observed_status TEXT NOT NULL
        CHECK(observed_status IN ('PENDING', 'CLAIMED', 'COMMITTED', 'FAILED')),
    resolution_status TEXT NOT NULL CHECK(resolution_status IN (
        'CANCELLED', 'RECONCILIATION_REQUIRED',
        'COMMITTED_EVIDENCE', 'FAILED_EVIDENCE'
    )),
    observed_status_version INTEGER NOT NULL CHECK(observed_status_version >= 0),
    created_ts_ms INTEGER NOT NULL CHECK(created_ts_ms >= 0),
    resolved_ts_ms INTEGER,
    FOREIGN KEY (effect_id)
        REFERENCES task_runtime_acceptance_effect_intent(effect_id) ON DELETE RESTRICT,
    FOREIGN KEY (intent_ref)
        REFERENCES task_runtime_graph_cancellation_intent(intent_ref) ON DELETE RESTRICT,
    CHECK (
        (resolution_status = 'RECONCILIATION_REQUIRED' AND resolved_ts_ms IS NULL)
        OR (resolution_status <> 'RECONCILIATION_REQUIRED' AND resolved_ts_ms IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_task_runtime_graph_cancellation_status
    ON task_runtime_graph_cancellation_intent(status, requested_ts_ms, graph_id);

CREATE INDEX IF NOT EXISTS idx_task_runtime_graph_cancellation_effect_resolution
    ON task_runtime_graph_cancellation_effect_fence(
        intent_ref, resolution_status, effect_id
    );

COMMIT;
