-- NE-03: runtime-fenced, append-only authority for restrictive long-term goals.
--
-- A goal may only contribute RESTRICT/REVIEW/BLOCK constraints.  Accepted
-- Operator effects append an immutable event and advance the canonical head
-- in the same SQLite transaction.  Runtime lease fencing prevents a stale
-- Host process from becoming a second writer.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS long_term_goal_event (
    runtime_scope_id TEXT NOT NULL
        CHECK(length(trim(runtime_scope_id)) BETWEEN 1 AND 512),
    workspace_id TEXT NOT NULL
        CHECK(length(trim(workspace_id)) BETWEEN 1 AND 240),
    subject_id TEXT NOT NULL
        CHECK(length(trim(subject_id)) BETWEEN 1 AND 240),
    goal_id TEXT NOT NULL
        CHECK(length(trim(goal_id)) BETWEEN 1 AND 160),
    version INTEGER NOT NULL
        CHECK(typeof(version) = 'integer' AND version >= 1),
    expected_version INTEGER NOT NULL
        CHECK(typeof(expected_version) = 'integer' AND expected_version >= 0),
    operation_id TEXT NOT NULL
        CHECK(length(trim(operation_id)) BETWEEN 1 AND 512),
    event_kind TEXT NOT NULL
        CHECK(event_kind IN ('ACTIVATED', 'REVOKED')),
    goal_ref TEXT NOT NULL
        CHECK(length(trim(goal_ref)) BETWEEN 1 AND 1024),
    constraints_json TEXT NOT NULL
        CHECK(length(constraints_json) BETWEEN 2 AND 65536),
    input_digest TEXT NOT NULL CHECK(
        length(input_digest) = 71
        AND substr(input_digest, 1, 7) = 'sha256:'
        AND substr(input_digest, 8) NOT GLOB '*[^0-9a-f]*'
    ),
    previous_event_digest TEXT NOT NULL CHECK(
        previous_event_digest = ''
        OR (
            length(previous_event_digest) = 71
            AND substr(previous_event_digest, 1, 7) = 'sha256:'
            AND substr(previous_event_digest, 8) NOT GLOB '*[^0-9a-f]*'
        )
    ),
    event_digest TEXT NOT NULL CHECK(
        length(event_digest) = 71
        AND substr(event_digest, 1, 7) = 'sha256:'
        AND substr(event_digest, 8) NOT GLOB '*[^0-9a-f]*'
    ),
    ingress_audit_ref TEXT NOT NULL
        CHECK(length(trim(ingress_audit_ref)) BETWEEN 1 AND 1024),
    principal_id TEXT NOT NULL
        CHECK(length(trim(principal_id)) BETWEEN 1 AND 512),
    runtime_owner_id TEXT NOT NULL
        CHECK(length(trim(runtime_owner_id)) BETWEEN 1 AND 512),
    runtime_lease_id TEXT NOT NULL
        CHECK(length(trim(runtime_lease_id)) BETWEEN 1 AND 512),
    runtime_fencing_token INTEGER NOT NULL CHECK(
        typeof(runtime_fencing_token) = 'integer'
        AND runtime_fencing_token >= 1
    ),
    runtime_issued_at_ms INTEGER NOT NULL CHECK(
        typeof(runtime_issued_at_ms) = 'integer'
        AND runtime_issued_at_ms >= 0
    ),
    runtime_expires_at_ms INTEGER NOT NULL CHECK(
        typeof(runtime_expires_at_ms) = 'integer'
        AND runtime_expires_at_ms > runtime_issued_at_ms
    ),
    observed_at_ms INTEGER NOT NULL CHECK(
        typeof(observed_at_ms) = 'integer' AND observed_at_ms >= 0
    ),
    created_at_ms INTEGER NOT NULL CHECK(
        typeof(created_at_ms) = 'integer' AND created_at_ms >= 0
    ),
    PRIMARY KEY(
        runtime_scope_id,
        workspace_id,
        subject_id,
        goal_id,
        version
    ),
    UNIQUE(runtime_scope_id, operation_id),
    UNIQUE(runtime_scope_id, event_digest)
);

