-- AW04-B: durable Digital Life command/event/outbox foundation.
-- Commands, events, intents and receipts are immutable.  State projection and
-- delivery claim fields are rebuildable/mutable under repository ownership.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS life_command (
    command_id TEXT PRIMARY KEY CHECK(length(trim(command_id)) BETWEEN 1 AND 240),
    command_hash TEXT NOT NULL UNIQUE CHECK(length(command_hash) = 64),
    person_id TEXT NOT NULL CHECK(length(trim(person_id)) BETWEEN 1 AND 240),
    command_type TEXT NOT NULL CHECK(length(trim(command_type)) BETWEEN 1 AND 120),
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    expected_version INTEGER NOT NULL CHECK(expected_version >= 0),
    correlation_id TEXT,
    causation_id TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metadata_json)),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0)
);

CREATE TABLE IF NOT EXISTS life_command_claim (
    command_id TEXT PRIMARY KEY CHECK(length(trim(command_id)) BETWEEN 1 AND 240),
    command_hash TEXT NOT NULL CHECK(length(command_hash) = 64),
    owner_token TEXT NOT NULL CHECK(length(trim(owner_token)) BETWEEN 1 AND 240),
    fencing_token INTEGER NOT NULL CHECK(fencing_token >= 1),
    claim_expires_at_ms INTEGER NOT NULL CHECK(claim_expires_at_ms >= 0),
    attempt_count INTEGER NOT NULL CHECK(attempt_count >= 1),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
    updated_at_ms INTEGER NOT NULL CHECK(updated_at_ms >= created_at_ms)
);

CREATE TABLE IF NOT EXISTS life_aggregate_claim (
    person_id TEXT PRIMARY KEY CHECK(length(trim(person_id)) BETWEEN 1 AND 240),
    owner_token TEXT NOT NULL CHECK(length(trim(owner_token)) BETWEEN 1 AND 240),
    fencing_token INTEGER NOT NULL CHECK(fencing_token >= 1),
    lease_expires_at_ms INTEGER NOT NULL CHECK(lease_expires_at_ms >= 0),
    attempt_count INTEGER NOT NULL CHECK(attempt_count >= 1),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
    updated_at_ms INTEGER NOT NULL CHECK(updated_at_ms >= created_at_ms)
);

CREATE TABLE IF NOT EXISTS life_event_journal (
    event_id TEXT PRIMARY KEY CHECK(length(trim(event_id)) BETWEEN 1 AND 260),
    command_id TEXT NOT NULL UNIQUE,
    person_id TEXT NOT NULL CHECK(length(trim(person_id)) BETWEEN 1 AND 240),
    sequence INTEGER NOT NULL CHECK(sequence >= 1),
    event_type TEXT NOT NULL CHECK(length(trim(event_type)) BETWEEN 1 AND 160),
    event_payload_json TEXT NOT NULL CHECK(json_valid(event_payload_json)),
    state_before_digest TEXT NOT NULL CHECK(length(state_before_digest) = 64),
    state_after_digest TEXT NOT NULL CHECK(length(state_after_digest) = 64),
    previous_event_digest TEXT,
    event_digest TEXT NOT NULL UNIQUE CHECK(length(event_digest) = 64),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
    UNIQUE(person_id, sequence),
    FOREIGN KEY(command_id) REFERENCES life_command(command_id)
);

CREATE TABLE IF NOT EXISTS life_projection_intent (
    intent_id TEXT PRIMARY KEY CHECK(length(trim(intent_id)) BETWEEN 1 AND 280),
    event_id TEXT NOT NULL,
    command_id TEXT NOT NULL,
    person_id TEXT NOT NULL CHECK(length(trim(person_id)) BETWEEN 1 AND 240),
    intent_kind TEXT NOT NULL CHECK(intent_kind IN ('ORGAN', 'PROJECTION')),
    target TEXT NOT NULL CHECK(length(trim(target)) BETWEEN 1 AND 240),
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    payload_digest TEXT NOT NULL CHECK(length(payload_digest) = 64),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
    FOREIGN KEY(event_id) REFERENCES life_event_journal(event_id),
    FOREIGN KEY(command_id) REFERENCES life_command(command_id)
);

