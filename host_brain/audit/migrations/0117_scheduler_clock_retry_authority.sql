-- NE-01: durable Scheduler clock retry/backoff ownership.
--
-- Clock retry state is mutable operational authority.  A lease owner and
-- monotonically increasing fencing token prevent two Host processes from
-- dispatching the same deferred occurrence concurrently.  Terminal Scheduler
-- results remain append-only in the existing 0065/0066 ledgers.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS scheduler_clock_retry_state (
    retry_key TEXT PRIMARY KEY
        CHECK(length(trim(retry_key)) BETWEEN 1 AND 512),
    retry_kind TEXT NOT NULL CHECK(retry_kind IN ('DUE', 'OCCURRENCE')),
    task_id TEXT NOT NULL
        CHECK(length(trim(task_id)) BETWEEN 1 AND 255),
    occurrence_id TEXT
        CHECK(occurrence_id IS NULL OR length(trim(occurrence_id)) BETWEEN 1 AND 512),
    failure_count INTEGER NOT NULL DEFAULT 0 CHECK(failure_count >= 0),
    next_retry_at_ms INTEGER NOT NULL DEFAULT 0 CHECK(next_retry_at_ms >= 0),
    last_reason_code TEXT,
    lease_owner_id TEXT,
    lease_fencing_token INTEGER NOT NULL DEFAULT 0 CHECK(lease_fencing_token >= 0),
    lease_expires_at_ms INTEGER,
    updated_at_ms INTEGER NOT NULL CHECK(updated_at_ms >= 0),
    CHECK(
        (lease_owner_id IS NULL AND lease_expires_at_ms IS NULL)
        OR (
            length(trim(lease_owner_id)) BETWEEN 1 AND 240
            AND lease_expires_at_ms IS NOT NULL
            AND lease_expires_at_ms >= 0
        )
    ),
    CHECK(
        (retry_kind = 'DUE' AND occurrence_id IS NULL)
        OR (
            retry_kind = 'OCCURRENCE'
            AND occurrence_id IS NOT NULL
            AND retry_key = occurrence_id
        )
    )
);

CREATE INDEX IF NOT EXISTS idx_scheduler_clock_retry_due
    ON scheduler_clock_retry_state(next_retry_at_ms, lease_expires_at_ms, retry_key);

CREATE UNIQUE INDEX IF NOT EXISTS uq_scheduler_clock_retry_occurrence
    ON scheduler_clock_retry_state(occurrence_id)
    WHERE retry_kind = 'OCCURRENCE';

COMMIT;
