-- NE-04: trusted isolated Evolution Lab operation and evidence authority.
--
-- 0108 and 0122 remain immutable historical/self-attested stores.  This
-- migration adds a separate operation/event owner for concrete Docker
-- attestations and proposal-only reports.  It never upgrades older evidence.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS evolution_lab_trusted_operation_head (
    operation_id TEXT NOT NULL PRIMARY KEY CHECK(length(trim(operation_id)) BETWEEN 1 AND 160),
    execution_id TEXT NOT NULL UNIQUE CHECK(length(trim(execution_id)) BETWEEN 1 AND 160),
    spec_digest TEXT NOT NULL REFERENCES evolution_lab_v2_experiment_spec(spec_digest) ON DELETE RESTRICT,
    workspace_id TEXT NOT NULL CHECK(length(trim(workspace_id)) BETWEEN 1 AND 160),
    dataset_manifest_digest TEXT NOT NULL CHECK(length(dataset_manifest_digest) = 64),
    variant_id TEXT NOT NULL CHECK(length(trim(variant_id)) BETWEEN 1 AND 160),
    status TEXT NOT NULL CHECK(status IN (
        'RESERVED',
        'RUNNING',
        'COMPLETED',
        'FAILED',
        'RECONCILIATION_REQUIRED'
    )),
    owner_id TEXT NOT NULL CHECK(length(trim(owner_id)) BETWEEN 1 AND 160),
    fencing_token INTEGER NOT NULL CHECK(typeof(fencing_token) = 'integer' AND fencing_token > 0),
    lease_expires_at_ms INTEGER NOT NULL CHECK(
        typeof(lease_expires_at_ms) = 'integer' AND lease_expires_at_ms >= 0
    ),
    container_name TEXT NOT NULL CHECK(length(trim(container_name)) BETWEEN 1 AND 160),
    receipt_digest TEXT CHECK(receipt_digest IS NULL OR length(receipt_digest) = 64),
    version INTEGER NOT NULL CHECK(typeof(version) = 'integer' AND version > 0),
    current_event_sequence INTEGER NOT NULL CHECK(
        typeof(current_event_sequence) = 'integer' AND current_event_sequence > 0
    ),
    current_event_digest TEXT NOT NULL CHECK(length(current_event_digest) = 64),
    created_at_ms INTEGER NOT NULL CHECK(typeof(created_at_ms) = 'integer' AND created_at_ms >= 0),
    updated_at_ms INTEGER NOT NULL CHECK(
        typeof(updated_at_ms) = 'integer' AND updated_at_ms >= created_at_ms
    )
);

CREATE INDEX IF NOT EXISTS idx_evolution_lab_trusted_operation_status
    ON evolution_lab_trusted_operation_head(status, lease_expires_at_ms, operation_id);

CREATE TABLE IF NOT EXISTS evolution_lab_trusted_operation_event (
    event_id TEXT NOT NULL PRIMARY KEY CHECK(length(trim(event_id)) BETWEEN 1 AND 200),
    operation_id TEXT NOT NULL REFERENCES evolution_lab_trusted_operation_head(operation_id) ON DELETE RESTRICT,
    event_sequence INTEGER NOT NULL CHECK(typeof(event_sequence) = 'integer' AND event_sequence > 0),
    event_type TEXT NOT NULL CHECK(event_type IN (
        'RESERVED',
        'RUNNING',
        'COMPLETED',
        'FAILED',
        'RECONCILIATION_REQUIRED'
    )),
    payload_json TEXT NOT NULL CHECK(
        json_valid(payload_json) AND length(payload_json) BETWEEN 2 AND 524288
    ),
    payload_digest TEXT NOT NULL CHECK(length(payload_digest) = 64),
    previous_event_digest TEXT CHECK(
        previous_event_digest IS NULL OR length(previous_event_digest) = 64
    ),
    event_digest TEXT NOT NULL UNIQUE CHECK(length(event_digest) = 64),
    created_at_ms INTEGER NOT NULL CHECK(typeof(created_at_ms) = 'integer' AND created_at_ms >= 0),
    UNIQUE(operation_id, event_sequence)
);

