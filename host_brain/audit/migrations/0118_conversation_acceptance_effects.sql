-- NE-01: durable post-Acceptance conversation projection receipts.
--
-- Preference extraction can legitimately produce zero events.  The immutable
-- receipt records that exact evaluation and binds every produced event and
-- profile projection version in the same SQLite transaction.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS conversation_preference_effect_receipt (
    operation_id TEXT PRIMARY KEY
        CHECK(length(trim(operation_id)) BETWEEN 1 AND 512),
    payload_sha256 TEXT NOT NULL
        CHECK(
            payload_sha256 GLOB 'sha256:*'
            AND length(payload_sha256) = 71
            AND substr(payload_sha256, 8) NOT GLOB '*[^0-9a-f]*'
        ),
    disposition TEXT NOT NULL CHECK(disposition IN ('APPLIED', 'NO_SIGNAL', 'DISABLED')),
    event_bindings_json TEXT NOT NULL
        CHECK(json_valid(event_bindings_json) AND json_type(event_bindings_json) = 'array'),
    profile_receipts_json TEXT NOT NULL
        CHECK(json_valid(profile_receipts_json) AND json_type(profile_receipts_json) = 'array'),
    completed_at_ms INTEGER NOT NULL CHECK(completed_at_ms >= 0),
    receipt_digest TEXT NOT NULL UNIQUE
        CHECK(
            receipt_digest GLOB 'sha256:*'
            AND length(receipt_digest) = 71
            AND substr(receipt_digest, 8) NOT GLOB '*[^0-9a-f]*'
        )
);

CREATE INDEX IF NOT EXISTS idx_conversation_preference_effect_completed
    ON conversation_preference_effect_receipt(completed_at_ms, operation_id);

CREATE TABLE IF NOT EXISTS conversation_preference_profile_version (
    operation_id TEXT NOT NULL,
    person_id TEXT NOT NULL CHECK(length(trim(person_id)) BETWEEN 1 AND 120),
    domain TEXT NOT NULL CHECK(length(trim(domain)) BETWEEN 1 AND 120),
    version INTEGER NOT NULL CHECK(version >= 1),
    profile_json TEXT NOT NULL
        CHECK(json_valid(profile_json) AND json_type(profile_json) = 'object'),
    profile_sha256 TEXT NOT NULL
        CHECK(
            profile_sha256 GLOB 'sha256:*'
            AND length(profile_sha256) = 71
            AND substr(profile_sha256, 8) NOT GLOB '*[^0-9a-f]*'
        ),
    updated_at_ms INTEGER NOT NULL CHECK(updated_at_ms >= 0),
    PRIMARY KEY(person_id, domain, version),
    UNIQUE(operation_id, person_id, domain),
    FOREIGN KEY(operation_id)
        REFERENCES conversation_preference_effect_receipt(operation_id)
        DEFERRABLE INITIALLY DEFERRED
);

CREATE INDEX IF NOT EXISTS idx_conversation_preference_profile_operation
    ON conversation_preference_profile_version(operation_id, person_id, domain);

CREATE TRIGGER IF NOT EXISTS conversation_preference_effect_receipt_no_update
BEFORE UPDATE ON conversation_preference_effect_receipt
BEGIN
    SELECT RAISE(ABORT, 'conversation preference effect receipts are immutable');
END;

CREATE TRIGGER IF NOT EXISTS conversation_preference_effect_receipt_no_delete
BEFORE DELETE ON conversation_preference_effect_receipt
BEGIN
    SELECT RAISE(ABORT, 'conversation preference effect receipts are immutable');
END;

CREATE TRIGGER IF NOT EXISTS conversation_preference_profile_version_no_update
BEFORE UPDATE ON conversation_preference_profile_version
BEGIN
    SELECT RAISE(ABORT, 'conversation preference profile versions are immutable');
END;

CREATE TRIGGER IF NOT EXISTS conversation_preference_profile_version_no_delete
BEFORE DELETE ON conversation_preference_profile_version
BEGIN
    SELECT RAISE(ABORT, 'conversation preference profile versions are immutable');
END;

-- A profile version must be part of the same transaction that creates its
-- parent evaluation receipt.  Once the receipt is visible, its profile set is
-- sealed and cannot be extended even when foreign-key enforcement is disabled
-- by a maintenance client.
CREATE TRIGGER IF NOT EXISTS conversation_preference_profile_version_parent_sealed
BEFORE INSERT ON conversation_preference_profile_version
WHEN EXISTS (
    SELECT 1 FROM conversation_preference_effect_receipt
    WHERE operation_id = NEW.operation_id
)
BEGIN
    SELECT RAISE(ABORT, 'conversation preference effect profile set is sealed');
END;

COMMIT;
