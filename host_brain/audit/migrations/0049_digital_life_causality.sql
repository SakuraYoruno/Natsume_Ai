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

CREATE TABLE IF NOT EXISTS life_experience_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    person_id TEXT NOT NULL,
    session_id TEXT,
    source_kind TEXT NOT NULL,
    event_kind TEXT NOT NULL,
    content_summary TEXT NOT NULL DEFAULT '',
    evidence_json TEXT NOT NULL DEFAULT '{}',
    salience REAL NOT NULL DEFAULT 0.0,
    valence REAL NOT NULL DEFAULT 0.0,
    uncertainty REAL NOT NULL DEFAULT 0.0,
    created_at_ms INTEGER NOT NULL,
    consumed_by_organs INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS organ_runtime_state (
    person_id TEXT NOT NULL,
    organ_id TEXT NOT NULL,
    state_json TEXT NOT NULL DEFAULT '{}',
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY(person_id, organ_id)
);

CREATE TABLE IF NOT EXISTS organ_runtime_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    experience_event_id TEXT NOT NULL,
    person_id TEXT NOT NULL,
    organ_id TEXT NOT NULL,
    delta_json TEXT NOT NULL DEFAULT '{}',
    state_before_json TEXT NOT NULL DEFAULT '{}',
    state_after_json TEXT NOT NULL DEFAULT '{}',
    created_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS organ_coupling_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    coupling_id TEXT NOT NULL UNIQUE,
    source_experience_event_id TEXT,
    person_id TEXT NOT NULL,
    source_organs_json TEXT NOT NULL DEFAULT '[]',
    target_organ_id TEXT NOT NULL,
    effect_json TEXT NOT NULL DEFAULT '{}',
    created_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS life_state_synthesis_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    synthesis_id TEXT NOT NULL UNIQUE,
    person_id TEXT NOT NULL,
    source_experience_event_id TEXT,
    organ_summary_json TEXT NOT NULL DEFAULT '{}',
    state_patch_json TEXT NOT NULL DEFAULT '{}',
    created_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS proactive_interaction_episode (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id TEXT NOT NULL UNIQUE,
    person_id TEXT NOT NULL,
    source_action_id TEXT,
    source_outbox_id TEXT,
    motive TEXT NOT NULL DEFAULT '',
    organ_sources_json TEXT NOT NULL DEFAULT '[]',
    message_text TEXT NOT NULL DEFAULT '',
    expected_response_kind TEXT NOT NULL DEFAULT '',
    sent_at_ms INTEGER NOT NULL DEFAULT 0,
    user_response_turn_id TEXT,
    outcome TEXT NOT NULL DEFAULT 'pending',
    organ_delta_after_json TEXT NOT NULL DEFAULT '{}',
    closed_at_ms INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_digital_life_state_event_person_ts ON digital_life_state_event(person_id, created_at_ms DESC);
CREATE INDEX IF NOT EXISTS idx_dialogue_life_influence_request ON dialogue_life_influence_log(request_id);
CREATE INDEX IF NOT EXISTS idx_dialogue_life_influence_person_ts ON dialogue_life_influence_log(person_id, created_at_ms DESC);
CREATE INDEX IF NOT EXISTS idx_embodiment_dispatch_person_ts ON embodiment_dispatch_log(person_id, created_at_ms DESC);
CREATE INDEX IF NOT EXISTS idx_work_environment_person_ts ON work_environment_event(person_id, created_at_ms DESC);
CREATE INDEX IF NOT EXISTS idx_proactive_life_outbox_person_ts ON proactive_life_outbox(person_id, created_at_ms DESC);
CREATE INDEX IF NOT EXISTS idx_life_experience_person_ts ON life_experience_event(person_id, created_at_ms DESC);
CREATE INDEX IF NOT EXISTS idx_life_experience_unconsumed ON life_experience_event(person_id, consumed_by_organs, created_at_ms);
CREATE INDEX IF NOT EXISTS idx_organ_runtime_event_person_ts ON organ_runtime_event(person_id, created_at_ms DESC);
CREATE INDEX IF NOT EXISTS idx_organ_coupling_person_ts ON organ_coupling_event(person_id, created_at_ms DESC);
CREATE INDEX IF NOT EXISTS idx_life_state_synthesis_person_ts ON life_state_synthesis_log(person_id, created_at_ms DESC);
CREATE INDEX IF NOT EXISTS idx_proactive_episode_person_ts ON proactive_interaction_episode(person_id, sent_at_ms DESC);
CREATE INDEX IF NOT EXISTS idx_proactive_episode_pending ON proactive_interaction_episode(person_id, outcome, sent_at_ms DESC);
