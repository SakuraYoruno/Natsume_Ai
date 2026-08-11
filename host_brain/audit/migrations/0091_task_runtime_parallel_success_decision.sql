-- NE-01 B5: canonical whole-graph success decision.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS task_runtime_parallel_success_decision (
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
        budget_version_after = budget_version_before + 1
    ),
    result_node_id TEXT NOT NULL,
    result_attempt_id TEXT NOT NULL,
    result_receipt_ref TEXT NOT NULL CHECK(length(trim(result_receipt_ref)) > 0),
    payload_json TEXT NOT NULL CHECK(length(payload_json) > 0),
    payload_sha256 TEXT NOT NULL CHECK(
        length(payload_sha256) = 71
        AND substr(payload_sha256, 1, 7) = 'sha256:'
        AND substr(payload_sha256, 8) NOT GLOB '*[^0-9a-f]*'
    ),
    graph_outbox_id TEXT NOT NULL,
    budget_outbox_id TEXT NOT NULL,
    created_ts_ms INTEGER NOT NULL CHECK(created_ts_ms >= 0),
    UNIQUE(graph_id, graph_revision),
    CHECK(graph_outbox_id <> budget_outbox_id),
    FOREIGN KEY (graph_id, graph_revision)
        REFERENCES task_runtime_graph(graph_id, revision) ON DELETE RESTRICT,
    FOREIGN KEY (budget_id)
        REFERENCES task_runtime_budget_reservation(budget_id) ON DELETE RESTRICT,
    FOREIGN KEY (graph_id, graph_revision, result_node_id)
        REFERENCES task_runtime_node(graph_id, graph_revision, node_id) ON DELETE RESTRICT,
    FOREIGN KEY (result_attempt_id, graph_id, graph_revision, result_node_id)
        REFERENCES task_runtime_attempt(
            attempt_id, graph_id, graph_revision, node_id
        ) ON DELETE RESTRICT,
    FOREIGN KEY (graph_outbox_id)
        REFERENCES task_runtime_outbox(outbox_id) ON DELETE RESTRICT,
    FOREIGN KEY (budget_outbox_id)
        REFERENCES task_runtime_outbox(outbox_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_task_runtime_parallel_success_graph
    ON task_runtime_parallel_success_decision(
        graph_id, graph_revision, graph_version_after
    );

COMMIT;
