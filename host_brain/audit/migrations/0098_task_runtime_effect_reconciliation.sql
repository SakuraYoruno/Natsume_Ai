-- NE-01 B5C: append-only Acceptance Effect reconciliation evidence.
--
-- This migration never rewrites the 0087 Effect row or the 0092 cancellation
-- fence/decision.  It inventories legacy ambiguous evidence, preserves
-- finalized cases as quarantine facts, and adds a signed proof sidecar for a
-- later application/runtime cut.  New FAILED_EVIDENCE authority is forbidden;
-- existing rows remain readable and immutable for governed correction.

BEGIN IMMEDIATE;

CREATE UNIQUE INDEX IF NOT EXISTS uq_task_runtime_cancellation_effect_fence_binding
    ON task_runtime_graph_cancellation_effect_fence(effect_id, intent_ref);

CREATE TABLE IF NOT EXISTS task_runtime_effect_reconciliation_case (
    case_id TEXT NOT NULL PRIMARY KEY
        CHECK(length(trim(case_id)) BETWEEN 1 AND 512),
    schema_version TEXT NOT NULL
        CHECK(schema_version = 'Natsume.task_runtime.acceptance_effect_reconciliation.case.v1'),
    purpose TEXT NOT NULL CHECK(purpose IN (
        'PENDING_CANCELLATION',
        'LEGACY_FINALIZED_CORRECTION'
    )),
    case_kind TEXT NOT NULL CHECK(case_kind IN (
        'LEGACY_FAILED_EVIDENCE',
        'LEGACY_RETRY_PENDING_CANCELLED',
        'RECONCILIATION_REQUIRED'
    )),
    classification TEXT NOT NULL CHECK(classification IN (
        'REPAIRABLE_REQUESTED',
        'QUARANTINED_FINALIZED',
        'QUARANTINED_STRUCTURAL'
    )),
    reason_code TEXT NOT NULL CHECK(length(trim(reason_code)) > 0),
    intent_ref TEXT NOT NULL,
    effect_id TEXT NOT NULL,
    graph_id TEXT NOT NULL,
    graph_revision INTEGER NOT NULL CHECK(
        typeof(graph_revision) = 'integer' AND graph_revision >= 1
    ),
    node_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    legacy_decision_ref TEXT,
    legacy_decision_payload_sha256 TEXT CHECK(
        legacy_decision_payload_sha256 IS NULL
        OR (
            length(legacy_decision_payload_sha256) = 71
            AND substr(legacy_decision_payload_sha256, 1, 7) = 'sha256:'
            AND substr(legacy_decision_payload_sha256, 8) NOT GLOB '*[^0-9a-f]*'
        )
    ),
    intent_status TEXT NOT NULL CHECK(intent_status IN ('REQUESTED', 'FINALIZED')),
    intent_status_version INTEGER NOT NULL CHECK(
        typeof(intent_status_version) = 'integer'
        AND intent_status_version >= 0
    ),
    effect_status TEXT NOT NULL CHECK(effect_status IN (
        'PENDING', 'CLAIMED', 'COMMITTED', 'FAILED'
    )),
    effect_status_version INTEGER NOT NULL CHECK(
        typeof(effect_status_version) = 'integer'
        AND effect_status_version >= 0
    ),
    fence_observed_status TEXT NOT NULL CHECK(fence_observed_status IN (
        'PENDING', 'CLAIMED', 'COMMITTED', 'FAILED'
    )),
    fence_observed_status_version INTEGER NOT NULL CHECK(
        typeof(fence_observed_status_version) = 'integer'
        AND fence_observed_status_version >= 0
    ),
    fence_resolution_status TEXT NOT NULL CHECK(fence_resolution_status IN (
        'CANCELLED', 'RECONCILIATION_REQUIRED',
        'COMMITTED_EVIDENCE', 'FAILED_EVIDENCE'
    )),
    snapshot_json TEXT NOT NULL CHECK(length(snapshot_json) BETWEEN 2 AND 262144),
    snapshot_sha256 TEXT NOT NULL CHECK(
        length(snapshot_sha256) = 71
        AND substr(snapshot_sha256, 1, 7) = 'sha256:'
        AND substr(snapshot_sha256, 8) NOT GLOB '*[^0-9a-f]*'
    ),
    created_ts_ms INTEGER NOT NULL CHECK(
        typeof(created_ts_ms) = 'integer' AND created_ts_ms >= 0
    ),
    UNIQUE(effect_id, intent_ref, purpose),
    UNIQUE(
        case_id, intent_ref, effect_id, purpose,
        graph_id, graph_revision, node_id, attempt_id
    ),
    FOREIGN KEY (intent_ref, graph_id, graph_revision)
        REFERENCES task_runtime_graph_cancellation_intent(
            intent_ref, graph_id, graph_revision
        ) ON DELETE RESTRICT,
    FOREIGN KEY (effect_id)
        REFERENCES task_runtime_acceptance_effect_intent(effect_id) ON DELETE RESTRICT,
    FOREIGN KEY (effect_id, intent_ref)
        REFERENCES task_runtime_graph_cancellation_effect_fence(
            effect_id, intent_ref
        ) ON DELETE RESTRICT,
    FOREIGN KEY (legacy_decision_ref)
        REFERENCES task_runtime_graph_cancellation_decision(decision_ref)
        ON DELETE RESTRICT,
    FOREIGN KEY (graph_id, graph_revision, node_id)
        REFERENCES task_runtime_node(
            graph_id, graph_revision, node_id
        ) ON DELETE RESTRICT,
    FOREIGN KEY (attempt_id, graph_id, graph_revision, node_id)
        REFERENCES task_runtime_attempt(
            attempt_id, graph_id, graph_revision, node_id
        ) ON DELETE RESTRICT,
    CHECK (
        (intent_status = 'REQUESTED'
            AND purpose = 'PENDING_CANCELLATION'
            AND legacy_decision_ref IS NULL
            AND legacy_decision_payload_sha256 IS NULL)
        OR
        (intent_status = 'FINALIZED'
            AND purpose = 'LEGACY_FINALIZED_CORRECTION'
            AND (
                classification = 'QUARANTINED_STRUCTURAL'
                OR (
                    legacy_decision_ref IS NOT NULL
                    AND legacy_decision_payload_sha256 IS NOT NULL
                )
            ))
    ),
    CHECK (
        (classification = 'REPAIRABLE_REQUESTED'
            AND intent_status = 'REQUESTED')
        OR
        (classification = 'QUARANTINED_FINALIZED'
            AND intent_status = 'FINALIZED')
        OR classification = 'QUARANTINED_STRUCTURAL'
    )
);

