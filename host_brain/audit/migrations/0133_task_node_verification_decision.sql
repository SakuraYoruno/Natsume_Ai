-- NE-01: append-only TaskNode verification decisions.
--
-- A VERIFYING node must not become VERIFIED/VERIFICATION_FAILED from an
-- in-memory result that disappears after process loss.  Every new transition
-- records the canonical VerificationResult, exact Attempt and Node Outbox in
-- the same SQLite transaction.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS task_runtime_node_verification_decision (
    verification_ref TEXT PRIMARY KEY
        CHECK(length(trim(verification_ref)) > 0),
    schema_version TEXT NOT NULL
        CHECK(schema_version = 'Natsume.task_runtime.node_verification_decision.v1'),
    verification_id TEXT NOT NULL
        CHECK(length(trim(verification_id)) > 0),
    graph_id TEXT NOT NULL,
    graph_revision INTEGER NOT NULL CHECK(graph_revision >= 1),
    node_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    node_version_before INTEGER NOT NULL CHECK(node_version_before >= 0),
    node_version_after INTEGER NOT NULL
        CHECK(node_version_after = node_version_before + 1),
    target_status TEXT NOT NULL
        CHECK(target_status IN ('VERIFIED', 'VERIFICATION_FAILED')),
    result_json TEXT NOT NULL CHECK(json_valid(result_json)),
    result_sha256 TEXT NOT NULL
        CHECK(length(result_sha256) = 64 AND result_sha256 GLOB '[0-9a-f]*'),
    evidence_refs_json TEXT NOT NULL
        CHECK(json_valid(evidence_refs_json) AND json_type(evidence_refs_json) = 'array'),
    node_outbox_id TEXT NOT NULL UNIQUE,
    created_ts_ms INTEGER NOT NULL CHECK(created_ts_ms >= 0),
    UNIQUE(graph_id, graph_revision, node_id, node_version_after),
    UNIQUE(verification_id, graph_id, graph_revision, node_id, attempt_id),
    FOREIGN KEY(graph_id, graph_revision, node_id)
        REFERENCES task_runtime_node(graph_id, graph_revision, node_id)
        ON DELETE RESTRICT,
    FOREIGN KEY(attempt_id, graph_id, graph_revision, node_id)
        REFERENCES task_runtime_attempt(
            attempt_id, graph_id, graph_revision, node_id
        ) ON DELETE RESTRICT,
    FOREIGN KEY(node_outbox_id)
        REFERENCES task_runtime_outbox(outbox_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_task_runtime_node_verification_graph
    ON task_runtime_node_verification_decision(
        graph_id, graph_revision, node_id, node_version_after
    );

CREATE INDEX IF NOT EXISTS idx_task_runtime_node_verification_attempt
    ON task_runtime_node_verification_decision(attempt_id, created_ts_ms);

CREATE TRIGGER IF NOT EXISTS trg_task_runtime_node_verification_no_update
BEFORE UPDATE ON task_runtime_node_verification_decision
BEGIN
    SELECT RAISE(ABORT, 'task node verification decision is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_task_runtime_node_verification_no_delete
BEFORE DELETE ON task_runtime_node_verification_decision
BEGIN
    SELECT RAISE(ABORT, 'task node verification decision cannot be deleted');
END;

COMMIT;
