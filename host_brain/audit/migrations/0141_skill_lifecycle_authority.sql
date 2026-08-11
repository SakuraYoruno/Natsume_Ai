-- NE-05: authoritative Skill lifecycle, canary and active-version projection.
-- Expand-only.  0060/0109 remain readable compatibility assets and are not
-- rewritten or promoted to V2 authority.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS skill_lifecycle_canary_operation_v2 (
    operation_id TEXT PRIMARY KEY CHECK(length(trim(operation_id)) BETWEEN 1 AND 255),
    idempotency_digest TEXT NOT NULL UNIQUE CHECK(length(idempotency_digest) = 64),
    request_digest TEXT NOT NULL CHECK(length(request_digest) = 64),
    workspace_id TEXT NOT NULL CHECK(length(trim(workspace_id)) BETWEEN 1 AND 128),
    bundle_id TEXT NOT NULL REFERENCES skill_supply_chain_bundle(bundle_id) ON DELETE RESTRICT,
    bundle_digest TEXT NOT NULL CHECK(length(bundle_digest) = 71),
    runtime_adapter_id TEXT NOT NULL CHECK(length(trim(runtime_adapter_id)) BETWEEN 2 AND 128),
    runtime_protocol_version TEXT NOT NULL CHECK(length(trim(runtime_protocol_version)) BETWEEN 2 AND 128),
    sandbox_attestation_digest TEXT NOT NULL CHECK(length(sandbox_attestation_digest) = 64),
    outbox_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK(status IN (
        'PENDING', 'PASSED', 'FAILED', 'RECONCILIATION_REQUIRED'
    )),
    downstream_receipt_digest TEXT CHECK(
        downstream_receipt_digest IS NULL OR length(downstream_receipt_digest) = 64
    ),
    result_digest TEXT CHECK(result_digest IS NULL OR length(result_digest) = 64),
    reason_code TEXT NOT NULL CHECK(length(trim(reason_code)) BETWEEN 3 AND 128),
    request_json TEXT NOT NULL CHECK(json_valid(request_json)),
    result_json TEXT CHECK(result_json IS NULL OR json_valid(result_json)),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
    updated_at_ms INTEGER NOT NULL CHECK(updated_at_ms >= created_at_ms),
    terminal_at_ms INTEGER,
    CHECK(
        (status = 'PENDING' AND downstream_receipt_digest IS NULL
            AND result_digest IS NULL AND result_json IS NULL
            AND terminal_at_ms IS NULL)
        OR
        (status IN ('PASSED', 'FAILED', 'RECONCILIATION_REQUIRED')
            AND downstream_receipt_digest IS NOT NULL
            AND result_digest IS NOT NULL AND result_json IS NOT NULL
            AND terminal_at_ms IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS skill_lifecycle_outbox_v2 (
    outbox_id TEXT PRIMARY KEY CHECK(length(trim(outbox_id)) BETWEEN 1 AND 255),
    operation_id TEXT NOT NULL UNIQUE
        REFERENCES skill_lifecycle_canary_operation_v2(operation_id) ON DELETE RESTRICT,
    destination_adapter_id TEXT NOT NULL
        CHECK(length(trim(destination_adapter_id)) BETWEEN 2 AND 128),
    payload_digest TEXT NOT NULL CHECK(length(payload_digest) = 64),
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    state TEXT NOT NULL CHECK(state IN (
        'PENDING', 'CLAIMED', 'ACKNOWLEDGED', 'UNKNOWN', 'FAILED'
    )),
    lease_owner TEXT,
    lease_expires_at_ms INTEGER,
    fencing_token INTEGER NOT NULL DEFAULT 0 CHECK(fencing_token >= 0),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
    downstream_receipt_digest TEXT CHECK(
        downstream_receipt_digest IS NULL OR length(downstream_receipt_digest) = 64
    ),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
    updated_at_ms INTEGER NOT NULL CHECK(updated_at_ms >= created_at_ms)
);

CREATE TABLE IF NOT EXISTS skill_activation_evidence_v2 (
    activation_id TEXT PRIMARY KEY CHECK(length(trim(activation_id)) BETWEEN 1 AND 255),
    evidence_digest TEXT NOT NULL UNIQUE CHECK(length(evidence_digest) = 64),
    workspace_id TEXT NOT NULL CHECK(length(trim(workspace_id)) BETWEEN 1 AND 128),
    bundle_id TEXT NOT NULL REFERENCES skill_supply_chain_bundle(bundle_id) ON DELETE RESTRICT,
    bundle_digest TEXT NOT NULL CHECK(length(bundle_digest) = 71),
    bundle_version TEXT NOT NULL CHECK(length(trim(bundle_version)) BETWEEN 1 AND 128),
    runtime_adapter_id TEXT NOT NULL CHECK(length(trim(runtime_adapter_id)) BETWEEN 2 AND 128),
    runtime_protocol_version TEXT NOT NULL CHECK(length(trim(runtime_protocol_version)) BETWEEN 2 AND 128),
    lab_report_digest TEXT NOT NULL
        REFERENCES evolution_lab_trusted_report(report_digest) ON DELETE RESTRICT,
    review_id TEXT NOT NULL
        REFERENCES agent_runtime_review(review_id) ON DELETE RESTRICT,
    review_status_version INTEGER NOT NULL CHECK(review_status_version >= 1),
    sbom_digest TEXT NOT NULL CHECK(length(sbom_digest) = 71),
    signature_audit_ref TEXT NOT NULL CHECK(length(trim(signature_audit_ref)) BETWEEN 1 AND 1024),
    sandbox_attestation_digest TEXT NOT NULL CHECK(length(sandbox_attestation_digest) = 64),
    canary_receipt_digest TEXT NOT NULL CHECK(length(canary_receipt_digest) = 64),
    maturity_decision_id TEXT NOT NULL UNIQUE,
    maturity_level INTEGER NOT NULL CHECK(maturity_level BETWEEN 0 AND 5),
    previous_bundle_id TEXT REFERENCES skill_supply_chain_bundle(bundle_id) ON DELETE RESTRICT,
    rollback_target_bundle_id TEXT REFERENCES skill_supply_chain_bundle(bundle_id) ON DELETE RESTRICT,
    new_capability INTEGER NOT NULL CHECK(new_capability IN (0, 1)),
    reason_code TEXT NOT NULL CHECK(length(trim(reason_code)) BETWEEN 3 AND 128),
    evidence_json TEXT NOT NULL CHECK(json_valid(evidence_json)),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
    CHECK(new_capability = 0 OR maturity_level <= 2),
    CHECK(rollback_target_bundle_id IS NULL OR rollback_target_bundle_id <> bundle_id)
);

CREATE TABLE IF NOT EXISTS skill_capability_maturity_decision_v1 (
    decision_id TEXT PRIMARY KEY CHECK(length(trim(decision_id)) BETWEEN 1 AND 255),
    decision_digest TEXT NOT NULL UNIQUE CHECK(length(decision_digest) = 64),
    activation_id TEXT NOT NULL UNIQUE
        REFERENCES skill_activation_evidence_v2(activation_id) ON DELETE RESTRICT,
    capability_id TEXT NOT NULL CHECK(length(trim(capability_id)) BETWEEN 1 AND 255),
    bundle_id TEXT NOT NULL REFERENCES skill_supply_chain_bundle(bundle_id) ON DELETE RESTRICT,
    prior_level INTEGER CHECK(prior_level IS NULL OR prior_level BETWEEN 0 AND 5),
    requested_level INTEGER NOT NULL CHECK(requested_level BETWEEN 0 AND 5),
    effective_level INTEGER NOT NULL CHECK(effective_level BETWEEN 0 AND 5),
    direction TEXT NOT NULL CHECK(direction IN ('NEW', 'MAINTAIN', 'UPGRADE', 'DOWNGRADE', 'ROLLBACK')),
    lab_report_digest TEXT NOT NULL
        REFERENCES evolution_lab_trusted_report(report_digest) ON DELETE RESTRICT,
    review_id TEXT NOT NULL REFERENCES agent_runtime_review(review_id) ON DELETE RESTRICT,
    decision_json TEXT NOT NULL CHECK(json_valid(decision_json)),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
    CHECK(direction <> 'NEW' OR effective_level <= 2)
);

CREATE TABLE IF NOT EXISTS skill_lifecycle_operation_v2 (
    operation_id TEXT PRIMARY KEY CHECK(length(trim(operation_id)) BETWEEN 1 AND 255),
    idempotency_digest TEXT NOT NULL UNIQUE CHECK(length(idempotency_digest) = 64),
    request_digest TEXT NOT NULL CHECK(length(request_digest) = 64),
    operation TEXT NOT NULL CHECK(operation IN ('ACTIVATE', 'DEGRADE', 'ROLLBACK')),
    activation_id TEXT NOT NULL
        REFERENCES skill_activation_evidence_v2(activation_id) ON DELETE RESTRICT,
    bundle_id TEXT NOT NULL REFERENCES skill_supply_chain_bundle(bundle_id) ON DELETE RESTRICT,
    previous_bundle_id TEXT REFERENCES skill_supply_chain_bundle(bundle_id) ON DELETE RESTRICT,
    receipt_id TEXT NOT NULL UNIQUE,
    operation_json TEXT NOT NULL CHECK(json_valid(operation_json)),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0)
);

CREATE TABLE IF NOT EXISTS skill_active_version_projection_v2 (
    bundle_kind TEXT NOT NULL CHECK(bundle_kind IN ('SKILL', 'PROVIDER')),
    bundle_name TEXT NOT NULL CHECK(length(trim(bundle_name)) BETWEEN 1 AND 255),
    bundle_id TEXT NOT NULL UNIQUE
        REFERENCES skill_supply_chain_bundle(bundle_id) ON DELETE RESTRICT,
    bundle_digest TEXT NOT NULL CHECK(length(bundle_digest) = 71),
    bundle_version TEXT NOT NULL CHECK(length(trim(bundle_version)) BETWEEN 1 AND 128),
    runtime_adapter_id TEXT NOT NULL CHECK(length(trim(runtime_adapter_id)) BETWEEN 2 AND 128),
    runtime_protocol_version TEXT NOT NULL CHECK(length(trim(runtime_protocol_version)) BETWEEN 2 AND 128),
    activation_id TEXT NOT NULL UNIQUE
        REFERENCES skill_activation_evidence_v2(activation_id) ON DELETE RESTRICT,
    maturity_decision_id TEXT NOT NULL UNIQUE
        REFERENCES skill_capability_maturity_decision_v1(decision_id) ON DELETE RESTRICT,
    maturity_level INTEGER NOT NULL CHECK(maturity_level BETWEEN 0 AND 5),
    activation_generation INTEGER NOT NULL CHECK(activation_generation >= 1),
    projection_digest TEXT NOT NULL UNIQUE CHECK(length(projection_digest) = 64),
    updated_at_ms INTEGER NOT NULL CHECK(updated_at_ms >= 0),
    PRIMARY KEY(bundle_kind, bundle_name)
);

CREATE TABLE IF NOT EXISTS skill_lifecycle_receipt_v2 (
    receipt_id TEXT PRIMARY KEY CHECK(length(trim(receipt_id)) BETWEEN 1 AND 255),
    receipt_digest TEXT NOT NULL UNIQUE CHECK(length(receipt_digest) = 64),
    operation_id TEXT NOT NULL UNIQUE,
    event TEXT NOT NULL CHECK(event IN ('ACTIVATED', 'DEGRADED', 'ROLLED_BACK', 'REJECTED')),
    activation_id TEXT NOT NULL
        REFERENCES skill_activation_evidence_v2(activation_id) ON DELETE RESTRICT,
    bundle_id TEXT NOT NULL REFERENCES skill_supply_chain_bundle(bundle_id) ON DELETE RESTRICT,
    evidence_digest TEXT NOT NULL
        REFERENCES skill_activation_evidence_v2(evidence_digest) ON DELETE RESTRICT,
    maturity_decision_digest TEXT NOT NULL
        REFERENCES skill_capability_maturity_decision_v1(decision_digest) ON DELETE RESTRICT,
    active_projection_digest TEXT CHECK(
        active_projection_digest IS NULL OR length(active_projection_digest) = 64
    ),
    previous_bundle_id TEXT REFERENCES skill_supply_chain_bundle(bundle_id) ON DELETE RESTRICT,
    receipt_json TEXT NOT NULL CHECK(json_valid(receipt_json)),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0)
);

CREATE INDEX IF NOT EXISTS idx_skill_lifecycle_canary_ready_v2
    ON skill_lifecycle_canary_operation_v2(status, updated_at_ms, operation_id);
CREATE INDEX IF NOT EXISTS idx_skill_lifecycle_outbox_ready_v2
    ON skill_lifecycle_outbox_v2(state, updated_at_ms, outbox_id);
CREATE INDEX IF NOT EXISTS idx_skill_activation_bundle_v2
    ON skill_activation_evidence_v2(bundle_id, created_at_ms, activation_id);
CREATE INDEX IF NOT EXISTS idx_skill_lifecycle_receipt_bundle_v2
    ON skill_lifecycle_receipt_v2(bundle_id, created_at_ms, receipt_id);

CREATE TRIGGER IF NOT EXISTS skill_activation_evidence_v2_no_update
BEFORE UPDATE ON skill_activation_evidence_v2
BEGIN SELECT RAISE(ABORT, 'Skill activation V2 evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS skill_activation_evidence_v2_no_delete
BEFORE DELETE ON skill_activation_evidence_v2
BEGIN SELECT RAISE(ABORT, 'Skill activation V2 evidence is append-only'); END;
CREATE TRIGGER IF NOT EXISTS skill_maturity_decision_v1_no_update
BEFORE UPDATE ON skill_capability_maturity_decision_v1
BEGIN SELECT RAISE(ABORT, 'Skill maturity decision is immutable'); END;
CREATE TRIGGER IF NOT EXISTS skill_maturity_decision_v1_no_delete
BEFORE DELETE ON skill_capability_maturity_decision_v1
BEGIN SELECT RAISE(ABORT, 'Skill maturity decision is append-only'); END;
CREATE TRIGGER IF NOT EXISTS skill_lifecycle_operation_v2_no_update
BEFORE UPDATE ON skill_lifecycle_operation_v2
BEGIN SELECT RAISE(ABORT, 'Skill lifecycle V2 operation is immutable'); END;
CREATE TRIGGER IF NOT EXISTS skill_lifecycle_operation_v2_no_delete
BEFORE DELETE ON skill_lifecycle_operation_v2
BEGIN SELECT RAISE(ABORT, 'Skill lifecycle V2 operation is append-only'); END;
CREATE TRIGGER IF NOT EXISTS skill_lifecycle_receipt_v2_no_update
BEFORE UPDATE ON skill_lifecycle_receipt_v2
BEGIN SELECT RAISE(ABORT, 'Skill lifecycle V2 receipt is immutable'); END;
CREATE TRIGGER IF NOT EXISTS skill_lifecycle_receipt_v2_no_delete
BEFORE DELETE ON skill_lifecycle_receipt_v2
BEGIN SELECT RAISE(ABORT, 'Skill lifecycle V2 receipt is append-only'); END;

CREATE TRIGGER IF NOT EXISTS skill_active_projection_v2_insert_guard
BEFORE INSERT ON skill_active_version_projection_v2
WHEN natsume_skill_lifecycle_write_authorized() <> 1
BEGIN SELECT RAISE(ABORT, 'Skill active projection requires lifecycle authority'); END;
CREATE TRIGGER IF NOT EXISTS skill_active_projection_v2_update_guard
BEFORE UPDATE ON skill_active_version_projection_v2
WHEN natsume_skill_lifecycle_write_authorized() <> 1
BEGIN SELECT RAISE(ABORT, 'Skill active projection requires lifecycle authority'); END;
CREATE TRIGGER IF NOT EXISTS skill_active_projection_v2_delete_guard
BEFORE DELETE ON skill_active_version_projection_v2
WHEN natsume_skill_lifecycle_write_authorized() <> 1
BEGIN SELECT RAISE(ABORT, 'Skill active projection requires lifecycle authority'); END;

CREATE TRIGGER IF NOT EXISTS skill_canary_operation_v2_insert_guard
BEFORE INSERT ON skill_lifecycle_canary_operation_v2
WHEN natsume_skill_lifecycle_write_authorized() <> 1
BEGIN SELECT RAISE(ABORT, 'Skill canary operation requires lifecycle authority'); END;
CREATE TRIGGER IF NOT EXISTS skill_canary_operation_v2_update_guard
BEFORE UPDATE ON skill_lifecycle_canary_operation_v2
WHEN natsume_skill_lifecycle_write_authorized() <> 1
BEGIN SELECT RAISE(ABORT, 'Skill canary operation requires lifecycle authority'); END;
CREATE TRIGGER IF NOT EXISTS skill_canary_operation_v2_delete_guard
BEFORE DELETE ON skill_lifecycle_canary_operation_v2
WHEN natsume_skill_lifecycle_write_authorized() <> 1
BEGIN SELECT RAISE(ABORT, 'Skill canary operation requires lifecycle authority'); END;
CREATE TRIGGER IF NOT EXISTS skill_lifecycle_outbox_v2_insert_guard
BEFORE INSERT ON skill_lifecycle_outbox_v2
WHEN natsume_skill_lifecycle_write_authorized() <> 1
BEGIN SELECT RAISE(ABORT, 'Skill lifecycle outbox requires lifecycle authority'); END;
CREATE TRIGGER IF NOT EXISTS skill_lifecycle_outbox_v2_update_guard
BEFORE UPDATE ON skill_lifecycle_outbox_v2
WHEN natsume_skill_lifecycle_write_authorized() <> 1
BEGIN SELECT RAISE(ABORT, 'Skill lifecycle outbox requires lifecycle authority'); END;
CREATE TRIGGER IF NOT EXISTS skill_lifecycle_outbox_v2_delete_guard
BEFORE DELETE ON skill_lifecycle_outbox_v2
WHEN natsume_skill_lifecycle_write_authorized() <> 1
BEGIN SELECT RAISE(ABORT, 'Skill lifecycle outbox requires lifecycle authority'); END;

COMMIT;
