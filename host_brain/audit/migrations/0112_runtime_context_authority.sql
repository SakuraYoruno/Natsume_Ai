-- NE-03: true aggregate CAS for Scene/Guardian and Host-lease-bound Guardian writes.

BEGIN IMMEDIATE;

CREATE UNIQUE INDEX IF NOT EXISTS ux_runtime_context_aggregate_revision
    ON runtime_event_timeline(aggregate_type, aggregate_id, aggregate_version)
    WHERE aggregate_type IN ('SCENE_STATE', 'RUNTIME_GUARDIAN');

CREATE TRIGGER IF NOT EXISTS runtime_guardian_requires_active_host_lease
BEFORE INSERT ON runtime_event_timeline
WHEN NEW.aggregate_type = 'RUNTIME_GUARDIAN'
BEGIN
    SELECT CASE
        WHEN json_valid(NEW.payload_json) = 0 THEN
            RAISE(ABORT, 'Guardian payload must be valid JSON')
        WHEN NOT EXISTS (
            SELECT 1
            FROM host_embodiment_runtime_lease AS lease
            WHERE lease.runtime_scope_id = json_extract(
                NEW.payload_json,
                '$.details.runtime_scope_id'
            )
              AND lease.owner_id = json_extract(
                NEW.payload_json,
                '$.details.runtime_owner_id'
            )
              AND lease.lease_id = json_extract(
                NEW.payload_json,
                '$.details.runtime_lease_id'
            )
              AND lease.fencing_token = CAST(json_extract(
                NEW.payload_json,
                '$.details.fencing_token'
              ) AS INTEGER)
              AND lease.released_at_ms IS NULL
              AND lease.issued_at_ms <= NEW.created_ts_ms
              AND lease.expires_at_ms > NEW.created_ts_ms
        ) THEN RAISE(ABORT, 'Guardian runtime authority is not active')
    END;
END;

CREATE TRIGGER IF NOT EXISTS scene_state_requires_active_host_lease
BEFORE INSERT ON runtime_event_timeline
WHEN NEW.aggregate_type = 'SCENE_STATE'
BEGIN
    SELECT CASE
        WHEN json_valid(NEW.payload_json) = 0 THEN
            RAISE(ABORT, 'Scene payload must be valid JSON')
        WHEN NOT EXISTS (
            SELECT 1
            FROM host_embodiment_runtime_lease AS lease
            WHERE lease.runtime_scope_id = json_extract(
                NEW.payload_json,
                '$.runtime_scope_id'
            )
              AND lease.owner_id = json_extract(
                NEW.payload_json,
                '$.runtime_owner_id'
            )
              AND lease.lease_id = json_extract(
                NEW.payload_json,
                '$.runtime_lease_id'
            )
              AND lease.fencing_token = CAST(json_extract(
                NEW.payload_json,
                '$.fencing_token'
              ) AS INTEGER)
              AND lease.released_at_ms IS NULL
              AND lease.issued_at_ms <= NEW.created_ts_ms
              AND lease.expires_at_ms > NEW.created_ts_ms
        ) THEN RAISE(ABORT, 'Scene runtime authority is not active')
    END;
END;

COMMIT;
