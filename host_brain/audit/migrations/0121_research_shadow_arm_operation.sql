-- D20c: durable, append-only recovery journal for one paired Research arm.
--
-- The 0107 ledger records immutable evidence after an arm has completed.  It
-- cannot distinguish a process that has never called a provider from one that
-- died between a canonical model attempt and evidence persistence.  This
-- sidecar reserves a deterministic execution identity, fences concurrent
-- callers, and records the resulting recovery disposition.  It remains shadow
-- experiment evidence only and never grants production influence.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS planning_search_research_arm_operation (
    operation_id TEXT NOT NULL PRIMARY KEY
        CHECK(length(trim(operation_id)) BETWEEN 1 AND 255),
    operation_digest TEXT NOT NULL UNIQUE
        CHECK(length(operation_digest) = 64),
    run_id TEXT NOT NULL
        REFERENCES planning_search_research_run_spec(run_id) ON DELETE RESTRICT,
    arm_role TEXT NOT NULL CHECK(arm_role IN ('BASELINE', 'SELECTED')),
    arm_id TEXT NOT NULL,
    execution_id TEXT NOT NULL UNIQUE
        CHECK(length(trim(execution_id)) BETWEEN 1 AND 160),
    operation_json TEXT NOT NULL
        CHECK(json_valid(operation_json) AND length(operation_json) BETWEEN 2 AND 262144),
    production_influence_enabled INTEGER NOT NULL
        CHECK(production_influence_enabled = 0),
    created_at_ms INTEGER NOT NULL
        CHECK(typeof(created_at_ms) = 'integer' AND created_at_ms >= 0),
    UNIQUE(run_id, arm_role),
    UNIQUE(run_id, arm_id)
);

CREATE INDEX IF NOT EXISTS idx_research_arm_operation_run
    ON planning_search_research_arm_operation(run_id, created_at_ms, operation_id);

CREATE TABLE IF NOT EXISTS planning_search_research_arm_operation_event (
    event_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE
        CHECK(length(trim(event_id)) BETWEEN 1 AND 320),
    operation_id TEXT NOT NULL
        REFERENCES planning_search_research_arm_operation(operation_id) ON DELETE RESTRICT,
    event_kind TEXT NOT NULL CHECK(event_kind IN (
        'RESERVED',
        'CALL_CLAIMED',
        'EXTERNAL_CALL_STARTED',
        'RETRY_AUTHORIZED_NO_AUDIT',
        'EVIDENCE_PERSISTED',
        'RECONCILIATION_REQUIRED'
    )),
    event_digest TEXT NOT NULL UNIQUE
        CHECK(length(event_digest) = 64),
    detail_json TEXT NOT NULL
        CHECK(json_valid(detail_json) AND length(detail_json) BETWEEN 2 AND 131072),
    created_at_ms INTEGER NOT NULL
        CHECK(typeof(created_at_ms) = 'integer' AND created_at_ms >= 0)
);

CREATE INDEX IF NOT EXISTS idx_research_arm_operation_event_operation
    ON planning_search_research_arm_operation_event(operation_id, event_sequence);

CREATE TRIGGER IF NOT EXISTS research_arm_operation_run_bound
BEFORE INSERT ON planning_search_research_arm_operation
WHEN NOT EXISTS (
    SELECT 1
    FROM planning_search_research_run_spec AS run
    WHERE run.run_id = NEW.run_id
      AND (
        (NEW.arm_role = 'BASELINE' AND NEW.arm_id = run.baseline_arm_id)
        OR (NEW.arm_role = 'SELECTED' AND NEW.arm_id = run.selected_arm_id)
      )
)
BEGIN
    SELECT RAISE(ABORT, 'research arm operation does not bind preregistered arm');
END;

