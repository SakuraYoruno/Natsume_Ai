-- DL-05: append-only Host protocol projection for voice/avatar expression.
--
-- This is deliberately a delivery *staging* ledger.  It records that the
-- canonical Life command accepted an expression protocol before a sentence
-- was exposed; it must not be confused with a physical Edge playback or VTS
-- receipt.  No raw media, model reasoning, or assistant sentence text is
-- copied into this projection.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS affective_expression_delivery_event_v1 (
    event_id TEXT NOT NULL
        REFERENCES life_event_journal(event_id) ON DELETE RESTRICT,
    delivery_id TEXT NOT NULL CHECK(length(trim(delivery_id)) BETWEEN 1 AND 240),
    person_id TEXT NOT NULL CHECK(length(trim(person_id)) BETWEEN 1 AND 240),
    self_id TEXT NOT NULL CHECK(self_id = 'natsume_ai'),
    workspace_id TEXT NOT NULL CHECK(length(trim(workspace_id)) BETWEEN 1 AND 240),
    relationship_subject_id TEXT NOT NULL
        CHECK(length(trim(relationship_subject_id)) BETWEEN 1 AND 240),
    session_id TEXT NOT NULL CHECK(length(trim(session_id)) BETWEEN 1 AND 240),
    delivery_kind TEXT NOT NULL CHECK(delivery_kind IN ('INTENT', 'CANCEL', 'RELEASE')),
    channel TEXT NOT NULL CHECK(channel IN ('VOICE', 'AVATAR')),
    action TEXT NOT NULL CHECK(action IN ('SPEAK', 'TRANSITION', 'CANCEL', 'RELEASE')),
    plan_id TEXT NOT NULL CHECK(length(trim(plan_id)) BETWEEN 1 AND 240),
    synchronization_id TEXT NOT NULL CHECK(length(trim(synchronization_id)) BETWEEN 1 AND 240),
    self_affect_revision INTEGER NOT NULL CHECK(self_affect_revision >= 0),
    sentence_index INTEGER NOT NULL CHECK(sentence_index >= 0),
    avatar_expression TEXT NOT NULL DEFAULT '' CHECK(length(avatar_expression) <= 64),
    previous_avatar_expression TEXT NOT NULL DEFAULT ''
        CHECK(length(previous_avatar_expression) <= 64),
    previous_expression_source TEXT NOT NULL DEFAULT ''
        CHECK(length(previous_expression_source) <= 64),
    receipt_status TEXT NOT NULL CHECK(receipt_status IN (
        'HOST_PROTOCOL_STAGED', 'HOST_CANCEL_STAGED', 'HOST_RELEASE_STAGED'
    )),
    event_digest TEXT NOT NULL CHECK(length(event_digest) = 64),
    payload_digest TEXT NOT NULL CHECK(length(payload_digest) = 64),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
    PRIMARY KEY(event_id, delivery_id)
);

CREATE INDEX IF NOT EXISTS idx_affective_expression_delivery_subject_cursor
    ON affective_expression_delivery_event_v1(
        person_id, workspace_id, relationship_subject_id, session_id,
        created_at_ms, event_id, delivery_id
    );
CREATE INDEX IF NOT EXISTS idx_affective_expression_delivery_avatar_head
    ON affective_expression_delivery_event_v1(
        person_id, workspace_id, relationship_subject_id, session_id,
        channel, action, created_at_ms DESC, event_id DESC, delivery_id DESC
    );

CREATE TRIGGER IF NOT EXISTS affective_expression_delivery_event_v1_insert_guard
BEFORE INSERT ON affective_expression_delivery_event_v1
WHEN NOT EXISTS (
    SELECT 1
    FROM life_event_journal AS event
    WHERE event.event_id = NEW.event_id
      AND event.person_id = NEW.person_id
      AND event.event_digest = NEW.event_digest
      AND natsume_sha256_hex(event.event_payload_json) = NEW.payload_digest
      AND json_extract(event.event_payload_json, '$.state_after.identity.self_id') = NEW.self_id
      AND json_extract(
          event.event_payload_json,
          '$.state_after.identity.relationship_subject_id'
      ) = NEW.relationship_subject_id
      AND json_extract(event.event_payload_json, '$.command_payload.workspace_id') = NEW.workspace_id
      AND json_extract(event.event_payload_json, '$.command_payload.session_id') = NEW.session_id
      AND EXISTS (
          SELECT 1
          FROM json_each(
              COALESCE(
                  json_extract(
                      event.event_payload_json,
                      '$.command_payload.expression_delivery_receipts'
                  ),
                  '[]'
              )
          ) AS receipt
          WHERE json_extract(receipt.value, '$.delivery_id') = NEW.delivery_id
            AND json_extract(receipt.value, '$.synchronization_id') = NEW.synchronization_id
            AND json_extract(receipt.value, '$.channel') = NEW.channel
            AND json_extract(receipt.value, '$.action') = NEW.action
            AND json_extract(receipt.value, '$.status') = NEW.receipt_status
      )
)
BEGIN
    SELECT RAISE(
        ABORT,
        'affective_expression_delivery_event_v1 requires an exact immutable Life event'
    );
