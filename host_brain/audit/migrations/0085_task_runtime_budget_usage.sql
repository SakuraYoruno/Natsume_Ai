BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS task_runtime_budget_usage (
    budget_id TEXT NOT NULL,
    usage_id TEXT NOT NULL,
    graph_id TEXT NOT NULL,
    graph_revision INTEGER NOT NULL CHECK(graph_revision >= 1),
    node_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    charge_kind TEXT NOT NULL CHECK(length(trim(charge_kind)) > 0),
    delta_json TEXT NOT NULL,
    delta_digest TEXT NOT NULL CHECK(length(trim(delta_digest)) > 0),
    accepted INTEGER NOT NULL CHECK(accepted IN (0, 1)),
    reason_code TEXT,
    exceeded_dimensions_json TEXT NOT NULL DEFAULT '[]',
    reservation_version_before INTEGER NOT NULL CHECK(reservation_version_before >= 0),
    reservation_version_after INTEGER NOT NULL CHECK(reservation_version_after >= 1),
    outbox_id TEXT NOT NULL,
    created_ts_ms INTEGER NOT NULL,
    PRIMARY KEY (budget_id, usage_id),
    FOREIGN KEY (budget_id)
        REFERENCES task_runtime_budget_reservation(budget_id) ON DELETE RESTRICT,
    FOREIGN KEY (attempt_id)
        REFERENCES task_runtime_attempt(attempt_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_task_runtime_budget_usage_attempt
    ON task_runtime_budget_usage(attempt_id, created_ts_ms);

COMMIT;
