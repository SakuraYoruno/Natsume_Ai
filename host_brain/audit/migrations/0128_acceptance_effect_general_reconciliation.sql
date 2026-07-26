-- 0128_acceptance_effect_general_reconciliation.sql
-- Signed, cancellation-independent attribution for unknown Acceptance Effects.
--
-- This migration is expand-only.  It does not inventory, rewrite, or infer
-- historical Effect outcomes.  A case is frozen only when a signed proof is
-- applied against the exact current queue row.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS
task_runtime_acceptance_effect_reconciliation_evidence (
    evidence_ref TEXT NOT NULL PRIMARY KEY
        CHECK(length(trim(evidence_ref)) BETWEEN 1 AND 512),
    schema_version TEXT NOT NULL CHECK(
        schema_version =
        'Natsume.task_runtime.acceptance_effect_reconciliation.evidence.v2'
    ),
    request_id TEXT NOT NULL UNIQUE
        CHECK(length(trim(request_id)) BETWEEN 1 AND 512),
    ingress_audit_ref TEXT NOT NULL
        CHECK(length(trim(ingress_audit_ref)) BETWEEN 1 AND 512),
    body_sha256 TEXT NOT NULL CHECK(
        length(body_sha256) = 71
        AND substr(body_sha256, 1, 7) = 'sha256:'
        AND substr(body_sha256, 8) NOT GLOB '*[^0-9a-f]*'
    ),
    effect_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    expected_case_snapshot_sha256 TEXT NOT NULL CHECK(
        length(expected_case_snapshot_sha256) = 71
        AND substr(expected_case_snapshot_sha256, 1, 7) = 'sha256:'
        AND substr(expected_case_snapshot_sha256, 8)
            NOT GLOB '*[^0-9a-f]*'
    ),
    expected_subject_snapshot_sha256 TEXT NOT NULL CHECK(
        length(expected_subject_snapshot_sha256) = 71
        AND substr(expected_subject_snapshot_sha256, 1, 7) = 'sha256:'
        AND substr(expected_subject_snapshot_sha256, 8)
            NOT GLOB '*[^0-9a-f]*'
    ),
    purpose TEXT NOT NULL CHECK(purpose = 'GENERAL_UNKNOWN_EFFECT'),
    outcome TEXT NOT NULL CHECK(
        outcome IN ('NO_SIDE_EFFECT', 'SIDE_EFFECT_CONFIRMED')
    ),
    target_effect_status TEXT NOT NULL CHECK(
        target_effect_status IN ('PENDING', 'COMMITTED')
    ),
    receipt_ref TEXT,
    executor_quiescence_ref TEXT,
    reconciler_actor_id TEXT NOT NULL
        CHECK(length(trim(reconciler_actor_id)) BETWEEN 1 AND 512),
    issued_ts_ms INTEGER NOT NULL CHECK(
        typeof(issued_ts_ms) = 'integer' AND issued_ts_ms >= 0
    ),
    attestation_ref TEXT NOT NULL UNIQUE
        CHECK(length(trim(attestation_ref)) BETWEEN 1 AND 512),
    attestation_key_id TEXT NOT NULL
        CHECK(length(trim(attestation_key_id)) BETWEEN 1 AND 255),
    attestation_signature TEXT NOT NULL CHECK(
        length(attestation_signature) = 96
        AND substr(attestation_signature, 1, 8) = 'ed25519:'
    ),
    proof_id TEXT NOT NULL,
    outbox_id TEXT NOT NULL,
    proof_binding_json TEXT NOT NULL
        CHECK(length(proof_binding_json) BETWEEN 2 AND 262144),
    proof_digest TEXT NOT NULL CHECK(
        length(proof_digest) = 71
        AND substr(proof_digest, 1, 7) = 'sha256:'
        AND substr(proof_digest, 8) NOT GLOB '*[^0-9a-f]*'
    ),
    evidence_payload_json TEXT NOT NULL
        CHECK(length(evidence_payload_json) BETWEEN 2 AND 262144),
    evidence_digest TEXT NOT NULL CHECK(
        length(evidence_digest) = 71
        AND substr(evidence_digest, 1, 7) = 'sha256:'
        AND substr(evidence_digest, 8) NOT GLOB '*[^0-9a-f]*'
    ),
    created_ts_ms INTEGER NOT NULL CHECK(
        typeof(created_ts_ms) = 'integer' AND created_ts_ms >= 0
    ),
    FOREIGN KEY (effect_id)
        REFERENCES task_runtime_acceptance_effect_intent(effect_id)
        ON DELETE RESTRICT,
    CHECK (
        (
            outcome = 'NO_SIDE_EFFECT'
            AND target_effect_status = 'PENDING'
            AND receipt_ref IS NULL
            AND executor_quiescence_ref IS NOT NULL
        )
        OR
        (
            outcome = 'SIDE_EFFECT_CONFIRMED'
            AND target_effect_status = 'COMMITTED'
            AND receipt_ref IS NOT NULL
        )
    )
);

