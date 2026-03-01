CREATE TABLE IF NOT EXISTS voice_event_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT,
    session_id TEXT,
    event_type TEXT,
    direction TEXT,
    seq INTEGER,
    status TEXT,
    reason_code TEXT,
    stage TEXT,
    payload TEXT,
    detail TEXT,
    ts_ms INTEGER
);

CREATE INDEX IF NOT EXISTS idx_voice_event_request ON voice_event_log(request_id);
CREATE INDEX IF NOT EXISTS idx_voice_event_session ON voice_event_log(session_id);
CREATE INDEX IF NOT EXISTS idx_voice_event_type ON voice_event_log(event_type);
CREATE INDEX IF NOT EXISTS idx_voice_event_ts ON voice_event_log(ts_ms);
