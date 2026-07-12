-- AW-03 Event-1: one durable delivery spine for Task Runtime events.
--
-- runtime_event_timeline (0056) is the append-only journal.  This migration
-- adds the transactional delivery intent, exactly-once projection inbox, and
-- rebuildable latest-state projection for the first production slice:
-- task_runtime_outbox -> runtime event journal -> task_runtime_latest_v1.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS runtime_event_projection_outbox (
    delivery_id TEXT PRIMARY KEY
        CHECK(length(trim(delivery_id)) BETWEEN 1 AND 512),
    projection_name TEXT NOT NULL
        CHECK(projection_name = 'task_runtime_latest_v1'),
    event_cursor INTEGER NOT NULL,
    event_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK(status IN ('PENDING', 'CLAIMED', 'COMPLETED')),
    available_ts_ms INTEGER NOT NULL CHECK(available_ts_ms >= 0),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
    claim_owner TEXT,
    claim_token TEXT,
    claim_expires_ts_ms INTEGER,
    status_version INTEGER NOT NULL DEFAULT 0 CHECK(status_version >= 0),
    last_error TEXT,
    last_operator_ref TEXT,
    created_ts_ms INTEGER NOT NULL CHECK(created_ts_ms >= 0),
    updated_ts_ms INTEGER NOT NULL CHECK(updated_ts_ms >= 0),
    completed_ts_ms INTEGER,
    UNIQUE(projection_name, event_cursor),
    UNIQUE(projection_name, event_id),
    FOREIGN KEY(event_cursor)
        REFERENCES runtime_event_timeline(cursor) ON DELETE RESTRICT,
    FOREIGN KEY(event_id)
        REFERENCES runtime_event_timeline(event_id) ON DELETE RESTRICT,
    CHECK (
        (
            status = 'CLAIMED'
            AND claim_owner IS NOT NULL
            AND claim_token IS NOT NULL
            AND claim_expires_ts_ms IS NOT NULL
            AND completed_ts_ms IS NULL
        )
        OR
        (
            status = 'PENDING'
            AND claim_owner IS NULL
            AND claim_token IS NULL
            AND claim_expires_ts_ms IS NULL
            AND completed_ts_ms IS NULL
        )
        OR
        (
            status = 'COMPLETED'
            AND claim_owner IS NULL
            AND claim_token IS NULL
            AND claim_expires_ts_ms IS NULL
            AND completed_ts_ms IS NOT NULL
        )
    )
);

