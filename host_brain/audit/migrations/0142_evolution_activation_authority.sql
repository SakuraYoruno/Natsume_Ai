-- NE-06: authoritative Feedback, Knowledge OS and Identity version activation.
-- 0110 remains an immutable historical ledger and is never promoted to V2
-- activation authority.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS evolution_policy_version_v2 (
    workspace_id TEXT NOT NULL CHECK(length(trim(workspace_id)) BETWEEN 1 AND 128),
    subject_id TEXT NOT NULL CHECK(length(trim(subject_id)) BETWEEN 1 AND 255),
    policy_kind TEXT NOT NULL CHECK(length(trim(policy_kind)) BETWEEN 1 AND 64),
    version TEXT NOT NULL CHECK(length(trim(version)) BETWEEN 1 AND 128),
    version_digest TEXT NOT NULL UNIQUE CHECK(length(version_digest) = 64),
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    provenance_ref TEXT NOT NULL CHECK(length(trim(provenance_ref)) BETWEEN 1 AND 1024),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
    PRIMARY KEY(workspace_id, subject_id, policy_kind, version)
);

CREATE TABLE IF NOT EXISTS evolution_feedback_proposal_v2 (
    proposal_id TEXT PRIMARY KEY CHECK(length(trim(proposal_id)) BETWEEN 1 AND 255),
    proposal_digest TEXT NOT NULL UNIQUE CHECK(length(proposal_digest) = 64),
    workspace_id TEXT NOT NULL CHECK(length(trim(workspace_id)) BETWEEN 1 AND 128),
    subject_id TEXT NOT NULL CHECK(length(trim(subject_id)) BETWEEN 1 AND 255),
    policy_kind TEXT NOT NULL CHECK(length(trim(policy_kind)) BETWEEN 1 AND 64),
    target_version TEXT NOT NULL CHECK(length(trim(target_version)) BETWEEN 1 AND 128),
    target_digest TEXT NOT NULL CHECK(length(target_digest) = 64),
    rollback_version TEXT NOT NULL CHECK(length(trim(rollback_version)) BETWEEN 1 AND 128),
    rollback_digest TEXT NOT NULL CHECK(length(rollback_digest) = 64),
    trace_digest TEXT NOT NULL CHECK(length(trace_digest) = 64),
    outcome_digest TEXT NOT NULL CHECK(length(outcome_digest) = 64),
    proposal_json TEXT NOT NULL CHECK(json_valid(proposal_json)),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0)
);

CREATE TABLE IF NOT EXISTS evolution_knowledge_entry_v2 (
    entry_id TEXT PRIMARY KEY CHECK(length(trim(entry_id)) BETWEEN 1 AND 255),
    entry_digest TEXT NOT NULL UNIQUE CHECK(length(entry_digest) = 64),
    namespace TEXT NOT NULL CHECK(
        namespace LIKE 'life-memory://person/%'
        OR namespace LIKE 'project-knowledge://workspace/%'
    ),
    logical_key TEXT NOT NULL CHECK(length(trim(logical_key)) BETWEEN 1 AND 255),
    workspace_id TEXT NOT NULL CHECK(length(trim(workspace_id)) BETWEEN 1 AND 128),
    owner_id TEXT NOT NULL CHECK(length(trim(owner_id)) BETWEEN 1 AND 128),
    content_summary TEXT NOT NULL CHECK(length(trim(content_summary)) BETWEEN 1 AND 4000),
    content_digest TEXT NOT NULL CHECK(length(content_digest) = 64),
    provenance_ref TEXT NOT NULL CHECK(length(trim(provenance_ref)) BETWEEN 1 AND 1024),
    version INTEGER NOT NULL CHECK(version >= 1),
    validity TEXT NOT NULL CHECK(validity IN ('PROPOSED', 'VALID', 'STALE', 'CONFLICTED')),
    confidence REAL NOT NULL CHECK(confidence >= 0.0 AND confidence <= 1.0),
    expires_at_ms INTEGER NOT NULL DEFAULT 0 CHECK(expires_at_ms >= 0),
    conflict_refs_json TEXT NOT NULL CHECK(json_valid(conflict_refs_json)),
    decision_refs_json TEXT NOT NULL CHECK(json_valid(decision_refs_json)),
    supersedes TEXT NOT NULL DEFAULT '',
    deprecated INTEGER NOT NULL CHECK(deprecated IN (0, 1)),
    entry_json TEXT NOT NULL CHECK(json_valid(entry_json)),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
    UNIQUE(workspace_id, namespace, logical_key, version)
);

