-- NE-01: append-only historical TaskGraph terminal reconciliation sidecar.
--
-- This migration is deliberately schema-only.  It does not inventory ACTIVE
-- graphs, scan any Task Runtime authority table, backfill cases, or rewrite
-- TaskGraph, BudgetReservation, 0090 decisions, or 0092 cancellation facts.
-- A later governed service may freeze an exact terminal snapshot as a case and
-- append one evidence proof in the same UoW as its action Outbox(es).

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS task_runtime_terminal_reconciliation_case (
    case_id TEXT NOT NULL PRIMARY KEY
        CHECK(length(trim(case_id)) BETWEEN 1 AND 512),
    schema_version TEXT NOT NULL CHECK(
        schema_version = 'Natsume.task_runtime.terminal_reconciliation.case.v1'
    ),
    case_kind TEXT NOT NULL CHECK(case_kind IN (
        'FAILED_UNSETTLED_BUDGET',
        'BLOCKED_FAILURE_NODE',
        'CANCELLED_UNSETTLED_BUDGET',
        'COMPLETED_INCONSISTENT',
        'TERMINAL_STRUCTURAL_ANOMALY',
        'TERMINAL_HEALTHY_CLOSURE'
    )),
    classification TEXT NOT NULL CHECK(classification IN (
        'EXACT_REPAIRABLE',
        'SIGNED_REPAIR_REQUIRED',
        'QUARANTINED_STRUCTURAL'
    )),
    reason_code TEXT NOT NULL
        CHECK(length(trim(reason_code)) BETWEEN 1 AND 255),

    graph_id TEXT NOT NULL,
    graph_revision INTEGER NOT NULL CHECK(
        typeof(graph_revision) = 'integer' AND graph_revision >= 1
    ),
    graph_status TEXT NOT NULL CHECK(graph_status IN (
        'BLOCKED', 'COMPLETED', 'FAILED', 'CANCELLED'
    )),
    graph_status_version INTEGER NOT NULL CHECK(
        typeof(graph_status_version) = 'integer'
        AND graph_status_version >= 0
    ),

    budget_id TEXT,
    budget_status TEXT CHECK(
        budget_status IS NULL OR budget_status IN (
            'PENDING', 'ACTIVE', 'CONSUMED',
            'RELEASED', 'EXCEEDED', 'CANCELLED'
        )
    ),
    budget_status_version INTEGER CHECK(
        budget_status_version IS NULL
        OR (
            typeof(budget_status_version) = 'integer'
            AND budget_status_version >= 0
        )
    ),

    node_id TEXT,
    node_status TEXT CHECK(
        node_status IS NULL OR node_status IN (
            'CREATED', 'BLOCKED', 'READY', 'WAITING_APPROVAL',
            'LEASED', 'RUNNING', 'EXECUTED', 'VERIFYING', 'VERIFIED',
            'VERIFICATION_FAILED', 'WAITING_ACCEPTANCE_REVIEW',
            'ACCEPTED', 'FAILED', 'RETRY_PENDING',
            'CANCEL_REQUESTED', 'CANCELLED'
        )
    ),
    node_status_version INTEGER CHECK(
        node_status_version IS NULL
        OR (
            typeof(node_status_version) = 'integer'
            AND node_status_version >= 0
        )
    ),

    attempt_id TEXT,
    attempt_status TEXT CHECK(
        attempt_status IS NULL OR attempt_status IN (
            'CREATED', 'LEASED', 'RUNNING', 'RECOVERABLE',
            'SUCCEEDED', 'FAILED', 'CANCEL_REQUESTED', 'CANCELLED'
        )
    ),
    attempt_status_version INTEGER CHECK(
        attempt_status_version IS NULL
        OR (
            typeof(attempt_status_version) = 'integer'
            AND attempt_status_version >= 0
        )
    ),

    recommended_action TEXT NOT NULL CHECK(recommended_action IN (
        'RELEASE_BUDGET',
        'FAIL_GRAPH',
        'FAIL_GRAPH_AND_RELEASE_BUDGET',
        'NONE'
    )),
    snapshot_json TEXT NOT NULL CHECK(
        length(snapshot_json) BETWEEN 2 AND 262144
        AND json_valid(snapshot_json)
        AND json_type(snapshot_json) = 'object'
    ),
    snapshot_sha256 TEXT NOT NULL CHECK(
        length(snapshot_sha256) = 71
        AND substr(snapshot_sha256, 1, 7) = 'sha256:'
        AND substr(snapshot_sha256, 8) NOT GLOB '*[^0-9a-f]*'
    ),
    created_ts_ms INTEGER NOT NULL CHECK(
        typeof(created_ts_ms) = 'integer' AND created_ts_ms >= 0
    ),

    UNIQUE(graph_id, graph_revision, case_kind, snapshot_sha256),
    FOREIGN KEY (graph_id, graph_revision)
        REFERENCES task_runtime_graph(graph_id, revision) ON DELETE RESTRICT,
    FOREIGN KEY (budget_id)
        REFERENCES task_runtime_budget_reservation(budget_id) ON DELETE RESTRICT,
    FOREIGN KEY (graph_id, graph_revision, node_id)
        REFERENCES task_runtime_node(
            graph_id, graph_revision, node_id
        ) ON DELETE RESTRICT,
    FOREIGN KEY (attempt_id, graph_id, graph_revision, node_id)
        REFERENCES task_runtime_attempt(
            attempt_id, graph_id, graph_revision, node_id
        ) ON DELETE RESTRICT,

    CHECK (
        (budget_id IS NULL
            AND budget_status IS NULL
            AND budget_status_version IS NULL)
        OR
        (budget_id IS NOT NULL
            AND length(trim(budget_id)) > 0
            AND budget_status IS NOT NULL
            AND budget_status_version IS NOT NULL)
    ),
    CHECK (
        (node_id IS NULL
            AND node_status IS NULL
            AND node_status_version IS NULL)
        OR
        (node_id IS NOT NULL
            AND length(trim(node_id)) > 0
            AND node_status IS NOT NULL
            AND node_status_version IS NOT NULL)
    ),
    CHECK (
        (attempt_id IS NULL
            AND attempt_status IS NULL
            AND attempt_status_version IS NULL)
        OR
        (attempt_id IS NOT NULL
            AND length(trim(attempt_id)) > 0
            AND attempt_status IS NOT NULL
            AND attempt_status_version IS NOT NULL
            AND node_id IS NOT NULL)
    ),
    CHECK (
        (case_kind = 'FAILED_UNSETTLED_BUDGET'
            AND classification = 'EXACT_REPAIRABLE'
            AND graph_status = 'FAILED'
            AND budget_id IS NOT NULL
            AND budget_status IN ('ACTIVE', 'EXCEEDED')
            AND recommended_action = 'RELEASE_BUDGET')
        OR
        (case_kind = 'BLOCKED_FAILURE_NODE'
            AND classification = 'SIGNED_REPAIR_REQUIRED'
            AND graph_status = 'BLOCKED'
            AND node_id IS NOT NULL
            AND node_status IN (
                'FAILED', 'VERIFICATION_FAILED', 'CANCELLED'
            )
            AND (
                (recommended_action = 'FAIL_GRAPH'
                    AND (
                        budget_id IS NULL
                        OR budget_status IN ('RELEASED', 'CANCELLED')
                    ))
                OR
                (recommended_action = 'FAIL_GRAPH_AND_RELEASE_BUDGET'
                    AND budget_id IS NOT NULL
                    AND budget_status IN ('ACTIVE', 'EXCEEDED'))
            ))
        OR
        (case_kind = 'CANCELLED_UNSETTLED_BUDGET'
            AND classification = 'SIGNED_REPAIR_REQUIRED'
            AND graph_status = 'CANCELLED'
            AND budget_id IS NOT NULL
            AND budget_status IN ('ACTIVE', 'EXCEEDED')
            AND recommended_action = 'RELEASE_BUDGET')
        OR
        (case_kind = 'COMPLETED_INCONSISTENT'
            AND classification = 'QUARANTINED_STRUCTURAL'
            AND graph_status = 'COMPLETED'
            AND recommended_action = 'NONE')
        OR
        (case_kind = 'TERMINAL_STRUCTURAL_ANOMALY'
            AND classification = 'QUARANTINED_STRUCTURAL'
            AND recommended_action = 'NONE')
        OR
        (case_kind = 'TERMINAL_HEALTHY_CLOSURE'
            AND classification = 'SIGNED_REPAIR_REQUIRED'
            AND budget_id IS NOT NULL
            AND node_id IS NULL
            AND attempt_id IS NULL
            AND recommended_action = 'NONE')
    )
);