CREATE TABLE IF NOT EXISTS long_term_goal_head (
    runtime_scope_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    goal_id TEXT NOT NULL,
    version INTEGER NOT NULL
        CHECK(typeof(version) = 'integer' AND version >= 1),
    status TEXT NOT NULL CHECK(status IN ('ACTIVE', 'REVOKED')),
    goal_ref TEXT NOT NULL
        CHECK(length(trim(goal_ref)) BETWEEN 1 AND 1024),
    constraints_json TEXT NOT NULL
        CHECK(length(constraints_json) BETWEEN 2 AND 65536),
    operation_id TEXT NOT NULL
        CHECK(length(trim(operation_id)) BETWEEN 1 AND 512),
    event_digest TEXT NOT NULL CHECK(
        length(event_digest) = 71
        AND substr(event_digest, 1, 7) = 'sha256:'
        AND substr(event_digest, 8) NOT GLOB '*[^0-9a-f]*'
    ),
    updated_at_ms INTEGER NOT NULL CHECK(
        typeof(updated_at_ms) = 'integer' AND updated_at_ms >= 0
    ),
    PRIMARY KEY(runtime_scope_id, workspace_id, subject_id, goal_id),
    FOREIGN KEY(
        runtime_scope_id,
        workspace_id,
        subject_id,
        goal_id,
        version
    ) REFERENCES long_term_goal_event(
        runtime_scope_id,
        workspace_id,
        subject_id,
        goal_id,
        version
    ) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_long_term_goal_active_scope
    ON long_term_goal_head(
        runtime_scope_id,
        workspace_id,
        subject_id,
        status,
        goal_id
    );

CREATE INDEX IF NOT EXISTS idx_long_term_goal_event_operation
    ON long_term_goal_event(runtime_scope_id, operation_id);

CREATE TRIGGER IF NOT EXISTS long_term_goal_event_no_update
BEFORE UPDATE ON long_term_goal_event
BEGIN SELECT RAISE(ABORT, 'long-term goal event is append-only'); END;

CREATE TRIGGER IF NOT EXISTS long_term_goal_event_no_delete
BEFORE DELETE ON long_term_goal_event
BEGIN SELECT RAISE(ABORT, 'long-term goal event is append-only'); END;

CREATE TRIGGER IF NOT EXISTS long_term_goal_head_no_delete
BEFORE DELETE ON long_term_goal_head
BEGIN SELECT RAISE(ABORT, 'long-term goal head cannot be deleted'); END;

CREATE TRIGGER IF NOT EXISTS long_term_goal_event_requires_active_runtime_lease
BEFORE INSERT ON long_term_goal_event
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM host_embodiment_runtime_lease AS lease
        WHERE lease.runtime_scope_id = NEW.runtime_scope_id
          AND lease.owner_id = NEW.runtime_owner_id
          AND lease.lease_id = NEW.runtime_lease_id
          AND lease.fencing_token = NEW.runtime_fencing_token
          AND lease.released_at_ms IS NULL
          AND lease.issued_at_ms = NEW.runtime_issued_at_ms
          AND lease.expires_at_ms >= NEW.runtime_expires_at_ms
          AND lease.issued_at_ms <= NEW.created_at_ms
          AND lease.expires_at_ms > NEW.created_at_ms
          AND lease.expires_at_ms > (
              CAST((julianday('now') - 2440587.5) * 86400000 AS INTEGER)
          )
          AND NEW.created_at_ms BETWEEN (
              CAST((julianday('now') - 2440587.5) * 86400000 AS INTEGER) - 5000
          ) AND (
              CAST((julianday('now') - 2440587.5) * 86400000 AS INTEGER) + 5000
          )
    ) THEN RAISE(ABORT, 'long-term goal runtime authority is not active') END;
END;