CREATE INDEX IF NOT EXISTS idx_evolution_lab_trusted_event_operation
    ON evolution_lab_trusted_operation_event(operation_id, event_sequence);

CREATE TABLE IF NOT EXISTS evolution_lab_trusted_run_receipt (
    run_id TEXT NOT NULL PRIMARY KEY CHECK(length(trim(run_id)) BETWEEN 1 AND 160),
    operation_id TEXT NOT NULL UNIQUE REFERENCES evolution_lab_trusted_operation_head(operation_id) ON DELETE RESTRICT,
    execution_id TEXT NOT NULL UNIQUE CHECK(length(trim(execution_id)) BETWEEN 1 AND 160),
    spec_digest TEXT NOT NULL REFERENCES evolution_lab_v2_experiment_spec(spec_digest) ON DELETE RESTRICT,
    workspace_id TEXT NOT NULL CHECK(length(trim(workspace_id)) BETWEEN 1 AND 160),
    dataset_manifest_digest TEXT NOT NULL CHECK(length(dataset_manifest_digest) = 64),
    variant_id TEXT NOT NULL CHECK(length(trim(variant_id)) BETWEEN 1 AND 160),
    receipt_digest TEXT NOT NULL UNIQUE CHECK(length(receipt_digest) = 64),
    sandbox_attestation_digest TEXT NOT NULL UNIQUE CHECK(length(sandbox_attestation_digest) = 64),
    verification_receipt_digest TEXT NOT NULL UNIQUE CHECK(length(verification_receipt_digest) = 64),
    effect_audit_receipt_digest TEXT NOT NULL CHECK(length(effect_audit_receipt_digest) = 64),
    receipt_json TEXT NOT NULL CHECK(
        json_valid(receipt_json) AND length(receipt_json) BETWEEN 2 AND 2097152
    ),
    production_side_effects INTEGER NOT NULL CHECK(production_side_effects = 0),
    benefit_eligible INTEGER NOT NULL CHECK(benefit_eligible = 1),
    production_influence_enabled INTEGER NOT NULL CHECK(production_influence_enabled = 0),
    created_at_ms INTEGER NOT NULL CHECK(typeof(created_at_ms) = 'integer' AND created_at_ms >= 0)
);

CREATE INDEX IF NOT EXISTS idx_evolution_lab_trusted_run_spec
    ON evolution_lab_trusted_run_receipt(spec_digest, variant_id, created_at_ms, run_id);

