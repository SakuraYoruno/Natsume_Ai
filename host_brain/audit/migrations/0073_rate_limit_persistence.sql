-- AW-03: catalog-owned durable sliding-window rate-limit state.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS rate_limit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL CHECK(length(trim(key)) BETWEEN 1 AND 512),
    ts_ms INTEGER NOT NULL CHECK(ts_ms >= 0)
);

CREATE INDEX IF NOT EXISTS idx_rate_limit_key_ts
    ON rate_limit_events(key, ts_ms);

COMMIT;