CREATE TRIGGER IF NOT EXISTS long_term_goal_event_requires_contiguous_head
BEFORE INSERT ON long_term_goal_event
BEGIN
    SELECT CASE
        WHEN NEW.version != NEW.expected_version + 1
        THEN RAISE(ABORT, 'long-term goal version is invalid')
        WHEN NEW.expected_version = 0 AND EXISTS (
            SELECT 1 FROM long_term_goal_head AS head
            WHERE head.runtime_scope_id = NEW.runtime_scope_id
              AND head.workspace_id = NEW.workspace_id
              AND head.subject_id = NEW.subject_id
              AND head.goal_id = NEW.goal_id
        ) THEN RAISE(ABORT, 'long-term goal expected head is stale')
        WHEN NEW.expected_version > 0 AND NOT EXISTS (
            SELECT 1 FROM long_term_goal_head AS head
            WHERE head.runtime_scope_id = NEW.runtime_scope_id
              AND head.workspace_id = NEW.workspace_id
              AND head.subject_id = NEW.subject_id
              AND head.goal_id = NEW.goal_id
              AND head.version = NEW.expected_version
              AND head.event_digest = NEW.previous_event_digest
        ) THEN RAISE(ABORT, 'long-term goal expected head is stale')
        WHEN NEW.event_kind = 'REVOKED' AND (
            NEW.expected_version = 0
            OR NOT EXISTS (
                SELECT 1 FROM long_term_goal_head AS head
                WHERE head.runtime_scope_id = NEW.runtime_scope_id
                  AND head.workspace_id = NEW.workspace_id
                  AND head.subject_id = NEW.subject_id
                  AND head.goal_id = NEW.goal_id
                  AND head.status = 'ACTIVE'
            )
        ) THEN RAISE(ABORT, 'long-term goal revoke requires active head')
    END;
END;

CREATE TRIGGER IF NOT EXISTS long_term_goal_head_requires_matching_event_insert
BEFORE INSERT ON long_term_goal_head
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM long_term_goal_event AS event
        WHERE event.runtime_scope_id = NEW.runtime_scope_id
          AND event.workspace_id = NEW.workspace_id
          AND event.subject_id = NEW.subject_id
          AND event.goal_id = NEW.goal_id
          AND event.version = NEW.version
          AND event.operation_id = NEW.operation_id
          AND event.goal_ref = NEW.goal_ref
          AND event.constraints_json = NEW.constraints_json
          AND event.event_digest = NEW.event_digest
          AND event.observed_at_ms = NEW.updated_at_ms
          AND (
              (event.event_kind = 'ACTIVATED' AND NEW.status = 'ACTIVE')
              OR (event.event_kind = 'REVOKED' AND NEW.status = 'REVOKED')
          )
    ) THEN RAISE(ABORT, 'long-term goal head has no matching event') END;
END;

CREATE TRIGGER IF NOT EXISTS long_term_goal_head_requires_matching_event_update
BEFORE UPDATE ON long_term_goal_head
BEGIN
    SELECT CASE WHEN
        NEW.runtime_scope_id != OLD.runtime_scope_id
        OR NEW.workspace_id != OLD.workspace_id
        OR NEW.subject_id != OLD.subject_id
        OR NEW.goal_id != OLD.goal_id
        OR NEW.version != OLD.version + 1
        OR NOT EXISTS (
            SELECT 1 FROM long_term_goal_event AS event
            WHERE event.runtime_scope_id = NEW.runtime_scope_id
              AND event.workspace_id = NEW.workspace_id
              AND event.subject_id = NEW.subject_id
              AND event.goal_id = NEW.goal_id
              AND event.version = NEW.version
              AND event.expected_version = OLD.version
              AND event.previous_event_digest = OLD.event_digest
              AND event.operation_id = NEW.operation_id
              AND event.goal_ref = NEW.goal_ref
              AND event.constraints_json = NEW.constraints_json
              AND event.event_digest = NEW.event_digest
              AND event.observed_at_ms = NEW.updated_at_ms
              AND (
                  (event.event_kind = 'ACTIVATED' AND NEW.status = 'ACTIVE')
                  OR (event.event_kind = 'REVOKED' AND NEW.status = 'REVOKED')
              )
        )
    THEN RAISE(ABORT, 'long-term goal head transition is invalid') END;
END;

COMMIT;