CREATE TABLE IF NOT EXISTS evolution_lab_trusted_report (
    report_id TEXT NOT NULL PRIMARY KEY CHECK(length(trim(report_id)) BETWEEN 1 AND 160),
    experiment_kind TEXT NOT NULL CHECK(length(trim(experiment_kind)) BETWEEN 1 AND 120),
    report_digest TEXT NOT NULL UNIQUE CHECK(length(report_digest) = 64),
    spec_digest TEXT NOT NULL REFERENCES evolution_lab_v2_experiment_spec(spec_digest) ON DELETE RESTRICT,
    workspace_id TEXT NOT NULL CHECK(length(trim(workspace_id)) BETWEEN 1 AND 160),
    report_json TEXT NOT NULL CHECK(
        json_valid(report_json) AND length(report_json) BETWEEN 2 AND 4194304
    ),
    promotion_eligible INTEGER NOT NULL CHECK(promotion_eligible = 1),
    review_required INTEGER NOT NULL CHECK(review_required = 1),
    production_influence_enabled INTEGER NOT NULL CHECK(production_influence_enabled = 0),
    created_at_ms INTEGER NOT NULL CHECK(typeof(created_at_ms) = 'integer' AND created_at_ms >= 0)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_evolution_lab_trusted_report_kind_digest
    ON evolution_lab_trusted_report(experiment_kind, report_digest);

CREATE TABLE IF NOT EXISTS evolution_lab_trusted_proposal (
    proposal_id TEXT NOT NULL PRIMARY KEY CHECK(length(trim(proposal_id)) BETWEEN 1 AND 160),
    proposal_digest TEXT NOT NULL UNIQUE CHECK(length(proposal_digest) = 64),
    report_digest TEXT NOT NULL REFERENCES evolution_lab_trusted_report(report_digest) ON DELETE RESTRICT,
    target_kind TEXT NOT NULL CHECK(length(trim(target_kind)) BETWEEN 1 AND 160),
    target_version TEXT NOT NULL CHECK(length(trim(target_version)) BETWEEN 1 AND 160),
    proposal_json TEXT NOT NULL CHECK(
        json_valid(proposal_json) AND length(proposal_json) BETWEEN 2 AND 524288
    ),
    review_required INTEGER NOT NULL CHECK(review_required = 1),
    production_influence_enabled INTEGER NOT NULL CHECK(production_influence_enabled = 0),
    created_at_ms INTEGER NOT NULL CHECK(typeof(created_at_ms) = 'integer' AND created_at_ms >= 0)
);

CREATE TABLE IF NOT EXISTS evolution_lab_redaction_receipt (
    redaction_id TEXT NOT NULL PRIMARY KEY CHECK(length(trim(redaction_id)) BETWEEN 1 AND 160),
    workspace_id TEXT NOT NULL CHECK(length(trim(workspace_id)) BETWEEN 1 AND 160),
    source_digest TEXT NOT NULL CHECK(length(source_digest) = 64),
    output_dataset_digest TEXT NOT NULL CHECK(length(output_dataset_digest) = 64),
    receipt_digest TEXT NOT NULL UNIQUE CHECK(length(receipt_digest) = 64),
    receipt_json TEXT NOT NULL CHECK(
        json_valid(receipt_json) AND length(receipt_json) BETWEEN 2 AND 524288
    ),
    created_at_ms INTEGER NOT NULL CHECK(typeof(created_at_ms) = 'integer' AND created_at_ms >= 0),
    UNIQUE(workspace_id, source_digest, output_dataset_digest)
);

CREATE TABLE IF NOT EXISTS evolution_lab_golden_suite_receipt (
    suite_id TEXT NOT NULL PRIMARY KEY CHECK(length(trim(suite_id)) BETWEEN 1 AND 160),
    commit_sha TEXT NOT NULL CHECK(length(commit_sha) = 40),
    migration_head TEXT NOT NULL CHECK(length(trim(migration_head)) BETWEEN 1 AND 200),
    receipt_digest TEXT NOT NULL UNIQUE CHECK(length(receipt_digest) = 64),
    receipt_json TEXT NOT NULL CHECK(
        json_valid(receipt_json) AND length(receipt_json) BETWEEN 2 AND 2097152
    ),
    status TEXT NOT NULL CHECK(status = 'PASS'),
    production_side_effects INTEGER NOT NULL CHECK(production_side_effects = 0),
    production_influence_enabled INTEGER NOT NULL CHECK(production_influence_enabled = 0),
    created_at_ms INTEGER NOT NULL CHECK(typeof(created_at_ms) = 'integer' AND created_at_ms >= 0)
);

CREATE TRIGGER IF NOT EXISTS evolution_lab_trusted_head_insert_guard
BEFORE INSERT ON evolution_lab_trusted_operation_head
WHEN natsume_evolution_lab_write_authorized() <> 1
BEGIN SELECT RAISE(ABORT, 'Evolution Lab trusted head insert requires repository authority'); END;

CREATE TRIGGER IF NOT EXISTS evolution_lab_trusted_head_update_guard
BEFORE UPDATE ON evolution_lab_trusted_operation_head
WHEN natsume_evolution_lab_write_authorized() <> 1
BEGIN SELECT RAISE(ABORT, 'Evolution Lab trusted head update requires repository authority'); END;

CREATE TRIGGER IF NOT EXISTS evolution_lab_trusted_head_no_delete
BEFORE DELETE ON evolution_lab_trusted_operation_head
BEGIN SELECT RAISE(ABORT, 'Evolution Lab trusted operation head cannot be deleted'); END;

CREATE TRIGGER IF NOT EXISTS evolution_lab_trusted_event_insert_guard
BEFORE INSERT ON evolution_lab_trusted_operation_event
WHEN natsume_evolution_lab_write_authorized() <> 1
BEGIN SELECT RAISE(ABORT, 'Evolution Lab trusted event insert requires repository authority'); END;

CREATE TRIGGER IF NOT EXISTS evolution_lab_trusted_event_chain_guard
BEFORE INSERT ON evolution_lab_trusted_operation_event
WHEN
    NEW.event_sequence <> (
        SELECT COALESCE(MAX(event_sequence), 0) + 1
        FROM evolution_lab_trusted_operation_event
        WHERE operation_id = NEW.operation_id
    )
    OR (
        NEW.event_sequence = 1
        AND NEW.previous_event_digest IS NOT NULL
    )
    OR (
        NEW.event_sequence > 1
        AND NEW.previous_event_digest <> (
            SELECT event_digest
            FROM evolution_lab_trusted_operation_event
            WHERE operation_id = NEW.operation_id
            ORDER BY event_sequence DESC
            LIMIT 1
        )
    )
    OR NEW.payload_digest <> natsume_sha256_hex(NEW.payload_json)
    OR NEW.event_digest <> natsume_sha256_hex(
        NEW.operation_id || '|' ||
        CAST(NEW.event_sequence AS TEXT) || '|' ||
        NEW.event_type || '|' ||
        NEW.payload_digest || '|' ||
        COALESCE(NEW.previous_event_digest, '')
    )
BEGIN SELECT RAISE(ABORT, 'Evolution Lab trusted event chain is invalid'); END;

CREATE TRIGGER IF NOT EXISTS evolution_lab_trusted_event_no_update
BEFORE UPDATE ON evolution_lab_trusted_operation_event
BEGIN SELECT RAISE(ABORT, 'Evolution Lab trusted operation event is immutable'); END;

CREATE TRIGGER IF NOT EXISTS evolution_lab_trusted_event_no_delete
BEFORE DELETE ON evolution_lab_trusted_operation_event
BEGIN SELECT RAISE(ABORT, 'Evolution Lab trusted operation event is append-only'); END;

CREATE TRIGGER IF NOT EXISTS evolution_lab_trusted_head_matches_event
BEFORE UPDATE ON evolution_lab_trusted_operation_head
WHEN NOT EXISTS (
    SELECT 1 FROM evolution_lab_trusted_operation_event AS event
    WHERE event.operation_id = NEW.operation_id
      AND event.event_sequence = NEW.current_event_sequence
      AND event.event_digest = NEW.current_event_digest
      AND event.event_type = NEW.status
)
BEGIN SELECT RAISE(ABORT, 'Evolution Lab trusted head must bind the latest event'); END;

CREATE TRIGGER IF NOT EXISTS evolution_lab_trusted_run_insert_guard
BEFORE INSERT ON evolution_lab_trusted_run_receipt
WHEN natsume_evolution_lab_write_authorized() <> 1
BEGIN SELECT RAISE(ABORT, 'Evolution Lab trusted run insert requires repository authority'); END;

CREATE TRIGGER IF NOT EXISTS evolution_lab_trusted_run_operation_bound
BEFORE INSERT ON evolution_lab_trusted_run_receipt
WHEN NOT EXISTS (
    SELECT 1 FROM evolution_lab_trusted_operation_head AS operation
    WHERE operation.operation_id = NEW.operation_id
      AND operation.execution_id = NEW.execution_id
      AND operation.spec_digest = NEW.spec_digest
      AND operation.workspace_id = NEW.workspace_id
      AND operation.dataset_manifest_digest = NEW.dataset_manifest_digest
      AND operation.variant_id = NEW.variant_id
      AND operation.status = 'RUNNING'
)
BEGIN SELECT RAISE(ABORT, 'Evolution Lab trusted run does not bind a running operation'); END;

CREATE TRIGGER IF NOT EXISTS evolution_lab_trusted_run_no_update
BEFORE UPDATE ON evolution_lab_trusted_run_receipt
BEGIN SELECT RAISE(ABORT, 'Evolution Lab trusted run receipt is immutable'); END;

CREATE TRIGGER IF NOT EXISTS evolution_lab_trusted_run_no_delete
BEFORE DELETE ON evolution_lab_trusted_run_receipt
BEGIN SELECT RAISE(ABORT, 'Evolution Lab trusted run receipt is append-only'); END;

CREATE TRIGGER IF NOT EXISTS evolution_lab_trusted_report_insert_guard
BEFORE INSERT ON evolution_lab_trusted_report
WHEN natsume_evolution_lab_write_authorized() <> 1
BEGIN SELECT RAISE(ABORT, 'Evolution Lab trusted report insert requires repository authority'); END;

CREATE TRIGGER IF NOT EXISTS evolution_lab_trusted_report_no_update
BEFORE UPDATE ON evolution_lab_trusted_report
BEGIN SELECT RAISE(ABORT, 'Evolution Lab trusted report is immutable'); END;

CREATE TRIGGER IF NOT EXISTS evolution_lab_trusted_report_no_delete
BEFORE DELETE ON evolution_lab_trusted_report
BEGIN SELECT RAISE(ABORT, 'Evolution Lab trusted report is append-only'); END;

CREATE TRIGGER IF NOT EXISTS evolution_lab_trusted_proposal_insert_guard
BEFORE INSERT ON evolution_lab_trusted_proposal
WHEN natsume_evolution_lab_write_authorized() <> 1
BEGIN SELECT RAISE(ABORT, 'Evolution Lab trusted proposal insert requires repository authority'); END;

CREATE TRIGGER IF NOT EXISTS evolution_lab_trusted_proposal_no_update
BEFORE UPDATE ON evolution_lab_trusted_proposal
BEGIN SELECT RAISE(ABORT, 'Evolution Lab trusted proposal is immutable'); END;

CREATE TRIGGER IF NOT EXISTS evolution_lab_trusted_proposal_no_delete
BEFORE DELETE ON evolution_lab_trusted_proposal
BEGIN SELECT RAISE(ABORT, 'Evolution Lab trusted proposal is append-only'); END;

CREATE TRIGGER IF NOT EXISTS evolution_lab_redaction_insert_guard
BEFORE INSERT ON evolution_lab_redaction_receipt
WHEN natsume_evolution_lab_write_authorized() <> 1
BEGIN SELECT RAISE(ABORT, 'Evolution Lab redaction insert requires repository authority'); END;

CREATE TRIGGER IF NOT EXISTS evolution_lab_redaction_no_update
BEFORE UPDATE ON evolution_lab_redaction_receipt
BEGIN SELECT RAISE(ABORT, 'Evolution Lab redaction receipt is immutable'); END;

CREATE TRIGGER IF NOT EXISTS evolution_lab_redaction_no_delete
BEFORE DELETE ON evolution_lab_redaction_receipt
BEGIN SELECT RAISE(ABORT, 'Evolution Lab redaction receipt is append-only'); END;

CREATE TRIGGER IF NOT EXISTS evolution_lab_golden_suite_insert_guard
BEFORE INSERT ON evolution_lab_golden_suite_receipt
WHEN natsume_evolution_lab_write_authorized() <> 1
BEGIN SELECT RAISE(ABORT, 'Evolution Lab golden suite insert requires repository authority'); END;

CREATE TRIGGER IF NOT EXISTS evolution_lab_golden_suite_no_update
BEFORE UPDATE ON evolution_lab_golden_suite_receipt
BEGIN SELECT RAISE(ABORT, 'Evolution Lab golden suite receipt is immutable'); END;

CREATE TRIGGER IF NOT EXISTS evolution_lab_golden_suite_no_delete
BEFORE DELETE ON evolution_lab_golden_suite_receipt
BEGIN SELECT RAISE(ABORT, 'Evolution Lab golden suite receipt is append-only'); END;

COMMIT;
