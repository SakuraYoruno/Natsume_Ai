-- NE-01: append-only TaskNode acceptance decisions.
--
-- Verification completion is not sufficient evidence for Acceptance.  Every
-- ACCEPTED or WAITING_ACCEPTANCE_REVIEW transition now freezes the canonical
-- AcceptanceJudgement, exact Attempt, Node Outbox and optional Review/Graph
-- blocking authority in the same SQLite transaction.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS task_runtime_node_acceptance_decision (
    acceptance_ref TEXT PRIMARY KEY
        CHECK(length(trim(acceptance_ref)) > 0),
    schema_version TEXT NOT NULL
        CHECK(schema_version = 'Natsume.task_runtime.node_acceptance_decision.v1'),
    judgement_id TEXT NOT NULL
        CHECK(length(trim(judgement_id)) > 0),
    graph_id TEXT NOT NULL,
    graph_revision INTEGER NOT NULL CHECK(graph_revision >= 1),
    node_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    node_version_before INTEGER NOT NULL CHECK(node_version_before >= 0),
    node_version_after INTEGER NOT NULL
        CHECK(node_version_after = node_version_before + 1),
    target_status TEXT NOT NULL
        CHECK(target_status IN ('ACCEPTED', 'WAITING_ACCEPTANCE_REVIEW')),
    result_json TEXT NOT NULL CHECK(json_valid(result_json)),
    result_sha256 TEXT NOT NULL
        CHECK(length(result_sha256) = 64 AND result_sha256 GLOB '[0-9a-f]*'),
    evidence_refs_json TEXT NOT NULL
        CHECK(json_valid(evidence_refs_json) AND json_type(evidence_refs_json) = 'array'),
    node_outbox_id TEXT NOT NULL UNIQUE,
    review_id TEXT,
    graph_version_before INTEGER CHECK(graph_version_before >= 0),
    graph_version_after INTEGER
        CHECK(graph_version_after = graph_version_before + 1),
    graph_outbox_id TEXT UNIQUE,
    created_ts_ms INTEGER NOT NULL CHECK(created_ts_ms >= 0),
    UNIQUE(graph_id, graph_revision, node_id, node_version_after),
    UNIQUE(judgement_id, graph_id, graph_revision, node_id, attempt_id),
    CHECK(
        (
            target_status = 'ACCEPTED'
            AND review_id IS NULL
            AND graph_version_before IS NULL
            AND graph_version_after IS NULL
            AND graph_outbox_id IS NULL
        )
        OR (
            target_status = 'WAITING_ACCEPTANCE_REVIEW'
            AND review_id IS NOT NULL
            AND (
                (
                    graph_outbox_id IS NULL
                    AND graph_version_before IS NULL
                    AND graph_version_after IS NULL
                )
                OR (
                    graph_outbox_id IS NOT NULL
                    AND graph_version_before IS NOT NULL
                    AND graph_version_after IS NOT NULL
                )
            )
        )
    ),
    FOREIGN KEY(graph_id, graph_revision, node_id)
        REFERENCES task_runtime_node(graph_id, graph_revision, node_id)
        ON DELETE RESTRICT,
    FOREIGN KEY(attempt_id, graph_id, graph_revision, node_id)
        REFERENCES task_runtime_attempt(
            attempt_id, graph_id, graph_revision, node_id
        ) ON DELETE RESTRICT,
    FOREIGN KEY(node_outbox_id)
        REFERENCES task_runtime_outbox(outbox_id) ON DELETE RESTRICT,
    FOREIGN KEY(review_id)
        REFERENCES agent_runtime_review(review_id) ON DELETE RESTRICT,
    FOREIGN KEY(graph_outbox_id)
        REFERENCES task_runtime_outbox(outbox_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_task_runtime_node_acceptance_graph
    ON task_runtime_node_acceptance_decision(
        graph_id, graph_revision, node_id, node_version_after
    );

CREATE INDEX IF NOT EXISTS idx_task_runtime_node_acceptance_attempt
    ON task_runtime_node_acceptance_decision(attempt_id, created_ts_ms);

CREATE INDEX IF NOT EXISTS idx_task_runtime_node_acceptance_review
    ON task_runtime_node_acceptance_decision(review_id)
    WHERE review_id IS NOT NULL;

CREATE TRIGGER IF NOT EXISTS trg_task_runtime_node_acceptance_no_update
BEFORE UPDATE ON task_runtime_node_acceptance_decision
BEGIN
    SELECT RAISE(ABORT, 'task node acceptance decision is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_task_runtime_node_acceptance_no_delete
BEFORE DELETE ON task_runtime_node_acceptance_decision
BEGIN
    SELECT RAISE(ABORT, 'task node acceptance decision cannot be deleted');
END;

COMMIT;
