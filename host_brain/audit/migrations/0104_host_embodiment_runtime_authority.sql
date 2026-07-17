-- D8: fence one canonical Host embodiment authority per durable runtime
-- scope.  Existing 0083 rows deliberately remain NULL-scoped: a new runtime
-- must not silently claim historical physical work whose ownership cannot be
-- reconstructed.  All new repository writes supply an immutable scope.

BEGIN IMMEDIATE;

ALTER TABLE host_embodiment_outbox
    ADD COLUMN runtime_scope_id TEXT;

CREATE INDEX IF NOT EXISTS idx_host_embodiment_outbox_scope_ready
    ON host_embodiment_outbox(runtime_scope_id, status, created_at_ms, outbox_id);

CREATE TRIGGER IF NOT EXISTS host_embodiment_outbox_scope_no_update
BEFORE UPDATE OF runtime_scope_id
ON host_embodiment_outbox
BEGIN
    SELECT RAISE(ABORT, 'host embodiment outbox runtime scope is immutable');
END;

-- One row retains the monotonic fencing counter for a durable scope.  Release
-- and expiry update the row instead of deleting it, so an old process can
-- never regain authority by reusing its owner name.
CREATE TABLE IF NOT EXISTS host_embodiment_runtime_lease (
    runtime_scope_id TEXT NOT NULL PRIMARY KEY
        CHECK(length(trim(runtime_scope_id)) BETWEEN 1 AND 512),
    owner_id TEXT NOT NULL
        CHECK(length(trim(owner_id)) BETWEEN 1 AND 512),
    lease_id TEXT NOT NULL UNIQUE
        CHECK(length(trim(lease_id)) BETWEEN 1 AND 512),
    fencing_token INTEGER NOT NULL
        CHECK(typeof(fencing_token) = 'integer' AND fencing_token >= 1),
    issued_at_ms INTEGER NOT NULL
        CHECK(typeof(issued_at_ms) = 'integer' AND issued_at_ms >= 0),
    expires_at_ms INTEGER NOT NULL
        CHECK(typeof(expires_at_ms) = 'integer' AND expires_at_ms > issued_at_ms),
    released_at_ms INTEGER
        CHECK(released_at_ms IS NULL OR (
            typeof(released_at_ms) = 'integer' AND released_at_ms >= issued_at_ms
        ))
);

CREATE INDEX IF NOT EXISTS idx_host_embodiment_runtime_lease_active
    ON host_embodiment_runtime_lease(expires_at_ms, released_at_ms);

CREATE TRIGGER IF NOT EXISTS host_embodiment_runtime_lease_no_delete
BEFORE DELETE ON host_embodiment_runtime_lease
BEGIN
    SELECT RAISE(ABORT, 'host embodiment runtime lease is append-only');
END;

-- This is a Host-owned durable record of the Edge-issued lease, not a second
-- Edge authority.  Its writes and releases are fenced by the current runtime
-- lease; its exact session/owner tuple remains readable after a Host crash so
-- the replacement runtime can perform an exact Edge reconnect.
CREATE TABLE IF NOT EXISTS host_embodiment_edge_session_lease (
    runtime_scope_id TEXT NOT NULL PRIMARY KEY
        REFERENCES host_embodiment_runtime_lease(runtime_scope_id) ON DELETE RESTRICT,
    session_id TEXT NOT NULL
        CHECK(length(trim(session_id)) BETWEEN 1 AND 512),
    owner_id TEXT NOT NULL
        CHECK(length(trim(owner_id)) BETWEEN 1 AND 512),
    lease_id TEXT NOT NULL
        CHECK(length(trim(lease_id)) BETWEEN 1 AND 512),
    fencing_token INTEGER NOT NULL
        CHECK(typeof(fencing_token) = 'integer' AND fencing_token >= 1),
    issued_at_ms INTEGER NOT NULL
        CHECK(typeof(issued_at_ms) = 'integer' AND issued_at_ms >= 0),
    expires_at_ms INTEGER NOT NULL
        CHECK(typeof(expires_at_ms) = 'integer' AND expires_at_ms > issued_at_ms),
    runtime_lease_id TEXT NOT NULL
        CHECK(length(trim(runtime_lease_id)) BETWEEN 1 AND 512),
    runtime_fencing_token INTEGER NOT NULL
        CHECK(typeof(runtime_fencing_token) = 'integer' AND runtime_fencing_token >= 1),
    updated_at_ms INTEGER NOT NULL
        CHECK(typeof(updated_at_ms) = 'integer' AND updated_at_ms >= 0),
    released_at_ms INTEGER
        CHECK(released_at_ms IS NULL OR (
            typeof(released_at_ms) = 'integer' AND released_at_ms >= issued_at_ms
        )),
    UNIQUE(runtime_scope_id, session_id, owner_id, lease_id, fencing_token)
);

CREATE INDEX IF NOT EXISTS idx_host_embodiment_edge_session_lookup
    ON host_embodiment_edge_session_lease(runtime_scope_id, session_id, owner_id, released_at_ms);

CREATE TRIGGER IF NOT EXISTS host_embodiment_edge_session_lease_no_delete
BEFORE DELETE ON host_embodiment_edge_session_lease
BEGIN
    SELECT RAISE(ABORT, 'host embodiment edge session lease is append-only');
END;

COMMIT;
