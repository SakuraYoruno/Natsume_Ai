-- 0044_llm_token_usage_log.sql
-- Persist estimated/actual LLM token usage by pool call.

CREATE TABLE IF NOT EXISTS llm_token_usage_log (
    id                         INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id                 TEXT    NOT NULL,
    scene_type                 TEXT    NOT NULL,
    cognitive_tier             TEXT    NOT NULL,
    provider                   TEXT,
    model_id                   TEXT,
    prompt_tokens              INTEGER NOT NULL DEFAULT 0,
    completion_tokens          INTEGER NOT NULL DEFAULT 0,
    total_tokens               INTEGER NOT NULL DEFAULT 0,
    billable_prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    billable_completion_tokens INTEGER NOT NULL DEFAULT 0,
    billable_total_tokens      INTEGER NOT NULL DEFAULT 0,
    estimated                  INTEGER NOT NULL DEFAULT 1,
    cached                     INTEGER NOT NULL DEFAULT 0,
    pool_exhausted             INTEGER NOT NULL DEFAULT 0,
    attempt_count              INTEGER NOT NULL DEFAULT 0,
    billable_attempt_count     INTEGER NOT NULL DEFAULT 0,
    source                     TEXT    NOT NULL DEFAULT 'estimate',
    detail_json                TEXT    NOT NULL DEFAULT '{}',
    ts_ms                      INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_llm_token_usage_log_request_id ON llm_token_usage_log(request_id);
CREATE INDEX IF NOT EXISTS idx_llm_token_usage_log_ts_ms ON llm_token_usage_log(ts_ms);
CREATE INDEX IF NOT EXISTS idx_llm_token_usage_log_scene ON llm_token_usage_log(scene_type);
