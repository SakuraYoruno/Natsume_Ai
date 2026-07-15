-- NE-01 Lane B / B1: durable bounded-ready and retry scheduling state.
-- Execution remains sequential in B1; these columns freeze the state required
-- for an atomic claim and a restart-safe retry deadline.

BEGIN IMMEDIATE;

ALTER TABLE task_runtime_node
    ADD COLUMN retry_not_before_ms INTEGER
        CHECK(retry_not_before_ms IS NULL OR retry_not_before_ms >= 0);

CREATE INDEX IF NOT EXISTS idx_task_runtime_node_retry_due
    ON task_runtime_node(
        graph_id, graph_revision, status, retry_not_before_ms, node_id
    );

COMMIT;