END;

-- Sentence intents are emitted before the corresponding sentence is visible.
CREATE TRIGGER IF NOT EXISTS affective_expression_delivery_event_v1_intents_from_life_event
AFTER INSERT ON life_event_journal
WHEN NEW.event_type IN ('surface_sentence_delivery_intent', 'foreground_completed')
 AND json_type(NEW.event_payload_json, '$.command_payload.expression_delivery_intents') = 'array'
BEGIN
    INSERT INTO affective_expression_delivery_event_v1(
        event_id, delivery_id, person_id, self_id, workspace_id,
        relationship_subject_id, session_id, delivery_kind, channel, action,
        plan_id, synchronization_id, self_affect_revision, sentence_index,
        avatar_expression, previous_avatar_expression, previous_expression_source,
        receipt_status, event_digest, payload_digest, created_at_ms
    )
    SELECT
        NEW.event_id,
        json_extract(item.value, '$.delivery_id'),
        NEW.person_id,
        json_extract(NEW.event_payload_json, '$.state_after.identity.self_id'),
        json_extract(item.value, '$.workspace_id'),
        json_extract(item.value, '$.relationship_subject_id'),
        json_extract(item.value, '$.session_id'),
        'INTENT',
        json_extract(item.value, '$.channel'),
        json_extract(item.value, '$.action'),
        json_extract(item.value, '$.plan_id'),
        json_extract(item.value, '$.synchronization_id'),
        CAST(json_extract(item.value, '$.self_affect_revision') AS INTEGER),
        CAST(json_extract(item.value, '$.sentence_index') AS INTEGER),
        COALESCE(json_extract(item.value, '$.payload.expression'), ''),
        COALESCE(json_extract(item.value, '$.payload.previous_expression'), ''),
        COALESCE(json_extract(item.value, '$.payload.previous_expression_source'), ''),
        'HOST_PROTOCOL_STAGED',
        NEW.event_digest,
        natsume_sha256_hex(NEW.event_payload_json),
        NEW.created_at_ms
    FROM json_each(
        json_extract(NEW.event_payload_json, '$.command_payload.expression_delivery_intents')
    ) AS item;
END;

-- Cancel and release controls only cover already-staged output.  They record
-- Host intent, not a claim that a remote device has actually stopped.
CREATE TRIGGER IF NOT EXISTS affective_expression_delivery_event_v1_controls_from_life_event
AFTER INSERT ON life_event_journal
WHEN NEW.event_type IN ('surface_stream_cancelled', 'foreground_completed')
 AND json_type(NEW.event_payload_json, '$.command_payload.expression_delivery_controls') = 'array'
BEGIN
    INSERT INTO affective_expression_delivery_event_v1(
        event_id, delivery_id, person_id, self_id, workspace_id,
        relationship_subject_id, session_id, delivery_kind, channel, action,
        plan_id, synchronization_id, self_affect_revision, sentence_index,
        avatar_expression, previous_avatar_expression, previous_expression_source,
        receipt_status, event_digest, payload_digest, created_at_ms
    )
    SELECT
        NEW.event_id,
        json_extract(item.value, '$.delivery_id'),
        NEW.person_id,
        json_extract(NEW.event_payload_json, '$.state_after.identity.self_id'),
        json_extract(item.value, '$.workspace_id'),
        json_extract(item.value, '$.relationship_subject_id'),
        json_extract(item.value, '$.session_id'),
        CASE json_extract(item.value, '$.action')
            WHEN 'CANCEL' THEN 'CANCEL'
            ELSE 'RELEASE'
        END,
        json_extract(item.value, '$.channel'),
        json_extract(item.value, '$.action'),
        json_extract(item.value, '$.plan_id'),
        json_extract(item.value, '$.synchronization_id'),
        CAST(json_extract(item.value, '$.self_affect_revision') AS INTEGER),
        CAST(json_extract(item.value, '$.sentence_index') AS INTEGER),
        '', '', '',
        CASE json_extract(item.value, '$.action')
            WHEN 'CANCEL' THEN 'HOST_CANCEL_STAGED'
            ELSE 'HOST_RELEASE_STAGED'
        END,
        NEW.event_digest,
        natsume_sha256_hex(NEW.event_payload_json),
        NEW.created_at_ms
    FROM json_each(
        json_extract(NEW.event_payload_json, '$.command_payload.expression_delivery_controls')
    ) AS item;
END;

CREATE TRIGGER IF NOT EXISTS affective_expression_delivery_event_v1_no_update
BEFORE UPDATE ON affective_expression_delivery_event_v1
BEGIN
    SELECT RAISE(ABORT, 'affective_expression_delivery_event_v1 is immutable');
END;

CREATE TRIGGER IF NOT EXISTS affective_expression_delivery_event_v1_no_delete
BEFORE DELETE ON affective_expression_delivery_event_v1
BEGIN
    SELECT RAISE(ABORT, 'affective_expression_delivery_event_v1 is append-only');
END;

COMMIT;
