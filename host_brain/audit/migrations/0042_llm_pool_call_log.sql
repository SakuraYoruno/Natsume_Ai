-- 0042_llm_pool_call_log.sql
-- Shared LLM pool observability log.

CREATE TABLE IF NOT EXISTS llm_pool_call_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id        TEXT    NOT NULL,
    scene_type        TEXT    NOT NULL,
    cognitive_tier    TEXT    NOT NULL,
    attempts_json     TEXT    NOT NULL DEFAULT '[]',
    attempt_count     INTEGER NOT NULL DEFAULT 0,
    final_model       TEXT,
    final_provider    TEXT,
    pool_exhausted    INTEGER NOT NULL DEFAULT 0,
    cached            INTEGER NOT NULL DEFAULT 0,
    degraded_to_rule  INTEGER NOT NULL DEFAULT 0,
    total_latency_ms  INTEGER NOT NULL DEFAULT 0,
    ts_ms             INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_llm_pool_call_log_request_id ON llm_pool_call_log(request_id);
CREATE INDEX IF NOT EXISTS idx_llm_pool_call_log_ts_ms ON llm_pool_call_log(ts_ms);
