-- DL-01: Seven Seas' canonical affective-life journal projection.
--
-- The generic life_command/life_event_journal UoW remains the only writer.
-- This table is a query projection populated exclusively from immutable Life
-- events; it stores no message, transcript, audio, image or model-chain data.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS affective_life_event_v1 (
    event_id TEXT PRIMARY KEY
        REFERENCES life_event_journal(event_id) ON DELETE RESTRICT,
    person_id TEXT NOT NULL CHECK(length(trim(person_id)) BETWEEN 1 AND 240),
    self_id TEXT NOT NULL CHECK(self_id = 'natsume_ai'),
    relationship_subject_id TEXT NOT NULL
        CHECK(length(trim(relationship_subject_id)) BETWEEN 1 AND 240),
    affective_kind TEXT NOT NULL CHECK(
        affective_kind IN (
            'OBSERVATION',
            'APPRAISAL',
            'TIME_ADVANCE',
            'EPISODE_RESOLVED',
            'REGULATION'
        )
    ),
    appraisal_id TEXT NOT NULL DEFAULT '',
    episode_id TEXT NOT NULL DEFAULT '',
    state_revision INTEGER NOT NULL CHECK(state_revision >= 0),
    event_digest TEXT NOT NULL UNIQUE CHECK(length(event_digest) = 64),
    payload_digest TEXT NOT NULL CHECK(length(payload_digest) = 64),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0)
);

CREATE INDEX IF NOT EXISTS idx_affective_life_event_subject_cursor
    ON affective_life_event_v1(
        person_id,
        created_at_ms,
        event_id
    );
CREATE INDEX IF NOT EXISTS idx_affective_life_event_episode
    ON affective_life_event_v1(person_id, episode_id, created_at_ms);

CREATE TRIGGER IF NOT EXISTS affective_life_event_v1_insert_guard
BEFORE INSERT ON affective_life_event_v1
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
      AND CAST(
          json_extract(
              event.event_payload_json,
              '$.state_after.affective.revision'
          ) AS INTEGER
      ) = NEW.state_revision
)
BEGIN
    SELECT RAISE(
        ABORT,
        'affective_life_event_v1 requires an exact immutable Life event'
    );
END;

CREATE TRIGGER IF NOT EXISTS affective_life_event_v1_from_life_event
AFTER INSERT ON life_event_journal
WHEN NEW.event_type IN (
    'affective_observation_recorded',
    'affective_event_appraised',
    'affective_time_advanced',
    'affective_episode_resolved',
    'affective_regulation_decided'
)
BEGIN
    INSERT INTO affective_life_event_v1(
        event_id,
        person_id,
        self_id,
        relationship_subject_id,
        affective_kind,
        appraisal_id,
        episode_id,
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
            '$.state_after.identity.relationship_subject_id'
        ),
        CASE NEW.event_type
            WHEN 'affective_observation_recorded' THEN 'OBSERVATION'
            WHEN 'affective_event_appraised' THEN 'APPRAISAL'
            WHEN 'affective_time_advanced' THEN 'TIME_ADVANCE'
            WHEN 'affective_episode_resolved' THEN 'EPISODE_RESOLVED'
            WHEN 'affective_regulation_decided' THEN 'REGULATION'
        END,
        COALESCE(
            json_extract(
                NEW.event_payload_json,
                '$.command_payload.appraisal.appraisal_id'
            ),
            ''
        ),
        COALESCE(
            json_extract(
                NEW.event_payload_json,
                '$.command_payload.episode.episode_id'
            ),
            json_extract(
                NEW.event_payload_json,
                '$.command_payload.episode_id'
            ),
            json_extract(
                NEW.event_payload_json,
                '$.command_payload.regulation.episode_id'
            ),
            ''
        ),
        CAST(
            json_extract(
                NEW.event_payload_json,
                '$.state_after.affective.revision'
            ) AS INTEGER
        ),
        NEW.event_digest,
        natsume_sha256_hex(NEW.event_payload_json),
        NEW.created_at_ms
    );
END;

CREATE TRIGGER IF NOT EXISTS affective_life_event_v1_no_update
BEFORE UPDATE ON affective_life_event_v1
BEGIN
    SELECT RAISE(ABORT, 'affective_life_event_v1 is immutable');
END;

CREATE TRIGGER IF NOT EXISTS affective_life_event_v1_no_delete
BEFORE DELETE ON affective_life_event_v1
BEGIN
    SELECT RAISE(ABORT, 'affective_life_event_v1 is append-only');
END;

COMMIT;
