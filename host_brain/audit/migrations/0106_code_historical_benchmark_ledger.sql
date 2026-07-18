-- D18: immutable, cleanup-gated historical Code benchmark experiment ledger.
-- This asset stores preregistration and paired experiment evidence only.  It
-- is never a production promotion, queue, task, model, tool or test authority.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS planning_search_code_benchmark_workload (
    registration_id TEXT NOT NULL PRIMARY KEY
        CHECK(length(trim(registration_id)) BETWEEN 16 AND 128),
    registration_digest TEXT NOT NULL UNIQUE
        CHECK(length(registration_digest) = 64),
    workload_id TEXT NOT NULL UNIQUE
        CHECK(length(trim(workload_id)) BETWEEN 1 AND 240),
    workload_digest TEXT NOT NULL UNIQUE
        CHECK(length(workload_digest) = 64),
    cohort_key TEXT NOT NULL
        CHECK(length(trim(cohort_key)) BETWEEN 1 AND 240),
    source_ref TEXT NOT NULL
        CHECK(length(trim(source_ref)) BETWEEN 1 AND 1024),
    source_commit TEXT NOT NULL
        CHECK(length(source_commit) BETWEEN 40 AND 64),
    path_policy_digest TEXT NOT NULL
        CHECK(length(path_policy_digest) = 64),
    execution_budget_json TEXT NOT NULL
        CHECK(length(execution_budget_json) BETWEEN 2 AND 8192),
    registration_json TEXT NOT NULL
        CHECK(length(registration_json) BETWEEN 2 AND 262144),
    mode TEXT NOT NULL
        CHECK(mode = 'PRIVATE_BARE_WORKTREE_TRUSTED_PROCESS_ONLY'),
    production_influence_enabled INTEGER NOT NULL
        CHECK(production_influence_enabled = 0),
    created_at_ms INTEGER NOT NULL
        CHECK(typeof(created_at_ms) = 'integer' AND created_at_ms >= 0)
);

CREATE INDEX IF NOT EXISTS idx_code_benchmark_workload_cohort_created
    ON planning_search_code_benchmark_workload(cohort_key, created_at_ms, registration_id);

CREATE TABLE IF NOT EXISTS planning_search_code_benchmark_run_spec (
    run_id TEXT NOT NULL PRIMARY KEY
        CHECK(length(trim(run_id)) BETWEEN 1 AND 120),
    spec_digest TEXT NOT NULL UNIQUE
        CHECK(length(spec_digest) = 64),
    registration_id TEXT NOT NULL
        REFERENCES planning_search_code_benchmark_workload(registration_id) ON DELETE RESTRICT,
    registration_digest TEXT NOT NULL
        CHECK(length(registration_digest) = 64),
    workload_digest TEXT NOT NULL
        CHECK(length(workload_digest) = 64),
    baseline_arm_id TEXT NOT NULL
        CHECK(length(trim(baseline_arm_id)) BETWEEN 1 AND 128),
    selected_arm_id TEXT NOT NULL
        CHECK(length(trim(selected_arm_id)) BETWEEN 1 AND 128),
    baseline_arm_digest TEXT NOT NULL
        CHECK(length(baseline_arm_digest) = 64),
    selected_arm_digest TEXT NOT NULL
        CHECK(length(selected_arm_digest) = 64),
    spec_json TEXT NOT NULL
        CHECK(length(spec_json) BETWEEN 2 AND 262144),
    mode TEXT NOT NULL
        CHECK(mode = 'PRIVATE_BARE_WORKTREE_TRUSTED_PROCESS_ONLY'),
    production_influence_enabled INTEGER NOT NULL
        CHECK(production_influence_enabled = 0),
    created_at_ms INTEGER NOT NULL
        CHECK(typeof(created_at_ms) = 'integer' AND created_at_ms >= 0),
    CHECK(baseline_arm_id <> selected_arm_id),
    CHECK(baseline_arm_digest <> selected_arm_digest)
);

CREATE INDEX IF NOT EXISTS idx_code_benchmark_run_spec_registration_created
    ON planning_search_code_benchmark_run_spec(registration_id, created_at_ms, run_id);

CREATE TABLE IF NOT EXISTS planning_search_code_benchmark_arm_execution (
    run_id TEXT NOT NULL
        REFERENCES planning_search_code_benchmark_run_spec(run_id) ON DELETE RESTRICT,
    arm_role TEXT NOT NULL
        CHECK(arm_role IN ('BASELINE', 'SELECTED')),
    arm_id TEXT NOT NULL
        CHECK(length(trim(arm_id)) BETWEEN 1 AND 128),
    arm_digest TEXT NOT NULL
        CHECK(length(arm_digest) = 64),
    evidence_digest TEXT NOT NULL
        CHECK(length(evidence_digest) = 64),
    source_ref TEXT NOT NULL
        CHECK(length(trim(source_ref)) BETWEEN 1 AND 1024),
    source_commit TEXT NOT NULL
        CHECK(length(source_commit) BETWEEN 40 AND 64),
    path_policy_digest TEXT NOT NULL
        CHECK(length(path_policy_digest) = 64),
    evidence_json TEXT NOT NULL
        CHECK(length(evidence_json) BETWEEN 2 AND 262144),
    outcome_json TEXT NOT NULL
        CHECK(length(outcome_json) BETWEEN 2 AND 131072),
    created_at_ms INTEGER NOT NULL
        CHECK(typeof(created_at_ms) = 'integer' AND created_at_ms >= 0),
    PRIMARY KEY(run_id, arm_role),
    UNIQUE(run_id, arm_id)
);

