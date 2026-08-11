-- NE-01: canonical durable TaskNode output artifacts.
--
-- One accepted TaskNode may publish exactly one immutable result artifact for
-- its exact Graph revision and successful Attempt.  Successor nodes refer to
-- the stable artifact_ref compiled into TaskNode.input_refs; payload and
-- output-contract digests are verified again by the application repository.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS task_runtime_node_artifact (
    artifact_ref TEXT NOT NULL PRIMARY KEY
        CHECK(length(trim(artifact_ref)) BETWEEN 1 AND 768),
    schema_version TEXT NOT NULL CHECK(
        schema_version = 'Natsume.task_runtime.node_artifact.v1'
    ),
    graph_id TEXT NOT NULL,
    graph_revision INTEGER NOT NULL CHECK(
        typeof(graph_revision) = 'integer' AND graph_revision >= 1
    ),
    node_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    receipt_ref TEXT NOT NULL
        CHECK(length(trim(receipt_ref)) BETWEEN 1 AND 768),
    output_contract_json TEXT NOT NULL
        CHECK(length(output_contract_json) BETWEEN 2 AND 262144),
    output_contract_sha256 TEXT NOT NULL CHECK(
        length(output_contract_sha256) = 71
        AND substr(output_contract_sha256, 1, 7) = 'sha256:'
        AND substr(output_contract_sha256, 8) NOT GLOB '*[^0-9a-f]*'
    ),
    payload_json TEXT NOT NULL
        CHECK(length(payload_json) BETWEEN 2 AND 1048576),
    payload_sha256 TEXT NOT NULL CHECK(
        length(payload_sha256) = 71
        AND substr(payload_sha256, 1, 7) = 'sha256:'
        AND substr(payload_sha256, 8) NOT GLOB '*[^0-9a-f]*'
    ),
    created_ts_ms INTEGER NOT NULL CHECK(
        typeof(created_ts_ms) = 'integer' AND created_ts_ms >= 0
    ),
    UNIQUE(graph_id, graph_revision, node_id),
    FOREIGN KEY (graph_id, graph_revision, node_id)
        REFERENCES task_runtime_node(
            graph_id, graph_revision, node_id
        ) ON DELETE RESTRICT,
    FOREIGN KEY (attempt_id, graph_id, graph_revision, node_id)
        REFERENCES task_runtime_attempt(
            attempt_id, graph_id, graph_revision, node_id
        ) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_task_runtime_node_artifact_attempt
    ON task_runtime_node_artifact(
        attempt_id, graph_id, graph_revision, node_id
    );

CREATE TRIGGER IF NOT EXISTS trg_task_runtime_node_artifact_immutable_update
BEFORE UPDATE ON task_runtime_node_artifact
BEGIN
    SELECT RAISE(ABORT, 'task runtime node artifact is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_task_runtime_node_artifact_immutable_delete
BEFORE DELETE ON task_runtime_node_artifact
BEGIN
    SELECT RAISE(ABORT, 'task runtime node artifact is immutable');
END;

COMMIT;
