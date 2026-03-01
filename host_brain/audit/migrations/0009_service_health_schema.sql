CREATE TABLE IF NOT EXISTS service_health_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT,
    service_id TEXT,
    owner TEXT,
    required INTEGER,
    status TEXT,
    reason_code TEXT,
    stage TEXT,
    detail TEXT,
    ts_ms INTEGER
);

CREATE INDEX IF NOT EXISTS idx_service_health_request ON service_health_log(request_id);
CREATE INDEX IF NOT EXISTS idx_service_health_service_id ON service_health_log(service_id);
CREATE INDEX IF NOT EXISTS idx_service_health_ts ON service_health_log(ts_ms);
