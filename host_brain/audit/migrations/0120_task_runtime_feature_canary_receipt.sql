-- NE-01: signed, append-only bounded fan-out feature-canary authority.
--
-- A non-empty process environment variable is never evidence that concurrent
-- DAG scheduling has passed an actual canary.  This table accepts only an
-- external Ed25519 attestation over the exact build, migration version,
-- scheduler configuration and evidence digest.  The Host reopens and verifies
-- this record at startup before it enables bounded fan-out.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS task_runtime_feature_canary_receipt (
    receipt_ref TEXT NOT NULL PRIMARY KEY
        CHECK(
            receipt_ref GLOB 'task-runtime-canary://sha256/*'
            AND length(receipt_ref) =
                length('task-runtime-canary://sha256/') + 64
            AND substr(
                receipt_ref,
                length('task-runtime-canary://sha256/') + 1
            ) NOT GLOB '*[^0-9a-f]*'
        ),
    schema_version TEXT NOT NULL
        CHECK(
            schema_version =
                'Natsume.task_runtime.feature_canary_receipt.v1'
        ),
    feature_key TEXT NOT NULL CHECK(feature_key = 'bounded_fanout'),
    build_commit_sha TEXT NOT NULL
        CHECK(
            length(build_commit_sha) = 40
            AND build_commit_sha NOT GLOB '*[^0-9a-f]*'
        ),
    runtime_migration_version INTEGER NOT NULL
        CHECK(
            typeof(runtime_migration_version) = 'integer'
            AND runtime_migration_version >= 1
        ),
    configuration_digest TEXT NOT NULL
        CHECK(
            configuration_digest GLOB 'sha256:*'
            AND length(configuration_digest) = 71
            AND substr(configuration_digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
    evidence_digest TEXT NOT NULL
        CHECK(
            evidence_digest GLOB 'sha256:*'
            AND length(evidence_digest) = 71
            AND substr(evidence_digest, 8) NOT GLOB '*[^0-9a-f]*'
        ),
    attestation_key_id TEXT NOT NULL
        CHECK(length(trim(attestation_key_id)) BETWEEN 1 AND 128),
    attestation_signature TEXT NOT NULL
        CHECK(
            attestation_signature GLOB 'ed25519:*'
            AND length(trim(attestation_signature)) BETWEEN 12 AND 512
        ),
    issued_at_ms INTEGER NOT NULL CHECK(issued_at_ms >= 0),
    expires_at_ms INTEGER NOT NULL CHECK(expires_at_ms > issued_at_ms)
);

CREATE INDEX IF NOT EXISTS idx_task_runtime_feature_canary_active
    ON task_runtime_feature_canary_receipt(
        feature_key,
        build_commit_sha,
        runtime_migration_version,
        expires_at_ms
    );

CREATE TRIGGER IF NOT EXISTS task_runtime_feature_canary_receipt_no_update
BEFORE UPDATE ON task_runtime_feature_canary_receipt
BEGIN
    SELECT RAISE(ABORT, 'task runtime feature canary receipts are immutable');
END;

CREATE TRIGGER IF NOT EXISTS task_runtime_feature_canary_receipt_no_delete
BEFORE DELETE ON task_runtime_feature_canary_receipt
BEGIN
    SELECT RAISE(ABORT, 'task runtime feature canary receipts are immutable');
END;

COMMIT;