CREATE TABLE IF NOT EXISTS evolution_decision_record_v1 (
    decision_id TEXT PRIMARY KEY CHECK(length(trim(decision_id)) BETWEEN 1 AND 1024),
    decision_digest TEXT NOT NULL UNIQUE CHECK(length(decision_digest) = 64),
    workspace_id TEXT NOT NULL CHECK(length(trim(workspace_id)) BETWEEN 1 AND 128),
    owner_id TEXT NOT NULL CHECK(length(trim(owner_id)) BETWEEN 1 AND 128),
    decision_key TEXT NOT NULL CHECK(length(trim(decision_key)) BETWEEN 1 AND 255),
    version INTEGER NOT NULL CHECK(version >= 1),
    status TEXT NOT NULL CHECK(
        status IN ('PROPOSED', 'ACCEPTED', 'SUPERSEDED', 'DEPRECATED')
    ),
    supersedes TEXT NOT NULL DEFAULT '',
    knowledge_entry_id TEXT NOT NULL UNIQUE
        REFERENCES evolution_knowledge_entry_v2(entry_id) ON DELETE RESTRICT,
    record_json TEXT NOT NULL CHECK(json_valid(record_json)),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
    UNIQUE(workspace_id, decision_key, version)
);

CREATE TABLE IF NOT EXISTS evolution_identity_snapshot_v2 (
    snapshot_ref TEXT PRIMARY KEY CHECK(length(trim(snapshot_ref)) BETWEEN 1 AND 1024),
    workspace_id TEXT NOT NULL CHECK(length(trim(workspace_id)) BETWEEN 1 AND 128),
    release_ref TEXT NOT NULL CHECK(length(trim(release_ref)) BETWEEN 1 AND 1024),
    previous_release_ref TEXT,
    identity_digest TEXT NOT NULL CHECK(length(identity_digest) = 71),
    snapshot_hash TEXT NOT NULL UNIQUE CHECK(length(snapshot_hash) = 71),
    snapshot_json TEXT NOT NULL CHECK(json_valid(snapshot_json)),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0)
);

CREATE TABLE IF NOT EXISTS evolution_identity_regression_v2 (
    report_id TEXT PRIMARY KEY CHECK(length(trim(report_id)) BETWEEN 1 AND 255),
    report_digest TEXT NOT NULL UNIQUE CHECK(length(report_digest) = 64),
    workspace_id TEXT NOT NULL CHECK(length(trim(workspace_id)) BETWEEN 1 AND 128),
    previous_snapshot_ref TEXT NOT NULL
        REFERENCES evolution_identity_snapshot_v2(snapshot_ref) ON DELETE RESTRICT,
    proposed_snapshot_ref TEXT NOT NULL
        REFERENCES evolution_identity_snapshot_v2(snapshot_ref) ON DELETE RESTRICT,
    diff_digest TEXT NOT NULL CHECK(length(diff_digest) = 64),
    severity TEXT NOT NULL CHECK(severity IN ('NONE', 'LOW', 'MODERATE', 'MAJOR')),
    passed INTEGER NOT NULL CHECK(passed IN (0, 1)),
    report_json TEXT NOT NULL CHECK(json_valid(report_json)),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0)
);

