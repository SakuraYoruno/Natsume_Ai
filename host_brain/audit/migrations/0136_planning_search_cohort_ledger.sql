-- NE-02: one append-only owner for fixed Research, Code and Pentest cohorts.
-- Cohort reports and promotion proposals are evidence only.  They cannot
-- activate PlanningSearch or influence production execution.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS planning_search_cohort_registration (
    domain TEXT NOT NULL
        CHECK(domain IN ('RESEARCH', 'CODE', 'PENTEST')),
    cohort_id TEXT NOT NULL
        CHECK(length(trim(cohort_id)) BETWEEN 1 AND 160),
    cohort_version TEXT NOT NULL
        CHECK(length(trim(cohort_version)) BETWEEN 1 AND 80),
    spec_digest TEXT NOT NULL UNIQUE
        CHECK(length(spec_digest) = 64),
    expected_member_count INTEGER NOT NULL
        CHECK(
            (domain = 'RESEARCH' AND expected_member_count = 24)
            OR (domain = 'CODE' AND expected_member_count = 20)
            OR (domain = 'PENTEST' AND expected_member_count = 30)
        ),
    spec_json TEXT NOT NULL
        CHECK(length(spec_json) BETWEEN 2 AND 1048576),
    production_influence_enabled INTEGER NOT NULL
        CHECK(production_influence_enabled = 0),
    created_at_ms INTEGER NOT NULL
        CHECK(typeof(created_at_ms) = 'integer' AND created_at_ms >= 0),
    PRIMARY KEY(domain, cohort_id, cohort_version)
);

CREATE INDEX IF NOT EXISTS idx_planning_cohort_registration_created
    ON planning_search_cohort_registration(
        created_at_ms, domain, cohort_id, cohort_version
    );