CREATE TABLE IF NOT EXISTS life_projection_claim (
    intent_id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK(status IN ('PENDING', 'CLAIMED', 'FAILED')),
    available_at_ms INTEGER NOT NULL CHECK(available_at_ms >= 0),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
    claim_owner TEXT,
    fencing_token INTEGER NOT NULL DEFAULT 0 CHECK(fencing_token >= 0),
    claim_expires_at_ms INTEGER,
    last_error TEXT,
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
    updated_at_ms INTEGER NOT NULL CHECK(updated_at_ms >= created_at_ms),
    FOREIGN KEY(intent_id) REFERENCES life_projection_intent(intent_id)
);

CREATE TABLE IF NOT EXISTS life_projection_receipt (
    intent_id TEXT PRIMARY KEY,
    payload_digest TEXT NOT NULL CHECK(length(payload_digest) = 64),
    result_json TEXT NOT NULL CHECK(json_valid(result_json)),
    completed_at_ms INTEGER NOT NULL CHECK(completed_at_ms >= 0),
    FOREIGN KEY(intent_id) REFERENCES life_projection_intent(intent_id)
);

CREATE TABLE IF NOT EXISTS life_delivery_outbox (
    outbox_id TEXT PRIMARY KEY CHECK(length(trim(outbox_id)) BETWEEN 1 AND 280),
    event_id TEXT NOT NULL,
    command_id TEXT NOT NULL,
    person_id TEXT NOT NULL CHECK(length(trim(person_id)) BETWEEN 1 AND 240),
    destination TEXT NOT NULL CHECK(length(trim(destination)) BETWEEN 1 AND 240),
    delivery_key TEXT NOT NULL UNIQUE CHECK(length(trim(delivery_key)) BETWEEN 1 AND 280),
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    payload_digest TEXT NOT NULL CHECK(length(payload_digest) = 64),
    authorization_request_id TEXT NOT NULL
        CHECK(length(trim(authorization_request_id)) BETWEEN 1 AND 240),
    governor_grant_ref TEXT NOT NULL
        CHECK(length(trim(governor_grant_ref)) BETWEEN 1 AND 512),
    authority_kind TEXT NOT NULL
        CHECK(length(trim(authority_kind)) BETWEEN 1 AND 120),
    authority_principal_id TEXT NOT NULL
        CHECK(length(trim(authority_principal_id)) BETWEEN 1 AND 240),
    authorization_digest TEXT NOT NULL CHECK(length(authorization_digest) = 64),
    status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK(status IN ('PENDING', 'CLAIMED', 'DELIVERED', 'FAILED')),
    available_at_ms INTEGER NOT NULL CHECK(available_at_ms >= 0),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
    claim_owner TEXT,
    fencing_token INTEGER NOT NULL DEFAULT 0 CHECK(fencing_token >= 0),
    claim_expires_at_ms INTEGER,
    last_error TEXT,
    result_json TEXT,
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
    updated_at_ms INTEGER NOT NULL CHECK(updated_at_ms >= created_at_ms),
    delivered_at_ms INTEGER,
    FOREIGN KEY(event_id) REFERENCES life_event_journal(event_id),
    FOREIGN KEY(command_id) REFERENCES life_command(command_id)
);

CREATE TABLE IF NOT EXISTS life_delivery_receipt (
    outbox_id TEXT PRIMARY KEY,
    delivery_key TEXT NOT NULL UNIQUE
        CHECK(length(trim(delivery_key)) BETWEEN 1 AND 280),
    payload_digest TEXT NOT NULL CHECK(length(payload_digest) = 64),
    authorization_digest TEXT NOT NULL CHECK(length(authorization_digest) = 64),
    result_json TEXT NOT NULL CHECK(json_valid(result_json)),
    completed_at_ms INTEGER NOT NULL CHECK(completed_at_ms >= 0),
    FOREIGN KEY(outbox_id) REFERENCES life_delivery_outbox(outbox_id)
);

CREATE TABLE IF NOT EXISTS life_command_receipt (
    command_id TEXT PRIMARY KEY,
    command_hash TEXT NOT NULL CHECK(length(command_hash) = 64),
    person_id TEXT NOT NULL CHECK(length(trim(person_id)) BETWEEN 1 AND 240),
    status TEXT NOT NULL CHECK(status IN ('APPLIED', 'CONFLICT')),
    reason_code TEXT,
    event_id TEXT,
    state_version INTEGER NOT NULL CHECK(state_version >= 0),
    state_digest TEXT NOT NULL CHECK(length(state_digest) = 64),
    outbox_count INTEGER NOT NULL DEFAULT 0 CHECK(outbox_count >= 0),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
    FOREIGN KEY(command_id) REFERENCES life_command(command_id),
    FOREIGN KEY(event_id) REFERENCES life_event_journal(event_id)
);