CREATE TABLE IF NOT EXISTS evolution_identity_plan_v2 (
    migration_id TEXT PRIMARY KEY CHECK(length(trim(migration_id)) BETWEEN 1 AND 255),
    plan_digest TEXT NOT NULL UNIQUE CHECK(length(plan_digest) = 64),
    workspace_id TEXT NOT NULL CHECK(length(trim(workspace_id)) BETWEEN 1 AND 128),
    previous_snapshot_ref TEXT NOT NULL
        REFERENCES evolution_identity_snapshot_v2(snapshot_ref) ON DELETE RESTRICT,
    proposed_snapshot_ref TEXT NOT NULL
        REFERENCES evolution_identity_snapshot_v2(snapshot_ref) ON DELETE RESTRICT,
    previous_snapshot_digest TEXT NOT NULL CHECK(length(previous_snapshot_digest) = 64),
    proposed_snapshot_digest TEXT NOT NULL CHECK(length(proposed_snapshot_digest) = 64),
    diff_digest TEXT NOT NULL CHECK(length(diff_digest) = 64),
    lab_report_digest TEXT NOT NULL
        REFERENCES evolution_lab_trusted_report(report_digest) ON DELETE RESTRICT,
    regression_report_digest TEXT NOT NULL
        REFERENCES evolution_identity_regression_v2(report_digest) ON DELETE RESTRICT,
    review_id TEXT NOT NULL REFERENCES agent_runtime_review(review_id) ON DELETE RESTRICT,
    review_status_version INTEGER NOT NULL CHECK(review_status_version >= 1),
    rollback_snapshot_ref TEXT NOT NULL
        REFERENCES evolution_identity_snapshot_v2(snapshot_ref) ON DELETE RESTRICT,
    activation_version INTEGER NOT NULL CHECK(activation_version >= 1),
    no_change_release INTEGER NOT NULL CHECK(no_change_release IN (0, 1)),
    plan_json TEXT NOT NULL CHECK(json_valid(plan_json)),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0)
);

CREATE TABLE IF NOT EXISTS evolution_version_decision_v1 (
    decision_id TEXT PRIMARY KEY CHECK(length(trim(decision_id)) BETWEEN 1 AND 255),
    decision_digest TEXT NOT NULL UNIQUE CHECK(length(decision_digest) = 64),
    operation_id TEXT NOT NULL UNIQUE,
    workspace_id TEXT NOT NULL CHECK(length(trim(workspace_id)) BETWEEN 1 AND 128),
    subject_kind TEXT NOT NULL CHECK(subject_kind IN ('FEEDBACK_POLICY', 'KNOWLEDGE', 'IDENTITY')),
    subject_id TEXT NOT NULL CHECK(length(trim(subject_id)) BETWEEN 1 AND 255),
    action TEXT NOT NULL CHECK(action IN ('APPLY', 'ROLLBACK', 'ACTIVATE', 'DEPRECATE')),
    target_version TEXT NOT NULL CHECK(length(trim(target_version)) BETWEEN 1 AND 128),
    target_digest TEXT NOT NULL CHECK(length(target_digest) = 64),
    previous_version TEXT NOT NULL,
    previous_digest TEXT NOT NULL,
    expected_generation INTEGER NOT NULL CHECK(expected_generation >= 0),
    authority_refs_json TEXT NOT NULL CHECK(json_valid(authority_refs_json)),
    reason_code TEXT NOT NULL CHECK(length(trim(reason_code)) BETWEEN 3 AND 128),
    decision_json TEXT NOT NULL CHECK(json_valid(decision_json)),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
    CHECK((previous_version = '' AND previous_digest = '') OR length(previous_digest) = 64)
);

CREATE TABLE IF NOT EXISTS evolution_activation_operation_v2 (
    operation_id TEXT PRIMARY KEY CHECK(length(trim(operation_id)) BETWEEN 1 AND 255),
    idempotency_digest TEXT NOT NULL UNIQUE CHECK(length(idempotency_digest) = 64),
    request_digest TEXT NOT NULL CHECK(length(request_digest) = 64),
    workspace_id TEXT NOT NULL CHECK(length(trim(workspace_id)) BETWEEN 1 AND 128),
    subject_kind TEXT NOT NULL CHECK(subject_kind IN ('FEEDBACK_POLICY', 'KNOWLEDGE', 'IDENTITY')),
    subject_id TEXT NOT NULL CHECK(length(trim(subject_id)) BETWEEN 1 AND 255),
    action TEXT NOT NULL CHECK(action IN ('APPLY', 'ROLLBACK', 'ACTIVATE', 'DEPRECATE')),
    decision_id TEXT NOT NULL UNIQUE
        REFERENCES evolution_version_decision_v1(decision_id) ON DELETE RESTRICT,
    receipt_id TEXT NOT NULL UNIQUE,
    request_json TEXT NOT NULL CHECK(json_valid(request_json)),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0)
);