CREATE INDEX IF NOT EXISTS idx_task_runtime_effect_reconciliation_case_class
    ON task_runtime_effect_reconciliation_case(
        classification, purpose, created_ts_ms, case_id
    );

CREATE TABLE IF NOT EXISTS task_runtime_effect_reconciliation_proof (
    proof_id TEXT NOT NULL PRIMARY KEY
        CHECK(length(trim(proof_id)) BETWEEN 1 AND 512),
    schema_version TEXT NOT NULL
        CHECK(schema_version = 'Natsume.task_runtime.acceptance_effect_reconciliation.proof.v1'),
    purpose TEXT NOT NULL CHECK(purpose IN (
        'PENDING_CANCELLATION',
        'LEGACY_FINALIZED_CORRECTION'
    )),
    case_id TEXT NOT NULL,
    intent_ref TEXT NOT NULL,
    effect_id TEXT NOT NULL,
    graph_id TEXT NOT NULL,
    graph_revision INTEGER NOT NULL CHECK(
        typeof(graph_revision) = 'integer' AND graph_revision >= 1
    ),
    node_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    case_snapshot_sha256 TEXT NOT NULL CHECK(
        length(case_snapshot_sha256) = 71
        AND substr(case_snapshot_sha256, 1, 7) = 'sha256:'
        AND substr(case_snapshot_sha256, 8) NOT GLOB '*[^0-9a-f]*'
    ),
    subject_snapshot_json TEXT NOT NULL
        CHECK(length(subject_snapshot_json) BETWEEN 2 AND 262144),
    subject_snapshot_sha256 TEXT NOT NULL CHECK(
        length(subject_snapshot_sha256) = 71
        AND substr(subject_snapshot_sha256, 1, 7) = 'sha256:'
        AND substr(subject_snapshot_sha256, 8) NOT GLOB '*[^0-9a-f]*'
    ),
    outcome TEXT NOT NULL CHECK(outcome IN (
        'NO_SIDE_EFFECT', 'SIDE_EFFECT_CONFIRMED'
    )),
    target_resolution_status TEXT NOT NULL CHECK(target_resolution_status IN (
        'CANCELLED', 'COMMITTED_EVIDENCE'
    )),
    receipt_ref TEXT,
    executor_quiescence_ref TEXT CHECK(
        executor_quiescence_ref IS NULL
        OR length(trim(executor_quiescence_ref)) BETWEEN 1 AND 512
    ),
    reconciler_actor_id TEXT NOT NULL
        CHECK(length(trim(reconciler_actor_id)) BETWEEN 1 AND 512),
    attestation_ref TEXT NOT NULL UNIQUE
        CHECK(length(trim(attestation_ref)) BETWEEN 1 AND 512),
    attestation_key_id TEXT NOT NULL
        CHECK(length(trim(attestation_key_id)) BETWEEN 1 AND 255),
    attestation_signature TEXT NOT NULL CHECK(
        length(attestation_signature) = 96
        AND substr(attestation_signature, 1, 8) = 'ed25519:'
    ),
    issued_ts_ms INTEGER NOT NULL CHECK(
        typeof(issued_ts_ms) = 'integer' AND issued_ts_ms >= 0
    ),
    proof_binding_json TEXT NOT NULL
        CHECK(length(proof_binding_json) BETWEEN 2 AND 262144),
    proof_digest TEXT NOT NULL CHECK(
        length(proof_digest) = 71
        AND substr(proof_digest, 1, 7) = 'sha256:'
        AND substr(proof_digest, 8) NOT GLOB '*[^0-9a-f]*'
    ),
    outbox_id TEXT NOT NULL UNIQUE,
    created_ts_ms INTEGER NOT NULL CHECK(
        typeof(created_ts_ms) = 'integer' AND created_ts_ms >= 0
    ),
    UNIQUE(effect_id, intent_ref, purpose),
    FOREIGN KEY (
        case_id, intent_ref, effect_id, purpose,
        graph_id, graph_revision, node_id, attempt_id
    ) REFERENCES task_runtime_effect_reconciliation_case(
        case_id, intent_ref, effect_id, purpose,
        graph_id, graph_revision, node_id, attempt_id
    ) ON DELETE RESTRICT,
    FOREIGN KEY (outbox_id)
        REFERENCES task_runtime_outbox(outbox_id) ON DELETE RESTRICT,
    CHECK (
        (outcome = 'NO_SIDE_EFFECT'
            AND target_resolution_status = 'CANCELLED'
            AND receipt_ref IS NULL)
        OR
        (outcome = 'SIDE_EFFECT_CONFIRMED'
            AND target_resolution_status = 'COMMITTED_EVIDENCE'
            AND receipt_ref IS NOT NULL
            AND length(trim(receipt_ref)) BETWEEN 1 AND 512)
    )
);

