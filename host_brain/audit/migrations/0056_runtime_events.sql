-- TASK-0474 / LTE-WP-40: durable runtime event timeline, cursor replay, and dedupe.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS runtime_event_timeline (
    cursor INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE CHECK(length(trim(event_id)) > 0),
    schema_version TEXT NOT NULL CHECK(schema_version = 'Natsume.runtime.event.v1'),
    event_type TEXT NOT NULL CHECK(length(trim(event_type)) > 0),
    source_kind TEXT NOT NULL CHECK(source_kind IN (
        'TASK_STORE_OUTBOX',
        'TRIBE_EVENT',
        'GRAPH_EVIDENCE',
        'PENTEST',
        'BIO_OPS',
        'REVIEW',
        'VERIFICATION',
        'TASK',
        'SYSTEM'
    )),
    source_ref TEXT NOT NULL CHECK(length(trim(source_ref)) > 0),
    aggregate_type TEXT NOT NULL CHECK(length(trim(aggregate_type)) > 0),
    aggregate_id TEXT NOT NULL CHECK(length(trim(aggregate_id)) > 0),
    aggregate_version INTEGER NOT NULL CHECK(aggregate_version >= 0),
    payload_ref TEXT NOT NULL CHECK(length(trim(payload_ref)) > 0),
    payload_json TEXT NOT NULL CHECK(length(trim(payload_json)) > 0),
    payload_digest TEXT NOT NULL CHECK(payload_digest GLOB 'sha256:[0-9a-f]*' AND length(payload_digest) = 71),
    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
    dedupe_key TEXT NOT NULL UNIQUE CHECK(length(trim(dedupe_key)) > 0),
    redaction_level TEXT NOT NULL CHECK(redaction_level IN (
        'PUBLIC',
        'INTERNAL_REDACTED'
    )),
    ts_ms INTEGER NOT NULL CHECK(ts_ms >= 0),
    created_ts_ms INTEGER NOT NULL CHECK(created_ts_ms >= 0),
    published_ts_ms INTEGER NOT NULL CHECK(published_ts_ms >= created_ts_ms)
);

CREATE INDEX IF NOT EXISTS idx_runtime_event_timeline_type_cursor
    ON runtime_event_timeline(event_type, cursor);

CREATE INDEX IF NOT EXISTS idx_runtime_event_timeline_source_cursor
    ON runtime_event_timeline(source_kind, cursor);

CREATE INDEX IF NOT EXISTS idx_runtime_event_timeline_aggregate
    ON runtime_event_timeline(aggregate_type, aggregate_id, aggregate_version);

COMMIT;
