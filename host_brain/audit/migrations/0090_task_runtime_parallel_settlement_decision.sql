-- NE-01 B4: canonical, independently resolvable parallel settlement decisions.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS task_runtime_parallel_settlement_decision (
    decision_ref TEXT PRIMARY KEY CHECK(length(trim(decision_ref)) > 0),
    schema_version TEXT NOT NULL CHECK(length(trim(schema_version)) > 0),
    graph_id TEXT NOT NULL,
    graph_revision INTEGER NOT NULL CHECK(graph_revision >= 1),
    graph_version_before INTEGER NOT NULL CHECK(graph_version_before >= 0),
    graph_version_after INTEGER NOT NULL CHECK(
        graph_version_after = graph_version_before + 1
    ),
    budget_id TEXT NOT NULL,
    budget_version_before INTEGER NOT NULL CHECK(budget_version_before >= 0),
    budget_version_after INTEGER NOT NULL CHECK(
        budget_version_after = budget_version_before
        OR budget_version_after = budget_version_before + 1
    ),
    decision_kind TEXT NOT NULL CHECK(decision_kind IN ('BUDGET', 'FAILURE', 'REVIEW')),
    selected_node_id TEXT NOT NULL,
    graph_target_status TEXT NOT NULL CHECK(graph_target_status IN ('FAILED', 'BLOCKED')),
    budget_target_status TEXT CHECK(
        budget_target_status IS NULL OR budget_target_status = 'RELEASED'
    ),
    payload_json TEXT NOT NULL CHECK(length(payload_json) > 0),
    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256) = 71),
    graph_outbox_id TEXT NOT NULL,
    budget_outbox_id TEXT,
    created_ts_ms INTEGER NOT NULL,
    UNIQUE(graph_id, graph_revision, graph_version_after),
    FOREIGN KEY (graph_id, graph_revision)
        REFERENCES task_runtime_graph(graph_id, revision) ON DELETE RESTRICT,
    FOREIGN KEY (budget_id)
        REFERENCES task_runtime_budget_reservation(budget_id) ON DELETE RESTRICT,
    FOREIGN KEY (graph_id, graph_revision, selected_node_id)
        REFERENCES task_runtime_node(graph_id, graph_revision, node_id) ON DELETE RESTRICT,
    FOREIGN KEY (graph_outbox_id)
        REFERENCES task_runtime_outbox(outbox_id) ON DELETE RESTRICT,
    FOREIGN KEY (budget_outbox_id)
        REFERENCES task_runtime_outbox(outbox_id) ON DELETE RESTRICT,
    CHECK (
        (budget_target_status IS NULL AND budget_outbox_id IS NULL
            AND budget_version_after = budget_version_before)
        OR
        (budget_target_status = 'RELEASED' AND budget_outbox_id IS NOT NULL
            AND budget_version_after = budget_version_before + 1)
    )
);

CREATE INDEX IF NOT EXISTS idx_task_runtime_parallel_decision_graph
    ON task_runtime_parallel_settlement_decision(
        graph_id, graph_revision, graph_version_after
    );

COMMIT;
