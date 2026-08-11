-- AW07-A: generic durable operator query projection spine.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS operator_query_projection_journal (
    cursor                INTEGER PRIMARY KEY AUTOINCREMENT,
    schema_version        TEXT NOT NULL
        CHECK(schema_version = 'Natsume.operator.query_projection.v1'),
    projection_kind       TEXT NOT NULL CHECK(length(trim(projection_kind)) > 0),
    document_key          TEXT NOT NULL CHECK(length(trim(document_key)) > 0),
    source_cursor         INTEGER NOT NULL CHECK(source_cursor >= 0),
    source_digest         TEXT NOT NULL
        CHECK(length(source_digest) = 71 AND substr(source_digest, 1, 7) = 'sha256:'),
    source_payload_json   TEXT NOT NULL CHECK(json_valid(source_payload_json)),
    source_ref            TEXT NOT NULL CHECK(length(trim(source_ref)) > 0),
    observed_ts_ms        INTEGER NOT NULL CHECK(observed_ts_ms >= 0),
    UNIQUE(projection_kind, document_key, source_cursor, source_digest)
);

CREATE TABLE IF NOT EXISTS operator_query_projection_document (
    projection_kind       TEXT NOT NULL CHECK(length(trim(projection_kind)) > 0),
    document_key          TEXT NOT NULL CHECK(length(trim(document_key)) > 0),
    schema_version        TEXT NOT NULL
        CHECK(schema_version = 'Natsume.operator.query_projection.v1'),
    projection_version    INTEGER NOT NULL CHECK(projection_version >= 1),
    journal_cursor        INTEGER NOT NULL CHECK(journal_cursor >= 1),
    source_cursor         INTEGER NOT NULL CHECK(source_cursor >= 0),
    source_digest         TEXT NOT NULL
        CHECK(length(source_digest) = 71 AND substr(source_digest, 1, 7) = 'sha256:'),
    payload_json          TEXT NOT NULL CHECK(json_valid(payload_json)),
    payload_digest        TEXT NOT NULL
        CHECK(length(payload_digest) = 71 AND substr(payload_digest, 1, 7) = 'sha256:'),
    freshness_ts_ms       INTEGER NOT NULL CHECK(freshness_ts_ms >= 0),
    degraded_state        TEXT NOT NULL CHECK(degraded_state IN (
        'HEALTHY', 'STALE', 'REBUILDING', 'SOURCE_UNAVAILABLE', 'DIGEST_MISMATCH'
    )),
    degraded_detail       TEXT,
    updated_ts_ms         INTEGER NOT NULL CHECK(updated_ts_ms >= 0),
    PRIMARY KEY(projection_kind, document_key),
    FOREIGN KEY(journal_cursor)
        REFERENCES operator_query_projection_journal(cursor) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS operator_query_projection_state (
    projection_kind       TEXT PRIMARY KEY CHECK(length(trim(projection_kind)) > 0),
    schema_version        TEXT NOT NULL
        CHECK(schema_version = 'Natsume.operator.query_projection.v1'),
    projection_version    INTEGER NOT NULL CHECK(projection_version >= 0),
    projection_cursor     INTEGER NOT NULL CHECK(projection_cursor >= 0),
    source_cursor         INTEGER NOT NULL CHECK(source_cursor >= 0),
    source_digest         TEXT NOT NULL
        CHECK(length(source_digest) = 71 AND substr(source_digest, 1, 7) = 'sha256:'),
    freshness_ts_ms       INTEGER NOT NULL CHECK(freshness_ts_ms >= 0),
    degraded_state        TEXT NOT NULL CHECK(degraded_state IN (
        'HEALTHY', 'STALE', 'REBUILDING', 'SOURCE_UNAVAILABLE', 'DIGEST_MISMATCH'
    )),
    degraded_detail       TEXT,
    rebuilt_ts_ms         INTEGER,
    updated_ts_ms         INTEGER NOT NULL CHECK(updated_ts_ms >= 0)
);

-- One durable lease coordinates projection daemons across host processes.
-- The row is never deleted: every takeover increments fencing_token so a
-- delayed worker can no longer renew or release the new leader's lease.
CREATE TABLE IF NOT EXISTS operator_query_projection_leader (
    lease_name            TEXT PRIMARY KEY CHECK(length(trim(lease_name)) > 0),
    owner_id              TEXT NOT NULL CHECK(length(trim(owner_id)) > 0),
    fencing_token         INTEGER NOT NULL CHECK(fencing_token >= 1),
    acquired_ts_ms        INTEGER NOT NULL CHECK(acquired_ts_ms >= 0),
    lease_expires_ts_ms   INTEGER NOT NULL CHECK(lease_expires_ts_ms >= acquired_ts_ms),
    updated_ts_ms         INTEGER NOT NULL CHECK(updated_ts_ms >= acquired_ts_ms)
);

CREATE INDEX IF NOT EXISTS idx_operator_projection_journal_kind_cursor
    ON operator_query_projection_journal(projection_kind, cursor);
CREATE INDEX IF NOT EXISTS idx_operator_projection_document_freshness
    ON operator_query_projection_document(
        projection_kind, degraded_state, freshness_ts_ms DESC
    );
CREATE INDEX IF NOT EXISTS idx_operator_projection_leader_expiry
    ON operator_query_projection_leader(lease_expires_ts_ms, lease_name);

CREATE TRIGGER IF NOT EXISTS operator_projection_journal_no_update
BEFORE UPDATE ON operator_query_projection_journal
BEGIN
    SELECT RAISE(ABORT, 'operator projection journal is immutable');
END;

CREATE TRIGGER IF NOT EXISTS operator_projection_journal_no_delete
BEFORE DELETE ON operator_query_projection_journal
BEGIN
    SELECT RAISE(ABORT, 'operator projection journal is immutable');
END;

COMMIT;
