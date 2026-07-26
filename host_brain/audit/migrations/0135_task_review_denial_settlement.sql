-- NE-01: atomically settle a terminal TaskNode Review decision.
--
-- A rejected or expired Review previously advanced Node, Graph, Budget and
-- their Outboxes through separate transactions.  This append-only receipt
-- freezes the exact Review and optional post-execution Acceptance lineage,
-- then binds all three terminal mutations to one SQLite unit of work.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS task_runtime_review_denial_settlement (
    settlement_ref TEXT PRIMARY KEY
        CHECK(length(trim(settlement_ref)) > 0),
    schema_version TEXT NOT NULL
        CHECK(schema_version = 'Natsume.task_runtime.review_denial_settlement.v1'),
    review_id TEXT NOT NULL UNIQUE,
    review_status TEXT NOT NULL
        CHECK(review_status IN ('REJECTED', 'EXPIRED', 'CANCELLED')),
    review_status_version INTEGER NOT NULL CHECK(review_status_version >= 1),
    review_sha256 TEXT NOT NULL
        CHECK(length(review_sha256) = 64 AND review_sha256 GLOB '[0-9a-f]*'),
    resume_phase TEXT NOT NULL
        CHECK(resume_phase IN ('PRE_EXECUTION', 'POST_EXECUTION_ACCEPTANCE')),
    graph_id TEXT NOT NULL,
    graph_revision INTEGER NOT NULL CHECK(graph_revision >= 1),
    node_id TEXT NOT NULL,
    node_version_before INTEGER NOT NULL CHECK(node_version_before >= 0),
    node_version_after INTEGER NOT NULL
        CHECK(node_version_after = node_version_before + 1),
    node_target_status TEXT NOT NULL
        CHECK(node_target_status IN ('FAILED', 'CANCELLED')),
    graph_status_before TEXT NOT NULL
        CHECK(graph_status_before IN ('ACTIVE', 'BLOCKED')),
    graph_version_before INTEGER NOT NULL CHECK(graph_version_before >= 0),
    graph_version_after INTEGER NOT NULL
        CHECK(graph_version_after = graph_version_before + 1),
    graph_target_status TEXT NOT NULL
        CHECK(graph_target_status IN ('FAILED', 'CANCELLED')),
    budget_id TEXT NOT NULL,
    budget_version_before INTEGER NOT NULL CHECK(budget_version_before >= 0),
    budget_version_after INTEGER NOT NULL
        CHECK(budget_version_after = budget_version_before + 1),
    acceptance_ref TEXT,
    attempt_id TEXT,
    blocking_decision_ref TEXT,
    node_outbox_id TEXT NOT NULL UNIQUE,
    graph_outbox_id TEXT NOT NULL UNIQUE,
    budget_outbox_id TEXT NOT NULL UNIQUE,
    created_ts_ms INTEGER NOT NULL CHECK(created_ts_ms >= 0),
    UNIQUE(graph_id, graph_revision, node_id, node_version_after),
    FOREIGN KEY(review_id)
        REFERENCES agent_runtime_review(review_id) ON DELETE RESTRICT,
    FOREIGN KEY(graph_id, graph_revision, node_id)
        REFERENCES task_runtime_node(graph_id, graph_revision, node_id)
        ON DELETE RESTRICT,
    FOREIGN KEY(graph_id, graph_revision)
        REFERENCES task_runtime_graph(graph_id, revision) ON DELETE RESTRICT,
    FOREIGN KEY(budget_id)
        REFERENCES task_runtime_budget_reservation(budget_id) ON DELETE RESTRICT,
    FOREIGN KEY(acceptance_ref)
        REFERENCES task_runtime_node_acceptance_decision(acceptance_ref)
        ON DELETE RESTRICT,
    FOREIGN KEY(attempt_id)
        REFERENCES task_runtime_attempt(attempt_id) ON DELETE RESTRICT,
    FOREIGN KEY(blocking_decision_ref)
        REFERENCES task_runtime_parallel_settlement_decision(decision_ref)
        ON DELETE RESTRICT,
    FOREIGN KEY(node_outbox_id)
        REFERENCES task_runtime_outbox(outbox_id) ON DELETE RESTRICT,
    FOREIGN KEY(graph_outbox_id)
        REFERENCES task_runtime_outbox(outbox_id) ON DELETE RESTRICT,
    FOREIGN KEY(budget_outbox_id)
        REFERENCES task_runtime_outbox(outbox_id) ON DELETE RESTRICT,
    CHECK(
        (
            resume_phase = 'PRE_EXECUTION'
            AND node_target_status = 'CANCELLED'
            AND graph_target_status = 'CANCELLED'
            AND acceptance_ref IS NULL
            AND attempt_id IS NULL
        )
        OR (
            resume_phase = 'POST_EXECUTION_ACCEPTANCE'
            AND node_target_status = 'FAILED'
            AND graph_target_status = 'FAILED'
            AND acceptance_ref IS NOT NULL
            AND attempt_id IS NOT NULL
        )
    )
);

CREATE INDEX IF NOT EXISTS idx_task_runtime_review_denial_graph
    ON task_runtime_review_denial_settlement(
        graph_id, graph_revision, graph_version_after
    );

CREATE TRIGGER IF NOT EXISTS trg_task_runtime_review_denial_no_update
BEFORE UPDATE ON task_runtime_review_denial_settlement
BEGIN
    SELECT RAISE(ABORT, 'review denial settlement is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_task_runtime_review_denial_no_delete
BEFORE DELETE ON task_runtime_review_denial_settlement
BEGIN
    SELECT RAISE(ABORT, 'review denial settlement cannot be deleted');
END;

CREATE TRIGGER IF NOT EXISTS trg_agent_runtime_settled_review_no_update
BEFORE UPDATE ON agent_runtime_review
WHEN EXISTS (
    SELECT 1 FROM task_runtime_review_denial_settlement
    WHERE review_id = OLD.review_id
)
BEGIN
    SELECT RAISE(ABORT, 'settled review decision is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_agent_runtime_settled_review_no_delete
BEFORE DELETE ON agent_runtime_review
WHEN EXISTS (
    SELECT 1 FROM task_runtime_review_denial_settlement
    WHERE review_id = OLD.review_id
)
BEGIN
    SELECT RAISE(ABORT, 'settled review decision cannot be deleted');
END;

COMMIT;