CREATE INDEX IF NOT EXISTS idx_task_runtime_effect_reconciliation_proof_case
    ON task_runtime_effect_reconciliation_proof(
        case_id, purpose, created_ts_ms, proof_id
    );

-- 0095 originally allowed graph_revision only for TASK_GRAPH.  Effect proof
-- Outboxes also require an exact graph epoch, but no other aggregate receives
-- this authority.  Rebuild the guards only after the case table exists so the
-- physical Outbox must bind one exact inventoried case and revision.
DROP TRIGGER IF EXISTS trg_task_runtime_outbox_graph_revision_insert_guard;
CREATE TRIGGER trg_task_runtime_outbox_graph_revision_insert_guard
BEFORE INSERT ON task_runtime_outbox
WHEN NOT (
    (
        NEW.aggregate_type = 'TASK_GRAPH'
        AND NEW.graph_revision IS NOT NULL
        AND typeof(NEW.graph_revision) = 'integer'
        AND NEW.graph_revision >= 1
        AND NEW.payload_ref = (
            'taskstore://event/TASK_GRAPH/' || NEW.aggregate_id
            || '@' || NEW.graph_revision || '/' || NEW.aggregate_version
        )
        AND EXISTS (
            SELECT 1 FROM task_runtime_graph AS graph
            WHERE graph.graph_id = NEW.aggregate_id
              AND graph.revision = NEW.graph_revision
        )
    )
    OR (
        NEW.aggregate_type = 'GRAPH_CANCELLATION_EFFECT_RECONCILIATION'
        AND NEW.graph_revision IS NOT NULL
        AND typeof(NEW.graph_revision) = 'integer'
        AND NEW.graph_revision >= 1
        AND NEW.payload_ref = (
            'taskstore://event/GRAPH_CANCELLATION_EFFECT_RECONCILIATION/'
            || NEW.aggregate_id || '@' || NEW.graph_revision
            || '/' || NEW.aggregate_version
        )
        AND EXISTS (
            SELECT 1 FROM task_runtime_effect_reconciliation_case AS item
            WHERE item.case_id = NEW.aggregate_id
              AND item.graph_revision = NEW.graph_revision
        )
    )
    OR (
        NEW.aggregate_type NOT IN (
            'TASK_GRAPH', 'GRAPH_CANCELLATION_EFFECT_RECONCILIATION'
        )
        AND NEW.graph_revision IS NULL
    )
)
BEGIN
    SELECT RAISE(ABORT, 'task_runtime_outbox graph revision is invalid');
