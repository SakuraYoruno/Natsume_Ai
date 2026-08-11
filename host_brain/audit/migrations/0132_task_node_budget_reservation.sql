-- NE-01: attempt-bound child BudgetReservation and immutable usage lineage.
--
-- The existing task_runtime_budget_reservation remains the only graph-wide
-- ceiling.  Every Attempt receives one child envelope inside the same claim
-- transaction; every charge mutates the parent and child ledgers together.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS task_runtime_node_budget_reservation (
    node_budget_id TEXT PRIMARY KEY CHECK(length(trim(node_budget_id)) > 0),
    schema_version TEXT NOT NULL
        CHECK(schema_version = 'Natsume.task_runtime.node_budget_reservation.v1'),
    parent_budget_id TEXT NOT NULL,
    graph_id TEXT NOT NULL,
    graph_revision INTEGER NOT NULL CHECK(graph_revision >= 1),
    node_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL UNIQUE,
    scope TEXT NOT NULL CHECK(length(trim(scope)) > 0),
    limits_json TEXT NOT NULL CHECK(json_valid(limits_json)),
    reserved_json TEXT NOT NULL CHECK(json_valid(reserved_json)),
    consumed_json TEXT NOT NULL CHECK(json_valid(consumed_json)),
    attempted_consumed_json TEXT NOT NULL CHECK(json_valid(attempted_consumed_json)),
    exceeded_dimensions_json TEXT NOT NULL DEFAULT '[]'
        CHECK(json_valid(exceeded_dimensions_json)),
    usage_version INTEGER NOT NULL DEFAULT 0 CHECK(usage_version >= 0),
    parent_version_at_claim INTEGER NOT NULL CHECK(parent_version_at_claim >= 0),
    claim_outbox_id TEXT,
    claim_origin TEXT NOT NULL
        CHECK(claim_origin IN ('ATOMIC_READY_CLAIM', 'LEGACY_CREATE_ATTEMPT', 'BACKFILL_0132')),
    created_ts_ms INTEGER NOT NULL CHECK(created_ts_ms >= 0),
    updated_ts_ms INTEGER NOT NULL CHECK(updated_ts_ms >= created_ts_ms),
    UNIQUE(node_budget_id, parent_budget_id),
    UNIQUE(parent_budget_id, graph_id, graph_revision, node_id, attempt_id),
    FOREIGN KEY(parent_budget_id, graph_id, graph_revision)
        REFERENCES task_runtime_budget_reservation(
            budget_id, graph_id, graph_revision
        ) ON DELETE RESTRICT,
    FOREIGN KEY(attempt_id, graph_id, graph_revision, node_id)
        REFERENCES task_runtime_attempt(
            attempt_id, graph_id, graph_revision, node_id
        ) ON DELETE RESTRICT,
    FOREIGN KEY(claim_outbox_id)
        REFERENCES task_runtime_outbox(outbox_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS task_runtime_node_budget_usage (
    node_budget_id TEXT NOT NULL,
    parent_budget_id TEXT NOT NULL,
    usage_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    accepted INTEGER NOT NULL CHECK(accepted IN (0, 1)),
    child_version_before INTEGER NOT NULL CHECK(child_version_before >= 0),
    child_version_after INTEGER NOT NULL CHECK(child_version_after >= child_version_before),
    parent_outbox_id TEXT NOT NULL,
    created_ts_ms INTEGER NOT NULL CHECK(created_ts_ms >= 0),
    PRIMARY KEY(node_budget_id, usage_id),
    UNIQUE(parent_budget_id, usage_id),
    FOREIGN KEY(node_budget_id, parent_budget_id)
        REFERENCES task_runtime_node_budget_reservation(
            node_budget_id, parent_budget_id
        ) ON DELETE RESTRICT,
    FOREIGN KEY(parent_budget_id, usage_id)
        REFERENCES task_runtime_budget_usage(
            budget_id, usage_id
        ) ON DELETE RESTRICT,
    FOREIGN KEY(attempt_id)
        REFERENCES task_runtime_attempt(attempt_id) ON DELETE RESTRICT,
    FOREIGN KEY(parent_outbox_id)
        REFERENCES task_runtime_outbox(outbox_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_task_runtime_node_budget_graph
    ON task_runtime_node_budget_reservation(
        graph_id, graph_revision, node_id, attempt_id
    );

CREATE INDEX IF NOT EXISTS idx_task_runtime_node_budget_parent
    ON task_runtime_node_budget_reservation(parent_budget_id, usage_version);

CREATE INDEX IF NOT EXISTS idx_task_runtime_node_budget_usage_attempt
    ON task_runtime_node_budget_usage(attempt_id, created_ts_ms, usage_id);

-- Existing attempts receive a conservative child envelope.  Existing usage is
-- folded into the child projection below, so an in-flight graph cannot regain
-- already consumed capacity after upgrade.
INSERT INTO task_runtime_node_budget_reservation(
    node_budget_id, schema_version, parent_budget_id, graph_id, graph_revision,
    node_id, attempt_id, scope, limits_json, reserved_json, consumed_json,
    attempted_consumed_json, exceeded_dimensions_json, usage_version,
    parent_version_at_claim, claim_outbox_id, claim_origin,
    created_ts_ms, updated_ts_ms
)
SELECT
    'node-budget::' || natsume_sha256_hex(
        budget.budget_id || char(10) || attempt.node_id
        || char(10) || attempt.attempt_id
    ),
    'Natsume.task_runtime.node_budget_reservation.v1',
    budget.budget_id,
    attempt.graph_id,
    attempt.graph_revision,
    attempt.node_id,
    attempt.attempt_id,
    budget.scope,
    budget.limits_json,
    budget.reserved_json,
    json_object(
        'token_count', COALESCE(SUM(
            CASE WHEN usage.accepted = 1
                THEN CAST(json_extract(usage.delta_json, '$.token_count') AS INTEGER)
                ELSE 0 END
        ), 0),
        'tool_call_count', COALESCE(SUM(
            CASE WHEN usage.accepted = 1
                THEN CAST(json_extract(usage.delta_json, '$.tool_call_count') AS INTEGER)
                ELSE 0 END
        ), 0),
        'time_ms', COALESCE(SUM(
            CASE WHEN usage.accepted = 1
                THEN CAST(json_extract(usage.delta_json, '$.time_ms') AS INTEGER)
                ELSE 0 END
        ), 0),
        'concurrency', COALESCE(SUM(
            CASE WHEN usage.accepted = 1
                THEN CAST(json_extract(usage.delta_json, '$.concurrency') AS INTEGER)
                ELSE 0 END
        ), 0),
        'retry_count', COALESCE(SUM(
            CASE WHEN usage.accepted = 1
                THEN CAST(json_extract(usage.delta_json, '$.retry_count') AS INTEGER)
                ELSE 0 END
        ), 0),
        'model_cost_micros', COALESCE(SUM(
            CASE WHEN usage.accepted = 1
                THEN CAST(json_extract(usage.delta_json, '$.model_cost_micros') AS INTEGER)
                ELSE 0 END
        ), 0)
    ),
    json_object(
        'token_count', COALESCE(SUM(CAST(json_extract(usage.delta_json, '$.token_count') AS INTEGER)), 0),
        'tool_call_count', COALESCE(SUM(CAST(json_extract(usage.delta_json, '$.tool_call_count') AS INTEGER)), 0),
        'time_ms', COALESCE(SUM(CAST(json_extract(usage.delta_json, '$.time_ms') AS INTEGER)), 0),
        'concurrency', COALESCE(SUM(CAST(json_extract(usage.delta_json, '$.concurrency') AS INTEGER)), 0),
        'retry_count', COALESCE(SUM(CAST(json_extract(usage.delta_json, '$.retry_count') AS INTEGER)), 0),
        'model_cost_micros', COALESCE(SUM(CAST(json_extract(usage.delta_json, '$.model_cost_micros') AS INTEGER)), 0)
    ),
    '[]',
    COUNT(usage.usage_id),
    0,
    (
        SELECT outbox.outbox_id
        FROM task_runtime_outbox AS outbox
        WHERE outbox.aggregate_type = 'TASK_ATTEMPT'
          AND outbox.aggregate_id = attempt.attempt_id
          AND outbox.aggregate_version = 0
        ORDER BY outbox.created_ts_ms, outbox.outbox_id
        LIMIT 1
    ),
    'BACKFILL_0132',
    attempt.created_ts_ms,
    MAX(attempt.updated_ts_ms, COALESCE(MAX(usage.created_ts_ms), attempt.updated_ts_ms))
FROM task_runtime_attempt AS attempt
JOIN task_runtime_budget_reservation AS budget
  ON budget.graph_id = attempt.graph_id
 AND budget.graph_revision = attempt.graph_revision
 AND budget.node_id IS NULL
 AND budget.attempt_id IS NULL
LEFT JOIN task_runtime_budget_usage AS usage
  ON usage.budget_id = budget.budget_id
 AND usage.attempt_id = attempt.attempt_id
GROUP BY attempt.attempt_id, budget.budget_id
ON CONFLICT(attempt_id) DO NOTHING;

INSERT INTO task_runtime_node_budget_usage(
    node_budget_id, parent_budget_id, usage_id, attempt_id, accepted,
    child_version_before, child_version_after, parent_outbox_id, created_ts_ms
)
SELECT
    child.node_budget_id,
    usage.budget_id,
    usage.usage_id,
    usage.attempt_id,
    usage.accepted,
    ROW_NUMBER() OVER (
        PARTITION BY usage.attempt_id
        ORDER BY usage.created_ts_ms, usage.usage_id
    ) - 1,
    ROW_NUMBER() OVER (
        PARTITION BY usage.attempt_id
        ORDER BY usage.created_ts_ms, usage.usage_id
    ),
    usage.outbox_id,
    usage.created_ts_ms
FROM task_runtime_budget_usage AS usage
JOIN task_runtime_node_budget_reservation AS child
  ON child.parent_budget_id = usage.budget_id
 AND child.attempt_id = usage.attempt_id
ON CONFLICT(parent_budget_id, usage_id) DO NOTHING;

CREATE TRIGGER IF NOT EXISTS trg_task_runtime_node_budget_identity_update
BEFORE UPDATE ON task_runtime_node_budget_reservation
FOR EACH ROW
WHEN NEW.node_budget_id <> OLD.node_budget_id
  OR NEW.schema_version <> OLD.schema_version
  OR NEW.parent_budget_id <> OLD.parent_budget_id
  OR NEW.graph_id <> OLD.graph_id
  OR NEW.graph_revision <> OLD.graph_revision
  OR NEW.node_id <> OLD.node_id
  OR NEW.attempt_id <> OLD.attempt_id
  OR NEW.scope <> OLD.scope
  OR NEW.limits_json <> OLD.limits_json
  OR NEW.reserved_json <> OLD.reserved_json
  OR NEW.parent_version_at_claim <> OLD.parent_version_at_claim
  OR COALESCE(NEW.claim_outbox_id, '') <> COALESCE(OLD.claim_outbox_id, '')
  OR NEW.claim_origin <> OLD.claim_origin
  OR NEW.created_ts_ms <> OLD.created_ts_ms
BEGIN
    SELECT RAISE(ABORT, 'task node budget reservation identity is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_task_runtime_node_budget_no_delete
BEFORE DELETE ON task_runtime_node_budget_reservation
BEGIN
    SELECT RAISE(ABORT, 'task node budget reservation cannot be deleted');
END;

CREATE TRIGGER IF NOT EXISTS trg_task_runtime_node_budget_usage_no_update
BEFORE UPDATE ON task_runtime_node_budget_usage
BEGIN
    SELECT RAISE(ABORT, 'task node budget usage is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_task_runtime_node_budget_usage_no_delete
BEFORE DELETE ON task_runtime_node_budget_usage
BEGIN
    SELECT RAISE(ABORT, 'task node budget usage is append-only');
END;

COMMIT;