CREATE INDEX IF NOT EXISTS idx_life_command_person_created
    ON life_command(person_id, created_at_ms, command_id);
CREATE INDEX IF NOT EXISTS idx_life_command_claim_expiry
    ON life_command_claim(claim_expires_at_ms, command_id);
CREATE INDEX IF NOT EXISTS idx_life_aggregate_claim_expiry
    ON life_aggregate_claim(lease_expires_at_ms, person_id);
CREATE INDEX IF NOT EXISTS idx_life_event_person_sequence
    ON life_event_journal(person_id, sequence);
CREATE INDEX IF NOT EXISTS idx_life_projection_intent_event
    ON life_projection_intent(event_id, intent_kind, intent_id);
CREATE INDEX IF NOT EXISTS idx_life_projection_claim_pending
    ON life_projection_claim(status, available_at_ms, claim_expires_at_ms, intent_id);
CREATE INDEX IF NOT EXISTS idx_life_delivery_pending
    ON life_delivery_outbox(status, available_at_ms, claim_expires_at_ms, outbox_id);
CREATE INDEX IF NOT EXISTS idx_life_delivery_command
    ON life_delivery_outbox(command_id, outbox_id);
CREATE INDEX IF NOT EXISTS idx_life_delivery_authority
    ON life_delivery_outbox(authorization_request_id, governor_grant_ref, outbox_id);

CREATE TRIGGER IF NOT EXISTS life_command_no_update
BEFORE UPDATE ON life_command
BEGIN
    SELECT RAISE(ABORT, 'life_command is immutable');
END;

CREATE TRIGGER IF NOT EXISTS life_command_no_delete
BEFORE DELETE ON life_command
BEGIN
    SELECT RAISE(ABORT, 'life_command is immutable');
END;

CREATE TRIGGER IF NOT EXISTS life_event_journal_no_update
BEFORE UPDATE ON life_event_journal
BEGIN
    SELECT RAISE(ABORT, 'life_event_journal is immutable');
END;

CREATE TRIGGER IF NOT EXISTS life_event_journal_no_delete
BEFORE DELETE ON life_event_journal
BEGIN
    SELECT RAISE(ABORT, 'life_event_journal is immutable');
END;

CREATE TRIGGER IF NOT EXISTS life_projection_intent_no_update
BEFORE UPDATE ON life_projection_intent
BEGIN
    SELECT RAISE(ABORT, 'life_projection_intent is immutable');
END;

CREATE TRIGGER IF NOT EXISTS life_projection_intent_no_delete
BEFORE DELETE ON life_projection_intent
BEGIN
    SELECT RAISE(ABORT, 'life_projection_intent is immutable');
END;

CREATE TRIGGER IF NOT EXISTS life_projection_receipt_no_update
BEFORE UPDATE ON life_projection_receipt
BEGIN
    SELECT RAISE(ABORT, 'life_projection_receipt is immutable');
END;

CREATE TRIGGER IF NOT EXISTS life_projection_receipt_no_delete
BEFORE DELETE ON life_projection_receipt
BEGIN
    SELECT RAISE(ABORT, 'life_projection_receipt is immutable');
END;

CREATE TRIGGER IF NOT EXISTS life_projection_receipt_insert_guard
BEFORE INSERT ON life_projection_receipt
WHEN NOT EXISTS (
    SELECT 1 FROM life_projection_intent
    WHERE intent_id = NEW.intent_id
      AND payload_digest = NEW.payload_digest
)
BEGIN
    SELECT RAISE(ABORT, 'life_projection_receipt must match immutable intent');
END;

CREATE TRIGGER IF NOT EXISTS life_delivery_outbox_identity_no_update
BEFORE UPDATE OF event_id, command_id, person_id, destination, delivery_key,
    payload_json, payload_digest, authorization_request_id, governor_grant_ref,
    authority_kind, authority_principal_id, authorization_digest, created_at_ms
ON life_delivery_outbox
BEGIN
    SELECT RAISE(ABORT, 'life_delivery_outbox identity and authority are immutable');
END;

