-- DL-06: immutable, reversible companion-expression profile lifecycle.
--
-- This projection is a read head over the generic Life journal.  It stores
-- only opaque profile/evidence identities and bounded counters: no Soul text,
-- user message, raw media, model reasoning, or private preference body is
-- copied outside the canonical event payload.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS companion_profile_life_event_v1 (
    event_id TEXT PRIMARY KEY
        REFERENCES life_event_journal(event_id) ON DELETE RESTRICT,
    person_id TEXT NOT NULL CHECK(length(trim(person_id)) BETWEEN 1 AND 240),
    self_id TEXT NOT NULL CHECK(self_id = 'natsume_ai'),
    workspace_id TEXT NOT NULL CHECK(length(trim(workspace_id)) BETWEEN 1 AND 240),
    relationship_subject_id TEXT NOT NULL
        CHECK(length(trim(relationship_subject_id)) BETWEEN 1 AND 240),
    profile_kind TEXT NOT NULL CHECK(profile_kind IN (
        'CANARY_STARTED', 'CANARY_INTERACTION', 'ACTIVATED', 'ROLLED_BACK'
    )),
    profile_id TEXT NOT NULL CHECK(length(trim(profile_id)) BETWEEN 1 AND 240),
    profile_digest TEXT NOT NULL CHECK(length(profile_digest) = 64),
    active_profile_id TEXT NOT NULL DEFAULT ''
        CHECK(length(active_profile_id) <= 240),
    rollback_profile_id TEXT NOT NULL DEFAULT ''
        CHECK(length(rollback_profile_id) <= 240),
    profile_revision INTEGER NOT NULL CHECK(profile_revision >= 0),
    canary_accepted_count INTEGER NOT NULL CHECK(
        canary_accepted_count BETWEEN 0 AND 5
    ),
    event_digest TEXT NOT NULL UNIQUE CHECK(length(event_digest) = 64),
    payload_digest TEXT NOT NULL CHECK(length(payload_digest) = 64),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0)
);

CREATE INDEX IF NOT EXISTS idx_companion_profile_life_event_subject_cursor
    ON companion_profile_life_event_v1(
        person_id, created_at_ms, event_id
    );
CREATE INDEX IF NOT EXISTS idx_companion_profile_life_event_profile
    ON companion_profile_life_event_v1(person_id, profile_id, created_at_ms, event_id);

CREATE TRIGGER IF NOT EXISTS companion_profile_life_event_v1_insert_guard
BEFORE INSERT ON companion_profile_life_event_v1
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
          '$.state_after.relationship_v2.workspace_id'
      ) = NEW.workspace_id
      AND json_extract(
          event.event_payload_json,
          '$.state_after.identity.relationship_subject_id'
      ) = NEW.relationship_subject_id
      AND json_extract(
          event.event_payload_json,
          '$.command_payload.profile.profile_id'
      ) = NEW.profile_id
      AND json_extract(
          event.event_payload_json,
          '$.command_payload.profile.profile_digest'
      ) = NEW.profile_digest
      AND COALESCE(
          json_extract(
              event.event_payload_json,
              '$.state_after.companion_profile.active_profile.profile_id'
          ),
          ''
      ) = NEW.active_profile_id
      AND COALESCE(
          json_extract(
              event.event_payload_json,
              '$.command_payload.profile.rollback_profile_id'
          ),
          ''
      ) = NEW.rollback_profile_id
      AND CAST(
          json_extract(
              event.event_payload_json,
              '$.state_after.companion_profile.revision'
          ) AS INTEGER
      ) = NEW.profile_revision
      AND COALESCE(
          json_array_length(
              json_extract(
                  event.event_payload_json,
                  '$.state_after.companion_profile.canary_accepted_interaction_ids'
              )
          ),
          0
      ) = NEW.canary_accepted_count
      AND CASE event.event_type
          WHEN 'companion_profile_canary_started' THEN 'CANARY_STARTED'
          WHEN 'companion_profile_canary_interaction_recorded' THEN 'CANARY_INTERACTION'
          WHEN 'companion_profile_activated' THEN 'ACTIVATED'
          WHEN 'companion_profile_rolled_back' THEN 'ROLLED_BACK'
      END = NEW.profile_kind
      AND (
          event.event_type <> 'companion_profile_canary_interaction_recorded'
          OR EXISTS (
              SELECT 1
              FROM life_event_journal AS aftereffect_event
              WHERE aftereffect_event.person_id = NEW.person_id
                AND aftereffect_event.sequence < event.sequence
                AND aftereffect_event.event_type = 'relational_aftereffect_recorded'
                AND json_extract(
                    aftereffect_event.event_payload_json,
                    '$.command_payload.aftereffect.aftereffect_id'
                ) = json_extract(
                    event.event_payload_json,
                    '$.command_payload.canary_interaction.aftereffect_id'
                )
                AND json_extract(
                    aftereffect_event.event_payload_json,
                    '$.command_payload.aftereffect.outcome'
                ) = 'HELPFUL'
                AND CAST(
                    json_extract(
                        aftereffect_event.event_payload_json,
                        '$.command_payload.aftereffect.boundary_respected'
                    ) AS INTEGER
                ) = 1
          )
      )
)
BEGIN
    SELECT RAISE(
        ABORT,
        'companion_profile_life_event_v1 requires an exact immutable Life event'
    );
