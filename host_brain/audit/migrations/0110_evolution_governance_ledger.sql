-- NE-06: proposal-only Feedback, Knowledge and Identity activation ledger.
-- Proposal rows are immutable; every apply/rollback is a separate event.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS evolution_feedback_proposal (
    proposal_id TEXT PRIMARY KEY CHECK(length(trim(proposal_id)) BETWEEN 1 AND 160),
    proposal_digest TEXT NOT NULL UNIQUE CHECK(length(proposal_digest) = 64),
    trace_digest TEXT NOT NULL CHECK(length(trace_digest) = 64),
    outcome_digest TEXT NOT NULL CHECK(length(outcome_digest) = 64),
    proposal_json TEXT NOT NULL CHECK(length(proposal_json) BETWEEN 2 AND 262144),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0)
);

CREATE TABLE IF NOT EXISTS evolution_feedback_event (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    proposal_id TEXT NOT NULL REFERENCES evolution_feedback_proposal(proposal_id) ON DELETE RESTRICT,
    event TEXT NOT NULL CHECK(event IN ('STAGED', 'APPLIED', 'ROLLED_BACK', 'REJECTED')),
    lab_report_ref TEXT,
    review_ref TEXT,
    rollback_ref TEXT,
    event_json TEXT NOT NULL CHECK(length(event_json) BETWEEN 2 AND 262144),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
    CHECK(event NOT IN ('APPLIED', 'ROLLED_BACK') OR (lab_report_ref IS NOT NULL AND review_ref IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS evolution_knowledge_entry (
    entry_id TEXT PRIMARY KEY CHECK(length(trim(entry_id)) BETWEEN 1 AND 1024),
    entry_digest TEXT NOT NULL UNIQUE CHECK(length(entry_digest) = 64),
    namespace TEXT NOT NULL CHECK(namespace LIKE 'life-memory://%' OR namespace LIKE 'project-knowledge://%'),
    workspace_id TEXT NOT NULL CHECK(length(trim(workspace_id)) BETWEEN 1 AND 255),
    content_digest TEXT NOT NULL CHECK(length(content_digest) = 64),
    provenance_ref TEXT NOT NULL CHECK(length(trim(provenance_ref)) BETWEEN 1 AND 1024),
    version INTEGER NOT NULL CHECK(version >= 1),
    validity TEXT NOT NULL CHECK(length(trim(validity)) BETWEEN 1 AND 48),
    confidence REAL NOT NULL CHECK(confidence >= 0.0 AND confidence <= 1.0),
    conflict_group TEXT NOT NULL DEFAULT '',
    supersedes TEXT NOT NULL DEFAULT '',
    deprecated INTEGER NOT NULL DEFAULT 0 CHECK(deprecated IN (0, 1)),
    entry_json TEXT NOT NULL CHECK(length(entry_json) BETWEEN 2 AND 262144),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0)
);

CREATE TABLE IF NOT EXISTS evolution_knowledge_event (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    entry_id TEXT NOT NULL REFERENCES evolution_knowledge_entry(entry_id) ON DELETE RESTRICT,
    event TEXT NOT NULL CHECK(event IN ('PROPOSED', 'ACTIVATED', 'DEPRECATED', 'REJECTED')),
    lab_report_ref TEXT,
    review_ref TEXT,
    event_json TEXT NOT NULL CHECK(length(event_json) BETWEEN 2 AND 262144),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
    CHECK(event != 'ACTIVATED' OR (lab_report_ref IS NOT NULL AND review_ref IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS evolution_identity_plan (
    migration_id TEXT PRIMARY KEY CHECK(length(trim(migration_id)) BETWEEN 1 AND 160),
    plan_digest TEXT NOT NULL UNIQUE CHECK(length(plan_digest) = 64),
    previous_snapshot_digest TEXT NOT NULL CHECK(length(previous_snapshot_digest) = 64),
    proposed_snapshot_digest TEXT NOT NULL CHECK(length(proposed_snapshot_digest) = 64),
    diff_digest TEXT NOT NULL CHECK(length(diff_digest) = 64),
    rollback_snapshot_digest TEXT NOT NULL CHECK(length(rollback_snapshot_digest) = 64),
    activation_version INTEGER NOT NULL CHECK(activation_version >= 1),
    plan_json TEXT NOT NULL CHECK(length(plan_json) BETWEEN 2 AND 262144),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0)
);

CREATE TABLE IF NOT EXISTS evolution_identity_event (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    migration_id TEXT NOT NULL REFERENCES evolution_identity_plan(migration_id) ON DELETE RESTRICT,
    event TEXT NOT NULL CHECK(event IN ('PROPOSED', 'ACTIVE', 'ROLLED_BACK', 'REJECTED')),
    regression_report_ref TEXT,
    review_ref TEXT,
    event_json TEXT NOT NULL CHECK(length(event_json) BETWEEN 2 AND 262144),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
    CHECK(event NOT IN ('ACTIVE', 'ROLLED_BACK') OR (regression_report_ref IS NOT NULL AND review_ref IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS idx_evolution_feedback_event_proposal
    ON evolution_feedback_event(proposal_id, sequence);
CREATE INDEX IF NOT EXISTS idx_evolution_knowledge_namespace
    ON evolution_knowledge_entry(namespace, workspace_id, created_at_ms, entry_id);
CREATE INDEX IF NOT EXISTS idx_evolution_knowledge_event_entry
    ON evolution_knowledge_event(entry_id, sequence);
CREATE INDEX IF NOT EXISTS idx_evolution_identity_event_plan
    ON evolution_identity_event(migration_id, sequence);

CREATE TRIGGER IF NOT EXISTS evolution_feedback_proposal_no_update
BEFORE UPDATE ON evolution_feedback_proposal
BEGIN SELECT RAISE(ABORT, 'Feedback proposal is immutable'); END;
CREATE TRIGGER IF NOT EXISTS evolution_feedback_proposal_no_delete
BEFORE DELETE ON evolution_feedback_proposal
BEGIN SELECT RAISE(ABORT, 'Feedback proposal is append-only'); END;
CREATE TRIGGER IF NOT EXISTS evolution_feedback_event_no_update
BEFORE UPDATE ON evolution_feedback_event
BEGIN SELECT RAISE(ABORT, 'Feedback event is immutable'); END;
CREATE TRIGGER IF NOT EXISTS evolution_feedback_event_no_delete
BEFORE DELETE ON evolution_feedback_event
BEGIN SELECT RAISE(ABORT, 'Feedback event is append-only'); END;
CREATE TRIGGER IF NOT EXISTS evolution_knowledge_entry_no_update
BEFORE UPDATE ON evolution_knowledge_entry
BEGIN SELECT RAISE(ABORT, 'Knowledge entry is immutable'); END;
CREATE TRIGGER IF NOT EXISTS evolution_knowledge_entry_no_delete
BEFORE DELETE ON evolution_knowledge_entry
BEGIN SELECT RAISE(ABORT, 'Knowledge entry is append-only'); END;
CREATE TRIGGER IF NOT EXISTS evolution_knowledge_event_no_update
BEFORE UPDATE ON evolution_knowledge_event
BEGIN SELECT RAISE(ABORT, 'Knowledge event is immutable'); END;
CREATE TRIGGER IF NOT EXISTS evolution_knowledge_event_no_delete
BEFORE DELETE ON evolution_knowledge_event
BEGIN SELECT RAISE(ABORT, 'Knowledge event is append-only'); END;
CREATE TRIGGER IF NOT EXISTS evolution_identity_plan_no_update
BEFORE UPDATE ON evolution_identity_plan
BEGIN SELECT RAISE(ABORT, 'Identity migration plan is immutable'); END;
CREATE TRIGGER IF NOT EXISTS evolution_identity_plan_no_delete
BEFORE DELETE ON evolution_identity_plan
BEGIN SELECT RAISE(ABORT, 'Identity migration plan is append-only'); END;
CREATE TRIGGER IF NOT EXISTS evolution_identity_event_no_update
BEFORE UPDATE ON evolution_identity_event
BEGIN SELECT RAISE(ABORT, 'Identity migration event is immutable'); END;
CREATE TRIGGER IF NOT EXISTS evolution_identity_event_no_delete
BEFORE DELETE ON evolution_identity_event
BEGIN SELECT RAISE(ABORT, 'Identity migration event is append-only'); END;

COMMIT;
