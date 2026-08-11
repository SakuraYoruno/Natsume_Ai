-- NE-01: append-only accepted HEAD, promotion and abandonment authority.
--
-- Expand-only: the 0101 allocation/lease/cleanup table remains byte-for-byte
-- frozen.  An absent lifecycle event means the legacy ACTIVE lifecycle state.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS agent_workspace_lifecycle_event (
    event_id TEXT PRIMARY KEY
        CHECK(length(trim(event_id)) BETWEEN 1 AND 240),
    schema_version TEXT NOT NULL
        CHECK(schema_version = 'Natsume.agent.workspace_lifecycle.v1'),
    workspace_id TEXT NOT NULL
        CHECK(length(trim(workspace_id)) BETWEEN 1 AND 120),
    transition_revision INTEGER NOT NULL
        CHECK(typeof(transition_revision) = 'integer' AND transition_revision >= 1),
    from_state TEXT NOT NULL
        CHECK(from_state IN ('ACTIVE', 'ACCEPTED')),
    to_state TEXT NOT NULL
        CHECK(to_state IN ('ACCEPTED', 'PROMOTED', 'ABANDONED')),
    graph_id TEXT NOT NULL
        CHECK(length(trim(graph_id)) BETWEEN 1 AND 240),
    graph_revision INTEGER NOT NULL
        CHECK(typeof(graph_revision) = 'integer' AND graph_revision >= 1),
    node_id TEXT NOT NULL
        CHECK(length(trim(node_id)) BETWEEN 1 AND 240),
    attempt_id TEXT NOT NULL
        CHECK(length(trim(attempt_id)) BETWEEN 1 AND 240),
    task_lease_id TEXT NOT NULL
        CHECK(length(trim(task_lease_id)) BETWEEN 1 AND 240),
    task_lease_fencing_token INTEGER NOT NULL CHECK(
        typeof(task_lease_fencing_token) = 'integer'
        AND task_lease_fencing_token >= 1
    ),
    workspace_lease_ref TEXT NOT NULL
        CHECK(length(trim(workspace_lease_ref)) BETWEEN 1 AND 512),
    workspace_lease_owner TEXT NOT NULL
        CHECK(length(trim(workspace_lease_owner)) BETWEEN 1 AND 240),
    workspace_lease_fencing_token INTEGER NOT NULL CHECK(
        typeof(workspace_lease_fencing_token) = 'integer'
        AND workspace_lease_fencing_token >= 1
    ),
    base_commit TEXT CHECK(
        base_commit IS NULL OR (
            length(base_commit) IN (40, 64)
            AND base_commit NOT GLOB '*[^0-9a-f]*'
        )
    ),
    accepted_head TEXT CHECK(
        accepted_head IS NULL OR (
            length(accepted_head) IN (40, 64)
            AND accepted_head NOT GLOB '*[^0-9a-f]*'
        )
    ),
    tree_digest TEXT CHECK(
        tree_digest IS NULL OR (
            length(tree_digest) = 71
            AND substr(tree_digest, 1, 7) = 'sha256:'
            AND substr(tree_digest, 8) NOT GLOB '*[^0-9a-f]*'
        )
    ),
    diff_digest TEXT CHECK(
        diff_digest IS NULL OR (
            length(diff_digest) = 71
            AND substr(diff_digest, 1, 7) = 'sha256:'
            AND substr(diff_digest, 8) NOT GLOB '*[^0-9a-f]*'
        )
    ),
    verification_ref TEXT
        CHECK(verification_ref IS NULL OR length(trim(verification_ref)) BETWEEN 1 AND 512),
    acceptance_ref TEXT
        CHECK(acceptance_ref IS NULL OR length(trim(acceptance_ref)) BETWEEN 1 AND 512),
    acceptance_event_id TEXT
        CHECK(acceptance_event_id IS NULL OR length(trim(acceptance_event_id)) BETWEEN 1 AND 240),
    terminal_ref TEXT
        CHECK(terminal_ref IS NULL OR length(trim(terminal_ref)) BETWEEN 1 AND 512),
    reason_code TEXT CHECK(
        reason_code IS NULL OR (
            length(reason_code) BETWEEN 3 AND 128
            AND reason_code GLOB '[A-Z]*'
            AND reason_code NOT GLOB '*[^A-Z0-9_]*'
        )
    ),
    previous_event_digest TEXT CHECK(
        previous_event_digest IS NULL OR (
            length(previous_event_digest) = 71
            AND substr(previous_event_digest, 1, 7) = 'sha256:'
            AND substr(previous_event_digest, 8) NOT GLOB '*[^0-9a-f]*'
        )
    ),
    event_digest TEXT NOT NULL UNIQUE CHECK(
        length(event_digest) = 71
        AND substr(event_digest, 1, 7) = 'sha256:'
        AND substr(event_digest, 8) NOT GLOB '*[^0-9a-f]*'
    ),
    created_ts_ms INTEGER NOT NULL
        CHECK(typeof(created_ts_ms) = 'integer' AND created_ts_ms >= 0),
    UNIQUE(workspace_id, transition_revision),
    FOREIGN KEY (workspace_id)
        REFERENCES agent_workspace_authority(workspace_id) ON DELETE RESTRICT,
    FOREIGN KEY (graph_id, graph_revision, node_id)
        REFERENCES task_runtime_node(graph_id, graph_revision, node_id) ON DELETE RESTRICT,
    FOREIGN KEY (attempt_id)
        REFERENCES task_runtime_attempt(attempt_id) ON DELETE RESTRICT,
    FOREIGN KEY (task_lease_id)
        REFERENCES task_runtime_lease(lease_id) ON DELETE RESTRICT,
    FOREIGN KEY (acceptance_event_id)
        REFERENCES agent_workspace_lifecycle_event(event_id) ON DELETE RESTRICT,
    CHECK(
        (from_state = 'ACTIVE' AND to_state IN ('ACCEPTED', 'ABANDONED'))
        OR (from_state = 'ACCEPTED' AND to_state IN ('PROMOTED', 'ABANDONED'))
    ),
    CHECK(
        (transition_revision = 1 AND previous_event_digest IS NULL)
        OR (transition_revision > 1 AND previous_event_digest IS NOT NULL)
    ),
    CHECK(
        (
            base_commit IS NULL AND accepted_head IS NULL
            AND tree_digest IS NULL AND diff_digest IS NULL
            AND verification_ref IS NULL AND acceptance_ref IS NULL
        )
        OR (
            base_commit IS NOT NULL AND accepted_head IS NOT NULL
            AND tree_digest IS NOT NULL AND diff_digest IS NOT NULL
            AND verification_ref IS NOT NULL AND acceptance_ref IS NOT NULL
        )
    ),
    CHECK(
        (to_state = 'ACCEPTED'
            AND base_commit IS NOT NULL
            AND acceptance_event_id IS NULL
            AND terminal_ref IS NULL AND reason_code IS NULL)
        OR (from_state = 'ACCEPTED'
            AND base_commit IS NOT NULL
            AND acceptance_event_id IS NOT NULL
            AND terminal_ref IS NOT NULL
            AND (
                (to_state = 'PROMOTED' AND reason_code IS NULL)
                OR (to_state = 'ABANDONED' AND reason_code IS NOT NULL)
            ))
        OR (from_state = 'ACTIVE' AND to_state = 'ABANDONED'
            AND base_commit IS NULL
            AND acceptance_event_id IS NULL
            AND terminal_ref IS NOT NULL AND reason_code IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_agent_workspace_lifecycle_latest
    ON agent_workspace_lifecycle_event(
        workspace_id, transition_revision DESC, event_id
    );

CREATE INDEX IF NOT EXISTS idx_agent_workspace_lifecycle_execution
    ON agent_workspace_lifecycle_event(
        graph_id, graph_revision, node_id, attempt_id, task_lease_id
    );

CREATE TRIGGER IF NOT EXISTS trg_agent_workspace_lifecycle_update_guard
BEFORE UPDATE ON agent_workspace_lifecycle_event
BEGIN
    SELECT RAISE(ABORT, 'workspace lifecycle event is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_agent_workspace_lifecycle_delete_guard
BEFORE DELETE ON agent_workspace_lifecycle_event
BEGIN
    SELECT RAISE(ABORT, 'workspace lifecycle event is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_agent_workspace_lifecycle_sequence_guard
BEFORE INSERT ON agent_workspace_lifecycle_event
WHEN
    (
        NEW.transition_revision = 1
        AND (
            NEW.from_state <> 'ACTIVE'
            OR EXISTS (
                SELECT 1 FROM agent_workspace_lifecycle_event
                WHERE workspace_id = NEW.workspace_id
            )
        )
    )
    OR (
        NEW.transition_revision > 1
        AND NOT EXISTS (
            SELECT 1
            FROM agent_workspace_lifecycle_event AS previous
            WHERE previous.workspace_id = NEW.workspace_id
              AND previous.transition_revision = NEW.transition_revision - 1
              AND previous.to_state = NEW.from_state
              AND previous.event_digest = NEW.previous_event_digest
        )
    )
BEGIN
    SELECT RAISE(ABORT, 'workspace lifecycle sequence or digest chain is invalid');
END;

CREATE TRIGGER IF NOT EXISTS trg_agent_workspace_lifecycle_binding_guard
BEFORE INSERT ON agent_workspace_lifecycle_event
WHEN
    NOT EXISTS (
        SELECT 1
        FROM agent_workspace_authority AS workspace
        WHERE workspace.workspace_id = NEW.workspace_id
          AND workspace.graph_id = NEW.graph_id
          AND workspace.graph_revision = NEW.graph_revision
          AND workspace.status = 'ACTIVE'
    )
    OR NOT EXISTS (
        SELECT 1
        FROM task_runtime_attempt AS attempt
        WHERE attempt.attempt_id = NEW.attempt_id
          AND attempt.graph_id = NEW.graph_id
          AND attempt.graph_revision = NEW.graph_revision
          AND attempt.node_id = NEW.node_id
    )
    OR NOT EXISTS (
        SELECT 1
        FROM task_runtime_lease AS lease
        WHERE lease.lease_id = NEW.task_lease_id
          AND lease.attempt_id = NEW.attempt_id
          AND lease.fencing_token = NEW.task_lease_fencing_token
    )
BEGIN
    SELECT RAISE(ABORT, 'workspace lifecycle execution binding is invalid');
END;

CREATE TRIGGER IF NOT EXISTS trg_agent_workspace_lifecycle_acceptance_guard
BEFORE INSERT ON agent_workspace_lifecycle_event
WHEN NEW.from_state = 'ACCEPTED' AND NOT EXISTS (
    SELECT 1
    FROM agent_workspace_lifecycle_event AS accepted
    WHERE accepted.event_id = NEW.acceptance_event_id
      AND accepted.workspace_id = NEW.workspace_id
      AND accepted.to_state = 'ACCEPTED'
      AND accepted.transition_revision < NEW.transition_revision
      AND accepted.graph_id = NEW.graph_id
      AND accepted.graph_revision = NEW.graph_revision
      AND accepted.node_id = NEW.node_id
      AND accepted.attempt_id = NEW.attempt_id
      AND accepted.task_lease_id = NEW.task_lease_id
      AND accepted.task_lease_fencing_token = NEW.task_lease_fencing_token
      AND accepted.base_commit = NEW.base_commit
      AND accepted.accepted_head = NEW.accepted_head
      AND accepted.tree_digest = NEW.tree_digest
      AND accepted.diff_digest = NEW.diff_digest
      AND accepted.verification_ref = NEW.verification_ref
      AND accepted.acceptance_ref = NEW.acceptance_ref
)
BEGIN
    SELECT RAISE(ABORT, 'workspace accepted receipt lineage is invalid');
END;

COMMIT;