END;

CREATE TRIGGER IF NOT EXISTS companion_profile_life_event_v1_from_life_event
AFTER INSERT ON life_event_journal
WHEN NEW.event_type IN (
    'companion_profile_canary_started',
    'companion_profile_canary_interaction_recorded',
    'companion_profile_activated',
    'companion_profile_rolled_back'
)
BEGIN
    INSERT INTO companion_profile_life_event_v1(
        event_id,
        person_id,
        self_id,
        workspace_id,
        relationship_subject_id,
        profile_kind,
        profile_id,
        profile_digest,
        active_profile_id,
        rollback_profile_id,
        profile_revision,
        canary_accepted_count,
        event_digest,
        payload_digest,
        created_at_ms
    ) VALUES (
        NEW.event_id,
        NEW.person_id,
        json_extract(NEW.event_payload_json, '$.state_after.identity.self_id'),
        json_extract(NEW.event_payload_json, '$.state_after.relationship_v2.workspace_id'),
        json_extract(
            NEW.event_payload_json,
            '$.state_after.identity.relationship_subject_id'
        ),
        CASE NEW.event_type
            WHEN 'companion_profile_canary_started' THEN 'CANARY_STARTED'
            WHEN 'companion_profile_canary_interaction_recorded' THEN 'CANARY_INTERACTION'
            WHEN 'companion_profile_activated' THEN 'ACTIVATED'
            WHEN 'companion_profile_rolled_back' THEN 'ROLLED_BACK'
        END,
        json_extract(NEW.event_payload_json, '$.command_payload.profile.profile_id'),
        json_extract(NEW.event_payload_json, '$.command_payload.profile.profile_digest'),
        COALESCE(
            json_extract(
                NEW.event_payload_json,
                '$.state_after.companion_profile.active_profile.profile_id'
            ),
            ''
        ),
        COALESCE(
            json_extract(
                NEW.event_payload_json,
                '$.command_payload.profile.rollback_profile_id'
            ),
            ''
        ),
        CAST(
            json_extract(
                NEW.event_payload_json,
                '$.state_after.companion_profile.revision'
            ) AS INTEGER
        ),
        COALESCE(
            json_array_length(
                json_extract(
                    NEW.event_payload_json,
                    '$.state_after.companion_profile.canary_accepted_interaction_ids'
                )
            ),
            0
        ),
        NEW.event_digest,
        natsume_sha256_hex(NEW.event_payload_json),
        NEW.created_at_ms
    );
END;

CREATE TRIGGER IF NOT EXISTS companion_profile_life_event_v1_no_update
BEFORE UPDATE ON companion_profile_life_event_v1
BEGIN
    SELECT RAISE(ABORT, 'companion_profile_life_event_v1 is immutable');
END;

CREATE TRIGGER IF NOT EXISTS companion_profile_life_event_v1_no_delete
BEFORE DELETE ON companion_profile_life_event_v1
BEGIN
    SELECT RAISE(ABORT, 'companion_profile_life_event_v1 is append-only');
END;

COMMIT;
