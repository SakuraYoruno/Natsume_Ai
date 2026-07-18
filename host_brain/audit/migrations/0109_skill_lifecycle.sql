-- NE-05: durable Skill activation evidence and lifecycle receipts.
-- Evidence is immutable; it is never a production execution authority by
-- itself.  Supply-chain activation must still pass the fenced bundle store.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS skill_activation_evidence (
    activation_id TEXT PRIMARY KEY CHECK(length(trim(activation_id)) BETWEEN 1 AND 255),
    evidence_digest TEXT NOT NULL UNIQUE
        CHECK(length(evidence_digest) = 71 AND substr(evidence_digest, 1, 7) = 'sha256:'),
    bundle_id TEXT NOT NULL,
    bundle_digest TEXT NOT NULL
        CHECK(length(bundle_digest) = 71 AND substr(bundle_digest, 1, 7) = 'sha256:'),
    bundle_version TEXT NOT NULL CHECK(length(trim(bundle_version)) BETWEEN 1 AND 255),
    lab_report_ref TEXT NOT NULL CHECK(length(trim(lab_report_ref)) BETWEEN 1 AND 1024),
    review_ref TEXT NOT NULL CHECK(length(trim(review_ref)) BETWEEN 1 AND 1024),
    sbom_digest TEXT NOT NULL
        CHECK(length(sbom_digest) = 71 AND substr(sbom_digest, 1, 7) = 'sha256:'),
    signature_audit_ref TEXT NOT NULL CHECK(length(trim(signature_audit_ref)) BETWEEN 1 AND 1024),
    sandbox_receipt_ref TEXT NOT NULL CHECK(length(trim(sandbox_receipt_ref)) BETWEEN 1 AND 1024),
    rollback_ref TEXT NOT NULL CHECK(length(trim(rollback_ref)) BETWEEN 1 AND 1024),
    previous_bundle_id TEXT,
    maturity_level INTEGER NOT NULL CHECK(maturity_level BETWEEN 0 AND 5),
    new_capability INTEGER NOT NULL CHECK(new_capability IN (0, 1)),
    reason_code TEXT NOT NULL CHECK(length(trim(reason_code)) BETWEEN 3 AND 128),
    evidence_json TEXT NOT NULL CHECK(length(evidence_json) BETWEEN 2 AND 262144),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0)
);

CREATE TABLE IF NOT EXISTS skill_lifecycle_receipt (
    receipt_id TEXT PRIMARY KEY CHECK(length(trim(receipt_id)) BETWEEN 1 AND 255),
    receipt_digest TEXT NOT NULL UNIQUE
        CHECK(length(receipt_digest) = 71 AND substr(receipt_digest, 1, 7) = 'sha256:'),
    activation_id TEXT NOT NULL REFERENCES skill_activation_evidence(activation_id) ON DELETE RESTRICT,
    event TEXT NOT NULL CHECK(event IN ('REQUESTED', 'ACTIVATED', 'DEGRADED', 'ROLLED_BACK', 'REJECTED')),
    bundle_id TEXT NOT NULL,
    evidence_digest TEXT NOT NULL REFERENCES skill_activation_evidence(evidence_digest) ON DELETE RESTRICT,
    operation_id TEXT,
    previous_bundle_id TEXT,
    receipt_json TEXT NOT NULL CHECK(length(receipt_json) BETWEEN 2 AND 262144),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0)
);

CREATE INDEX IF NOT EXISTS idx_skill_activation_evidence_bundle
    ON skill_activation_evidence(bundle_id, created_at_ms, activation_id);
CREATE INDEX IF NOT EXISTS idx_skill_lifecycle_receipt_bundle
    ON skill_lifecycle_receipt(bundle_id, created_at_ms, receipt_id);
CREATE INDEX IF NOT EXISTS idx_skill_lifecycle_receipt_activation
    ON skill_lifecycle_receipt(activation_id, created_at_ms, receipt_id);

CREATE TRIGGER IF NOT EXISTS skill_activation_evidence_no_update
BEFORE UPDATE ON skill_activation_evidence
BEGIN SELECT RAISE(ABORT, 'Skill activation evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS skill_activation_evidence_no_delete
BEFORE DELETE ON skill_activation_evidence
BEGIN SELECT RAISE(ABORT, 'Skill activation evidence is append-only'); END;
CREATE TRIGGER IF NOT EXISTS skill_lifecycle_receipt_no_update
BEFORE UPDATE ON skill_lifecycle_receipt
BEGIN SELECT RAISE(ABORT, 'Skill lifecycle receipt is immutable'); END;
CREATE TRIGGER IF NOT EXISTS skill_lifecycle_receipt_no_delete
BEFORE DELETE ON skill_lifecycle_receipt
BEGIN SELECT RAISE(ABORT, 'Skill lifecycle receipt is append-only'); END;

COMMIT;
