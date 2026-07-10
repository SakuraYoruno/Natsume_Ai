-- TASK-0467 / LTE-WP-21: durable canonical task runtime.
-- Expand-only: AgentJobRegistry remains the default read authority until a later cutover.

CREATE TABLE IF NOT EXISTS task_runtime_graph (
    graph_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision >= 1),
    root_work_order_id TEXT NOT NULL CHECK(length(trim(root_work_order_id)) > 0),
    planner_ref TEXT NOT NULL CHECK(length(trim(planner_ref)) > 0),
    status TEXT NOT NULL CHECK(status IN (
        'CREATED', 'ACTIVE', 'BLOCKED', 'COMPLETED', 'FAILED', 'CANCELLED'
    )),
    status_version INTEGER NOT NULL DEFAULT 0 CHECK(status_version >= 0),
    graph_json TEXT NOT NULL,
    source_kind TEXT NOT NULL DEFAULT 'CANONICAL'
        CHECK(source_kind IN ('CANONICAL', 'LEGACY_AGENT_JOB')),
    source_ref TEXT,
    legacy_snapshot_json TEXT,
    created_ts_ms INTEGER NOT NULL,
    updated_ts_ms INTEGER NOT NULL,
    PRIMARY KEY (graph_id, revision),
    UNIQUE (source_kind, source_ref)
);

