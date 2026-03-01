CREATE TABLE IF NOT EXISTS clawd_action_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT,
    intent_id TEXT,
    target_desc TEXT,
    action_name TEXT,
    confidence REAL,
    before_artifact_ref TEXT,
    after_artifact_ref TEXT,
    manifest_ref TEXT,
    status TEXT,
    reason_code TEXT,
    stage TEXT,
    detail TEXT,
    ts_ms INTEGER
);

CREATE INDEX IF NOT EXISTS idx_clawd_action_request ON clawd_action_log(request_id);
CREATE INDEX IF NOT EXISTS idx_clawd_action_intent ON clawd_action_log(intent_id);
CREATE INDEX IF NOT EXISTS idx_clawd_action_ts ON clawd_action_log(ts_ms);
