-- DL-04: immutable affective turn and sentence-delivery projection.
--
-- The generic Life command/event UoW remains the only writer.  This table is
-- a privacy-minimised read head: it contains plan/sentence digests and timing
-- identity, never raw user observation media, prompt bodies, model thoughts,
-- or sentence text.  Exact visible text remains in the canonical event only
-- for idempotent delivery/recovery.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS affective_turn_delivery_event_v1 (
    event_id TEXT PRIMARY KEY
        REFERENCES life_event_journal(event_id) ON DELETE RESTRICT,
    person_id TEXT NOT NULL CHECK(length(trim(person_id)) BETWEEN 1 AND 240),
    self_id TEXT NOT NULL CHECK(self_id = 'natsume_ai'),
    workspace_id TEXT NOT NULL CHECK(length(trim(workspace_id)) BETWEEN 1 AND 240),
    relationship_subject_id TEXT NOT NULL
        CHECK(length(trim(relationship_subject_id)) BETWEEN 1 AND 240),
    session_id TEXT NOT NULL CHECK(length(trim(session_id)) BETWEEN 1 AND 240),
    delivery_kind TEXT NOT NULL CHECK(
        delivery_kind IN ('PLAN', 'SENTENCE', 'CANCELLED', 'FOREGROUND')
    ),
    plan_id TEXT NOT NULL CHECK(length(trim(plan_id)) BETWEEN 1 AND 240),
    synchronization_id TEXT NOT NULL CHECK(length(trim(synchronization_id)) BETWEEN 1 AND 240),
    self_affect_revision INTEGER NOT NULL CHECK(self_affect_revision >= 0),
    sentence_index INTEGER NOT NULL DEFAULT -1 CHECK(sentence_index >= -1),
    sentence_digest TEXT NOT NULL DEFAULT ''
        CHECK(sentence_digest = '' OR length(sentence_digest) = 64),
    plan_digest TEXT NOT NULL CHECK(length(plan_digest) = 64),
    event_digest TEXT NOT NULL UNIQUE CHECK(length(event_digest) = 64),
    payload_digest TEXT NOT NULL CHECK(length(payload_digest) = 64),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0)
);

CREATE INDEX IF NOT EXISTS idx_affective_turn_delivery_subject_cursor
    ON affective_turn_delivery_event_v1(person_id, created_at_ms, event_id);
CREATE INDEX IF NOT EXISTS idx_affective_turn_delivery_plan
    ON affective_turn_delivery_event_v1(person_id, plan_id, sentence_index);

CREATE TRIGGER IF NOT EXISTS affective_turn_delivery_event_v1_insert_guard
BEFORE INSERT ON affective_turn_delivery_event_v1
WHEN NOT EXISTS (
    SELECT 1
    FROM life_event_journal AS event
    WHERE event.event_id = NEW.event_id
      AND event.person_id = NEW.person_id
      AND event.event_digest = NEW.event_digest
      AND natsume_sha256_hex(event.event_payload_json) = NEW.payload_digest
      AND json_extract(
          event.event_payload_json,
          '$.state_after.identity.self_id'
      ) = NEW.self_id
      AND json_extract(
          event.event_payload_json,
          '$.state_after.identity.relationship_subject_id'
      ) = NEW.relationship_subject_id
      AND json_extract(
          event.event_payload_json,
          '$.command_payload.workspace_id'
      ) = NEW.workspace_id
      AND json_extract(
          event.event_payload_json,
          '$.command_payload.session_id'
      ) = NEW.session_id
      AND COALESCE(
          json_extract(
              event.event_payload_json,
              '$.command_payload.affective_turn_plan.plan_id'
          ),
          json_extract(
              event.event_payload_json,
              '$.command_payload.affective_turn_plan_ref'
          ),
          ''
      ) = NEW.plan_id
      AND COALESCE(
          json_extract(
              event.event_payload_json,
              '$.command_payload.affective_turn_plan.expression_plan.synchronization_id'
          ),
          json_extract(
              event.event_payload_json,
              '$.command_payload.synchronization_id'
          ),
          ''
      ) = NEW.synchronization_id
      AND CAST(
          COALESCE(
              json_extract(
                  event.event_payload_json,
                  '$.command_payload.affective_turn_plan.self_affect_revision'
              ),
              json_extract(
                  event.event_payload_json,
                  '$.command_payload.self_affect_revision'
              ),
              0
          ) AS INTEGER
      ) = NEW.self_affect_revision
      AND COALESCE(
          json_extract(
              event.event_payload_json,
              '$.command_payload.affective_turn_plan_digest'
          ),
          ''
      ) = NEW.plan_digest
)
BEGIN
    SELECT RAISE(
        ABORT,
        'affective_turn_delivery_event_v1 requires an exact immutable Life event'
    );
