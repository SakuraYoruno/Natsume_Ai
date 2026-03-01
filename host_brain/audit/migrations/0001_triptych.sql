-- Triptych baseline schema for local SQLite ledger.

CREATE TABLE IF NOT EXISTS intent_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT,
    thread_id TEXT,
    turn_id TEXT,
    intent_id TEXT,
    session_id TEXT,
    seq INTEGER,
    ts_ms INTEGER,
    nonce TEXT,
    parse_stage TEXT,
    raw_text TEXT,
    normalized_json TEXT,
    proposer_node_id TEXT,
    model_snapshot TEXT,
    prompt_profile_id TEXT,
    tool_visibility_hash TEXT,
    proposed_intent_draft_json TEXT,
    lint_result TEXT
);

CREATE TABLE IF NOT EXISTS governor_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT,
    thread_id TEXT,
    turn_id TEXT,
    intent_id TEXT,
    decision TEXT,
    reason_code TEXT,
    rule_trace TEXT,
    tokens TEXT,
    snapshot_refs TEXT,
    stage TEXT,
    ts_ms INTEGER
);

CREATE TABLE IF NOT EXISTS skill_exec_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT,
    thread_id TEXT,
    turn_id TEXT,
    intent_id TEXT,
    event_id TEXT,
    skill_name TEXT,
    approval_token_ref TEXT,
    confirm_token_ref TEXT,
    undo_token_ref TEXT,
    result_status TEXT,
    result_code TEXT,
    result_digest TEXT,
    evidence_ref TEXT,
    manifest_ref TEXT,
    ts_ms INTEGER
);

CREATE TABLE IF NOT EXISTS error_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT,
    stage TEXT,
    reason_code TEXT,
    detail TEXT,
    ts_ms INTEGER
);

CREATE TABLE IF NOT EXISTS qa_violation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT,
    code TEXT,
    detail TEXT,
    field TEXT,
    ts_ms INTEGER
);

CREATE TABLE IF NOT EXISTS replay_guard (
    intent_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    nonce TEXT NOT NULL,
    ts_ms INTEGER NOT NULL,
    UNIQUE(session_id, nonce)
);

CREATE TABLE IF NOT EXISTS session_seq_guard (
    session_id TEXT PRIMARY KEY,
    seq INTEGER NOT NULL,
    updated_ts_ms INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_intent_request ON intent_log(request_id);
CREATE INDEX IF NOT EXISTS idx_intent_intent_id ON intent_log(intent_id);
CREATE INDEX IF NOT EXISTS idx_governor_request ON governor_log(request_id);
CREATE INDEX IF NOT EXISTS idx_governor_intent_id ON governor_log(intent_id);
CREATE INDEX IF NOT EXISTS idx_skill_request ON skill_exec_log(request_id);
CREATE INDEX IF NOT EXISTS idx_skill_intent_id ON skill_exec_log(intent_id);
CREATE INDEX IF NOT EXISTS idx_skill_evidence_ref ON skill_exec_log(evidence_ref);
