-- GR-04: one immutable HostCommand admission per TaskGraph revision.
--
-- A failure replan preserves root_work_order_id/graph_id and creates revision
-- + 1.  The original 0062 single-column UNIQUE constraints accidentally made
-- that lineage impossible.  Rebuild the journal so the immutable identity is
-- the graph revision while allowing the canonical executor assignment to be
-- reused across revisions of the same root work order.

BEGIN IMMEDIATE;

CREATE TABLE host_command_journal_revisioned (
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
    root_work_order_id TEXT NOT NULL
        CHECK(length(trim(root_work_order_id)) BETWEEN 1 AND 240),
    graph_id TEXT NOT NULL CHECK(length(trim(graph_id)) BETWEEN 1 AND 240),
    graph_revision INTEGER NOT NULL CHECK(graph_revision >= 1),
    assignment_id TEXT NOT NULL
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
    UNIQUE(root_work_order_id, graph_revision),
    UNIQUE(graph_id, graph_revision),
    FOREIGN KEY(graph_id, graph_revision)
        REFERENCES task_runtime_graph(graph_id, revision) ON DELETE RESTRICT
);

INSERT INTO host_command_journal_revisioned(
    command_id, schema_version, request_id, surface, principal_id, session_id,
    ingress_claim_ref, ingress_audit_ref, governor_grant_ref, input_ref,
    input_sha256, route_type, target_id, business_scope, operation_fingerprint,
    root_work_order_id, graph_id, graph_revision, assignment_id, command_json,
    command_digest, work_order_json, work_order_digest, assignment_json,
    assignment_digest, graph_json, graph_digest, created_at_ms
)
SELECT
    command_id, schema_version, request_id, surface, principal_id, session_id,
    ingress_claim_ref, ingress_audit_ref, governor_grant_ref, input_ref,
    input_sha256, route_type, target_id, business_scope, operation_fingerprint,
    root_work_order_id, graph_id, graph_revision, assignment_id, command_json,
    command_digest, work_order_json, work_order_digest, assignment_json,
    assignment_digest, graph_json, graph_digest, created_at_ms
FROM host_command_journal;

DROP TABLE host_command_journal;
ALTER TABLE host_command_journal_revisioned RENAME TO host_command_journal;

CREATE INDEX idx_host_command_journal_principal_created
    ON host_command_journal(principal_id, created_at_ms, command_id);

CREATE INDEX idx_host_command_journal_graph
    ON host_command_journal(graph_id, graph_revision);

COMMIT;
