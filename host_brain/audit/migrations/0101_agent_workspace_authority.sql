-- NE-01 W1: durable, graph-bound workspace ownership and fencing authority.
--
-- The database stores only a logical managed_root_ref plus a validated
-- workspace-relative segment.  Physical filesystem roots remain process
-- configuration and can therefore never be redirected by a database row.

BEGIN IMMEDIATE;

CREATE UNIQUE INDEX IF NOT EXISTS uq_task_runtime_graph_workspace_binding
    ON task_runtime_graph(graph_id, revision, root_work_order_id);

CREATE TABLE IF NOT EXISTS agent_workspace_authority (
    workspace_id TEXT NOT NULL PRIMARY KEY
        CHECK(length(trim(workspace_id)) BETWEEN 1 AND 120),
    schema_version TEXT NOT NULL CHECK(
        schema_version = 'Natsume.agent.workspace_authority.v1'
    ),
    root_work_order_id TEXT NOT NULL
        CHECK(length(trim(root_work_order_id)) BETWEEN 1 AND 240),
    graph_id TEXT NOT NULL CHECK(length(trim(graph_id)) BETWEEN 1 AND 240),
    graph_revision INTEGER NOT NULL CHECK(
        typeof(graph_revision) = 'integer' AND graph_revision >= 1
    ),
    owner_principal_id TEXT NOT NULL
        CHECK(length(trim(owner_principal_id)) BETWEEN 1 AND 240),
    managed_root_ref TEXT NOT NULL
        CHECK(length(trim(managed_root_ref)) BETWEEN 1 AND 120),
    workspace_relpath TEXT NOT NULL
        CHECK(length(trim(workspace_relpath)) BETWEEN 1 AND 120),
    source_ref TEXT NOT NULL
        CHECK(length(trim(source_ref)) BETWEEN 1 AND 512),
    isolation_mode TEXT NOT NULL CHECK(isolation_mode IN (
        'DIRECTORY_FENCE', 'CONTAINER', 'MICROVM'
    )),
    access_mode TEXT NOT NULL CHECK(access_mode = 'READ_WRITE'),
    allowed_paths_json TEXT NOT NULL CHECK(
        json_valid(allowed_paths_json)
        AND json_type(allowed_paths_json) = 'array'
        AND length(CAST(allowed_paths_json AS BLOB)) BETWEEN 4 AND 65536
    ),
    allowed_paths_digest TEXT NOT NULL CHECK(
        length(allowed_paths_digest) = 71
        AND substr(allowed_paths_digest, 1, 7) = 'sha256:'
        AND substr(allowed_paths_digest, 8) NOT GLOB '*[^0-9a-f]*'
    ),
    status TEXT NOT NULL CHECK(status IN (
        'ALLOCATED', 'ACTIVE', 'CLEANUP_PENDING', 'CLEANED', 'QUARANTINED'
    )),
    status_version INTEGER NOT NULL DEFAULT 0 CHECK(
        typeof(status_version) = 'integer' AND status_version >= 0
    ),
    lease_owner TEXT CHECK(
        lease_owner IS NULL
        OR length(trim(lease_owner)) BETWEEN 1 AND 240
    ),
    lease_token_digest TEXT CHECK(
        lease_token_digest IS NULL
        OR (
            length(lease_token_digest) = 71
            AND substr(lease_token_digest, 1, 7) = 'sha256:'
            AND substr(lease_token_digest, 8) NOT GLOB '*[^0-9a-f]*'
        )
    ),
    lease_fencing_token INTEGER NOT NULL DEFAULT 0 CHECK(
        typeof(lease_fencing_token) = 'integer'
        AND lease_fencing_token >= 0
    ),
    lease_expires_ts_ms INTEGER CHECK(
        lease_expires_ts_ms IS NULL
        OR (
            typeof(lease_expires_ts_ms) = 'integer'
            AND lease_expires_ts_ms >= 0
        )
    ),
    cleanup_receipt_ref TEXT CHECK(
        cleanup_receipt_ref IS NULL
        OR length(trim(cleanup_receipt_ref)) BETWEEN 1 AND 512
    ),
    cleanup_digest TEXT CHECK(
        cleanup_digest IS NULL
        OR (
            length(cleanup_digest) = 71
            AND substr(cleanup_digest, 1, 7) = 'sha256:'
            AND substr(cleanup_digest, 8) NOT GLOB '*[^0-9a-f]*'
        )
    ),
    created_ts_ms INTEGER NOT NULL CHECK(
        typeof(created_ts_ms) = 'integer' AND created_ts_ms >= 0
    ),
    updated_ts_ms INTEGER NOT NULL CHECK(
        typeof(updated_ts_ms) = 'integer'
        AND updated_ts_ms >= created_ts_ms
    ),
    UNIQUE(root_work_order_id, graph_revision),
    UNIQUE(graph_id, graph_revision),
    UNIQUE(managed_root_ref, workspace_relpath),
    FOREIGN KEY(graph_id, graph_revision, root_work_order_id)
        REFERENCES task_runtime_graph(
            graph_id, revision, root_work_order_id
        ) ON DELETE RESTRICT,
    CHECK(workspace_relpath = workspace_id),
    CHECK(
        (
            lease_owner IS NULL
            AND lease_token_digest IS NULL
            AND lease_expires_ts_ms IS NULL
        )
        OR
        (
            lease_owner IS NOT NULL
            AND lease_token_digest IS NOT NULL
            AND lease_expires_ts_ms IS NOT NULL
            AND lease_fencing_token >= 1
        )
    ),
    CHECK(
        (status = 'ALLOCATED' AND lease_owner IS NULL)
        OR (status IN ('ACTIVE', 'CLEANUP_PENDING') AND lease_owner IS NOT NULL)
        OR (status IN ('CLEANED', 'QUARANTINED') AND lease_owner IS NULL)
    ),
    CHECK(
        (status = 'CLEANED'
            AND cleanup_receipt_ref IS NOT NULL
            AND cleanup_digest IS NOT NULL)
        OR
        (status <> 'CLEANED'
            AND cleanup_receipt_ref IS NULL
            AND cleanup_digest IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_agent_workspace_authority_owner_status
    ON agent_workspace_authority(
        owner_principal_id, status, updated_ts_ms, workspace_id
    );

CREATE INDEX IF NOT EXISTS idx_agent_workspace_authority_lease_expiry
    ON agent_workspace_authority(
        status, lease_expires_ts_ms, workspace_id
    );

CREATE TRIGGER IF NOT EXISTS trg_agent_workspace_authority_immutable_update
BEFORE UPDATE OF
    workspace_id, schema_version, root_work_order_id, graph_id,
    graph_revision, owner_principal_id, managed_root_ref,
    workspace_relpath, source_ref, isolation_mode, access_mode,
    allowed_paths_json, allowed_paths_digest, created_ts_ms
ON agent_workspace_authority
BEGIN
    SELECT RAISE(ABORT, 'agent workspace immutable authority cannot change');
END;

CREATE TRIGGER IF NOT EXISTS trg_agent_workspace_authority_delete_guard
BEFORE DELETE ON agent_workspace_authority
BEGIN
    SELECT RAISE(ABORT, 'agent workspace authority cannot be deleted');
END;

CREATE TRIGGER IF NOT EXISTS trg_agent_workspace_authority_version_guard
BEFORE UPDATE ON agent_workspace_authority
WHEN NEW.status_version <> OLD.status_version + 1
BEGIN
    SELECT RAISE(ABORT, 'agent workspace status version must advance exactly once');
END;

CREATE TRIGGER IF NOT EXISTS trg_agent_workspace_authority_fence_guard
BEFORE UPDATE ON agent_workspace_authority
WHEN NEW.lease_fencing_token < OLD.lease_fencing_token
  OR (
      NEW.lease_owner IS NOT NULL
      AND (
          OLD.lease_owner IS NULL
          OR OLD.lease_owner <> NEW.lease_owner
          OR OLD.lease_token_digest <> NEW.lease_token_digest
      )
      AND NEW.lease_fencing_token <> OLD.lease_fencing_token + 1
  )
BEGIN
    SELECT RAISE(ABORT, 'agent workspace fencing token is invalid');
END;

CREATE TRIGGER IF NOT EXISTS trg_agent_workspace_authority_status_guard
BEFORE UPDATE OF status ON agent_workspace_authority
WHEN NOT (
    (OLD.status = 'ALLOCATED' AND NEW.status IN ('ACTIVE', 'QUARANTINED'))
    OR
    (OLD.status = 'ACTIVE'
        AND NEW.status IN ('ACTIVE', 'CLEANUP_PENDING', 'QUARANTINED'))
    OR
    (OLD.status = 'CLEANUP_PENDING'
        AND NEW.status IN ('CLEANUP_PENDING', 'CLEANED', 'QUARANTINED'))
)
BEGIN
    SELECT RAISE(ABORT, 'agent workspace status transition is invalid');
END;

CREATE TRIGGER IF NOT EXISTS trg_agent_workspace_authority_cleanup_immutable
BEFORE UPDATE OF cleanup_receipt_ref, cleanup_digest
ON agent_workspace_authority
WHEN OLD.cleanup_receipt_ref IS NOT NULL OR OLD.cleanup_digest IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'agent workspace cleanup receipt is immutable');
END;

COMMIT;
