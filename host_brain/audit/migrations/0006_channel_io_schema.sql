-- Channel I/O audit schema extension for ingress/egress event tracing.

CREATE TABLE IF NOT EXISTS channel_io_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT,
    channel TEXT,
    direction TEXT,
    thread_key TEXT,
    message_id TEXT,
    user_id TEXT,
    status TEXT,
    reason_code TEXT,
    stage TEXT,
    detail TEXT,
    payload_digest TEXT,
    ts_ms INTEGER
);

CREATE INDEX IF NOT EXISTS idx_channel_io_request ON channel_io_log(request_id);
CREATE INDEX IF NOT EXISTS idx_channel_io_thread ON channel_io_log(thread_key);
CREATE INDEX IF NOT EXISTS idx_channel_io_message ON channel_io_log(channel, direction, message_id);
CREATE INDEX IF NOT EXISTS idx_channel_io_ts ON channel_io_log(ts_ms);