CREATE INDEX IF NOT EXISTS idx_code_benchmark_arm_execution_evidence
    ON planning_search_code_benchmark_arm_execution(evidence_digest, created_at_ms, run_id);

CREATE TABLE IF NOT EXISTS planning_search_code_benchmark_cleanup_receipt (
    run_id TEXT NOT NULL PRIMARY KEY
        REFERENCES planning_search_code_benchmark_run_spec(run_id) ON DELETE RESTRICT,
    cleanup_digest TEXT NOT NULL UNIQUE
        CHECK(length(cleanup_digest) = 64),
    cleanup_status TEXT NOT NULL
        CHECK(cleanup_status = 'VERIFIED_CLEAN'),
    cleanup_json TEXT NOT NULL
        CHECK(length(cleanup_json) BETWEEN 2 AND 131072),
    created_at_ms INTEGER NOT NULL
        CHECK(typeof(created_at_ms) = 'integer' AND created_at_ms >= 0)
);

CREATE TABLE IF NOT EXISTS planning_search_code_benchmark_receipt (
    run_id TEXT NOT NULL PRIMARY KEY
        REFERENCES planning_search_code_benchmark_run_spec(run_id) ON DELETE RESTRICT,
    receipt_digest TEXT NOT NULL UNIQUE
        CHECK(length(receipt_digest) = 64),
    run_digest TEXT NOT NULL UNIQUE
        CHECK(length(run_digest) = 64),
    baseline_evidence_digest TEXT NOT NULL
        CHECK(length(baseline_evidence_digest) = 64),
    selected_evidence_digest TEXT NOT NULL
        CHECK(length(selected_evidence_digest) = 64),
    cleanup_digest TEXT NOT NULL
        CHECK(length(cleanup_digest) = 64),
    receipt_status TEXT NOT NULL
        CHECK(receipt_status = 'COMPLETED_CLEAN'),
    claim_scope TEXT NOT NULL
        CHECK(claim_scope = 'EXPERIMENT_EVIDENCE_ONLY'),
    production_influence_enabled INTEGER NOT NULL
        CHECK(production_influence_enabled = 0),
    receipt_json TEXT NOT NULL
        CHECK(length(receipt_json) BETWEEN 2 AND 524288),
    created_at_ms INTEGER NOT NULL
        CHECK(typeof(created_at_ms) = 'integer' AND created_at_ms >= 0)
);

CREATE INDEX IF NOT EXISTS idx_code_benchmark_receipt_created
    ON planning_search_code_benchmark_receipt(created_at_ms, run_id);

CREATE TRIGGER IF NOT EXISTS code_benchmark_run_spec_registration_bound
BEFORE INSERT ON planning_search_code_benchmark_run_spec
WHEN NOT EXISTS (
    SELECT 1
    FROM planning_search_code_benchmark_workload AS workload
    WHERE workload.registration_id = NEW.registration_id
      AND workload.registration_digest = NEW.registration_digest
      AND workload.workload_digest = NEW.workload_digest
      AND workload.mode = NEW.mode
      AND workload.production_influence_enabled = NEW.production_influence_enabled
)
BEGIN
    SELECT RAISE(ABORT, 'code benchmark run spec does not bind workload registration');
END;

CREATE TRIGGER IF NOT EXISTS code_benchmark_arm_execution_bound
BEFORE INSERT ON planning_search_code_benchmark_arm_execution
WHEN NOT EXISTS (
    SELECT 1
    FROM planning_search_code_benchmark_run_spec AS spec
    INNER JOIN planning_search_code_benchmark_workload AS workload
        ON workload.registration_id = spec.registration_id
    WHERE spec.run_id = NEW.run_id
      AND (
          (NEW.arm_role = 'BASELINE'
           AND NEW.arm_id = spec.baseline_arm_id
           AND NEW.arm_digest = spec.baseline_arm_digest)
          OR
          (NEW.arm_role = 'SELECTED'
           AND NEW.arm_id = spec.selected_arm_id
           AND NEW.arm_digest = spec.selected_arm_digest)
      )
      AND NEW.source_ref = workload.source_ref
      AND NEW.source_commit = workload.source_commit
      AND NEW.path_policy_digest = workload.path_policy_digest
)
BEGIN
    SELECT RAISE(ABORT, 'code benchmark arm execution does not bind preregistered arm');
