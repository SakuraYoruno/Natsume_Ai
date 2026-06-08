CREATE TABLE IF NOT EXISTS user_preference_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    person_id TEXT NOT NULL,
    session_id TEXT,
    domain TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    polarity TEXT NOT NULL,
    target_kind TEXT NOT NULL,
    target_id TEXT NOT NULL,
    target_label TEXT,
    attributes_json TEXT NOT NULL DEFAULT '{}',
    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
    confidence REAL NOT NULL DEFAULT 0.0,
    created_at_ms INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_user_preference_event_id ON user_preference_event(event_id);
CREATE INDEX IF NOT EXISTS idx_user_preference_event_person_domain ON user_preference_event(person_id, domain, created_at_ms);
CREATE INDEX IF NOT EXISTS idx_user_preference_event_target ON user_preference_event(domain, target_kind, target_id);

CREATE TABLE IF NOT EXISTS user_preference_profile (
    person_id TEXT NOT NULL,
    domain TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    profile_json TEXT NOT NULL DEFAULT '{}',
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY(person_id, domain)
);
CREATE INDEX IF NOT EXISTS idx_user_preference_profile_updated ON user_preference_profile(updated_at_ms);

CREATE TABLE IF NOT EXISTS recommendation_delivery_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    delivery_id TEXT NOT NULL,
    person_id TEXT NOT NULL,
    session_id TEXT,
    domain TEXT NOT NULL,
    platform TEXT,
    conversation_id TEXT,
    request_id TEXT,
    job_id TEXT,
    recommendations_ref TEXT,
    ranked_recommendations_ref TEXT,
    items_json TEXT NOT NULL DEFAULT '[]',
    message_payload_json TEXT NOT NULL DEFAULT '{}',
    created_at_ms INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_recommendation_delivery_id ON recommendation_delivery_log(delivery_id);
CREATE INDEX IF NOT EXISTS idx_recommendation_delivery_person_domain ON recommendation_delivery_log(person_id, domain, created_at_ms);
CREATE INDEX IF NOT EXISTS idx_recommendation_delivery_session ON recommendation_delivery_log(session_id, created_at_ms);
CREATE INDEX IF NOT EXISTS idx_recommendation_delivery_conversation ON recommendation_delivery_log(conversation_id, created_at_ms);
