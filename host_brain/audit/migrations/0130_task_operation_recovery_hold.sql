-- NE-01: append-only authority for nonterminal Operation recovery holds.
--
-- A hold never claims that an external side effect did or did not happen.
-- It freezes the exact Graph/Node/Attempt/Operation/Budget/cancellation
-- observation that forced the runtime to stop, and emits one durable Outbox
-- event in the same transaction.  Signed reconciliation remains the only
-- authority that may later COMMIT, RELEASE, or REACQUIRE the Operation.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS task_runtime_operation_recovery_hold (
    case_ref TEXT PRIMARY KEY CHECK(
        length(case_ref) = 97
        AND substr(case_ref, 1, 33) = 'operation-recovery-hold://sha256/'
        AND substr(case_ref, 34) NOT GLOB '*[^0-9a-f]*'
    ),
    schema_version TEXT NOT NULL CHECK(
        schema_version = 'Natsume.task.operation_recovery_hold.v1'
    ),
    case_kind TEXT NOT NULL CHECK(case_kind IN (
        'UNKNOWN_SIDE_EFFECT',
        'CANCELLATION_CROSSING',
        'CHECKPOINT_BUDGET_CONFLICT',
        'CHECKPOINT_AUTHORITY_CONFLICT'
    )),
    classification TEXT NOT NULL CHECK(classification IN (
        'SIGNED_OPERATION_RECONCILIATION_REQUIRED',
        'SIGNED_CANCELLATION_RECONCILIATION_REQUIRED',
        'QUARANTINED_CHECKPOINT_CONFLICT'
    )),
    reason_code TEXT NOT NULL CHECK(
        length(reason_code) BETWEEN 1 AND 120
        AND reason_code NOT GLOB '*[^A-Z0-9_]*'
    ),
    checkpoint_state TEXT CHECK(
        checkpoint_state IS NULL OR checkpoint_state IN ('CONFLICT', 'INVALID')
    ),

    graph_id TEXT NOT NULL,
    graph_revision INTEGER NOT NULL CHECK(graph_revision >= 1),
    graph_status TEXT NOT NULL CHECK(graph_status IN (
        'CREATED', 'ACTIVE', 'BLOCKED', 'COMPLETED', 'FAILED', 'CANCELLED'
    )),
    graph_status_version INTEGER NOT NULL CHECK(graph_status_version >= 0),
    node_id TEXT NOT NULL,
    node_status TEXT NOT NULL,
    node_status_version INTEGER NOT NULL CHECK(node_status_version >= 0),
    attempt_id TEXT NOT NULL,
    attempt_status TEXT NOT NULL,
    attempt_status_version INTEGER NOT NULL CHECK(attempt_status_version >= 0),
    reservation_id TEXT NOT NULL,
    operation_status TEXT NOT NULL CHECK(operation_status IN (
        'RESERVED', 'RECOVERY_REQUIRED', 'COMMITTED'
    )),
    operation_status_version INTEGER NOT NULL CHECK(operation_status_version >= 0),
    budget_id TEXT NOT NULL,
    budget_status TEXT NOT NULL CHECK(budget_status IN (
        'ACTIVE', 'EXCEEDED'
    )),
    budget_status_version INTEGER NOT NULL CHECK(budget_status_version >= 0),
    cancellation_intent_ref TEXT,
    cancellation_status TEXT CHECK(
        cancellation_status IS NULL OR cancellation_status IN (
            'REQUESTED', 'FINALIZED'
        )
    ),
    cancellation_status_version INTEGER CHECK(
        cancellation_status_version IS NULL
        OR cancellation_status_version >= 0
    ),

    subject_json TEXT NOT NULL CHECK(
        length(subject_json) BETWEEN 2 AND 65536
        AND json_valid(subject_json)
        AND json_type(subject_json) = 'object'
    ),
    subject_sha256 TEXT NOT NULL CHECK(
        length(subject_sha256) = 71
        AND substr(subject_sha256, 1, 7) = 'sha256:'
        AND substr(subject_sha256, 8) NOT GLOB '*[^0-9a-f]*'
    ),
    outbox_id TEXT NOT NULL UNIQUE,
    created_ts_ms INTEGER NOT NULL CHECK(created_ts_ms >= 0),

    UNIQUE(
        reservation_id,
        operation_status_version,
        case_kind,
        subject_sha256
    ),
    FOREIGN KEY(graph_id, graph_revision)
        REFERENCES task_runtime_graph(graph_id, revision) ON DELETE RESTRICT,
    FOREIGN KEY(graph_id, graph_revision, node_id)
        REFERENCES task_runtime_node(
            graph_id, graph_revision, node_id
        ) ON DELETE RESTRICT,
    FOREIGN KEY(attempt_id, graph_id, graph_revision, node_id)
        REFERENCES task_runtime_attempt(
            attempt_id, graph_id, graph_revision, node_id
        ) ON DELETE RESTRICT,
    FOREIGN KEY(reservation_id)
        REFERENCES task_runtime_idempotency_reservation(
            reservation_id
        ) ON DELETE RESTRICT,
    FOREIGN KEY(budget_id, graph_id, graph_revision)
        REFERENCES task_runtime_budget_reservation(
            budget_id, graph_id, graph_revision
        ) ON DELETE RESTRICT,
    FOREIGN KEY(cancellation_intent_ref, graph_id, graph_revision)
        REFERENCES task_runtime_graph_cancellation_intent(
            intent_ref, graph_id, graph_revision
        ) ON DELETE RESTRICT,
    FOREIGN KEY(outbox_id)
        REFERENCES task_runtime_outbox(outbox_id) ON DELETE RESTRICT,

    CHECK(
        (cancellation_intent_ref IS NULL
            AND cancellation_status IS NULL
            AND cancellation_status_version IS NULL)
        OR
        (cancellation_intent_ref IS NOT NULL
            AND cancellation_status IS NOT NULL
            AND cancellation_status_version IS NOT NULL)
    ),
    CHECK(
        (case_kind = 'UNKNOWN_SIDE_EFFECT'
            AND classification =
                'SIGNED_OPERATION_RECONCILIATION_REQUIRED'
            AND checkpoint_state IS NULL
            AND operation_status IN ('RESERVED', 'RECOVERY_REQUIRED')
            AND cancellation_intent_ref IS NULL)
        OR
        (case_kind = 'CANCELLATION_CROSSING'
            AND classification =
                'SIGNED_CANCELLATION_RECONCILIATION_REQUIRED'
            AND checkpoint_state IS NULL
            AND operation_status IN ('RESERVED', 'RECOVERY_REQUIRED')
            AND cancellation_intent_ref IS NOT NULL)
        OR
        (case_kind = 'CHECKPOINT_BUDGET_CONFLICT'
            AND classification = 'QUARANTINED_CHECKPOINT_CONFLICT'
            AND checkpoint_state = 'CONFLICT'
            AND operation_status = 'COMMITTED')
        OR
        (case_kind = 'CHECKPOINT_AUTHORITY_CONFLICT'
            AND classification = 'QUARANTINED_CHECKPOINT_CONFLICT'
            AND checkpoint_state = 'INVALID'
            AND operation_status = 'COMMITTED')
    )
);