CREATE TRIGGER IF NOT EXISTS research_arm_operation_event_chain
BEFORE INSERT ON planning_search_research_arm_operation_event
BEGIN
    SELECT CASE WHEN NEW.event_kind = 'RESERVED' AND EXISTS (
        SELECT 1
        FROM planning_search_research_arm_operation_event AS prior
        WHERE prior.operation_id = NEW.operation_id
    ) THEN RAISE(ABORT, 'research arm operation reservation is not first') END;

    SELECT CASE WHEN NEW.event_kind <> 'RESERVED' AND NOT EXISTS (
        SELECT 1
        FROM planning_search_research_arm_operation_event AS prior
        WHERE prior.operation_id = NEW.operation_id
    ) THEN RAISE(ABORT, 'research arm operation event lacks reservation') END;

    SELECT CASE WHEN NEW.event_kind = 'CALL_CLAIMED' AND NOT EXISTS (
        SELECT 1
        FROM planning_search_research_arm_operation_event AS prior
        WHERE prior.operation_id = NEW.operation_id
          AND prior.event_sequence = (
            SELECT MAX(latest.event_sequence)
            FROM planning_search_research_arm_operation_event AS latest
            WHERE latest.operation_id = NEW.operation_id
          )
          AND (
            prior.event_kind IN ('RESERVED', 'RETRY_AUTHORIZED_NO_AUDIT')
            OR (
                prior.event_kind = 'CALL_CLAIMED'
                AND CAST(json_extract(prior.detail_json, '$.expires_at_ms') AS INTEGER)
                    <= NEW.created_at_ms
            )
          )
    ) THEN RAISE(ABORT, 'research arm operation claim transition is invalid') END;

    SELECT CASE WHEN NEW.event_kind = 'EXTERNAL_CALL_STARTED' AND NOT EXISTS (
        SELECT 1
        FROM planning_search_research_arm_operation_event AS prior
        WHERE prior.operation_id = NEW.operation_id
          AND prior.event_sequence = (
            SELECT MAX(latest.event_sequence)
            FROM planning_search_research_arm_operation_event AS latest
            WHERE latest.operation_id = NEW.operation_id
          )
          AND prior.event_kind = 'CALL_CLAIMED'
          AND CAST(json_extract(prior.detail_json, '$.expires_at_ms') AS INTEGER)
              > NEW.created_at_ms
    ) THEN RAISE(ABORT, 'research arm external call lacks active claim') END;

    SELECT CASE WHEN NEW.event_kind IN ('RETRY_AUTHORIZED_NO_AUDIT', 'RECONCILIATION_REQUIRED') AND NOT EXISTS (
        SELECT 1
        FROM planning_search_research_arm_operation_event AS prior
        WHERE prior.operation_id = NEW.operation_id
          AND prior.event_sequence = (
            SELECT MAX(latest.event_sequence)
            FROM planning_search_research_arm_operation_event AS latest
            WHERE latest.operation_id = NEW.operation_id
          )
          AND prior.event_kind = 'EXTERNAL_CALL_STARTED'
    ) THEN RAISE(ABORT, 'research arm reconciliation transition is invalid') END;

    SELECT CASE WHEN NEW.event_kind = 'EVIDENCE_PERSISTED' AND NOT EXISTS (
        SELECT 1
        FROM planning_search_research_arm_operation_event AS prior
        WHERE prior.operation_id = NEW.operation_id
          AND prior.event_sequence = (
            SELECT MAX(latest.event_sequence)
            FROM planning_search_research_arm_operation_event AS latest
            WHERE latest.operation_id = NEW.operation_id
          )
          AND prior.event_kind = 'EXTERNAL_CALL_STARTED'
    ) THEN RAISE(ABORT, 'research arm evidence transition is invalid') END;

    SELECT CASE WHEN NEW.event_kind = 'EVIDENCE_PERSISTED' AND NOT EXISTS (
        SELECT 1
        FROM planning_search_research_arm_operation AS operation
        JOIN planning_search_research_arm_execution AS arm
          ON arm.run_id = operation.run_id
         AND arm.arm_role = operation.arm_role
         AND arm.arm_id = operation.arm_id
        WHERE operation.operation_id = NEW.operation_id
    ) THEN RAISE(ABORT, 'research arm evidence event lacks evidence row') END;
END;

CREATE TRIGGER IF NOT EXISTS research_arm_operation_no_update
BEFORE UPDATE ON planning_search_research_arm_operation
BEGIN SELECT RAISE(ABORT, 'research arm operation is immutable'); END;
CREATE TRIGGER IF NOT EXISTS research_arm_operation_no_delete
BEFORE DELETE ON planning_search_research_arm_operation
BEGIN SELECT RAISE(ABORT, 'research arm operation is append-only'); END;
CREATE TRIGGER IF NOT EXISTS research_arm_operation_event_no_update
BEFORE UPDATE ON planning_search_research_arm_operation_event
BEGIN SELECT RAISE(ABORT, 'research arm operation event is immutable'); END;
CREATE TRIGGER IF NOT EXISTS research_arm_operation_event_no_delete
BEFORE DELETE ON planning_search_research_arm_operation_event
BEGIN SELECT RAISE(ABORT, 'research arm operation event is append-only'); END;

COMMIT;