CREATE TABLE IF NOT EXISTS evolution_policy_active_projection_v2 (
    workspace_id TEXT NOT NULL CHECK(length(trim(workspace_id)) BETWEEN 1 AND 128),
    subject_id TEXT NOT NULL CHECK(length(trim(subject_id)) BETWEEN 1 AND 255),
    policy_kind TEXT NOT NULL CHECK(length(trim(policy_kind)) BETWEEN 1 AND 64),
    active_version TEXT NOT NULL CHECK(length(trim(active_version)) BETWEEN 1 AND 128),
    active_digest TEXT NOT NULL CHECK(length(active_digest) = 64),
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    activation_generation INTEGER NOT NULL CHECK(activation_generation >= 1),
    projection_digest TEXT NOT NULL UNIQUE CHECK(length(projection_digest) = 64),
    updated_at_ms INTEGER NOT NULL CHECK(updated_at_ms >= 0),
    PRIMARY KEY(workspace_id, subject_id, policy_kind)
);

CREATE TABLE IF NOT EXISTS evolution_knowledge_active_projection_v2 (
    workspace_id TEXT NOT NULL CHECK(length(trim(workspace_id)) BETWEEN 1 AND 128),
    namespace TEXT NOT NULL,
    logical_key TEXT NOT NULL CHECK(length(trim(logical_key)) BETWEEN 1 AND 255),
    entry_id TEXT NOT NULL UNIQUE
        REFERENCES evolution_knowledge_entry_v2(entry_id) ON DELETE RESTRICT,
    entry_digest TEXT NOT NULL CHECK(length(entry_digest) = 64),
    activation_generation INTEGER NOT NULL CHECK(activation_generation >= 1),
    projection_digest TEXT NOT NULL UNIQUE CHECK(length(projection_digest) = 64),
    updated_at_ms INTEGER NOT NULL CHECK(updated_at_ms >= 0),
    PRIMARY KEY(workspace_id, namespace, logical_key)
);

CREATE TABLE IF NOT EXISTS evolution_identity_active_projection_v2 (
    workspace_id TEXT PRIMARY KEY CHECK(length(trim(workspace_id)) BETWEEN 1 AND 128),
    snapshot_ref TEXT NOT NULL
        REFERENCES evolution_identity_snapshot_v2(snapshot_ref) ON DELETE RESTRICT,
    identity_digest TEXT NOT NULL CHECK(length(identity_digest) = 71),
    snapshot_hash TEXT NOT NULL CHECK(length(snapshot_hash) = 71),
    activation_version INTEGER NOT NULL CHECK(activation_version >= 1),
    activation_generation INTEGER NOT NULL CHECK(activation_generation >= 1),
    projection_digest TEXT NOT NULL UNIQUE CHECK(length(projection_digest) = 64),
    updated_at_ms INTEGER NOT NULL CHECK(updated_at_ms >= 0)
);

CREATE TABLE IF NOT EXISTS evolution_transition_receipt_v1 (
    receipt_id TEXT PRIMARY KEY CHECK(length(trim(receipt_id)) BETWEEN 1 AND 255),
    receipt_digest TEXT NOT NULL UNIQUE CHECK(length(receipt_digest) = 64),
    operation_id TEXT NOT NULL UNIQUE
        REFERENCES evolution_activation_operation_v2(operation_id) ON DELETE RESTRICT,
    decision_digest TEXT NOT NULL
        REFERENCES evolution_version_decision_v1(decision_digest) ON DELETE RESTRICT,
    workspace_id TEXT NOT NULL CHECK(length(trim(workspace_id)) BETWEEN 1 AND 128),
    subject_kind TEXT NOT NULL CHECK(subject_kind IN ('FEEDBACK_POLICY', 'KNOWLEDGE', 'IDENTITY')),
    subject_id TEXT NOT NULL CHECK(length(trim(subject_id)) BETWEEN 1 AND 255),
    event TEXT NOT NULL CHECK(event IN ('APPLIED', 'ROLLED_BACK', 'ACTIVATED', 'DEPRECATED', 'REJECTED')),
    target_version TEXT NOT NULL CHECK(length(trim(target_version)) BETWEEN 1 AND 128),
    target_digest TEXT NOT NULL CHECK(length(target_digest) = 64),
    previous_version TEXT NOT NULL,
    previous_digest TEXT NOT NULL,
    active_generation INTEGER NOT NULL CHECK(active_generation >= 1),
    projection_digest TEXT NOT NULL CHECK(length(projection_digest) = 64),
    receipt_json TEXT NOT NULL CHECK(json_valid(receipt_json)),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
    CHECK((previous_version = '' AND previous_digest = '') OR length(previous_digest) = 64)
);

