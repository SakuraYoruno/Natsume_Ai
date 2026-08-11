-- AW-03-C1: Canon proposal/document command spine and rebuildable projections.
-- Expand-only. Production code must never create or alter this schema at runtime.

CREATE TABLE IF NOT EXISTS canon_command (
    command_id TEXT PRIMARY KEY CHECK(length(trim(command_id)) BETWEEN 1 AND 240),
    command_hash TEXT NOT NULL UNIQUE CHECK(length(command_hash) = 64),
    command_type TEXT NOT NULL CHECK(command_type IN (
        'SUBMIT_PROPOSAL',
        'REJECT_PROPOSAL',
        'PROJECT_PROPOSAL_STATUS',
        'PROJECT_DOCUMENT',
        'REMOVE_DOCUMENT'
    )),
    aggregate_kind TEXT NOT NULL CHECK(aggregate_kind IN ('PROPOSAL', 'DOCUMENT')),
    aggregate_id TEXT NOT NULL CHECK(length(trim(aggregate_id)) BETWEEN 1 AND 240),
    expected_version INTEGER NOT NULL CHECK(expected_version >= 0),
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    payload_digest TEXT NOT NULL CHECK(length(payload_digest) = 64),
    actor_id TEXT NOT NULL CHECK(length(trim(actor_id)) BETWEEN 1 AND 240),
    trace_id TEXT NOT NULL CHECK(length(trim(trace_id)) BETWEEN 1 AND 240),
    created_ts_ms INTEGER NOT NULL CHECK(created_ts_ms >= 0)
);

CREATE INDEX IF NOT EXISTS idx_canon_command_aggregate
    ON canon_command(aggregate_kind, aggregate_id, created_ts_ms, command_id);

