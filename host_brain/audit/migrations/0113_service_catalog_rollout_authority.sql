-- NE-03: durable five-stage Service Catalog rollout authority.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS service_catalog_rollout_coordinator_lease (
    runtime_scope_id TEXT NOT NULL PRIMARY KEY
        CHECK(length(trim(runtime_scope_id)) BETWEEN 1 AND 512),
    owner_id TEXT NOT NULL
        CHECK(length(trim(owner_id)) BETWEEN 1 AND 512),
    lease_id TEXT NOT NULL UNIQUE
        CHECK(length(trim(lease_id)) BETWEEN 1 AND 512),
    fencing_token INTEGER NOT NULL
        CHECK(typeof(fencing_token) = 'integer' AND fencing_token >= 1),
    issued_at_ms INTEGER NOT NULL,
    expires_at_ms INTEGER NOT NULL CHECK(expires_at_ms > issued_at_ms),
    released_at_ms INTEGER
);

CREATE TABLE IF NOT EXISTS service_catalog_rollout_operation (
    operation_id TEXT NOT NULL PRIMARY KEY,
    operation_digest TEXT NOT NULL UNIQUE,
    runtime_scope_id TEXT NOT NULL,
    coordinator_owner_id TEXT NOT NULL,
    coordinator_lease_id TEXT NOT NULL,
    coordinator_fencing_token INTEGER NOT NULL,
    catalog_snapshot_digest TEXT NOT NULL,
    candidate_owner_id TEXT NOT NULL,
    stage TEXT NOT NULL
        CHECK(stage IN ('AUDIT', 'SHADOW', 'PREFLIGHT', 'CANARY', 'ENFORCE')),
    stage_rank INTEGER NOT NULL CHECK(
        (stage = 'AUDIT' AND stage_rank = 0)
        OR (stage = 'SHADOW' AND stage_rank = 1)
        OR (stage = 'PREFLIGHT' AND stage_rank = 2)
        OR (stage = 'CANARY' AND stage_rank = 3)
        OR (stage = 'ENFORCE' AND stage_rank = 4)
    ),
    endpoint_ids_json TEXT NOT NULL CHECK(json_valid(endpoint_ids_json)),
    rollback_ref TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL,
    FOREIGN KEY(runtime_scope_id)
        REFERENCES service_catalog_rollout_coordinator_lease(runtime_scope_id)
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS service_catalog_rollout_receipt (
    receipt_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_id TEXT NOT NULL UNIQUE,
    operation_id TEXT NOT NULL,
    event_key TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    status TEXT NOT NULL,
    runtime_scope_id TEXT NOT NULL,
    coordinator_owner_id TEXT NOT NULL,
    coordinator_lease_id TEXT NOT NULL,
    coordinator_fencing_token INTEGER NOT NULL,
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    previous_digest TEXT,
    receipt_digest TEXT NOT NULL UNIQUE,
    created_at_ms INTEGER NOT NULL,
    FOREIGN KEY(operation_id)
        REFERENCES service_catalog_rollout_operation(operation_id)
        ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_service_catalog_rollout_receipt_operation
    ON service_catalog_rollout_receipt(operation_id, receipt_seq);

CREATE TABLE IF NOT EXISTS service_catalog_rollout_head (
    runtime_scope_id TEXT NOT NULL,
    candidate_owner_id TEXT NOT NULL,
    stage TEXT NOT NULL
        CHECK(stage IN ('AUDIT', 'SHADOW', 'PREFLIGHT', 'CANARY', 'ENFORCE')),
    stage_rank INTEGER NOT NULL CHECK(
        (stage = 'AUDIT' AND stage_rank = 0)
        OR (stage = 'SHADOW' AND stage_rank = 1)
        OR (stage = 'PREFLIGHT' AND stage_rank = 2)
        OR (stage = 'CANARY' AND stage_rank = 3)
        OR (stage = 'ENFORCE' AND stage_rank = 4)
    ),
    activation_operation_id TEXT NOT NULL,
    catalog_snapshot_digest TEXT NOT NULL,
    health_identity_digest TEXT NOT NULL,
    activated_at_ms INTEGER NOT NULL,
    PRIMARY KEY(runtime_scope_id, candidate_owner_id),
    FOREIGN KEY(activation_operation_id)
        REFERENCES service_catalog_rollout_operation(operation_id)
        ON DELETE RESTRICT
);

CREATE TRIGGER IF NOT EXISTS service_catalog_rollout_operation_no_update
BEFORE UPDATE ON service_catalog_rollout_operation
BEGIN
    SELECT RAISE(ABORT, 'service catalog rollout operation is immutable');
END;

CREATE TRIGGER IF NOT EXISTS service_catalog_rollout_operation_no_delete
BEFORE DELETE ON service_catalog_rollout_operation
BEGIN
    SELECT RAISE(ABORT, 'service catalog rollout operation is append-only');
END;

CREATE TRIGGER IF NOT EXISTS service_catalog_rollout_receipt_no_update
BEFORE UPDATE ON service_catalog_rollout_receipt
BEGIN
    SELECT RAISE(ABORT, 'service catalog rollout receipt is immutable');
END;

CREATE TRIGGER IF NOT EXISTS service_catalog_rollout_receipt_no_delete
BEFORE DELETE ON service_catalog_rollout_receipt
BEGIN
    SELECT RAISE(ABORT, 'service catalog rollout receipt is append-only');
END;

CREATE TRIGGER IF NOT EXISTS service_catalog_rollout_receipt_state_machine
BEFORE INSERT ON service_catalog_rollout_receipt
BEGIN
    SELECT CASE WHEN NEW.event_key <> NEW.operation_id || ':' || NEW.event_type
        THEN RAISE(ABORT, 'service catalog rollout event key is invalid') END;

    SELECT CASE WHEN NOT (
        (NEW.event_type = 'ROLLOUT_PREPARED' AND NEW.status IN ('PREPARED', 'BLOCKED'))
        OR (NEW.event_type = 'ACTIVATION_STARTING' AND NEW.status = 'STARTING')
        OR (NEW.event_type = 'ACTIVATION_STARTED' AND NEW.status = 'STARTED')
        OR (NEW.event_type = 'ACTIVATION_FAILED' AND NEW.status = 'FAILED')
        OR (NEW.event_type = 'ACTIVATION_ROLLED_BACK' AND NEW.status = 'ROLLED_BACK')
    ) THEN RAISE(ABORT, 'service catalog rollout event/status pair is invalid') END;

    SELECT CASE
        WHEN NEW.event_type = 'ROLLOUT_PREPARED' AND EXISTS (
            SELECT 1 FROM service_catalog_rollout_receipt AS prior
            WHERE prior.operation_id = NEW.operation_id
        ) THEN RAISE(ABORT, 'service catalog rollout receipt transition is invalid')
        WHEN NEW.event_type = 'ACTIVATION_STARTING' AND NOT EXISTS (
            SELECT 1 FROM service_catalog_rollout_receipt AS prior
            WHERE prior.operation_id = NEW.operation_id
              AND prior.receipt_seq = (
                  SELECT MAX(latest.receipt_seq)
                  FROM service_catalog_rollout_receipt AS latest
                  WHERE latest.operation_id = NEW.operation_id
              )
              AND prior.event_type = 'ROLLOUT_PREPARED'
              AND prior.status = 'PREPARED'
        ) THEN RAISE(ABORT, 'service catalog rollout receipt transition is invalid')
        WHEN NEW.event_type IN ('ACTIVATION_STARTED', 'ACTIVATION_ROLLED_BACK')
             AND NOT EXISTS (
                SELECT 1 FROM service_catalog_rollout_receipt AS prior
                WHERE prior.operation_id = NEW.operation_id
                  AND prior.receipt_seq = (
                      SELECT MAX(latest.receipt_seq)
                      FROM service_catalog_rollout_receipt AS latest
                      WHERE latest.operation_id = NEW.operation_id
                  )
                  AND prior.event_type = 'ACTIVATION_STARTING'
                  AND prior.status = 'STARTING'
             ) THEN RAISE(ABORT, 'service catalog rollout receipt transition is invalid')
        WHEN NEW.event_type = 'ACTIVATION_FAILED' AND NOT EXISTS (
            SELECT 1 FROM service_catalog_rollout_receipt AS prior
            WHERE prior.operation_id = NEW.operation_id
              AND prior.receipt_seq = (
                  SELECT MAX(latest.receipt_seq)
                  FROM service_catalog_rollout_receipt AS latest
                  WHERE latest.operation_id = NEW.operation_id
              )
              AND (
                  (prior.event_type = 'ROLLOUT_PREPARED' AND prior.status = 'BLOCKED')
                  OR (prior.event_type = 'ACTIVATION_STARTING' AND prior.status = 'STARTING')
              )
        ) THEN RAISE(ABORT, 'service catalog rollout receipt transition is invalid')
    END;
END;

CREATE TRIGGER IF NOT EXISTS service_catalog_rollout_operation_active_authority
BEFORE INSERT ON service_catalog_rollout_operation
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM service_catalog_rollout_coordinator_lease AS lease
        WHERE lease.runtime_scope_id = NEW.runtime_scope_id
          AND lease.owner_id = NEW.coordinator_owner_id
          AND lease.lease_id = NEW.coordinator_lease_id
          AND lease.fencing_token = NEW.coordinator_fencing_token
          AND lease.released_at_ms IS NULL
          AND lease.issued_at_ms <= NEW.created_at_ms
          AND lease.expires_at_ms > NEW.created_at_ms
    ) THEN RAISE(ABORT, 'service catalog coordinator authority is not active') END;

    SELECT CASE
        WHEN NOT EXISTS (
            SELECT 1 FROM service_catalog_rollout_head AS head
            WHERE head.runtime_scope_id = NEW.runtime_scope_id
              AND head.candidate_owner_id = NEW.candidate_owner_id
        ) AND NEW.stage_rank <> 0
        THEN RAISE(ABORT, 'service catalog rollout must start at AUDIT')
        WHEN EXISTS (
            SELECT 1 FROM service_catalog_rollout_head AS head
            WHERE head.runtime_scope_id = NEW.runtime_scope_id
              AND head.candidate_owner_id = NEW.candidate_owner_id
              AND (NEW.stage_rank < head.stage_rank OR NEW.stage_rank > head.stage_rank + 1)
        )
        THEN RAISE(ABORT, 'service catalog rollout stage regression or skip')
    END;
END;

CREATE TRIGGER IF NOT EXISTS service_catalog_rollout_terminal_host_authority
BEFORE INSERT ON service_catalog_rollout_receipt
WHEN NEW.event_type = 'ACTIVATION_STARTED'
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM service_catalog_rollout_operation AS operation
        WHERE operation.operation_id = NEW.operation_id
          AND json_extract(NEW.payload_json, '$.catalog_snapshot_digest') =
              operation.catalog_snapshot_digest
          AND json_extract(NEW.payload_json, '$.candidate_owner_id') =
              operation.candidate_owner_id
          AND json_extract(NEW.payload_json, '$.stage') = operation.stage
          AND json_extract(NEW.payload_json, '$.rollback_ref') =
              operation.rollback_ref
          AND json_extract(NEW.payload_json, '$.activation_identity.operation_id') =
              operation.operation_id
          AND json_extract(
                NEW.payload_json,
                '$.activation_identity.catalog_snapshot_digest'
              ) = operation.catalog_snapshot_digest
          AND json_extract(
                NEW.payload_json,
                '$.activation_identity.candidate_owner_id'
              ) = operation.candidate_owner_id
          AND json_extract(NEW.payload_json, '$.activation_identity.stage') =
              operation.stage
          AND length(trim(COALESCE(json_extract(
                NEW.payload_json,
                '$.activation_identity.operation_id_digest'
              ), ''))) > 0
          AND json_extract(
                NEW.payload_json,
                '$.runtime_authority.runtime_scope_id'
              ) = operation.runtime_scope_id
          AND EXISTS (
              SELECT 1
              FROM json_each(operation.endpoint_ids_json) AS endpoint
              WHERE endpoint.value = json_extract(
                  NEW.payload_json,
                  '$.health_identity.endpoint_id'
              )
          )
    ) THEN RAISE(ABORT, 'service catalog activation identity drift') END;

    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM host_embodiment_runtime_lease AS lease
        WHERE lease.runtime_scope_id = json_extract(
                NEW.payload_json, '$.runtime_authority.runtime_scope_id')
          AND lease.owner_id = json_extract(
                NEW.payload_json, '$.runtime_authority.runtime_owner_id')
          AND lease.lease_id = json_extract(
                NEW.payload_json, '$.runtime_authority.runtime_lease_id')
          AND lease.fencing_token = CAST(json_extract(
                NEW.payload_json, '$.runtime_authority.fencing_token') AS INTEGER)
          AND lease.released_at_ms IS NULL
          AND lease.issued_at_ms <= NEW.created_at_ms
          AND lease.expires_at_ms > NEW.created_at_ms
    ) THEN RAISE(ABORT, 'service catalog activation Host authority is not active') END;

    SELECT CASE WHEN
        length(trim(COALESCE(json_extract(
            NEW.payload_json, '$.health_identity.endpoint_id'), ''))) = 0
        OR json_extract(NEW.payload_json, '$.health_identity.owner_id') <>
            json_extract(NEW.payload_json, '$.candidate_owner_id')
        OR length(trim(COALESCE(json_extract(
            NEW.payload_json, '$.health_identity.protocol_id'), ''))) = 0
        OR length(trim(COALESCE(json_extract(
            NEW.payload_json, '$.health_identity.protocol_version'), ''))) = 0
    THEN RAISE(ABORT, 'service catalog health identity is incomplete') END;
END;

CREATE TRIGGER IF NOT EXISTS service_catalog_rollout_receipt_active_coordinator
BEFORE INSERT ON service_catalog_rollout_receipt
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM service_catalog_rollout_operation AS operation
        JOIN service_catalog_rollout_coordinator_lease AS lease
          ON lease.runtime_scope_id = NEW.runtime_scope_id
         AND lease.owner_id = NEW.coordinator_owner_id
         AND lease.lease_id = NEW.coordinator_lease_id
         AND lease.fencing_token = NEW.coordinator_fencing_token
        WHERE operation.operation_id = NEW.operation_id
          AND operation.runtime_scope_id = NEW.runtime_scope_id
          AND lease.released_at_ms IS NULL
          AND lease.issued_at_ms <= NEW.created_at_ms
          AND lease.expires_at_ms > NEW.created_at_ms
    ) THEN RAISE(ABORT, 'service catalog receipt coordinator authority is not active') END;
END;

COMMIT;
