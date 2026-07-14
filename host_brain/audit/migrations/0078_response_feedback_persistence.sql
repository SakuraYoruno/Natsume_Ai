-- Centralize the response-feedback projection previously created only by the
-- legacy inline SQLiteLedger schema.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS response_feedback_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_request_id TEXT,
    session_id TEXT,
    person_id TEXT,
    thread_id TEXT,
    target_text_hash TEXT,
    feedback_score REAL NOT NULL,
    feedback_tags TEXT,
    feedback_source TEXT,
    created_at_ms INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_response_feedback_request
    ON response_feedback_log(target_request_id);
CREATE INDEX IF NOT EXISTS idx_response_feedback_session
    ON response_feedback_log(session_id);
CREATE INDEX IF NOT EXISTS idx_response_feedback_person
    ON response_feedback_log(person_id);
CREATE INDEX IF NOT EXISTS idx_response_feedback_hash
    ON response_feedback_log(target_text_hash);
CREATE INDEX IF NOT EXISTS idx_response_feedback_created
    ON response_feedback_log(created_at_ms);

COMMIT;
