CREATE TABLE IF NOT EXISTS retrieval_event_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT,
    intent_id TEXT,
    op_kind TEXT,
    status TEXT,
    reason_code TEXT,
    stage TEXT,
    payload TEXT,
    detail TEXT,
    ts_ms INTEGER
);

CREATE INDEX IF NOT EXISTS idx_retrieval_event_request ON retrieval_event_log(request_id);
CREATE INDEX IF NOT EXISTS idx_retrieval_event_op_kind ON retrieval_event_log(op_kind);
CREATE INDEX IF NOT EXISTS idx_retrieval_event_ts ON retrieval_event_log(ts_ms);
