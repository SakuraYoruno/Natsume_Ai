-- NE-04: append-only Evolution Lab experiment, report and proposal ledger.
-- Lab rows are evidence only and cannot mutate production authority.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS evolution_lab_experiment_spec (
    experiment_id TEXT NOT NULL PRIMARY KEY,
    spec_digest TEXT NOT NULL UNIQUE CHECK(length(spec_digest) = 64),
    spec_json TEXT NOT NULL CHECK(length(spec_json) BETWEEN 2 AND 262144),
    created_at_ms INTEGER NOT NULL CHECK(typeof(created_at_ms) = 'integer' AND created_at_ms >= 0)
);

CREATE TABLE IF NOT EXISTS evolution_lab_run_receipt (
    run_id TEXT NOT NULL PRIMARY KEY,
    spec_digest TEXT NOT NULL REFERENCES evolution_lab_experiment_spec(spec_digest) ON DELETE RESTRICT,
    variant TEXT NOT NULL,
    evidence_digest TEXT NOT NULL CHECK(length(evidence_digest) = 64),
    receipt_json TEXT NOT NULL CHECK(length(receipt_json) BETWEEN 2 AND 524288),
    production_side_effects INTEGER NOT NULL CHECK(production_side_effects = 0),
    created_at_ms INTEGER NOT NULL CHECK(typeof(created_at_ms) = 'integer' AND created_at_ms >= 0)
);

CREATE INDEX IF NOT EXISTS idx_evolution_lab_run_spec_variant
    ON evolution_lab_run_receipt(spec_digest, variant, created_at_ms, run_id);

CREATE TABLE IF NOT EXISTS evolution_lab_report (
    report_id TEXT NOT NULL PRIMARY KEY,
    report_digest TEXT NOT NULL UNIQUE CHECK(length(report_digest) = 64),
    spec_digest TEXT NOT NULL REFERENCES evolution_lab_experiment_spec(spec_digest) ON DELETE RESTRICT,
    report_json TEXT NOT NULL CHECK(length(report_json) BETWEEN 2 AND 1048576),
    created_at_ms INTEGER NOT NULL CHECK(typeof(created_at_ms) = 'integer' AND created_at_ms >= 0)
);

CREATE TABLE IF NOT EXISTS evolution_lab_proposal (
    proposal_id TEXT NOT NULL PRIMARY KEY,
    report_digest TEXT NOT NULL REFERENCES evolution_lab_report(report_digest) ON DELETE RESTRICT,
    proposal_json TEXT NOT NULL CHECK(length(proposal_json) BETWEEN 2 AND 262144),
    review_required INTEGER NOT NULL CHECK(review_required = 1),
    production_influence_enabled INTEGER NOT NULL CHECK(production_influence_enabled = 0),
    created_at_ms INTEGER NOT NULL CHECK(typeof(created_at_ms) = 'integer' AND created_at_ms >= 0)
);

CREATE TRIGGER IF NOT EXISTS evolution_lab_run_spec_bound
BEFORE INSERT ON evolution_lab_run_receipt
WHEN NOT EXISTS (
    SELECT 1 FROM evolution_lab_experiment_spec
    WHERE spec_digest = NEW.spec_digest
)
BEGIN
    SELECT RAISE(ABORT, 'Evolution Lab run does not bind an experiment spec');
END;

CREATE TRIGGER IF NOT EXISTS evolution_lab_proposal_report_bound
BEFORE INSERT ON evolution_lab_proposal
WHEN NOT EXISTS (
    SELECT 1 FROM evolution_lab_report
    WHERE report_digest = NEW.report_digest
)
BEGIN
    SELECT RAISE(ABORT, 'Evolution Lab proposal does not bind a report');
END;

CREATE TRIGGER IF NOT EXISTS evolution_lab_spec_no_update
BEFORE UPDATE ON evolution_lab_experiment_spec
BEGIN SELECT RAISE(ABORT, 'Evolution Lab experiment spec is immutable'); END;
CREATE TRIGGER IF NOT EXISTS evolution_lab_spec_no_delete
BEFORE DELETE ON evolution_lab_experiment_spec
BEGIN SELECT RAISE(ABORT, 'Evolution Lab experiment spec is append-only'); END;
CREATE TRIGGER IF NOT EXISTS evolution_lab_run_no_update
BEFORE UPDATE ON evolution_lab_run_receipt
BEGIN SELECT RAISE(ABORT, 'Evolution Lab run receipt is immutable'); END;
CREATE TRIGGER IF NOT EXISTS evolution_lab_run_no_delete
BEFORE DELETE ON evolution_lab_run_receipt
BEGIN SELECT RAISE(ABORT, 'Evolution Lab run receipt is append-only'); END;
CREATE TRIGGER IF NOT EXISTS evolution_lab_report_no_update
BEFORE UPDATE ON evolution_lab_report
BEGIN SELECT RAISE(ABORT, 'Evolution Lab report is immutable'); END;
CREATE TRIGGER IF NOT EXISTS evolution_lab_report_no_delete
BEFORE DELETE ON evolution_lab_report
BEGIN SELECT RAISE(ABORT, 'Evolution Lab report is append-only'); END;
CREATE TRIGGER IF NOT EXISTS evolution_lab_proposal_no_update
BEFORE UPDATE ON evolution_lab_proposal
BEGIN SELECT RAISE(ABORT, 'Evolution Lab proposal is immutable'); END;
CREATE TRIGGER IF NOT EXISTS evolution_lab_proposal_no_delete
BEFORE DELETE ON evolution_lab_proposal
BEGIN SELECT RAISE(ABORT, 'Evolution Lab proposal is append-only'); END;

COMMIT;
