-- TASK-0465 / LTE-WP-12: governed, durable WorldCanon apply boundary.
-- 0052 is intentionally reserved for TASK-0467.  Do not renumber this asset.

CREATE TABLE IF NOT EXISTS canon_apply_token (
    token_digest TEXT PRIMARY KEY CHECK(length(token_digest) = 64),
    token_type TEXT NOT NULL CHECK(token_type IN ('canon_apply', 'canon_rollback')),
    operation TEXT NOT NULL CHECK(operation IN ('APPLY', 'ROLLBACK')),
    subject_type TEXT NOT NULL CHECK(subject_type IN ('CANON_PROPOSAL', 'PENTEST_PROMOTION')),
    subject_id TEXT NOT NULL CHECK(length(trim(subject_id)) BETWEEN 1 AND 240),
    subject_version INTEGER NOT NULL CHECK(subject_version >= 0),
    subject_hash TEXT NOT NULL CHECK(length(subject_hash) = 64),
    target_doc_id TEXT NOT NULL CHECK(length(trim(target_doc_id)) BETWEEN 1 AND 160),
    base_version INTEGER NOT NULL CHECK(base_version >= 0),
    actor_id TEXT NOT NULL CHECK(length(trim(actor_id)) BETWEEN 1 AND 240),
    session_id TEXT NOT NULL CHECK(length(trim(session_id)) BETWEEN 1 AND 240),
    trace_id TEXT NOT NULL CHECK(length(trim(trace_id)) BETWEEN 1 AND 240),
    evidence_digest TEXT NOT NULL CHECK(length(evidence_digest) = 64),
    authority_key_id TEXT NOT NULL CHECK(length(trim(authority_key_id)) BETWEEN 1 AND 128),
    approval_attestation_json TEXT NOT NULL CHECK(length(approval_attestation_json) <= 8192),
    approval_attestation_digest TEXT NOT NULL UNIQUE CHECK(length(approval_attestation_digest) = 64),
    approval_signature TEXT NOT NULL CHECK(length(approval_signature) BETWEEN 80 AND 256),
    approval_nonce TEXT NOT NULL UNIQUE CHECK(length(approval_nonce) BETWEEN 16 AND 128),
    issued_ts_ms INTEGER NOT NULL CHECK(issued_ts_ms >= 0),
    expiry_ts_ms INTEGER NOT NULL CHECK(expiry_ts_ms > issued_ts_ms),
    state TEXT NOT NULL DEFAULT 'AVAILABLE' CHECK(state IN ('AVAILABLE', 'BURNED')),
    burned_apply_id TEXT,
    burned_ts_ms INTEGER,
    CHECK(
        (state = 'AVAILABLE' AND burned_apply_id IS NULL AND burned_ts_ms IS NULL)
        OR
        (state = 'BURNED' AND burned_apply_id IS NOT NULL AND burned_ts_ms IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_canon_apply_token_state_expiry
    ON canon_apply_token(state, expiry_ts_ms);

CREATE TABLE IF NOT EXISTS canon_apply_journal (
    apply_id TEXT PRIMARY KEY CHECK(length(trim(apply_id)) BETWEEN 1 AND 240),
    command_hash TEXT NOT NULL UNIQUE CHECK(length(command_hash) = 64),
    operation TEXT NOT NULL CHECK(operation IN ('APPLY', 'ROLLBACK')),
    subject_type TEXT NOT NULL CHECK(subject_type IN ('CANON_PROPOSAL', 'PENTEST_PROMOTION')),
    subject_id TEXT NOT NULL CHECK(length(trim(subject_id)) BETWEEN 1 AND 240),
    subject_version INTEGER NOT NULL CHECK(subject_version >= 0),
    subject_hash TEXT NOT NULL CHECK(length(subject_hash) = 64),
    target_doc_id TEXT NOT NULL CHECK(length(trim(target_doc_id)) BETWEEN 1 AND 160),
    base_version INTEGER NOT NULL CHECK(base_version >= 0),
    token_digest TEXT NOT NULL CHECK(length(token_digest) = 64),
    actor_id TEXT NOT NULL CHECK(length(trim(actor_id)) BETWEEN 1 AND 240),
    session_id TEXT NOT NULL CHECK(length(trim(session_id)) BETWEEN 1 AND 240),
    trace_id TEXT NOT NULL CHECK(length(trim(trace_id)) BETWEEN 1 AND 240),
    evidence_refs_json TEXT NOT NULL CHECK(length(evidence_refs_json) <= 8192),
    evidence_digest TEXT NOT NULL CHECK(length(evidence_digest) = 64),
    authority_key_id TEXT NOT NULL CHECK(length(trim(authority_key_id)) BETWEEN 1 AND 128),
    approval_attestation_digest TEXT NOT NULL CHECK(length(approval_attestation_digest) = 64),
    state TEXT NOT NULL CHECK(state IN ('PREPARED', 'APPLIED', 'FAILED', 'RECOVERY_REQUIRED')),
    subject_previous_status TEXT NOT NULL,
    subject_prepared_status TEXT NOT NULL,
    subject_final_status TEXT NOT NULL,
    before_payload_json TEXT,
    target_payload_json TEXT,
    before_version INTEGER NOT NULL CHECK(before_version >= 0),
    before_hash TEXT,
    after_version INTEGER NOT NULL CHECK(after_version >= 0),
    after_hash TEXT,
    fence_version INTEGER NOT NULL DEFAULT 1 CHECK(fence_version >= 1),
    file_replaced INTEGER NOT NULL DEFAULT 0 CHECK(file_replaced IN (0, 1)),
    recovery_not_before_ts_ms INTEGER NOT NULL CHECK(recovery_not_before_ts_ms >= prepared_ts_ms),
    lease_owner TEXT CHECK(lease_owner IS NULL OR length(trim(lease_owner)) BETWEEN 1 AND 240),
    lease_expiry_ts_ms INTEGER NOT NULL CHECK(lease_expiry_ts_ms >= prepared_ts_ms),
    reason_code TEXT,
    detail TEXT,
    prepared_ts_ms INTEGER NOT NULL CHECK(prepared_ts_ms >= 0),
    updated_ts_ms INTEGER NOT NULL CHECK(updated_ts_ms >= 0),
    completed_ts_ms INTEGER,
    FOREIGN KEY(token_digest) REFERENCES canon_apply_token(token_digest),
    UNIQUE(token_digest)
);

CREATE INDEX IF NOT EXISTS idx_canon_apply_journal_state_updated
    ON canon_apply_journal(state, updated_ts_ms);

CREATE INDEX IF NOT EXISTS idx_canon_apply_journal_subject
    ON canon_apply_journal(subject_type, subject_id, prepared_ts_ms DESC);

CREATE INDEX IF NOT EXISTS idx_canon_apply_journal_target
    ON canon_apply_journal(target_doc_id, prepared_ts_ms DESC);

-- Serialize the non-atomic file/SQLite window per document across processes.
-- Terminal journal rows release the target for a later, freshly-bound command.
CREATE UNIQUE INDEX IF NOT EXISTS uq_canon_apply_active_target
    ON canon_apply_journal(target_doc_id)
    WHERE state IN ('PREPARED', 'RECOVERY_REQUIRED');
