-- NE-04 V2: parallel, append-only evidence for isolated Evolution Lab runs.
--
-- V1 rows remain historical/self-attested evidence.  V2 does not migrate or
-- promote them: every V2 run binds a frozen workspace/dataset/spec, sandbox
-- receipt, no-effect audit and independently identified verifier receipt.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS evolution_lab_v2_experiment_spec (
    experiment_id TEXT NOT NULL PRIMARY KEY,
    spec_digest TEXT NOT NULL UNIQUE CHECK(length(spec_digest) = 64),
    workspace_id TEXT NOT NULL CHECK(length(trim(workspace_id)) BETWEEN 1 AND 160),
    dataset_manifest_digest TEXT NOT NULL CHECK(length(dataset_manifest_digest) = 64),
    spec_json TEXT NOT NULL CHECK(json_valid(spec_json) AND length(spec_json) BETWEEN 2 AND 524288),
    created_at_ms INTEGER NOT NULL CHECK(typeof(created_at_ms) = 'integer' AND created_at_ms >= 0)
);

CREATE TABLE IF NOT EXISTS evolution_lab_v2_execution_evidence (
    execution_id TEXT NOT NULL PRIMARY KEY CHECK(length(trim(execution_id)) BETWEEN 1 AND 160),
    spec_digest TEXT NOT NULL REFERENCES evolution_lab_v2_experiment_spec(spec_digest) ON DELETE RESTRICT,
    workspace_id TEXT NOT NULL CHECK(length(trim(workspace_id)) BETWEEN 1 AND 160),
    dataset_manifest_digest TEXT NOT NULL CHECK(length(dataset_manifest_digest) = 64),
    sandbox_receipt_digest TEXT NOT NULL UNIQUE CHECK(length(sandbox_receipt_digest) = 64),
    verification_receipt_digest TEXT NOT NULL UNIQUE CHECK(length(verification_receipt_digest) = 64),
    effect_audit_receipt_digest TEXT NOT NULL CHECK(length(effect_audit_receipt_digest) = 64),
    evidence_digest TEXT NOT NULL UNIQUE CHECK(length(evidence_digest) = 64),
    sandbox_receipt_json TEXT NOT NULL CHECK(json_valid(sandbox_receipt_json) AND length(sandbox_receipt_json) BETWEEN 2 AND 524288),
    verification_receipt_json TEXT NOT NULL CHECK(json_valid(verification_receipt_json) AND length(verification_receipt_json) BETWEEN 2 AND 524288),
    effect_count INTEGER NOT NULL CHECK(effect_count = 0),
    production_side_effects INTEGER NOT NULL CHECK(production_side_effects = 0),
    benefit_eligible INTEGER NOT NULL CHECK(benefit_eligible = 0),
    created_at_ms INTEGER NOT NULL CHECK(typeof(created_at_ms) = 'integer' AND created_at_ms >= 0)
);

CREATE INDEX IF NOT EXISTS idx_evolution_lab_v2_evidence_spec
    ON evolution_lab_v2_execution_evidence(spec_digest, created_at_ms, execution_id);

CREATE TABLE IF NOT EXISTS evolution_lab_v2_run_receipt (
    run_id TEXT NOT NULL PRIMARY KEY CHECK(length(trim(run_id)) BETWEEN 1 AND 160),
    execution_id TEXT NOT NULL UNIQUE REFERENCES evolution_lab_v2_execution_evidence(execution_id) ON DELETE RESTRICT,
    receipt_digest TEXT NOT NULL UNIQUE CHECK(length(receipt_digest) = 64),
    spec_digest TEXT NOT NULL REFERENCES evolution_lab_v2_experiment_spec(spec_digest) ON DELETE RESTRICT,
    workspace_id TEXT NOT NULL CHECK(length(trim(workspace_id)) BETWEEN 1 AND 160),
    dataset_manifest_digest TEXT NOT NULL CHECK(length(dataset_manifest_digest) = 64),
    variant_id TEXT NOT NULL CHECK(length(trim(variant_id)) BETWEEN 1 AND 160),
    receipt_json TEXT NOT NULL CHECK(json_valid(receipt_json) AND length(receipt_json) BETWEEN 2 AND 1048576),
    production_side_effects INTEGER NOT NULL CHECK(production_side_effects = 0),
    benefit_eligible INTEGER NOT NULL CHECK(benefit_eligible = 0),
    created_at_ms INTEGER NOT NULL CHECK(typeof(created_at_ms) = 'integer' AND created_at_ms >= 0)
);

CREATE INDEX IF NOT EXISTS idx_evolution_lab_v2_run_spec_variant
    ON evolution_lab_v2_run_receipt(spec_digest, variant_id, created_at_ms, run_id);

CREATE TABLE IF NOT EXISTS evolution_lab_v2_report (
    report_id TEXT NOT NULL PRIMARY KEY CHECK(length(trim(report_id)) BETWEEN 1 AND 160),
    report_digest TEXT NOT NULL UNIQUE CHECK(length(report_digest) = 64),
    spec_digest TEXT NOT NULL REFERENCES evolution_lab_v2_experiment_spec(spec_digest) ON DELETE RESTRICT,
    report_json TEXT NOT NULL CHECK(json_valid(report_json) AND length(report_json) BETWEEN 2 AND 2097152),
    promotion_eligible INTEGER NOT NULL CHECK(promotion_eligible = 0),
    created_at_ms INTEGER NOT NULL CHECK(typeof(created_at_ms) = 'integer' AND created_at_ms >= 0)
);

