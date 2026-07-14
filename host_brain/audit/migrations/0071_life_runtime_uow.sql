-- AW-03/AW-04 pre-cutover: single persistence owner for Life runtime writes.
-- Existing Life tables are catalog-owned by 0049/0057; this asset seals each
-- repository transaction with immutable operation provenance.

CREATE TABLE IF NOT EXISTS life_runtime_uow (
    uow_id TEXT PRIMARY KEY CHECK(length(trim(uow_id)) BETWEEN 1 AND 240),
    operation TEXT NOT NULL CHECK(operation IN (
        'STATE_WRITE',
        'STATE_CONFLICT',
        'STATE_EVENT_APPEND',
        'EXPERIENCE_APPEND',
        'ORGAN_PROCESS',
        'ORGAN_RECALIBRATE',
        'SYNTHESIS_APPEND',
        'PROACTIVE_EPISODE_OPEN',
        'PROACTIVE_EPISODE_CLOSE',
        'PROACTIVE_OUTBOX_APPEND',
        'WORK_EVENT_APPEND',
        'DIALOGUE_EVENT_APPEND',
        'EMBODIMENT_EVENT_APPEND'
    )),
    aggregate_id TEXT NOT NULL CHECK(length(trim(aggregate_id)) BETWEEN 1 AND 240),
    command_ref TEXT,
    payload_digest TEXT NOT NULL CHECK(length(payload_digest) = 64),
    expected_version INTEGER,
    resulting_version INTEGER,
    status TEXT NOT NULL CHECK(status IN ('APPLIED', 'CONFLICT')),
    writer_id TEXT NOT NULL DEFAULT 'life_runtime_repository'
        CHECK(writer_id = 'life_runtime_repository'),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0)
);

CREATE INDEX IF NOT EXISTS idx_life_runtime_uow_aggregate
    ON life_runtime_uow(aggregate_id, created_at_ms DESC, uow_id);

CREATE INDEX IF NOT EXISTS idx_life_runtime_uow_operation
    ON life_runtime_uow(operation, created_at_ms DESC, uow_id);

CREATE TRIGGER IF NOT EXISTS life_runtime_uow_no_update
BEFORE UPDATE ON life_runtime_uow
BEGIN
    SELECT RAISE(ABORT, 'life_runtime_uow is immutable');
END;

CREATE TRIGGER IF NOT EXISTS life_runtime_uow_no_delete
BEFORE DELETE ON life_runtime_uow
BEGIN
    SELECT RAISE(ABORT, 'life_runtime_uow is immutable');
END;
