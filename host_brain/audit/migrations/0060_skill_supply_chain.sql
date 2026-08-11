-- TASK-0490 / M48-B: durable Skill/Provider supply-chain quarantine and activation state.
-- Expand-only. Runtime repositories verify this catalog-owned schema and never create it.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS skill_supply_chain_bundle (
    bundle_id TEXT PRIMARY KEY CHECK(length(trim(bundle_id)) BETWEEN 1 AND 255),
    bundle_kind TEXT NOT NULL CHECK(bundle_kind IN ('SKILL', 'PROVIDER')),
    bundle_name TEXT NOT NULL CHECK(length(trim(bundle_name)) BETWEEN 1 AND 255),
    bundle_version TEXT NOT NULL CHECK(length(trim(bundle_version)) BETWEEN 1 AND 128),
    publisher_id TEXT NOT NULL CHECK(length(trim(publisher_id)) BETWEEN 1 AND 255),
    bundle_digest TEXT NOT NULL
        CHECK(
            length(bundle_digest) = 71
            AND substr(bundle_digest, 1, 7) = 'sha256:'
            AND substr(bundle_digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
    signature_key_id TEXT CHECK(
        signature_key_id IS NULL OR length(trim(signature_key_id)) BETWEEN 1 AND 255
    ),
    signature_algorithm TEXT CHECK(
        signature_algorithm IS NULL OR signature_algorithm IN (
            'ED25519', 'ECDSA_P256_SHA256', 'RSA_PSS_SHA256', 'HMAC_SHA256'
        )
    ),
    signature_verified INTEGER NOT NULL DEFAULT 0 CHECK(signature_verified IN (0, 1)),
    signature_verification_audit_ref TEXT CHECK(
        signature_verification_audit_ref IS NULL
        OR length(trim(signature_verification_audit_ref)) BETWEEN 1 AND 1024
    ),
    sbom_digest TEXT CHECK(
        sbom_digest IS NULL
        OR (
            length(sbom_digest) = 71
            AND substr(sbom_digest, 1, 7) = 'sha256:'
            AND substr(sbom_digest, 8) NOT GLOB '*[^0-9a-f]*'
        )
    ),
    quarantine_audit_ref TEXT NOT NULL
        CHECK(length(trim(quarantine_audit_ref)) BETWEEN 1 AND 1024),
    sandbox_profile_id TEXT CHECK(
        sandbox_profile_id IS NULL OR length(trim(sandbox_profile_id)) BETWEEN 1 AND 255
    ),
    sandbox_profile_digest TEXT CHECK(
        sandbox_profile_digest IS NULL
        OR (
            length(sandbox_profile_digest) = 71
            AND substr(sandbox_profile_digest, 1, 7) = 'sha256:'
            AND substr(sandbox_profile_digest, 8) NOT GLOB '*[^0-9a-f]*'
        )
    ),
    state TEXT NOT NULL DEFAULT 'QUARANTINED'
        CHECK(state IN ('QUARANTINED', 'STAGED', 'ACTIVE', 'REJECTED')),
    reason_code TEXT NOT NULL CHECK(length(trim(reason_code)) BETWEEN 3 AND 128),
    latest_audit_ref TEXT NOT NULL CHECK(length(trim(latest_audit_ref)) BETWEEN 1 AND 1024),
    intake_idempotency_digest TEXT NOT NULL UNIQUE
        CHECK(
            length(intake_idempotency_digest) = 71
            AND substr(intake_idempotency_digest, 1, 7) = 'sha256:'
            AND substr(intake_idempotency_digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
    activation_generation INTEGER NOT NULL DEFAULT 0 CHECK(activation_generation >= 0),
    activation_fencing_token INTEGER NOT NULL DEFAULT 0 CHECK(activation_fencing_token >= 0),
    status_version INTEGER NOT NULL DEFAULT 0 CHECK(status_version >= 0),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
    updated_at_ms INTEGER NOT NULL CHECK(updated_at_ms >= created_at_ms),
    staged_at_ms INTEGER,
    activated_at_ms INTEGER,
    rejected_at_ms INTEGER,
    UNIQUE(bundle_kind, bundle_name, bundle_version),
    CHECK(
        signature_verified = 0
        OR (
            signature_key_id IS NOT NULL
            AND signature_algorithm IS NOT NULL
            AND signature_verification_audit_ref IS NOT NULL
        )
    ),
    CHECK(
        state NOT IN ('STAGED', 'ACTIVE')
        OR (
            signature_verified = 1
            AND signature_key_id IS NOT NULL
            AND signature_algorithm IS NOT NULL
            AND signature_verification_audit_ref IS NOT NULL
            AND sbom_digest IS NOT NULL
            AND sandbox_profile_id IS NOT NULL
            AND sandbox_profile_digest IS NOT NULL
            AND staged_at_ms IS NOT NULL
        )
    ),
    CHECK(
        state != 'ACTIVE'
        OR (
            activation_generation >= 1
            AND activation_fencing_token >= 1
            AND activated_at_ms IS NOT NULL
        )
    ),
    CHECK(state != 'REJECTED' OR rejected_at_ms IS NOT NULL),
    CHECK(
        state != 'QUARANTINED'
        OR (activation_generation = 0 AND activation_fencing_token = 0)
    )
);

CREATE TABLE IF NOT EXISTS skill_supply_chain_operation (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id TEXT NOT NULL UNIQUE CHECK(length(trim(operation_id)) BETWEEN 1 AND 255),
    idempotency_digest TEXT NOT NULL UNIQUE
        CHECK(
            length(idempotency_digest) = 71
            AND substr(idempotency_digest, 1, 7) = 'sha256:'
            AND substr(idempotency_digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
    request_digest TEXT NOT NULL
        CHECK(
            length(request_digest) = 71
            AND substr(request_digest, 1, 7) = 'sha256:'
            AND substr(request_digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
    operation TEXT NOT NULL CHECK(operation IN ('INTAKE', 'STAGE', 'ACTIVATE', 'REJECT')),
    bundle_id TEXT NOT NULL,
    previous_state TEXT CHECK(
        previous_state IS NULL
        OR previous_state IN ('QUARANTINED', 'STAGED', 'ACTIVE', 'REJECTED')
    ),
    new_state TEXT NOT NULL CHECK(new_state IN ('QUARANTINED', 'STAGED', 'ACTIVE', 'REJECTED')),
    replaced_bundle_id TEXT,
    activation_generation INTEGER NOT NULL DEFAULT 0 CHECK(activation_generation >= 0),
    activation_fencing_token INTEGER NOT NULL DEFAULT 0 CHECK(activation_fencing_token >= 0),
    status_version INTEGER NOT NULL CHECK(status_version >= 0),
    reason_code TEXT NOT NULL CHECK(length(trim(reason_code)) BETWEEN 3 AND 128),
    audit_ref TEXT NOT NULL CHECK(length(trim(audit_ref)) BETWEEN 1 AND 1024),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
    FOREIGN KEY(bundle_id) REFERENCES skill_supply_chain_bundle(bundle_id) ON DELETE RESTRICT,
    FOREIGN KEY(replaced_bundle_id) REFERENCES skill_supply_chain_bundle(bundle_id) ON DELETE RESTRICT,
    CHECK(
        (operation = 'INTAKE' AND previous_state IS NULL AND new_state = 'QUARANTINED')
        OR (operation = 'STAGE' AND previous_state = 'QUARANTINED' AND new_state = 'STAGED')
        OR (operation = 'ACTIVATE' AND previous_state = 'STAGED' AND new_state = 'ACTIVE')
        OR (
            operation = 'REJECT'
            AND previous_state IN ('QUARANTINED', 'STAGED', 'ACTIVE')
            AND new_state = 'REJECTED'
        )
    ),
    CHECK(operation = 'ACTIVATE' OR replaced_bundle_id IS NULL),
    CHECK(
        operation != 'ACTIVATE'
        OR (activation_generation >= 1 AND activation_fencing_token >= 1)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_skill_supply_chain_active_identity
    ON skill_supply_chain_bundle(bundle_kind, bundle_name)
    WHERE state = 'ACTIVE';
CREATE INDEX IF NOT EXISTS idx_skill_supply_chain_bundle_state
    ON skill_supply_chain_bundle(state, bundle_kind, bundle_name, updated_at_ms);
CREATE INDEX IF NOT EXISTS idx_skill_supply_chain_bundle_digest
    ON skill_supply_chain_bundle(bundle_digest);
CREATE INDEX IF NOT EXISTS idx_skill_supply_chain_operation_bundle
    ON skill_supply_chain_operation(bundle_id, created_at_ms, operation_id);
CREATE INDEX IF NOT EXISTS idx_skill_supply_chain_operation_replaced
    ON skill_supply_chain_operation(replaced_bundle_id, created_at_ms)
    WHERE replaced_bundle_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_skill_supply_chain_operation_audit
    ON skill_supply_chain_operation(audit_ref, created_at_ms);

COMMIT;
