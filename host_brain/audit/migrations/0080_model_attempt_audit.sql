-- AW05: durable canonical model-attempt audit journal.
-- Raw prompts, outputs and credentials are deliberately excluded.

BEGIN IMMEDIATE;

-- The canonical graph runtime still emits the route/control projection in
-- addition to per-attempt rows.  Fresh migration-only databases must own this
-- table too; relying on SQLiteLedger's legacy inline bootstrap made a clean
-- production install fail on its first graph completion.
CREATE TABLE IF NOT EXISTS model_control_log (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id              TEXT,
    node_id                 TEXT,
    model_node_id           TEXT,
    model_profile_id        TEXT,
    provider                TEXT,
    model_id                TEXT,
    expected_param_hash     TEXT,
    actual_param_hash       TEXT,
    drift_detected          INTEGER,
    approval_token_ref      TEXT,
    evidence_ref            TEXT,
    reason_code             TEXT,
    status                  TEXT,
    stage                   TEXT,
    ts_ms                   INTEGER
);

CREATE INDEX IF NOT EXISTS idx_model_control_request
    ON model_control_log (request_id);
CREATE INDEX IF NOT EXISTS idx_model_control_profile
    ON model_control_log (model_profile_id);
CREATE INDEX IF NOT EXISTS idx_model_control_ts
    ON model_control_log (ts_ms);

CREATE TABLE IF NOT EXISTS model_attempt_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    attempt_id          TEXT NOT NULL,
    request_id          TEXT NOT NULL,
    route_decision_id   TEXT NOT NULL,
    attempt_index       INTEGER NOT NULL CHECK(attempt_index >= 1),
    status              TEXT NOT NULL CHECK(status IN (
                            'started', 'succeeded', 'failed', 'timed_out',
                            'rejected', 'cancelled'
                        )),
    retry_reason        TEXT NOT NULL,
    adapter_id          TEXT NOT NULL,
    provider            TEXT NOT NULL,
    model_id            TEXT NOT NULL,
    prompt_id           TEXT NOT NULL,
    prompt_version      TEXT NOT NULL,
    prompt_digest       TEXT NOT NULL CHECK(length(prompt_digest) = 64),
    started_at_ms       INTEGER NOT NULL CHECK(started_at_ms >= 0),
    ended_at_ms         INTEGER,
    latency_ms          INTEGER,
    output_digest       TEXT,
    error_code          TEXT,
    error_message       TEXT,
    metadata            TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metadata)),
    ts_ms               INTEGER NOT NULL CHECK(ts_ms >= 0),
    UNIQUE(attempt_id, status)
);

CREATE INDEX IF NOT EXISTS idx_model_attempt_request
    ON model_attempt_log (request_id, attempt_index, id);
CREATE INDEX IF NOT EXISTS idx_model_attempt_route
    ON model_attempt_log (route_decision_id, id);
CREATE INDEX IF NOT EXISTS idx_model_attempt_prompt
    ON model_attempt_log (prompt_id, prompt_version, id);
CREATE INDEX IF NOT EXISTS idx_model_attempt_ts ON model_attempt_log (ts_ms);

COMMIT;