END;

DROP TRIGGER IF EXISTS trg_task_runtime_outbox_graph_revision_update_guard;
CREATE TRIGGER trg_task_runtime_outbox_graph_revision_update_guard
BEFORE UPDATE OF aggregate_type, aggregate_id, graph_revision,
                 aggregate_version, payload_ref
ON task_runtime_outbox
WHEN NOT (
    (
        NEW.aggregate_type = 'TASK_GRAPH'
        AND NEW.graph_revision IS NOT NULL
        AND typeof(NEW.graph_revision) = 'integer'
        AND NEW.graph_revision >= 1
        AND NEW.payload_ref = (
            'taskstore://event/TASK_GRAPH/' || NEW.aggregate_id
            || '@' || NEW.graph_revision || '/' || NEW.aggregate_version
        )
        AND EXISTS (
            SELECT 1 FROM task_runtime_graph AS graph
            WHERE graph.graph_id = NEW.aggregate_id
              AND graph.revision = NEW.graph_revision
        )
    )
    OR (
        NEW.aggregate_type = 'GRAPH_CANCELLATION_EFFECT_RECONCILIATION'
        AND NEW.graph_revision IS NOT NULL
        AND typeof(NEW.graph_revision) = 'integer'
        AND NEW.graph_revision >= 1
        AND NEW.payload_ref = (
            'taskstore://event/GRAPH_CANCELLATION_EFFECT_RECONCILIATION/'
            || NEW.aggregate_id || '@' || NEW.graph_revision
            || '/' || NEW.aggregate_version
        )
        AND EXISTS (
            SELECT 1 FROM task_runtime_effect_reconciliation_case AS item
            WHERE item.case_id = NEW.aggregate_id
              AND item.graph_revision = NEW.graph_revision
        )
    )
    OR (
        NEW.aggregate_type NOT IN (
            'TASK_GRAPH', 'GRAPH_CANCELLATION_EFFECT_RECONCILIATION'
        )
        AND NEW.graph_revision IS NULL
    )
)
BEGIN
    SELECT RAISE(ABORT, 'task_runtime_outbox graph revision is invalid');