CREATE TABLE IF NOT EXISTS evolution_lab_v2_proposal (
    proposal_id TEXT NOT NULL PRIMARY KEY CHECK(length(trim(proposal_id)) BETWEEN 1 AND 160),
    proposal_digest TEXT NOT NULL UNIQUE CHECK(length(proposal_digest) = 64),
    report_digest TEXT NOT NULL REFERENCES evolution_lab_v2_report(report_digest) ON DELETE RESTRICT,
    proposal_json TEXT NOT NULL CHECK(json_valid(proposal_json) AND length(proposal_json) BETWEEN 2 AND 524288),
    review_required INTEGER NOT NULL CHECK(review_required = 1),
    production_influence_enabled INTEGER NOT NULL CHECK(production_influence_enabled = 0),
    created_at_ms INTEGER NOT NULL CHECK(typeof(created_at_ms) = 'integer' AND created_at_ms >= 0)
);

CREATE TRIGGER IF NOT EXISTS evolution_lab_v2_evidence_spec_bound
BEFORE INSERT ON evolution_lab_v2_execution_evidence
WHEN NOT EXISTS (
    SELECT 1 FROM evolution_lab_v2_experiment_spec AS spec
    WHERE spec.spec_digest = NEW.spec_digest
      AND spec.workspace_id = NEW.workspace_id
      AND spec.dataset_manifest_digest = NEW.dataset_manifest_digest
)
BEGIN
    SELECT RAISE(ABORT, 'Evolution Lab V2 evidence does not bind its registered spec');
END;

CREATE TRIGGER IF NOT EXISTS evolution_lab_v2_run_evidence_bound
BEFORE INSERT ON evolution_lab_v2_run_receipt
WHEN NOT EXISTS (
    SELECT 1 FROM evolution_lab_v2_execution_evidence AS evidence
    WHERE evidence.execution_id = NEW.execution_id
      AND evidence.spec_digest = NEW.spec_digest
      AND evidence.workspace_id = NEW.workspace_id
      AND evidence.dataset_manifest_digest = NEW.dataset_manifest_digest
)
BEGIN
    SELECT RAISE(ABORT, 'Evolution Lab V2 run does not bind exact execution evidence');
END;

CREATE TRIGGER IF NOT EXISTS evolution_lab_v2_proposal_report_bound
BEFORE INSERT ON evolution_lab_v2_proposal
WHEN NOT EXISTS (
    SELECT 1 FROM evolution_lab_v2_report
    WHERE report_digest = NEW.report_digest
)
BEGIN
    SELECT RAISE(ABORT, 'Evolution Lab V2 proposal does not bind a durable report');
END;

CREATE TRIGGER IF NOT EXISTS evolution_lab_v2_spec_no_update
BEFORE UPDATE ON evolution_lab_v2_experiment_spec
BEGIN SELECT RAISE(ABORT, 'Evolution Lab V2 experiment spec is immutable'); END;
CREATE TRIGGER IF NOT EXISTS evolution_lab_v2_spec_no_delete
BEFORE DELETE ON evolution_lab_v2_experiment_spec
BEGIN SELECT RAISE(ABORT, 'Evolution Lab V2 experiment spec is append-only'); END;
CREATE TRIGGER IF NOT EXISTS evolution_lab_v2_evidence_no_update
BEFORE UPDATE ON evolution_lab_v2_execution_evidence
BEGIN SELECT RAISE(ABORT, 'Evolution Lab V2 execution evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS evolution_lab_v2_evidence_no_delete
BEFORE DELETE ON evolution_lab_v2_execution_evidence
BEGIN SELECT RAISE(ABORT, 'Evolution Lab V2 execution evidence is append-only'); END;
CREATE TRIGGER IF NOT EXISTS evolution_lab_v2_run_no_update
BEFORE UPDATE ON evolution_lab_v2_run_receipt
BEGIN SELECT RAISE(ABORT, 'Evolution Lab V2 run receipt is immutable'); END;
CREATE TRIGGER IF NOT EXISTS evolution_lab_v2_run_no_delete
BEFORE DELETE ON evolution_lab_v2_run_receipt
BEGIN SELECT RAISE(ABORT, 'Evolution Lab V2 run receipt is append-only'); END;
CREATE TRIGGER IF NOT EXISTS evolution_lab_v2_report_no_update
BEFORE UPDATE ON evolution_lab_v2_report
BEGIN SELECT RAISE(ABORT, 'Evolution Lab V2 report is immutable'); END;
CREATE TRIGGER IF NOT EXISTS evolution_lab_v2_report_no_delete
BEFORE DELETE ON evolution_lab_v2_report
BEGIN SELECT RAISE(ABORT, 'Evolution Lab V2 report is append-only'); END;
CREATE TRIGGER IF NOT EXISTS evolution_lab_v2_proposal_no_update
BEFORE UPDATE ON evolution_lab_v2_proposal
BEGIN SELECT RAISE(ABORT, 'Evolution Lab V2 proposal is immutable'); END;
CREATE TRIGGER IF NOT EXISTS evolution_lab_v2_proposal_no_delete
BEFORE DELETE ON evolution_lab_v2_proposal
BEGIN SELECT RAISE(ABORT, 'Evolution Lab V2 proposal is append-only'); END;

COMMIT;
