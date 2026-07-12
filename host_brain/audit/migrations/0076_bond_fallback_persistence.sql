-- AW-03 Bond fallback persistence authority.
--
-- bond_events is the mutable projection required by the Neo4j-compatible
-- fallback API.  Every distinct canonical event write is retained in the
-- immutable journal in the same transaction as the projection update.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS bond_events (
    event_id TEXT PRIMARY KEY
        CHECK(length(trim(event_id)) BETWEEN 1 AND 512),
    user_id TEXT NOT NULL
        CHECK(length(trim(user_id)) BETWEEN 1 AND 512),
    session_id TEXT NOT NULL
        CHECK(length(trim(session_id)) BETWEEN 1 AND 512),
    topic_id TEXT NOT NULL
        CHECK(length(trim(topic_id)) BETWEEN 1 AND 512),
    capability_hint TEXT NOT NULL
        CHECK(length(trim(capability_hint)) BETWEEN 1 AND 512),
    evidence_refs TEXT NOT NULL
        CHECK(length(CAST(evidence_refs AS BLOB)) BETWEEN 2 AND 1048576),
    payload TEXT NOT NULL
        CHECK(length(CAST(payload AS BLOB)) BETWEEN 2 AND 2097152),
    ts_ms INTEGER NOT NULL CHECK(ts_ms >= 0)
);

CREATE TABLE IF NOT EXISTS bond_fallback_event_journal (
    journal_id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_ref TEXT NOT NULL UNIQUE
        CHECK(length(trim(operation_ref)) BETWEEN 1 AND 512),
    event_id TEXT NOT NULL
        CHECK(length(trim(event_id)) BETWEEN 1 AND 512),
    change_kind TEXT NOT NULL CHECK(change_kind IN ('INSERT', 'UPDATE')),
    request_json TEXT NOT NULL
        CHECK(length(CAST(request_json AS BLOB)) BETWEEN 2 AND 2097152),
    request_digest TEXT NOT NULL
        CHECK(length(request_digest) = 71 AND substr(request_digest, 1, 7) = 'sha256:'),
    before_json TEXT,
    before_digest TEXT
        CHECK(before_digest IS NULL OR (
            length(before_digest) = 71 AND substr(before_digest, 1, 7) = 'sha256:'
        )),
    result_json TEXT NOT NULL
        CHECK(length(CAST(result_json AS BLOB)) BETWEEN 2 AND 2097152),
    result_digest TEXT NOT NULL
        CHECK(length(result_digest) = 71 AND substr(result_digest, 1, 7) = 'sha256:'),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
    UNIQUE(event_id, request_digest)
);

CREATE INDEX IF NOT EXISTS idx_bond_user
    ON bond_events(user_id);
CREATE INDEX IF NOT EXISTS idx_bond_ts
    ON bond_events(ts_ms);
CREATE INDEX IF NOT EXISTS idx_bond_fallback_journal_event
    ON bond_fallback_event_journal(event_id, journal_id);
CREATE INDEX IF NOT EXISTS idx_bond_fallback_journal_created
    ON bond_fallback_event_journal(created_at_ms, journal_id);

CREATE TRIGGER IF NOT EXISTS trg_bond_fallback_journal_no_update
BEFORE UPDATE ON bond_fallback_event_journal
BEGIN
    SELECT RAISE(ABORT, 'BOND_FALLBACK_EVENT_JOURNAL_IMMUTABLE');
END;

CREATE TRIGGER IF NOT EXISTS trg_bond_fallback_journal_no_delete
BEFORE DELETE ON bond_fallback_event_journal
BEGIN
    SELECT RAISE(ABORT, 'BOND_FALLBACK_EVENT_JOURNAL_IMMUTABLE');
END;

COMMIT;