CREATE INDEX IF NOT EXISTS
idx_task_runtime_acceptance_effect_recon_evidence_effect
    ON task_runtime_acceptance_effect_reconciliation_evidence(
        effect_id, created_ts_ms, evidence_ref
    );

CREATE TABLE IF NOT EXISTS task_runtime_acceptance_effect_reconciliation_case (
    case_id TEXT NOT NULL PRIMARY KEY
        CHECK(length(trim(case_id)) BETWEEN 1 AND 512),
    schema_version TEXT NOT NULL
        CHECK(
            schema_version =
            'Natsume.task_runtime.acceptance_effect_reconciliation.case.v2'
        ),
    purpose TEXT NOT NULL CHECK(purpose = 'GENERAL_UNKNOWN_EFFECT'),
    case_kind TEXT NOT NULL CHECK(case_kind = 'GENERAL_UNKNOWN_EFFECT'),
    classification TEXT NOT NULL CHECK(
        classification = 'REPAIRABLE_SIGNED'
    ),
    effect_id TEXT NOT NULL,
    graph_id TEXT NOT NULL,
    graph_revision INTEGER NOT NULL CHECK(
        typeof(graph_revision) = 'integer' AND graph_revision >= 1
    ),
    node_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    snapshot_json TEXT NOT NULL
        CHECK(length(snapshot_json) BETWEEN 2 AND 262144),
    snapshot_sha256 TEXT NOT NULL CHECK(
        length(snapshot_sha256) = 71
        AND substr(snapshot_sha256, 1, 7) = 'sha256:'
        AND substr(snapshot_sha256, 8) NOT GLOB '*[^0-9a-f]*'
    ),
    created_ts_ms INTEGER NOT NULL CHECK(
        typeof(created_ts_ms) = 'integer' AND created_ts_ms >= 0
    ),
    UNIQUE(effect_id, snapshot_sha256),
    UNIQUE(
        case_id, effect_id, graph_id, graph_revision, node_id, attempt_id
    ),
    FOREIGN KEY (effect_id)
        REFERENCES task_runtime_acceptance_effect_intent(effect_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (graph_id, graph_revision)
        REFERENCES task_runtime_graph(graph_id, revision)
        ON DELETE RESTRICT,
    FOREIGN KEY (graph_id, graph_revision, node_id)
        REFERENCES task_runtime_node(graph_id, graph_revision, node_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (attempt_id, graph_id, graph_revision, node_id)
        REFERENCES task_runtime_attempt(
            attempt_id, graph_id, graph_revision, node_id
        ) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_task_runtime_acceptance_effect_recon_case_effect
    ON task_runtime_acceptance_effect_reconciliation_case(
        effect_id, created_ts_ms, case_id
    );

CREATE TABLE IF NOT EXISTS task_runtime_acceptance_effect_reconciliation_proof (
    proof_id TEXT NOT NULL PRIMARY KEY
        CHECK(length(trim(proof_id)) BETWEEN 1 AND 512),
    schema_version TEXT NOT NULL
        CHECK(
            schema_version =
            'Natsume.task_runtime.acceptance_effect_reconciliation.proof.v2'
        ),
    purpose TEXT NOT NULL CHECK(purpose = 'GENERAL_UNKNOWN_EFFECT'),
    case_id TEXT NOT NULL UNIQUE,
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
    outcome TEXT NOT NULL CHECK(
        outcome IN ('NO_SIDE_EFFECT', 'SIDE_EFFECT_CONFIRMED')
    ),
    target_effect_status TEXT NOT NULL CHECK(
        target_effect_status IN ('PENDING', 'COMMITTED')
    ),
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
    FOREIGN KEY (
        case_id, effect_id, graph_id, graph_revision, node_id, attempt_id
    ) REFERENCES task_runtime_acceptance_effect_reconciliation_case(
        case_id, effect_id, graph_id, graph_revision, node_id, attempt_id
    ) ON DELETE RESTRICT,
    FOREIGN KEY (outbox_id)
        REFERENCES task_runtime_outbox(outbox_id) ON DELETE RESTRICT,
    CHECK (
        (
            outcome = 'NO_SIDE_EFFECT'
            AND target_effect_status = 'PENDING'
            AND receipt_ref IS NULL
            AND executor_quiescence_ref IS NOT NULL
        )
        OR
        (
            outcome = 'SIDE_EFFECT_CONFIRMED'
            AND target_effect_status = 'COMMITTED'
            AND receipt_ref IS NOT NULL
            AND length(trim(receipt_ref)) BETWEEN 1 AND 512
        )
    )
);

CREATE INDEX IF NOT EXISTS idx_task_runtime_acceptance_effect_recon_proof_effect
    ON task_runtime_acceptance_effect_reconciliation_proof(
        effect_id, created_ts_ms, proof_id
    );

CREATE TRIGGER IF NOT EXISTS
trg_task_acceptance_effect_reconciliation_evidence_no_update
BEFORE UPDATE ON task_runtime_acceptance_effect_reconciliation_evidence
BEGIN
    SELECT RAISE(
        ABORT,
        'Acceptance Effect reconciliation evidence is append-only'
    );
END;

CREATE TRIGGER IF NOT EXISTS
trg_task_acceptance_effect_reconciliation_evidence_no_delete
BEFORE DELETE ON task_runtime_acceptance_effect_reconciliation_evidence
BEGIN
    SELECT RAISE(
        ABORT,
        'Acceptance Effect reconciliation evidence is append-only'
    );
END;

CREATE TRIGGER IF NOT EXISTS
trg_task_acceptance_effect_reconciliation_case_no_update
BEFORE UPDATE ON task_runtime_acceptance_effect_reconciliation_case
BEGIN
    SELECT RAISE(
        ABORT,
        'Acceptance Effect reconciliation case is immutable'
    );
END;

CREATE TRIGGER IF NOT EXISTS
trg_task_acceptance_effect_reconciliation_case_no_delete
BEFORE DELETE ON task_runtime_acceptance_effect_reconciliation_case
BEGIN
    SELECT RAISE(
        ABORT,
        'Acceptance Effect reconciliation case is immutable'
    );
END;

CREATE TRIGGER IF NOT EXISTS
trg_task_acceptance_effect_reconciliation_proof_no_update
BEFORE UPDATE ON task_runtime_acceptance_effect_reconciliation_proof
BEGIN
    SELECT RAISE(
        ABORT,
        'Acceptance Effect reconciliation proof is append-only'
    );
END;

CREATE TRIGGER IF NOT EXISTS
trg_task_acceptance_effect_reconciliation_proof_no_delete
BEFORE DELETE ON task_runtime_acceptance_effect_reconciliation_proof
BEGIN
    SELECT RAISE(
        ABORT,
        'Acceptance Effect reconciliation proof is append-only'
    );
END;

-- The NO_SIDE_EFFECT marker is executable retry authority.  It may only be
-- installed after its immutable proof exists in the same transaction.
CREATE TRIGGER IF NOT EXISTS
trg_task_acceptance_effect_reconciled_retry_requires_proof
BEFORE UPDATE OF status, reason_code
ON task_runtime_acceptance_effect_intent
WHEN NEW.reason_code = 'ACCEPTANCE_EFFECT_RECONCILED_NO_SIDE_EFFECT'
 AND NOT EXISTS (
    SELECT 1
    FROM task_runtime_acceptance_effect_reconciliation_proof AS proof
    WHERE proof.effect_id = NEW.effect_id
      AND proof.outcome = 'NO_SIDE_EFFECT'
      AND proof.target_effect_status = 'PENDING'
 )
BEGIN
    SELECT RAISE(
        ABORT,
        'Acceptance Effect retry requires signed reconciliation proof'
    );
END;

-- A positive signed attribution is terminal for this Effect identity.  The
-- reconciliation transaction may perform the first exact COMMITTED update;
-- later attempts cannot reopen or substitute the receipt.
CREATE TRIGGER IF NOT EXISTS
trg_task_acceptance_effect_confirmed_proof_terminal
BEFORE UPDATE ON task_runtime_acceptance_effect_intent
WHEN EXISTS (
    SELECT 1
    FROM task_runtime_acceptance_effect_reconciliation_proof AS proof
    WHERE proof.effect_id = OLD.effect_id
      AND proof.outcome = 'SIDE_EFFECT_CONFIRMED'
      AND (
        NEW.status <> 'COMMITTED'
        OR NEW.receipt_ref IS NULL
        OR NEW.receipt_ref <> proof.receipt_ref
      )
 )
BEGIN
    SELECT RAISE(
        ABORT,
        'signed Acceptance Effect attribution is terminal'
    );
END;

COMMIT;