CREATE TABLE IF NOT EXISTS evolution_failure_evidence_v1 (
    failure_id TEXT PRIMARY KEY CHECK(length(trim(failure_id)) BETWEEN 1 AND 255),
    workspace_id TEXT NOT NULL CHECK(length(trim(workspace_id)) BETWEEN 1 AND 128),
    subject_kind TEXT NOT NULL CHECK(length(trim(subject_kind)) BETWEEN 1 AND 32),
    subject_id_digest TEXT NOT NULL CHECK(length(subject_id_digest) = 64),
    operation TEXT NOT NULL CHECK(length(trim(operation)) BETWEEN 1 AND 64),
    reason_code TEXT NOT NULL CHECK(length(trim(reason_code)) BETWEEN 3 AND 128),
    detail_digest TEXT NOT NULL CHECK(length(detail_digest) = 64),
    evidence_json TEXT NOT NULL CHECK(json_valid(evidence_json)),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0)
);

CREATE TABLE IF NOT EXISTS evolution_knowledge_shadow_compare_v1 (
    compare_id TEXT PRIMARY KEY CHECK(length(trim(compare_id)) BETWEEN 1 AND 255),
    workspace_id TEXT NOT NULL CHECK(length(trim(workspace_id)) BETWEEN 1 AND 128),
    namespace TEXT NOT NULL,
    query_digest TEXT NOT NULL CHECK(length(query_digest) = 64),
    active_result_digest TEXT NOT NULL CHECK(length(active_result_digest) = 64),
    legacy_result_digest TEXT NOT NULL CHECK(length(legacy_result_digest) = 64),
    active_count INTEGER NOT NULL CHECK(active_count >= 0),
    legacy_count INTEGER NOT NULL CHECK(legacy_count >= 0),
    compare_json TEXT NOT NULL CHECK(json_valid(compare_json)),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0)
);

CREATE INDEX IF NOT EXISTS idx_evolution_knowledge_v2_scope
    ON evolution_knowledge_entry_v2(workspace_id, namespace, logical_key, version);
CREATE INDEX IF NOT EXISTS idx_evolution_decision_v1_scope
    ON evolution_decision_record_v1(workspace_id, decision_key, version);
CREATE INDEX IF NOT EXISTS idx_evolution_receipt_v1_subject
    ON evolution_transition_receipt_v1(workspace_id, subject_kind, subject_id, created_at_ms);
CREATE INDEX IF NOT EXISTS idx_evolution_failure_v1_time
    ON evolution_failure_evidence_v1(created_at_ms, failure_id);

