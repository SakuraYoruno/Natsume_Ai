-- TASK-0487 / M48-B: durable Secure Ingress authority, replay, rate and audit state.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS secure_ingress_principal (
    principal_id TEXT PRIMARY KEY,
    principal_kind TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('ACTIVE', 'DISABLED', 'REVOKED')),
    version INTEGER NOT NULL CHECK(version >= 1),
    granted_scopes_json TEXT NOT NULL,
    allowed_audiences_json TEXT NOT NULL,
    allowed_actions_json TEXT NOT NULL,
    valid_from_ms INTEGER NOT NULL,
    valid_until_ms INTEGER,
    updated_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS secure_ingress_credential (
    principal_id TEXT NOT NULL,
    key_id TEXT NOT NULL,
    credential_kind TEXT NOT NULL,
    algorithm TEXT NOT NULL,
    secret_ref TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('ACTIVE', 'DISABLED', 'REVOKED')),
    version INTEGER NOT NULL CHECK(version >= 1),
    valid_from_ms INTEGER NOT NULL,
    valid_until_ms INTEGER,
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY (principal_id, key_id),
    FOREIGN KEY (principal_id) REFERENCES secure_ingress_principal(principal_id)
);

CREATE TABLE IF NOT EXISTS secure_ingress_rate_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rate_key_digest TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    action TEXT NOT NULL,
    required_scopes_json TEXT NOT NULL,
    rate_lane TEXT NOT NULL CHECK(rate_lane IN ('ATTEMPT', 'AUTHORITY')),
    ts_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS secure_ingress_nonce_claim (
    principal_id TEXT NOT NULL,
    nonce_digest TEXT NOT NULL,
    request_id TEXT NOT NULL,
    body_sha256 TEXT NOT NULL,
    claim_ref TEXT NOT NULL,
    claimed_at_ms INTEGER NOT NULL,
    expires_at_ms INTEGER NOT NULL,
    PRIMARY KEY (principal_id, nonce_digest)
);

CREATE TABLE IF NOT EXISTS secure_ingress_idempotency_claim (
    principal_id TEXT NOT NULL,
    surface TEXT NOT NULL,
    idempotency_digest TEXT NOT NULL,
    action TEXT NOT NULL,
    required_scopes_json TEXT NOT NULL,
    request_id TEXT NOT NULL,
    body_sha256 TEXT NOT NULL,
    claim_ref TEXT NOT NULL UNIQUE,
    result_ref TEXT,
    claim_status TEXT NOT NULL CHECK(claim_status IN ('PROVISIONAL', 'COMMITTED', 'ABORTED')),
    provisional_expires_at_ms INTEGER,
    claimed_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    expires_at_ms INTEGER NOT NULL,
    PRIMARY KEY (principal_id, surface, idempotency_digest),
    CHECK(claim_status != 'PROVISIONAL' OR provisional_expires_at_ms IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS secure_ingress_audit (
    audit_ref TEXT PRIMARY KEY,
    request_id TEXT,
    correlation_id TEXT,
    surface TEXT NOT NULL,
    action TEXT NOT NULL,
    method TEXT NOT NULL,
    normalized_path TEXT NOT NULL,
    principal_id TEXT,
    tenant_id TEXT,
    key_id TEXT,
    credential_kind TEXT,
    credential_ref_digest TEXT,
    requested_scopes_json TEXT NOT NULL,
    required_scopes_json TEXT NOT NULL,
    body_sha256 TEXT NOT NULL,
    idempotency_digest TEXT,
    nonce_digest TEXT,
    transport_key_digest TEXT,
    decision TEXT NOT NULL CHECK(decision IN ('ALLOW', 'DENY')),
    reason_code TEXT NOT NULL,
    stage TEXT NOT NULL,
    claim_ref TEXT,
    rate_snapshot_json TEXT,
    side_effects_executed INTEGER NOT NULL CHECK(side_effects_executed = 0),
    ts_ms INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_secure_ingress_credential_status
    ON secure_ingress_credential(principal_id, status, valid_from_ms, valid_until_ms);
CREATE INDEX IF NOT EXISTS idx_secure_ingress_rate_window
    ON secure_ingress_rate_event(rate_key_digest, ts_ms);
CREATE INDEX IF NOT EXISTS idx_secure_ingress_nonce_expiry
    ON secure_ingress_nonce_claim(expires_at_ms);
CREATE INDEX IF NOT EXISTS idx_secure_ingress_idempotency_expiry
    ON secure_ingress_idempotency_claim(expires_at_ms);
CREATE INDEX IF NOT EXISTS idx_secure_ingress_audit_request
    ON secure_ingress_audit(request_id, ts_ms);
CREATE INDEX IF NOT EXISTS idx_secure_ingress_audit_principal
    ON secure_ingress_audit(principal_id, ts_ms);
CREATE INDEX IF NOT EXISTS idx_secure_ingress_audit_reason
    ON secure_ingress_audit(reason_code, ts_ms);

COMMIT;