CREATE TABLE IF NOT EXISTS canon_journal (
    journal_id TEXT PRIMARY KEY CHECK(length(trim(journal_id)) BETWEEN 1 AND 260),
    command_id TEXT NOT NULL UNIQUE,
    aggregate_kind TEXT NOT NULL CHECK(aggregate_kind IN ('PROPOSAL', 'DOCUMENT')),
    aggregate_id TEXT NOT NULL CHECK(length(trim(aggregate_id)) BETWEEN 1 AND 240),
    aggregate_version INTEGER NOT NULL CHECK(aggregate_version >= 1),
    event_type TEXT NOT NULL CHECK(event_type IN (
        'PROPOSAL_SUBMITTED',
        'PROPOSAL_REJECTED',
        'PROPOSAL_STATUS_PROJECTED',
        'DOCUMENT_PROJECTED',
        'DOCUMENT_REMOVED'
    )),
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    payload_digest TEXT NOT NULL CHECK(length(payload_digest) = 64),
    previous_event_digest TEXT CHECK(previous_event_digest IS NULL OR length(previous_event_digest) = 64),
    event_digest TEXT NOT NULL UNIQUE CHECK(length(event_digest) = 64),
    created_ts_ms INTEGER NOT NULL CHECK(created_ts_ms >= 0),
    FOREIGN KEY(command_id) REFERENCES canon_command(command_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_canon_journal_aggregate_version
    ON canon_journal(aggregate_kind, aggregate_id, aggregate_version);

CREATE INDEX IF NOT EXISTS idx_canon_journal_replay
    ON canon_journal(aggregate_kind, aggregate_id, aggregate_version, journal_id);

CREATE TABLE IF NOT EXISTS canon_projection_head (
    aggregate_kind TEXT NOT NULL CHECK(aggregate_kind IN ('PROPOSAL', 'DOCUMENT')),
    aggregate_id TEXT NOT NULL CHECK(length(trim(aggregate_id)) BETWEEN 1 AND 240),
    version INTEGER NOT NULL CHECK(version >= 1),
    last_journal_id TEXT NOT NULL UNIQUE,
    last_event_digest TEXT NOT NULL CHECK(length(last_event_digest) = 64),
    updated_ts_ms INTEGER NOT NULL CHECK(updated_ts_ms >= 0),
    PRIMARY KEY(aggregate_kind, aggregate_id),
    FOREIGN KEY(last_journal_id) REFERENCES canon_journal(journal_id)
);

CREATE TABLE IF NOT EXISTS canon_proposal_projection (
    proposal_id TEXT PRIMARY KEY CHECK(length(trim(proposal_id)) BETWEEN 1 AND 240),
    canon_kind TEXT NOT NULL DEFAULT 'world' CHECK(canon_kind = 'world'),
    target_id TEXT NOT NULL CHECK(length(trim(target_id)) BETWEEN 1 AND 160),
    base_version INTEGER NOT NULL CHECK(base_version >= 0),
    proposed_version INTEGER NOT NULL CHECK(proposed_version = base_version + 1),
    proposed_hash TEXT NOT NULL CHECK(length(proposed_hash) = 64),
    proposed_title TEXT NOT NULL CHECK(length(trim(proposed_title)) >= 1),
    proposed_tags_json TEXT NOT NULL CHECK(json_valid(proposed_tags_json)),
    proposed_body TEXT NOT NULL CHECK(length(trim(proposed_body)) >= 1),
    source_intent_id TEXT,
    proposer_node_id TEXT NOT NULL CHECK(length(trim(proposer_node_id)) BETWEEN 1 AND 240),
    summary TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN (
        'pending', 'rejected', 'APPLY_PREPARED', 'APPLIED',
        'ROLLBACK_PREPARED', 'ROLLED_BACK'
    )),
    status_reason TEXT,
    status_actor_id TEXT,
    projection_version INTEGER NOT NULL CHECK(projection_version >= 1),
    last_journal_id TEXT NOT NULL UNIQUE,
    created_ts_ms INTEGER NOT NULL CHECK(created_ts_ms >= 0),
    updated_ts_ms INTEGER NOT NULL CHECK(updated_ts_ms >= created_ts_ms),
    FOREIGN KEY(last_journal_id) REFERENCES canon_journal(journal_id)
);

CREATE INDEX IF NOT EXISTS idx_canon_proposal_projection_status
    ON canon_proposal_projection(status, updated_ts_ms DESC, proposal_id);

CREATE INDEX IF NOT EXISTS idx_canon_proposal_projection_target
    ON canon_proposal_projection(target_id, updated_ts_ms DESC, proposal_id);

CREATE TABLE IF NOT EXISTS canon_document_projection (
    doc_id TEXT PRIMARY KEY CHECK(length(trim(doc_id)) BETWEEN 1 AND 160),
    canon_version INTEGER NOT NULL CHECK(canon_version >= 1),
    canon_hash TEXT NOT NULL CHECK(length(canon_hash) = 64),
    status TEXT NOT NULL,
    title TEXT NOT NULL CHECK(length(trim(title)) >= 1),
    tags_json TEXT NOT NULL CHECK(json_valid(tags_json)),
    body TEXT NOT NULL CHECK(length(trim(body)) >= 1),
    updated_by TEXT NOT NULL CHECK(length(trim(updated_by)) BETWEEN 1 AND 240),
    canon_ts_ms INTEGER NOT NULL CHECK(canon_ts_ms >= 0),
    source_path TEXT NOT NULL CHECK(length(trim(source_path)) >= 1),
    projection_version INTEGER NOT NULL CHECK(projection_version >= 1),
    last_journal_id TEXT NOT NULL UNIQUE,
    projected_ts_ms INTEGER NOT NULL CHECK(projected_ts_ms >= 0),
    FOREIGN KEY(last_journal_id) REFERENCES canon_journal(journal_id)
);

CREATE INDEX IF NOT EXISTS idx_canon_document_projection_status
    ON canon_document_projection(status, doc_id);

-- The source of truth is append-only. Projections are deliberately mutable and
-- may be deleted/rebuilt by CanonRepository from canon_journal.
CREATE TRIGGER IF NOT EXISTS canon_command_no_update
BEFORE UPDATE ON canon_command
BEGIN
    SELECT RAISE(ABORT, 'canon_command is immutable');
END;

CREATE TRIGGER IF NOT EXISTS canon_command_no_delete
BEFORE DELETE ON canon_command
BEGIN
    SELECT RAISE(ABORT, 'canon_command is immutable');
END;

CREATE TRIGGER IF NOT EXISTS canon_journal_no_update
BEFORE UPDATE ON canon_journal
BEGIN
    SELECT RAISE(ABORT, 'canon_journal is immutable');
END;

CREATE TRIGGER IF NOT EXISTS canon_journal_no_delete
BEFORE DELETE ON canon_journal
BEGIN
    SELECT RAISE(ABORT, 'canon_journal is immutable');
END;