CREATE INDEX IF NOT EXISTS idx_task_operation_recovery_hold_current
    ON task_runtime_operation_recovery_hold(
        graph_id,
        graph_revision,
        reservation_id,
        operation_status_version,
        budget_status_version,
        created_ts_ms,
        case_ref
    );

CREATE INDEX IF NOT EXISTS idx_task_operation_recovery_hold_classification
    ON task_runtime_operation_recovery_hold(
        classification,
        case_kind,
        created_ts_ms,
        case_ref
    );

CREATE TRIGGER IF NOT EXISTS trg_task_operation_recovery_hold_exact_insert
BEFORE INSERT ON task_runtime_operation_recovery_hold
WHEN
    NOT EXISTS (
        SELECT 1 FROM task_runtime_graph AS graph
        WHERE graph.graph_id = NEW.graph_id
          AND graph.revision = NEW.graph_revision
          AND graph.status = NEW.graph_status
          AND graph.status_version = NEW.graph_status_version
    )
    OR NOT EXISTS (
        SELECT 1 FROM task_runtime_node AS node
        WHERE node.graph_id = NEW.graph_id
          AND node.graph_revision = NEW.graph_revision
          AND node.node_id = NEW.node_id
          AND node.status = NEW.node_status
          AND node.status_version = NEW.node_status_version
    )
    OR NOT EXISTS (
        SELECT 1 FROM task_runtime_attempt AS attempt
        WHERE attempt.attempt_id = NEW.attempt_id
          AND attempt.graph_id = NEW.graph_id
          AND attempt.graph_revision = NEW.graph_revision
          AND attempt.node_id = NEW.node_id
          AND attempt.status = NEW.attempt_status
          AND attempt.status_version = NEW.attempt_status_version
    )
    OR NOT EXISTS (
        SELECT 1 FROM task_runtime_idempotency_reservation AS operation
        WHERE operation.reservation_id = NEW.reservation_id
          AND operation.attempt_id = NEW.attempt_id
          AND operation.status = NEW.operation_status
          AND operation.status_version = NEW.operation_status_version
    )
    OR NOT EXISTS (
        SELECT 1 FROM task_runtime_budget_reservation AS budget
        WHERE budget.budget_id = NEW.budget_id
          AND budget.graph_id = NEW.graph_id
          AND budget.graph_revision = NEW.graph_revision
          AND budget.node_id IS NULL
          AND budget.attempt_id IS NULL
          AND budget.status = NEW.budget_status
          AND budget.status_version = NEW.budget_status_version
    )
    OR (
        NEW.cancellation_intent_ref IS NULL
        AND EXISTS (
            SELECT 1 FROM task_runtime_graph_cancellation_intent AS intent
            WHERE intent.graph_id = NEW.graph_id
              AND intent.graph_revision = NEW.graph_revision
        )
    )
    OR (
        NEW.cancellation_intent_ref IS NOT NULL
        AND NOT EXISTS (
            SELECT 1 FROM task_runtime_graph_cancellation_intent AS intent
            WHERE intent.intent_ref = NEW.cancellation_intent_ref
              AND intent.graph_id = NEW.graph_id
              AND intent.graph_revision = NEW.graph_revision
              AND intent.status = NEW.cancellation_status
              AND intent.status_version = NEW.cancellation_status_version
        )
    )
    OR NOT EXISTS (
        SELECT 1 FROM task_runtime_outbox AS outbox
        WHERE outbox.outbox_id = NEW.outbox_id
          AND outbox.aggregate_type = 'OPERATION_RECOVERY_HOLD'
          AND outbox.aggregate_id = NEW.case_ref
          AND outbox.aggregate_version = 1
          AND outbox.event_type = 'OPERATION_RECOVERY_HOLD_RECORDED'
          AND outbox.attempt_id = NEW.attempt_id
    )
BEGIN
    SELECT RAISE(ABORT, 'TASK_OPERATION_RECOVERY_HOLD_BINDING_DRIFT');
END;

CREATE TRIGGER IF NOT EXISTS trg_task_operation_recovery_hold_no_update
BEFORE UPDATE ON task_runtime_operation_recovery_hold
BEGIN
    SELECT RAISE(ABORT, 'TASK_OPERATION_RECOVERY_HOLD_IMMUTABLE');
END;

CREATE TRIGGER IF NOT EXISTS trg_task_operation_recovery_hold_no_delete
BEFORE DELETE ON task_runtime_operation_recovery_hold
BEGIN
    SELECT RAISE(ABORT, 'TASK_OPERATION_RECOVERY_HOLD_IMMUTABLE');
END;

COMMIT;
