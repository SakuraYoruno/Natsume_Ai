-- NE-03: workspace-scoped World observation journal with an atomic head.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS world_observation_head (
    scope_id TEXT PRIMARY KEY CHECK(length(scope_id) = 64),
    workspace_id TEXT NOT NULL CHECK(length(trim(workspace_id)) BETWEEN 1 AND 240),
    subject_id TEXT NOT NULL CHECK(length(trim(subject_id)) BETWEEN 1 AND 240),
    revision INTEGER NOT NULL CHECK(revision >= 0),
    batch_digest TEXT NOT NULL CHECK(batch_digest = '' OR length(batch_digest) = 64),
    updated_at_ms INTEGER NOT NULL CHECK(updated_at_ms >= 0),
    UNIQUE(workspace_id, subject_id)
);

CREATE TABLE IF NOT EXISTS world_observation_batch (
    scope_id TEXT NOT NULL REFERENCES world_observation_head(scope_id) ON DELETE RESTRICT,
    revision INTEGER NOT NULL CHECK(revision >= 1),
    workspace_id TEXT NOT NULL CHECK(length(trim(workspace_id)) BETWEEN 1 AND 240),
    subject_id TEXT NOT NULL CHECK(length(trim(subject_id)) BETWEEN 1 AND 240),
    runtime_scope_id TEXT NOT NULL CHECK(length(trim(runtime_scope_id)) BETWEEN 1 AND 512),
    runtime_owner_id TEXT NOT NULL CHECK(length(trim(runtime_owner_id)) BETWEEN 1 AND 512),
    runtime_lease_id TEXT NOT NULL CHECK(length(trim(runtime_lease_id)) BETWEEN 1 AND 512),
    fencing_token INTEGER NOT NULL CHECK(typeof(fencing_token) = 'integer' AND fencing_token >= 1),
    batch_digest TEXT NOT NULL CHECK(length(batch_digest) = 64),
    previous_batch_digest TEXT NOT NULL CHECK(previous_batch_digest = '' OR length(previous_batch_digest) = 64),
    source_event_ref TEXT NOT NULL CHECK(length(trim(source_event_ref)) BETWEEN 4 AND 544),
    source_event_digest TEXT NOT NULL CHECK(length(source_event_digest) = 64),
    observed_at_ms INTEGER NOT NULL CHECK(observed_at_ms >= 0),
    batch_json TEXT NOT NULL CHECK(length(batch_json) BETWEEN 2 AND 1048576),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
    CHECK(observed_at_ms <= created_at_ms),
    PRIMARY KEY(scope_id, revision),
    UNIQUE(scope_id, batch_digest),
    UNIQUE(scope_id, source_event_ref)
);

CREATE INDEX IF NOT EXISTS idx_world_observation_batch_scope_revision
    ON world_observation_batch(scope_id, revision);

CREATE TRIGGER IF NOT EXISTS world_observation_batch_no_update
BEFORE UPDATE ON world_observation_batch
BEGIN SELECT RAISE(ABORT, 'World observation batch is immutable'); END;

CREATE TRIGGER IF NOT EXISTS world_observation_batch_no_delete
BEFORE DELETE ON world_observation_batch
BEGIN SELECT RAISE(ABORT, 'World observation journal is append-only'); END;

CREATE TRIGGER IF NOT EXISTS world_observation_batch_runtime_authority
BEFORE INSERT ON world_observation_batch
WHEN NOT EXISTS (
    SELECT 1
    FROM host_embodiment_runtime_lease AS lease
    WHERE lease.runtime_scope_id = NEW.runtime_scope_id
      AND lease.owner_id = NEW.runtime_owner_id
      AND lease.lease_id = NEW.runtime_lease_id
      AND lease.fencing_token = NEW.fencing_token
      AND lease.issued_at_ms <= NEW.created_at_ms
      AND lease.expires_at_ms > NEW.created_at_ms
      AND lease.released_at_ms IS NULL
)
BEGIN SELECT RAISE(ABORT, 'World observation runtime authority is stale'); END;

COMMIT;
