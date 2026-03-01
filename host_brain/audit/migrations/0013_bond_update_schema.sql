CREATE TABLE IF NOT EXISTS bond_update_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT,
    intent_id TEXT,
    session_id TEXT,
    seq INTEGER,
    event_id TEXT,
    user_id TEXT,
    topic_id TEXT,
    capability_hint TEXT,
    update_kind TEXT,
    status TEXT,
    reason_code TEXT,
    stage TEXT,
    evidence_refs TEXT,
    payload TEXT,
    detail TEXT,
    ts_ms INTEGER
);

CREATE INDEX IF NOT EXISTS idx_bond_update_request ON bond_update_log(request_id);
CREATE INDEX IF NOT EXISTS idx_bond_update_intent ON bond_update_log(intent_id);
CREATE INDEX IF NOT EXISTS idx_bond_update_event ON bond_update_log(event_id);
CREATE INDEX IF NOT EXISTS idx_bond_update_user ON bond_update_log(user_id);
CREATE INDEX IF NOT EXISTS idx_bond_update_ts ON bond_update_log(ts_ms);