CREATE TABLE IF NOT EXISTS planning_search_cohort_member_evidence (
    domain TEXT NOT NULL,
    cohort_id TEXT NOT NULL,
    cohort_version TEXT NOT NULL,
    workload_id TEXT NOT NULL
        CHECK(length(trim(workload_id)) BETWEEN 1 AND 160),
    run_id TEXT NOT NULL
        CHECK(length(trim(run_id)) BETWEEN 1 AND 255),
    evidence_digest TEXT NOT NULL UNIQUE
        CHECK(length(evidence_digest) = 64),
    evidence_json TEXT NOT NULL
        CHECK(length(evidence_json) BETWEEN 2 AND 4194304),
    authority_proof_digest TEXT NOT NULL UNIQUE
        CHECK(length(authority_proof_digest) = 64),
    authority_proof_json TEXT NOT NULL
        CHECK(length(authority_proof_json) BETWEEN 2 AND 262144),
    production_influence_enabled INTEGER NOT NULL
        CHECK(production_influence_enabled = 0),
    created_at_ms INTEGER NOT NULL
        CHECK(typeof(created_at_ms) = 'integer' AND created_at_ms >= 0),
    PRIMARY KEY(domain, cohort_id, cohort_version, workload_id),
    UNIQUE(domain, run_id),
    FOREIGN KEY(domain, cohort_id, cohort_version)
        REFERENCES planning_search_cohort_registration(
            domain, cohort_id, cohort_version
        )
        ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_planning_cohort_member_created
    ON planning_search_cohort_member_evidence(
        domain, cohort_id, cohort_version, created_at_ms, workload_id
    );

CREATE TABLE IF NOT EXISTS planning_search_cohort_report (
    domain TEXT NOT NULL,
    cohort_id TEXT NOT NULL,
    cohort_version TEXT NOT NULL,
    report_digest TEXT NOT NULL UNIQUE
        CHECK(length(report_digest) = 64),
    spec_digest TEXT NOT NULL
        CHECK(length(spec_digest) = 64),
    evidence_set_digest TEXT NOT NULL
        CHECK(length(evidence_set_digest) = 64),
    report_json TEXT NOT NULL
        CHECK(length(report_json) BETWEEN 2 AND 1048576),
    benefit_eligible INTEGER NOT NULL
        CHECK(benefit_eligible IN (0, 1)),
    calibration_eligible INTEGER NOT NULL
        CHECK(calibration_eligible IN (0, 1)),
    proposal_eligible INTEGER NOT NULL
        CHECK(proposal_eligible IN (0, 1)),
    production_influence_enabled INTEGER NOT NULL
        CHECK(production_influence_enabled = 0),
    created_at_ms INTEGER NOT NULL
        CHECK(typeof(created_at_ms) = 'integer' AND created_at_ms >= 0),
    PRIMARY KEY(domain, cohort_id, cohort_version),
    FOREIGN KEY(domain, cohort_id, cohort_version)
        REFERENCES planning_search_cohort_registration(
            domain, cohort_id, cohort_version
        )
        ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_planning_cohort_report_created
    ON planning_search_cohort_report(created_at_ms, domain, report_digest);

CREATE TABLE IF NOT EXISTS planning_search_promotion_evidence (
    proposal_id TEXT NOT NULL
        CHECK(length(trim(proposal_id)) BETWEEN 16 AND 80),
    domain TEXT NOT NULL
        CHECK(domain IN ('RESEARCH', 'CODE', 'PENTEST')),
    report_digest TEXT NOT NULL
        REFERENCES planning_search_cohort_report(report_digest)
        ON DELETE RESTRICT,
    report_ref TEXT NOT NULL
        CHECK(length(trim(report_ref)) BETWEEN 1 AND 1024),
    created_at_ms INTEGER NOT NULL
        CHECK(typeof(created_at_ms) = 'integer' AND created_at_ms >= 0),
    PRIMARY KEY(proposal_id, domain),
    UNIQUE(proposal_id, report_digest)
);

CREATE TABLE IF NOT EXISTS planning_search_promotion_proposal (
    proposal_id TEXT NOT NULL PRIMARY KEY
        CHECK(length(trim(proposal_id)) BETWEEN 16 AND 80),
    proposal_digest TEXT NOT NULL UNIQUE
        CHECK(length(proposal_digest) = 64),
    target_algorithm_version TEXT NOT NULL
        CHECK(length(trim(target_algorithm_version)) BETWEEN 1 AND 160),
    evidence_set_digest TEXT NOT NULL
        CHECK(length(evidence_set_digest) = 64),
    proposal_json TEXT NOT NULL
        CHECK(length(proposal_json) BETWEEN 2 AND 1048576),
    review_required INTEGER NOT NULL
        CHECK(review_required = 1),
    activation_authority INTEGER NOT NULL
        CHECK(activation_authority = 0),
    production_influence_enabled INTEGER NOT NULL
        CHECK(production_influence_enabled = 0),
    created_at_ms INTEGER NOT NULL
        CHECK(typeof(created_at_ms) = 'integer' AND created_at_ms >= 0)
);

CREATE INDEX IF NOT EXISTS idx_planning_promotion_proposal_created
    ON planning_search_promotion_proposal(created_at_ms, proposal_id);

CREATE TRIGGER IF NOT EXISTS planning_cohort_member_registration_bound
BEFORE INSERT ON planning_search_cohort_member_evidence
WHEN NOT EXISTS (
    SELECT 1
    FROM planning_search_cohort_registration
    WHERE domain = NEW.domain
      AND cohort_id = NEW.cohort_id
      AND cohort_version = NEW.cohort_version
)
BEGIN
    SELECT RAISE(ABORT, 'planning cohort member lacks exact registration');
END;

CREATE TRIGGER IF NOT EXISTS planning_cohort_report_complete
BEFORE INSERT ON planning_search_cohort_report
WHEN NOT EXISTS (
    SELECT 1
    FROM planning_search_cohort_registration AS registration
    WHERE registration.domain = NEW.domain
      AND registration.cohort_id = NEW.cohort_id
      AND registration.cohort_version = NEW.cohort_version
      AND registration.spec_digest = NEW.spec_digest
      AND (
          SELECT COUNT(*)
          FROM planning_search_cohort_member_evidence AS member
          WHERE member.domain = NEW.domain
            AND member.cohort_id = NEW.cohort_id
            AND member.cohort_version = NEW.cohort_version
      ) = registration.expected_member_count
)
BEGIN
    SELECT RAISE(ABORT, 'planning cohort report requires exact complete evidence');
END;

CREATE TRIGGER IF NOT EXISTS planning_promotion_proposal_evidence_complete
BEFORE INSERT ON planning_search_promotion_proposal
WHEN
    (
        SELECT COUNT(*)
        FROM planning_search_promotion_evidence
        WHERE proposal_id = NEW.proposal_id
          AND domain = 'PENTEST'
    ) <> 1
    OR NOT EXISTS (
        SELECT 1
        FROM planning_search_promotion_evidence AS evidence
        JOIN planning_search_cohort_report AS report
          ON report.report_digest = evidence.report_digest
        WHERE evidence.proposal_id = NEW.proposal_id
          AND evidence.domain = 'PENTEST'
          AND report.domain = 'PENTEST'
          AND report.calibration_eligible = 1
          AND report.proposal_eligible = 1
    )
    OR NOT EXISTS (
        SELECT 1
        FROM planning_search_promotion_evidence AS evidence
        JOIN planning_search_cohort_report AS report
          ON report.report_digest = evidence.report_digest
        WHERE evidence.proposal_id = NEW.proposal_id
          AND evidence.domain IN ('RESEARCH', 'CODE')
          AND report.domain = evidence.domain
          AND report.benefit_eligible = 1
          AND report.proposal_eligible = 1
    )
BEGIN
    SELECT RAISE(
        ABORT,
        'planning promotion requires safe Pentest and non-Pentest benefit'
    );
END;

CREATE TRIGGER IF NOT EXISTS planning_cohort_registration_no_update
BEFORE UPDATE ON planning_search_cohort_registration
BEGIN SELECT RAISE(ABORT, 'planning cohort registration is immutable'); END;
CREATE TRIGGER IF NOT EXISTS planning_cohort_registration_no_delete
BEFORE DELETE ON planning_search_cohort_registration
BEGIN SELECT RAISE(ABORT, 'planning cohort registration is append-only'); END;
CREATE TRIGGER IF NOT EXISTS planning_cohort_member_no_update
BEFORE UPDATE ON planning_search_cohort_member_evidence
BEGIN SELECT RAISE(ABORT, 'planning cohort member evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS planning_cohort_member_no_delete
BEFORE DELETE ON planning_search_cohort_member_evidence
BEGIN SELECT RAISE(ABORT, 'planning cohort member evidence is append-only'); END;
CREATE TRIGGER IF NOT EXISTS planning_cohort_report_no_update
BEFORE UPDATE ON planning_search_cohort_report
BEGIN SELECT RAISE(ABORT, 'planning cohort report is immutable'); END;
CREATE TRIGGER IF NOT EXISTS planning_cohort_report_no_delete
BEFORE DELETE ON planning_search_cohort_report
BEGIN SELECT RAISE(ABORT, 'planning cohort report is append-only'); END;
CREATE TRIGGER IF NOT EXISTS planning_promotion_evidence_no_update
BEFORE UPDATE ON planning_search_promotion_evidence
BEGIN SELECT RAISE(ABORT, 'planning promotion evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS planning_promotion_evidence_no_delete
BEFORE DELETE ON planning_search_promotion_evidence
BEGIN SELECT RAISE(ABORT, 'planning promotion evidence is append-only'); END;
CREATE TRIGGER IF NOT EXISTS planning_promotion_proposal_no_update
BEFORE UPDATE ON planning_search_promotion_proposal
BEGIN SELECT RAISE(ABORT, 'planning promotion proposal is immutable'); END;
CREATE TRIGGER IF NOT EXISTS planning_promotion_proposal_no_delete
BEFORE DELETE ON planning_search_promotion_proposal
BEGIN SELECT RAISE(ABORT, 'planning promotion proposal is append-only'); END;

COMMIT;
