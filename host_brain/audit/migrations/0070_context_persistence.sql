-- AW-03 Context-1: central migration for conversation and context ledgers.
--
-- These tables historically existed only in SQLiteLedger's runtime schema
-- bootstrap.  From 0070 onward the migration catalog is their production
-- authority.  TopicLedger and TopicState intentionally share one table and
-- one repository writer.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS conversation_turn (
    turn_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    person_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    ts_ms INTEGER NOT NULL,
    turn_index INTEGER NOT NULL,
    lang TEXT NOT NULL DEFAULT 'zh',
    token_count INTEGER,
    corrupted INTEGER NOT NULL DEFAULT 0,
    metadata TEXT,
    created_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS session_topic_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    person_id TEXT,
    topic_key TEXT NOT NULL,
    last_answer_turn_index INTEGER NOT NULL,
    last_answer_request_id TEXT,
    source_assistant_act TEXT,
    confidence REAL NOT NULL DEFAULT 1.0,
    stale_after_turn_index INTEGER NOT NULL DEFAULT 0,
    metadata TEXT,
    updated_at_ms INTEGER NOT NULL,
    UNIQUE(session_id, topic_key)
);

CREATE TABLE IF NOT EXISTS session_tactic_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    topic_key TEXT NOT NULL,
    interaction_move TEXT,
    tactic_family TEXT NOT NULL,
    tactic_variant TEXT,
    trajectory_stage TEXT,
    ts_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS conversation_belief_state (
    session_id TEXT NOT NULL,
    person_id TEXT NOT NULL DEFAULT 'default',
    version INTEGER NOT NULL DEFAULT 1,
    belief_json TEXT NOT NULL DEFAULT '{}',
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY(session_id, person_id)
);

CREATE TABLE IF NOT EXISTS conversation_experience_event (
    event_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    person_id TEXT NOT NULL DEFAULT 'default',
    user_text TEXT NOT NULL DEFAULT '',
    selected_intent TEXT NOT NULL DEFAULT '',
    response_policy TEXT NOT NULL DEFAULT '',
    understanding_json TEXT NOT NULL DEFAULT '{}',
    belief_before_json TEXT NOT NULL DEFAULT '{}',
    belief_after_json TEXT NOT NULL DEFAULT '{}',
    assistant_reply TEXT NOT NULL DEFAULT '',
    outcome_signal TEXT NOT NULL DEFAULT 'unknown',
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS conversation_correction_event (
    correction_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL DEFAULT '',
    request_id TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    person_id TEXT NOT NULL DEFAULT 'default',
    raw_text TEXT NOT NULL DEFAULT '',
    wrong_inference_json TEXT NOT NULL DEFAULT '{}',
    correct_reference_json TEXT NOT NULL DEFAULT '{}',
    repair_lesson TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0.0,
    created_at_ms INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS conversation_experience_lesson (
    lesson_id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL DEFAULT 'default',
    domain TEXT NOT NULL DEFAULT 'general',
    pattern TEXT NOT NULL DEFAULT '',
    lesson TEXT NOT NULL DEFAULT '',
    source_event_ids_json TEXT NOT NULL DEFAULT '[]',
    success_count INTEGER NOT NULL DEFAULT 0,
    failure_count INTEGER NOT NULL DEFAULT 0,
    confidence REAL NOT NULL DEFAULT 0.0,
    updated_at_ms INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS memory_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    person_id TEXT NOT NULL DEFAULT 'default',
    session_id TEXT,
    conversation_id TEXT,
    source_kind TEXT NOT NULL,
    event_kind TEXT NOT NULL,
    visibility TEXT NOT NULL
        CHECK(visibility IN ('visible', 'private', 'system', 'projection')),
    role TEXT CHECK(role IN ('user', 'assistant') OR role IS NULL OR role = ''),
    content TEXT NOT NULL DEFAULT '',
    content_summary TEXT NOT NULL DEFAULT '',
    causality_json TEXT NOT NULL DEFAULT '{}',
    refs_json TEXT NOT NULL DEFAULT '{}',
    salience REAL NOT NULL DEFAULT 0.0,
    valence REAL NOT NULL DEFAULT 0.0,
    uncertainty REAL NOT NULL DEFAULT 0.0,
    confidence REAL NOT NULL DEFAULT 1.0,
    projection_status_json TEXT NOT NULL DEFAULT '{}',
    created_at_ms INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS memory_sanitize_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id TEXT NOT NULL DEFAULT 'default',
    summary_json TEXT NOT NULL DEFAULT '{}',
    created_at_ms INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_conversation_turn_session_ts
    ON conversation_turn(session_id, ts_ms);
CREATE INDEX IF NOT EXISTS idx_conversation_turn_session_turn_idx
    ON conversation_turn(session_id, turn_index);
CREATE INDEX IF NOT EXISTS idx_conversation_turn_request
    ON conversation_turn(request_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_conversation_turn_session_request_role_unique
    ON conversation_turn(session_id, request_id, role);

CREATE INDEX IF NOT EXISTS idx_session_topic_state_session
    ON session_topic_state(session_id);
CREATE INDEX IF NOT EXISTS idx_session_topic_state_updated
    ON session_topic_state(updated_at_ms);
CREATE INDEX IF NOT EXISTS idx_session_topic_state_request
    ON session_topic_state(last_answer_request_id);

CREATE INDEX IF NOT EXISTS idx_session_tactic_ledger_session_ts
    ON session_tactic_ledger(session_id, ts_ms DESC);
CREATE INDEX IF NOT EXISTS idx_session_tactic_ledger_topic
    ON session_tactic_ledger(session_id, topic_key, ts_ms DESC);
CREATE INDEX IF NOT EXISTS idx_session_tactic_ledger_request
    ON session_tactic_ledger(request_id);

CREATE INDEX IF NOT EXISTS idx_conversation_experience_event_session
    ON conversation_experience_event(session_id, person_id, created_at_ms);
CREATE INDEX IF NOT EXISTS idx_conversation_correction_session
    ON conversation_correction_event(session_id, person_id, created_at_ms);
CREATE INDEX IF NOT EXISTS idx_conversation_lesson_domain
    ON conversation_experience_lesson(person_id, domain, updated_at_ms);

CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_event_id
    ON memory_event(event_id);
CREATE INDEX IF NOT EXISTS idx_memory_event_person_ts
    ON memory_event(person_id, created_at_ms DESC);
CREATE INDEX IF NOT EXISTS idx_memory_event_session_ts
    ON memory_event(session_id, created_at_ms DESC);
CREATE INDEX IF NOT EXISTS idx_memory_event_visibility_ts
    ON memory_event(person_id, visibility, created_at_ms DESC);
CREATE INDEX IF NOT EXISTS idx_memory_event_kind_ts
    ON memory_event(event_kind, created_at_ms DESC);

COMMIT;