END;

CREATE TRIGGER IF NOT EXISTS code_benchmark_cleanup_receipt_pair_complete
BEFORE INSERT ON planning_search_code_benchmark_cleanup_receipt
WHEN (
    SELECT COUNT(*)
    FROM planning_search_code_benchmark_arm_execution
    WHERE run_id = NEW.run_id
) <> 2
OR NOT EXISTS (
    SELECT 1
    FROM planning_search_code_benchmark_arm_execution
    WHERE run_id = NEW.run_id AND arm_role = 'BASELINE'
)
OR NOT EXISTS (
    SELECT 1
    FROM planning_search_code_benchmark_arm_execution
    WHERE run_id = NEW.run_id AND arm_role = 'SELECTED'
)
BEGIN
    SELECT RAISE(ABORT, 'code benchmark cleanup requires both paired arm executions');
END;

CREATE TRIGGER IF NOT EXISTS code_benchmark_receipt_cleanup_bound
BEFORE INSERT ON planning_search_code_benchmark_receipt
WHEN NOT EXISTS (
    SELECT 1
    FROM planning_search_code_benchmark_cleanup_receipt AS cleanup
    WHERE cleanup.run_id = NEW.run_id
      AND cleanup.cleanup_digest = NEW.cleanup_digest
      AND cleanup.cleanup_status = 'VERIFIED_CLEAN'
)
OR NOT EXISTS (
    SELECT 1
    FROM planning_search_code_benchmark_arm_execution
    WHERE run_id = NEW.run_id
      AND arm_role = 'BASELINE'
      AND evidence_digest = NEW.baseline_evidence_digest
)
OR NOT EXISTS (
    SELECT 1
    FROM planning_search_code_benchmark_arm_execution
    WHERE run_id = NEW.run_id
      AND arm_role = 'SELECTED'
      AND evidence_digest = NEW.selected_evidence_digest
)
BEGIN
    SELECT RAISE(ABORT, 'code benchmark receipt requires cleanup-gated paired evidence');
END;

CREATE TRIGGER IF NOT EXISTS code_benchmark_workload_no_update
BEFORE UPDATE ON planning_search_code_benchmark_workload
BEGIN
    SELECT RAISE(ABORT, 'code benchmark workload is immutable');
END;

CREATE TRIGGER IF NOT EXISTS code_benchmark_workload_no_delete
BEFORE DELETE ON planning_search_code_benchmark_workload
BEGIN
    SELECT RAISE(ABORT, 'code benchmark workload is append-only');
END;

CREATE TRIGGER IF NOT EXISTS code_benchmark_run_spec_no_update
BEFORE UPDATE ON planning_search_code_benchmark_run_spec
BEGIN
    SELECT RAISE(ABORT, 'code benchmark run spec is immutable');
END;

CREATE TRIGGER IF NOT EXISTS code_benchmark_run_spec_no_delete
BEFORE DELETE ON planning_search_code_benchmark_run_spec
BEGIN
    SELECT RAISE(ABORT, 'code benchmark run spec is append-only');
END;

CREATE TRIGGER IF NOT EXISTS code_benchmark_arm_execution_no_update
BEFORE UPDATE ON planning_search_code_benchmark_arm_execution
BEGIN
    SELECT RAISE(ABORT, 'code benchmark arm execution is immutable');
END;

CREATE TRIGGER IF NOT EXISTS code_benchmark_arm_execution_no_delete
BEFORE DELETE ON planning_search_code_benchmark_arm_execution
BEGIN
    SELECT RAISE(ABORT, 'code benchmark arm execution is append-only');
END;

CREATE TRIGGER IF NOT EXISTS code_benchmark_cleanup_receipt_no_update
BEFORE UPDATE ON planning_search_code_benchmark_cleanup_receipt
BEGIN
    SELECT RAISE(ABORT, 'code benchmark cleanup receipt is immutable');
END;

CREATE TRIGGER IF NOT EXISTS code_benchmark_cleanup_receipt_no_delete
BEFORE DELETE ON planning_search_code_benchmark_cleanup_receipt
BEGIN
    SELECT RAISE(ABORT, 'code benchmark cleanup receipt is append-only');
END;

CREATE TRIGGER IF NOT EXISTS code_benchmark_receipt_no_update
BEFORE UPDATE ON planning_search_code_benchmark_receipt
BEGIN
    SELECT RAISE(ABORT, 'code benchmark receipt is immutable');
END;

CREATE TRIGGER IF NOT EXISTS code_benchmark_receipt_no_delete
BEFORE DELETE ON planning_search_code_benchmark_receipt
BEGIN
    SELECT RAISE(ABORT, 'code benchmark receipt is append-only');
END;

COMMIT;