CREATE INDEX IF NOT EXISTS idx_task_runtime_terminal_case_graph
    ON task_runtime_terminal_reconciliation_case(
        graph_id, graph_revision, graph_status_version, created_ts_ms, case_id
    );

CREATE INDEX IF NOT EXISTS idx_task_runtime_terminal_case_classification
    ON task_runtime_terminal_reconciliation_case(
        classification, case_kind, created_ts_ms, case_id
    );

-- A case captures one exact observation.  Structural quarantine may record a
-- mismatched Budget-to-Graph binding, but it must still identify the exact
-- extant Budget row and version that was observed.
CREATE TRIGGER IF NOT EXISTS trg_task_runtime_terminal_case_binding_insert
BEFORE INSERT ON task_runtime_terminal_reconciliation_case
WHEN
    NOT EXISTS (
        SELECT 1 FROM task_runtime_graph AS graph
        WHERE graph.graph_id = NEW.graph_id
          AND graph.revision = NEW.graph_revision
          AND graph.status = NEW.graph_status
          AND graph.status_version = NEW.graph_status_version
    )
    OR (
        NEW.budget_id IS NOT NULL
        AND NOT EXISTS (
            SELECT 1 FROM task_runtime_budget_reservation AS budget
            WHERE budget.budget_id = NEW.budget_id
              AND budget.status = NEW.budget_status
              AND budget.status_version = NEW.budget_status_version
              AND (
                  NEW.classification = 'QUARANTINED_STRUCTURAL'
                  OR (
                      budget.graph_id = NEW.graph_id
                      AND budget.graph_revision = NEW.graph_revision
                      AND budget.node_id IS NULL
                      AND budget.attempt_id IS NULL
                  )
              )
        )
    )
    OR (
        NEW.node_id IS NOT NULL
        AND NOT EXISTS (
            SELECT 1 FROM task_runtime_node AS node
            WHERE node.graph_id = NEW.graph_id
              AND node.graph_revision = NEW.graph_revision
              AND node.node_id = NEW.node_id
              AND node.status = NEW.node_status
              AND node.status_version = NEW.node_status_version
        )
    )
    OR (
        NEW.attempt_id IS NOT NULL
        AND NOT EXISTS (
            SELECT 1 FROM task_runtime_attempt AS attempt
            WHERE attempt.attempt_id = NEW.attempt_id
              AND attempt.graph_id = NEW.graph_id
              AND attempt.graph_revision = NEW.graph_revision
              AND attempt.node_id = NEW.node_id
              AND attempt.status = NEW.attempt_status
              AND attempt.status_version = NEW.attempt_status_version
        )
    )
