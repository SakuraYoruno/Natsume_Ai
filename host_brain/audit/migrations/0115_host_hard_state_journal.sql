-- NE-01: durable, runtime-fenced Host HardState mode authority.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS host_hard_state_event (
    runtime_scope_id TEXT NOT NULL
        CHECK(length(trim(runtime_scope_id)) BETWEEN 1 AND 512),
    revision INTEGER NOT NULL
        CHECK(typeof(revision) = 'integer' AND revision >= 1),
    event_id TEXT NOT NULL
        CHECK(length(trim(event_id)) BETWEEN 1 AND 512),
    idempotency_key TEXT NOT NULL
        CHECK(
            length(idempotency_key) = 71
            AND substr(idempotency_key, 1, 7) = 'sha256:'
            AND substr(idempotency_key, 8) NOT GLOB '*[^0-9a-f]*'
        ),
    event_kind TEXT NOT NULL
        CHECK(event_kind IN ('INITIALIZED', 'MODE_CHANGED')),
    from_mode TEXT NOT NULL
        CHECK(from_mode IN ('normal', 'focus', 'gaming', 'sleep', 'maintenance', 'locked')),
    to_mode TEXT NOT NULL
        CHECK(to_mode IN ('normal', 'focus', 'gaming', 'sleep', 'maintenance', 'locked')),
    source TEXT NOT NULL
        CHECK(length(trim(source)) BETWEEN 1 AND 128),
    reason TEXT NOT NULL DEFAULT ''
        CHECK(length(reason) <= 2048),
    previous_event_digest TEXT NOT NULL
        CHECK(previous_event_digest = '' OR (
            length(previous_event_digest) = 71
            AND substr(previous_event_digest, 1, 7) = 'sha256:'
            AND substr(previous_event_digest, 8) NOT GLOB '*[^0-9a-f]*'
        )),
    event_digest TEXT NOT NULL
        CHECK(
            length(event_digest) = 71
            AND substr(event_digest, 1, 7) = 'sha256:'
            AND substr(event_digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
    runtime_owner_id TEXT NOT NULL
        CHECK(length(trim(runtime_owner_id)) BETWEEN 1 AND 512),
    runtime_lease_id TEXT NOT NULL
        CHECK(length(trim(runtime_lease_id)) BETWEEN 1 AND 512),
    runtime_fencing_token INTEGER NOT NULL
        CHECK(typeof(runtime_fencing_token) = 'integer' AND runtime_fencing_token >= 1),
    runtime_issued_at_ms INTEGER NOT NULL
        CHECK(typeof(runtime_issued_at_ms) = 'integer' AND runtime_issued_at_ms >= 0),
    runtime_expires_at_ms INTEGER NOT NULL
        CHECK(typeof(runtime_expires_at_ms) = 'integer'
              AND runtime_expires_at_ms > runtime_issued_at_ms),
    transition_ts_ms INTEGER NOT NULL
        CHECK(typeof(transition_ts_ms) = 'integer' AND transition_ts_ms >= 0),
    created_at_ms INTEGER NOT NULL
        CHECK(typeof(created_at_ms) = 'integer' AND created_at_ms >= transition_ts_ms),
    PRIMARY KEY(runtime_scope_id, revision),
    UNIQUE(runtime_scope_id, event_id),
    UNIQUE(runtime_scope_id, idempotency_key),
    UNIQUE(runtime_scope_id, event_digest)
);

CREATE TABLE IF NOT EXISTS host_hard_state_head (
    runtime_scope_id TEXT NOT NULL PRIMARY KEY
        CHECK(length(trim(runtime_scope_id)) BETWEEN 1 AND 512),
    revision INTEGER NOT NULL
        CHECK(typeof(revision) = 'integer' AND revision >= 1),
    sys_mode TEXT NOT NULL
        CHECK(sys_mode IN ('normal', 'focus', 'gaming', 'sleep', 'maintenance', 'locked')),
    event_id TEXT NOT NULL
        CHECK(length(trim(event_id)) BETWEEN 1 AND 512),
    event_digest TEXT NOT NULL
        CHECK(
            length(event_digest) = 71
            AND substr(event_digest, 1, 7) = 'sha256:'
            AND substr(event_digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
    updated_at_ms INTEGER NOT NULL
        CHECK(typeof(updated_at_ms) = 'integer' AND updated_at_ms >= 0),
    FOREIGN KEY(runtime_scope_id, revision)
        REFERENCES host_hard_state_event(runtime_scope_id, revision)
        ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_host_hard_state_event_scope_time
    ON host_hard_state_event(runtime_scope_id, transition_ts_ms, revision);

CREATE TRIGGER IF NOT EXISTS host_hard_state_event_no_update
BEFORE UPDATE ON host_hard_state_event
BEGIN
    SELECT RAISE(ABORT, 'host hard state event is append-only');
END;

CREATE TRIGGER IF NOT EXISTS host_hard_state_event_no_delete
BEFORE DELETE ON host_hard_state_event
BEGIN
    SELECT RAISE(ABORT, 'host hard state event is append-only');
END;

CREATE TRIGGER IF NOT EXISTS host_hard_state_head_no_delete
BEFORE DELETE ON host_hard_state_head
BEGIN
    SELECT RAISE(ABORT, 'host hard state head cannot be deleted');
END;

CREATE TRIGGER IF NOT EXISTS host_hard_state_event_requires_active_runtime_lease
BEFORE INSERT ON host_hard_state_event
BEGIN
    SELECT CASE
        WHEN NOT EXISTS (
            SELECT 1
            FROM host_embodiment_runtime_lease AS lease
            WHERE lease.runtime_scope_id = NEW.runtime_scope_id
              AND lease.owner_id = NEW.runtime_owner_id
              AND lease.lease_id = NEW.runtime_lease_id
              AND lease.fencing_token = NEW.runtime_fencing_token
              AND lease.released_at_ms IS NULL
              AND lease.issued_at_ms = NEW.runtime_issued_at_ms
              AND lease.expires_at_ms >= NEW.runtime_expires_at_ms
              AND lease.issued_at_ms <= NEW.transition_ts_ms
              AND lease.expires_at_ms > NEW.transition_ts_ms
              AND lease.issued_at_ms <= NEW.created_at_ms
              AND lease.expires_at_ms > NEW.created_at_ms
              AND lease.expires_at_ms > (
                  CAST(
                      (julianday('now') - 2440587.5) * 86400000
                      AS INTEGER
                  )
              )
              AND NEW.created_at_ms BETWEEN (
                  CAST(
                      (julianday('now') - 2440587.5) * 86400000
                      AS INTEGER
                  ) - 5000
              ) AND (
                  CAST(
                      (julianday('now') - 2440587.5) * 86400000
                      AS INTEGER
                  ) + 5000
              )
        ) THEN RAISE(ABORT, 'host hard state runtime authority is not active')
    END;
END;

CREATE TRIGGER IF NOT EXISTS host_hard_state_event_requires_contiguous_head
BEFORE INSERT ON host_hard_state_event
BEGIN
    SELECT CASE
        WHEN NEW.revision = 1 AND (
            NEW.event_kind != 'INITIALIZED'
            OR NEW.from_mode != NEW.to_mode
            OR NEW.previous_event_digest != ''
            OR EXISTS (
                SELECT 1 FROM host_hard_state_head
                WHERE runtime_scope_id = NEW.runtime_scope_id
            )
        ) THEN RAISE(ABORT, 'host hard state initialization identity is invalid')
        WHEN NEW.revision > 1 AND (
            NEW.event_kind != 'MODE_CHANGED'
            OR NOT EXISTS (
                SELECT 1
                FROM host_hard_state_head AS head
                WHERE head.runtime_scope_id = NEW.runtime_scope_id
                  AND head.revision = NEW.revision - 1
                  AND head.sys_mode = NEW.from_mode
                  AND head.event_digest = NEW.previous_event_digest
            )
        ) THEN RAISE(ABORT, 'host hard state transition head is stale')
    END;
END;

CREATE TRIGGER IF NOT EXISTS host_hard_state_head_requires_matching_event_insert
BEFORE INSERT ON host_hard_state_head
BEGIN
    SELECT CASE
        WHEN NOT EXISTS (
            SELECT 1 FROM host_hard_state_event AS event
            WHERE event.runtime_scope_id = NEW.runtime_scope_id
              AND event.revision = NEW.revision
              AND event.to_mode = NEW.sys_mode
              AND event.event_id = NEW.event_id
              AND event.event_digest = NEW.event_digest
              AND event.transition_ts_ms = NEW.updated_at_ms
        ) THEN RAISE(ABORT, 'host hard state head has no matching event')
    END;
END;

CREATE TRIGGER IF NOT EXISTS host_hard_state_head_requires_matching_event_update
BEFORE UPDATE ON host_hard_state_head
BEGIN
    SELECT CASE
        WHEN NEW.runtime_scope_id != OLD.runtime_scope_id
          OR NEW.revision != OLD.revision + 1
          OR NOT EXISTS (
              SELECT 1 FROM host_hard_state_event AS event
              WHERE event.runtime_scope_id = NEW.runtime_scope_id
                AND event.revision = NEW.revision
                AND event.from_mode = OLD.sys_mode
                AND event.to_mode = NEW.sys_mode
                AND event.previous_event_digest = OLD.event_digest
                AND event.event_id = NEW.event_id
                AND event.event_digest = NEW.event_digest
                AND event.transition_ts_ms = NEW.updated_at_ms
          )
        THEN RAISE(ABORT, 'host hard state head transition is invalid')
    END;
END;

COMMIT;
