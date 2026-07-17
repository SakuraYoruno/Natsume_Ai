-- D13: immutable, domain-neutral shadow-planning calibration evidence.
-- A prediction records what the planner simulated; an outcome records an
-- observed baseline action.  Neither table is a promotion authority.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS planning_search_calibration_prediction (
    prediction_id TEXT NOT NULL PRIMARY KEY
        CHECK(length(trim(prediction_id)) BETWEEN 16 AND 256),
    identity_digest TEXT NOT NULL UNIQUE
        CHECK(length(identity_digest) = 64),
    domain TEXT NOT NULL
        CHECK(length(trim(domain)) BETWEEN 1 AND 128),
    scope_key TEXT NOT NULL
        CHECK(length(trim(scope_key)) BETWEEN 1 AND 512),
    request_id TEXT NOT NULL
        CHECK(length(trim(request_id)) BETWEEN 1 AND 512),
    state_schema TEXT NOT NULL
        CHECK(length(trim(state_schema)) BETWEEN 1 AND 256),
    state_digest TEXT NOT NULL
        CHECK(length(state_digest) = 64),
    baseline_candidate_id TEXT NOT NULL
        CHECK(length(trim(baseline_candidate_id)) BETWEEN 1 AND 512),
    executed_candidate_id TEXT NOT NULL
        CHECK(length(trim(executed_candidate_id)) BETWEEN 1 AND 512),
    executed_path_json TEXT NOT NULL,
    shadow_recommended_candidate_id TEXT NOT NULL
        CHECK(length(trim(shadow_recommended_candidate_id)) BETWEEN 1 AND 512),
    shadow_path_json TEXT NOT NULL,
    search_trace_digest TEXT NOT NULL
        CHECK(length(search_trace_digest) = 64),
    search_budget_json TEXT NOT NULL,
    search_cost_json TEXT NOT NULL,
    adapter_revision TEXT NOT NULL
        CHECK(length(trim(adapter_revision)) BETWEEN 1 AND 256),
    predicted_reward REAL NOT NULL
        CHECK(predicted_reward >= 0.0 AND predicted_reward <= 1.0),
    mode TEXT NOT NULL
        CHECK(mode = 'SHADOW_ONLY'),
    created_at_ms INTEGER NOT NULL
        CHECK(typeof(created_at_ms) = 'integer' AND created_at_ms >= 0),
    UNIQUE(domain, scope_key, request_id, executed_candidate_id)
);

CREATE INDEX IF NOT EXISTS idx_planning_calibration_prediction_domain_created
    ON planning_search_calibration_prediction(domain, created_at_ms, prediction_id);

CREATE INDEX IF NOT EXISTS idx_planning_calibration_prediction_scope_created
    ON planning_search_calibration_prediction(domain, scope_key, created_at_ms, prediction_id);

CREATE TABLE IF NOT EXISTS planning_search_calibration_outcome (
    prediction_id TEXT NOT NULL PRIMARY KEY
        REFERENCES planning_search_calibration_prediction(prediction_id) ON DELETE RESTRICT,
    executed_candidate_id TEXT NOT NULL
        CHECK(length(trim(executed_candidate_id)) BETWEEN 1 AND 512),
    actual_reward REAL NOT NULL
        CHECK(actual_reward >= 0.0 AND actual_reward <= 1.0),
    outcome_ref TEXT NOT NULL
        CHECK(length(trim(outcome_ref)) BETWEEN 1 AND 1024),
    verifier_refs_json TEXT NOT NULL,
    outcome_snapshot_json TEXT NOT NULL,
    outcome_digest TEXT NOT NULL
        CHECK(length(outcome_digest) = 64),
    reconciled_at_ms INTEGER NOT NULL
        CHECK(typeof(reconciled_at_ms) = 'integer' AND reconciled_at_ms >= 0)
);

CREATE INDEX IF NOT EXISTS idx_planning_calibration_outcome_reconciled
    ON planning_search_calibration_outcome(reconciled_at_ms, prediction_id);

CREATE TRIGGER IF NOT EXISTS planning_calibration_prediction_no_update
BEFORE UPDATE ON planning_search_calibration_prediction
BEGIN
    SELECT RAISE(ABORT, 'planning calibration prediction is immutable');
END;

CREATE TRIGGER IF NOT EXISTS planning_calibration_prediction_no_delete
BEFORE DELETE ON planning_search_calibration_prediction
BEGIN
    SELECT RAISE(ABORT, 'planning calibration prediction is append-only');
END;

CREATE TRIGGER IF NOT EXISTS planning_calibration_outcome_no_update
BEFORE UPDATE ON planning_search_calibration_outcome
BEGIN
    SELECT RAISE(ABORT, 'planning calibration outcome is immutable');
END;

CREATE TRIGGER IF NOT EXISTS planning_calibration_outcome_no_delete
BEFORE DELETE ON planning_search_calibration_outcome
BEGIN
    SELECT RAISE(ABORT, 'planning calibration outcome is append-only');
END;

CREATE TRIGGER IF NOT EXISTS planning_calibration_outcome_candidate_bound
BEFORE INSERT ON planning_search_calibration_outcome
WHEN NOT EXISTS (
    SELECT 1
    FROM planning_search_calibration_prediction
    WHERE prediction_id = NEW.prediction_id
      AND executed_candidate_id = NEW.executed_candidate_id
)
BEGIN
    SELECT RAISE(ABORT, 'planning calibration outcome candidate does not match prediction');
END;

COMMIT;