END;

-- Freeze every legacy candidate before installing the forward guards.  The
-- snapshot key order is canonical and mirrored by the persistence codec.
WITH candidate_base AS (
    SELECT
        intent.intent_ref,
        intent.status AS intent_status,
        intent.status_version AS intent_status_version,
        intent.payload_sha256 AS intent_payload_sha256,
        intent.request_id AS intent_request_id,
        intent.graph_id,
        intent.graph_revision,
        effect.effect_id,
        effect.node_id,
        effect.attempt_id,
        effect.commit_scope AS effect_commit_scope,
        effect.effect_type,
        effect.payload_ref AS effect_payload_ref,
        effect.payload_sha256 AS effect_payload_sha256,
        effect.idempotency_key AS effect_idempotency_key,
        effect.status AS effect_status,
        effect.status_version AS effect_status_version,
        effect.available_ts_ms AS effect_available_ts_ms,
        effect.lease_owner AS effect_lease_owner,
        effect.lease_expires_ts_ms AS effect_lease_expires_ts_ms,
        effect.fencing_token AS effect_fencing_token,
        effect.attempt_count AS effect_attempt_count,
        effect.receipt_ref AS effect_receipt_ref,
        effect.reason_code AS effect_reason_code,
        effect.last_error AS effect_last_error,
        CASE WHEN effect.last_error IS NULL THEN NULL
             ELSE 'sha256:' || natsume_sha256_hex(effect.last_error)
        END AS effect_last_error_sha256,
        effect.created_ts_ms AS effect_created_ts_ms,
        effect.updated_ts_ms AS effect_updated_ts_ms,
        effect.committed_ts_ms AS effect_committed_ts_ms,
        effect.failed_ts_ms AS effect_failed_ts_ms,
        attempt.status AS attempt_status,
        attempt.status_version AS attempt_status_version,
        fence.observed_status AS fence_observed_status,
        fence.observed_status_version AS fence_observed_status_version,
        fence.resolution_status AS fence_resolution_status,
        fence.created_ts_ms AS fence_created_ts_ms,
        fence.resolved_ts_ms AS fence_resolved_ts_ms,
        decision.decision_ref,
        decision.payload_sha256 AS decision_payload_sha256,
        CASE
            WHEN fence.resolution_status = 'FAILED_EVIDENCE'
                THEN 'LEGACY_FAILED_EVIDENCE'
            ELSE 'LEGACY_RETRY_PENDING_CANCELLED'
        END AS case_kind,
        CASE
            WHEN fence.resolution_status = 'FAILED_EVIDENCE'
             AND effect.graph_id = intent.graph_id
             AND effect.graph_revision = intent.graph_revision
             AND effect.status = 'FAILED'
             AND effect.attempt_count >= 1
             AND effect.fencing_token >= 1
             AND effect.reason_code IS NOT NULL
             AND effect.last_error IS NOT NULL
             AND effect.receipt_ref IS NULL
             AND effect.failed_ts_ms IS NOT NULL
             AND (
                (fence.observed_status = 'FAILED'
                    AND effect.status_version = fence.observed_status_version)
                OR
                (fence.observed_status = 'CLAIMED'
                    AND effect.status_version = fence.observed_status_version + 1)
             ) THEN 1
            WHEN fence.resolution_status = 'CANCELLED'
             AND fence.observed_status = 'PENDING'
             AND effect.graph_id = intent.graph_id
             AND effect.graph_revision = intent.graph_revision
             AND effect.status = 'PENDING'
             AND effect.status_version = fence.observed_status_version
             AND effect.attempt_count >= 1
             AND effect.fencing_token >= 1
             AND effect.status_version >= 2
             AND effect.reason_code IS NOT NULL
             AND effect.last_error IS NOT NULL
             AND effect.lease_owner IS NULL
             AND effect.lease_expires_ts_ms IS NULL
             AND effect.receipt_ref IS NULL
             AND effect.committed_ts_ms IS NULL
             AND effect.failed_ts_ms IS NULL THEN 1
            ELSE 0
        END AS structurally_valid
    FROM task_runtime_graph_cancellation_effect_fence AS fence
    JOIN task_runtime_acceptance_effect_intent AS effect
      ON effect.effect_id = fence.effect_id
    JOIN task_runtime_graph_cancellation_intent AS intent
      ON intent.intent_ref = fence.intent_ref
    JOIN task_runtime_attempt AS attempt
      ON attempt.attempt_id = effect.attempt_id
     AND attempt.graph_id = effect.graph_id
     AND attempt.graph_revision = effect.graph_revision
     AND attempt.node_id = effect.node_id
    LEFT JOIN task_runtime_graph_cancellation_decision AS decision
      ON decision.intent_ref = intent.intent_ref
    WHERE fence.resolution_status = 'FAILED_EVIDENCE'
       OR (
            fence.observed_status = 'PENDING'
        AND fence.resolution_status = 'CANCELLED'
        AND (
            effect.attempt_count > 0
            OR effect.fencing_token > 0
            OR effect.status_version > 0
            OR effect.reason_code IS NOT NULL
            OR effect.last_error IS NOT NULL
        )
       )
), classified AS (
    SELECT *,
        CASE
            WHEN intent_status = 'FINALIZED'
             AND decision_ref IS NOT NULL
             AND structurally_valid = 1 THEN 'QUARANTINED_FINALIZED'
            WHEN intent_status = 'REQUESTED'
             AND decision_ref IS NULL
             AND structurally_valid = 1 THEN 'REPAIRABLE_REQUESTED'
            ELSE 'QUARANTINED_STRUCTURAL'
        END AS classification,
        CASE
            WHEN intent_status = 'FINALIZED'
             AND decision_ref IS NOT NULL
             AND structurally_valid = 1
                THEN 'LEGACY_FINALIZED_IMMUTABLE_DECISION'
            WHEN intent_status = 'REQUESTED'
             AND decision_ref IS NULL
             AND structurally_valid = 1
             AND case_kind = 'LEGACY_FAILED_EVIDENCE'
                THEN 'REQUESTED_LEGACY_FAILED_EVIDENCE'
            WHEN intent_status = 'REQUESTED'
             AND decision_ref IS NULL
             AND structurally_valid = 1
                THEN 'REQUESTED_RETRY_PENDING_CANCELLED'
            ELSE 'LEGACY_EFFECT_EVIDENCE_STRUCTURAL_ANOMALY'
        END AS inventory_reason,
        CASE WHEN intent_status = 'FINALIZED'
            THEN 'LEGACY_FINALIZED_CORRECTION'
            ELSE 'PENDING_CANCELLATION'
        END AS purpose
    FROM candidate_base
), snapshotted AS (
    SELECT *, json_object(
        'attempt_id', attempt_id,
        'attempt_status', attempt_status,
        'attempt_status_version', attempt_status_version,
        'case_kind', case_kind,
        'classification', classification,
        'effect_attempt_count', effect_attempt_count,
        'effect_available_ts_ms', effect_available_ts_ms,
        'effect_commit_scope', effect_commit_scope,
        'effect_committed_ts_ms', effect_committed_ts_ms,
        'effect_created_ts_ms', effect_created_ts_ms,
        'effect_failed_ts_ms', effect_failed_ts_ms,
        'effect_fencing_token', effect_fencing_token,
        'effect_id', effect_id,
        'effect_idempotency_key', effect_idempotency_key,
        'effect_last_error_sha256', effect_last_error_sha256,
        'effect_lease_expires_ts_ms', effect_lease_expires_ts_ms,
        'effect_lease_owner', effect_lease_owner,
        'effect_payload_ref', effect_payload_ref,
        'effect_payload_sha256', effect_payload_sha256,
        'effect_reason_code', effect_reason_code,
        'effect_receipt_ref', effect_receipt_ref,
        'effect_status', effect_status,
        'effect_status_version', effect_status_version,
        'effect_type', effect_type,
        'effect_updated_ts_ms', effect_updated_ts_ms,
        'fence_observed_status', fence_observed_status,
        'fence_observed_status_version', fence_observed_status_version,
        'fence_resolution_status', fence_resolution_status,
        'graph_id', graph_id,
        'graph_revision', graph_revision,
        'intent_payload_sha256', intent_payload_sha256,
        'intent_ref', intent_ref,
        'intent_request_id', intent_request_id,
        'intent_status', intent_status,
        'intent_status_version', intent_status_version,
        'inventory_reason', inventory_reason,
        'legacy_decision_payload_sha256', decision_payload_sha256,
        'legacy_decision_ref', decision_ref,
        'node_id', node_id,
        'purpose', purpose
    ) AS snapshot_json
    FROM classified
)
INSERT OR IGNORE INTO task_runtime_effect_reconciliation_case(
    case_id, schema_version, purpose, case_kind, classification, reason_code,
    intent_ref, effect_id, graph_id, graph_revision, node_id, attempt_id,
    legacy_decision_ref, legacy_decision_payload_sha256,
    intent_status, intent_status_version, effect_status,
    effect_status_version, fence_observed_status,
    fence_observed_status_version, fence_resolution_status,
    snapshot_json, snapshot_sha256, created_ts_ms
)
SELECT
    'effect-reconciliation-case://sha256/' || natsume_sha256_hex(
        purpose || char(10) || intent_ref || char(10) || effect_id
    ),
    'Natsume.task_runtime.acceptance_effect_reconciliation.case.v1',
    purpose, case_kind, classification, inventory_reason,
    intent_ref, effect_id, graph_id, graph_revision, node_id, attempt_id,
    decision_ref, decision_payload_sha256,
    intent_status, intent_status_version, effect_status,
    effect_status_version, fence_observed_status,
    fence_observed_status_version, fence_resolution_status,
    snapshot_json,
    'sha256:' || natsume_sha256_hex(snapshot_json),
    fence_created_ts_ms
