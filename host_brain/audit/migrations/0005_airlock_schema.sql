-- Airlock schema extension for egress sanitization and ingress quarantine logs.

CREATE TABLE IF NOT EXISTS airlock_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT,
    direction TEXT,
    reason_code TEXT,
    removed_fields TEXT,
    redactions TEXT,
    payload_digest TEXT,
    quarantine_digest TEXT,
    ingress_classification TEXT,
    blocked INTEGER,
    detail TEXT,
    ts_ms INTEGER
);

CREATE INDEX IF NOT EXISTS idx_airlock_request ON airlock_log(request_id);
CREATE INDEX IF NOT EXISTS idx_airlock_direction ON airlock_log(direction);
CREATE INDEX IF NOT EXISTS idx_airlock_ts ON airlock_log(ts_ms);