CREATE TABLE IF NOT EXISTS task_runtime_node (
    graph_id TEXT NOT NULL,
    graph_revision INTEGER NOT NULL CHECK(graph_revision >= 1),
    node_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN (
        'CREATED', 'BLOCKED', 'READY', 'WAITING_APPROVAL', 'LEASED', 'RUNNING',
        'EXECUTED', 'VERIFYING', 'VERIFIED', 'VERIFICATION_FAILED',
        'WAITING_ACCEPTANCE_REVIEW', 'ACCEPTED', 'FAILED', 'RETRY_PENDING',
        'CANCEL_REQUESTED', 'CANCELLED'
    )),
    status_version INTEGER NOT NULL DEFAULT 0 CHECK(status_version >= 0),
    node_json TEXT NOT NULL,
    cancellation_requested_ts_ms INTEGER,
    created_ts_ms INTEGER NOT NULL,
    updated_ts_ms INTEGER NOT NULL,
    PRIMARY KEY (graph_id, graph_revision, node_id),
    FOREIGN KEY (graph_id, graph_revision)
        REFERENCES task_runtime_graph(graph_id, revision) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS task_runtime_attempt (
    attempt_id TEXT PRIMARY KEY,
    graph_id TEXT NOT NULL,
    graph_revision INTEGER NOT NULL CHECK(graph_revision >= 1),
    node_id TEXT NOT NULL,
    attempt_no INTEGER NOT NULL CHECK(attempt_no >= 1),
    status TEXT NOT NULL CHECK(status IN (
        'CREATED', 'LEASED', 'RUNNING', 'RECOVERABLE', 'SUCCEEDED', 'FAILED',
        'CANCEL_REQUESTED', 'CANCELLED'
    )),
    status_version INTEGER NOT NULL DEFAULT 0 CHECK(status_version >= 0),
    owner_id TEXT,
    operation_scope TEXT,
    operation_fingerprint TEXT,
    created_ts_ms INTEGER NOT NULL,
    acquired_ts_ms INTEGER,
    started_ts_ms INTEGER,
    completed_ts_ms INTEGER,
    updated_ts_ms INTEGER NOT NULL,
    error_ref TEXT,
    UNIQUE (graph_id, graph_revision, node_id, attempt_no),
    FOREIGN KEY (graph_id, graph_revision, node_id)
        REFERENCES task_runtime_node(graph_id, graph_revision, node_id) ON DELETE RESTRICT,
    CHECK (
        (operation_scope IS NULL AND operation_fingerprint IS NULL)
        OR (length(trim(operation_scope)) > 0 AND length(trim(operation_fingerprint)) > 0)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_task_runtime_live_attempt
    ON task_runtime_attempt(graph_id, graph_revision, node_id)
    WHERE status IN ('CREATED', 'LEASED', 'RUNNING', 'RECOVERABLE', 'CANCEL_REQUESTED');

CREATE TABLE IF NOT EXISTS task_runtime_lease (
    lease_id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL,
    owner_id TEXT NOT NULL CHECK(length(trim(owner_id)) > 0),
    fencing_token INTEGER NOT NULL CHECK(fencing_token >= 1),
    acquired_ts_ms INTEGER NOT NULL,
    heartbeat_ts_ms INTEGER NOT NULL,
    expires_ts_ms INTEGER NOT NULL,
    released_ts_ms INTEGER,
    status TEXT NOT NULL CHECK(status IN ('ACTIVE', 'RELEASED', 'EXPIRED', 'CANCELLED')),
    status_version INTEGER NOT NULL DEFAULT 0 CHECK(status_version >= 0),
    FOREIGN KEY (attempt_id) REFERENCES task_runtime_attempt(attempt_id) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_task_runtime_active_lease
    ON task_runtime_lease(attempt_id) WHERE status = 'ACTIVE';

CREATE TABLE IF NOT EXISTS task_runtime_heartbeat (
    heartbeat_id TEXT PRIMARY KEY,
    lease_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    fencing_token INTEGER NOT NULL CHECK(fencing_token >= 1),
    observed_ts_ms INTEGER NOT NULL,
    expires_ts_ms INTEGER NOT NULL,
    FOREIGN KEY (lease_id) REFERENCES task_runtime_lease(lease_id) ON DELETE RESTRICT,
    FOREIGN KEY (attempt_id) REFERENCES task_runtime_attempt(attempt_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS task_runtime_idempotency_reservation (
    reservation_id TEXT PRIMARY KEY,
    business_scope TEXT NOT NULL CHECK(length(trim(business_scope)) > 0),
    operation_fingerprint TEXT NOT NULL CHECK(length(trim(operation_fingerprint)) > 0),
    attempt_id TEXT,
    status TEXT NOT NULL CHECK(status IN ('RESERVED', 'COMMITTED', 'RELEASED', 'CANCELLED')),
    status_version INTEGER NOT NULL DEFAULT 0 CHECK(status_version >= 0),
    receipt_ref TEXT,
    created_ts_ms INTEGER NOT NULL,
    updated_ts_ms INTEGER NOT NULL,
    UNIQUE (business_scope, operation_fingerprint),
    FOREIGN KEY (attempt_id) REFERENCES task_runtime_attempt(attempt_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS task_runtime_budget_reservation (
    budget_id TEXT PRIMARY KEY,
    scope TEXT NOT NULL CHECK(length(trim(scope)) > 0),
    graph_id TEXT,
    graph_revision INTEGER,
    node_id TEXT,
    attempt_id TEXT,
    limits_json TEXT NOT NULL,
    reserved_json TEXT NOT NULL,
    consumed_json TEXT NOT NULL,
    attempted_consumed_json TEXT NOT NULL,
    exceeded_dimensions_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL CHECK(status IN (
        'PENDING', 'ACTIVE', 'CONSUMED', 'RELEASED', 'EXCEEDED', 'CANCELLED'
    )),
    status_version INTEGER NOT NULL DEFAULT 0 CHECK(status_version >= 0),
    created_ts_ms INTEGER NOT NULL,
    updated_ts_ms INTEGER NOT NULL,
    FOREIGN KEY (attempt_id) REFERENCES task_runtime_attempt(attempt_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS task_runtime_outbox (
    outbox_id TEXT PRIMARY KEY,
    aggregate_type TEXT NOT NULL CHECK(length(trim(aggregate_type)) > 0),
    aggregate_id TEXT NOT NULL CHECK(length(trim(aggregate_id)) > 0),
    aggregate_version INTEGER NOT NULL CHECK(aggregate_version >= 0),
    event_type TEXT NOT NULL CHECK(length(trim(event_type)) > 0),
    payload_ref TEXT NOT NULL CHECK(length(trim(payload_ref)) > 0),
    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
    attempt_id TEXT,
    idempotency_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK(status IN ('PENDING', 'PUBLISHED', 'CANCELLED')),
    available_ts_ms INTEGER NOT NULL,
    created_ts_ms INTEGER NOT NULL,
    published_ts_ms INTEGER,
    status_version INTEGER NOT NULL DEFAULT 0 CHECK(status_version >= 0),
    FOREIGN KEY (attempt_id) REFERENCES task_runtime_attempt(attempt_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_task_runtime_node_status
    ON task_runtime_node(status, updated_ts_ms);
CREATE INDEX IF NOT EXISTS idx_task_runtime_attempt_node
    ON task_runtime_attempt(graph_id, graph_revision, node_id, attempt_no);
CREATE INDEX IF NOT EXISTS idx_task_runtime_attempt_recovery
    ON task_runtime_attempt(status, updated_ts_ms);
CREATE INDEX IF NOT EXISTS idx_task_runtime_lease_expiry
    ON task_runtime_lease(status, expires_ts_ms);
CREATE INDEX IF NOT EXISTS idx_task_runtime_heartbeat_attempt
    ON task_runtime_heartbeat(attempt_id, observed_ts_ms);
CREATE INDEX IF NOT EXISTS idx_task_runtime_budget_scope
    ON task_runtime_budget_reservation(scope, status);
CREATE INDEX IF NOT EXISTS idx_task_runtime_outbox_pending
    ON task_runtime_outbox(status, available_ts_ms, created_ts_ms);
