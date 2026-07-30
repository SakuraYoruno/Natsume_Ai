-- DL-04: immutable, task-bound text visibility ledger for governed Chat.
--
-- A visible sentence is not a Life mutation and is not an accepted external
-- effect.  It is an attempt/lease/grant-bound delivery intent that must be
-- durable before SSE can expose it.  The Task Runtime owns this ledger so a
-- restart can replay exact already-visible text without re-running the model.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS task_runtime_chat_delivery_event (
    event_id TEXT PRIMARY KEY
        CHECK(length(trim(event_id)) BETWEEN 1 AND 1024),
    schema_version TEXT NOT NULL
        CHECK(schema_version = 'Natsume.task_runtime.chat_delivery.v1'),
    event_kind TEXT NOT NULL
        CHECK(event_kind IN (
            'PLAN_FROZEN',
            'SENTENCE_STAGED',
            'STREAM_COMPLETED',
            'STREAM_INTERRUPTED',
            'STREAM_CANCELLED'
        )),
    graph_id TEXT NOT NULL CHECK(length(trim(graph_id)) BETWEEN 1 AND 240),
    graph_revision INTEGER NOT NULL CHECK(graph_revision >= 1),
    node_id TEXT NOT NULL CHECK(length(trim(node_id)) BETWEEN 1 AND 240),
    attempt_id TEXT NOT NULL CHECK(length(trim(attempt_id)) BETWEEN 1 AND 240),
    lease_id TEXT NOT NULL CHECK(length(trim(lease_id)) BETWEEN 1 AND 240),
    lease_owner_id TEXT NOT NULL
        CHECK(length(trim(lease_owner_id)) BETWEEN 1 AND 240),
    fencing_token INTEGER NOT NULL CHECK(fencing_token >= 1),
    node_grant_ref TEXT NOT NULL
        CHECK(length(trim(node_grant_ref)) BETWEEN 1 AND 1024),
    request_id TEXT NOT NULL CHECK(length(trim(request_id)) BETWEEN 1 AND 240),
    session_id TEXT NOT NULL CHECK(length(trim(session_id)) BETWEEN 1 AND 240),
    principal_id TEXT NOT NULL
        CHECK(length(trim(principal_id)) BETWEEN 1 AND 240),
    self_id TEXT NOT NULL
        CHECK(self_id = 'natsume_ai'),
    relationship_subject_id TEXT NOT NULL
        CHECK(length(trim(relationship_subject_id)) BETWEEN 1 AND 240),
    workspace_id TEXT NOT NULL
        CHECK(length(trim(workspace_id)) BETWEEN 1 AND 240),
    plan_id TEXT NOT NULL CHECK(length(trim(plan_id)) BETWEEN 1 AND 512),
    plan_digest TEXT NOT NULL
        CHECK(length(plan_digest) = 64 AND plan_digest GLOB '[0-9a-f]*'),
    synchronization_id TEXT NOT NULL
        CHECK(length(trim(synchronization_id)) BETWEEN 1 AND 512),
    self_affect_revision INTEGER NOT NULL CHECK(self_affect_revision >= 0),
    sentence_index INTEGER NOT NULL CHECK(sentence_index >= -1),
    sent_sentence_count INTEGER NOT NULL CHECK(sent_sentence_count >= 0),
    sentence_text TEXT NOT NULL DEFAULT ''
        CHECK(length(sentence_text) <= 2000),
    sentence_digest TEXT NOT NULL DEFAULT ''
        CHECK(
            sentence_digest = ''
            OR (length(sentence_digest) = 64 AND sentence_digest GLOB '[0-9a-f]*')
        ),
    reason_code TEXT NOT NULL DEFAULT ''
        CHECK(length(reason_code) <= 160),
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    payload_digest TEXT NOT NULL
        CHECK(length(payload_digest) = 64 AND payload_digest GLOB '[0-9a-f]*'),
    created_ts_ms INTEGER NOT NULL CHECK(created_ts_ms >= 0),
    UNIQUE(graph_id, graph_revision, node_id, attempt_id, event_kind, sentence_index),
    FOREIGN KEY(graph_id, graph_revision, node_id)
        REFERENCES task_runtime_node(graph_id, graph_revision, node_id)
        ON DELETE RESTRICT,
    FOREIGN KEY(attempt_id)
        REFERENCES task_runtime_attempt(attempt_id)
        ON DELETE RESTRICT,
    FOREIGN KEY(lease_id)
        REFERENCES task_runtime_lease(lease_id)
        ON DELETE RESTRICT,
    CHECK(
        (
            event_kind = 'SENTENCE_STAGED'
            AND sentence_index >= 0
            AND sent_sentence_count = 0
            AND length(trim(sentence_text)) BETWEEN 1 AND 2000
            AND length(sentence_digest) = 64
        )
        OR (
            event_kind <> 'SENTENCE_STAGED'
            AND sentence_index = -1
            AND sentence_text = ''
            AND sentence_digest = ''
        )
    )
);

CREATE INDEX IF NOT EXISTS idx_task_runtime_chat_delivery_request
    ON task_runtime_chat_delivery_event(
        request_id, session_id, principal_id, workspace_id, sentence_index, event_id
    );

CREATE INDEX IF NOT EXISTS idx_task_runtime_chat_delivery_attempt
    ON task_runtime_chat_delivery_event(
        graph_id, graph_revision, node_id, attempt_id, sentence_index
    );

CREATE TRIGGER trg_task_runtime_chat_delivery_identity_guard
BEFORE INSERT ON task_runtime_chat_delivery_event
WHEN NOT EXISTS (
    SELECT 1
    FROM task_runtime_attempt AS attempt
    JOIN task_runtime_lease AS lease
      ON lease.lease_id = NEW.lease_id
     AND lease.attempt_id = attempt.attempt_id
    WHERE attempt.attempt_id = NEW.attempt_id
      AND attempt.graph_id = NEW.graph_id
      AND attempt.graph_revision = NEW.graph_revision
      AND attempt.node_id = NEW.node_id
      AND lease.owner_id = NEW.lease_owner_id
      AND lease.fencing_token = NEW.fencing_token
)
BEGIN
    SELECT RAISE(ABORT, 'task chat delivery event does not bind exact attempt lease fence');
END;

CREATE TRIGGER trg_task_runtime_chat_delivery_no_update
BEFORE UPDATE ON task_runtime_chat_delivery_event
BEGIN
    SELECT RAISE(ABORT, 'task chat delivery event is immutable');
END;

CREATE TRIGGER trg_task_runtime_chat_delivery_no_delete
BEFORE DELETE ON task_runtime_chat_delivery_event
BEGIN
    SELECT RAISE(ABORT, 'task chat delivery event cannot be deleted');
END;

COMMIT;
