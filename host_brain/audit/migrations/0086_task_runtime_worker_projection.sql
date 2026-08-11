BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS task_runtime_worker_projection (
    projection_id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    command_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    graph_id TEXT NOT NULL,
    graph_revision INTEGER NOT NULL CHECK (graph_revision >= 1),
    node_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    route_type TEXT NOT NULL CHECK (route_type IN ('chat', 'skill', 'delegate')),
    target_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('APPROVE', 'REJECT')),
    projection_json TEXT NOT NULL,
    created_ts_ms INTEGER NOT NULL CHECK (created_ts_ms >= 0),
    UNIQUE(graph_id, graph_revision, node_id, attempt_id),
    UNIQUE(payload_digest, graph_id, graph_revision, node_id, attempt_id)
);

CREATE INDEX IF NOT EXISTS idx_task_runtime_worker_projection_graph
ON task_runtime_worker_projection(graph_id, graph_revision, node_id);

CREATE INDEX IF NOT EXISTS idx_task_runtime_worker_projection_request
ON task_runtime_worker_projection(request_id, created_ts_ms);

COMMIT;