CREATE TRIGGER IF NOT EXISTS evolution_policy_version_v2_no_update
BEFORE UPDATE ON evolution_policy_version_v2
BEGIN SELECT RAISE(ABORT, 'Evolution policy version is immutable'); END;
CREATE TRIGGER IF NOT EXISTS evolution_policy_version_v2_no_delete
BEFORE DELETE ON evolution_policy_version_v2
BEGIN SELECT RAISE(ABORT, 'Evolution policy version is append-only'); END;
CREATE TRIGGER IF NOT EXISTS evolution_feedback_proposal_v2_no_update
BEFORE UPDATE ON evolution_feedback_proposal_v2
BEGIN SELECT RAISE(ABORT, 'Evolution feedback proposal V2 is immutable'); END;
CREATE TRIGGER IF NOT EXISTS evolution_feedback_proposal_v2_no_delete
BEFORE DELETE ON evolution_feedback_proposal_v2
BEGIN SELECT RAISE(ABORT, 'Evolution feedback proposal V2 is append-only'); END;
CREATE TRIGGER IF NOT EXISTS evolution_knowledge_entry_v2_no_update
BEFORE UPDATE ON evolution_knowledge_entry_v2
BEGIN SELECT RAISE(ABORT, 'Knowledge entry V2 is immutable'); END;
CREATE TRIGGER IF NOT EXISTS evolution_knowledge_entry_v2_no_delete
BEFORE DELETE ON evolution_knowledge_entry_v2
BEGIN SELECT RAISE(ABORT, 'Knowledge entry V2 is append-only'); END;
CREATE TRIGGER IF NOT EXISTS evolution_decision_record_v1_no_update
BEFORE UPDATE ON evolution_decision_record_v1
BEGIN SELECT RAISE(ABORT, 'Decision record V1 is immutable'); END;
CREATE TRIGGER IF NOT EXISTS evolution_decision_record_v1_no_delete
BEFORE DELETE ON evolution_decision_record_v1
BEGIN SELECT RAISE(ABORT, 'Decision record V1 is append-only'); END;
CREATE TRIGGER IF NOT EXISTS evolution_identity_snapshot_v2_no_update
BEFORE UPDATE ON evolution_identity_snapshot_v2
BEGIN SELECT RAISE(ABORT, 'Identity snapshot V2 is immutable'); END;
CREATE TRIGGER IF NOT EXISTS evolution_identity_snapshot_v2_no_delete
BEFORE DELETE ON evolution_identity_snapshot_v2
BEGIN SELECT RAISE(ABORT, 'Identity snapshot V2 is append-only'); END;
CREATE TRIGGER IF NOT EXISTS evolution_identity_regression_v2_no_update
BEFORE UPDATE ON evolution_identity_regression_v2
BEGIN SELECT RAISE(ABORT, 'Identity regression V2 is immutable'); END;
CREATE TRIGGER IF NOT EXISTS evolution_identity_regression_v2_no_delete
BEFORE DELETE ON evolution_identity_regression_v2
BEGIN SELECT RAISE(ABORT, 'Identity regression V2 is append-only'); END;
CREATE TRIGGER IF NOT EXISTS evolution_identity_plan_v2_no_update
BEFORE UPDATE ON evolution_identity_plan_v2
BEGIN SELECT RAISE(ABORT, 'Identity plan V2 is immutable'); END;
CREATE TRIGGER IF NOT EXISTS evolution_identity_plan_v2_no_delete
BEFORE DELETE ON evolution_identity_plan_v2
BEGIN SELECT RAISE(ABORT, 'Identity plan V2 is append-only'); END;
CREATE TRIGGER IF NOT EXISTS evolution_version_decision_v1_no_update
BEFORE UPDATE ON evolution_version_decision_v1
BEGIN SELECT RAISE(ABORT, 'Evolution decision is immutable'); END;
CREATE TRIGGER IF NOT EXISTS evolution_version_decision_v1_no_delete
BEFORE DELETE ON evolution_version_decision_v1
BEGIN SELECT RAISE(ABORT, 'Evolution decision is append-only'); END;
CREATE TRIGGER IF NOT EXISTS evolution_activation_operation_v2_no_update
BEFORE UPDATE ON evolution_activation_operation_v2
BEGIN SELECT RAISE(ABORT, 'Evolution operation is immutable'); END;
CREATE TRIGGER IF NOT EXISTS evolution_activation_operation_v2_no_delete
BEFORE DELETE ON evolution_activation_operation_v2
BEGIN SELECT RAISE(ABORT, 'Evolution operation is append-only'); END;
CREATE TRIGGER IF NOT EXISTS evolution_transition_receipt_v1_no_update
BEFORE UPDATE ON evolution_transition_receipt_v1
BEGIN SELECT RAISE(ABORT, 'Evolution receipt is immutable'); END;
CREATE TRIGGER IF NOT EXISTS evolution_transition_receipt_v1_no_delete
BEFORE DELETE ON evolution_transition_receipt_v1
BEGIN SELECT RAISE(ABORT, 'Evolution receipt is append-only'); END;
CREATE TRIGGER IF NOT EXISTS evolution_failure_evidence_v1_no_update
BEFORE UPDATE ON evolution_failure_evidence_v1
BEGIN SELECT RAISE(ABORT, 'Evolution failure evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS evolution_failure_evidence_v1_no_delete
BEFORE DELETE ON evolution_failure_evidence_v1
BEGIN SELECT RAISE(ABORT, 'Evolution failure evidence is append-only'); END;
CREATE TRIGGER IF NOT EXISTS evolution_knowledge_shadow_compare_v1_no_update
BEFORE UPDATE ON evolution_knowledge_shadow_compare_v1
BEGIN SELECT RAISE(ABORT, 'Knowledge shadow comparison is immutable'); END;
CREATE TRIGGER IF NOT EXISTS evolution_knowledge_shadow_compare_v1_no_delete
BEFORE DELETE ON evolution_knowledge_shadow_compare_v1
BEGIN SELECT RAISE(ABORT, 'Knowledge shadow comparison is append-only'); END;

CREATE TRIGGER IF NOT EXISTS evolution_policy_projection_v2_insert_guard
BEFORE INSERT ON evolution_policy_active_projection_v2
WHEN natsume_evolution_activation_write_authorized() <> 1
BEGIN SELECT RAISE(ABORT, 'Evolution policy projection requires V2 authority'); END;
CREATE TRIGGER IF NOT EXISTS evolution_policy_projection_v2_update_guard
BEFORE UPDATE ON evolution_policy_active_projection_v2
WHEN natsume_evolution_activation_write_authorized() <> 1
BEGIN SELECT RAISE(ABORT, 'Evolution policy projection requires V2 authority'); END;
CREATE TRIGGER IF NOT EXISTS evolution_policy_projection_v2_delete_guard
BEFORE DELETE ON evolution_policy_active_projection_v2
WHEN natsume_evolution_activation_write_authorized() <> 1
BEGIN SELECT RAISE(ABORT, 'Evolution policy projection requires V2 authority'); END;
CREATE TRIGGER IF NOT EXISTS evolution_knowledge_projection_v2_insert_guard
BEFORE INSERT ON evolution_knowledge_active_projection_v2
WHEN natsume_evolution_activation_write_authorized() <> 1
BEGIN SELECT RAISE(ABORT, 'Knowledge projection requires V2 authority'); END;
CREATE TRIGGER IF NOT EXISTS evolution_knowledge_projection_v2_update_guard
BEFORE UPDATE ON evolution_knowledge_active_projection_v2
WHEN natsume_evolution_activation_write_authorized() <> 1
BEGIN SELECT RAISE(ABORT, 'Knowledge projection requires V2 authority'); END;
CREATE TRIGGER IF NOT EXISTS evolution_knowledge_projection_v2_delete_guard
BEFORE DELETE ON evolution_knowledge_active_projection_v2
WHEN natsume_evolution_activation_write_authorized() <> 1
BEGIN SELECT RAISE(ABORT, 'Knowledge projection requires V2 authority'); END;
CREATE TRIGGER IF NOT EXISTS evolution_identity_projection_v2_insert_guard
BEFORE INSERT ON evolution_identity_active_projection_v2
WHEN natsume_evolution_activation_write_authorized() <> 1
BEGIN SELECT RAISE(ABORT, 'Identity projection requires V2 authority'); END;
CREATE TRIGGER IF NOT EXISTS evolution_identity_projection_v2_update_guard
BEFORE UPDATE ON evolution_identity_active_projection_v2
WHEN natsume_evolution_activation_write_authorized() <> 1
BEGIN SELECT RAISE(ABORT, 'Identity projection requires V2 authority'); END;
CREATE TRIGGER IF NOT EXISTS evolution_identity_projection_v2_delete_guard
BEFORE DELETE ON evolution_identity_active_projection_v2
WHEN natsume_evolution_activation_write_authorized() <> 1
BEGIN SELECT RAISE(ABORT, 'Identity projection requires V2 authority'); END;

CREATE TRIGGER IF NOT EXISTS evolution_feedback_proposal_v1_read_only
BEFORE INSERT ON evolution_feedback_proposal
BEGIN SELECT RAISE(ABORT, 'Evolution governance V1 is read-only'); END;
CREATE TRIGGER IF NOT EXISTS evolution_feedback_event_v1_read_only
BEFORE INSERT ON evolution_feedback_event
BEGIN SELECT RAISE(ABORT, 'Evolution governance V1 is read-only'); END;
CREATE TRIGGER IF NOT EXISTS evolution_knowledge_entry_v1_read_only
BEFORE INSERT ON evolution_knowledge_entry
BEGIN SELECT RAISE(ABORT, 'Evolution governance V1 is read-only'); END;
CREATE TRIGGER IF NOT EXISTS evolution_knowledge_event_v1_read_only
BEFORE INSERT ON evolution_knowledge_event
BEGIN SELECT RAISE(ABORT, 'Evolution governance V1 is read-only'); END;
CREATE TRIGGER IF NOT EXISTS evolution_identity_plan_v1_read_only
BEFORE INSERT ON evolution_identity_plan
BEGIN SELECT RAISE(ABORT, 'Evolution governance V1 is read-only'); END;
CREATE TRIGGER IF NOT EXISTS evolution_identity_event_v1_read_only
BEFORE INSERT ON evolution_identity_event
BEGIN SELECT RAISE(ABORT, 'Evolution governance V1 is read-only'); END;

COMMIT;
