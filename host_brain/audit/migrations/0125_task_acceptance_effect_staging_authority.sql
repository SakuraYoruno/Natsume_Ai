-- NE-01: seal Acceptance Effect pre-staging behind canonical Acceptance UoWs.
--
-- Existing rows remain readable and are not rewritten. Every future intent
-- insert must execute on a TaskStore connection that registered the local
-- callback and entered the exact effect/graph/node/attempt/scope/idempotency
-- identity while one of the canonical Acceptance transactions is active.

BEGIN IMMEDIATE;

CREATE TRIGGER IF NOT EXISTS trg_task_runtime_acceptance_effect_staging_authority_insert
BEFORE INSERT ON task_runtime_acceptance_effect_intent
BEGIN
    SELECT CASE
        WHEN natsume_task_acceptance_effect_staging_authorized(
            NEW.effect_id,
            NEW.graph_id,
            NEW.graph_revision,
            NEW.node_id,
            NEW.attempt_id,
            NEW.commit_scope,
            NEW.idempotency_key
        ) <> 1 THEN
            RAISE(ABORT, 'TASK_ACCEPTANCE_EFFECT_STAGING_AUTHORITY_REQUIRED')
        WHEN NEW.status <> 'PENDING'
          OR NEW.available_ts_ms <> NEW.created_ts_ms
          OR NEW.updated_ts_ms <> NEW.created_ts_ms
          OR NEW.lease_owner IS NOT NULL
          OR NEW.lease_expires_ts_ms IS NOT NULL
          OR NEW.fencing_token <> 0
          OR NEW.attempt_count <> 0
          OR NEW.status_version <> 0
          OR NEW.receipt_ref IS NOT NULL
          OR NEW.reason_code IS NOT NULL
          OR NEW.last_error IS NOT NULL
          OR NEW.committed_ts_ms IS NOT NULL
          OR NEW.failed_ts_ms IS NOT NULL THEN
            RAISE(ABORT, 'TASK_ACCEPTANCE_EFFECT_INITIAL_STATE_REQUIRED')
    END;
END;

COMMIT;