CREATE TRIGGER IF NOT EXISTS life_delivery_outbox_no_delete
BEFORE DELETE ON life_delivery_outbox
BEGIN
    SELECT RAISE(ABORT, 'life_delivery_outbox is durable');
END;

CREATE TRIGGER IF NOT EXISTS life_delivery_receipt_insert_guard
BEFORE INSERT ON life_delivery_receipt
WHEN NOT EXISTS (
    SELECT 1 FROM life_delivery_outbox
    WHERE outbox_id = NEW.outbox_id
      AND delivery_key = NEW.delivery_key
      AND payload_digest = NEW.payload_digest
      AND authorization_digest = NEW.authorization_digest
      AND status = 'DELIVERED'
)
BEGIN
    SELECT RAISE(ABORT, 'life_delivery_receipt must match a delivered outbox row');
END;

CREATE TRIGGER IF NOT EXISTS life_delivery_receipt_no_update
BEFORE UPDATE ON life_delivery_receipt
BEGIN
    SELECT RAISE(ABORT, 'life_delivery_receipt is immutable');
END;

CREATE TRIGGER IF NOT EXISTS life_delivery_receipt_no_delete
BEFORE DELETE ON life_delivery_receipt
BEGIN
    SELECT RAISE(ABORT, 'life_delivery_receipt is immutable');
END;

CREATE TRIGGER IF NOT EXISTS life_delivery_outbox_receipt_status_guard
BEFORE UPDATE OF status ON life_delivery_outbox
WHEN EXISTS (
    SELECT 1 FROM life_delivery_receipt
    WHERE outbox_id = OLD.outbox_id
)
AND NEW.status != 'DELIVERED'
BEGIN
    SELECT RAISE(ABORT, 'delivered outbox status is sealed by receipt');
END;

CREATE TRIGGER IF NOT EXISTS life_command_receipt_no_update
BEFORE UPDATE ON life_command_receipt
BEGIN
    SELECT RAISE(ABORT, 'life_command_receipt is immutable');
END;

CREATE TRIGGER IF NOT EXISTS life_command_receipt_no_delete
BEFORE DELETE ON life_command_receipt
BEGIN
    SELECT RAISE(ABORT, 'life_command_receipt is immutable');
END;

-- Once a person has entered the 0079 journal, every state projection write
-- must be backed by the exact same-version immutable event.  This fences the
-- pre-cutover LifeRuntime writer after explicit baseline adoption.
CREATE TRIGGER IF NOT EXISTS life_command_state_projection_insert_guard
BEFORE INSERT ON digital_life_state
WHEN EXISTS (
    SELECT 1 FROM life_event_journal WHERE person_id = NEW.person_id
)
AND NOT EXISTS (
    SELECT 1 FROM life_event_journal
    WHERE person_id = NEW.person_id
      AND sequence = NEW.version
      AND (
          created_at_ms = NEW.updated_at_ms
          OR (
              event_type = 'baseline_adopted'
              AND CAST(
                  json_extract(
                      event_payload_json,
                      '$.command_payload.baseline_updated_at_ms'
                  ) AS INTEGER
              ) = NEW.updated_at_ms
          )
      )
      AND json(json_extract(event_payload_json, '$.state_after')) = json(NEW.state_json)
)
BEGIN
    SELECT RAISE(ABORT, 'LIFE_COMMAND_STATE_EVENT_REQUIRED');
END;

CREATE TRIGGER IF NOT EXISTS life_command_state_projection_update_guard
BEFORE UPDATE ON digital_life_state
WHEN EXISTS (
    SELECT 1 FROM life_event_journal WHERE person_id = NEW.person_id
)
AND NOT EXISTS (
    SELECT 1 FROM life_event_journal
    WHERE person_id = NEW.person_id
      AND sequence = NEW.version
      AND (
          created_at_ms = NEW.updated_at_ms
          OR (
              event_type = 'baseline_adopted'
              AND CAST(
                  json_extract(
                      event_payload_json,
                      '$.command_payload.baseline_updated_at_ms'
                  ) AS INTEGER
              ) = NEW.updated_at_ms
          )
      )
      AND json(json_extract(event_payload_json, '$.state_after')) = json(NEW.state_json)
)
BEGIN
    SELECT RAISE(ABORT, 'LIFE_COMMAND_STATE_EVENT_REQUIRED');
END;

COMMIT;
