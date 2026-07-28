-- DL-03: canonical relationship episode and aftereffect event projection.
--
-- The generic Life UoW remains the only writer.  This projection is derived
-- exclusively from immutable Life events and stores opaque evidence refs,
-- never a user message, reply body, audio or image payload.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS relationship_life_event_v1 (
    event_id TEXT PRIMARY KEY
        REFERENCES life_event_journal(event_id) ON DELETE RESTRICT,
    person_id TEXT NOT NULL CHECK(length(trim(person_id)) BETWEEN 1 AND 240),
    self_id TEXT NOT NULL CHECK(self_id = 'natsume_ai'),
    workspace_id TEXT NOT NULL CHECK(length(trim(workspace_id)) BETWEEN 1 AND 240),
    relationship_subject_id TEXT NOT NULL
        CHECK(length(trim(relationship_subject_id)) BETWEEN 1 AND 240),
    relationship_kind TEXT NOT NULL CHECK(
        relationship_kind IN ('RESPONSE_ACCEPTED', 'AFTEREFFECT', 'PROACTIVE_PERMISSION')
    ),
    episode_id TEXT NOT NULL DEFAULT '',
    aftereffect_id TEXT NOT NULL DEFAULT '',
    state_revision INTEGER NOT NULL CHECK(state_revision >= 0),
    event_digest TEXT NOT NULL UNIQUE CHECK(length(event_digest) = 64),
    payload_digest TEXT NOT NULL CHECK(length(payload_digest) = 64),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0)
);

CREATE INDEX IF NOT EXISTS idx_relationship_life_event_subject_cursor
    ON relationship_life_event_v1(person_id, created_at_ms, event_id);
CREATE INDEX IF NOT EXISTS idx_relationship_life_event_episode
    ON relationship_life_event_v1(person_id, episode_id, created_at_ms);

CREATE TRIGGER IF NOT EXISTS relationship_life_event_v1_insert_guard
BEFORE INSERT ON relationship_life_event_v1
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
          '$.state_after.relationship_v2.workspace_id'
      ) = NEW.workspace_id
      AND CAST(
          json_extract(
              event.event_payload_json,
              '$.state_after.relationship_v2.revision'
          ) AS INTEGER
      ) = NEW.state_revision
)
BEGIN
    SELECT RAISE(
        ABORT,
        'relationship_life_event_v1 requires an exact immutable Life event'
    );
END;

CREATE TRIGGER IF NOT EXISTS relationship_life_event_v1_from_life_event
AFTER INSERT ON life_event_journal
WHEN NEW.event_type IN (
    'relationship_response_accepted',
    'relational_aftereffect_recorded',
    'relationship_proactive_permission_changed'
)
BEGIN
    INSERT INTO relationship_life_event_v1(
        event_id,
        person_id,
        self_id,
        workspace_id,
        relationship_subject_id,
        relationship_kind,
        episode_id,
        aftereffect_id,
        state_revision,
        event_digest,
        payload_digest,
        created_at_ms
    ) VALUES (
        NEW.event_id,
        NEW.person_id,
        json_extract(NEW.event_payload_json, '$.state_after.identity.self_id'),
        json_extract(
            NEW.event_payload_json,
            '$.state_after.relationship_v2.workspace_id'
        ),
        json_extract(
            NEW.event_payload_json,
            '$.state_after.identity.relationship_subject_id'
        ),
        CASE NEW.event_type
            WHEN 'relationship_response_accepted' THEN 'RESPONSE_ACCEPTED'
            WHEN 'relational_aftereffect_recorded' THEN 'AFTEREFFECT'
            WHEN 'relationship_proactive_permission_changed' THEN 'PROACTIVE_PERMISSION'
        END,
        COALESCE(
            json_extract(
                NEW.event_payload_json,
                '$.command_payload.episode.episode_id'
            ),
            json_extract(
                NEW.event_payload_json,
                '$.command_payload.aftereffect.episode_id'
            ),
            ''
        ),
        COALESCE(
            json_extract(
                NEW.event_payload_json,
                '$.command_payload.aftereffect.aftereffect_id'
            ),
            ''
        ),
        CAST(
            json_extract(
                NEW.event_payload_json,
                '$.state_after.relationship_v2.revision'
            ) AS INTEGER
        ),
        NEW.event_digest,
        natsume_sha256_hex(NEW.event_payload_json),
        NEW.created_at_ms
    );
END;

CREATE TRIGGER IF NOT EXISTS relationship_life_event_v1_no_update
BEFORE UPDATE ON relationship_life_event_v1
BEGIN
    SELECT RAISE(ABORT, 'relationship_life_event_v1 is immutable');
END;

CREATE TRIGGER IF NOT EXISTS relationship_life_event_v1_no_delete
BEFORE DELETE ON relationship_life_event_v1
BEGIN
    SELECT RAISE(ABORT, 'relationship_life_event_v1 is append-only');
END;

COMMIT;