CREATE TABLE IF NOT EXISTS runtime_event_projection_inbox (
    projection_name TEXT NOT NULL
        CHECK(projection_name = 'task_runtime_latest_v1'),
    event_cursor INTEGER NOT NULL,
    event_id TEXT NOT NULL,
    delivery_id TEXT NOT NULL UNIQUE,
    payload_digest TEXT NOT NULL
        CHECK(
            length(payload_digest) = 71
            AND substr(payload_digest, 1, 7) = 'sha256:'
            AND substr(payload_digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
    applied_ts_ms INTEGER NOT NULL CHECK(applied_ts_ms >= 0),
    PRIMARY KEY(projection_name, event_cursor),
    UNIQUE(projection_name, event_id),
    FOREIGN KEY(event_cursor)
        REFERENCES runtime_event_timeline(cursor) ON DELETE RESTRICT,
    FOREIGN KEY(event_id)
        REFERENCES runtime_event_timeline(event_id) ON DELETE RESTRICT,
    FOREIGN KEY(delivery_id)
        REFERENCES runtime_event_projection_outbox(delivery_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS runtime_event_task_projection (
    projection_name TEXT NOT NULL
        CHECK(projection_name = 'task_runtime_latest_v1'),
    aggregate_type TEXT NOT NULL CHECK(length(trim(aggregate_type)) > 0),
    aggregate_id TEXT NOT NULL CHECK(length(trim(aggregate_id)) > 0),
    last_event_cursor INTEGER NOT NULL,
    last_event_id TEXT NOT NULL,
    aggregate_version INTEGER NOT NULL CHECK(aggregate_version >= 0),
    event_type TEXT NOT NULL CHECK(length(trim(event_type)) > 0),
    payload_ref TEXT NOT NULL CHECK(length(trim(payload_ref)) > 0),
    state_json TEXT NOT NULL CHECK(length(trim(state_json)) > 0),
    state_digest TEXT NOT NULL
        CHECK(
            length(state_digest) = 71
            AND substr(state_digest, 1, 7) = 'sha256:'
            AND substr(state_digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
    updated_ts_ms INTEGER NOT NULL CHECK(updated_ts_ms >= 0),
    PRIMARY KEY(projection_name, aggregate_type, aggregate_id),
    FOREIGN KEY(last_event_cursor)
        REFERENCES runtime_event_timeline(cursor) ON DELETE RESTRICT,
    FOREIGN KEY(last_event_id)
        REFERENCES runtime_event_timeline(event_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_runtime_event_projection_outbox_ready
    ON runtime_event_projection_outbox(
        projection_name, status, available_ts_ms, event_cursor
    );

CREATE INDEX IF NOT EXISTS idx_runtime_event_projection_outbox_claim_expiry
    ON runtime_event_projection_outbox(
        projection_name, status, claim_expires_ts_ms, event_cursor
    );

CREATE INDEX IF NOT EXISTS idx_runtime_event_projection_inbox_event
    ON runtime_event_projection_inbox(projection_name, event_id);

CREATE INDEX IF NOT EXISTS idx_runtime_event_task_projection_cursor
    ON runtime_event_task_projection(projection_name, last_event_cursor);

-- Backfill any Task Runtime journal rows written before this migration.  The
-- stable cursor-derived id makes migration replay idempotent.
INSERT OR IGNORE INTO runtime_event_projection_outbox(
    delivery_id, projection_name, event_cursor, event_id, status,
    available_ts_ms, attempt_count, status_version,
    created_ts_ms, updated_ts_ms
)
SELECT
    'runtime-event-delivery://task-runtime-latest-v1/' || cursor,
    'task_runtime_latest_v1',
    cursor,
    event_id,
    'PENDING',
    created_ts_ms,
    0,
    0,
    created_ts_ms,
    created_ts_ms
FROM runtime_event_timeline
WHERE source_kind = 'TASK_STORE_OUTBOX';

-- The journal row and its projection intent commit in the same SQLite UoW.
-- Application code has no second writer and cannot forget to enqueue.
CREATE TRIGGER IF NOT EXISTS trg_runtime_event_task_projection_enqueue
AFTER INSERT ON runtime_event_timeline
WHEN NEW.source_kind = 'TASK_STORE_OUTBOX'
BEGIN
    INSERT INTO runtime_event_projection_outbox(
        delivery_id, projection_name, event_cursor, event_id, status,
        available_ts_ms, attempt_count, status_version,
        created_ts_ms, updated_ts_ms
    ) VALUES (
        'runtime-event-delivery://task-runtime-latest-v1/' || NEW.cursor,
        'task_runtime_latest_v1',
        NEW.cursor,
        NEW.event_id,
        'PENDING',
        NEW.created_ts_ms,
        0,
        0,
        NEW.created_ts_ms,
        NEW.created_ts_ms
    );
END;

-- 0056 was intended to be a timeline but did not fence mutation.  From 0068
-- onward it is the canonical append-only event journal.
CREATE TRIGGER IF NOT EXISTS trg_runtime_event_journal_no_update
BEFORE UPDATE ON runtime_event_timeline
BEGIN
    SELECT RAISE(ABORT, 'RUNTIME_EVENT_JOURNAL_IMMUTABLE');
END;

CREATE TRIGGER IF NOT EXISTS trg_runtime_event_journal_no_delete
BEFORE DELETE ON runtime_event_timeline
BEGIN
    SELECT RAISE(ABORT, 'RUNTIME_EVENT_JOURNAL_IMMUTABLE');
END;

COMMIT;