END;

CREATE TRIGGER IF NOT EXISTS affective_turn_delivery_event_v1_from_life_event
AFTER INSERT ON life_event_journal
WHEN NEW.event_type IN (
    'affective_turn_planned',
    'surface_sentence_delivery_intent',
    'surface_stream_cancelled',
    'foreground_completed'
)
AND COALESCE(
    json_extract(
        NEW.event_payload_json,
        '$.command_payload.affective_turn_plan.plan_id'
    ),
    json_extract(
        NEW.event_payload_json,
        '$.command_payload.affective_turn_plan_ref'
    ),
    ''
) <> ''
BEGIN
    INSERT INTO affective_turn_delivery_event_v1(
        event_id,
        person_id,
        self_id,
        workspace_id,
        relationship_subject_id,
        session_id,
        delivery_kind,
        plan_id,
        synchronization_id,
        self_affect_revision,
        sentence_index,
        sentence_digest,
        plan_digest,
        event_digest,
        payload_digest,
        created_at_ms
    ) VALUES (
        NEW.event_id,
        NEW.person_id,
        json_extract(NEW.event_payload_json, '$.state_after.identity.self_id'),
        json_extract(NEW.event_payload_json, '$.command_payload.workspace_id'),
        json_extract(
            NEW.event_payload_json,
            '$.state_after.identity.relationship_subject_id'
        ),
        json_extract(NEW.event_payload_json, '$.command_payload.session_id'),
        CASE NEW.event_type
            WHEN 'affective_turn_planned' THEN 'PLAN'
            WHEN 'surface_sentence_delivery_intent' THEN 'SENTENCE'
            WHEN 'surface_stream_cancelled' THEN 'CANCELLED'
            WHEN 'foreground_completed' THEN 'FOREGROUND'
        END,
        COALESCE(
            json_extract(
                NEW.event_payload_json,
                '$.command_payload.affective_turn_plan.plan_id'
            ),
            json_extract(
                NEW.event_payload_json,
                '$.command_payload.affective_turn_plan_ref'
            ),
            ''
        ),
        COALESCE(
            json_extract(
                NEW.event_payload_json,
                '$.command_payload.affective_turn_plan.expression_plan.synchronization_id'
            ),
            json_extract(
                NEW.event_payload_json,
                '$.command_payload.synchronization_id'
            ),
            ''
        ),
        CAST(
            COALESCE(
                json_extract(
                    NEW.event_payload_json,
                    '$.command_payload.affective_turn_plan.self_affect_revision'
                ),
                json_extract(
                    NEW.event_payload_json,
                    '$.command_payload.self_affect_revision'
                ),
                0
            ) AS INTEGER
        ),
        CAST(
            COALESCE(
                json_extract(
                    NEW.event_payload_json,
                    '$.command_payload.sentence_index'
                ),
                -1
            ) AS INTEGER
        ),
        COALESCE(
            json_extract(
                NEW.event_payload_json,
                '$.command_payload.sentence_digest'
            ),
            ''
        ),
        json_extract(
            NEW.event_payload_json,
            '$.command_payload.affective_turn_plan_digest'
        ),
        NEW.event_digest,
        natsume_sha256_hex(NEW.event_payload_json),
        NEW.created_at_ms
    );
END;

CREATE TRIGGER IF NOT EXISTS affective_turn_delivery_event_v1_no_update
BEFORE UPDATE ON affective_turn_delivery_event_v1
BEGIN
    SELECT RAISE(ABORT, 'affective_turn_delivery_event_v1 is immutable');
END;

CREATE TRIGGER IF NOT EXISTS affective_turn_delivery_event_v1_no_delete
BEFORE DELETE ON affective_turn_delivery_event_v1
BEGIN
    SELECT RAISE(ABORT, 'affective_turn_delivery_event_v1 is append-only');
END;

COMMIT;
