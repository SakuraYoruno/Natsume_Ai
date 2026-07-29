-- DL-04: task-bound provisional text receipts for governed Chat streaming.
--
-- A sentence may become visible before Task Verification/Acceptance, but it
-- must first be bound to the immutable graph/node/attempt and verified node
-- grant.  This table is deliberately separate from the Digital Life journal:
-- it is not a self-affect mutation, a delivery effect, or a claim that Voice
-- or Avatar playback occurred.  It stores only assistant-visible text needed
-- for idempotent reconnect/reconciliation.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS task_runtime_governed_chat_stream_receipt (
    receipt_id TEXT PRIMARY KEY
        CHECK(length(trim(receipt_id)) BETWEEN 1 AND 1024),
    graph_id TEXT NOT NULL CHECK(length(trim(graph_id)) BETWEEN 1 AND 240),
    graph_revision INTEGER NOT NULL CHECK(graph_revision >= 1),
    node_id TEXT NOT NULL CHECK(length(trim(node_id)) BETWEEN 1 AND 240),
    attempt_id TEXT NOT NULL CHECK(length(trim(attempt_id)) BETWEEN 1 AND 240),
    sentence_index INTEGER NOT NULL CHECK(sentence_index >= 0),
    request_id TEXT NOT NULL CHECK(length(trim(request_id)) BETWEEN 1 AND 240),
    principal_id TEXT NOT NULL CHECK(length(trim(principal_id)) BETWEEN 1 AND 240),
    workspace_id TEXT NOT NULL CHECK(length(trim(workspace_id)) BETWEEN 1 AND 240),
    session_id TEXT NOT NULL CHECK(length(trim(session_id)) BETWEEN 1 AND 240),
    affective_turn_plan_id TEXT NOT NULL
        CHECK(length(trim(affective_turn_plan_id)) BETWEEN 1 AND 240),
    synchronization_id TEXT NOT NULL
        CHECK(length(trim(synchronization_id)) BETWEEN 1 AND 240),
    self_affect_revision INTEGER NOT NULL CHECK(self_affect_revision >= 0),
    sentence TEXT NOT NULL CHECK(length(trim(sentence)) BETWEEN 1 AND 2000),
    sentence_digest TEXT NOT NULL CHECK(length(sentence_digest) = 64),
    node_grant_ref TEXT NOT NULL CHECK(length(trim(node_grant_ref)) BETWEEN 1 AND 1024),
    node_grant_digest TEXT NOT NULL CHECK(length(node_grant_digest) = 71),
    payload_digest TEXT NOT NULL CHECK(length(payload_digest) = 71),
    staged_at_ms INTEGER NOT NULL CHECK(staged_at_ms >= 0),
    UNIQUE(graph_id, graph_revision, node_id, attempt_id, sentence_index)
);

CREATE INDEX IF NOT EXISTS idx_task_runtime_governed_chat_stream_replay
    ON task_runtime_governed_chat_stream_receipt(
        request_id, principal_id, workspace_id, session_id,
        graph_id, graph_revision, node_id, attempt_id, sentence_index
    );

CREATE TRIGGER IF NOT EXISTS trg_task_runtime_governed_chat_stream_receipt_no_update
BEFORE UPDATE ON task_runtime_governed_chat_stream_receipt
BEGIN
    SELECT RAISE(ABORT, 'TASK_RUNTIME_GOVERNED_CHAT_STREAM_RECEIPT_IMMUTABLE');
END;

CREATE TRIGGER IF NOT EXISTS trg_task_runtime_governed_chat_stream_receipt_no_delete
BEFORE DELETE ON task_runtime_governed_chat_stream_receipt
BEGIN
    SELECT RAISE(ABORT, 'TASK_RUNTIME_GOVERNED_CHAT_STREAM_RECEIPT_IMMUTABLE');
END;

COMMIT;