FROM snapshotted;

CREATE TRIGGER IF NOT EXISTS trg_task_effect_reconciliation_case_no_update
BEFORE UPDATE ON task_runtime_effect_reconciliation_case
BEGIN
    SELECT RAISE(ABORT, 'effect reconciliation case inventory is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_task_effect_reconciliation_case_no_delete
BEFORE DELETE ON task_runtime_effect_reconciliation_case
BEGIN
    SELECT RAISE(ABORT, 'effect reconciliation case inventory is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_task_effect_reconciliation_proof_no_update
BEFORE UPDATE ON task_runtime_effect_reconciliation_proof
BEGIN
    SELECT RAISE(ABORT, 'effect reconciliation proof is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_task_effect_reconciliation_proof_no_delete
BEFORE DELETE ON task_runtime_effect_reconciliation_proof
BEGIN
    SELECT RAISE(ABORT, 'effect reconciliation proof is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_task_effect_failed_evidence_forbidden_insert
BEFORE INSERT ON task_runtime_graph_cancellation_effect_fence
WHEN NEW.resolution_status = 'FAILED_EVIDENCE'
BEGIN
    SELECT RAISE(ABORT, 'new FAILED_EVIDENCE effect fences are forbidden');
END;

CREATE TRIGGER IF NOT EXISTS trg_task_effect_failed_evidence_forbidden_update
BEFORE UPDATE ON task_runtime_graph_cancellation_effect_fence
WHEN OLD.resolution_status = 'FAILED_EVIDENCE'
  OR NEW.resolution_status = 'FAILED_EVIDENCE'
BEGIN
    SELECT RAISE(ABORT, 'legacy FAILED_EVIDENCE effect fences are immutable');
END;

COMMIT;
