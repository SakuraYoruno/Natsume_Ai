-- D20b: append-only Research shadow registration, paired run and receipt ledger.
-- This schema is experiment evidence only and has no production authority.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS planning_search_research_registration (
    registration_id TEXT NOT NULL PRIMARY KEY,
    binding_digest TEXT NOT NULL UNIQUE CHECK(length(binding_digest) = 64),
    profile_digest TEXT NOT NULL CHECK(length(profile_digest) = 64),
    budget_digest TEXT NOT NULL CHECK(length(budget_digest) = 64),
    registration_json TEXT NOT NULL CHECK(length(registration_json) BETWEEN 2 AND 262144),
    production_influence_enabled INTEGER NOT NULL CHECK(production_influence_enabled = 0),
    created_at_ms INTEGER NOT NULL CHECK(typeof(created_at_ms) = 'integer' AND created_at_ms >= 0)
);

CREATE TABLE IF NOT EXISTS planning_search_research_run_spec (
    run_id TEXT NOT NULL PRIMARY KEY,
    spec_digest TEXT NOT NULL UNIQUE CHECK(length(spec_digest) = 64),
    registration_id TEXT NOT NULL REFERENCES planning_search_research_registration(registration_id) ON DELETE RESTRICT,
    baseline_arm_id TEXT NOT NULL,
    selected_arm_id TEXT NOT NULL,
    spec_json TEXT NOT NULL CHECK(length(spec_json) BETWEEN 2 AND 262144),
    randomized_arm_order INTEGER NOT NULL CHECK(randomized_arm_order = 1),
    production_influence_enabled INTEGER NOT NULL CHECK(production_influence_enabled = 0),
    created_at_ms INTEGER NOT NULL CHECK(typeof(created_at_ms) = 'integer' AND created_at_ms >= 0),
    CHECK(baseline_arm_id <> selected_arm_id)
);

CREATE INDEX IF NOT EXISTS idx_research_run_spec_registration_created
    ON planning_search_research_run_spec(registration_id, created_at_ms, run_id);

CREATE TABLE IF NOT EXISTS planning_search_research_arm_execution (
    run_id TEXT NOT NULL REFERENCES planning_search_research_run_spec(run_id) ON DELETE RESTRICT,
    arm_role TEXT NOT NULL CHECK(arm_role IN ('BASELINE', 'SELECTED')),
    arm_id TEXT NOT NULL,
    evidence_digest TEXT NOT NULL CHECK(length(evidence_digest) = 64),
    model_receipt_json TEXT NOT NULL CHECK(length(model_receipt_json) BETWEEN 2 AND 262144),
    verifier_receipt_json TEXT NOT NULL CHECK(length(verifier_receipt_json) BETWEEN 2 AND 262144),
    created_at_ms INTEGER NOT NULL CHECK(typeof(created_at_ms) = 'integer' AND created_at_ms >= 0),
    PRIMARY KEY(run_id, arm_role),
    UNIQUE(run_id, arm_id)
);

CREATE INDEX IF NOT EXISTS idx_research_arm_execution_evidence
    ON planning_search_research_arm_execution(evidence_digest, created_at_ms, run_id);

CREATE TABLE IF NOT EXISTS planning_search_research_terminal_receipt (
    run_id TEXT NOT NULL PRIMARY KEY REFERENCES planning_search_research_run_spec(run_id) ON DELETE RESTRICT,
    receipt_digest TEXT NOT NULL UNIQUE CHECK(length(receipt_digest) = 64),
    receipt_json TEXT NOT NULL CHECK(length(receipt_json) BETWEEN 2 AND 524288),
    production_influence_enabled INTEGER NOT NULL CHECK(production_influence_enabled = 0),
    created_at_ms INTEGER NOT NULL CHECK(typeof(created_at_ms) = 'integer' AND created_at_ms >= 0)
);

CREATE TRIGGER IF NOT EXISTS research_run_spec_registration_bound
BEFORE INSERT ON planning_search_research_run_spec
WHEN NOT EXISTS (
    SELECT 1 FROM planning_search_research_registration
    WHERE registration_id = NEW.registration_id
)
BEGIN
    SELECT RAISE(ABORT, 'research run spec does not bind registration');
END;

CREATE TRIGGER IF NOT EXISTS research_arm_execution_bound
BEFORE INSERT ON planning_search_research_arm_execution
WHEN NOT EXISTS (
    SELECT 1 FROM planning_search_research_run_spec
    WHERE run_id = NEW.run_id
      AND ((NEW.arm_role = 'BASELINE' AND NEW.arm_id = baseline_arm_id)
        OR (NEW.arm_role = 'SELECTED' AND NEW.arm_id = selected_arm_id))
)
BEGIN
    SELECT RAISE(ABORT, 'research arm execution does not bind preregistered arm');
END;

CREATE TRIGGER IF NOT EXISTS research_terminal_pair_complete
BEFORE INSERT ON planning_search_research_terminal_receipt
WHEN (SELECT COUNT(*) FROM planning_search_research_arm_execution WHERE run_id = NEW.run_id) <> 2
BEGIN
    SELECT RAISE(ABORT, 'research terminal receipt requires both paired arms');
END;

CREATE TRIGGER IF NOT EXISTS research_registration_no_update
BEFORE UPDATE ON planning_search_research_registration
BEGIN SELECT RAISE(ABORT, 'research registration is immutable'); END;
CREATE TRIGGER IF NOT EXISTS research_registration_no_delete
BEFORE DELETE ON planning_search_research_registration
BEGIN SELECT RAISE(ABORT, 'research registration is append-only'); END;
CREATE TRIGGER IF NOT EXISTS research_run_spec_no_update
BEFORE UPDATE ON planning_search_research_run_spec
BEGIN SELECT RAISE(ABORT, 'research run spec is immutable'); END;
CREATE TRIGGER IF NOT EXISTS research_run_spec_no_delete
BEFORE DELETE ON planning_search_research_run_spec
BEGIN SELECT RAISE(ABORT, 'research run spec is append-only'); END;
CREATE TRIGGER IF NOT EXISTS research_arm_execution_no_update
BEFORE UPDATE ON planning_search_research_arm_execution
BEGIN SELECT RAISE(ABORT, 'research arm execution is immutable'); END;
CREATE TRIGGER IF NOT EXISTS research_arm_execution_no_delete
BEFORE DELETE ON planning_search_research_arm_execution
BEGIN SELECT RAISE(ABORT, 'research arm execution is append-only'); END;
CREATE TRIGGER IF NOT EXISTS research_terminal_receipt_no_update
BEFORE UPDATE ON planning_search_research_terminal_receipt
BEGIN SELECT RAISE(ABORT, 'research terminal receipt is immutable'); END;
CREATE TRIGGER IF NOT EXISTS research_terminal_receipt_no_delete
BEFORE DELETE ON planning_search_research_terminal_receipt
BEGIN SELECT RAISE(ABORT, 'research terminal receipt is append-only'); END;

COMMIT;
