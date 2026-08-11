-- DL-04: make replacement governed Chat attempts fail closed before another
-- provider call can recreate a visible sentence or mask an unknown outcome.

BEGIN IMMEDIATE;

CREATE INDEX IF NOT EXISTS idx_task_runtime_chat_delivery_recovery_scope
    ON task_runtime_chat_delivery_event(
        graph_id,
        graph_revision,
        node_id,
        request_id,
        session_id,
        principal_id,
        self_id,
        relationship_subject_id,
        workspace_id,
        attempt_id
    );

COMMIT;
