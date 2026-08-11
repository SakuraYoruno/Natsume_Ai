-- AW-03: durable Host command admission bound to the canonical task runtime.
--
-- The row is an immutable admission journal.  Execution status, attempts,
-- leases, idempotency receipts and delivery acknowledgements remain owned by
-- migrations 0052/0054/0059; this table must not become a second runtime state
-- machine.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS host_command_journal (
    command_id TEXT PRIMARY KEY CHECK(length(trim(command_id)) BETWEEN 1 AND 512),
    schema_version TEXT NOT NULL CHECK(length(trim(schema_version)) BETWEEN 1 AND 128),
    request_id TEXT NOT NULL CHECK(length(trim(request_id)) BETWEEN 1 AND 240),
    surface TEXT NOT NULL CHECK(length(trim(surface)) BETWEEN 1 AND 160),
    principal_id TEXT NOT NULL CHECK(length(trim(principal_id)) BETWEEN 1 AND 240),
    session_id TEXT NOT NULL CHECK(length(trim(session_id)) BETWEEN 1 AND 240),
    ingress_claim_ref TEXT UNIQUE
        CHECK(ingress_claim_ref IS NULL OR length(trim(ingress_claim_ref)) BETWEEN 1 AND 512),
    ingress_audit_ref TEXT
        CHECK(ingress_audit_ref IS NULL OR length(trim(ingress_audit_ref)) BETWEEN 1 AND 512),
    governor_grant_ref TEXT
        CHECK(governor_grant_ref IS NULL OR length(trim(governor_grant_ref)) BETWEEN 1 AND 512),
    input_ref TEXT NOT NULL CHECK(length(trim(input_ref)) BETWEEN 1 AND 512),
    input_sha256 TEXT NOT NULL
        CHECK(
            length(input_sha256) = 71
            AND substr(input_sha256, 1, 7) = 'sha256:'
            AND substr(input_sha256, 8) NOT GLOB '*[^0-9a-f]*'
        ),
    route_type TEXT NOT NULL CHECK(length(trim(route_type)) BETWEEN 1 AND 64),
    target_id TEXT NOT NULL CHECK(length(trim(target_id)) BETWEEN 1 AND 255),
    business_scope TEXT NOT NULL CHECK(length(trim(business_scope)) BETWEEN 1 AND 255),
    operation_fingerprint TEXT NOT NULL
        CHECK(
            length(operation_fingerprint) = 71
            AND substr(operation_fingerprint, 1, 7) = 'sha256:'
            AND substr(operation_fingerprint, 8) NOT GLOB '*[^0-9a-f]*'
        ),
    root_work_order_id TEXT NOT NULL UNIQUE
        CHECK(length(trim(root_work_order_id)) BETWEEN 1 AND 240),
    graph_id TEXT NOT NULL CHECK(length(trim(graph_id)) BETWEEN 1 AND 240),
    graph_revision INTEGER NOT NULL CHECK(graph_revision >= 1),
    assignment_id TEXT NOT NULL UNIQUE
        CHECK(length(trim(assignment_id)) BETWEEN 1 AND 512),
    command_json TEXT NOT NULL
        CHECK(length(CAST(command_json AS BLOB)) BETWEEN 2 AND 1048576),
    command_digest TEXT NOT NULL
        CHECK(
            length(command_digest) = 71
            AND substr(command_digest, 1, 7) = 'sha256:'
            AND substr(command_digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
    work_order_json TEXT NOT NULL
        CHECK(length(CAST(work_order_json AS BLOB)) BETWEEN 2 AND 1048576),
    work_order_digest TEXT NOT NULL
        CHECK(
            length(work_order_digest) = 71
            AND substr(work_order_digest, 1, 7) = 'sha256:'
            AND substr(work_order_digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
    assignment_json TEXT NOT NULL
        CHECK(length(CAST(assignment_json AS BLOB)) BETWEEN 2 AND 65536),
    assignment_digest TEXT NOT NULL
        CHECK(
            length(assignment_digest) = 71
            AND substr(assignment_digest, 1, 7) = 'sha256:'
            AND substr(assignment_digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
    graph_json TEXT NOT NULL
        CHECK(length(CAST(graph_json AS BLOB)) BETWEEN 2 AND 1048576),
    graph_digest TEXT NOT NULL
        CHECK(
            length(graph_digest) = 71
            AND substr(graph_digest, 1, 7) = 'sha256:'
            AND substr(graph_digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
    UNIQUE(surface, request_id),
    FOREIGN KEY(graph_id, graph_revision)
        REFERENCES task_runtime_graph(graph_id, revision) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_host_command_journal_principal_created
    ON host_command_journal(principal_id, created_at_ms, command_id);

CREATE INDEX IF NOT EXISTS idx_host_command_journal_graph
    ON host_command_journal(graph_id, graph_revision);

COMMIT;
