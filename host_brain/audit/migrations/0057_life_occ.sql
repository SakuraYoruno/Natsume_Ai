-- TASK-0476 / LTE-WP-50: digital_life_state expected-version OCC and catalog-owned schema.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS digital_life_state (
    person_id TEXT PRIMARY KEY,
    version INTEGER NOT NULL DEFAULT 1,
    state_json TEXT NOT NULL,
    updated_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS digital_life_state_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    person_id TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    event_type TEXT NOT NULL,
    delta_json TEXT NOT NULL DEFAULT '{}',
    state_before_json TEXT NOT NULL DEFAULT '{}',
    state_after_json TEXT NOT NULL DEFAULT '{}',
    visible_channels_json TEXT NOT NULL DEFAULT '[]',
    created_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS digital_life_state_occ_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    person_id TEXT NOT NULL,
    expected_version INTEGER,
    actual_version INTEGER,
    attempted_version INTEGER,
    status TEXT NOT NULL CHECK(status IN ('APPLIED','CONFLICT','FALLBACK_APPLIED')),
    reason_code TEXT NOT NULL,
    command_ref TEXT,
    source_kind TEXT NOT NULL DEFAULT 'life_state_command',
    created_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS dialogue_life_influence_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    request_id TEXT,
    person_id TEXT NOT NULL,
    session_id TEXT,
    user_message TEXT NOT NULL DEFAULT '',
    reply_text TEXT NOT NULL DEFAULT '',
    life_director_json TEXT NOT NULL DEFAULT '{}',
    life_causality_json TEXT NOT NULL DEFAULT '{}',
    created_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS embodiment_dispatch_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dispatch_id TEXT NOT NULL UNIQUE,
    person_id TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    target_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'UNKNOWN',
    reason_code TEXT,
    result_json TEXT NOT NULL DEFAULT '{}',
    created_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS work_environment_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    person_id TEXT NOT NULL,
    session_id TEXT,
    request_id TEXT,
    event_type TEXT NOT NULL,
    work_id TEXT,
    summary TEXT NOT NULL DEFAULT '',
    urgency TEXT NOT NULL DEFAULT 'medium',
    emotional_effect_json TEXT NOT NULL DEFAULT '{}',
    created_at_ms INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS proactive_life_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    outbox_id TEXT NOT NULL UNIQUE,
    person_id TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    motive TEXT NOT NULL DEFAULT '',
    pressure REAL NOT NULL DEFAULT 0.0,
    target_json TEXT NOT NULL DEFAULT '{}',
    message_json TEXT NOT NULL DEFAULT '{}',
    gate_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'UNKNOWN',
    reason_code TEXT,
    result_json TEXT NOT NULL DEFAULT '{}',
    created_at_ms INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_digital_life_state_person_version
    ON digital_life_state(person_id, version);

CREATE INDEX IF NOT EXISTS idx_digital_life_state_event_person_ts
    ON digital_life_state_event(person_id, created_at_ms DESC);

CREATE INDEX IF NOT EXISTS idx_digital_life_state_occ_person_ts
    ON digital_life_state_occ_event(person_id, created_at_ms DESC);

CREATE INDEX IF NOT EXISTS idx_dialogue_life_influence_request
    ON dialogue_life_influence_log(request_id);

CREATE INDEX IF NOT EXISTS idx_dialogue_life_influence_person_ts
    ON dialogue_life_influence_log(person_id, created_at_ms DESC);

CREATE INDEX IF NOT EXISTS idx_embodiment_dispatch_person_ts
    ON embodiment_dispatch_log(person_id, created_at_ms DESC);

CREATE INDEX IF NOT EXISTS idx_work_environment_person_ts
    ON work_environment_event(person_id, created_at_ms DESC);

CREATE INDEX IF NOT EXISTS idx_proactive_life_outbox_person_ts
    ON proactive_life_outbox(person_id, created_at_ms DESC);

COMMIT;
