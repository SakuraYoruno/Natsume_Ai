CREATE TABLE IF NOT EXISTS vision_event_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT,
    intent_id TEXT,
    event_type TEXT,
    status TEXT,
    reason_code TEXT,
    stage TEXT,
    source_ref TEXT,
    payload TEXT,
    detail TEXT,
    ts_ms INTEGER
);

CREATE INDEX IF NOT EXISTS idx_vision_event_request ON vision_event_log(request_id);
CREATE INDEX IF NOT EXISTS idx_vision_event_type ON vision_event_log(event_type);
CREATE INDEX IF NOT EXISTS idx_vision_event_source_ref ON vision_event_log(source_ref);
CREATE INDEX IF NOT EXISTS idx_vision_event_ts ON vision_event_log(ts_ms);
