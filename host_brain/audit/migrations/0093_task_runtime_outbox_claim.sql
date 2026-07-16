-- NE-01: cross-process fenced claims for the canonical Task Runtime outbox.
--
-- The 0052 status contract cannot be widened without rebuilding a table that
-- is referenced by later decision journals.  A live delivery claim is
-- therefore represented by status='PENDING' plus the complete claim triplet.
-- PUBLISHED/CANCELLED remain terminal and can never retain a claim.

BEGIN IMMEDIATE;

ALTER TABLE task_runtime_outbox ADD COLUMN claim_owner TEXT
    CHECK(claim_owner IS NULL OR typeof(claim_owner) = 'text');
ALTER TABLE task_runtime_outbox ADD COLUMN claim_token TEXT
    CHECK(claim_token IS NULL OR typeof(claim_token) = 'text');
ALTER TABLE task_runtime_outbox ADD COLUMN claim_expires_ts_ms INTEGER
    CHECK(
        claim_expires_ts_ms IS NULL
        OR (
            typeof(claim_expires_ts_ms) = 'integer'
            AND claim_expires_ts_ms >= 0
        )
    );
ALTER TABLE task_runtime_outbox ADD COLUMN claim_fencing_token INTEGER
    NOT NULL DEFAULT 0
    CHECK(
        typeof(claim_fencing_token) = 'integer'
        AND claim_fencing_token >= 0
    );
ALTER TABLE task_runtime_outbox ADD COLUMN attempt_count INTEGER
    NOT NULL DEFAULT 0
    CHECK(
        typeof(attempt_count) = 'integer'
        AND attempt_count >= 0
        AND attempt_count = claim_fencing_token
    );
ALTER TABLE task_runtime_outbox ADD COLUMN last_error TEXT
    CHECK(
        last_error IS NULL
        OR (
            typeof(last_error) = 'text'
            AND length(trim(last_error)) > 0
            AND length(last_error) <= 2000
        )
    );

CREATE INDEX IF NOT EXISTS idx_task_runtime_outbox_delivery_ready
    ON task_runtime_outbox(
        status, available_ts_ms, claim_expires_ts_ms, created_ts_ms, outbox_id
    );

CREATE INDEX IF NOT EXISTS idx_task_runtime_outbox_aggregate_delivery_order
    ON task_runtime_outbox(
        aggregate_type, aggregate_id, status, aggregate_version,
        created_ts_ms, outbox_id
    );

CREATE TRIGGER IF NOT EXISTS trg_task_runtime_outbox_claim_insert_guard
BEFORE INSERT ON task_runtime_outbox
WHEN NOT (
    (
        NEW.status = 'PENDING'
        AND (
            (
                NEW.claim_owner IS NULL
                AND NEW.claim_token IS NULL
                AND NEW.claim_expires_ts_ms IS NULL
            )
            OR
            (
                NEW.claim_owner IS NOT NULL
                AND NEW.claim_token IS NOT NULL
                AND NEW.claim_expires_ts_ms IS NOT NULL
                AND typeof(NEW.claim_owner) = 'text'
                AND typeof(NEW.claim_token) = 'text'
                AND typeof(NEW.claim_expires_ts_ms) = 'integer'
                AND length(trim(NEW.claim_owner)) > 0
                AND length(trim(NEW.claim_token)) > 0
                AND NEW.claim_expires_ts_ms >= 0
                AND typeof(NEW.claim_fencing_token) = 'integer'
                AND typeof(NEW.attempt_count) = 'integer'
                AND NEW.claim_fencing_token > 0
                AND NEW.attempt_count > 0
                AND NEW.claim_fencing_token = NEW.attempt_count
            )
        )
    )
    OR
    (
        NEW.status IN ('PUBLISHED', 'CANCELLED')
        AND NEW.claim_owner IS NULL
        AND NEW.claim_token IS NULL
        AND NEW.claim_expires_ts_ms IS NULL
    )
)
BEGIN
    SELECT RAISE(ABORT, 'task_runtime_outbox claim state is invalid');
END;

CREATE TRIGGER IF NOT EXISTS trg_task_runtime_outbox_claim_update_guard
BEFORE UPDATE OF status, claim_owner, claim_token, claim_expires_ts_ms,
    claim_fencing_token, attempt_count
ON task_runtime_outbox
WHEN NOT (
    (
        NEW.status = 'PENDING'
        AND (
            (
                NEW.claim_owner IS NULL
                AND NEW.claim_token IS NULL
                AND NEW.claim_expires_ts_ms IS NULL
            )
            OR
            (
                NEW.claim_owner IS NOT NULL
                AND NEW.claim_token IS NOT NULL
                AND NEW.claim_expires_ts_ms IS NOT NULL
                AND typeof(NEW.claim_owner) = 'text'
                AND typeof(NEW.claim_token) = 'text'
                AND typeof(NEW.claim_expires_ts_ms) = 'integer'
                AND length(trim(NEW.claim_owner)) > 0
                AND length(trim(NEW.claim_token)) > 0
                AND NEW.claim_expires_ts_ms >= 0
                AND typeof(NEW.claim_fencing_token) = 'integer'
                AND typeof(NEW.attempt_count) = 'integer'
                AND NEW.claim_fencing_token > 0
                AND NEW.attempt_count > 0
                AND NEW.claim_fencing_token = NEW.attempt_count
            )
        )
    )
    OR
    (
        NEW.status IN ('PUBLISHED', 'CANCELLED')
        AND NEW.claim_owner IS NULL
        AND NEW.claim_token IS NULL
        AND NEW.claim_expires_ts_ms IS NULL
    )
)
BEGIN
    SELECT RAISE(ABORT, 'task_runtime_outbox claim state is invalid');
END;

COMMIT;
