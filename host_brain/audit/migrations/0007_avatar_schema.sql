CREATE TABLE IF NOT EXISTS avatar_action_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT,
    trigger TEXT,
    emotion_tag TEXT,
    expression_id TEXT,
    status TEXT,
    reason_code TEXT,
    stage TEXT,
    detail TEXT,
    ts_ms INTEGER
);

CREATE INDEX IF NOT EXISTS idx_avatar_action_request ON avatar_action_log(request_id);
CREATE INDEX IF NOT EXISTS idx_avatar_action_emotion ON avatar_action_log(emotion_tag);
CREATE INDEX IF NOT EXISTS idx_avatar_action_ts ON avatar_action_log(ts_ms);