BEGIN
    SELECT RAISE(ABORT, 'terminal reconciliation case snapshot binding drifted');
END;

CREATE TABLE IF NOT EXISTS task_runtime_terminal_reconciliation_proof (
    proof_id TEXT NOT NULL PRIMARY KEY
        CHECK(length(trim(proof_id)) BETWEEN 1 AND 512),
    schema_version TEXT NOT NULL CHECK(
        schema_version = 'Natsume.task_runtime.terminal_reconciliation.proof.v1'
    ),
    case_id TEXT NOT NULL,
    case_snapshot_sha256 TEXT NOT NULL CHECK(
        length(case_snapshot_sha256) = 71
        AND substr(case_snapshot_sha256, 1, 7) = 'sha256:'
        AND substr(case_snapshot_sha256, 8) NOT GLOB '*[^0-9a-f]*'
    ),
    authority_kind TEXT NOT NULL CHECK(authority_kind IN (
        'SYSTEM_DURABLE_EVIDENCE', 'SIGNED_OPERATOR'
    )),
    action TEXT NOT NULL CHECK(action IN (
        'RELEASE_BUDGET',
        'FAIL_GRAPH',
        'FAIL_GRAPH_AND_RELEASE_BUDGET',
        'NONE'
    )),
    target_graph_status TEXT CHECK(
        target_graph_status IS NULL
        OR target_graph_status IN ('FAILED', 'CANCELLED')
    ),
    target_budget_status TEXT CHECK(
        target_budget_status IS NULL OR target_budget_status = 'RELEASED'
    ),
    subject_snapshot_json TEXT NOT NULL CHECK(
        length(subject_snapshot_json) BETWEEN 2 AND 262144
        AND json_valid(subject_snapshot_json)
        AND json_type(subject_snapshot_json) = 'object'
    ),
    subject_snapshot_sha256 TEXT NOT NULL CHECK(
        length(subject_snapshot_sha256) = 71
        AND substr(subject_snapshot_sha256, 1, 7) = 'sha256:'
        AND substr(subject_snapshot_sha256, 8) NOT GLOB '*[^0-9a-f]*'
    ),
    reconciler_actor_id TEXT NOT NULL
        CHECK(length(trim(reconciler_actor_id)) BETWEEN 1 AND 512),
    attestation_ref TEXT UNIQUE CHECK(
        attestation_ref IS NULL
        OR length(trim(attestation_ref)) BETWEEN 1 AND 512
    ),
    attestation_key_id TEXT CHECK(
        attestation_key_id IS NULL
        OR length(trim(attestation_key_id)) BETWEEN 1 AND 255
    ),
    attestation_signature TEXT CHECK(
        attestation_signature IS NULL
        OR (
            length(attestation_signature) = 96
            AND substr(attestation_signature, 1, 8) = 'ed25519:'
            AND substr(attestation_signature, 9, 86)
                NOT GLOB '*[^A-Za-z0-9_-]*'
            AND substr(attestation_signature, 95, 2) = '=='
        )
    ),
    issued_ts_ms INTEGER NOT NULL CHECK(
        typeof(issued_ts_ms) = 'integer' AND issued_ts_ms >= 0
    ),
    proof_binding_json TEXT NOT NULL CHECK(
        length(proof_binding_json) BETWEEN 2 AND 262144
        AND json_valid(proof_binding_json)
        AND json_type(proof_binding_json) = 'object'
    ),
    proof_digest TEXT NOT NULL CHECK(
        length(proof_digest) = 71
        AND substr(proof_digest, 1, 7) = 'sha256:'
        AND substr(proof_digest, 8) NOT GLOB '*[^0-9a-f]*'
    ),
    graph_outbox_id TEXT,
    budget_outbox_id TEXT,
    created_ts_ms INTEGER NOT NULL CHECK(
        typeof(created_ts_ms) = 'integer' AND created_ts_ms >= 0
    ),

    UNIQUE(case_id),
    FOREIGN KEY (case_id)
        REFERENCES task_runtime_terminal_reconciliation_case(case_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (graph_outbox_id)
        REFERENCES task_runtime_outbox(outbox_id) ON DELETE RESTRICT,
    FOREIGN KEY (budget_outbox_id)
        REFERENCES task_runtime_outbox(outbox_id) ON DELETE RESTRICT,

    CHECK (
        (authority_kind = 'SYSTEM_DURABLE_EVIDENCE'
            AND attestation_ref IS NULL
            AND attestation_key_id IS NULL
            AND attestation_signature IS NULL)
        OR
        (authority_kind = 'SIGNED_OPERATOR'
            AND attestation_ref IS NOT NULL
            AND attestation_key_id IS NOT NULL
            AND attestation_signature IS NOT NULL)
    ),
    CHECK (
        (action = 'RELEASE_BUDGET'
            AND target_graph_status IN ('FAILED', 'CANCELLED')
            AND target_budget_status = 'RELEASED'
            AND graph_outbox_id IS NULL
            AND budget_outbox_id IS NOT NULL)
        OR
        (action = 'FAIL_GRAPH'
            AND target_graph_status = 'FAILED'
            AND target_budget_status IS NULL
            AND graph_outbox_id IS NOT NULL
            AND budget_outbox_id IS NULL)
        OR
        (action = 'FAIL_GRAPH_AND_RELEASE_BUDGET'
            AND target_graph_status = 'FAILED'
            AND target_budget_status = 'RELEASED'
            AND graph_outbox_id IS NOT NULL
            AND budget_outbox_id IS NOT NULL)
        OR
        (action = 'NONE'
            AND target_graph_status IS NULL
            AND target_budget_status IS NULL
            AND graph_outbox_id IS NULL
            AND budget_outbox_id IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_task_runtime_terminal_proof_authority
    ON task_runtime_terminal_reconciliation_proof(
        authority_kind, action, created_ts_ms, proof_id
    );

-- A stale observation may only stop blocking production when a later exact
-- current case replaces it.  The replacement remains blocking until its own
-- proof (or a further replacement) is trusted; a plain Graph/Budget rewrite
-- cannot silently close an inventoried case.
CREATE TABLE IF NOT EXISTS task_runtime_terminal_reconciliation_supersession (
    supersession_id TEXT NOT NULL PRIMARY KEY
        CHECK(length(trim(supersession_id)) BETWEEN 1 AND 512),
    schema_version TEXT NOT NULL CHECK(
        schema_version = 'Natsume.task_runtime.terminal_reconciliation.supersession.v1'
    ),
    prior_case_id TEXT NOT NULL UNIQUE,
    replacement_case_id TEXT NOT NULL,
    graph_id TEXT NOT NULL,
    graph_revision INTEGER NOT NULL CHECK(
        typeof(graph_revision) = 'integer' AND graph_revision >= 1
    ),
    prior_snapshot_sha256 TEXT NOT NULL CHECK(
        length(prior_snapshot_sha256) = 71
        AND substr(prior_snapshot_sha256, 1, 7) = 'sha256:'
        AND substr(prior_snapshot_sha256, 8) NOT GLOB '*[^0-9a-f]*'
    ),
    replacement_snapshot_sha256 TEXT NOT NULL CHECK(
        length(replacement_snapshot_sha256) = 71
        AND substr(replacement_snapshot_sha256, 1, 7) = 'sha256:'
        AND substr(replacement_snapshot_sha256, 8) NOT GLOB '*[^0-9a-f]*'
    ),
    reason_code TEXT NOT NULL CHECK(
        reason_code = 'CURRENT_CASE_RECLASSIFIED'
    ),
    created_ts_ms INTEGER NOT NULL CHECK(
        typeof(created_ts_ms) = 'integer' AND created_ts_ms >= 0
    ),
    FOREIGN KEY (prior_case_id)
        REFERENCES task_runtime_terminal_reconciliation_case(case_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (replacement_case_id)
        REFERENCES task_runtime_terminal_reconciliation_case(case_id)
        ON DELETE RESTRICT,
    CHECK(prior_case_id <> replacement_case_id)
);

CREATE INDEX IF NOT EXISTS idx_task_runtime_terminal_supersession_replacement
    ON task_runtime_terminal_reconciliation_supersession(
        replacement_case_id, created_ts_ms, supersession_id
    );

-- A no-op healthy closure is not a generic way to mint signable cases.  It
-- may only replace an earlier unresolved observation of the same immutable
-- Graph revision.  The current healthy classification itself is revalidated
-- by the Store in the proof UoW.
CREATE TRIGGER IF NOT EXISTS trg_task_runtime_terminal_healthy_closure_origin_insert
BEFORE INSERT ON task_runtime_terminal_reconciliation_case
WHEN
    NEW.case_kind = 'TERMINAL_HEALTHY_CLOSURE'
    AND NOT EXISTS (
        SELECT 1
        FROM task_runtime_terminal_reconciliation_case AS prior
        WHERE prior.graph_id = NEW.graph_id
          AND prior.graph_revision = NEW.graph_revision
          AND prior.case_id <> NEW.case_id
          AND NOT EXISTS (
              SELECT 1
              FROM task_runtime_terminal_reconciliation_proof AS proof
              WHERE proof.case_id = prior.case_id
          )
          AND NOT EXISTS (
              SELECT 1
              FROM task_runtime_terminal_reconciliation_supersession AS supersession
              WHERE supersession.prior_case_id = prior.case_id
          )
    )
BEGIN
    SELECT RAISE(ABORT, 'terminal healthy closure lacks unresolved prior case');
END;

CREATE TRIGGER IF NOT EXISTS trg_task_runtime_terminal_supersession_binding_insert
BEFORE INSERT ON task_runtime_terminal_reconciliation_supersession
WHEN
    NOT EXISTS (
        SELECT 1
        FROM task_runtime_terminal_reconciliation_case AS prior
        JOIN task_runtime_terminal_reconciliation_case AS replacement
          ON replacement.case_id = NEW.replacement_case_id
        WHERE prior.case_id = NEW.prior_case_id
          AND prior.graph_id = replacement.graph_id
          AND prior.graph_revision = replacement.graph_revision
          AND prior.graph_id = NEW.graph_id
          AND prior.graph_revision = NEW.graph_revision
          AND prior.snapshot_sha256 = NEW.prior_snapshot_sha256
          AND replacement.snapshot_sha256 = NEW.replacement_snapshot_sha256
    )
    OR EXISTS (
        SELECT 1 FROM task_runtime_terminal_reconciliation_proof AS proof
        WHERE proof.case_id = NEW.prior_case_id
    )
BEGIN
    SELECT RAISE(ABORT, 'terminal reconciliation supersession binding drifted');
END;

CREATE TRIGGER IF NOT EXISTS trg_task_runtime_terminal_proof_binding_insert
BEFORE INSERT ON task_runtime_terminal_reconciliation_proof
WHEN
    EXISTS (
        SELECT 1
        FROM task_runtime_terminal_reconciliation_supersession AS supersession
        WHERE supersession.prior_case_id = NEW.case_id
    )
    OR
    NOT EXISTS (
        SELECT 1
        FROM task_runtime_terminal_reconciliation_case AS item
        WHERE item.case_id = NEW.case_id
          AND item.snapshot_sha256 = NEW.case_snapshot_sha256
          AND item.recommended_action = NEW.action
          AND (
              (item.classification = 'EXACT_REPAIRABLE'
                  AND NEW.authority_kind IN (
                      'SYSTEM_DURABLE_EVIDENCE', 'SIGNED_OPERATOR'
                  ))
              OR
              (item.classification = 'SIGNED_REPAIR_REQUIRED'
                  AND NEW.authority_kind = 'SIGNED_OPERATOR')
          )
          AND (
              NEW.action NOT IN (
                  'RELEASE_BUDGET', 'FAIL_GRAPH_AND_RELEASE_BUDGET'
              )
              OR item.budget_id IS NOT NULL
          )
          AND (
              NEW.action <> 'RELEASE_BUDGET'
              OR NEW.target_graph_status = item.graph_status
          )
    )
    OR (
        NEW.graph_outbox_id IS NOT NULL
        AND NOT EXISTS (
            SELECT 1
            FROM task_runtime_terminal_reconciliation_case AS item
            JOIN task_runtime_outbox AS outbox
              ON outbox.outbox_id = NEW.graph_outbox_id
            WHERE item.case_id = NEW.case_id
              AND outbox.aggregate_type = 'TASK_GRAPH'
              AND outbox.aggregate_id = item.graph_id
              AND outbox.graph_revision = item.graph_revision
              AND outbox.aggregate_version = item.graph_status_version + 1
              AND outbox.event_type = 'TASK_GRAPH_FAILED'
              AND outbox.attempt_id IS NULL
        )
    )
    OR (
        NEW.budget_outbox_id IS NOT NULL
        AND NOT EXISTS (
            SELECT 1
            FROM task_runtime_terminal_reconciliation_case AS item
            JOIN task_runtime_outbox AS outbox
              ON outbox.outbox_id = NEW.budget_outbox_id
            WHERE item.case_id = NEW.case_id
              AND item.budget_id IS NOT NULL
              AND item.budget_status_version IS NOT NULL
              AND outbox.aggregate_type = 'BUDGET_RESERVATION'
              AND outbox.aggregate_id = item.budget_id
              AND outbox.graph_revision IS NULL
              AND outbox.aggregate_version = item.budget_status_version + 1
              AND outbox.event_type = 'BUDGET_RESERVATION_RELEASED'
              AND outbox.attempt_id IS NULL
        )
    )
BEGIN
    SELECT RAISE(ABORT, 'terminal reconciliation proof binding drifted');
END;

CREATE TRIGGER IF NOT EXISTS trg_task_runtime_terminal_case_immutable_update
BEFORE UPDATE ON task_runtime_terminal_reconciliation_case
BEGIN
    SELECT RAISE(ABORT, 'terminal reconciliation case is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_task_runtime_terminal_case_immutable_delete
BEFORE DELETE ON task_runtime_terminal_reconciliation_case
BEGIN
    SELECT RAISE(ABORT, 'terminal reconciliation case is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_task_runtime_terminal_proof_immutable_update
BEFORE UPDATE ON task_runtime_terminal_reconciliation_proof
BEGIN
    SELECT RAISE(ABORT, 'terminal reconciliation proof is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_task_runtime_terminal_proof_immutable_delete
BEFORE DELETE ON task_runtime_terminal_reconciliation_proof
BEGIN
    SELECT RAISE(ABORT, 'terminal reconciliation proof is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_task_runtime_terminal_supersession_immutable_update
BEFORE UPDATE ON task_runtime_terminal_reconciliation_supersession
BEGIN
    SELECT RAISE(ABORT, 'terminal reconciliation supersession is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_task_runtime_terminal_supersession_immutable_delete
BEFORE DELETE ON task_runtime_terminal_reconciliation_supersession
BEGIN
    SELECT RAISE(ABORT, 'terminal reconciliation supersession is append-only');
END;

COMMIT;
